import uuid
from types import SimpleNamespace

import pytest

from app.api.routes import _editorial_diagnostic
from app.db.models import AgentRun
from app.orchestration.executor import PipelineExecutor
from app.orchestration.state import PipelineState
from app.services.llm_gateway import ProviderError


def finding(severity: str) -> dict:
    return {
        "block_id": str(uuid.uuid4()),
        "sentence": "Uma afirmação",
        "issue": "Ajustar a afirmação",
        "severity": severity,
        "suggested_action": "Reescrever com fidelidade",
    }


def test_minor_editor_findings_are_advisory_and_do_not_block_delivery():
    output = {
        "decision": "rewrite",
        "fidelity_findings": [finding("minor")],
        "language_findings": [finding("minor")],
        "rewrite_block_ids": [str(uuid.uuid4())],
    }

    assert (
        PipelineExecutor._editor_resolution(output, rewrite_budget_remaining=True)
        == "approved_with_advisory_findings"
    )
    assert PipelineExecutor._blocking_editor_findings(output) == []


@pytest.mark.asyncio
async def test_editor_provider_output_fallback_requests_rewrite_for_bad_draft():
    project_id = uuid.uuid4()
    pipeline_run_id = uuid.uuid4()
    block_id = str(uuid.uuid4())
    editor_run_id = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"pipeline:{pipeline_run_id}:editor:1",
    )
    draft = {
        "title": "Guia completo de energia solar",
        "title_evidence": [],
        "blocks": [
            {
                "block_id": block_id,
                "type": "paragraph",
                "position": 0,
                "sentences": [
                    {"text": "Texto validado.", "is_factual": False, "evidence": []}
                ],
            }
        ],
        "unsupported_claims": [],
    }
    editor_run = SimpleNamespace(
        feedback={},
        output_json=None,
        decision=None,
        recovered=False,
        recovery_code=None,
        recovered_by_agent_run_id=None,
    )

    class Runtime:
        def __init__(self):
            self.events = []

        async def call(self, *_args, **_kwargs):
            raise ProviderError(
                "invalid_output",
                provider="gemini",
                model="gemini-editor",
                retryable=False,
                error_code="provider_schema_invalid",
            )

        async def event(self, *args, **kwargs):
            self.events.append((args, kwargs))

    class Db:
        def __init__(self):
            self.added = []

        async def get(self, model, identifier):
            if model is AgentRun and identifier == editor_run_id:
                return editor_run
            return None

        def add(self, value):
            self.added.append(value)

        async def flush(self):
            return None

        async def commit(self):
            return None

    executor = object.__new__(PipelineExecutor)
    executor.project = SimpleNamespace(
        id=project_id,
        language="pt-BR",
        audience="leitores",
    )
    executor.pipeline_run = SimpleNamespace(id=pipeline_run_id)
    executor.runtime = Runtime()
    executor.db = Db()
    executor.skills = SimpleNamespace(prompt_fragment=lambda _role: "")
    executor._stage_context = None
    executor._flag = lambda _name: 1

    async def no_op(*_args, **_kwargs):
        return None

    async def approved_facts():
        return []

    executor._stage = no_op
    executor._cancellation_boundary = no_op
    executor._approved_fact_dicts = approved_facts
    executor._context = lambda _run_id: {}
    executor._revision_prompt = lambda prompt: prompt
    executor._handoff = no_op

    state = PipelineState(
        project_id=project_id,
        pipeline_run_id=pipeline_run_id,
        draft=draft,
    )

    await executor.editor(state)

    assert state.draft == draft
    assert state.editorial_review["decision"] == "rejected"
    assert state.editorial_review["resolution"] == "provider_output_blocked"
    assert state.editorial_review["model_decision"] == "provider_output_invalid"
    assert state.editorial_review["rewrite_block_ids"] == []
    assert state.editorial_review["open_evidence_gaps"]
    assert "word_count" in " ".join(
        state.editorial_review["deterministic_quality_gaps"]
    )
    assert editor_run.recovered is True
    assert editor_run.recovery_code == "provider_schema_invalid"
    assert editor_run.recovered_by_agent_run_id is not None
    assert len(executor.db.added) == 1
    assert executor.db.added[0].model == "markdown-draft-preservation-v1"
    assert str(executor.db.added[0].decision) == "GateDecision.rejected"
    assert executor.runtime.events[0][0][2] == "editor.provider_output_recovered"


