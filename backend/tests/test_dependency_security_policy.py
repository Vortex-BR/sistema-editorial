from pathlib import Path
import tomllib

import yaml


ROOT = Path(__file__).parents[2]
GITLEAKS_IMAGE = (
    "ghcr.io/gitleaks/gitleaks:v8.30.1@"
    "sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f"
)


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def load_workflow() -> dict:
    return yaml.safe_load(read(".github/workflows/docker-build.yml"))


def named_step(job: dict, name: str) -> dict:
    return next(step for step in job["steps"] if step.get("name") == name)


def test_secret_scan_uses_license_free_cli_pinned_by_digest():
    workflow_text = read(".github/workflows/docker-build.yml")
    workflow = load_workflow()
    job = workflow["jobs"]["dependency-security"]
    step = named_step(job, "Detect committed secrets")

    assert "gitleaks/gitleaks-action@v2" not in workflow_text
    assert "GITLEAKS_LICENSE" not in workflow_text
    assert step["shell"] == "bash"
    assert GITLEAKS_IMAGE in step["run"]
    assert "GIT_CONFIG_KEY_0=safe.directory" in step["run"]
    assert "GIT_CONFIG_VALUE_0=/repo" in step["run"]
    assert "--redact" in step["run"]
    assert "--report-format sarif" in step["run"]
    assert "gitleaks.exit" in step["run"]


def test_gitleaks_allowlists_are_narrow_and_rule_scoped():
    config_text = read(".gitleaks.toml")
    config = tomllib.loads(config_text)
    allowlists = config["allowlists"]

    assert config["extend"]["useDefault"] is True
    assert all("commits" not in item for item in allowlists)
    assert "disabledRules" not in config_text
    assert len(allowlists) == 3

    synthetic = allowlists[0]
    expected_literals = (
        "sk-" + "secret-value-123456789",
        "sk-" + "parameter-secret-must-not-appear",
        "sk-" + "abcdefghijklmnopqrstuv",
    )
    assert synthetic["regexTarget"] == "line"
    assert tuple(synthetic["regexes"]) == expected_literals
    assert "paths" not in synthetic
    assert "targetRules" not in synthetic

    report_exception = allowlists[1]
    assert report_exception["condition"] == "AND"
    assert report_exception["targetRules"] == ["generic-api-key"]
    assert report_exception["paths"] == [
        r"^RELATORIO_ATUALIZACAO_EDITORIAL_V3_7\.md$"
    ]
    assert report_exception["regexTarget"] == "line"
    assert report_exception["regexes"] == [
        r"^\s*CREDENTIAL_MASTER_KEYS=\s*$"
    ]

    curl_exception = allowlists[2]
    assert curl_exception["condition"] == "AND"
    assert curl_exception["targetRules"] == ["curl-auth-header"]
    assert curl_exception["paths"] == [r"^scripts/ci/image-smoke\.sh$"]
    assert curl_exception["regexTarget"] == "line"
    assert curl_exception["regexes"] == [
        r'''^\s*--header 'X-Admin-Token:' "\$\{base_url\}/api/v1/projects"\s*$'''
    ]


def test_current_tree_no_longer_contains_the_two_false_positive_lines():
    report = read("RELATORIO_ATUALIZACAO_EDITORIAL_V3_7.md")
    smoke = read("scripts/ci/image-smoke.sh")

    assert "CREDENTIAL_MASTER_KEYS=" not in report
    assert "--header 'X-Admin-Token:'" not in smoke
    assert 'admin_token_header="X-Admin-Token"' in smoke
    assert '--header "${admin_token_header}:"' in smoke


def test_runtime_dependency_uses_a_patched_pypdf_release():
    runtime = read("backend/requirements-runtime.txt")
    assert "pypdf==6.14.2" in runtime
    assert "pypdf==5.4.0" not in runtime


def test_dependency_gate_enforces_all_three_security_results():
    workflow = load_workflow()
    job = workflow["jobs"]["dependency-security"]
    enforce = named_step(job, "Enforce dependency security policy")["run"]
    upload = named_step(job, "Upload dependency audit reports")
    summary = named_step(job, "Summarize security findings")["run"]

    for status_file in ("pip-audit.exit", "npm-audit.exit", "gitleaks.exit"):
        assert status_file in enforce
    assert "gitleaks.sarif" in upload["with"]["path"]
    assert "fix_versions" in summary
    assert "metadata" in summary and "vulnerabilities" in summary
    assert "ruleId" in summary and "startLine" in summary
    assert "secret values remain redacted" in summary
