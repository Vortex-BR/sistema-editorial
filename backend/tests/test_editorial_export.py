import io
import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.routes import router
from app.core.config import settings
from app.db.models import PipelineRun, Project
from app.db.session import get_db
from app.services.editorial_export import (
    EDITORIAL_SEAL_INVALID_ERROR,
    EXECUTION_MANIFEST_INVALID_ERROR,
    HUMAN_APPROVAL_REQUIRED_ERROR,
    VERSION_INCONSISTENT_ERROR,
    EditorialExportService,
)
from app.services.editorial_seal import (
    article_version_checksum,
    review_package_checksum,
)
import app.services.execution_manifest as manifest_module
from app.services.execution_manifest import prompt_contract_manifest
from app.services.quality_evaluator import quality_rubric_manifest


NOW = datetime(2026, 7, 13, 15, 30, tzinfo=timezone.utc)
ADMIN_TOKEN = "editorial-export-admin-token"


class Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class ExportDb:
    def __init__(
        self,
        project,
        article=None,
        version=None,
        run=None,
        review=None,
        rows=None,
        manifest=None,
    ):
        self.project = project
        self.article = article
        self.version = version
        self.run = run
        self.review = review
        self.rows = rows or []
        self.manifest = manifest
        self.scalar_calls = 0
        self.fact_statement = ""
        self.fact_params = {}

    async def get(self, model, identifier):
        if model is Project:
            return self.project if self.project and self.project.id == identifier else None
        if model is PipelineRun:
            return self.run if self.run and self.run.id == identifier else None
        raise AssertionError(f"Unexpected get for {model}")

    async def scalar(self, statement):
        self.scalar_calls += 1
        sql = str(statement)
        if "FROM articles" in sql:
            return self.article
        if "FROM article_versions" in sql:
            return self.version
        if "FROM human_editorial_reviews" in sql:
            return self.review
        if "FROM execution_manifests" in sql:
            return self.manifest
        raise AssertionError(f"Unexpected scalar query: {sql}")

    async def scalars(self, statement):
        sql = str(statement)
        if "FROM agent_handoffs" in sql or "FROM source_snapshots" in sql:
            return Rows([])
        raise AssertionError(f"Unexpected scalars query: {sql}")

    async def execute(self, statement):
        self.fact_statement = str(statement)
        self.fact_params = statement.compile().params
        return Rows(self.rows)


