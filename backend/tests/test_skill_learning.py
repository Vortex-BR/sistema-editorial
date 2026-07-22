import uuid
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.core.config import settings
from app.db.models import (
    Skill,
    SkillKind,
    SkillLifecycleEvent,
    SkillValidation,
    SkillVersion,
)
from app.services.learned_skills import LearnedSkillResolver
from app.services.skill_learning import (
    PipelineOutcomeSignals,
    SkillLearningInputError,
    SkillLearningService,
    SkillLifecycleConflict,
)


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class LifecycleDb:
    def __init__(self, article_versions):
        self.article_versions = article_versions
        self.skill = None
        self.version = None
        self.validations = []
        self.events = []

    async def scalar(self, statement):
        sql = str(statement)
        params = statement.compile().params
        if "FROM skills" in sql and "skill_versions" not in sql:
            return self.skill
        if "FROM skill_versions" in sql:
            return self.version
        if "SELECT skill_validations.id" in sql:
            run_id = self._matching_uuid(params, set(self.article_versions))
            return next(
                (
                    row.id
                    for row in self.validations
                    if row.pipeline_run_id == run_id
                ),
                None,
            )
        if "FROM article_versions" in sql:
            run_id = self._matching_uuid(params, set(self.article_versions))
            return self.article_versions.get(run_id)
        raise AssertionError(f"Unexpected scalar query: {sql}")

    async def scalars(self, statement):
        sql = str(statement)
        if "FROM skill_validations" in sql:
            return Rows(list(self.validations))
        raise AssertionError(f"Unexpected scalars query: {sql}")

    async def execute(self, statement):
        sql = str(statement)
        if "FROM skills JOIN skill_versions" in sql:
            return Rows([])
        raise AssertionError(f"Unexpected execute query: {sql}")

    def add(self, instance):
        if getattr(instance, "id", None) is None:
            instance.id = uuid.uuid4()
        if isinstance(instance, Skill):
            self.skill = instance
        elif isinstance(instance, SkillVersion):
            self.version = instance
        elif isinstance(instance, SkillValidation):
            self.validations.append(instance)
        elif isinstance(instance, SkillLifecycleEvent):
            self.events.append(instance)
        else:
            raise AssertionError(f"Unexpected instance: {instance}")

    async def flush(self):
        return None

    @staticmethod
    def _matching_uuid(params, candidates):
        return next((value for value in params.values() if value in candidates), None)


def article_for(project, *, current_version=1):
    return SimpleNamespace(
        id=uuid.uuid4(),
        project_id=project.id,
        current_version=current_version,
    )


def project_article():
    project = SimpleNamespace(id=uuid.uuid4(), niche="finance")
    article = article_for(project)
    return project, article


def candidate(article_id, *, title="Validar estrutura", rules=None):
    return {
        "niche": "finance",
        "title": title,
        "rules": rules or ["Confirmar a estrutura antes de redigir."],
        "evidence_article_id": str(article_id),
        "confidence_score": 0.8,
        "auto_inject": False,
    }


def approved_outcome(*, rework=1, score=0.9):
    return PipelineOutcomeSignals(
        editorial_decision="approved",
        editorial_rework_count=rework,
        rubric_score=score,
        factual_regression=False,
        unsupported_claim_count=0,
        major_fidelity_findings=0,
        critical_fidelity_findings=0,
    )


def article_version(article, run_id, version):
    return SimpleNamespace(
        id=uuid.uuid4(),
        article_id=article.id,
        pipeline_run_id=run_id,
        version=version,
    )


