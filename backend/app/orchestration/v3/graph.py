"""State-transition rules for the executable Editorial Intelligence V3."""

from dataclasses import dataclass
from typing import Awaitable, Callable

from app.orchestration.v3.state import V3PipelineState, V3Stage

Node = Callable[[V3PipelineState], Awaitable[V3PipelineState]]
TransitionHook = Callable[[str, V3PipelineState], Awaitable[None]]


@dataclass
class V3PipelineNodes:
    content_contract: Node
    knowledge_architect: Node
    knowledge_gate: Node
    research_planner: Node
    source_discovery: Node
    source_reader: Node
    knowledge_synthesizer: Node
    knowledge_completeness_gate: Node
    writer: Node
    development_editor: Node
    fact_checker: Node
    language_editor: Node
    external_reference_gate: Node
    finalizer: Node
    quality_gate: Node
    source_coverage_gate: Node | None = None
    targeted_source_recovery: Node | None = None
    intelligence_planner: Node | None = None
    evidence_graph_builder: Node | None = None
    intelligence_gate: Node | None = None


class EditorialIntelligenceV3Graph:
    def __init__(self, nodes: V3PipelineNodes, after_transition: TransitionHook | None = None):
        self.nodes = nodes
        self.after_transition = after_transition

    async def run(self, state: V3PipelineState) -> V3PipelineState:
        while state.stage not in {V3Stage.completed, V3Stage.blocked}:
            completed_stage = state.stage.value
            node = getattr(self.nodes, completed_stage, None)
            if node is None:
                # Compatibility for V3.1–V3.4 test graphs and resumable manifests.
                if state.stage == V3Stage.source_coverage_gate:
                    state.source_coverage_report = {"status": "passed"}
                elif state.stage == V3Stage.targeted_source_recovery:
                    state.source_recovery_exhausted = True
                else:
                    raise RuntimeError(f"No node configured for V3 stage {completed_stage}")
            else:
                state = await node(state)
            state = self._transition(state)
            if self.after_transition is not None:
                await self.after_transition(completed_stage, state)
        return state

    def _transition(self, state: V3PipelineState) -> V3PipelineState:
        if state.stage == V3Stage.content_contract:
            if not state.contract:
                return self._block(state, "V3 content contract was not created")
            state.stage = V3Stage.knowledge_architect
        elif state.stage == V3Stage.knowledge_architect:
            if not state.contract:
                return self._block(state, "Knowledge architect produced no graph")
            state.stage = V3Stage.knowledge_gate
        elif state.stage == V3Stage.knowledge_gate:
            if (state.contract_validation or {}).get("status") != "passed":
                return self._block(
                    state,
                    "Knowledge contract did not pass deterministic validation",
                    code="V3_KNOWLEDGE_CONTRACT_BLOCKED",
                )
            state.stage = (
                V3Stage.intelligence_planner
                if self.nodes.intelligence_planner is not None
                else V3Stage.research_planner
            )
        elif state.stage == V3Stage.intelligence_planner:
            if not state.intelligence_state:
                return self._block(
                    state,
                    "Editorial intelligence planning produced no canonical state",
                    code="V3_INTELLIGENCE_PLANNING_MISSING",
                )
            if (state.intelligence_validation or {}).get("status") != "passed":
                return self._block(
                    state,
                    "Editorial intelligence planning did not pass deterministic validation",
                    code="V3_INTELLIGENCE_PLANNING_BLOCKED",
                )
            state.stage = V3Stage.research_planner
        elif state.stage == V3Stage.research_planner:
            if not state.research_plan:
                return self._block(state, "V3 research plan was not created")
            state.stage = V3Stage.source_discovery
        elif state.stage == V3Stage.source_discovery:
            if state.raw_source_documents:
                state.stage = V3Stage.source_reader
            elif self.nodes.targeted_source_recovery is not None:
                state.stage = V3Stage.targeted_source_recovery
            else:
                return self._block(
                    state,
                    "Nenhuma fonte utilizável foi encontrada para o assunto informado.",
                    code="V3_NO_SOURCE_RESULTS",
                )
        elif state.stage == V3Stage.source_reader:
            state.stage = (
                V3Stage.source_coverage_gate
                if self.nodes.source_coverage_gate is not None
                else V3Stage.knowledge_synthesizer
            )
        elif state.stage == V3Stage.source_coverage_gate:
            if (state.source_coverage_report or {}).get("status") == "passed":
                state.stage = V3Stage.knowledge_synthesizer
            elif not state.source_recovery_exhausted:
                state.stage = V3Stage.targeted_source_recovery
            else:
                report = state.source_coverage_report or {}
                return self._block(
                    state,
                    "A cobertura de fontes permaneceu incompleta após as tentativas de recuperação.",
                    code=str(
                        report.get("suggested_blocking_code")
                        or state.blocking_code
                        or "V3_RESEARCH_COVERAGE_INCOMPLETE"
                    ),
                )
        elif state.stage == V3Stage.targeted_source_recovery:
            intelligence_mode = (
                (state.research_metrics or {}).get("last_recovery_mode")
                == "intelligence"
                or bool(state.intelligence_recovery_tasks)
            )
            exhausted = (
                state.intelligence_recovery_exhausted
                if intelligence_mode
                else state.source_recovery_exhausted
            )
            metric_key = (
                "intelligence_recovery_new_candidate_count"
                if intelligence_mode
                else "source_recovery_new_candidate_count"
            )
            new_candidate_count = int(
                (state.research_metrics or {}).get(metric_key, 0) or 0
            )
            if not exhausted and new_candidate_count == 0:
                state.stage = V3Stage.targeted_source_recovery
            elif state.raw_source_documents and not exhausted:
                if intelligence_mode:
                    state.intelligence_recovery_tasks = []
                state.research_metrics = {
                    **(state.research_metrics or {}),
                    "last_recovery_mode": None,
                }
                state.stage = V3Stage.source_reader
            elif exhausted:
                report = state.source_coverage_report or {}
                return self._block(
                    state,
                    state.blocking_reason
                    or "A pesquisa esgotou os limites sem encontrar fontes suficientes.",
                    code=str(
                        report.get("suggested_blocking_code")
                        or state.blocking_code
                        or (
                            "V3_INTELLIGENCE_RECOVERY_EXHAUSTED"
                            if intelligence_mode
                            else "V3_SEARCH_NO_CANDIDATES"
                        )
                    ),
                )
            else:
                state.stage = V3Stage.targeted_source_recovery
        elif state.stage == V3Stage.knowledge_synthesizer:
            if not state.section_dossiers:
                return self._block(
                    state,
                    state.blocking_reason or "Knowledge synthesis produced no dossiers",
                    code=state.blocking_code or "V3_KNOWLEDGE_SYNTHESIS_EMPTY",
                )
            state.stage = (
                V3Stage.evidence_graph_builder
                if self.nodes.evidence_graph_builder is not None
                else V3Stage.knowledge_completeness_gate
            )
        elif state.stage == V3Stage.evidence_graph_builder:
            if not state.intelligence_state:
                return self._block(
                    state,
                    "Evidence graph builder produced no canonical intelligence state",
                    code="V3_EVIDENCE_GRAPH_MISSING",
                )
            state.stage = (
                V3Stage.intelligence_gate
                if self.nodes.intelligence_gate is not None
                else V3Stage.knowledge_completeness_gate
            )
        elif state.stage == V3Stage.intelligence_gate:
            if (state.intelligence_validation or {}).get("status") != "passed":
                if (
                    self.nodes.targeted_source_recovery is not None
                    and state.intelligence_recovery_tasks
                    and not state.intelligence_recovery_exhausted
                ):
                    state.stage = V3Stage.targeted_source_recovery
                else:
                    return self._block(
                        state,
                        state.blocking_reason
                        or "Editorial intelligence state is not ready for writing",
                        code=state.blocking_code or "V3_INTELLIGENCE_GATE_BLOCKED",
                    )
            else:
                state.stage = V3Stage.knowledge_completeness_gate
        elif state.stage == V3Stage.knowledge_completeness_gate:
            if (state.completeness_report or {}).get("status") != "passed":
                return self._block(
                    state,
                    "Knowledge is incomplete; writer execution is forbidden",
                    code="V3_KNOWLEDGE_INCOMPLETE",
                )
            state.stage = V3Stage.writer
        elif state.stage == V3Stage.writer:
            if not state.draft:
                return self._block(state, "V3 writer produced no draft")
            state.stage = V3Stage.development_editor
        elif state.stage == V3Stage.development_editor:
            if (state.development_review or {}).get("status") != "passed":
                return self._block(state, "Development review failed")
            state.stage = V3Stage.fact_checker
        elif state.stage == V3Stage.fact_checker:
            if (state.fact_check or {}).get("status") != "passed":
                return self._block(state, "Fact checking failed")
            state.stage = V3Stage.language_editor
        elif state.stage == V3Stage.language_editor:
            if (state.language_review or {}).get("status") != "passed":
                return self._block(state, "Language review failed")
            state.stage = V3Stage.external_reference_gate
        elif state.stage == V3Stage.external_reference_gate:
            if (state.external_reference_report or {}).get("status") != "passed":
                return self._block(state, "External-reference validation failed")
            state.stage = V3Stage.finalizer
        elif state.stage == V3Stage.finalizer:
            if not state.final_package:
                return self._block(state, "V3 final package was not created")
            state.stage = V3Stage.quality_gate
        elif state.stage == V3Stage.quality_gate:
            if (state.quality_evaluation or {}).get("status") != "passed":
                return self._block(
                    state,
                    state.blocking_reason or "V3 procedural quality gate failed",
                    code=state.blocking_code or "V3_ARTICLE_QUALITY_BLOCKED",
                )
            state.stage = V3Stage.completed
        return state

    @staticmethod
    def _block(
        state: V3PipelineState,
        reason: str,
        *,
        code: str = "V3_PIPELINE_BLOCKED",
    ) -> V3PipelineState:
        state.stage = V3Stage.blocked
        state.blocking_reason = reason
        state.blocking_code = code
        return state
