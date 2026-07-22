import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.orchestration.v3.executor import EditorialV3Executor, V3PipelineBlocked
from app.orchestration.v3.graph import EditorialIntelligenceV3Graph, V3PipelineNodes
from app.orchestration.v3.state import V3PipelineState, V3Stage
from app.schemas.editorial_v3_runtime import V3WriterOutput, V3WriterSectionOutput
from app.services.pipeline_control import CheckpointService


async def noop(state):
    return state


def nodes(**overrides):
    values = {
        "content_contract": noop,
        "knowledge_architect": noop,
        "knowledge_gate": noop,
        "research_planner": noop,
        "source_discovery": noop,
        "source_reader": noop,
        "knowledge_synthesizer": noop,
        "knowledge_completeness_gate": noop,
        "writer": noop,
        "development_editor": noop,
        "fact_checker": noop,
        "language_editor": noop,
        "external_reference_gate": noop,
        "finalizer": noop,
        "quality_gate": noop,
    }
    values.update(overrides)
    return V3PipelineNodes(**values)


@pytest.mark.asyncio
async def test_graph_blocks_a_node_that_mutates_the_stage_directly():
    async def illegal(state):
        state.stage = V3Stage.writer
        return state

    graph = EditorialIntelligenceV3Graph(nodes(content_contract=illegal))
    state = V3PipelineState(project_id=uuid.uuid4())

    result = await graph.run(state)

    assert result.stage == V3Stage.blocked
    assert result.blocking_code == "V3_GRAPH_STAGE_MUTATION"


@pytest.mark.asyncio
async def test_graph_transition_limit_stops_recovery_self_loop():
    graph = EditorialIntelligenceV3Graph(
        nodes(targeted_source_recovery=noop), max_transitions=2
    )
    state = V3PipelineState(
        project_id=uuid.uuid4(),
        stage=V3Stage.targeted_source_recovery,
        research_metrics={"source_recovery_new_candidate_count": 0},
    )

    result = await graph.run(state)

    assert result.stage == V3Stage.blocked
    assert result.blocking_code == "V3_GRAPH_TRANSITION_LIMIT_EXCEEDED"


@pytest.mark.asyncio
async def test_graph_blocks_invalid_node_state_instead_of_crashing():
    async def invalid(_state):
        return {"stage": "writer"}

    graph = EditorialIntelligenceV3Graph(nodes(content_contract=invalid))
    state = V3PipelineState(project_id=uuid.uuid4())

    result = await graph.run(state)

    assert result.stage == V3Stage.blocked
    assert result.blocking_code == "V3_GRAPH_INVALID_STATE"


@pytest.mark.asyncio
async def test_graph_transition_limit_survives_resume():
    graph = EditorialIntelligenceV3Graph(
        nodes(targeted_source_recovery=noop), max_transitions=2
    )
    state = V3PipelineState(
        project_id=uuid.uuid4(),
        stage=V3Stage.targeted_source_recovery,
        graph_transition_count=1,
        research_metrics={"source_recovery_new_candidate_count": 0},
    )

    first = await graph.run(state)
    resumed = EditorialIntelligenceV3Graph(
        nodes(targeted_source_recovery=noop), max_transitions=2
    )
    result = await resumed.run(first)

    assert result.stage == V3Stage.blocked
    assert result.blocking_code == "V3_GRAPH_TRANSITION_LIMIT_EXCEEDED"
    assert result.graph_transition_count == 2


def test_checkpoint_progress_suffix_creates_distinct_idempotency_keys():
    state = {"research_cycle": 0, "editor_cycle": 0}
    first = CheckpointService.idempotency_key(
        "writer",
        1,
        "1.0",
        state,
        idempotency_suffix="progress:foundation",
    )
    second = CheckpointService.idempotency_key(
        "writer",
        1,
        "1.0",
        state,
        idempotency_suffix="progress:execution",
    )

    assert first != second
    assert first.endswith("progress:foundation")


def _sentence(text):
    return {
        "sentence_id": str(uuid.uuid4()),
        "text": text,
        "is_factual": False,
        "evidence": [],
        "question_ids": [],
        "answer_status": None,
    }


