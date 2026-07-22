import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.routes import list_facts, project_detail
from app.db.models import Project
from app.schemas.api import ProjectDetailRead


NOW = datetime(2026, 7, 13, 18, 0, tzinfo=timezone.utc)


class ScalarRows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class ProjectDetailDb:
    def __init__(
        self,
        project,
        pipeline_runs,
        article,
        article_version,
        *,
        counts=None,
        agent_runs=None,
        facts=None,
        human_reviews=None,
        quality_evaluations=None,
    ):
        self.project = project
        self.pipeline_runs = pipeline_runs
        self.article = article
        self.article_version = article_version
        self.counts = counts or {}
        self.agent_runs = agent_runs or {}
        self.facts = facts or {}
        self.human_reviews = human_reviews or []
        self.quality_evaluations = quality_evaluations or {}
        self.queried_agent_run_ids = []
        self.queried_fact_run_ids = []
        self.article_version_params = {}

    async def get(self, model, identifier):
        if model is Project:
            return self.project if self.project.id == identifier else None
        raise AssertionError(f"Unexpected get for {model}")

    async def scalars(self, statement):
        sql = str(statement)
        params = statement.compile().params
        if "FROM pipeline_runs" in sql:
            return ScalarRows(self.pipeline_runs)
        if "FROM agent_runs" in sql:
            run_id = self._run_id_from(params)
            self.queried_agent_run_ids.append(run_id)
            return ScalarRows(self.agent_runs.get(run_id, []))
        if "FROM fact_ledger" in sql:
            run_id = self._run_id_from(params)
            self.queried_fact_run_ids.append(run_id)
            return ScalarRows(self.facts.get(run_id, []))
        if "FROM human_editorial_reviews" in sql:
            return ScalarRows(self.human_reviews)
        if "FROM agent_handoffs" in sql or "FROM source_snapshots" in sql:
            return ScalarRows([])
        raise AssertionError(f"Unexpected scalars statement: {sql}")

    async def scalar(self, statement):
        sql = str(statement)
        params = statement.compile().params
        if "FROM execution_manifests" in sql:
            return None
        if "FROM pipeline_checkpoints" in sql:
            return None
        if "FROM quality_evaluations" in sql:
            return self.quality_evaluations.get(self._run_id_from(params))
        if "count(fact_ledger.id)" in sql:
            run_id = self._run_id_from(params)
            total, approved = self.counts.get(run_id, (0, 0))
            return approved if "fact_ledger.approved IS true" in sql else total
        if "FROM articles" in sql:
            return self.article
        if "FROM article_versions" in sql:
            self.article_version_params = params
            if self.article_version is None:
                return None
            values = set(params.values())
            if (
                self.article_version.article_id in values
                and self.article_version.version in values
            ):
                return self.article_version
            return None
        if "FROM pipeline_runs" in sql:
            if sql.lstrip().startswith("SELECT pipeline_runs.id"):
                return self.pipeline_runs[0].id if self.pipeline_runs else None
            values = set(params.values())
            return next(
                (
                    run
                    for run in self.pipeline_runs
                    if run.id in values and run.project_id in values
                ),
                None,
            )
        raise AssertionError(f"Unexpected scalar statement: {sql}")

    def _run_id_from(self, params):
        values = set(params.values())
        return next(
            (run.id for run in self.pipeline_runs if run.id in values),
            None,
        )


def project_fixture():
    project_id = uuid.uuid4()
    project = SimpleNamespace(
        id=project_id,
        name="Projeto coerente",
        topic="Origem dos dados",
        search_intent="informational",
        audience="Editores",
        language="pt-BR",
        niche=None,
        content_type="article",
        status="failed",
        current_stage="researcher",
        created_at=NOW,
    )
    prior = pipeline_run(project_id, "completed", NOW, "finalizer")
    latest = pipeline_run(project_id, "failed", NOW + timedelta(minutes=5), "researcher")
    article_id = uuid.uuid4()
    article = SimpleNamespace(
        id=article_id,
        project_id=project_id,
        current_version=2,
        active_pipeline_run_id=latest.id,
        final_markdown="# Conteúdo materializado A",
    )
    version = SimpleNamespace(
        id=uuid.uuid4(),
        article_id=article_id,
        pipeline_run_id=prior.id,
        version=2,
        title="Conteúdo versionado B",
        outline=["Origem"],
        editorial_status="approved",
        final_markdown="# Conteúdo versionado B",
        final_html="<h1>Conteúdo versionado B</h1>",
        seo_metadata={"title": "Conteúdo versionado B"},
        source_report={"fact_count": 1},
        created_at=NOW,
        updated_at=NOW,
    )
    latest_agent = agent_run(project_id, latest.id, "researcher")
    prior_agent = agent_run(project_id, prior.id, "finalizer")
    db = ProjectDetailDb(
        project,
        [latest, prior],
        article,
        version,
        counts={latest.id: (3, 0), prior.id: (5, 5)},
        agent_runs={latest.id: [latest_agent], prior.id: [prior_agent]},
    )
    return db, latest, prior


