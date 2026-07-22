import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.human_editorial_review as review_module
import app.api.routes as routes_module
from app.api.routes import start_project
from app.db.models import (
    Article,
    ArticleVersion,
    HumanEditorialReview,
    PipelineRun,
    PipelineRunStatus,
    Project,
    ProjectStatus,
)
from app.services.human_editorial_review import (
    HumanEditorialReviewService,
    HumanReviewConflict,
)
from app.services.editorial_seal import (
    article_version_checksum,
    review_package_checksum,
)
from app.services.quality_evaluator import (
    checksum as quality_checksum,
    evaluate_snapshot,
    quality_rubric_manifest,
)


NOW = datetime(2026, 7, 13, 18, 0, tzinfo=timezone.utc)


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class ReviewDb:
    def __init__(self, project, run, article, version, review):
        self.project = project
        self.run = run
        self.article = article
        self.version = version
        self.review = review
        quality_result = {
            "critical_blockers": [],
            "axes": {},
            "warnings": [],
        }
        result_checksum = quality_checksum(quality_result)
        self.quality_evaluation = SimpleNamespace(
            id=uuid.uuid4(),
            project_id=project.id,
            pipeline_run_id=run.id,
            article_version_id=version.id,
            rubric_version="quality-rubric.v1",
            rubric_checksum="a" * 64,
            evaluator_kind="deterministic",
            status="passed",
            overall_score=0.9,
            thresholds_json={},
            result_json={**quality_result, "result_checksum": result_checksum},
            result_checksum=result_checksum,
            created_at=NOW,
        )
        self.revision_run = None
        self.other_active = None
        self.added = []

    async def scalar(self, statement):
        sql = str(statement)
        if "FROM human_editorial_reviews" in sql:
            return self.review
        if "FROM quality_evaluations" in sql:
            return self.quality_evaluation
        if "SELECT pipeline_runs.id" in sql:
            return self.other_active
        if "FROM articles" in sql:
            return self.article
        if "FROM article_versions" in sql:
            return self.version
        raise AssertionError(f"Unexpected scalar query: {sql}")

    async def get(self, model, identifier):
        if model is Project:
            return self.project if identifier == self.project.id else None
        if model is PipelineRun:
            if self.revision_run and identifier == self.revision_run.id:
                return self.revision_run
            return self.run if identifier == self.run.id else None
        if model is ArticleVersion:
            return self.version if identifier == self.version.id else None
        if model is Article:
            return self.article if identifier == self.article.id else None
        raise AssertionError(f"Unexpected get: {model}")

    def add(self, value):
        if getattr(value, "id", None) is None:
            value.id = uuid.uuid4()
        self.added.append(value)
        if isinstance(value, HumanEditorialReview):
            value.created_at = NOW
            value.updated_at = NOW
            self.review = value

    async def flush(self):
        return None


class FakeRuns:
    def __init__(self, db):
        self.db = db
        self.created_metadata = None
        self.project_status_at_create = None

    async def acquire(self, run_id):
        if run_id != self.db.run.id:
            raise ValueError("Pipeline run not found")
        return self.db.run

    async def transition(
        self,
        run_id,
        target,
        *,
        origin,
        reason=None,
        stage=None,
        expected_lock_version=None,
    ):
        assert run_id == self.db.run.id
        assert origin == "admin.human-review"
        assert expected_lock_version == self.db.run.lock_version
        self.db.run.status = target
        self.db.run.current_stage = stage or self.db.run.current_stage
        self.db.run.lock_version += 1
        self.db.run.transition_reason = reason
        return self.db.run

    async def create(self, project_id, idempotency_key, *, trigger_type, metadata):
        assert project_id == self.db.project.id
        assert idempotency_key.startswith("human-review:")
        self.created_metadata = metadata
        self.project_status_at_create = self.db.project.status
        self.db.revision_run = SimpleNamespace(
            id=uuid.uuid4(),
            project_id=project_id,
            status=PipelineRunStatus.queued,
            current_stage="planner",
            metadata_json=metadata,
        )
        return self.db.revision_run, True


