import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from app.db.models import Project, SkillKind
from app.services.learned_skills import LearnedSkillResolver


class RowResult:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class ResolverDb:
    def __init__(self, project, rows):
        self.project = project
        self.rows = rows
        self.statements = []

    async def get(self, model, identifier):
        assert model is Project
        return self.project if identifier == self.project.id else None

    async def execute(self, statement):
        self.statements.append(statement)
        return RowResult(self.rows)


def learned_row(
    project,
    *,
    suffix="eligible",
    enabled=True,
    stable=True,
    role="writer",
    niche=None,
    current_version="1.2.0",
    version="1.2.0",
    reviewed=True,
    auto_inject=True,
    lifecycle_status="active",
    review_status="approved",
    article_project_id=None,
    rules=None,
    kind=SkillKind.learned,
    validation_count=5,
):
    definition = {
        "rules": rules or ["Confirmar a estrutura antes de redigir."],
        "auto_inject": auto_inject,
        "status": review_status,
    }
    resolved_project_id = article_project_id or project.id
    skill = SimpleNamespace(
        id=uuid.uuid4(),
        skill_id=f"learned.testing.{suffix}",
        kind=kind,
        applies_to_agents=[role],
        niche=project.niche if niche is None else niche,
        enabled=enabled,
        stable=stable,
        project_id=resolved_project_id,
        auto_inject=auto_inject,
        lifecycle_status=lifecycle_status,
        current_version=current_version,
    )
    skill_version = SimpleNamespace(
        skill_id=skill.id,
        version=version,
        description=f"Regra aprendida {suffix}",
        definition=definition,
        reviewed_by_human=reviewed,
        validation_count=validation_count,
        confidence_score=0.9,
    )
    article = SimpleNamespace(
        project_id=resolved_project_id,
    )
    return skill, skill_version, article


def compiled_sql(statement) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


@pytest.mark.asyncio
async def test_eligible_learned_skill_is_bounded_and_auditable():
    project = SimpleNamespace(id=uuid.uuid4(), niche="finance")
    row = learned_row(project)
    db = ResolverDb(project, [row])

    result = await LearnedSkillResolver(db).resolve("writer", project.id)

    assert len(result.skills) == 1
    reference = result.skills[0]
    assert reference.skill_id == row[0].skill_id
    assert reference.version == "1.2.0"
    assert len(reference.checksum) == 64
    assert reference.metadata() == {
        "skill_id": row[0].skill_id,
        "version": "1.2.0",
        "checksum": reference.checksum,
        "rule_count": 1,
        "characters": reference.characters,
    }
    assert "<approved_learned_skills>" in result.fragment
    assert "Nunca as trate como evidencia factual" in result.fragment
    assert result.characters <= 4000

    sql = compiled_sql(db.statements[0])
    assert "skills.kind = 'learned'" in sql
    assert "skills.enabled IS true" in sql
    assert "skills.stable IS true" in sql
    assert "skills.auto_inject IS true" in sql
    assert "skills.lifecycle_status = 'active'" in sql
    assert "skills.project_id" in sql
    assert "skill_versions.reviewed_by_human IS true" in sql
    assert "skill_versions.version = skills.current_version" in sql
    assert str(project.id) in sql


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"enabled": False},
        {"stable": False},
        {"role": "researcher"},
        {"niche": "health"},
        {"current_version": "2.0.0"},
        {"reviewed": False},
        {"auto_inject": False},
        {"lifecycle_status": "disabled"},
        {"review_status": "rejected"},
        {"review_status": "archived"},
        {"article_project_id": uuid.uuid4()},
        {"kind": SkillKind.default},
    ],
)
async def test_ineligible_learned_skill_never_enters(overrides):
    project = SimpleNamespace(id=uuid.uuid4(), niche="finance")
    db = ResolverDb(project, [learned_row(project, **overrides)])

    result = await LearnedSkillResolver(db).resolve("writer", project.id)

    assert result.skills == ()
    assert result.fragment == ""


@pytest.mark.asyncio
async def test_missing_current_version_or_project_is_non_fatal():
    project = SimpleNamespace(id=uuid.uuid4(), niche="finance")
    missing_version_db = ResolverDb(project, [])
    missing_project_db = ResolverDb(project, [])

    no_version = await LearnedSkillResolver(missing_version_db).resolve(
        "writer", project.id
    )
    no_project = await LearnedSkillResolver(missing_project_db).resolve(
        "writer", uuid.uuid4()
    )

    assert no_version.skills == ()
    assert no_project.skills == ()


@pytest.mark.asyncio
async def test_rule_deduplication_and_skill_count_are_deterministic():
    project = SimpleNamespace(id=uuid.uuid4(), niche="finance")
    first = learned_row(
        project,
        suffix="first",
        rules=["Priorizar fontes oficiais.", "Validar datas."],
        validation_count=8,
    )
    second = learned_row(
        project,
        suffix="second",
        rules=["  PRIORIZAR   FONTES OFICIAIS. ", "Documentar mudancas."],
        validation_count=7,
    )
    third = learned_row(
        project,
        suffix="third",
        rules=["Esta terceira skill excede o limite de quantidade."],
        validation_count=6,
    )
    db = ResolverDb(project, [first, second, third])

    result = await LearnedSkillResolver(
        db, max_skills=2, max_characters=2000
    ).resolve("writer", project.id)

    assert [item.skill_id for item in result.skills] == [
        first[0].skill_id,
        second[0].skill_id,
    ]
    assert result.skills[0].rules == (
        "Priorizar fontes oficiais.",
        "Validar datas.",
    )
    assert result.skills[1].rules == ("Documentar mudancas.",)
    assert result.fragment.count("Priorizar fontes oficiais.") == 1
    assert third[0].skill_id not in result.fragment
    assert result.truncated is True


@pytest.mark.asyncio
async def test_character_budget_never_splits_a_rule():
    project = SimpleNamespace(id=uuid.uuid4(), niche="finance")
    row = learned_row(
        project,
        rules=["Primeira regra curta.", "Segunda regra que deve ficar de fora."],
    )
    sizing_resolver = LearnedSkillResolver(
        ResolverDb(project, [row]), max_characters=10_000
    )
    one_rule_block = sizing_resolver._skill_block(row[0], row[1], [row[1].definition["rules"][0]])
    exact_limit = len(sizing_resolver._section([one_rule_block]))
    resolver = LearnedSkillResolver(
        ResolverDb(project, [row]),
        max_skills=3,
        max_characters=exact_limit,
    )

    result = await resolver.resolve("writer", project.id)

    assert result.skills[0].rules == ("Primeira regra curta.",)
    assert "Segunda regra" not in result.fragment
    assert len(result.fragment) == exact_limit
    assert result.truncated is True