def export_fixture(project_name="../../CON/Projeto Árvore"):
    project_id = uuid.uuid4()
    run_id = uuid.uuid4()
    article_id = uuid.uuid4()
    project = SimpleNamespace(
        id=project_id,
        name=project_name,
        topic="Cultivo de árvores",
        language="pt-BR",
        content_type="article",
        status="completed",
    )
    article = SimpleNamespace(
        id=article_id,
        project_id=project_id,
        active_pipeline_run_id=run_id,
        current_version=3,
        content_type="article",
        status="approved",
        final_markdown="# Árvores úteis\n\nConteúdo antes e depois.",
        final_html=(
            '<h1 onclick="steal()">Árvores úteis</h1>'
            '<script>ADMIN_TOKEN=script-secret-value</script>'
            '<p>Conteúdo <strong>seguro</strong>.</p>'
            '<a href="javascript:steal()">link bloqueado</a>'
        ),
        seo_metadata={
            "title": "Árvores úteis",
            "meta_description": "Conteúdo editorial seguro.",
            "slug": "arvores-uteis",
            "language": "pt-BR",
            "focus_keyphrase": "árvores",
            "related_keyphrases": ["cultivo"],
            "ADMIN_TOKEN": "metadata-secret-value",
        },
        source_report={
            "generated_at": NOW.isoformat(),
            "distinct_source_count": 1,
            "fact_count": 1,
            "traceability": [
                {
                    "block_id": "block-1",
                    "sentences": [
                        {"text": "Conteúdo seguro.", "fact_ids": ["fact-1"]}
                    ],
                }
            ],
            "title_fact_ids": ["fact-1"],
            "internal_prompt": "prompt-secret-value",
        },
        created_at=NOW,
        updated_at=NOW,
        content_embedding=[0.1],
        content_embedding_provider="provider-secret-value",
    )
    version = SimpleNamespace(
        id=uuid.uuid4(),
        article_id=article_id,
        pipeline_run_id=run_id,
        version=3,
        title="Árvores úteis",
        outline=["Introdução"],
        editorial_status="human_approved",
        final_markdown=article.final_markdown,
        final_html=article.final_html,
        seo_metadata=article.seo_metadata,
        source_report=article.source_report,
        created_at=NOW,
        updated_at=NOW,
        sealed_at=NOW,
    )
    version.content_checksum = article_version_checksum(version)
    run = SimpleNamespace(
        id=run_id,
        project_id=project_id,
        status="completed",
        current_stage="finalizer",
        started_at=NOW,
        finished_at=NOW,
        metadata_json={"DATABASE_URL": "postgresql://production"},
        error_message="Traceback (most recent call last)",
    )
    review = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=project_id,
        pipeline_run_id=run_id,
        article_version_id=version.id,
        reviewer="Editora Ana",
        decision="approved",
        observation="Revisão humana concluída",
        reviewed_at=NOW,
    )
    review.review_package_json = {
        "article_version_id": str(version.id),
        "article_version": version.version,
        "article_version_checksum": version.content_checksum,
        "pipeline_run_id": str(run_id),
        "changes": {"current_title": version.title},
    }
    review.review_package_checksum = review_package_checksum(
        review.review_package_json
    )
    source = SimpleNamespace(
        id=uuid.uuid4(),
        title="Fonte confiável",
        canonical_url="https://example.com/referencia",
        publisher="Example",
        source_type="academic",
        published_at=NOW,
        reliability_score=0.95,
        snapshot_text="OTHER_PROJECT_SECRET raw licensed content",
        metadata_json={"Authorization": "Bearer source-secret"},
    )
    snapshot = SimpleNamespace(
        id=uuid.uuid4(),
        source_id=source.id,
        accessed_at=NOW,
        content_hash="a" * 64,
        snapshot_text="OTHER_PROJECT_SECRET snapshot",
        title="Fonte confiável",
        author="Autora capturada",
        publisher="Example",
        published_at=NOW,
        canonical_url="https://example.com/referencia",
        domain="example.com",
        source_type="academic",
        reliability_score=0.95,
        extraction_method="tavily_raw_content",
        metadata_json={"x-goog-api-key": "source-secret"},
    )
    question = SimpleNamespace(id=uuid.uuid4(), question="Quais árvores são úteis?")
    fact = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=project_id,
        pipeline_run_id=run_id,
        research_question_id=question.id,
        claim_text="Árvores fornecem sombra.",
        exact_quote="Trees provide shade.",
        source_locator="section 2",
        confidence_score=0.93,
        approved=True,
        approved_by_run_id=uuid.uuid4(),
        conflict_group=None,
        superseded_by_id=None,
        source_id=source.id,
        source_snapshot_id=snapshot.id,
        created_at=NOW,
    )
    manifest_data = {
        "format_version": 1,
        "pipeline_run_id": str(run_id),
        "project_id": str(project_id),
        "fixed_at": NOW.isoformat(),
        "build": {"commit_sha": "reviewed-commit", "build_version": "test-build"},
        "default_skills": [],
        "embedding_route": None,
        "feature_flags": {"superior_skills_mode": "enforced"},
        "learned_skills": {},
        "memory_snapshots": {},
        "model_routes": {},
        "prompt_contracts": prompt_contract_manifest(),
        "quality_evaluator": quality_rubric_manifest(),
        "search_route": {"provider": "tavily"},
        "style_pattern_snapshots": {},
        "super_skills": {},
        "artifact_scope": {
            "handoffs": "append_only_run_scoped",
            "source_snapshots": "append_only_run_scoped",
        },
        "missing_dependencies": [],
    }
    manifest = SimpleNamespace(
        id=uuid.uuid4(),
        pipeline_run_id=run_id,
        format_version=1,
        manifest_json=manifest_data,
        checksum=manifest_module._checksum(manifest_data),
        created_at=NOW,
    )
    db = ExportDb(
        project,
        article,
        version,
        run,
        review,
        [(fact, snapshot, question)],
        manifest,
    )
    db.aggregate_source = source
    db.source_snapshot = snapshot
    return db


