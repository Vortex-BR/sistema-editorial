import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.orchestration.executor import PipelineExecutor
from app.db.models import GateDecision
from app.orchestration.state import PipelineState, Stage
from app.services.research_engine import SearchDocument
from app.services.search_policy import MAX_EXTRACTION_CHARS_PER_DOCUMENT
from app.services.search_policy import SEARCH_RESULTS_PER_MARKET


def document(url: str, *, market: str | None = None) -> SearchDocument:
    return SearchDocument(
        url=url,
        title=f"Source {url}",
        content="evidence " * 40,
        publisher="example.test",
        source_type="practical",
        reliability_score=0.8,
        accessed_at=datetime(2026, 7, 15, tzinfo=timezone.utc),
        extraction_method="serper_html_text",
        search_market=market,
    )


class FakeDb:
    def __init__(self, rows):
        self.rows = rows
        self.commits = 0

    async def get(self, _model, run_id):
        return self.rows.get(run_id)

    async def commit(self):
        self.commits += 1


class FakeRuntime:
    def __init__(self):
        self.events = []

    async def search_credential(self):
        return "serper", "stored-secret"

    async def event(self, *_args, **kwargs):
        self.events.append(kwargs)


@pytest.mark.asyncio
async def test_google_keyword_discovery_is_cached_and_never_used_as_fact_source():
    project_id = uuid.uuid4()
    pipeline_run_id = uuid.uuid4()

    class Db:
        def __init__(self):
            self.rows = {}

        async def get(self, _model, run_id):
            return self.rows.get(run_id)

        def add(self, row):
            self.rows[row.id] = row

        async def flush(self):
            return None

    class Research:
        def __init__(self):
            self.calls = 0

        async def discover_keywords(self, query, provider, api_key, **kwargs):
            self.calls += 1
            assert query == "germinação de cannabis"
            assert provider == "serper"
            assert api_key == "stored-secret"
            assert kwargs["market"].code == "br"
            return [
                "como germinar semente de cannabis",
                "germinação de cannabis",
            ]

    executor = object.__new__(PipelineExecutor)
    executor.project = SimpleNamespace(
        id=project_id,
        name="Guia de germinação",
        topic="germinação de cannabis",
    )
    executor.pipeline_run = SimpleNamespace(id=pipeline_run_id)
    executor.db = Db()
    executor.runtime = FakeRuntime()
    executor.research = Research()
    executor._stage_context = None
    state = PipelineState(
        project_id=project_id,
        pipeline_run_id=pipeline_run_id,
        plan={"semantic_keywords": ["germinação", "cannabis", "sementes"]},
    )

    await executor._ensure_google_keywords(
        state,
        provider="serper",
        api_key="stored-secret",
    )
    await executor._ensure_google_keywords(
        state,
        provider="serper",
        api_key="stored-secret",
    )

    assert executor.research.calls == 1
    assert state.plan["google_keywords"] == [
        "como germinar semente de cannabis",
        "germinação de cannabis",
    ]
    row = next(iter(executor.db.rows.values()))
    assert row.input_json["used_as_factual_source"] is False
    assert row.output_json["used_as_factual_source"] is False
    assert row.decision is GateDecision.approved


@pytest.mark.asyncio
async def test_empty_google_keyword_discovery_falls_back_to_topic():
    project_id = uuid.uuid4()
    pipeline_run_id = uuid.uuid4()

    class Db:
        def __init__(self):
            self.row = None

        async def get(self, _model, _run_id):
            return self.row

        def add(self, row):
            self.row = row

        async def flush(self):
            return None

    class Research:
        async def discover_keywords(self, *_args, **_kwargs):
            return []

    executor = object.__new__(PipelineExecutor)
    executor.project = SimpleNamespace(
        id=project_id,
        name="Guia de germinacao",
        topic="como germinar semente de cannabis",
    )
    executor.pipeline_run = SimpleNamespace(id=pipeline_run_id)
    executor.db = Db()
    executor.runtime = FakeRuntime()
    executor.research = Research()
    executor._stage_context = None
    state = PipelineState(
        project_id=project_id,
        pipeline_run_id=pipeline_run_id,
        plan={"semantic_keywords": ["germinacao", "cannabis"]},
    )

    await executor._ensure_google_keywords(
        state,
        provider="serper",
        api_key="stored-secret",
    )

    assert state.plan["google_keywords"] == []
    assert executor.db.row.decision is GateDecision.approved
    assert executor.db.row.output_json["error_code"] is None
    assert state.plan["seo_brief"]["focus_keyphrase"] == (
        "como germinar semente de cannabis"
    )