class FakeEvents:
    appended = []

    def __init__(self, _db):
        pass

    async def append(self, project_id, run_id, event_type, stage, payload, **kwargs):
        self.appended.append(
            (project_id, run_id, event_type, stage, payload, kwargs)
        )


def review_fixture():
    project = SimpleNamespace(
        id=uuid.uuid4(),
        status=ProjectStatus.needs_human_approval,
        current_stage="human_approval",
    )
    run = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=project.id,
        status=PipelineRunStatus.needs_human_approval,
        current_stage="human_approval",
        lock_version=4,
    )
    article = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=project.id,
        current_version=2,
        active_pipeline_run_id=run.id,
        status="needs_human_approval",
    )
    version = SimpleNamespace(
        id=uuid.uuid4(),
        article_id=article.id,
        pipeline_run_id=run.id,
        version=2,
        editorial_status="needs_human_approval",
        title="Draft",
        outline=["Draft"],
        final_markdown="# Draft",
        final_html="<h1>Draft</h1>",
        seo_metadata={"title": "Draft"},
        source_report={"unsupported_claim_count": 0},
        sealed_at=None,
    )
    version.content_checksum = article_version_checksum(version)
    package = {
        "article_version_id": str(version.id),
        "article_version": version.version,
        "article_version_checksum": version.content_checksum,
        "pipeline_run_id": str(run.id),
        "changes": {"current_title": version.title},
        "risks": [],
    }
    review = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=project.id,
        pipeline_run_id=run.id,
        article_version_id=version.id,
        reviewer=None,
        decision="pending",
        observation=None,
        reviewed_at=None,
        decision_idempotency_key=None,
        revision_run_id=None,
        review_package_json=package,
        review_package_checksum=review_package_checksum(package),
        created_at=NOW,
        updated_at=NOW,
    )
    db = ReviewDb(project, run, article, version, review)
    service = HumanEditorialReviewService(db)
    service.runs = FakeRuns(db)
    return db, service


@pytest.fixture(autouse=True)
def fake_events(monkeypatch):
    FakeEvents.appended = []
    monkeypatch.setattr(review_module, "EventService", FakeEvents)


@pytest.mark.asyncio
async def test_final_package_creates_pending_review_without_fake_reviewer():
    project = SimpleNamespace(id=uuid.uuid4())
    run = SimpleNamespace(id=uuid.uuid4(), project_id=project.id)
    article = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=project.id,
        current_version=1,
        active_pipeline_run_id=run.id,
    )
    version = SimpleNamespace(
        id=uuid.uuid4(),
        article_id=article.id,
        pipeline_run_id=run.id,
        version=1,
        title="Review me",
        outline=[],
        final_markdown="# Review me",
        final_html="<h1>Review me</h1>",
        seo_metadata={},
        source_report={},
        sealed_at=None,
    )
    db = ReviewDb(project, run, article, version, None)
    service = HumanEditorialReviewService(db)
    service.build_review_package = AsyncMock(return_value={"risks": []})

    review = await service.ensure_pending(project, run)

    assert review.decision == "pending"
    assert review.reviewer is None
    assert review.reviewed_at is None
    assert review.pipeline_run_id == run.id
    assert review.article_version_id == version.id
    assert version.content_checksum == review.review_package_json[
        "article_version_checksum"
    ]
    assert review.review_package_checksum == review_package_checksum(
        review.review_package_json
    )