def pipeline_run(project_id, status, created_at, stage):
    return SimpleNamespace(
        id=uuid.uuid4(),
        project_id=project_id,
        status=status,
        trigger_type="api",
        current_stage=stage,
        attempt=1,
        retryable=False,
        next_retry_at=None,
        cancellation_requested_at=None,
        last_successful_checkpoint=None,
        started_at=created_at,
        finished_at=created_at if status in {"completed", "failed"} else None,
        error_code="TEST_FAILURE" if status == "failed" else None,
        created_at=created_at,
    )


def agent_run(project_id, pipeline_run_id, role):
    return SimpleNamespace(
        id=uuid.uuid4(),
        project_id=project_id,
        pipeline_run_id=pipeline_run_id,
        agent_role=role,
        status="succeeded",
        decision="approved",
        latency_ms=10,
        estimated_cost_usd=0,
        output_json=None,
    )


@pytest.mark.asyncio
async def test_latest_failed_run_never_claims_the_previous_article():
    db, latest, prior = project_fixture()

    result = await project_detail(db.project.id, db=db)
    parsed = ProjectDetailRead.model_validate(result)

    assert parsed.latest_pipeline_run.id == latest.id
    assert parsed.selected_pipeline_run.id == latest.id
    assert parsed.facts.pipeline_run_id == latest.id
    assert parsed.facts.total == 3
    assert {item.pipeline_run_id for item in parsed.runs} == {latest.id}
    assert parsed.article_version.version == db.article.current_version
    assert parsed.article_version.markdown == "# Conteúdo versionado B"
    assert parsed.article_pipeline_run_id == prior.id


@pytest.mark.asyncio
async def test_legacy_failed_research_run_gets_diagnostic_without_mutation():
    db, latest, _prior = project_fixture()
    latest.current_stage = "blocked"
    gatekeeper = agent_run(db.project.id, latest.id, "research_gatekeeper")
    gatekeeper.decision = "insufficient"
    gatekeeper.output_json = {
        "decision": "insufficient",
        "coverage_complete": False,
        "coverage_by_question": {
            "q1": 1,
            "q2": 1,
            "q3": 1,
            "q4": 1,
            "q5": 0,
            "q6": 0,
        },
        "missing_questions": ["Qual profundidade?", "Qual pH?"],
        "unresolved_conflicts": ["germination_depth"],
        "source_diversity_score": 1,
        "approved_fact_ids": [],
        "instructions": ["Pesquisar as duas perguntas ausentes"],
    }
    db.agent_runs[latest.id] = [gatekeeper]

    result = await project_detail(db.project.id, db=db)
    parsed = ProjectDetailRead.model_validate(result)

    assert latest.status == "failed"
    assert latest.error_code == "TEST_FAILURE"
    assert parsed.selected_pipeline_run.status == "failed"
    assert parsed.selected_pipeline_run.outcome_code == "research_insufficient"
    assert parsed.research_diagnostic.outcome_code == "research_insufficient"
    assert parsed.research_diagnostic.covered_question_count == 4
    assert parsed.research_diagnostic.total_question_count == 6
    assert parsed.research_diagnostic.missing_questions == [
        "Qual profundidade?",
        "Qual pH?",
    ]
    assert parsed.article_matches_selected_pipeline_run is False
    assert db.queried_agent_run_ids == [latest.id]


@pytest.mark.asyncio
async def test_explicit_run_selection_scopes_facts_agents_and_article_indicator():
    db, latest, prior = project_fixture()

    result = await project_detail(db.project.id, pipeline_run_id=prior.id, db=db)
    parsed = ProjectDetailRead.model_validate(result)

    assert parsed.latest_pipeline_run.id == latest.id
    assert parsed.selected_pipeline_run.id == prior.id
    assert parsed.facts.pipeline_run_id == prior.id
    assert parsed.facts.total == 5
    assert parsed.facts.approved == 5
    assert {item.pipeline_run_id for item in parsed.runs} == {prior.id}
    assert parsed.article_pipeline_run_id == prior.id
    assert parsed.article_matches_selected_pipeline_run is True
    assert db.queried_agent_run_ids == [prior.id]


