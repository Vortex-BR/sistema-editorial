<#
.SYNOPSIS
Creates a clean, traceable ZIP of the current repository worktree for review.

.DESCRIPTION
Run from any directory. The script packages tracked files plus untracked files that
are not ignored by Git. It excludes dependencies, build output, caches, real .env
files, logs, credentials, volumes, databases, archives, and local artifacts.

The generated REVIEW-MANIFEST.txt records the Git revision and status, expected
runtime/schema versions, and SHA-256 hashes for every packaged source file.

.EXAMPLE
.\New-ReviewPackage.ps1

.EXAMPLE
.\New-ReviewPackage.ps1 -OutputPath C:\review\docker-seo-review.zip
#>
[CmdletBinding()]
param(
    [string]$OutputPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path -LiteralPath $PSScriptRoot).Path
if (-not (Test-Path -LiteralPath (Join-Path $repoRoot ".git") -PathType Container)) {
    throw "The script must remain in the root of the docker-seo Git worktree."
}

function Invoke-RepositoryGit {
    param([string[]]$Arguments)

    $output = @(& git -C $repoRoot @Arguments)
    if ($LASTEXITCODE -ne 0) {
        throw "git $($Arguments -join ' ') failed with exit code $LASTEXITCODE."
    }
    return $output
}

function Test-ReviewPackageExclusion {
    param([string]$RelativePath)

    $normalized = $RelativePath.Replace("\", "/")
    $segments = @($normalized.Split("/"))
    $directoryExclusions = @(
        ".git",
        "node_modules",
        "dist",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        ".mypy_cache",
        ".tox",
        ".venv",
        "venv",
        "htmlcov",
        "coverage",
        "logs",
        ".logs",
        "volumes",
        ".volumes",
        "docker-data",
        "secrets",
        ".secrets",
        "credentials",
        ".credentials"
    )
    foreach ($segment in $segments) {
        if ($directoryExclusions -contains $segment.ToLowerInvariant()) {
            return $true
        }
    }

    $name = $segments[-1]
    $lowerName = $name.ToLowerInvariant()
    $isEnvironmentFile = (
        $lowerName -eq ".env" -or
        $lowerName.StartsWith(".env.") -or
        $lowerName.EndsWith(".env") -or
        $lowerName.Contains(".env.")
    )
    $isEnvironmentTemplate = $lowerName -match "\.(example|sample|template)$"
    if ($isEnvironmentFile -and -not $isEnvironmentTemplate) {
        return $true
    }

    if ($lowerName -match "\.(log(?:\..*)?|pem|key|pfx|p12|jks|keystore)$") {
        return $true
    }
    if ($lowerName -match "\.(zip|7z|rar|tar|tar\.gz|tgz|gz)$") {
        return $true
    }
    if ($lowerName -match "\.(db|sqlite|sqlite3|tmp|temp|bak|swp|tsbuildinfo)$") {
        return $true
    }
    if ($lowerName -match "^(credentials?|service[-_.]?account).*(json|ya?ml|ini|toml)$") {
        return $true
    }
    if ($lowerName -in @(".coverage", ".ds_store", "thumbs.db", "id_rsa", "id_ed25519")) {
        return $true
    }

    return $false
}

function Get-CommittedAlembicHead {
    $versionFiles = @(
        Invoke-RepositoryGit @(
            "ls-tree",
            "-r",
            "--name-only",
            "HEAD",
            "--",
            "backend/alembic/versions"
        ) | Where-Object { $_ -match "\.py$" }
    )
    $revisions = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::Ordinal
    )
    $parents = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::Ordinal
    )
    foreach ($versionFile in $versionFiles) {
        $content = (Invoke-RepositoryGit @("show", "HEAD:$versionFile")) -join "`n"
        $revisionMatch = [regex]::Match(
            $content,
            '(?m)^revision\s*=\s*["'']([^"'']+)["'']'
        )
        if (-not $revisionMatch.Success) {
            continue
        }
        [void]$revisions.Add($revisionMatch.Groups[1].Value)

        $parentMatch = [regex]::Match(
            $content,
            '(?m)^down_revision\s*=\s*["'']([^"'']+)["'']'
        )
        if ($parentMatch.Success) {
            [void]$parents.Add($parentMatch.Groups[1].Value)
        }
    }

    $heads = @($revisions | Where-Object { -not $parents.Contains($_) } | Sort-Object)
    if ($heads.Count -ne 1) {
        throw "Expected exactly one committed Alembic head, found: $($heads -join ', ')."
    }
    return $heads[0]
}