@pytest.mark.asyncio
async def test_review_package_contains_every_editor_in_chief_section():
    project = SimpleNamespace(id=uuid.uuid4())
    run = SimpleNamespace(id=uuid.uuid4(), project_id=project.id)
    article = SimpleNamespace(id=uuid.uuid4(), project_id=project.id)
    version = SimpleNamespace(
        id=uuid.uuid4(),
        article_id=article.id,
        pipeline_run_id=run.id,
        version=2,
        title="Versão atual",
        outline=["Atual"],
        final_markdown="# Atual\n\nTexto novo",
        seo_metadata={"title": "SEO atual"},
        source_report={"unsupported_claim_count": 0},
        change_reason="writer run",
    )
    previous = SimpleNamespace(
        version=1,
        title="Versão anterior",
        outline=["Anterior"],
        final_markdown=None,
    )
    question = SimpleNamespace(
        id=uuid.uuid4(),
        question="Pergunta prioritária",
        priority=1,
        coverage_status="covered",
        created_at=NOW,
    )
    source = SimpleNamespace(
        id=uuid.uuid4(),
        title="Título global posterior",
        canonical_url="https://changed.example/posterior",
        publisher="Publisher posterior",
        reliability_score=0.2,
    )
    snapshot = SimpleNamespace(
        id=uuid.uuid4(),
        source_id=source.id,
        title="Fonte oficial capturada",
        canonical_url="https://example.com/oficial",
        domain="example.com",
        author="Autora original",
        publisher="Example",
        published_at=NOW,
        source_type="government",
        reliability_score=0.95,
        content_hash="b" * 64,
        accessed_at=NOW,
        extraction_method="serper_html_text",
    )
    fact = SimpleNamespace(
        id=uuid.uuid4(),
        claim_text="Afirmação sustentada",
        source_id=source.id,
        source_snapshot_id=snapshot.id,
        confidence_score=0.9,
        approved=True,
        conflict_group=None,
        superseded_by_id=None,
        created_at=NOW,
    )
    editor = SimpleNamespace(output_json={"fidelity_findings": []})

    class PackageDb:
        async def scalars(self, statement):
            assert "FROM research_questions" in str(statement)
            return Rows([question])

        async def execute(self, statement):
            assert "FROM fact_ledger" in str(statement)
            assert "JOIN sources" not in str(statement)
            return Rows([(fact, snapshot, question)])

        async def scalar(self, statement):
            sql = str(statement)
            if "FROM agent_runs" in sql:
                return editor
            if "FROM article_versions" in sql:
                return previous
            raise AssertionError(f"Unexpected scalar: {sql}")

    package = await HumanEditorialReviewService(PackageDb()).build_review_package(
        project, run, article, version
    )

    assert package["facts"][0]["claim"] == "Afirmação sustentada"
    assert package["sources"][0]["title"] == "Fonte oficial capturada"
    assert package["sources"][0]["url"] == "https://example.com/oficial"
    assert package["sources"][0]["reliability_score"] == 0.95
    assert package["facts"][0]["source_snapshot_id"] == str(snapshot.id)
    assert package["coverage"]["complete"] is True
    assert package["conflicts"] == []
    assert package["seo"] == {"title": "SEO atual"}
    assert package["changes"]["previous_version"] == 1
    assert package["changes"]["current_version"] == 2
    assert package["changes"]["previous_title"] == "Versão anterior"
    assert package["changes"]["current_title"] == "Versão atual"
    assert package["changes"]["markdown_changed"] is True
    assert package["changes"]["character_delta"] == len(version.final_markdown)
    assert package["article_version_checksum"] == article_version_checksum(version)
    assert package["risks"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("second_superseded", "expected_unresolved"),
    [(False, True), (True, False)],
)
async def test_quality_evaluator_and_review_package_agree_on_conflicts(
    second_superseded,
    expected_unresolved,
):
    project = SimpleNamespace(id=uuid.uuid4())
    run = SimpleNamespace(id=uuid.uuid4(), project_id=project.id)
    article = SimpleNamespace(id=uuid.uuid4(), project_id=project.id)
    version = SimpleNamespace(
        id=uuid.uuid4(),
        article_id=article.id,
        pipeline_run_id=run.id,
        version=1,
        title="Current version",
        outline=["Evidence"],
        final_markdown="# Evidence\n\nCurrent text.",
        seo_metadata={},
        source_report={"unsupported_claim_count": 0},
        change_reason="test",
    )
    question = SimpleNamespace(
        id=uuid.uuid4(),
        question="Which value is current?",
        priority=1,
        coverage_status="covered",
        created_at=NOW,
    )
    snapshot = SimpleNamespace(
        id=uuid.uuid4(),
        title="Captured source",
        canonical_url="https://example.com/evidence",
        domain="example.com",
        author="Author",
        publisher="Publisher",
        published_at=NOW,
        source_type="scientific",
        reliability_score=0.9,
        content_hash="c" * 64,
        accessed_at=NOW,
        extraction_method="html_text",
        snapshot_text="The measured value is current.",
    )
    first = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=project.id,
        pipeline_run_id=run.id,
        claim_text="The measured value is current.",
        source_id=uuid.uuid4(),
        source_snapshot_id=snapshot.id,
        confidence_score=0.9,
        approved=True,
        conflict_group="measurement",
        superseded_by_id=None,
        created_at=NOW,
    )
    second = SimpleNamespace(
        **{
            **first.__dict__,
            "id": uuid.uuid4(),
            "source_id": uuid.uuid4(),
            "superseded_by_id": first.id if second_superseded else None,
        }
    )
    fact_rows = [(first, snapshot, question), (second, snapshot, question)]

    class PackageDb:
        async def scalars(self, statement):
            assert "FROM research_questions" in str(statement)
            return Rows([question])

        async def execute(self, statement):
            assert "FROM fact_ledger" in str(statement)
            return Rows(fact_rows)

        async def scalar(self, statement):
            sql = str(statement)
            if "FROM agent_runs" in sql or "FROM article_versions" in sql:
                return None
            raise AssertionError(f"Unexpected scalar: {sql}")

    package = await HumanEditorialReviewService(PackageDb()).build_review_package(
        project, run, article, version
    )
    quality_facts = [
        {
            "id": str(fact.id),
            "project_id": str(fact.project_id),
            "pipeline_run_id": str(fact.pipeline_run_id),
            "question_id": str(question.id),
            "claim": fact.claim_text,
            "exact_quote": fact.claim_text,
            "snapshot_text": snapshot.snapshot_text,
            "snapshot_id": str(snapshot.id),
            "approved": fact.approved,
            "same_run": True,
            "conflict_group": fact.conflict_group,
            "superseded": fact.superseded_by_id is not None,
        }
        for fact in (first, second)
    ]
    quality_result = evaluate_snapshot(
        {
            "pipeline_run_id": str(run.id),
            "project": {
                "id": str(project.id),
                "topic": "measurement",
                "audience": "auditors",
                "search_intent": "informational",
                "content_type": "article",
            },
            "version": {
                "title": version.title,
                "outline": version.outline,
                "markdown": version.final_markdown,
                "seo": {},
            },
            "questions": [
                {
                    "id": str(question.id),
                    "priority": 1,
                    "coverage_status": "covered",
                }
            ],
            "facts": quality_facts,
            "claims": [],
            "comparison_documents": [],
            "voice": [],
        },
        quality_rubric_manifest(),
    )
    quality_blocker = next(
        (
            blocker
            for blocker in quality_result["critical_blockers"]
            if blocker["code"] == "unresolved_conflict"
        ),
        None,
    )

    assert bool(package["conflicts"]) is expected_unresolved
    assert (quality_blocker is not None) is expected_unresolved
    if expected_unresolved:
        active_ids = sorted((str(first.id), str(second.id)))
        assert package["conflicts"] == [
            {
                "group": "measurement",
                "active_fact_ids": active_ids,
                "claims": [
                    next(item for item in package["facts"] if item["id"] == fact_id)
                    for fact_id in active_ids
                ],
            }
        ]
        assert quality_blocker["details"]["active_fact_ids"] == {
            "measurement": active_ids
        }


