from pathlib import Path
import yaml
from pydantic import BaseModel, Field


class SkillDefinition(BaseModel):
    skill_id: str
    version: str
    applies_to_agent: list[str]
    description: str
    rules: list[str] = Field(min_length=1)
    examples_good: list[str] = Field(default_factory=list)
    examples_bad: list[str] = Field(default_factory=list)
    llm_hint_template: str


class SkillRegistry:
    def __init__(
        self,
        root: str = "/app/skills/default",
        *,
        definitions: dict[str, SkillDefinition] | None = None,
    ):
        self.root = Path(root)
        self.definitions = definitions
        if not self.root.exists():
            local_root = Path(__file__).resolve().parents[3] / "skills" / self.root.name
            if local_root.exists():
                self.root = local_root

    def load_defaults(self) -> dict[str, SkillDefinition]:
        if self.definitions is not None:
            return dict(self.definitions)
        loaded: dict[str, SkillDefinition] = {}
        if not self.root.exists():
            return loaded
        for path in sorted(self.root.glob("*.yaml")):
            skill = SkillDefinition.model_validate(
                yaml.safe_load(path.read_text(encoding="utf-8"))
            )
            if skill.skill_id in loaded:
                raise ValueError(f"Duplicate default skill: {skill.skill_id}")
            loaded[skill.skill_id] = skill
        return loaded

    def prompt_fragment(self, agent_role: str) -> str:
        relevant = [
            s for s in self.load_defaults().values() if agent_role in s.applies_to_agent
        ]
        fragments = []
        for skill in relevant:
            parts = [
                f"SKILL {skill.skill_id}@{skill.version}",
                skill.description,
                "Regras:\n" + "\n".join(f"- {rule}" for rule in skill.rules),
            ]
            if skill.examples_good:
                parts.append(
                    "Boas referências:\n"
                    + "\n".join(f"- {example}" for example in skill.examples_good)
                )
            if skill.examples_bad:
                parts.append(
                    "Evitar:\n"
                    + "\n".join(f"- {example}" for example in skill.examples_bad)
                )
            parts.append(skill.llm_hint_template)
            fragments.append("\n".join(parts))
        return "\n\n".join(fragments)