@pytest.mark.asyncio
async def test_export_rejects_unknown_project():
    db = ExportDb(None)

    with pytest.raises(HTTPException) as exc:
        await EditorialExportService(db).build(uuid.uuid4())

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_export_rejects_project_without_final_article():
    project = SimpleNamespace(id=uuid.uuid4())
    db = ExportDb(project)

    with pytest.raises(HTTPException) as exc:
        await EditorialExportService(db).build(project.id)

    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_publishable_export_requires_explicit_human_approval():
    db = export_fixture("Projeto aguardando editor")
    db.review.decision = "pending"
    db.review.reviewer = None
    db.review.reviewed_at = None
    db.run.status = "needs_human_approval"
    db.article.status = "needs_human_approval"
    db.version.editorial_status = "needs_human_approval"

    with pytest.raises(HTTPException) as exc:
        await EditorialExportService(db).build(db.project.id, exported_at=NOW)

    assert exc.value.status_code == 409
    assert exc.value.detail == {
        "error_code": HUMAN_APPROVAL_REQUIRED_ERROR,
        "message": "Human editor-in-chief approval is required",
    }
    assert db.fact_statement == ""


def assert_publishable_manifest_blocked(exc: HTTPException, db: ExportDb) -> None:
    assert exc.status_code == 409
    assert exc.detail == {
        "error_code": EXECUTION_MANIFEST_INVALID_ERROR,
        "message": "A valid execution manifest is required for publishable export",
    }
    assert db.fact_statement == ""


@pytest.mark.asyncio
async def test_publishable_export_rejects_missing_manifest():
    db = export_fixture("Projeto sem manifesto")
    db.manifest = None

    with pytest.raises(HTTPException) as exc:
        await EditorialExportService(db).build(db.project.id, exported_at=NOW)

    assert_publishable_manifest_blocked(exc.value, db)


@pytest.mark.asyncio
async def test_publishable_export_rejects_invalid_manifest_checksum():
    db = export_fixture("Projeto com checksum inválido")
    db.manifest.checksum = "0" * 64

    with pytest.raises(HTTPException) as exc:
        await EditorialExportService(db).build(db.project.id, exported_at=NOW)

    assert_publishable_manifest_blocked(exc.value, db)


@pytest.mark.asyncio
async def test_publishable_export_rejects_manifest_from_another_run():
    db = export_fixture("Projeto com manifesto de outro run")
    db.manifest.pipeline_run_id = uuid.uuid4()

    with pytest.raises(HTTPException) as exc:
        await EditorialExportService(db).build(db.project.id, exported_at=NOW)

    assert_publishable_manifest_blocked(exc.value, db)


@pytest.mark.asyncio
async def test_publishable_export_rejects_manifest_format_version_drift():
    db = export_fixture("Projeto com versão de manifesto incompatível")
    db.manifest.format_version = 2

    with pytest.raises(HTTPException) as exc:
        await EditorialExportService(db).build(db.project.id, exported_at=NOW)

    assert_publishable_manifest_blocked(exc.value, db)


@pytest.mark.asyncio
async def test_publishable_export_rejects_manifest_project_drift():
    db = export_fixture("Projeto com manifesto de outro projeto")
    db.manifest.manifest_json["project_id"] = str(uuid.uuid4())
    db.manifest.checksum = manifest_module._checksum(db.manifest.manifest_json)

    with pytest.raises(HTTPException) as exc:
        await EditorialExportService(db).build(db.project.id, exported_at=NOW)

    assert_publishable_manifest_blocked(exc.value, db)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("setting_name", "deployed_value"),
    [
        ("app_commit_sha", "currently-deployed-commit"),
        ("app_build_version", "currently-deployed-build"),
    ],
)
async def test_publishable_export_rejects_build_identity_drift(
    monkeypatch, setting_name, deployed_value
):
    db = export_fixture("Projeto com drift de build")
    monkeypatch.setattr(settings, setting_name, deployed_value)

    with pytest.raises(HTTPException) as exc:
        await EditorialExportService(db).build(db.project.id, exported_at=NOW)

    assert_publishable_manifest_blocked(exc.value, db)