@pytest.mark.asyncio
async def test_project_detail_exposes_selected_human_review_and_history():
    db, _latest, prior = project_fixture()
    review = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=db.project.id,
        pipeline_run_id=prior.id,
        article_version_id=db.article_version.id,
        reviewer="Editora Ana",
        decision="approved",
        observation="Conteúdo conferido",
        reviewed_at=NOW,
        revision_run_id=None,
        review_package_json={"coverage": {"complete": True, "questions": []}},
        created_at=NOW,
        updated_at=NOW,
    )
    db.human_reviews = [review]

    result = await project_detail(db.project.id, pipeline_run_id=prior.id, db=db)
    parsed = ProjectDetailRead.model_validate(result)

    assert parsed.human_review is not None
    assert parsed.human_review.reviewer == "Editora Ana"
    assert parsed.human_review.decision == "approved"
    assert parsed.human_review.review_package["coverage"]["complete"] is True
    assert [item.id for item in parsed.human_review_history] == [review.id]


@pytest.mark.asyncio
async def test_project_detail_compares_quality_result_with_human_decision():
    db, _latest, prior = project_fixture()
    review = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=db.project.id,
        pipeline_run_id=prior.id,
        article_version_id=db.article_version.id,
        reviewer="Editora Ana",
        decision="approved",
        observation=None,
        reviewed_at=NOW,
        revision_run_id=None,
        review_package_json={},
        created_at=NOW,
        updated_at=NOW,
    )
    quality = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=db.project.id,
        pipeline_run_id=prior.id,
        article_version_id=db.article_version.id,
        rubric_version="quality-rubric.v1",
        rubric_checksum="a" * 64,
        evaluator_kind="deterministic",
        status="passed",
        overall_score=0.91,
        result_checksum="b" * 64,
        result_json={"axes": {}, "critical_blockers": [], "warnings": []},
        thresholds_json={"min_overall_score": 0.75},
        created_at=NOW,
    )
    db.human_reviews = [review]
    db.quality_evaluations = {prior.id: quality}

    result = await project_detail(db.project.id, pipeline_run_id=prior.id, db=db)
    parsed = ProjectDetailRead.model_validate(result)

    assert parsed.quality_evaluation["rubric_version"] == "quality-rubric.v1"
    assert parsed.quality_evaluation["human_comparison"] == {
        "human_decision": "approved",
        "evaluator_recommendation": "passed",
        "agreement": True,
    }


@pytest.mark.asyncio
async def test_project_detail_rejects_run_from_another_project_before_queries():
    db, _latest, _prior = project_fixture()

    with pytest.raises(HTTPException) as exc:
        await project_detail(db.project.id, pipeline_run_id=uuid.uuid4(), db=db)

    assert exc.value.status_code == 404
    assert exc.value.detail == "Pipeline run not found for project"
    assert db.queried_agent_run_ids == []
    assert db.article_version_params == {}


@pytest.mark.asyncio
async def test_article_without_run_has_explicit_unknown_origin():
    db, _latest, _prior = project_fixture()
    db.article_version.pipeline_run_id = None

    result = await project_detail(db.project.id, db=db)
    parsed = ProjectDetailRead.model_validate(result)

    assert parsed.article_version is not None
    assert parsed.article_version.pipeline_run_id is None
    assert parsed.article_pipeline_run_id is None
    assert parsed.article_matches_selected_pipeline_run is False


@pytest.mark.asyncio
async def test_facts_reject_run_from_another_project_without_ledger_query():
    db, _latest, _prior = project_fixture()
    foreign_project_id = uuid.uuid4()
    foreign_run = pipeline_run(foreign_project_id, "completed", NOW, "finalizer")
    db.pipeline_runs.append(foreign_run)

    with pytest.raises(HTTPException) as exc:
        await list_facts(
            db.project.id,
            pipeline_run_id=foreign_run.id,
            db=db,
        )

    assert exc.value.status_code == 404
    assert exc.value.detail == "Pipeline run not found for project"
    assert db.queried_fact_run_ids == []


@pytest.mark.asyncio
async def test_facts_from_valid_explicit_run_include_their_origin():
    db, latest, _prior = project_fixture()
    fact = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=db.project.id,
        pipeline_run_id=latest.id,
        claim_text="Fato do run selecionado",
        source_id=uuid.uuid4(),
        source_snapshot_id=uuid.uuid4(),
        confidence_score=0.9,
        approved=False,
        source_locator="p. 1",
        conflict_group=None,
        created_at=NOW,
    )
    db.facts[latest.id] = [fact]

    result = await list_facts(db.project.id, pipeline_run_id=latest.id, db=db)

    assert result[0]["project_id"] == db.project.id
    assert result[0]["pipeline_run_id"] == latest.id
    assert result[0]["source_snapshot_id"] == fact.source_snapshot_id
    assert db.queried_fact_run_ids == [latest.id]