@pytest.mark.asyncio
async def test_human_approval_passes_after_the_only_conflict_is_resolved():
    db, service = review_fixture()
    active_fact_id = str(uuid.uuid4())
    db.review.review_package_json = {
        "article_version_id": str(db.version.id),
        "article_version": db.version.version,
        "article_version_checksum": db.version.content_checksum,
        "pipeline_run_id": str(db.run.id),
        "changes": {"current_title": db.version.title},
        "facts": [
            {
                "id": active_fact_id,
                "conflict_group": "measurement",
                "superseded": False,
            }
        ],
        "conflicts": [],
    }
    db.review.review_package_checksum = review_package_checksum(
        db.review.review_package_json
    )
    db.quality_evaluation.status = "passed"
    db.quality_evaluation.result_json = {
        "critical_blockers": [],
        "axes": {
            "conflicts": {
                "score": 1.0,
                "metrics": {"unresolved_groups": [], "active_fact_ids": {}},
            }
        },
    }

    result = await service.decide(
        db.run.id,
        decision="approve",
        reviewer="Editora Ana",
        observation="Conflito anterior resolvido",
        idempotency_key="approve-resolved-conflict",
    )

    assert result.run.status == PipelineRunStatus.completed
    assert db.review.decision == "approved"


@pytest.mark.asyncio
async def test_idempotency_key_length_is_validated_before_database_access():
    db, service = review_fixture()

    with pytest.raises(ValueError, match="too long"):
        await service.decide(
            db.run.id,
            decision="approve",
            reviewer="Editor humano",
            observation=None,
            idempotency_key="x" * 161,
        )

    assert db.review.decision == "pending"