def test_major_fidelity_gets_one_targeted_rewrite_then_deterministic_repair():
    output = {
        "decision": "rewrite",
        "fidelity_findings": [finding("major")],
        "language_findings": [],
        "rewrite_block_ids": [str(uuid.uuid4())],
    }

    assert (
        PipelineExecutor._editor_resolution(output, rewrite_budget_remaining=True)
        == "targeted_rewrite"
    )
    assert (
        PipelineExecutor._editor_resolution(output, rewrite_budget_remaining=False)
        == "deterministic_targeted_repair"
    )


def test_major_language_finding_requires_the_same_targeted_correction():
    output = {
        "decision": "rewrite",
        "fidelity_findings": [],
        "language_findings": [finding("major")],
        "rewrite_block_ids": [str(uuid.uuid4())],
    }

    assert (
        PipelineExecutor._blocking_editor_findings(output)
        == (output["language_findings"])
    )
    assert (
        PipelineExecutor._editor_resolution(output, rewrite_budget_remaining=True)
        == "targeted_rewrite"
    )


def test_approved_decision_cannot_override_a_major_language_finding():
    block_id = str(uuid.uuid4())
    output = {
        "decision": "approved",
        "fidelity_findings": [],
        "language_findings": [
            {
                **finding("major"),
                "block_id": block_id,
            }
        ],
        "rewrite_block_ids": [block_id],
    }

    assert (
        PipelineExecutor._editor_resolution(output, rewrite_budget_remaining=True)
        == "targeted_rewrite"
    )


def test_targeted_revision_replaces_only_the_two_requested_blocks():
    first_id, second_id, untouched_id = (
        str(uuid.uuid4()),
        str(uuid.uuid4()),
        str(uuid.uuid4()),
    )
    prior = {
        "title": "Guia completo de energia solar",
        "title_evidence": [],
        "blocks": [
            {"block_id": first_id, "type": "paragraph", "sentences": [{"text": "A"}]},
            {"block_id": second_id, "type": "paragraph", "sentences": [{"text": "B"}]},
            {
                "block_id": untouched_id,
                "type": "paragraph",
                "sentences": [{"text": "Preservado"}],
            },
        ],
        "unsupported_claims": [],
    }
    revision = {
        "blocks": [
            {
                "block_id": first_id,
                "type": "paragraph",
                "sentences": [{"text": "A corrigido"}],
            },
            {
                "block_id": second_id,
                "type": "paragraph",
                "sentences": [{"text": "B corrigido"}],
            },
        ],
        "unsupported_claims": [],
    }

    merged = PipelineExecutor._merge_targeted_revision(
        prior, revision, {first_id, second_id}
    )

    assert [block["sentences"][0]["text"] for block in merged["blocks"]] == [
        "A corrigido",
        "B corrigido",
        "Preservado",
    ]
    assert prior["blocks"][0]["sentences"][0]["text"] == "A"


def test_deterministic_recovery_removes_only_exact_grave_and_meta_sentences():
    block_id = str(uuid.uuid4())
    prior = {
        "title": "Guia completo de energia solar",
        "title_evidence": [],
        "blocks": [
            {
                "block_id": block_id,
                "type": "paragraph",
                "sentences": [
                    {"text": "Remover exatamente esta frase."},
                    {"text": "Esta frase deve permanecer."},
                    {"text": "Este artigo se baseia em fatos aprovados."},
                ],
            }
        ],
        "unsupported_claims": [],
    }
    review = {
        "fidelity_findings": [],
        "language_findings": [
            {
                "block_id": block_id,
                "sentence": "Remover exatamente esta frase.",
                "severity": "major",
            }
        ],
    }

    recovered, removed = PipelineExecutor._recover_targeted_revision(prior, review)

    assert removed == 2
    assert recovered["blocks"][0]["sentences"] == [
        {"text": "Esta frase deve permanecer."}
    ]
    assert prior["blocks"][0]["sentences"][0]["text"] == (
        "Remover exatamente esta frase."
    )