@pytest.mark.asyncio
async def test_draft_declares_unavailable_manifest_and_is_never_publishable():
    db = export_fixture("Projeto sem manifesto em rascunho")
    db.manifest = None

    package = await EditorialExportService(db).build(
        db.project.id,
        exported_at=NOW,
        draft=True,
    )

    assert "RASCUNHO" in package.filename
    assert "PUBLICAVEL" not in package.filename
    with ZipFile(io.BytesIO(package.content)) as archive:
        root = package.root_directory
        manifest = json.loads(
            archive.read(f"{root}/manifesto-execucao.json").decode("utf-8")
        )
        metadata = json.loads(
            archive.read(f"{root}/metadata.json").decode("utf-8")
        )
        article = json.loads(
            archive.read(f"{root}/artigo.json").decode("utf-8")
        )
        readme = archive.read(f"{root}/LEIA-ME-RASCUNHO.txt").decode("utf-8")

    assert manifest == {
        "error_code": "EXECUTION_MANIFEST_UNAVAILABLE",
        "message": "Execution manifest is unavailable for this draft",
        "pipeline_run_id": str(db.run.id),
        "publishable": False,
        "status": "unavailable",
    }
    assert metadata["execution_manifest"]["status"] == "unavailable"
    assert (
        metadata["execution_manifest"]["error_code"]
        == "EXECUTION_MANIFEST_UNAVAILABLE"
    )
    assert metadata["package_kind"] == "review_draft"
    assert metadata["publication_status"] == "not_publishable"
    assert article["package_kind"] == "review_draft"
    assert article["publication_status"] == "not_publishable"
    assert "NÃO PUBLICAR" in readme


@pytest.mark.asyncio
async def test_review_draft_export_is_unmistakably_labeled():
    db = export_fixture("Projeto aguardando editor")
    db.review.decision = "pending"
    db.review.reviewer = None
    db.review.reviewed_at = None
    db.run.status = "needs_human_approval"
    db.article.status = "needs_human_approval"
    db.version.editorial_status = "needs_human_approval"

    package = await EditorialExportService(db).build(
        db.project.id,
        exported_at=NOW,
        draft=True,
    )

    assert package.filename == (
        "projeto-aguardando-editor-rascunho-v3-RASCUNHO-20260713.zip"
    )
    with ZipFile(io.BytesIO(package.content)) as archive:
        names = archive.namelist()
        readme_name = next(name for name in names if name.endswith("LEIA-ME-RASCUNHO.txt"))
        metadata_name = next(name for name in names if name.endswith("metadata.json"))
        readme = archive.read(readme_name).decode("utf-8")
        metadata = json.loads(archive.read(metadata_name))

    assert "NÃO PUBLICAR" in readme
    assert metadata["package_kind"] == "review_draft"
    assert metadata["publication_status"] == "not_publishable"


