import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import FactLedger, ResearchPlan, ResearchQuestion
from app.services.fact_conflicts import unresolved_fact_conflicts


@dataclass(frozen=True)
class ResearchCoverageEvaluation:
    questions: tuple[ResearchQuestion, ...] = field(repr=False)
    facts: tuple[FactLedger, ...] = field(repr=False)
    valid_fact_ids: tuple[uuid.UUID, ...]
    invalid_fact_ids: tuple[uuid.UUID, ...]
    covered_question_ids: frozenset[uuid.UUID]
    missing_questions: tuple[str, ...]
    unresolved_conflicts: tuple[str, ...]
    unresolved_conflict_fact_ids: dict[str, tuple[str, ...]]
    distinct_source_count: int
    minimum_distinct_sources: int
    minimum_facts_per_question: int
    coverage_complete: bool
    required_node_ids: tuple[str, ...] = ()

    @property
    def coverage_by_question(self) -> dict[str, float]:
        selected = set(self.valid_fact_ids)
        counts: dict[uuid.UUID, int] = defaultdict(int)
        for fact in self.facts:
            if fact.id in selected:
                counts[fact.research_question_id] += 1
        return {
            str(question.id): min(
                1.0,
                counts[question.id] / self.minimum_facts_per_question,
            )
            for question in self.questions
        }


    @property
    def core_questions(self) -> tuple[ResearchQuestion, ...]:
        explicit = tuple(
            question
            for question in self.questions
            if str(getattr(question, "importance", "core")) == "core"
        )
        return explicit or self.questions

    @property
    def missing_core_questions(self) -> tuple[str, ...]:
        return tuple(
            question.question
            for question in self.core_questions
            if question.id not in self.covered_question_ids
        )

    @property
    def missing_supporting_questions(self) -> tuple[str, ...]:
        return tuple(
            question.question
            for question in self.questions
            if str(getattr(question, "importance", "core")) == "supporting"
            and question.id not in self.covered_question_ids
        )

    @property
    def missing_optional_questions(self) -> tuple[str, ...]:
        return tuple(
            question.question
            for question in self.questions
            if str(getattr(question, "importance", "core")) == "optional"
            and question.id not in self.covered_question_ids
        )

    @property
    def core_coverage_complete(self) -> bool:
        return bool(self.core_questions) and not self.missing_core_questions

    @property
    def covered_node_ids(self) -> frozenset[str]:
        selected_questions = {
            question.id for question in self.questions if question.id in self.covered_question_ids
        }
        return frozenset(
            str(node_id)
            for question in self.questions
            if question.id in selected_questions
            for node_id in (getattr(question, "node_ids", None) or [])
            if str(node_id)
        )

    @property
    def missing_required_node_ids(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.required_node_ids) - set(self.covered_node_ids)))

    @property
    def evidence_ready(self) -> bool:
        return bool(self.valid_fact_ids) and not any(
            (
                self.missing_core_questions,
                self.invalid_fact_ids,
                self.unresolved_conflicts,
                self.missing_required_node_ids,
                self.distinct_source_count < self.minimum_distinct_sources,
            )
        )

    @property
    def source_diversity_score(self) -> float:
        if self.minimum_distinct_sources <= 0:
            return 1.0
        return min(1.0, self.distinct_source_count / self.minimum_distinct_sources)

    @property
    def selected_source_count_by_question(self) -> dict[str, int]:
        selected = set(self.valid_fact_ids)
        sources_by_question: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
        for fact in self.facts:
            if fact.id in selected:
                sources_by_question[fact.research_question_id].add(fact.source_id)
        return {
            str(question.id): len(sources_by_question[question.id])
            for question in self.questions
        }

    def permits_approval(self, model_decision: str) -> bool:
        return model_decision == "approved" and self.evidence_ready

    def persist(self, *, approved: bool, reviewer_run_id: uuid.UUID) -> None:
        selected = set(self.valid_fact_ids) if approved else set()
        for question in self.questions:
            question.coverage_status = (
                "covered"
                if question.id in self.covered_question_ids
                else "uncovered"
            )
        for fact in self.facts:
            fact.approved = fact.id in selected
            fact.approved_by_run_id = reviewer_run_id if fact.approved else None

    def next_cycle_instructions(self) -> list[str]:
        instructions = [
            f"Obter ao menos {self.minimum_facts_per_question} fatos verificáveis "
            f"para a pergunta central: {question}"
            for question in self.missing_core_questions
        ]
        instructions.extend(
            f"Buscar, se o orçamento permitir, evidência de apoio para: {question}"
            for question in self.missing_supporting_questions
        )
        if self.missing_required_node_ids:
            instructions.append(
                "Cobrir os nós editoriais obrigatórios ainda sem evidência: "
                + ", ".join(self.missing_required_node_ids)
            )
        if self.invalid_fact_ids:
            instructions.append(
                "Reavaliar os IDs recomendados: somente fatos do projeto, run e "
                "plano atuais são aceitos"
            )
        if self.distinct_source_count < self.minimum_distinct_sources:
            instructions.append(
                f"Obter pelo menos {self.minimum_distinct_sources} fontes distintas "
                "entre os fatos recomendados"
            )
        instructions.extend(
            f"Resolver o conflito factual: {conflict}"
            for conflict in self.unresolved_conflicts
        )
        return instructions