@pytest.mark.asyncio
async def test_human_approval_is_explicit_and_idempotent():
    db, service = review_fixture()

    first = await service.decide(
        db.run.id,
        decision="approve",
        reviewer="Editora Ana",
        observation="Fontes e texto conferidos",
        idempotency_key="approve-review-1",
    )
    duplicate = await service.decide(
        db.run.id,
        decision="approve",
        reviewer="Editora Ana",
        observation="Fontes e texto conferidos",
        idempotency_key="approve-review-1",
    )

    assert first.run.status == PipelineRunStatus.completed
    assert db.project.status == ProjectStatus.completed
    assert db.article.status == "approved"
    assert db.version.editorial_status == "human_approved"
    assert db.version.sealed_at == db.review.reviewed_at
    assert db.review.reviewer == "Editora Ana"
    assert db.review.reviewed_at is not None
    assert duplicate.duplicate is True
    assert len(FakeEvents.appended) == 1


@pytest.mark.asyncio
async def test_human_approval_rejects_markdown_checksum_drift():
    db, service = review_fixture()
    db.version.final_markdown = "# Mutated after review"

    with pytest.raises(HumanReviewConflict, match="integrity"):
        await service.decide(
            db.run.id,
            decision="approve",
            reviewer="Editora Ana",
            observation="Attempt after drift",
            idempotency_key="approve-drifted-version",
        )

    assert db.review.decision == "pending"
    assert db.version.sealed_at is None


@pytest.mark.asyncio
async def test_human_approval_rejects_review_package_drift():
    db, service = review_fixture()
    db.review.review_package_json["risks"].append(
        {"code": "injected_after_review"}
    )

    with pytest.raises(HumanReviewConflict, match="integrity"):
        await service.decide(
            db.run.id,
            decision="approve",
            reviewer="Editora Ana",
            observation="Attempt after package drift",
            idempotency_key="approve-drifted-package",
        )

    assert db.review.decision == "pending"
    assert db.version.sealed_at is None


@pytest.mark.asyncio
async def test_human_cannot_approve_with_critical_quality_blockers():
    db, service = review_fixture()
    db.quality_evaluation.status = "blocked"
    db.quality_evaluation.result_json = {
        "critical_blockers": [{"code": "claim_not_supported"}]
    }

    with pytest.raises(HumanReviewConflict, match="quality blockers"):
        await service.decide(
            db.run.id,
            decision="approve",
            reviewer="Editora Ana",
            observation="Tentativa de aprovação",
            idempotency_key="approve-blocked-review",
        )

    assert db.review.decision == "pending"