def test_research_attempt_is_unique_per_cycle_instead_of_question_position():
    state = PipelineState(project_id=uuid.uuid4(), research_cycle=0)
    first_cycle = PipelineExecutor._research_attempt(state)
    state.research_cycle = 1
    second_cycle = PipelineExecutor._research_attempt(state)

    assert first_cycle == 1
    assert second_cycle == 2


def test_nonfactual_transitions_and_headings_remain_evidence_free():
    executor = object.__new__(PipelineExecutor)
    executor.project = SimpleNamespace(name="Guia", topic="Tema")
    fact_id = uuid.uuid4()
    approved = [
        {
            "id": str(fact_id),
            "claim_text": "A condição foi verificada.",
            "source": {"domain": "example.com"},
        }
    ]
    output = {
        "title": "Guia editorial completo",
        "title_evidence": [],
        "blocks": [
            {
                "block_id": None,
                "type": "h2",
                "position": 0,
                "sentences": [
                    {
                        "text": "O que observar antes de começar",
                        "is_factual": False,
                        "evidence": [],
                    }
                ],
            },
            {
                "block_id": None,
                "type": "paragraph",
                "position": 1,
                "sentences": [
                    {
                        "text": "Esse contexto ajuda a organizar a leitura.",
                        "is_factual": False,
                        "evidence": [],
                    },
                    {
                        "text": "A condição foi verificada.",
                        "is_factual": True,
                        "evidence": [
                            {
                                "fact_id": str(fact_id),
                                "entailment_score": 0.9,
                            }
                        ],
                    },
                ],
            },
        ],
        "unsupported_claims": [],
    }

    normalized, invalid, fallback_used, removed = executor._normalize_writer_output(
        PipelineState(project_id=uuid.uuid4()), output, approved
    )

    assert invalid == set()
    assert fallback_used is False
    assert removed == 0
    assert normalized["title_evidence"] == []
    assert normalized["blocks"][0]["sentences"][0]["evidence"] == []
    assert normalized["blocks"][1]["sentences"][0]["evidence"] == []


def test_editorial_diagnostic_exposes_safe_reason_and_delivery_resolution():
    pipeline_run_id = uuid.uuid4()
    editor = SimpleNamespace(
        agent_role="editor",
        pipeline_run_id=pipeline_run_id,
        decision="rewrite",
        output_json={
            "decision": "rewrite",
            "fidelity_findings": [finding("major")],
            "language_findings": [],
        },
    )
    repair = SimpleNamespace(
        agent_role="editorial_repair",
        decision="approved",
        model="targeted-sentence-removal-v2",
        feedback={"resolution": "deterministic_targeted_repair"},
    )

    diagnostic = _editorial_diagnostic([editor, repair])

    assert diagnostic["pipeline_run_id"] == pipeline_run_id
    assert diagnostic["decision"] == "approved"
    assert diagnostic["model_decision"] == "rewrite"
    assert diagnostic["resolution"] == "deterministic_targeted_repair"
    assert diagnostic["blocking_finding_count"] == 1
    assert diagnostic["findings"][0]["issue"] == "Ajustar a afirmação"