class ResearchCoverageService:
    def __init__(
        self,
        db: AsyncSession,
        project_id: uuid.UUID,
        pipeline_run_id: uuid.UUID,
    ):
        self.db = db
        self.project_id = project_id
        self.pipeline_run_id = pipeline_run_id

    async def evaluate(
        self,
        approved_fact_ids: Iterable[uuid.UUID] | None,
        *,
        minimum_distinct_sources: int,
        minimum_facts_per_question: int = 1,
        reported_conflicts: Iterable[str] = (),
        required_node_ids: Iterable[str] = (),
    ) -> ResearchCoverageEvaluation:
        plan = await self.db.scalar(
            select(ResearchPlan)
            .where(
                ResearchPlan.project_id == self.project_id,
                ResearchPlan.pipeline_run_id == self.pipeline_run_id,
            )
            .order_by(ResearchPlan.version.desc(), ResearchPlan.id.desc())
            .limit(1)
        )
        if plan is None:
            raise ValueError("Current research plan not found")
        questions = tuple(
            (
                await self.db.scalars(
                    select(ResearchQuestion)
                    .where(ResearchQuestion.plan_id == plan.id)
                    .order_by(
                        ResearchQuestion.priority,
                        ResearchQuestion.created_at,
                        ResearchQuestion.id,
                    )
                )
            ).all()
        )
        facts = tuple(
            (
                await self.db.scalars(
                    select(FactLedger).where(
                        FactLedger.project_id == self.project_id,
                        FactLedger.pipeline_run_id == self.pipeline_run_id,
                    )
                )
            ).all()
        )
        selected_ids = (
            {fact.id for fact in facts if fact.approved}
            if approved_fact_ids is None
            else {uuid.UUID(str(value)) for value in approved_fact_ids}
        )
        return self.evaluate_rows(
            questions,
            facts,
            selected_ids,
            project_id=self.project_id,
            pipeline_run_id=self.pipeline_run_id,
            minimum_distinct_sources=minimum_distinct_sources,
            minimum_facts_per_question=minimum_facts_per_question,
            reported_conflicts=reported_conflicts,
            required_node_ids=tuple(
                str(node.get("node_id"))
                for node in (getattr(plan, "hierarchy_json", None) or {}).get("nodes", [])
                if node.get("research_required", True)
                and node.get("applicability", "required") == "required"
                and str(node.get("node_id") or "")
            ),
        )

    @staticmethod
    def evaluate_rows(
        questions: Iterable[ResearchQuestion],
        facts: Iterable[FactLedger],
        approved_fact_ids: Iterable[uuid.UUID],
        *,
        project_id: uuid.UUID,
        pipeline_run_id: uuid.UUID,
        minimum_distinct_sources: int,
        minimum_facts_per_question: int = 1,
        reported_conflicts: Iterable[str] = (),
        required_node_ids: Iterable[str] = (),
    ) -> ResearchCoverageEvaluation:
        ordered_questions = tuple(questions)
        question_ids = {question.id for question in ordered_questions}
        run_facts = tuple(
            fact
            for fact in facts
            if fact.project_id == project_id
            and fact.pipeline_run_id == pipeline_run_id
        )
        scoped_facts = tuple(
            fact
            for fact in run_facts
            if fact.research_question_id in question_ids
        )
        requested_ids = {uuid.UUID(str(value)) for value in approved_fact_ids}
        known = {
            fact.id: fact
            for fact in scoped_facts
            if fact.superseded_by_id is None
        }
        question_order = {
            question.id: index for index, question in enumerate(ordered_questions)
        }
        valid_rows = sorted(
            (known[fact_id] for fact_id in requested_ids if fact_id in known),
            key=lambda fact: (
                question_order[fact.research_question_id],
                str(fact.id),
            ),
        )
        valid_fact_ids = tuple(fact.id for fact in valid_rows)
        invalid_fact_ids = tuple(sorted(requested_ids - known.keys(), key=str))

        fact_ids_by_question: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
        for fact in valid_rows:
            fact_ids_by_question[fact.research_question_id].append(fact.id)
        minimum_facts_per_question = max(1, int(minimum_facts_per_question))
        covered_question_ids = frozenset(
            question_id
            for question_id, fact_ids in fact_ids_by_question.items()
            if len(fact_ids) >= minimum_facts_per_question
        )
        missing_questions = tuple(
            question.question
            for question in ordered_questions
            if question.id not in covered_question_ids
        )
        distinct_source_count = len({fact.source_id for fact in valid_rows})

        # A conflict blocks automatically only when the gatekeeper selected at
        # least two active facts in the same persisted group. A group spanning
        # unselected candidates blocks only when the gatekeeper explicitly
        # validates that it remains unresolved.
        selected_conflicts = unresolved_fact_conflicts(
            scoped_facts,
            project_id=project_id,
            pipeline_run_id=pipeline_run_id,
            valid_fact_ids=valid_fact_ids,
        )
        candidate_conflicts = unresolved_fact_conflicts(
            scoped_facts,
            project_id=project_id,
            pipeline_run_id=pipeline_run_id,
            valid_fact_ids=known,
        )
        reported = {
            str(group).strip() for group in reported_conflicts if str(group).strip()
        }
        conflicts_by_group = {
            conflict.group: conflict.active_fact_ids
            for conflict in selected_conflicts
        }
        conflicts_by_group.update(
            {
                conflict.group: conflict.active_fact_ids
                for conflict in candidate_conflicts
                if conflict.group in reported
            }
        )
        unresolved_conflicts = tuple(sorted(conflicts_by_group))
        unresolved_conflict_fact_ids = {
            group: conflicts_by_group[group] for group in unresolved_conflicts
        }
        covered_nodes = {
            str(node_id)
            for question in ordered_questions
            if question.id in covered_question_ids
            for node_id in (getattr(question, "node_ids", None) or [])
            if str(node_id)
        }
        required_nodes = tuple(dict.fromkeys(str(value) for value in required_node_ids if str(value)))
        missing_required_nodes = tuple(sorted(set(required_nodes) - covered_nodes))
        coverage_complete = bool(ordered_questions) and not any(
            (
                missing_questions,
                missing_required_nodes,
                invalid_fact_ids,
                unresolved_conflicts,
                distinct_source_count < minimum_distinct_sources,
            )
        )
        return ResearchCoverageEvaluation(
            questions=ordered_questions,
            facts=run_facts,
            valid_fact_ids=valid_fact_ids,
            invalid_fact_ids=invalid_fact_ids,
            covered_question_ids=covered_question_ids,
            missing_questions=missing_questions,
            unresolved_conflicts=unresolved_conflicts,
            unresolved_conflict_fact_ids=unresolved_conflict_fact_ids,
            distinct_source_count=distinct_source_count,
            minimum_distinct_sources=minimum_distinct_sources,
            minimum_facts_per_question=minimum_facts_per_question,
            coverage_complete=coverage_complete,
            required_node_ids=required_nodes,
        )