@pytest.mark.asyncio
async def test_equivalent_candidates_share_fingerprint_and_independent_evidence(
    monkeypatch,
):
    monkeypatch.setattr(settings, "learned_skill_stability_threshold", 3)
    project, _ = project_article()
    run_ids = [uuid.uuid4() for _ in range(3)]
    articles = [article_for(project, current_version=index) for index in range(1, 4)]
    versions = {
        run_id: article_version(article, run_id, index)
        for index, (run_id, article) in enumerate(
            zip(run_ids, articles), start=1
        )
    }
    db = LifecycleDb(versions)
    service = SkillLearningService(db)

    candidates = [
        candidate(
            articles[0].id,
            title="Primeira redacao",
            rules=["Confirmar fontes.", "Revisar estrutura."],
        ),
        candidate(
            articles[1].id,
            title="Mesmo aprendizado com outro titulo",
            rules=["  revisar   ESTRUTURA. ", "CONFIRMAR FONTES."],
        ),
        candidate(
            articles[2].id,
            title="Terceira corroboracao",
            rules=["Confirmar fontes.", "Revisar estrutura."],
        ),
    ]
    returned = []
    for index, (run_id, article, payload) in enumerate(
        zip(run_ids, articles, candidates), start=1
    ):
        returned.append(
            await service.record_candidate(
                project=project,
                pipeline_run_id=run_id,
                article=article,
                candidate=payload,
                outcome=approved_outcome(rework=3 - index, score=0.7 + index / 10),
            )
        )

    assert returned == [db.skill, db.skill, db.skill]
    assert db.skill.fingerprint == SkillLearningService.fingerprint(
        niche="finance",
        applies_to_agents=("researcher", "research_gatekeeper"),
        rules=["REVISAR ESTRUTURA.", "confirmar fontes."],
    )
    assert len(db.validations) == 3
    assert len({row.pipeline_run_id for row in db.validations}) == 3
    assert db.version.validation_count == 3
    assert db.skill.lifecycle_status == "corroborated"
    assert db.skill.stable is False
    assert db.skill.enabled is False
    assert db.skill.auto_inject is False
    assert db.version.reviewed_by_human is False
    assert db.validations[0].outcome_json["correlation_only"] is True
    assert db.validations[1].outcome_json["rework_reduced"] is True
    assert db.validations[1].outcome_json["rubric_improved"] is True
    assert [event.action for event in db.events].count("candidate_created") == 1
    assert [event.action for event in db.events].count("corroborated") == 1


@pytest.mark.asyncio
async def test_same_run_never_counts_twice(monkeypatch):
    monkeypatch.setattr(settings, "learned_skill_stability_threshold", 3)
    project, article = project_article()
    run_id = uuid.uuid4()
    db = LifecycleDb({run_id: article_version(article, run_id, 1)})
    service = SkillLearningService(db)
    payload = candidate(article.id)

    await service.record_candidate(
        project=project,
        pipeline_run_id=run_id,
        article=article,
        candidate=payload,
        outcome=approved_outcome(),
    )
    await service.record_candidate(
        project=project,
        pipeline_run_id=run_id,
        article=article,
        candidate=payload,
        outcome=approved_outcome(),
    )

    assert len(db.validations) == 1
    assert db.version.validation_count == 1
    assert [event.action for event in db.events].count("validation_recorded") == 1


@pytest.mark.asyncio
async def test_missing_same_run_article_version_creates_no_candidate(monkeypatch):
    monkeypatch.setattr(settings, "learned_skill_stability_threshold", 3)
    project, article = project_article()
    db = LifecycleDb({})

    with pytest.raises(
        SkillLearningInputError,
        match="article version from the same pipeline run",
    ):
        await SkillLearningService(db).record_candidate(
            project=project,
            pipeline_run_id=uuid.uuid4(),
            article=article,
            candidate=candidate(article.id),
            outcome=approved_outcome(),
        )

    assert db.skill is None
    assert db.version is None
    assert db.validations == []
    assert db.events == []


@pytest.mark.asyncio
async def test_factual_regression_is_recorded_but_never_corroborates(monkeypatch):
    monkeypatch.setattr(settings, "learned_skill_stability_threshold", 3)
    project, article = project_article()
    run_id = uuid.uuid4()
    db = LifecycleDb({run_id: article_version(article, run_id, 1)})
    service = SkillLearningService(db)
    regressed = PipelineOutcomeSignals(
        editorial_decision="approved",
        editorial_rework_count=0,
        rubric_score=0.4,
        factual_regression=True,
        unsupported_claim_count=1,
        major_fidelity_findings=1,
        critical_fidelity_findings=0,
    )

    await service.record_candidate(
        project=project,
        pipeline_run_id=run_id,
        article=article,
        candidate=candidate(article.id),
        outcome=regressed,
    )

    assert len(db.validations) == 1
    assert db.validations[0].factual_regression is True
    assert db.validations[0].corroborating is False
    assert db.version.validation_count == 0
    assert db.skill.lifecycle_status == "candidate"