@pytest.mark.asyncio
async def test_export_builds_safe_valid_utf8_zip_for_only_requested_project():
    db = export_fixture()

    package = await EditorialExportService(db).build(
        db.project.id,
        exported_at=NOW,
    )

    assert package.filename == "con-projeto-arvore-v3-PUBLICAVEL-20260713.zip"
    assert ".." not in package.filename
    assert "/" not in package.filename
    assert "\\" not in package.filename
    assert "fact_ledger.project_id" in db.fact_statement
    assert "fact_ledger.pipeline_run_id" in db.fact_statement

    with ZipFile(io.BytesIO(package.content)) as archive:
        assert archive.testzip() is None
        names = archive.namelist()
        assert names == [
            "con-projeto-arvore/artigo.html",
            "con-projeto-arvore/artigo.json",
            "con-projeto-arvore/artigo.md",
            "con-projeto-arvore/evidencias.json",
            "con-projeto-arvore/fontes.json",
            "con-projeto-arvore/manifesto-execucao.json",
            "con-projeto-arvore/metadata.json",
        ]
        assert all(".." not in name for name in names)
        markdown = archive.read("con-projeto-arvore/artigo.md").decode("utf-8")
        html = archive.read("con-projeto-arvore/artigo.html").decode("utf-8")
        article = json.loads(archive.read("con-projeto-arvore/artigo.json"))
        evidence = json.loads(archive.read("con-projeto-arvore/evidencias.json"))
        sources = json.loads(archive.read("con-projeto-arvore/fontes.json"))
        metadata = json.loads(archive.read("con-projeto-arvore/metadata.json"))
        manifest = json.loads(
            archive.read("con-projeto-arvore/manifesto-execucao.json")
        )
        combined = b"\n".join(archive.read(name) for name in names)

    assert markdown.startswith("# Árvores úteis")
    assert manifest["checksum"] == db.manifest.checksum
    assert manifest["build"]["commit_sha"] == "reviewed-commit"
    assert "<script" not in html.lower()
    assert "onclick" not in html.lower()
    assert "javascript:" not in html.lower()
    assert article["version"]["title"] == "Árvores úteis"
    assert article["version"]["seo_metadata"]["slug"] == "arvores-uteis"
    assert article["version"]["content_checksum"] == db.version.content_checksum
    assert evidence["facts"][0]["claim"] == "Árvores fornecem sombra."
    assert evidence["facts"][0]["exact_quote"] == "Trees provide shade."
    assert evidence["audit_report"]["traceability"][0]["block_id"] == "block-1"
    assert sources["sources"][0]["url"] == "https://example.com/referencia"
    assert sources["sources"][0]["author"] == "Autora capturada"
    assert sources["sources"][0]["extraction_method"] == "tavily_raw_content"
    assert sources["sources"][0]["content_hash"] == "a" * 64
    assert metadata["source_count"] == 1
    assert metadata["fact_count"] == 1
    assert metadata["approved_fact_count"] == 1
    assert metadata["package_revision"] == "2"
    assert metadata["package_kind"] == "publishable"
    assert metadata["publication_status"] == "human_approved"
    assert metadata["human_review"]["reviewer"] == "Editora Ana"
    assert metadata["editorial_seal"] == {
        "status": "ready",
        "publishable": True,
        "content_checksum": db.version.content_checksum,
        "review_package_checksum": db.review.review_package_checksum,
        "sealed_at": NOW.isoformat(),
    }
    assert metadata["exported_at"] == NOW.isoformat()
    for forbidden in (
        b"ADMIN_TOKEN",
        b"Authorization",
        b"x-goog-api-key",
        b"DATABASE_URL",
        b"REDIS_URL",
        b"Traceback",
        b"OTHER_PROJECT_SECRET",
        b"prompt-secret-value",
        b"provider-secret-value",
    ):
        assert forbidden not in combined


@pytest.mark.asyncio
async def test_exported_source_provenance_ignores_later_aggregate_source_changes():
    db = export_fixture("Projeto com fonte histórica")
    db.aggregate_source.title = "Título global alterado"
    db.aggregate_source.publisher = "Outro publisher"
    db.aggregate_source.canonical_url = "https://changed.example/new"
    db.aggregate_source.source_type = "forum"
    db.aggregate_source.published_at = None
    db.aggregate_source.reliability_score = 0.1

    first_package = await EditorialExportService(db).build(
        db.project.id, exported_at=NOW
    )
    db.aggregate_source.title = "Título global alterado novamente"
    db.aggregate_source.reliability_score = 0.01
    second_package = await EditorialExportService(db).build(
        db.project.id, exported_at=NOW
    )

    def sources_from(package):
        with ZipFile(io.BytesIO(package.content)) as archive:
            return json.loads(
                archive.read(f"{package.root_directory}/fontes.json")
            )["sources"]

    first_sources = sources_from(first_package)
    second_sources = sources_from(second_package)
    assert first_sources == second_sources
    assert first_sources[0]["title"] == db.source_snapshot.title
    assert first_sources[0]["url"] == db.source_snapshot.canonical_url
    assert first_sources[0]["reliability_score"] == 0.95
    assert "JOIN sources" not in db.fact_statement