def _block(section_id, position, block_type, text):
    return {
        "block_id": str(uuid.uuid4()),
        "type": block_type,
        "position": position,
        "section_id": section_id,
        "method_id": None,
        "sentences": [_sentence(text)],
        "table_headers": [],
        "table_rows": [],
        "callout_kind": None,
        "callout_title": None,
    }


def _unit(section_id, *, first=False, block_count=3):
    title = "Título editorial completo para teste"
    blocks = []
    for index in range(block_count):
        if first and index == 0:
            kind = "h1"
            text = title
        elif index == 0:
            kind = "h2"
            text = f"Seção {section_id}"
        else:
            kind = "paragraph"
            paragraph_count = max(1, block_count - 1)
            target_words = 330 // paragraph_count
            vocabulary = [
                "desenvolvimento",
                "editorial",
                section_id,
                "explica",
                "contexto",
                "critério",
                "decisão",
                "continuidade",
            ]
            words = (vocabulary * ((target_words // len(vocabulary)) + 1))[
                :target_words
            ]
            text = " ".join(words) + "."
        blocks.append(_block(section_id, index, kind, text))
    return V3WriterSectionOutput(
        section_id=section_id,
        title=title if first else None,
        blocks=blocks,
        covered_method_ids=[],
        scope_confirmation=f"A seção {section_id} foi concluída dentro do escopo.",
    )


def test_incremental_writer_assembly_is_ordered_valid_and_deterministic():
    run_id = uuid.uuid4()
    executor = object.__new__(EditorialV3Executor)
    executor.pipeline_run = SimpleNamespace(id=run_id)
    section_ids = ["foundation", "execution", "verification"]
    units = {
        "foundation": _unit("foundation", first=True, block_count=4),
        "execution": _unit("execution", block_count=3),
        "verification": _unit("verification", block_count=3),
    }
    state = V3PipelineState(
        project_id=uuid.uuid4(),
        pipeline_run_id=run_id,
        writer_sections={
            key: value.model_dump(mode="json") for key, value in units.items()
        },
        writer_completed_section_ids=section_ids,
    )

    first = executor._assemble_writer_section_payloads(state, section_ids)
    second = executor._assemble_writer_section_payloads(state, section_ids)
    draft = V3WriterOutput.model_validate(first)

    assert first == second
    assert draft.covered_section_ids == section_ids
    assert [block.position for block in draft.blocks] == list(range(10))
    assert len({block.block_id for block in draft.blocks}) == 10
    assert sum(block.type == "h1" for block in draft.blocks) == 1


def test_resume_invariants_reject_foreign_checkpoint_identity():
    state = V3PipelineState(
        project_id=uuid.uuid4(),
        pipeline_run_id=uuid.uuid4(),
    )

    errors = state.resume_invariant_errors(
        project_id=uuid.uuid4(), pipeline_run_id=uuid.uuid4()
    )

    assert "checkpoint project_id does not match the current project" in errors
    assert "checkpoint pipeline_run_id does not match the current run" in errors


def test_writer_progress_requires_payload_for_every_completed_unit():
    with pytest.raises(ValueError, match="missing persisted unit payloads"):
        V3PipelineState(
            project_id=uuid.uuid4(),
            writer_completed_section_ids=["foundation"],
        )


def test_writer_progress_rejects_orphaned_unit_payload():
    with pytest.raises(ValueError, match="not marked as completed"):
        V3PipelineState(
            project_id=uuid.uuid4(),
            writer_sections={
                "foundation": _unit("foundation", first=True, block_count=4).model_dump(
                    mode="json"
                )
            },
        )


@pytest.mark.asyncio
async def test_incremental_writer_generates_and_checkpoints_one_section_at_a_time():
    run_id = uuid.uuid4()
    executor = object.__new__(EditorialV3Executor)
    executor.pipeline_run = SimpleNamespace(id=run_id)
    executor.project = SimpleNamespace(id=uuid.uuid4())
    executor.runtime = SimpleNamespace(event=AsyncMock())
    executor.execution_manifest = {
        "feature_flags": {"v3_writer_section_repair_attempts": 1}
    }
    executor._stage_context = None
    executor.context_budget = SimpleNamespace(
        compact_writer_input=lambda payload, maximum_characters: (
            payload,
            SimpleNamespace(as_payload=lambda: {"maximum": maximum_characters}),
        )
    )
    executor._progress_checkpoint = AsyncMock()
    executor._validate_writer_section_evidence = lambda unit, state: None

    async def agent_call(**kwargs):
        section_id = kwargs["input_json"]["current_section"]["section_id"]
        index = kwargs["input_json"]["current_section_index"]
        return _unit(
            section_id,
            first=index == 0,
            block_count=4 if index == 0 else 3,
        ).model_dump(mode="json")

    executor._agent_call = AsyncMock(side_effect=agent_call)
    section_ids = ["foundation", "execution", "verification"]
    writer_input = {
        "editorial_sequence": [
            {
                "section_id": section_id,
                "maximum_depth_weight": 1.0,
                "depends_on": section_ids[:index],
            }
            for index, section_id in enumerate(section_ids)
        ],
        "section_dossiers": [],
        "claim_catalog": [],
        "editorial_intelligence": {},
        "target_word_range": [900, 1200],
    }
    state = V3PipelineState(project_id=executor.project.id, pipeline_run_id=run_id)

    draft = await executor._write_incremental_sections(
        state,
        writer_input=writer_input,
        common_prompt="Prompt global. ",
        mode_prompt="Prompt de modo.",
        target_word_range=(900, 1200),
    )

    assert isinstance(draft, V3WriterOutput)
    assert executor._agent_call.await_count == 3
    assert executor._progress_checkpoint.await_count == 3
    assert state.writer_completed_section_ids == section_ids
    assert state.writer_progress["status"] == "assembled"
    assert [
        call.kwargs["unit_id"] for call in executor._progress_checkpoint.await_args_list
    ] == section_ids


@pytest.mark.asyncio
async def test_incremental_writer_resume_skips_completed_sections():
    run_id = uuid.uuid4()
    executor = object.__new__(EditorialV3Executor)
    executor.pipeline_run = SimpleNamespace(id=run_id)
    executor.project = SimpleNamespace(id=uuid.uuid4())
    executor.runtime = SimpleNamespace(event=AsyncMock())
    executor.execution_manifest = {
        "feature_flags": {"v3_writer_section_repair_attempts": 1}
    }
    executor._stage_context = None
    executor.context_budget = SimpleNamespace(
        compact_writer_input=lambda payload, maximum_characters: (
            payload,
            SimpleNamespace(as_payload=lambda: {}),
        )
    )
    executor._progress_checkpoint = AsyncMock()
    executor._validate_writer_section_evidence = lambda unit, state: None
    section_ids = ["foundation", "execution", "verification"]
    persisted = {
        "foundation": _unit("foundation", first=True, block_count=4),
        "execution": _unit("execution", block_count=3),
    }
    state = V3PipelineState(
        project_id=executor.project.id,
        pipeline_run_id=run_id,
        writer_sections={
            key: value.model_dump(mode="json") for key, value in persisted.items()
        },
        writer_completed_section_ids=["foundation", "execution"],
    )

    async def agent_call(**kwargs):
        section_id = kwargs["input_json"]["current_section"]["section_id"]
        return _unit(section_id, block_count=3).model_dump(mode="json")

    executor._agent_call = AsyncMock(side_effect=agent_call)
    writer_input = {
        "editorial_sequence": [
            {"section_id": section_id, "maximum_depth_weight": 1.0}
            for section_id in section_ids
        ],
        "section_dossiers": [],
        "claim_catalog": [],
        "editorial_intelligence": {},
        "target_word_range": [900, 1200],
    }

    draft = await executor._write_incremental_sections(
        state,
        writer_input=writer_input,
        common_prompt="Prompt global. ",
        mode_prompt="Prompt de modo.",
        target_word_range=(900, 1200),
    )

    assert isinstance(draft, V3WriterOutput)
    executor._agent_call.assert_awaited_once()
    assert (
        executor._agent_call.await_args.kwargs["input_json"]["current_section"][
            "section_id"
        ]
        == "verification"
    )
    executor._progress_checkpoint.assert_awaited_once()
    assert executor._progress_checkpoint.await_args.kwargs["unit_id"] == "verification"


def test_writer_block_allocation_guarantees_valid_complete_draft_floor():
    assert EditorialV3Executor._writer_section_minimum_block_counts(3) == [4, 3, 3]
    assert EditorialV3Executor._writer_section_minimum_block_counts(4) == [3, 3, 2, 2]
    assert EditorialV3Executor._writer_section_minimum_block_counts(6) == [2] * 6


def test_writer_block_allocation_respects_complete_article_ceiling():
    minimums = EditorialV3Executor._writer_section_minimum_block_counts(11)
    maximums = EditorialV3Executor._writer_section_maximum_block_counts(
        11, minimum_block_counts=minimums
    )

    assert EditorialV3Executor._writer_section_maximum_block_counts(
        3, minimum_block_counts=[4, 3, 3]
    ) == [30, 30, 30]
    assert sum(maximums) == 300
    assert all(
        maximum >= minimum for maximum, minimum in zip(maximums, minimums, strict=True)
    )


def test_writer_section_boundary_rejects_unit_above_allocated_block_ceiling():
    with pytest.raises(ValueError, match="at most 30 blocks"):
        EditorialV3Executor._validate_writer_section_boundary(
            _unit("foundation", first=True, block_count=31),
            first=True,
            minimum_blocks=4,
            maximum_blocks=30,
        )


def test_writer_word_ranges_respect_complete_article_budget():
    sequence = [
        {
            "section_id": f"section_{index}",
            "minimum_depth_weight": index + 1,
            "maximum_depth_weight": index + 1,
        }
        for index in range(8)
    ]

    ranges = EditorialV3Executor._writer_section_word_ranges(
        sequence, target_word_range=(1800, 3500)
    )

    assert len(ranges) == 8
    assert sum(item[0] for item in ranges) == 1800
    assert sum(item[1] for item in ranges) == 3500
    assert all(40 <= minimum <= maximum for minimum, maximum in ranges)
    assert ranges[-1][0] > ranges[0][0]


def test_writer_word_ranges_reject_impossible_blueprint():
    sequence = [
        {"section_id": f"section_{index}", "maximum_depth_weight": 1}
        for index in range(30)
    ]

    with pytest.raises(
        V3PipelineBlocked, match="too small for the number of active sections"
    ):
        EditorialV3Executor._writer_section_word_ranges(
            sequence, target_word_range=(800, 1000)
        )


@pytest.mark.asyncio
async def test_incremental_writer_rejects_non_prefix_resume_order():
    run_id = uuid.uuid4()
    executor = object.__new__(EditorialV3Executor)
    executor.pipeline_run = SimpleNamespace(id=run_id)
    executor.project = SimpleNamespace(id=uuid.uuid4())
    state = V3PipelineState(
        project_id=executor.project.id,
        pipeline_run_id=run_id,
        writer_sections={
            "execution": _unit("execution", block_count=3).model_dump(mode="json")
        },
        writer_completed_section_ids=["execution"],
    )
    writer_input = {
        "editorial_sequence": [
            {"section_id": "foundation", "maximum_depth_weight": 1.0},
            {"section_id": "execution", "maximum_depth_weight": 1.0},
            {"section_id": "verification", "maximum_depth_weight": 1.0},
        ]
    }

    with pytest.raises(
        V3PipelineBlocked, match="not a prefix of the fixed editorial sequence"
    ):
        await executor._write_incremental_sections(
            state,
            writer_input=writer_input,
            common_prompt="Prompt global. ",
            mode_prompt="Prompt de modo.",
            target_word_range=(900, 1200),
        )


@pytest.mark.asyncio
async def test_incremental_writer_repairs_invalid_unit_before_checkpointing():
    run_id = uuid.uuid4()
    executor = object.__new__(EditorialV3Executor)
    executor.pipeline_run = SimpleNamespace(id=run_id)
    executor.project = SimpleNamespace(id=uuid.uuid4())
    executor.runtime = SimpleNamespace(event=AsyncMock())
    executor.execution_manifest = {
        "feature_flags": {"v3_writer_section_repair_attempts": 1}
    }
    executor._stage_context = None
    executor.context_budget = SimpleNamespace(
        compact_writer_input=lambda payload, maximum_characters: (
            payload,
            SimpleNamespace(as_payload=lambda: {}),
        )
    )
    executor._progress_checkpoint = AsyncMock()
    executor._validate_writer_section_evidence = lambda unit, state: None
    section_ids = ["foundation", "execution", "verification"]

    short = _unit("foundation", first=True, block_count=4)
    for block in short.blocks:
        if block.type == "paragraph":
            block.sentences[0].text = "Conteúdo curto."

    async def agent_call(**kwargs):
        key = kwargs["key"]
        section_id = kwargs["input_json"]["current_section"]["section_id"]
        if key == "article_section:foundation":
            return short.model_dump(mode="json")
        return _unit(
            section_id,
            first=section_id == "foundation",
            block_count=4 if section_id == "foundation" else 3,
        ).model_dump(mode="json")

    executor._agent_call = AsyncMock(side_effect=agent_call)
    writer_input = {
        "editorial_sequence": [
            {"section_id": section_id, "maximum_depth_weight": 1.0}
            for section_id in section_ids
        ],
        "section_dossiers": [],
        "claim_catalog": [],
        "editorial_intelligence": {},
        "target_word_range": [900, 1200],
    }
    state = V3PipelineState(project_id=executor.project.id, pipeline_run_id=run_id)

    draft = await executor._write_incremental_sections(
        state,
        writer_input=writer_input,
        common_prompt="Prompt global. ",
        mode_prompt="Prompt de modo.",
        target_word_range=(900, 1200),
    )

    assert isinstance(draft, V3WriterOutput)
    assert executor._agent_call.await_count == 4
    assert any(
        call.kwargs["key"] == "article_section_repair:foundation"
        for call in executor._agent_call.await_args_list
    )
    assert state.writer_section_repair_counts == {"foundation": 1}
    assert executor._progress_checkpoint.await_count == 3


@pytest.mark.asyncio
async def test_v3_graph_completes_the_full_generation_and_review_chain():
    async def complete_current_stage(state):
        if state.stage == V3Stage.content_contract:
            state.contract = {"contract_version": "editorial-v3.8"}
        elif state.stage == V3Stage.knowledge_gate:
            state.contract_validation = {"status": "passed"}
        elif state.stage == V3Stage.intelligence_planner:
            state.intelligence_state = {"version": "test"}
            state.intelligence_validation = {"status": "passed"}
        elif state.stage == V3Stage.research_planner:
            state.research_plan = {"tasks": [{"id": "task-1"}]}
        elif state.stage == V3Stage.source_discovery:
            state.raw_source_documents = [{"url": "https://example.org/source"}]
        elif state.stage == V3Stage.source_reader:
            state.source_documents = [{"url": "https://example.org/source"}]
        elif state.stage == V3Stage.source_coverage_gate:
            state.source_coverage_report = {"status": "passed"}
        elif state.stage == V3Stage.knowledge_synthesizer:
            state.section_dossiers = [{"section_id": "foundation"}]
        elif state.stage == V3Stage.evidence_graph_builder:
            state.intelligence_state = {"version": "test", "evidence": True}
        elif state.stage == V3Stage.intelligence_gate:
            state.intelligence_validation = {"status": "passed"}
        elif state.stage == V3Stage.knowledge_completeness_gate:
            state.completeness_report = {"status": "passed"}
        elif state.stage == V3Stage.writer:
            state.draft = {"title": "Draft validado"}
        elif state.stage == V3Stage.development_editor:
            state.development_review = {"status": "passed"}
        elif state.stage == V3Stage.fact_checker:
            state.fact_check = {"status": "passed"}
        elif state.stage == V3Stage.language_editor:
            state.language_review = {"status": "passed"}
        elif state.stage == V3Stage.external_reference_gate:
            state.external_reference_report = {"status": "passed"}
        elif state.stage == V3Stage.finalizer:
            state.final_package = {"status": "ready"}
        elif state.stage == V3Stage.quality_gate:
            state.quality_evaluation = {"status": "passed"}
        return state

    graph = EditorialIntelligenceV3Graph(
        V3PipelineNodes(
            **{name: complete_current_stage for name in V3PipelineNodes.__annotations__}
        )
    )
    state = V3PipelineState(project_id=uuid.uuid4())

    result = await graph.run(state)

    assert result.stage == V3Stage.completed
    assert result.blocking_code is None
    assert result.graph_transition_count == 19


@pytest.mark.asyncio
async def test_incremental_writer_preserves_only_completed_units_after_interruption():
    run_id = uuid.uuid4()
    executor = object.__new__(EditorialV3Executor)
    executor.pipeline_run = SimpleNamespace(id=run_id)
    executor.project = SimpleNamespace(id=uuid.uuid4())
    executor.runtime = SimpleNamespace(event=AsyncMock())
    executor.execution_manifest = {
        "feature_flags": {"v3_writer_section_repair_attempts": 1}
    }
    executor._stage_context = None
    executor.context_budget = SimpleNamespace(
        compact_writer_input=lambda payload, maximum_characters: (
            payload,
            SimpleNamespace(as_payload=lambda: {}),
        )
    )
    executor._progress_checkpoint = AsyncMock()
    executor._validate_writer_section_evidence = lambda unit, state: None

    async def agent_call(**kwargs):
        section_id = kwargs["input_json"]["current_section"]["section_id"]
        if section_id == "execution":
            raise RuntimeError("simulated provider interruption")
        return _unit(section_id, first=True, block_count=4).model_dump(mode="json")

    executor._agent_call = AsyncMock(side_effect=agent_call)
    section_ids = ["foundation", "execution", "verification"]
    writer_input = {
        "editorial_sequence": [
            {"section_id": section_id, "maximum_depth_weight": 1.0}
            for section_id in section_ids
        ],
        "section_dossiers": [],
        "claim_catalog": [],
        "editorial_intelligence": {},
        "target_word_range": [900, 1200],
    }
    state = V3PipelineState(project_id=executor.project.id, pipeline_run_id=run_id)

    with pytest.raises(RuntimeError, match="simulated provider interruption"):
        await executor._write_incremental_sections(
            state,
            writer_input=writer_input,
            common_prompt="Prompt global. ",
            mode_prompt="Prompt de modo.",
            target_word_range=(900, 1200),
        )

    assert state.writer_completed_section_ids == ["foundation"]
    assert set(state.writer_sections) == {"foundation"}
    executor._progress_checkpoint.assert_awaited_once()
    assert executor._progress_checkpoint.await_args.kwargs["unit_id"] == "foundation"


@pytest.mark.asyncio
async def test_incremental_writer_never_checkpoints_a_unit_that_remains_invalid():
    run_id = uuid.uuid4()
    executor = object.__new__(EditorialV3Executor)
    executor.pipeline_run = SimpleNamespace(id=run_id)
    executor.project = SimpleNamespace(id=uuid.uuid4())
    executor.runtime = SimpleNamespace(event=AsyncMock())
    executor.execution_manifest = {
        "feature_flags": {"v3_writer_section_repair_attempts": 1}
    }
    executor._stage_context = None
    executor.context_budget = SimpleNamespace(
        compact_writer_input=lambda payload, maximum_characters: (
            payload,
            SimpleNamespace(as_payload=lambda: {}),
        )
    )
    executor._progress_checkpoint = AsyncMock()
    executor._validate_writer_section_evidence = lambda unit, state: None

    invalid = _unit("foundation", first=True, block_count=4)
    for block in invalid.blocks:
        if block.type == "paragraph":
            block.sentences[0].text = "Conteúdo insuficiente."
    executor._agent_call = AsyncMock(return_value=invalid.model_dump(mode="json"))
    writer_input = {
        "editorial_sequence": [
            {"section_id": section_id, "maximum_depth_weight": 1.0}
            for section_id in ["foundation", "execution", "verification"]
        ],
        "section_dossiers": [],
        "claim_catalog": [],
        "editorial_intelligence": {},
        "target_word_range": [900, 1200],
    }
    state = V3PipelineState(project_id=executor.project.id, pipeline_run_id=run_id)

    with pytest.raises(V3PipelineBlocked, match="remained invalid after repair"):
        await executor._write_incremental_sections(
            state,
            writer_input=writer_input,
            common_prompt="Prompt global. ",
            mode_prompt="Prompt de modo.",
            target_word_range=(900, 1200),
        )

    assert state.writer_completed_section_ids == []
    assert state.writer_sections == {}
    executor._progress_checkpoint.assert_not_awaited()
