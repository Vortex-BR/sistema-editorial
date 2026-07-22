from dataclasses import dataclass
from typing import Awaitable, Callable
from app.orchestration.state import PipelineState, Stage

Node = Callable[[PipelineState], Awaitable[PipelineState]]
TransitionHook = Callable[[str, PipelineState], Awaitable[None]]


@dataclass
class PipelineNodes:
    planner: Node
    researcher: Node
    research_gatekeeper: Node
    writer: Node
    editor: Node
    finalizer: Node
    quality_gate: Node
    skill_curator: Node


class EvidenceFirstGraph:
    """Explicit gated state machine. Writing has no edge from planning/research."""

    def __init__(
        self,
        nodes: PipelineNodes,
        after_transition: TransitionHook | None = None,
        *,
        max_research_cycles: int = 2,
        max_editor_cycles: int = 1,
    ):
        self.nodes = nodes
        self.after_transition = after_transition
        self.max_research_cycles = max_research_cycles
        self.max_editor_cycles = max_editor_cycles

    async def run(self, state: PipelineState) -> PipelineState:
        while state.stage not in {Stage.completed, Stage.blocked, Stage.needs_review}:
            completed_stage = state.stage.value
            state = await getattr(self.nodes, state.stage.value)(state)
            state = self._transition(state)
            if self.after_transition:
                await self.after_transition(completed_stage, state)
        return state

    def _transition(self, state: PipelineState) -> PipelineState:
        if state.stage == Stage.planner:
            if not state.plan:
                return self._block(state, "Planner produced no research plan")
            state.stage = Stage.researcher
        elif state.stage == Stage.researcher:
            state.research_cycle += 1
            state.stage = Stage.research_gatekeeper
        elif state.stage == Stage.research_gatekeeper:
            decision = (state.research_audit or {}).get("decision")
            if decision == "approved":
                if not (
                    (state.research_audit or {}).get("coverage_complete") is True
                    or (state.research_audit or {}).get("evidence_ready") is True
                ):
                    return self._block(
                        state,
                        "Gatekeeper approved without validated evidence",
                    )
                approved_ids = set(
                    (state.research_audit or {}).get("approved_fact_ids", [])
                )
                if not approved_ids:
                    return self._block(
                        state, "Gatekeeper approved without persisted fact IDs"
                    )
                state.facts = [
                    fact
                    for fact in state.facts
                    if str(fact.get("id")) in {str(x) for x in approved_ids}
                ]
                state.stage = Stage.writer
            elif state.research_cycle < self.max_research_cycles:
                state.stage = Stage.researcher
            else:
                return self._block(
                    state,
                    "No usable evidence was extracted from the search results",
                    code="NO_USABLE_RESEARCH_RESULTS",
                )
        elif state.stage == Stage.writer:
            if not state.draft or state.draft.get("unsupported_claims"):
                return self._block(state, "Writer emitted an unsupported claim")
            if not self._all_factual_sentences_have_evidence(state.draft):
                return self._block(
                    state, "At least one factual sentence has no approved evidence"
                )
            state.stage = Stage.editor
        elif state.stage == Stage.editor:
            decision = (state.editorial_review or {}).get("decision")
            if decision == "approved":
                state.stage = Stage.finalizer
            elif (
                decision == "rewrite"
                and state.editor_cycle < self.max_editor_cycles
            ):
                state.editor_cycle += 1
                state.rewrite_block_ids = state.editorial_review.get(
                    "rewrite_block_ids", []
                )
                state.stage = Stage.writer
            else:
                return self._block(
                    state, "Editorial fidelity was rejected or exceeded rewrite limit"
                )
        elif state.stage == Stage.finalizer:
            if (
                not state.final_package
                or state.final_package.get("unsupported_claim_count", 1) != 0
            ):
                return self._block(
                    state, "Final package failed the zero-unsupported-claim invariant"
                )
            state.stage = Stage.quality_gate
        elif state.stage == Stage.quality_gate:
            quality_status = (state.quality_evaluation or {}).get("status")
            if quality_status == "passed":
                state.stage = Stage.skill_curator
            else:
                return self._block(
                    state,
                    "O artigo não atingiu o padrão editorial mínimo.",
                    code="ARTICLE_QUALITY_BLOCKED",
                )
        elif state.stage == Stage.skill_curator:
            state.stage = Stage.completed
        return state

    @staticmethod
    def _all_factual_sentences_have_evidence(draft: dict) -> bool:
        return all(
            (not sentence.get("is_factual", True)) or bool(sentence.get("evidence"))
            for block in draft.get("blocks", [])
            for sentence in block.get("sentences", [])
        )

    @staticmethod
    def _block(
        state: PipelineState,
        reason: str,
        *,
        code: str = "PIPELINE_QUALITY_BLOCKED",
    ) -> PipelineState:
        state.stage = Stage.blocked
        state.blocking_reason = reason
        state.blocking_code = code
        return state