@pytest.mark.asyncio
async def test_different_second_decision_conflicts_with_history():
    db, service = review_fixture()
    await service.decide(
        db.run.id,
        decision="approve",
        reviewer="Editor humano",
        observation=None,
        idempotency_key="first-decision",
    )

    with pytest.raises(HumanReviewConflict, match="final decision"):
        await service.decide(
            db.run.id,
            decision="reject",
            reviewer="Editor humano",
            observation="Mudança posterior indevida",
            idempotency_key="second-decision",
        )


@pytest.mark.asyncio
async def test_rejection_is_terminal_and_records_reason():
    db, service = review_fixture()

    await service.decide(
        db.run.id,
        decision="reject",
        reviewer="Editor responsável",
        observation="Risco factual não aceitável",
        idempotency_key="reject-review-1",
    )

    assert db.run.status == PipelineRunStatus.rejected
    assert db.project.status == ProjectStatus.rejected
    assert db.article.status == "rejected"
    assert db.version.editorial_status == "rejected"
    assert db.review.observation == "Risco factual não aceitável"


@pytest.mark.asyncio
async def test_revision_request_creates_new_run_and_preserves_old_version():
    db, service = review_fixture()
    original_version_id = db.version.id

    result = await service.decide(
        db.run.id,
        decision="request_revision",
        reviewer="Editora-chefe",
        observation="Reescrever a abertura e preservar todas as fontes.",
        idempotency_key="revision-review-1",
    )

    assert db.run.status == PipelineRunStatus.needs_review
    assert db.version.id == original_version_id
    assert db.version.editorial_status == "revision_requested"
    assert result.revision_created is True
    assert result.revision_run is db.revision_run
    assert db.review.revision_run_id == db.revision_run.id
    assert db.project.status == ProjectStatus.queued
    assert service.runs.project_status_at_create == ProjectStatus.needs_review
    assert service.runs.created_metadata == {
        "human_revision": {
            "review_id": str(db.review.id),
            "parent_pipeline_run_id": str(db.run.id),
            "parent_article_version_id": str(db.version.id),
            "reviewer": "Editora-chefe",
            "instructions": "Reescrever a abertura e preservar todas as fontes.",
        }
    }


@pytest.mark.asyncio
async def test_revision_does_not_start_while_another_run_is_active():
    db, service = review_fixture()
    db.other_active = uuid.uuid4()

    with pytest.raises(HumanReviewConflict, match="already active"):
        await service.decide(
            db.run.id,
            decision="request_revision",
            reviewer="Editora-chefe",
            observation="Nova versão necessária.",
            idempotency_key="revision-conflict",
        )

    assert db.run.status == PipelineRunStatus.needs_human_approval
    assert db.review.decision == "pending"
    assert db.review.revision_run_id is None


@pytest.mark.asyncio
async def test_new_run_request_does_not_bypass_pending_human_review(monkeypatch):
    project = SimpleNamespace(
        id=uuid.uuid4(),
        status=ProjectStatus.needs_human_approval,
    )
    run = SimpleNamespace(
        id=uuid.uuid4(),
        status=PipelineRunStatus.needs_human_approval,
    )

    class Db:
        async def get(self, model, identifier):
            assert model is Project
            assert identifier == project.id
            return project

        async def commit(self):
            return None

    class Runs:
        def __init__(self, _db):
            pass

        async def create(self, project_id, idempotency_key, *, trigger_type):
            assert project_id == project.id
            return run, False

    monkeypatch.setattr(routes_module, "PipelineRunService", Runs)

    result = await start_project(
        project.id,
        request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace())),
        db=Db(),
        idempotency_key="new-run",
    )

    assert result["duplicate"] is True
    assert result["pipeline_run_id"] == run.id
    assert project.status == ProjectStatus.needs_human_approval