function Assert-NoHighConfidenceSecrets {
    param(
        [string]$PackageRoot,
        [string[]]$RelativePaths
    )

    $patterns = [ordered]@{
        "private key" = ("-----BEGIN " + "(?:RSA |EC |OPENSSH )?PRIVATE KEY-----")
        "AWS access key" = '\bAKIA[0-9A-Z]{16}\b'
        "Google API key" = '\bAIza[A-Za-z0-9_-]{30,}\b'
        "OpenAI-style secret" = '\bsk-[A-Za-z0-9_-]{24,}\b'
        "GitHub token" = '\bgh[pousr]_[A-Za-z0-9]{30,}\b'
    }
    foreach ($relativePath in $RelativePaths) {
        $platformPath = $relativePath.Replace(
            "/",
            [System.IO.Path]::DirectorySeparatorChar
        )
        $fullPath = Join-Path $PackageRoot $platformPath
        $text = [System.Text.Encoding]::UTF8.GetString(
            [System.IO.File]::ReadAllBytes($fullPath)
        )
        foreach ($entry in $patterns.GetEnumerator()) {
            if ([regex]::IsMatch($text, $entry.Value)) {
                throw "Potential $($entry.Key) found in packaged file $relativePath."
            }
        }
    }
}

$commitSha = (Invoke-RepositoryGit @("rev-parse", "HEAD") | Select-Object -First 1).Trim()
$branch = (Invoke-RepositoryGit @("branch", "--show-current") | Select-Object -First 1).Trim()
if ([string]::IsNullOrWhiteSpace($branch)) {
    $branch = "(detached HEAD)"
}
$gitStatus = @(Invoke-RepositoryGit @("status", "--porcelain=v1", "--untracked-files=all"))
if ($gitStatus.Count -ne 0) {
    throw "Refusing to package a dirty worktree. Commit or remove every change first."
}
$alembicHead = Get-CommittedAlembicHead
$commitUtcText = (
    Invoke-RepositoryGit @("show", "-s", "--format=%cI", "HEAD") |
        Select-Object -First 1
).Trim()
$commitUtc = [DateTimeOffset]::Parse(
    $commitUtcText,
    [System.Globalization.CultureInfo]::InvariantCulture
).ToUniversalTime()
$commitLines = @(
    Invoke-RepositoryGit @("log", "--format=%H  %cI  %s", "HEAD")
)

if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = Join-Path $repoRoot "docker-seo-review-$($commitSha.Substring(0, 12)).zip"
} elseif (-not [System.IO.Path]::IsPathRooted($OutputPath)) {
    $OutputPath = Join-Path $repoRoot $OutputPath
}
$OutputPath = [System.IO.Path]::GetFullPath($OutputPath)
if ([System.IO.Path]::GetExtension($OutputPath) -ne ".zip") {
    throw "OutputPath must end in .zip."
}

$outputDirectory = Split-Path -Parent $OutputPath
if (-not (Test-Path -LiteralPath $outputDirectory -PathType Container)) {
    New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
}
if (Test-Path -LiteralPath $OutputPath) {
    Remove-Item -LiteralPath $OutputPath -Force
}