def test_writer_removes_unknown_fact_id_and_keeps_valid_evidence():
    executor = object.__new__(PipelineExecutor)
    executor.project = SimpleNamespace(name="Guia", topic="Tema")
    approved_id = uuid.uuid4()
    unknown_id = uuid.uuid4()
    approved = [
        {
            "id": str(approved_id),
            "research_question_id": str(uuid.uuid4()),
            "research_question": "Pergunta",
            "claim_text": "A condição foi verificada.",
            "confidence_score": 0.8,
            "source": {"title": "Fonte", "domain": "example.com"},
        }
    ]
    output = {
        "title": "Guia",
        "title_evidence": [{"fact_id": str(approved_id), "entailment_score": 0.8}],
        "blocks": [
            {
                "block_id": None,
                "type": "paragraph",
                "position": 0,
                "sentences": [
                    {
                        "text": "A condição foi verificada.",
                        "is_factual": True,
                        "evidence": [
                            {"fact_id": str(approved_id), "entailment_score": 0.8},
                            {"fact_id": str(unknown_id), "entailment_score": 0.8},
                        ],
                    }
                ],
            }
        ],
        "unsupported_claims": [],
    }

    normalized, invalid, fallback_used, meta_removed = (
        executor._normalize_writer_output(
            PipelineState(project_id=uuid.uuid4()), output, approved
        )
    )

    assert invalid == {str(unknown_id)}
    assert fallback_used is False
    assert meta_removed == 0
    assert normalized["blocks"][0]["sentences"][0]["evidence"] == [
        {
            "fact_id": str(approved_id),
            "entailment_score": 0.8,
        }
    ]


def test_writer_blocks_when_invalid_evidence_removes_all_supportable_content():
    executor = object.__new__(PipelineExecutor)
    executor.project = SimpleNamespace(name="Guia", topic="Tema")
    approved_id = uuid.uuid4()
    unknown_id = uuid.uuid4()
    approved = [
        {
            "id": str(approved_id),
            "claim_text": "A condição foi verificada.",
            "source": {"domain": "example.com"},
        }
    ]
    output = {
        "title": "Guia gerado",
        "title_evidence": [
            {
                "fact_id": str(unknown_id),
                "entailment_score": 0.8,
            }
        ],
        "blocks": [
            {
                "block_id": None,
                "type": "paragraph",
                "position": 0,
                "sentences": [
                    {
                        "text": "Afirmação sem referência válida.",
                        "is_factual": True,
                        "evidence": [
                            {
                                "fact_id": str(unknown_id),
                                "entailment_score": 0.8,
                            }
                        ],
                    }
                ],
            }
        ],
        "unsupported_claims": [],
    }

    with pytest.raises(ValueError, match="no supportable content"):
        executor._normalize_writer_output(
            PipelineState(project_id=uuid.uuid4()), output, approved
        )


def test_writer_normalization_never_merges_source_metadata_into_copy():
    executor = object.__new__(PipelineExecutor)
    executor.project = SimpleNamespace(name="Guia", topic="Tema")
    fact_id = uuid.uuid4()
    approved = [
        {
            "id": str(fact_id),
            "claim_text": "Regar demais é um erro comum.",
            "source": {
                "title": "Os 10 erros mais comuns",
                "domain": "example.com",
            },
        }
    ]
    output = {
        "title": "Cuidados antes de começar",
        "title_evidence": [],
        "blocks": [
            {
                "block_id": None,
                "type": "paragraph",
                "position": 0,
                "sentences": [
                    {
                        "text": "Regar demais é um erro comum.",
                        "is_factual": True,
                        "evidence": [
                            {
                                "fact_id": str(fact_id),
                                "entailment_score": 0.8,
                            }
                        ],
                    }
                ],
            }
        ],
        "unsupported_claims": [],
    }

    normalized, *_ = executor._normalize_writer_output(
        PipelineState(project_id=uuid.uuid4()), output, approved
    )

    visible = " ".join(
        sentence["text"]
        for block in normalized["blocks"]
        for sentence in block["sentences"]
    ).casefold()
    assert visible == "regar demais é um erro comum."
    assert "example.com" not in visible
    assert "os 10 erros mais comuns" not in visible