def test_seo_brief_keeps_the_primary_subject_instead_of_a_legal_side_note():
    executor = object.__new__(PipelineExecutor)
    executor.project = SimpleNamespace(
        name="Guia germinação de cannabis",
        topic=(
            "Criar um guia completo sobre germinação de sementes de cannabis, "
            "com um aviso para consultar a legislação vigente ao final."
        ),
        search_intent="informational",
        audience="Jardineiro iniciante",
    )
    plan = {
        "semantic_keywords": [
            "germinação de cannabis",
            "sementes de cannabis",
            "legislação sobre cannabis",
        ],
        "content_gaps": [],
        "competitor_angles": [],
    }

    brief = executor._build_seo_brief(
        plan,
        [
            "legislação sobre cannabis",
            "germinação de cannabis",
        ],
    )

    assert executor._keyword_seed_query() == "germinação de cannabis"
    assert brief["focus_keyphrase"] == "germinação de cannabis"
    assert brief["focus_keyphrase"] != "legislação sobre cannabis"


@pytest.mark.asyncio
async def test_technical_retry_reuses_each_question_source_payload():
    project_id = uuid.uuid4()
    pipeline_run_id = uuid.uuid4()
    questions = [
        {"id": str(uuid.uuid4()), "question": "Question one", "priority": 1},
        {"id": str(uuid.uuid4()), "question": "Question two", "priority": 2},
    ]
    state = PipelineState(
        project_id=project_id,
        pipeline_run_id=pipeline_run_id,
        stage=Stage.researcher,
        plan={"questions": questions, "semantic_keywords": []},
    )
    executor = object.__new__(PipelineExecutor)
    executor.project = SimpleNamespace(
        id=project_id,
        topic="topic",
        niche="niche",
        language="pt-BR",
    )
    executor.pipeline_run = SimpleNamespace(id=pipeline_run_id)
    executor.runtime = FakeRuntime()
    cached_documents = {
        executor._agent_run_id(f"researcher:{question['id']}", 1): [
            document(f"https://source-{index}.example/a"),
            document(f"https://source-{index}.example/b"),
        ]
        for index, question in enumerate(questions, start=1)
    }
    executor.db = FakeDb(
        {
            run_id: SimpleNamespace(
                input_json={
                    "sources": [item.as_payload() for item in documents]
                }
            )
            for run_id, documents in cached_documents.items()
        }
    )

    class SearchMustNotRun:
        async def search(self, *_args, **_kwargs):
            raise AssertionError("Serper was called during a technical retry")

    executor.research = SearchMustNotRun()
    extracted = []

    async def no_boundary():
        return None

    async def no_stage(*_args):
        return None

    async def extract(_state, question, documents, *, attempt, run_id):
        extracted.append((question["id"], documents, attempt, run_id))

    async def all_facts():
        return []

    async def no_handoff(*_args, **_kwargs):
        return None

    executor._cancellation_boundary = no_boundary
    executor._stage = no_stage
    executor._extract_question = extract
    executor._all_fact_dicts = all_facts
    executor._handoff = no_handoff

    await executor.researcher(state)

    assert len(extracted) == 2
    assert [item[2] for item in extracted] == [1, 1]
    assert all(len(item[1]) == 2 for item in extracted)
    # Each finished question is visible/durable before the stage-level commit.
    assert executor.db.commits == 3