@pytest.mark.asyncio
async def test_publishable_export_detects_markdown_drift_before_creating_files():
    db = export_fixture("Projeto seguro")
    db.version.final_markdown = "# Título\n\nADMIN_TOKEN=real-secret-value"

    with pytest.raises(HTTPException) as exc:
        await EditorialExportService(db).build(db.project.id, exported_at=NOW)

    assert exc.value.status_code == 409
    assert exc.value.detail == {
        "error_code": EDITORIAL_SEAL_INVALID_ERROR,
        "message": "A valid editorial seal is required for export",
    }
    assert "real-secret-value" not in str(exc.value.detail)
    assert db.fact_statement == ""


@pytest.mark.asyncio
async def test_publishable_export_detects_seo_drift_before_creating_files():
    db = export_fixture("Projeto com SEO alterado")
    db.version.seo_metadata = {**db.version.seo_metadata, "title": "Alterado"}

    with pytest.raises(HTTPException) as exc:
        await EditorialExportService(db).build(db.project.id, exported_at=NOW)

    assert exc.value.status_code == 409
    assert exc.value.detail["error_code"] == EDITORIAL_SEAL_INVALID_ERROR
    assert db.fact_statement == ""


@pytest.mark.asyncio
async def test_export_uses_only_current_immutable_version_and_its_run():
    db = export_fixture("Projeto com versões divergentes")
    materialized_run_id = uuid.uuid4()
    db.article.active_pipeline_run_id = materialized_run_id
    db.article.status = "materialized-a"
    db.article.final_markdown = "# Conteúdo A materializado"
    db.article.final_html = "<p>HTML A materializado</p>"
    db.article.seo_metadata = {"title": "SEO A", "slug": "conteudo-a"}
    db.article.source_report = {"traceability": [{"block_id": "bloco-a"}]}
    db.version.final_markdown = "# Conteúdo B imutável\n\nSomente a versão B."
    db.version.final_html = "<h1>HTML B imutável</h1>"
    db.version.title = "Título B imutável"
    db.version.outline = ["Estrutura B"]
    db.version.editorial_status = "human_approved"
    db.version.seo_metadata = {"title": "SEO B", "slug": "conteudo-b"}
    db.version.source_report = {
        "fact_count": 1,
        "traceability": [{"block_id": "bloco-b", "sentences": []}],
    }

    package = await EditorialExportService(db).build(
        db.project.id,
        exported_at=NOW,
        draft=True,
    )

    assert db.version.pipeline_run_id in db.fact_params.values()
    assert materialized_run_id not in db.fact_params.values()
    with ZipFile(io.BytesIO(package.content)) as archive:
        combined = b"\n".join(archive.read(name) for name in archive.namelist())
        markdown = archive.read(
            f"{package.root_directory}/artigo.md"
        ).decode("utf-8")
        article_payload = json.loads(
            archive.read(f"{package.root_directory}/artigo.json")
        )
        evidence = json.loads(
            archive.read(f"{package.root_directory}/evidencias.json")
        )

    assert markdown.startswith("# Conteúdo B imutável")
    assert article_payload["version"]["html"] == "<h1>HTML B imutável</h1>"
    assert article_payload["version"]["title"] == "Título B imutável"
    assert article_payload["version"]["outline"] == ["Estrutura B"]
    assert article_payload["version"]["editorial_status"] == "human_approved"
    assert article_payload["version"]["seo_metadata"]["slug"] == "conteudo-b"
    assert article_payload["version"]["pipeline_run_id"] == str(
        db.version.pipeline_run_id
    )
    assert evidence["pipeline_run_id"] == str(db.version.pipeline_run_id)
    assert evidence["audit_report"]["traceability"][0]["block_id"] == "bloco-b"
    for forbidden in (
        "Conteúdo A materializado",
        "HTML A materializado",
        "SEO A",
        "conteudo-a",
        "bloco-a",
        "materialized-a",
        str(materialized_run_id),
    ):
        assert forbidden.encode() not in combined


