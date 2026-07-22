import hashlib
import json
import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import (
    Article,
    ArticleVersion,
    Project,
    Skill,
    SkillKind,
    SkillLifecycleEvent,
    SkillValidation,
    SkillVersion,
)


LIFECYCLE_STATUSES = frozenset(
    {
        "candidate",
        "corroborated",
        "human_approved",
        "stable",
        "active",
        "disabled",
        "rejected",
    }
)


class SkillLearningInputError(ValueError):
    pass


class SkillLifecycleConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class PipelineOutcomeSignals:
    editorial_decision: str
    editorial_rework_count: int
    rubric_score: float
    factual_regression: bool
    unsupported_claim_count: int
    major_fidelity_findings: int
    critical_fidelity_findings: int

    @property
    def corroborates(self) -> bool:
        return bool(
            self.editorial_decision == "approved"
            and self.unsupported_claim_count == 0
            and not self.factual_regression
        )

    @classmethod
    def from_pipeline_state(cls, state) -> "PipelineOutcomeSignals":
        review = state.editorial_review or {}
        final_package = state.final_package or {}
        findings = [
            *review.get("fidelity_findings", []),
            *review.get("language_findings", []),
        ]
        severity_counts = {
            severity: sum(
                str(finding.get("severity", "")).lower() == severity
                for finding in findings
            )
            for severity in ("minor", "major", "critical")
        }
        rework_count = max(int(state.editor_cycle or 0), 0)
        penalty = (
            severity_counts["minor"] * 0.03
            + severity_counts["major"] * 0.15
            + severity_counts["critical"] * 0.35
            + rework_count * 0.08
        )
        unsupported = max(int(final_package.get("unsupported_claim_count", 1)), 0)
        factual_regression = bool(
            unsupported
            or severity_counts["major"]
            or severity_counts["critical"]
        )
        return cls(
            editorial_decision=str(review.get("decision", "unknown")),
            editorial_rework_count=rework_count,
            rubric_score=round(max(0.0, min(1.0, 1.0 - penalty)), 4),
            factual_regression=factual_regression,
            unsupported_claim_count=unsupported,
            major_fidelity_findings=severity_counts["major"],
            critical_fidelity_findings=severity_counts["critical"],
        )

    def as_dict(self) -> dict:
        return {
            "editorial_decision": self.editorial_decision,
            "editorial_rework_count": self.editorial_rework_count,
            "rubric_score": self.rubric_score,
            "factual_regression": self.factual_regression,
            "unsupported_claim_count": self.unsupported_claim_count,
            "major_fidelity_findings": self.major_fidelity_findings,
            "critical_fidelity_findings": self.critical_fidelity_findings,
        }