@pytest.mark.asyncio
async def test_new_editorial_cycle_uses_a_new_search_identity():
    project_id = uuid.uuid4()
    pipeline_run_id = uuid.uuid4()
    question = {"id": str(uuid.uuid4()), "question": "Question", "priority": 1}
    executor = object.__new__(PipelineExecutor)
    executor.project = SimpleNamespace(
        id=project_id,
        topic="topic",
        niche=None,
        language="pt-BR",
    )
    executor.pipeline_run = SimpleNamespace(id=pipeline_run_id)
    prior_id = executor._agent_run_id(f"researcher:{question['id']}", 1)
    executor.db = FakeDb(
        {
            prior_id: SimpleNamespace(
                input_json={"sources": [document("https://old.example").as_payload()]}
            )
        }
    )

    new_id = executor._agent_run_id(f"researcher:{question['id']}", 2)

    assert new_id != prior_id
    assert await executor._cached_research_documents(new_id) == []


@pytest.mark.asyncio
async def test_new_question_prioritizes_local_market_then_international_markets():
    project_id = uuid.uuid4()
    pipeline_run_id = uuid.uuid4()
    question = {
        "id": str(uuid.uuid4()),
        "question": "Como germinar a semente?",
        "priority": 1,
        "search_queries": {
            "united_states": "hemp seed germination method",
            "spain": "método germinación semilla cáñamo",
            "switzerland": "Hanfsamen Keimungsmethode",
            "brazil": None,
        },
    }
    executor = object.__new__(PipelineExecutor)
    executor.project = SimpleNamespace(
        id=project_id,
        topic="Guia de germinação",
        niche=None,
        language="pt-BR",
    )
    executor.pipeline_run = SimpleNamespace(id=pipeline_run_id)
    executor.runtime = FakeRuntime()
    executor._stage_context = None

    class SearchSpy:
        def __init__(self):
            self.calls = []

        async def search(self, query, provider, api_key, **kwargs):
            market = kwargs["market"]
            self.calls.append((query, provider, api_key, kwargs))
            return [
                document(
                    f"https://{market.code}.example/article",
                    market=market.code,
                )
            ]

    executor.research = SearchSpy()
    state = PipelineState(
        project_id=project_id,
        pipeline_run_id=pipeline_run_id,
        stage=Stage.researcher,
        plan={"questions": [question]},
    )

    documents = await executor._search_question_markets(
        state,
        question,
        query="fallback",
        provider="serper",
        api_key="stored-secret",
    )

    assert [call[3]["market"].code for call in executor.research.calls] == [
        "br",
        "us",
        "es",
    ]
    assert all(
        call[3]["exclude_brazil"] is False for call in executor.research.calls
    )
    assert all(
        call[3]["max_results"] == SEARCH_RESULTS_PER_MARKET
        for call in executor.research.calls
    )
    assert {item.search_market for item in documents} == {"br", "us", "es"}
    assert executor.runtime.events[-1]["idempotency_key"].startswith(
        "research.market_search_completed:"
    )


@pytest.mark.asyncio
async def test_new_cycle_excludes_domains_already_seen_for_the_question():
    project_id = uuid.uuid4()
    pipeline_run_id = uuid.uuid4()
    question = {
        "id": str(uuid.uuid4()),
        "question": "Quais métodos funcionam?",
        "search_queries": {
            "united_states": "seed germination methods",
            "spain": "métodos de germinación",
            "switzerland": "Keimungsmethoden",
        },
    }
    executor = object.__new__(PipelineExecutor)
    executor.pipeline_run = SimpleNamespace(id=pipeline_run_id)
    prior_id = executor._agent_run_id(f"researcher:{question['id']}", 1)
    executor.db = FakeDb(
        {
            prior_id: SimpleNamespace(
                input_json={
                    "sources": [
                        document("https://already-seen.example/guide").as_payload()
                    ]
                }
            )
        }
    )
    state = PipelineState(
        project_id=project_id,
        pipeline_run_id=pipeline_run_id,
        stage=Stage.researcher,
        research_cycle=1,
        plan={"questions": [question]},
    )

    queries = await executor._localized_queries_for_cycle(
        state,
        question,
        fallback_query="fallback",
    )

    assert "-site:already-seen.example" in queries["united_states"]
    assert "alternative methods" in queries["united_states"]
    assert "métodos técnicas" in queries["spain"]
    assert "Methoden Techniken" in queries["switzerland"]


