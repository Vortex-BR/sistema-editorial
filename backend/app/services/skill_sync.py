from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Skill, SkillKind, SkillVersion
from app.services.skill_registry import SkillRegistry


async def sync_default_skills(db: AsyncSession) -> int:
    root = Path(settings.skills_path)
    if not root.exists():
        local_root = Path(__file__).resolve().parents[3] / "skills" / "default"
        root = local_root if local_root.exists() else root
    definitions = SkillRegistry(str(root)).load_defaults()
    changed = 0
    for definition in definitions.values():
        skill = await db.scalar(
            select(Skill).where(Skill.skill_id == definition.skill_id)
        )
        if skill is None:
            skill = Skill(
                skill_id=definition.skill_id,
                kind=SkillKind.default,
                applies_to_agents=definition.applies_to_agent,
                enabled=True,
                stable=True,
                current_version=definition.version,
            )
            db.add(skill)
            await db.flush()
        version_exists = await db.scalar(
            select(SkillVersion.id).where(
                SkillVersion.skill_id == skill.id,
                SkillVersion.version == definition.version,
            )
        )
        if version_exists is None:
            db.add(
                SkillVersion(
                    skill_id=skill.id,
                    version=definition.version,
                    description=definition.description,
                    definition=definition.model_dump(mode="json"),
                    confidence_score=1,
                    validation_count=0,
                    reviewed_by_human=True,
                )
            )
            changed += 1
        skill.current_version = definition.version
        skill.applies_to_agents = definition.applies_to_agent
    await db.commit()
    return changed