@pytest.mark.asyncio
async def test_threshold_without_human_review_never_activates(monkeypatch):
    monkeypatch.setattr(settings, "learned_skill_stability_threshold", 3)
    project, _ = project_article()
    run_ids = [uuid.uuid4() for _ in range(3)]
    articles = [article_for(project, current_version=index) for index in range(1, 4)]
    db = LifecycleDb(
        {
            run_id: article_version(article, run_id, index)
            for index, (run_id, article) in enumerate(
                zip(run_ids, articles), start=1
            )
        }
    )
    service = SkillLearningService(db)
    for run_id, article in zip(run_ids, articles):
        await service.record_candidate(
            project=project,
            pipeline_run_id=run_id,
            article=article,
            candidate=candidate(article.id),
            outcome=approved_outcome(),
        )

    assert db.skill.lifecycle_status == "corroborated"
    assert db.skill.stable is False
    assert db.skill.enabled is False
    with pytest.raises(SkillLifecycleConflict, match="activate"):
        await service.apply_action(
            db.skill.skill_id,
            "activate",
            reason="Attempt to skip human review",
        )


@pytest.mark.asyncio
async def test_multiple_runs_from_one_article_never_corroborate(monkeypatch):
    monkeypatch.setattr(settings, "learned_skill_stability_threshold", 3)
    monkeypatch.setattr(settings, "learned_skill_min_independent_articles", 2)
    project, article = project_article()
    run_ids = [uuid.uuid4() for _ in range(3)]
    db = LifecycleDb(
        {
            run_id: article_version(article, run_id, index)
            for index, run_id in enumerate(run_ids, start=1)
        }
    )
    service = SkillLearningService(db)

    for index, run_id in enumerate(run_ids, start=1):
        article.current_version = index
        await service.record_candidate(
            project=project,
            pipeline_run_id=run_id,
            article=article,
            candidate=candidate(article.id),
            outcome=approved_outcome(),
        )

    assert db.version.validation_count == 3
    assert db.skill.lifecycle_status == "candidate"
    assert db.skill.stable is False
    assert [event.action for event in db.events].count("corroborated") == 0


@pytest.mark.asyncio
async def test_human_approval_promotion_activation_and_rollback_are_auditable(
    monkeypatch,
):
    monkeypatch.setattr(settings, "learned_skill_stability_threshold", 3)
    project, article = project_article()
    db = LifecycleDb({})
    db.skill = Skill(
        id=uuid.uuid4(),
        skill_id="learned.finance.lifecycle",
        kind=SkillKind.learned,
        project_id=project.id,
        applies_to_agents=["writer"],
        niche="finance",
        fingerprint="f" * 64,
        lifecycle_status="corroborated",
        auto_inject=False,
        enabled=False,
        stable=False,
        current_version="1.0.0",
    )
    db.version = SkillVersion(
        id=uuid.uuid4(),
        skill_id=db.skill.id,
        version="1.0.0",
        description="Lifecycle rule",
        definition={"rules": ["Regra validada"], "auto_inject": False},
        confidence_score=0.9,
        validation_count=3,
        reviewed_by_human=False,
    )
    db.validations = [
        SkillValidation(
            id=uuid.uuid4(),
            skill_version_id=db.version.id,
            pipeline_run_id=uuid.uuid4(),
            article_id=uuid.uuid4(),
            article_version_id=uuid.uuid4(),
            editorial_rework_count=index,
            rubric_score=0.9,
            factual_regression=False,
            corroborating=True,
            outcome_json={},
        )
        for index in range(3)
    ]
    service = SkillLearningService(db)

    await service.apply_action(
        db.skill.skill_id, "approve", reason="Outcomes reviewed by editor"
    )
    assert db.skill.lifecycle_status == "human_approved"
    assert db.version.reviewed_by_human is True
    assert db.skill.stable is False

    await service.apply_action(
        db.skill.skill_id, "promote", reason="Evidence threshold confirmed"
    )
    assert db.skill.lifecycle_status == "stable"
    assert db.skill.stable is True
    assert db.skill.enabled is False

    await service.apply_action(
        db.skill.skill_id, "activate", reason="Explicit auto-inject authorization"
    )
    assert db.skill.lifecycle_status == "active"
    assert db.skill.enabled is True
    assert db.skill.auto_inject is True
    assert LearnedSkillResolver._eligible(
        db.skill,
        db.version,
        SimpleNamespace(project_id=project.id),
        "writer",
        project,
    )

    await service.apply_action(
        db.skill.skill_id, "rollback", reason="Observed editorial regression"
    )
    assert db.skill.lifecycle_status == "disabled"
    assert db.skill.enabled is False
    assert db.skill.auto_inject is False
    assert not LearnedSkillResolver._eligible(
        db.skill,
        db.version,
        SimpleNamespace(project_id=project.id),
        "writer",
        project,
    )
    assert [event.action for event in db.events] == [
        "approve",
        "promote",
        "activate",
        "rollback",
    ]
    assert all(event.actor == "admin-api" for event in db.events)
    assert all(event.reason for event in db.events)


