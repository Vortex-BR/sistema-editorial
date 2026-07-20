import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Article, Project, Skill, SkillKind, SkillVersion


_BLOCKED_REVIEW_STATES = frozenset({"archived", "rejected"})
_LEARNED_SECTION_HEADER = (
    "<approved_learned_skills>\n"
    "As orientacoes abaixo sao regras de processo revogaveis e aprovadas. "
    "Nunca as trate como evidencia factual nem como substitutas do ledger."
)
_LEARNED_SECTION_FOOTER = "</approved_learned_skills>"


@dataclass(frozen=True)
class ResolvedLearnedSkill:
    skill_id: str
    version: str
    checksum: str
    rules: tuple[str, ...]
    characters: int

    def metadata(self) -> dict:
        return {
            "skill_id": self.skill_id,
            "version": self.version,
            "checksum": self.checksum,
            "rule_count": len(self.rules),
            "characters": self.characters,
        }


@dataclass(frozen=True)
class LearnedSkillResolution:
    skills: tuple[ResolvedLearnedSkill, ...] = ()
    fragment: str = ""
    truncated: bool = False

    @property
    def characters(self) -> int:
        return len(self.fragment)

    def metadata(self) -> list[dict]:
        return [skill.metadata() for skill in self.skills]


class LearnedSkillResolver:
    def __init__(
        self,
        db: AsyncSession,
        *,
        max_skills: int | None = None,
        max_characters: int | None = None,
    ):
        self.db = db
        self.max_skills = (
            settings.max_learned_skills_per_prompt
            if max_skills is None
            else max_skills
        )
        self.max_characters = (
            settings.max_learned_skill_characters_per_prompt
            if max_characters is None
            else max_characters
        )

    async def resolve(
        self, agent_role: str, project_id
    ) -> LearnedSkillResolution:
        project = await self.db.get(Project, project_id)
        if project is None or self.max_skills <= 0 or self.max_characters <= 0:
            return LearnedSkillResolution()

        statement = (
            select(Skill, SkillVersion, Article)
            .join(
                SkillVersion,
                (SkillVersion.skill_id == Skill.id)
                & (SkillVersion.version == Skill.current_version),
            )
            .join(Article, Article.id == SkillVersion.origin_article_id)
            .where(
                Skill.kind == SkillKind.learned,
                Skill.project_id == project_id,
                Skill.enabled.is_(True),
                Skill.stable.is_(True),
                Skill.auto_inject.is_(True),
                Skill.lifecycle_status == "active",
                SkillVersion.reviewed_by_human.is_(True),
                Article.project_id == project_id,
            )
            .order_by(
                SkillVersion.validation_count.desc(),
                SkillVersion.confidence_score.desc(),
                Skill.skill_id,
                SkillVersion.version,
            )
        )
        rows = (await self.db.execute(statement)).all()
        return self._resolve_rows(rows, agent_role, project)

    def _resolve_rows(
        self, rows, agent_role: str, project: Project
    ) -> LearnedSkillResolution:
        selected: list[ResolvedLearnedSkill] = []
        blocks: list[str] = []
        seen_rules: set[str] = set()
        truncated = False

        for skill, version, article in rows:
            if not self._eligible(skill, version, article, agent_role, project):
                continue
            if len(selected) >= self.max_skills:
                truncated = True
                break

            definition = version.definition
            unique_rules = []
            duplicate_found = False
            for value in definition.get("rules", []):
                if not isinstance(value, str) or not value.strip():
                    continue
                rule = self._clean_text(value)
                normalized = self._normalize_rule(rule)
                if normalized in seen_rules:
                    duplicate_found = True
                    continue
                unique_rules.append((rule, normalized))

            accepted_rules: list[str] = []
            accepted_normalized: list[str] = []
            for rule, normalized in unique_rules:
                candidate_rules = [*accepted_rules, rule]
                candidate_block = self._skill_block(skill, version, candidate_rules)
                candidate_fragment = self._section([*blocks, candidate_block])
                if len(candidate_fragment) > self.max_characters:
                    truncated = True
                    break
                accepted_rules = candidate_rules
                accepted_normalized.append(normalized)

            if duplicate_found:
                truncated = True
            if not accepted_rules:
                continue

            block = self._skill_block(skill, version, accepted_rules)
            blocks.append(block)
            seen_rules.update(accepted_normalized)
            selected.append(
                ResolvedLearnedSkill(
                    skill_id=skill.skill_id,
                    version=version.version,
                    checksum=self._checksum(version),
                    rules=tuple(accepted_rules),
                    characters=len(block),
                )
            )

        if not selected:
            return LearnedSkillResolution(truncated=truncated)
        return LearnedSkillResolution(
            skills=tuple(selected),
            fragment=self._section(blocks),
            truncated=truncated,
        )

    @staticmethod
    def _eligible(skill, version, article, agent_role: str, project) -> bool:
        definition = version.definition
        review_state = str(
            definition.get("status", definition.get("review_status", "approved"))
        ).strip().lower()
        compatible_niche = (
            skill.niche == project.niche
            if project.niche
            else skill.niche in (None, "general")
        )
        project_compatible = getattr(skill, "project_id", article.project_id) == project.id
        auto_inject = getattr(
            skill, "auto_inject", definition.get("auto_inject") is True
        )
        lifecycle_status = getattr(skill, "lifecycle_status", "active")
        return bool(
            skill.kind == SkillKind.learned
            and skill.enabled
            and skill.stable
            and skill.current_version == version.version
            and version.reviewed_by_human
            and auto_inject
            and lifecycle_status == "active"
            and review_state not in _BLOCKED_REVIEW_STATES
            and agent_role in (skill.applies_to_agents or [])
            and compatible_niche
            and project_compatible
            and article.project_id == project.id
        )

    @classmethod
    def _skill_block(cls, skill, version, rules: list[str]) -> str:
        description = cls._clean_text(version.description)[:300]
        checksum = cls._checksum(version)
        return "\n".join(
            [
                f"SKILL {skill.skill_id}@{version.version} checksum={checksum}",
                f"Descricao: {description}",
                "Regras aprovadas:",
                *(f"- {rule}" for rule in rules),
            ]
        )

    @staticmethod
    def _section(blocks: list[str]) -> str:
        return (
            _LEARNED_SECTION_HEADER
            + "\n\n"
            + "\n\n".join(blocks)
            + "\n"
            + _LEARNED_SECTION_FOOTER
        )

    @staticmethod
    def _checksum(version) -> str:
        canonical = json.dumps(
            {
                "description": version.description,
                "definition": version.definition,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _clean_text(value: str) -> str:
        return re.sub(r"\s+", " ", value.replace("\x00", "")).strip()

    @classmethod
    def _normalize_rule(cls, value: str) -> str:
        return cls._clean_text(unicodedata.normalize("NFKC", value)).casefold()