def test_internal_research_questions_become_natural_blog_headings():
    questions = {
        "Quais são as condições ideais para a germinação de sementes?": (
            "Condições ideais para a germinação de sementes"
        ),
        "Quais métodos são mais eficazes para germinar sementes?": (
            "Métodos para germinar sementes"
        ),
        "Como identificar uma semente viável?": "Como identificar uma semente viável",
        "Quais são os principais erros a evitar durante a germinação?": (
            "Principais erros a evitar durante a germinação"
        ),
        "Quais erros evitar?": "Erros a evitar",
    }

    assert {
        question: PipelineExecutor._editorial_heading(question)
        for question in questions
    } == questions
    assert all("?" not in heading for heading in questions.values())


def test_long_internal_question_becomes_a_concise_editorial_heading():
    question = (
        "O que é a germinação de sementes de cannabis, quais condições "
        "ambientais favorecem o processo e quais métodos são adequados"
    )

    heading = PipelineExecutor._editorial_heading(question)

    assert heading == "Germinação de sementes de cannabis"
    assert len(heading) <= 80
    assert heading.casefold() != question.casefold()


def test_seo_heading_structure_keeps_editorial_headings_evidence_free():
    executor = object.__new__(PipelineExecutor)
    executor.project = SimpleNamespace(name="Guia", topic="Tema", language="pt-BR")
    state = PipelineState(
        project_id=uuid.uuid4(),
        plan={
            "google_keywords": ["como germinar semente de cannabis"],
            "semantic_keywords": ["sementes de cannabis"],
            "questions": [{"question": "Quais condições favorecem a germinação?"}],
        },
    )
    output = {
        "title": "Como germinar semente de cannabis",
        "title_evidence": [],
        "blocks": [
            {
                "block_id": None,
                "type": "h2",
                "position": 0,
                "sentences": [
                    {
                        "text": "Quais condições favorecem a germinação?",
                        "is_factual": False,
                        "evidence": [],
                    }
                ],
            },
            {
                "block_id": None,
                "type": "paragraph",
                "position": 1,
                "sentences": [
                    {
                        "text": "A seção desenvolve o contexto.",
                        "is_factual": False,
                        "evidence": [],
                    }
                ],
            },
        ],
        "unsupported_claims": [],
    }

    normalized = executor._ensure_seo_heading_structure(state, output, [])

    heading = normalized["blocks"][0]["sentences"][0]
    assert heading["text"] == "Condições que favorecem a germinação"
    assert heading["evidence"] == []
    assert normalized["title_evidence"] == []


def test_short_blog_and_shallow_heading_structure_trigger_regeneration():
    draft = {
        "title": "Guia de germinação de cannabis",
        "blocks": [
            {"type": "h2", "sentences": [{"text": "Primeiros passos"}]},
            {"type": "h3", "sentences": [{"text": "Condições"}]},
            {
                "type": "paragraph",
                "sentences": [{"text": "Texto curto e insuficiente."}],
            },
        ],
    }

    assert PipelineExecutor._draft_quality_gaps(draft, 650) == [
        "word_count:12<650",
        "h2_count:1<4",
    ]


def test_mechanical_heading_and_paragraph_pattern_triggers_regeneration():
    blocks = []
    for index in range(6):
        blocks.extend(
            [
                {
                    "type": "h2",
                    "sentences": [{"text": f"Seção editorial {index}"}],
                },
                {
                    "type": "paragraph",
                    "sentences": [
                        {
                            "text": (
                                "A condição precisa ser observada com atenção "
                                "antes de iniciar esta etapa."
                            )
                        },
                        {
                            "text": (
                                "O acompanhamento ajuda a reconhecer mudanças "
                                "relevantes durante o processo."
                            )
                        },
                    ],
                },
            ]
        )
    draft = {
        "title": "Como acompanhar o processo com segurança",
        "blocks": blocks,
    }

    gaps = PipelineExecutor._draft_quality_gaps(
        draft,
        1,
        maximum_words=1000,
        minimum_h2=4,
        minimum_h3=0,
    )

    assert "mechanical_prose_pattern" in gaps