@pytest.mark.asyncio
async def test_human_can_reject_and_regression_blocks_approval(monkeypatch):
    monkeypatch.setattr(settings, "learned_skill_stability_threshold", 3)
    project, article = project_article()
    db = LifecycleDb({})
    db.skill = Skill(
        id=uuid.uuid4(),
        skill_id="learned.finance.reject",
        kind=SkillKind.learned,
        project_id=project.id,
        applies_to_agents=["writer"],
        niche="finance",
        fingerprint="e" * 64,
        lifecycle_status="corroborated",
        auto_inject=False,
        enabled=False,
        stable=False,
        current_version="1.0.0",
    )
    db.version = SkillVersion(
        id=uuid.uuid4(),
        skill_id=db.skill.id,
        version="1.0.0",
        description="Rejected rule",
        definition={"rules": ["Regra"], "auto_inject": False},
        confidence_score=0.9,
        validation_count=3,
        reviewed_by_human=False,
    )
    db.validations = [
        SkillValidation(
            id=uuid.uuid4(),
            skill_version_id=db.version.id,
            pipeline_run_id=uuid.uuid4(),
            article_id=uuid.uuid4(),
            article_version_id=uuid.uuid4(),
            editorial_rework_count=0,
            rubric_score=0.9,
            factual_regression=index == 2,
            corroborating=True,
            outcome_json={},
        )
        for index in range(3)
    ]
    service = SkillLearningService(db)

    with pytest.raises(SkillLifecycleConflict, match="regression"):
        await service.apply_action(
            db.skill.skill_id, "approve", reason="Review outcomes"
        )
    await service.apply_action(
        db.skill.skill_id, "reject", reason="Factual regression observed"
    )

    assert db.skill.lifecycle_status == "rejected"
    assert db.skill.enabled is False
    assert db.skill.stable is False
    assert db.version.reviewed_by_human is False
    assert db.events[-1].action == "reject"


def test_pipeline_outcome_separates_correlation_from_causality():
    state = SimpleNamespace(
        editorial_review={
            "decision": "approved",
            "fidelity_findings": [],
            "language_findings": [
                {"severity": "minor"},
            ],
        },
        final_package={"unsupported_claim_count": 0},
        editor_cycle=1,
    )

    outcome = PipelineOutcomeSignals.from_pipeline_state(state)

    assert outcome.corroborates is True
    assert outcome.editorial_rework_count == 1
    assert outcome.rubric_score == 0.89
    assert outcome.factual_regression is False


def test_stability_threshold_can_be_raised_but_never_reduced_to_one_article():
    from app.core.config import Settings

    configured = Settings(
        _env_file=None,
        learned_skill_stability_threshold=5,
        learned_skill_min_independent_articles=4,
    )
    assert configured.learned_skill_stability_threshold == 5
    assert configured.learned_skill_min_independent_articles == 4
    with pytest.raises(ValidationError):
        Settings(_env_file=None, learned_skill_stability_threshold=1)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, learned_skill_min_independent_articles=1)