$candidateFiles = @(
    Invoke-RepositoryGit @(
        "-c", "core.quotepath=false",
        "ls-tree", "-r", "--name-only", "HEAD"
    ) | Sort-Object -Unique
)

$tempBase = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$stagingRoot = [System.IO.Path]::GetFullPath(
    (Join-Path $tempBase "docker-seo-review-$([guid]::NewGuid().ToString('N'))")
)
if (-not $stagingRoot.StartsWith($tempBase, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to use a staging directory outside the system temporary directory."
}
$sourceRoot = Join-Path $stagingRoot "committed-source"
$packageRoot = Join-Path $stagingRoot "package"
$sourceArchive = Join-Path $stagingRoot "committed-source.tar"

$packagedFiles = [System.Collections.Generic.List[string]]::new()
try {
    New-Item -ItemType Directory -Path $stagingRoot | Out-Null
    New-Item -ItemType Directory -Path $sourceRoot | Out-Null
    New-Item -ItemType Directory -Path $packageRoot | Out-Null

    & git -C $repoRoot archive --format=tar "--output=$sourceArchive" $commitSha
    if ($LASTEXITCODE -ne 0) {
        throw "git archive failed with exit code $LASTEXITCODE."
    }
    & tar -xf $sourceArchive -C $sourceRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Could not extract the committed Git archive."
    }

    foreach ($relativePath in $candidateFiles) {
        if (Test-ReviewPackageExclusion $relativePath) {
            continue
        }

        $platformPath = $relativePath.Replace("/", [System.IO.Path]::DirectorySeparatorChar)
        $sourcePath = Join-Path $sourceRoot $platformPath
        if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
            continue
        }

        $destinationPath = Join-Path $packageRoot $platformPath
        $destinationDirectory = Split-Path -Parent $destinationPath
        if (-not (Test-Path -LiteralPath $destinationDirectory -PathType Container)) {
            New-Item -ItemType Directory -Path $destinationDirectory -Force | Out-Null
        }
        Copy-Item -LiteralPath $sourcePath -Destination $destinationPath
        $packagedFiles.Add($relativePath.Replace("\", "/"))
    }

    if ($packagedFiles.Count -eq 0) {
        throw "No repository files remained after applying package exclusions."
    }
    Assert-NoHighConfidenceSecrets -PackageRoot $packageRoot -RelativePaths $packagedFiles

    $manifest = [System.Collections.Generic.List[string]]::new()
    $manifest.Add("Repository review manifest")
    $manifest.Add("==========================")
    $manifest.Add("Commit SHA: $commitSha")
    $manifest.Add("Branch: $branch")
    $manifest.Add("Generated UTC: $($commitUtc.ToString('yyyy-MM-ddTHH:mm:ssZ'))")
    $manifest.Add("Git status: clean")
    $manifest.Add("Alembic head: $alembicHead")
    $manifest.Add("Expected Python: 3.12 (Dockerfile and backend/Dockerfile)")
    $manifest.Add("Expected Node.js: 22 (Dockerfile and frontend/Dockerfile)")
    $manifest.Add("Expected PostgreSQL: 17")
    $manifest.Add("Expected pgvector: 0.8.5")
    $manifest.Add("Secret scan: passed (forbidden paths and high-confidence patterns)")
    $manifest.Add("")
    $manifest.Add("Git status --porcelain=v1:")
    $manifest.Add("(clean)")
    $manifest.Add("")
    $manifest.Add("Commits (newest first):")
    foreach ($commitLine in $commitLines) {
        $manifest.Add($commitLine)
    }
    $manifest.Add("")
    $manifest.Add("Packaged source files (SHA-256; manifest excluded from this list):")
    $expectedHashes = @{}
    foreach ($relativePath in ($packagedFiles | Sort-Object)) {
        $platformPath = $relativePath.Replace("/", [System.IO.Path]::DirectorySeparatorChar)
        $hash = (Get-FileHash -LiteralPath (Join-Path $packageRoot $platformPath) -Algorithm SHA256).Hash.ToLowerInvariant()
        $expectedHashes[$relativePath] = $hash
        $manifest.Add("$hash  $relativePath")
    }
    [System.IO.File]::WriteAllLines(
        (Join-Path $packageRoot "REVIEW-MANIFEST.txt"),
        $manifest,
        [System.Text.UTF8Encoding]::new($false)
    )

    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zipStream = [System.IO.File]::Open(
        $OutputPath,
        [System.IO.FileMode]::CreateNew,
        [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::None
    )
    $archive = [System.IO.Compression.ZipArchive]::new(
        $zipStream,
        [System.IO.Compression.ZipArchiveMode]::Create,
        $false
    )
    try {
        foreach ($file in (Get-ChildItem -LiteralPath $packageRoot -Recurse -Force -File | Sort-Object FullName)) {
            $entryName = $file.FullName.Substring($packageRoot.Length + 1).Replace("\", "/")
            $entry = $archive.CreateEntry(
                $entryName,
                [System.IO.Compression.CompressionLevel]::Optimal
            )
            $entry.LastWriteTime = $commitUtc
            $sourceStream = [System.IO.File]::OpenRead($file.FullName)
            $entryStream = $entry.Open()
            try {
                $sourceStream.CopyTo($entryStream)
            } finally {
                $entryStream.Dispose()
                $sourceStream.Dispose()
            }
        }
    } finally {
        $archive.Dispose()
        $zipStream.Dispose()
    }
} finally {
    if (
        (Test-Path -LiteralPath $stagingRoot -PathType Container) -and
        $stagingRoot.StartsWith($tempBase, [System.StringComparison]::OrdinalIgnoreCase)
    ) {
        Remove-Item -LiteralPath $stagingRoot -Recurse -Force
    }
}

$validationArchive = [System.IO.Compression.ZipFile]::OpenRead($OutputPath)
try {
    $entryNames = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::Ordinal
    )
    foreach ($entry in $validationArchive.Entries) {
        if (-not $entryNames.Add($entry.FullName)) {
            throw "Duplicate ZIP entry: $($entry.FullName)."
        }
        if (
            $entry.FullName -ne "REVIEW-MANIFEST.txt" -and
            (Test-ReviewPackageExclusion $entry.FullName)
        ) {
            throw "Forbidden file was written to ZIP: $($entry.FullName)."
        }
    }
    if (-not $entryNames.Contains("REVIEW-MANIFEST.txt")) {
        throw "REVIEW-MANIFEST.txt is missing from the ZIP."
    }
    if ($entryNames.Count -ne ($expectedHashes.Count + 1)) {
        throw "ZIP file count does not match the manifest."
    }

    foreach ($relativePath in $expectedHashes.Keys) {
        $entry = $validationArchive.GetEntry($relativePath)
        if ($null -eq $entry) {
            throw "Manifest file is missing from ZIP: $relativePath."
        }
        $entryStream = $entry.Open()
        $sha256 = [System.Security.Cryptography.SHA256]::Create()
        try {
            $actualHash = [System.BitConverter]::ToString(
                $sha256.ComputeHash($entryStream)
            ).Replace("-", "").ToLowerInvariant()
        } finally {
            $sha256.Dispose()
            $entryStream.Dispose()
        }
        if ($actualHash -ne $expectedHashes[$relativePath]) {
            throw "SHA-256 verification failed for ZIP entry $relativePath."
        }
    }
} finally {
    $validationArchive.Dispose()
}

$archiveHash = (Get-FileHash -LiteralPath $OutputPath -Algorithm SHA256).Hash.ToLowerInvariant()
[pscustomobject]@{
    Zip = $OutputPath
    SHA256 = $archiveHash
    Commit = $commitSha
    AlembicHead = $alembicHead
    Status = "clean"
    PackagedFiles = $packagedFiles.Count
}