def test_heading_normalizer_does_not_invent_h3_or_stuff_keyword():
    fact_id = str(uuid.uuid4())
    evidence = [{"fact_id": fact_id, "entailment_score": 1}]
    state = PipelineState(
        project_id=uuid.uuid4(),
        plan={
            "questions": [],
            "seo_brief": {
                "focus_keyphrase": "energia solar",
                "related_keyphrases": [],
            },
        },
    )
    output = {
        "title": "Guia completo de energia solar",
        "title_evidence": evidence,
        "blocks": [
            {
                "block_id": str(uuid.uuid4()),
                "type": "h2",
                "position": 0,
                "sentences": [
                    {
                        "text": "Critérios antes da decisão",
                        "is_factual": False,
                        "evidence": evidence,
                    }
                ],
            }
        ],
        "unsupported_claims": [],
    }

    normalized = PipelineExecutor._ensure_seo_heading_structure(
        state,
        output,
        [{"id": fact_id, "confidence_score": 1}],
    )

    assert [block["type"] for block in normalized["blocks"]] == ["h2"]
    assert normalized["blocks"][0]["sentences"][0]["text"] == (
        "Critérios antes da decisão"
    )


def test_internal_research_question_is_rewritten_as_editorial_heading():
    fact_id = str(uuid.uuid4())
    question = "Quais são as condições ideais?"
    state = PipelineState(
        project_id=uuid.uuid4(),
        plan={
            "questions": [{"question": question}],
            "seo_brief": {
                "focus_keyphrase": "energia solar",
                "related_keyphrases": [],
            },
        },
    )
    evidence = [{"fact_id": fact_id, "entailment_score": 1}]
    output = {
        "title": "Guia completo de energia solar",
        "title_evidence": evidence,
        "blocks": [
            {
                "block_id": str(uuid.uuid4()),
                "type": "h2",
                "position": 0,
                "sentences": [
                    {
                        "text": question,
                        "is_factual": False,
                        "evidence": evidence,
                    }
                ],
            }
        ],
        "unsupported_claims": [],
    }
    approved = [{"id": fact_id, "confidence_score": 1}]

    normalized = PipelineExecutor._ensure_seo_heading_structure(state, output, approved)

    heading = normalized["blocks"][0]["sentences"][0]["text"]
    assert not heading.endswith("?")
    assert heading != question


def test_slug_transliterates_portuguese_diacritics():
    assert PipelineExecutor._slug("Germinação de cannabis") == (
        "germinacao-de-cannabis"
    )


def test_visible_source_credit_is_removed_without_manufacturing_copy():
    executor = object.__new__(PipelineExecutor)
    executor.project = SimpleNamespace(name="Guia", topic="Tema")
    approved_id = uuid.uuid4()
    approved = [
        {
            "id": str(approved_id),
            "claim_text": "A condição foi verificada.",
            "source": {"title": "Site Externo", "domain": "externo.example"},
        }
    ]
    output = {
        "title": "Guia editorial completo",
        "title_evidence": [],
        "blocks": [
            {
                "block_id": None,
                "type": "paragraph",
                "position": 0,
                "sentences": [
                    {
                        "text": "A condição foi verificada (Fonte: Site Externo).",
                        "is_factual": True,
                        "evidence": [
                            {
                                "fact_id": str(approved_id),
                                "entailment_score": 0.8,
                            }
                        ],
                    },
                    {
                        "text": "A condição foi verificada.",
                        "is_factual": True,
                        "evidence": [
                            {
                                "fact_id": str(approved_id),
                                "entailment_score": 0.8,
                            }
                        ],
                    },
                ],
            }
        ],
        "unsupported_claims": [],
    }

    normalized, invalid, fallback_used, meta_removed = (
        executor._normalize_writer_output(
            PipelineState(project_id=uuid.uuid4()), output, approved
        )
    )

    assert invalid == set()
    assert fallback_used is False
    assert meta_removed == 1
    visible = " ".join(
        sentence["text"]
        for block in normalized["blocks"]
        for sentence in block["sentences"]
    )
    assert visible == "A condição foi verificada."