@pytest.mark.asyncio
async def test_empty_extraction_regenerates_from_same_serper_documents():
    project_id = uuid.uuid4()
    pipeline_run_id = uuid.uuid4()
    question = {
        "id": str(uuid.uuid4()),
        "question": "Quais métodos funcionam?",
        "priority": 1,
        "search_queries": {
            "united_states": "seed germination methods",
            "spain": "métodos de germinación",
            "switzerland": "Keimungsmethoden",
        },
    }
    executor = object.__new__(PipelineExecutor)
    executor.project = SimpleNamespace(
        id=project_id,
        topic="Guia de germinação",
        niche=None,
        language="pt-BR",
    )
    executor.pipeline_run = SimpleNamespace(id=pipeline_run_id)
    executor.runtime = FakeRuntime()
    executor.db = FakeDb({})
    executor._stage_context = None

    class SearchSpy:
        def __init__(self):
            self.calls = []

        async def search(self, _query, _provider, _api_key, **kwargs):
            market = kwargs["market"]
            self.calls.append(market.code)
            return [
                document(
                    f"https://{market.code}.example/methods",
                    market=market.code,
                )
            ]

    executor.research = SearchSpy()
    extraction_calls = []

    async def no_stage(*_args):
        return None

    async def no_boundary():
        return None

    async def extract(
        _state,
        _question,
        documents,
        *,
        attempt,
        run_id,
        recovery=False,
    ):
        extraction_calls.append(
            {
                "urls": [item.url for item in documents],
                "attempt": attempt,
                "run_id": run_id,
                "recovery": recovery,
            }
        )
        return 1 if recovery else 0

    async def no_facts():
        return []

    async def no_handoff(*_args, **_kwargs):
        return None

    executor._stage = no_stage
    executor._cancellation_boundary = no_boundary
    executor._extract_question = extract
    executor._all_fact_dicts = no_facts
    executor._handoff = no_handoff
    state = PipelineState(
        project_id=project_id,
        pipeline_run_id=pipeline_run_id,
        stage=Stage.researcher,
        plan={"questions": [question], "semantic_keywords": []},
    )

    await executor.researcher(state)

    assert executor.research.calls == ["br", "us", "es"]
    assert len(extraction_calls) == 2
    assert extraction_calls[0]["urls"] == extraction_calls[1]["urls"]
    assert extraction_calls[0]["recovery"] is False
    assert extraction_calls[1]["recovery"] is True
    assert any(
        event["idempotency_key"].startswith(
            "research.extraction_regeneration_started:"
        )
        for event in executor.runtime.events
    )


@pytest.mark.asyncio
async def test_extraction_caps_facts_per_url_and_requires_two_sources():
    project_id = uuid.uuid4()
    pipeline_run_id = uuid.uuid4()
    question_id = uuid.uuid4()
    first = document("https://first.example/article", market="us")
    second = document("https://second.example/article", market="es")
    first.content = (
        "first exact quote one; first exact quote two; first exact quote three "
        + ("x" * (MAX_EXTRACTION_CHARS_PER_DOCUMENT + 500))
        + "TAIL_MARKER"
    )
    second.content = "second exact quote one; second exact quote two"

    candidates = [
        {
            "source_url": first.url,
            "claim_text": f"First claim {index}",
            "exact_quote": f"first exact quote {label}",
            "source_locator": "body",
            "confidence_score": 0.8,
            "conflict_group": None,
        }
        for index, label in enumerate(("one", "two", "three"), start=1)
    ] + [
        {
            "source_url": second.url,
            "claim_text": f"Second claim {index}",
            "exact_quote": f"second exact quote {label}",
            "source_locator": "body",
            "confidence_score": 0.8,
            "conflict_group": None,
        }
        for index, label in enumerate(("one", "two"), start=1)
    ]

    class Runtime:
        execution_manifest = None

        def __init__(self):
            self.prepared = []
            self.events = []
            self.prompt = ""

        async def prepare_input(self, **kwargs):
            self.prepared.append(kwargs)

        async def call(self, *_args, **_kwargs):
            self.prompt = _args[4]
            return {"facts": candidates}

        async def event(self, *_args, **kwargs):
            self.events.append({"args": _args, **kwargs})

    class Ledger:
        def __init__(self):
            self.persisted = []

        async def persist_fact(self, question, source, candidate):
            self.persisted.append((question, source.url, candidate))

    executor = object.__new__(PipelineExecutor)
    executor.project = SimpleNamespace(
        id=project_id,
        topic="topic",
        niche=None,
        language="pt-BR",
    )
    executor.pipeline_run = SimpleNamespace(
        id=pipeline_run_id,
        metadata_json={},
    )
    executor.execution_manifest = {}
    executor.runtime = Runtime()
    executor.skills = SimpleNamespace(prompt_fragment=lambda _role: "")
    executor.ledger = Ledger()
    executor._stage_context = None

    async def no_boundary():
        return None

    executor._cancellation_boundary = no_boundary
    state = PipelineState(
        project_id=project_id,
        pipeline_run_id=pipeline_run_id,
        stage=Stage.researcher,
        plan={"questions": [], "semantic_keywords": []},
    )

    await executor._extract_question(
        state,
        {"id": str(question_id), "question": "Question"},
        [first, second],
        attempt=1,
    )

    persisted_urls = [item[1] for item in executor.ledger.persisted]
    assert persisted_urls.count(first.url) == 2
    assert persisted_urls.count(second.url) == 2
    assert len(executor.runtime.prepared) == 1
    assert "TAIL_MARKER" in executor.runtime.prepared[0]["input_json"]["sources"][0][
        "content"
    ]
    assert "TAIL_MARKER" not in executor.runtime.prompt
    assert executor.runtime.events == []


