import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import SuperiorSkill, SuperiorSkillScope, SuperiorSkillVersion

from app.services.editorial_roles import ALL_AGENT_ROLES


AGENT_ROLES = set(ALL_AGENT_ROLES)


class SuperiorSkillDefinition(BaseModel):
    skill_id: str = Field(pattern=r"^superior\.[a-z0-9-]+$")
    scope: Literal["global_core", "agent"]
    agent_role: str | None
    version: str
    title: str
    mission: str
    expertise: list[str] = Field(min_length=1)
    responsibilities: list[str] = Field(min_length=1)
    boundaries: list[str] = Field(min_length=1)
    decision_protocol: list[str] = Field(min_length=1)
    memory_policy: list[str] = Field(min_length=1)
    handoff_policy: list[str] = Field(min_length=1)
    voice: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_scope(self):
        if self.scope == "global_core" and self.agent_role is not None:
            raise ValueError("Global core cannot have an agent role")
        if self.scope == "agent" and self.agent_role not in AGENT_ROLES:
            raise ValueError("Agent superior skill requires a supported role")
        return self

    def checksum(self) -> str:
        canonical = json.dumps(
            self.model_dump(mode="json"), ensure_ascii=False, sort_keys=True
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def prompt_fragment(self) -> str:
        sections = [
            f"{self.title} ({self.skill_id}@{self.version})",
            f"Missão: {self.mission}",
            "Expertise:\n" + "\n".join(f"- {x}" for x in self.expertise),
            "Responsabilidades:\n"
            + "\n".join(f"- {x}" for x in self.responsibilities),
            "Limites:\n" + "\n".join(f"- {x}" for x in self.boundaries),
            "Método de decisão:\n"
            + "\n".join(f"- {x}" for x in self.decision_protocol),
            "Política de memória:\n"
            + "\n".join(f"- {x}" for x in self.memory_policy),
            "Handoff:\n" + "\n".join(f"- {x}" for x in self.handoff_policy),
            "Voz: " + ", ".join(self.voice),
        ]
        return "\n".join(sections)


class SuperiorSkillRegistry:
    def __init__(self, root: str | None = None):
        self.root = Path(root or settings.superior_skills_path)
        if not self.root.exists():
            local = Path(__file__).resolve().parents[3] / "skills" / "superior"
            if local.exists():
                self.root = local

    def load_defaults(self) -> dict[str, SuperiorSkillDefinition]:
        definitions = {}
        for path in sorted(self.root.glob("*.yaml")) if self.root.exists() else []:
            definition = SuperiorSkillDefinition.model_validate(
                yaml.safe_load(path.read_text(encoding="utf-8"))
            )
            if definition.skill_id in definitions:
                raise ValueError(f"Duplicate superior skill: {definition.skill_id}")
            definitions[definition.skill_id] = definition
        global_count = sum(x.scope == "global_core" for x in definitions.values())
        roles = {x.agent_role for x in definitions.values() if x.scope == "agent"}
        if global_count != 1 or roles != AGENT_ROLES:
            raise ValueError("Superior skills require one global core and one per LLM role")
        return definitions


async def sync_superior_skills(db: AsyncSession) -> int:
    definitions = SuperiorSkillRegistry().load_defaults()
    changed = 0
    for definition in definitions.values():
        row = await db.scalar(
            select(SuperiorSkill).where(SuperiorSkill.skill_id == definition.skill_id)
        )
        if row is None:
            row = SuperiorSkill(
                skill_id=definition.skill_id,
                scope=SuperiorSkillScope(definition.scope),
                agent_role=definition.agent_role,
                enabled=True,
                current_version=definition.version,
            )
            db.add(row)
            await db.flush()
        current = await db.scalar(
            select(SuperiorSkillVersion).where(
                SuperiorSkillVersion.superior_skill_id == row.id,
                SuperiorSkillVersion.version == row.current_version,
            )
        )
        version = await db.scalar(
            select(SuperiorSkillVersion).where(
                SuperiorSkillVersion.superior_skill_id == row.id,
                SuperiorSkillVersion.version == definition.version,
            )
        )
        if version is None:
            version = SuperiorSkillVersion(
                superior_skill_id=row.id,
                version=definition.version,
                definition=definition.model_dump(mode="json"),
                checksum=definition.checksum(),
                status=(
                    "active"
                    if row.current_version == definition.version
                    else "draft"
                ),
                reviewed_by_human=True,
                approved_at=datetime.now(timezone.utc),
                created_by="repository-seed",
            )
            db.add(version)
            changed += 1
            await db.flush()
        # Repository-owned personas may advance with the application. A version
        # selected or created by a human remains untouched.
        if current is None or current.created_by == "repository-seed":
            if current is not None and current.id != version.id:
                current.status = "archived"
            version.status = "active"
            version.reviewed_by_human = True
            version.approved_at = version.approved_at or datetime.now(
                timezone.utc
            )
            row.current_version = definition.version
    await db.commit()
    return changed


async def active_superior_definitions(
    db: AsyncSession, agent_role: str
) -> list[SuperiorSkillDefinition]:
    rows = (
        await db.execute(
            select(SuperiorSkill, SuperiorSkillVersion)
            .join(
                SuperiorSkillVersion,
                (SuperiorSkillVersion.superior_skill_id == SuperiorSkill.id)
                & (SuperiorSkillVersion.version == SuperiorSkill.current_version),
            )
            .where(
                SuperiorSkill.enabled.is_(True),
                SuperiorSkillVersion.status == "active",
                (SuperiorSkill.scope == SuperiorSkillScope.global_core)
                | (SuperiorSkill.agent_role == agent_role),
            )
            .order_by(SuperiorSkill.scope, SuperiorSkill.skill_id)
        )
    ).all()
    return [SuperiorSkillDefinition.model_validate(version.definition) for _, version in rows]