class SkillLearningService:
    learned_roles = ("researcher", "research_gatekeeper")

    def __init__(
        self,
        db: AsyncSession,
        *,
        stability_threshold: int | None = None,
        minimum_independent_articles: int | None = None,
    ):
        self.db = db
        self.stability_threshold = (
            settings.learned_skill_stability_threshold
            if stability_threshold is None
            else stability_threshold
        )
        self.minimum_independent_articles = (
            settings.learned_skill_min_independent_articles
            if minimum_independent_articles is None
            else minimum_independent_articles
        )

    async def record_candidate(
        self,
        *,
        project: Project,
        pipeline_run_id: uuid.UUID,
        article: Article,
        candidate: dict,
        outcome: PipelineOutcomeSignals,
    ) -> Skill | None:
        self._validate_candidate(project, article, candidate)
        rules = self._canonical_rules(candidate.get("rules", []))
        fingerprint = self.fingerprint(
            niche=project.niche or "general",
            applies_to_agents=self.learned_roles,
            rules=rules,
        )
        bind = getattr(self.db, "bind", None)
        if bind is not None and bind.dialect.name == "postgresql":
            lock_key = int(fingerprint[:16], 16)
            if lock_key >= 2**63:
                lock_key -= 2**64
            await self.db.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": lock_key},
            )
        article_version = await self.db.scalar(
            select(ArticleVersion).where(
                ArticleVersion.article_id == article.id,
                ArticleVersion.version == article.current_version,
                ArticleVersion.pipeline_run_id == pipeline_run_id,
            )
        )
        if article_version is None:
            raise SkillLearningInputError(
                "Corroboration requires an article version from the same pipeline run"
            )
        skill = await self.db.scalar(
            select(Skill)
            .where(
                Skill.kind == SkillKind.learned,
                Skill.project_id == project.id,
                Skill.fingerprint == fingerprint,
            )
            .with_for_update()
        )
        version = None
        if skill is None:
            skill, version = await self._adopt_legacy_candidate(
                project, fingerprint, rules
            )
        if skill is None:
            skill = Skill(
                skill_id=(
                    f"learned.{self._slug(project.niche or 'general')}."
                    f"{fingerprint[:16]}"
                ),
                kind=SkillKind.learned,
                project_id=project.id,
                applies_to_agents=list(self.learned_roles),
                niche=project.niche or "general",
                fingerprint=fingerprint,
                lifecycle_status="candidate",
                auto_inject=False,
                enabled=False,
                stable=False,
                current_version="0.1.0",
            )
            self.db.add(skill)
            await self.db.flush()
            version = SkillVersion(
                skill_id=skill.id,
                version="0.1.0",
                description=str(candidate["title"]).strip(),
                definition={
                    "rules": rules,
                    "auto_inject": False,
                    "scope": "project_niche",
                    "status": "candidate",
                    "stability_threshold": self.stability_threshold,
                    "minimum_independent_articles": (
                        self.minimum_independent_articles
                    ),
                },
                origin_article_id=article.id,
                confidence_score=float(candidate["confidence_score"]),
                validation_count=0,
                reviewed_by_human=False,
            )
            self.db.add(version)
            await self.db.flush()
            self._add_event(
                skill,
                version,
                action="candidate_created",
                from_status="candidate",
                to_status="candidate",
                actor="skill-curator",
                reason="Equivalent process rule candidate recorded",
                pipeline_run_id=pipeline_run_id,
                article_id=article.id,
                details={
                    "fingerprint": fingerprint,
                    "causal_attribution": "not_established",
                },
            )
        if skill.lifecycle_status == "rejected":
            return skill
        if version is None:
            version = await self._current_version(skill)
        if version is None:
            raise SkillLearningInputError("Learned skill current version is missing")

        existing = await self.db.scalar(
            select(SkillValidation.id).where(
                SkillValidation.skill_version_id == version.id,
                SkillValidation.pipeline_run_id == pipeline_run_id,
            )
        )
        if existing is not None:
            return skill

        prior = list(
            (
                await self.db.scalars(
                    select(SkillValidation)
                    .where(SkillValidation.skill_version_id == version.id)
                    .order_by(SkillValidation.created_at, SkillValidation.id)
                )
            ).all()
        )
        prior_rework = [row.editorial_rework_count for row in prior]
        prior_rubrics = [row.rubric_score for row in prior]
        outcome_json = {
            **outcome.as_dict(),
            "rework_reduced": (
                outcome.editorial_rework_count < (sum(prior_rework) / len(prior_rework))
                if prior_rework
                else None
            ),
            "rubric_improved": (
                outcome.rubric_score > (sum(prior_rubrics) / len(prior_rubrics))
                if prior_rubrics
                else None
            ),
            "correlation_only": True,
            "causal_attribution": "not_established",
            "curator_output_is_not_sufficient_evidence": True,
        }
        validation = SkillValidation(
            skill_version_id=version.id,
            pipeline_run_id=pipeline_run_id,
            article_id=article.id,
            article_version_id=article_version.id,
            evidence_source="pipeline_outcome",
            editorial_rework_count=outcome.editorial_rework_count,
            rubric_score=outcome.rubric_score,
            factual_regression=outcome.factual_regression,
            corroborating=outcome.corroborates,
            outcome_json=outcome_json,
        )
        self.db.add(validation)
        await self.db.flush()
        positive_count = sum(row.corroborating for row in prior) + int(
            outcome.corroborates
        )
        positive_article_ids = {
            row.article_id for row in prior if row.corroborating
        }
        if outcome.corroborates:
            positive_article_ids.add(article.id)
        independent_article_count = len(positive_article_ids)
        version.validation_count = positive_count
        self._add_event(
            skill,
            version,
            action="validation_recorded",
            from_status=skill.lifecycle_status,
            to_status=skill.lifecycle_status,
            actor="pipeline-outcome",
            reason="Independent pipeline outcome linked to candidate",
            pipeline_run_id=pipeline_run_id,
            article_id=article.id,
            details={
                "validation_id": str(validation.id),
                "corroborating": outcome.corroborates,
                "independent_article_count": independent_article_count,
                **outcome_json,
            },
        )
        if (
            positive_count >= self.stability_threshold
            and independent_article_count
            >= self.minimum_independent_articles
            and skill.lifecycle_status == "candidate"
        ):
            previous = skill.lifecycle_status
            skill.lifecycle_status = "corroborated"
            self._add_event(
                skill,
                version,
                action="corroborated",
                from_status=previous,
                to_status="corroborated",
                actor="lifecycle-policy",
                reason="Independent validation threshold reached",
                pipeline_run_id=pipeline_run_id,
                article_id=article.id,
                details={
                    "validation_count": positive_count,
                    "threshold": self.stability_threshold,
                    "independent_article_count": independent_article_count,
                    "minimum_independent_articles": (
                        self.minimum_independent_articles
                    ),
                    "human_approval_required": True,
                },
            )
        return skill

    async def apply_action(
        self,
        skill_id: str,
        action: str,
        *,
        reason: str,
        actor: str = "admin-api",
    ) -> tuple[Skill, SkillVersion]:
        skill = await self.db.scalar(
            select(Skill)
            .where(Skill.skill_id == skill_id, Skill.kind == SkillKind.learned)
            .with_for_update()
        )
        if skill is None:
            raise SkillLearningInputError("Learned skill not found")
        version = await self._current_version(skill)
        if version is None:
            raise SkillLearningInputError("Learned skill current version is missing")
        validations = list(
            (
                await self.db.scalars(
                    select(SkillValidation).where(
                        SkillValidation.skill_version_id == version.id
                    )
                )
            ).all()
        )
        positive_count = sum(row.corroborating for row in validations)
        independent_article_count = len(
            {row.article_id for row in validations if row.corroborating}
        )
        factual_regressions = sum(row.factual_regression for row in validations)
        previous = skill.lifecycle_status

        if action == "approve":
            self._require_status(skill, {"corroborated"}, action)
            if positive_count < settings.learned_skill_stability_threshold:
                raise SkillLifecycleConflict("Independent validation threshold not met")
            if (
                independent_article_count
                < settings.learned_skill_min_independent_articles
            ):
                raise SkillLifecycleConflict("Independent article threshold not met")
            if factual_regressions:
                raise SkillLifecycleConflict("Factual regression blocks human approval")
            version.reviewed_by_human = True
            skill.lifecycle_status = "human_approved"
        elif action == "promote":
            self._require_status(skill, {"human_approved"}, action)
            if not version.reviewed_by_human:
                raise SkillLifecycleConflict("Human review is required before promotion")
            skill.stable = True
            skill.promoted_at = datetime.now(timezone.utc)
            skill.lifecycle_status = "stable"
        elif action == "activate":
            self._require_status(skill, {"stable", "disabled"}, action)
            if not skill.stable or not version.reviewed_by_human:
                raise SkillLifecycleConflict("Only a stable human-approved skill may activate")
            skill.enabled = True
            skill.auto_inject = True
            skill.lifecycle_status = "active"
        elif action in {"disable", "rollback"}:
            self._require_status(skill, {"active"}, action)
            skill.enabled = False
            skill.auto_inject = False
            skill.lifecycle_status = "disabled"
        elif action == "reject":
            self._require_status(
                skill,
                {
                    "candidate",
                    "corroborated",
                    "human_approved",
                    "stable",
                    "active",
                    "disabled",
                },
                action,
            )
            version.reviewed_by_human = False
            skill.enabled = False
            skill.auto_inject = False
            skill.stable = False
            skill.lifecycle_status = "rejected"
        else:
            raise SkillLearningInputError("Unknown learned skill lifecycle action")

        self._add_event(
            skill,
            version,
            action=action,
            from_status=previous,
            to_status=skill.lifecycle_status,
            actor=actor,
            reason=reason,
            details={
                "validation_count": positive_count,
                "independent_article_count": independent_article_count,
                "factual_regressions": factual_regressions,
                "version_checksum": self._version_checksum(version),
                "causal_attribution": "not_established",
            },
        )
        return skill, version

    async def _adopt_legacy_candidate(self, project, fingerprint, rules):
        rows = (
            await self.db.execute(
                select(Skill, SkillVersion)
                .join(
                    SkillVersion,
                    (SkillVersion.skill_id == Skill.id)
                    & (SkillVersion.version == Skill.current_version),
                )
                .where(
                    Skill.kind == SkillKind.learned,
                    Skill.project_id == project.id,
                    Skill.fingerprint.is_(None),
                )
            )
        ).all()
        for skill, version in rows:
            legacy_rules = self._canonical_rules(version.definition.get("rules", []))
            legacy_fingerprint = self.fingerprint(
                niche=skill.niche or "general",
                applies_to_agents=skill.applies_to_agents or self.learned_roles,
                rules=legacy_rules,
            )
            if legacy_fingerprint == fingerprint and legacy_rules == rules:
                skill.fingerprint = fingerprint
                return skill, version
        return None, None

    async def _current_version(self, skill):
        return await self.db.scalar(
            select(SkillVersion).where(
                SkillVersion.skill_id == skill.id,
                SkillVersion.version == skill.current_version,
            )
        )

    @staticmethod
    def _validate_candidate(project, article, candidate) -> None:
        expected_niche = project.niche or "general"
        if article.project_id != project.id:
            raise SkillLearningInputError("Candidate article belongs to another project")
        if str(candidate.get("evidence_article_id")) != str(article.id):
            raise SkillLearningInputError("Candidate evidence article does not match")
        if str(candidate.get("niche", "")).strip() != expected_niche:
            raise SkillLearningInputError("Candidate niche does not match the project")
        if not str(candidate.get("title", "")).strip():
            raise SkillLearningInputError("Candidate title is required")
        if candidate.get("auto_inject") is not False:
            raise SkillLearningInputError("Curator candidates cannot authorize injection")

    @classmethod
    def fingerprint(cls, *, niche: str, applies_to_agents, rules) -> str:
        canonical = {
            "niche": cls._normalize(niche),
            "applies_to_agents": sorted(set(applies_to_agents)),
            "rules": sorted(set(cls._normalize(rule) for rule in rules)),
        }
        raw = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @classmethod
    def _canonical_rules(cls, rules) -> list[str]:
        if not isinstance(rules, list):
            raise SkillLearningInputError("Candidate rules must be a list")
        canonical = {}
        for value in rules:
            if not isinstance(value, str) or not value.strip():
                continue
            clean = re.sub(r"\s+", " ", value.replace("\x00", "")).strip()
            canonical.setdefault(cls._normalize(clean), clean)
        if not canonical:
            raise SkillLearningInputError("Candidate requires at least one process rule")
        return [canonical[key] for key in sorted(canonical)]

    @staticmethod
    def _normalize(value: str) -> str:
        return re.sub(
            r"\s+", " ", unicodedata.normalize("NFKC", value)
        ).strip().casefold()

    @staticmethod
    def _slug(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "general"

    @staticmethod
    def _require_status(skill, allowed: set[str], action: str) -> None:
        if skill.lifecycle_status not in allowed:
            raise SkillLifecycleConflict(
                f"Action {action} is invalid from {skill.lifecycle_status}"
            )

    def _add_event(
        self,
        skill,
        version,
        *,
        action,
        from_status,
        to_status,
        actor,
        reason,
        details,
        pipeline_run_id=None,
        article_id=None,
    ) -> None:
        if from_status not in LIFECYCLE_STATUSES or to_status not in LIFECYCLE_STATUSES:
            raise SkillLearningInputError("Invalid lifecycle status")
        self.db.add(
            SkillLifecycleEvent(
                skill_id=skill.id,
                skill_version_id=version.id if version else None,
                pipeline_run_id=pipeline_run_id,
                article_id=article_id,
                from_status=from_status,
                to_status=to_status,
                action=action,
                actor=actor,
                reason=reason,
                details=details,
            )
        )

    @staticmethod
    def _version_checksum(version) -> str:
        raw = json.dumps(
            {
                "description": version.description,
                "definition": version.definition,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()