@pytest.mark.asyncio
async def test_approved_export_remains_valid_after_a_later_run_is_cancelled():
    db = export_fixture("Approved article with cancelled follow-up")
    db.project.status = "completed"
    db.project.last_run_status = "cancelled"

    package = await EditorialExportService(db).build(db.project.id, exported_at=NOW)

    with ZipFile(io.BytesIO(package.content)) as archive:
        article = json.loads(
            archive.read(f"{package.root_directory}/artigo.json").decode("utf-8")
        )
    assert article["publication_status"] == "human_approved"
    assert article["version"]["pipeline_run_id"] == str(db.version.pipeline_run_id)


@pytest.mark.asyncio
async def test_export_rejects_structurally_inconsistent_current_version_safely():
    db = export_fixture("Projeto inconsistente")
    db.article.current_version += 1

    with pytest.raises(HTTPException) as exc:
        await EditorialExportService(db).build(db.project.id, exported_at=NOW)

    assert exc.value.status_code == 409
    assert exc.value.detail == {
        "error_code": VERSION_INCONSISTENT_ERROR,
        "message": "The current editorial version is inconsistent",
    }
    assert db.fact_statement == ""


def test_export_endpoint_returns_admin_only_zip_with_safe_headers(monkeypatch):
    db = export_fixture("Projeto Editorial")

    async def database_dependency():
        yield db

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = database_dependency
    monkeypatch.setattr(settings, "admin_api_token", ADMIN_TOKEN)

    with TestClient(app) as client:
        response = client.get(
            f"/api/v1/projects/{db.project.id}/export",
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"].startswith(
        'attachment; filename="projeto-editorial-v3-PUBLICAVEL-'
    )
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    with ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.testzip() is None


def test_export_endpoint_returns_safe_conflict_without_partial_zip(monkeypatch):
    db = export_fixture("Projeto com manifesto corrompido")
    db.manifest.checksum = "invalid-checksum"

    async def database_dependency():
        yield db

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = database_dependency
    monkeypatch.setattr(settings, "admin_api_token", ADMIN_TOKEN)

    with TestClient(app) as client:
        response = client.get(
            f"/api/v1/projects/{db.project.id}/export",
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )

    assert response.status_code == 409
    assert response.json() == {
        "detail": {
            "error_code": EXECUTION_MANIFEST_INVALID_ERROR,
            "message": (
                "A valid execution manifest is required for publishable export"
            ),
        }
    }
    assert response.headers["content-type"].startswith("application/json")
    assert not response.content.startswith(b"PK")
    assert db.fact_statement == ""


def test_safe_source_report_preserves_v361_binding_and_sentence_identity():
    sentence_id = str(uuid.uuid4())
    article_version_id = str(uuid.uuid4())
    report = EditorialExportService._safe_source_report(
        {
            "pipeline_contract_version": "editorial-v3.6.1",
            "source_document_count": 4,
            "distinct_source_count": 3,
            "approved_claim_count": 7,
            "editorial_intelligence_binding": {
                "validated_artifact_hash": "a" * 64,
                "article_version_id": article_version_id,
                "draft_revision": 4,
                "internal_prompt": "secret-in-binding",
            },
            "traceability": [
                {
                    "block_id": "block-1",
                    "sentences": [
                        {
                            "sentence_id": sentence_id,
                            "text": "Frase rastreável.",
                            "question_ids": ["q_foundation_central_1"],
                            "answer_status": "direct",
                            "fact_ids": ["fact-1"],
                            "internal_prompt": "secret",
                        }
                    ],
                }
            ],
            "internal_prompt": "secret",
        }
    )

    assert report["pipeline_contract_version"] == "editorial-v3.6.1"
    assert report["approved_claim_count"] == 7
    assert report["editorial_intelligence_binding"]["article_version_id"] == article_version_id
    assert "internal_prompt" not in report["editorial_intelligence_binding"]
    sentence = report["traceability"][0]["sentences"][0]
    assert sentence["sentence_id"] == sentence_id
    assert sentence["question_ids"] == ["q_foundation_central_1"]
    assert "internal_prompt" not in sentence
    assert "internal_prompt" not in report