@pytest.mark.asyncio
async def test_writer_uses_one_full_generation_and_sends_quality_gaps_to_editor(
    monkeypatch,
):
    project_id = uuid.uuid4()
    pipeline_run_id = uuid.uuid4()
    approved_id = uuid.uuid4()
    approved = [
        {
            "id": str(approved_id),
            "research_question_id": str(uuid.uuid4()),
            "research_question": "O que as fontes demonstram?",
            "claim_text": "A condição principal foi verificada.",
            "confidence_score": 0.8,
            "source": {"title": "Fonte", "domain": "example.com"},
        }
    ]
    writer_output = {
        "title": "Guia verificável",
        "title_evidence": [],
        "blocks": [
            {
                "block_id": None,
                "type": "paragraph",
                "position": 0,
                "sentences": [
                    {
                        "text": "A condição principal foi verificada.",
                        "is_factual": True,
                        "evidence": [
                            {
                                "fact_id": str(approved_id),
                                "entailment_score": 0.8,
                            }
                        ],
                    }
                ],
            }
        ],
        "unsupported_claims": [],
    }

    class Coverage:
        valid_fact_ids = (approved_id,)
        evidence_ready = True

    class CoverageService:
        def __init__(self, *_args):
            pass

        async def evaluate(self, *_args, **_kwargs):
            return Coverage()

    class Runtime:
        def __init__(self):
            self.calls = []
            self.events = []

        async def call(self, *_args, **kwargs):
            self.calls.append((_args, kwargs))
            return writer_output

        async def event(self, *_args, **kwargs):
            self.events.append((_args, kwargs))

    class Db:
        def __init__(self):
            self.runs = {}

        async def get(self, _model, run_id):
            return self.runs.setdefault(
                run_id, SimpleNamespace(feedback={}, output_json=None)
            )

    class Versions:
        def __init__(self):
            self.persisted = []

        async def persist_draft(self, *_args):
            self.persisted.append(_args)

    monkeypatch.setattr(
        "app.orchestration.executor.ResearchCoverageService", CoverageService
    )
    executor = object.__new__(PipelineExecutor)
    executor.project = SimpleNamespace(
        id=project_id,
        name="Guia",
        topic="Tema",
        language="pt-BR",
        audience="leitores",
    )
    executor.pipeline_run = SimpleNamespace(id=pipeline_run_id)
    executor.runtime = Runtime()
    executor.db = Db()
    executor.versions = Versions()
    executor.skills = SimpleNamespace(prompt_fragment=lambda _role: "")
    executor._stage_context = None
    executor._flag = lambda _name: 5

    async def no_op(*_args, **_kwargs):
        return None

    async def approved_facts():
        return approved

    executor._stage = no_op
    executor._cancellation_boundary = no_op
    executor._approved_fact_dicts = approved_facts
    executor._context = lambda _run_id: {}
    executor._revision_prompt = lambda prompt: prompt
    executor._handoff = no_op
    state = PipelineState(
        project_id=project_id,
        pipeline_run_id=pipeline_run_id,
        research_audit={"decision": "approved"},
        plan={"questions": []},
    )

    await executor.writer(state)

    writer_run_id = executor._agent_run_id("writer", 1)
    assert len(executor.runtime.calls) == 1
    assert executor.versions.persisted[0][3] == writer_run_id
    assert state.draft["blocks"][0]["sentences"][0]["evidence"][0]["fact_id"] == str(
        approved_id
    )
    assert executor.db.runs[writer_run_id].feedback["full_regeneration_used"] is False
    assert executor.db.runs[writer_run_id].feedback["remaining_quality_gaps"]
    assert state.editorial_review["writer_quality_gaps"]