@pytest.mark.asyncio
async def test_extraction_keeps_usable_facts_when_market_diversity_is_limited():
    project_id = uuid.uuid4()
    pipeline_run_id = uuid.uuid4()
    question_id = uuid.uuid4()
    first = document("https://first.example/article", market="us")
    second = document("https://second.example/article", market="us")
    third = document("https://third.example/article", market="es")
    first.content = "first exact quote"
    second.content = "second exact quote"
    third.content = "third exact quote"

    class Runtime:
        execution_manifest = None

        def __init__(self):
            self.events = []

        async def prepare_input(self, **_kwargs):
            return None

        async def call(self, *_args, **_kwargs):
            return {
                "facts": [
                    {
                        "source_url": first.url,
                        "claim_text": "First claim",
                        "exact_quote": "first exact quote",
                        "source_locator": "body",
                        "confidence_score": 0.8,
                        "conflict_group": None,
                    },
                    {
                        "source_url": second.url,
                        "claim_text": "Second claim",
                        "exact_quote": "second exact quote",
                        "source_locator": "body",
                        "confidence_score": 0.8,
                        "conflict_group": None,
                    },
                ]
            }

        async def event(self, *_args, **kwargs):
            self.events.append({"args": _args, **kwargs})

    class Ledger:
        def __init__(self):
            self.persisted = []

        async def persist_fact(self, *_args):
            self.persisted.append(_args)

    executor = object.__new__(PipelineExecutor)
    executor.project = SimpleNamespace(
        id=project_id,
        topic="topic",
        niche=None,
        language="pt-BR",
    )
    executor.pipeline_run = SimpleNamespace(id=pipeline_run_id, metadata_json={})
    executor.execution_manifest = {}
    executor.runtime = Runtime()
    executor.skills = SimpleNamespace(prompt_fragment=lambda _role: "")
    executor.ledger = Ledger()
    executor._stage_context = None

    async def no_boundary():
        return None

    executor._cancellation_boundary = no_boundary
    state = PipelineState(
        project_id=project_id,
        pipeline_run_id=pipeline_run_id,
        stage=Stage.researcher,
        plan={"questions": [], "semantic_keywords": []},
    )

    await executor._extract_question(
        state,
        {"id": str(question_id), "question": "Question"},
        [first, second, third],
        attempt=1,
    )

    assert len(executor.ledger.persisted) == 2
    assert executor.runtime.events[-1]["idempotency_key"].startswith(
        "research.question_market_diversity:"
    )
    assert executor.runtime.events[-1]["args"][4]["pipeline_continues"] is True
