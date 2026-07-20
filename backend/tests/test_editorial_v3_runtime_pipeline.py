from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.orchestration.v3.graph import (
    EditorialIntelligenceV3Graph,
    V3PipelineNodes,
)
from app.orchestration.v3.state import V3PipelineState, V3Stage
from app.schemas.editorial_v3 import (
    ExternalReference,
    ResearchSourceSignals,
    SourceOwnershipType,
    SourcePageType,
)
from app.schemas.editorial_v3_runtime import (
    MethodInventoryItem,
    StructuredDocumentSection,
    StructuredSourceDocument,
    V3DevelopmentReview,
    V3FactCheckReview,
    V3LanguageReview,
    V3WriterOutput,
)
from app.services.editorial_v3.document_parser import SourceDocumentParser
from app.services.editorial_v3.external_reference_validator import (
    ExternalReferenceValidator,
)
from app.services.editorial_v3.knowledge_contract import (
    KnowledgeContractBuilder,
    KnowledgeContractInput,
)
from app.services.editorial_v3.procedural_quality import ProceduralQualityService
from app.services.editorial_v3.research_planner import V3ResearchPlanningService
from app.services.editorial_v3.source_policy import ResearchSourcePolicyService
from app.services.execution_manifest import v3_prompt_contract_manifest
from app.services.research_engine import SearchDocument


def _search_document(url: str) -> SearchDocument:
    return SearchDocument(
        url=url,
        title="Fonte de teste",
        content="Conteúdo de teste " * 30,
        publisher="Publisher",
        source_type="practical",
        reliability_score=0.8,
        accessed_at=datetime.now(timezone.utc),
    )


def test_document_parser_rejects_store_pages_and_quarantines_store_blog():
    parser = SourceDocumentParser()
    product = parser.parse_html(
        """
        <html><head><title>Comprar agora</title>
        <script type="application/ld+json">{"@type":"Product","offers":{"price":"99"}}</script>
        </head><body><main><h1>Produto</h1>
        <p>Esta página comercial possui descrição suficiente para ser processada pelo leitor estruturado.</p>
        <button>Adicionar ao carrinho</button></main></body></html>
        """,
        source=_search_document("https://shop.example.com/products/item"),
    )
    assert product.assessment.usage_policy.value == "rejected"
    assert product.assessment.eligible_for_primary_evidence is False

    blog = parser.parse_html(
        """
        <html><head><title>Guia da loja</title><meta name="generator" content="WooCommerce"></head>
        <body><article><h1>Guia</h1>
        <p>Este artigo descreve um método com detalhes suficientes para comparação, mas pertence a uma loja.</p>
        <ol><li>Prepare os materiais.</li><li>Observe o processo.</li><li>Registre o resultado.</li></ol>
        </article><a href="/shop/">Loja</a></body></html>
        """,
        source=_search_document("https://shop.example.com/blog/guia"),
    )
    assert blog.assessment.usage_policy.value == "comparison_only"
    assert blog.assessment.eligible_for_external_reference is False
    assert blog.assessment.absolute_claim_support_allowed is False


def _structured_document(url: str, *, commercial: bool) -> StructuredSourceDocument:
    policy = ResearchSourcePolicyService()
    signals = ResearchSourceSignals(
        url=url,
        title="Método direto: guia técnico completo",
        ownership_type=(
            SourceOwnershipType.ecommerce
            if commercial
            else SourceOwnershipType.academic
        ),
        page_type=(
            SourcePageType.ecommerce_blog_article
            if commercial
            else SourcePageType.technical_guide
        ),
        is_ecommerce_domain=commercial,
        author_present=True,
        references_present=True,
        institutional_affiliation=not commercial,
        content_depth_score=0.9,
        procedural_depth_score=0.95,
        scientific_support_score=0.8,
        topic_relevance_score=0.95,
    )
    assessment = policy.assess(signals)
    text = (
        "Método direto. Passo 1 prepare os materiais. Passo 2 execute o procedimento. "
        "Passo 3 observe o sinal esperado. Materiais e procedimento detalhado. "
    ) * 20
    return StructuredSourceDocument(
        document_id=uuid4(),
        url=url,
        canonical_url=url,
        title="Método direto: guia técnico completo",
        author="Autor",
        publisher="Universidade" if not commercial else "Loja",
        accessed_at=datetime.now(timezone.utc),
        language="pt-BR",
        document_type=signals.page_type,
        content_hash="a" * 64 if not commercial else "b" * 64,
        sections=[
            StructuredDocumentSection(
                section_id="sec_123456789abc" if not commercial else "sec_abcdef123456",
                heading_path=["Método direto", "Passo a passo"],
                paragraphs=[text],
                ordered_steps=[
                    "Prepare os materiais necessários",
                    "Execute o método com cuidado",
                    "Observe o resultado esperado",
                ],
                source_locator="section:1",
                character_count=len(text),
            )
        ],
        assessment=assessment,
        source_signals=signals,
        plain_text=text,
    )


def test_external_reference_uses_independent_technical_source_not_store_blog():
    method = MethodInventoryItem(
        method_id="metodo_direto",
        name="Método direto",
        aliases=["germinação direta"],
        distinguishing_feature="O processo começa diretamente no meio final.",
        supporting_claim_keys=["claim_001"],
    )
    commercial = _structured_document(
        "https://loja.example/blog/metodo-direto", commercial=True
    )
    academic = _structured_document(
        "https://universidade.edu/guia/metodo-direto", commercial=False
    )

    selected = ExternalReferenceValidator().select([method], [commercial, academic])

    assert selected["metodo_direto"].status == "approved"
    assert str(selected["metodo_direto"].url).startswith("https://universidade.edu/")
    assert selected["metodo_direto"].is_ecommerce_domain is False


def test_research_plan_is_derived_from_the_ordered_knowledge_graph():
    contract = KnowledgeContractBuilder().build(
        KnowledgeContractInput(
            topic="processo de teste",
            reader_start_state="Leitor que ainda não compreende o processo de teste.",
            reader_final_state="Leitor capaz de reconhecer o resultado final do processo.",
            article_promise="Explicar fundamentos, métodos, escolha, execução e resultado final observável.",
            scope_limit="O conteúdo termina no resultado final e não avança para outra fase.",
        )
    )
    plan = V3ResearchPlanningService().build(contract, max_tasks=20)

    assert [task.knowledge_node_id for task in plan.tasks] == [
        node.node_id for node in contract.nodes
    ]
    assert all(task.queries for task in plan.tasks)
    assert all(
        "ecommerce" not in role
        for task in plan.tasks
        for role in task.required_source_roles
    )
    assert any(task.critical for task in plan.tasks)


@pytest.mark.asyncio
async def test_v3_graph_runs_transition_hook_after_every_completed_stage():
    calls: list[str] = []

    async def node(state):
        if state.stage == V3Stage.content_contract:
            state.contract = {"ok": True}
        elif state.stage == V3Stage.knowledge_architect:
            state.contract = {"ok": True}
        elif state.stage == V3Stage.knowledge_gate:
            state.contract_validation = {"status": "blocked"}
        return state

    async def hook(stage, state):
        calls.append(stage)

    required_nodes = {
        name: node
        for name in V3PipelineNodes.__annotations__
        if name not in {
            "source_coverage_gate",
            "targeted_source_recovery",
            "intelligence_planner",
            "evidence_graph_builder",
            "intelligence_gate",
        }
    }
    nodes = V3PipelineNodes(**required_nodes)
    state = await EditorialIntelligenceV3Graph(nodes, after_transition=hook).run(
        V3PipelineState(project_id=uuid4())
    )

    assert state.stage == V3Stage.blocked
    assert calls == ["content_contract", "knowledge_architect", "knowledge_gate"]


def test_procedural_quality_blocks_short_template_text():
    draft = V3WriterOutput.model_construct(
        title="Guia procedural de teste suficientemente descritivo",
        blocks=[
            SimpleNamespace(
                type="paragraph",
                section_id="subject_foundation",
                method_id=None,
                sentences=[
                    SimpleNamespace(
                        text="Neste guia, é importante destacar um processo simples.",
                        is_factual=False,
                        evidence=[],
                    )
                ],
            )
        ],
        covered_section_ids=["subject_foundation"],
        covered_method_ids=["metodo_a"],
    )
    method = SimpleNamespace(
        method_id="metodo_a",
        external_reference=ExternalReference.model_construct(status="approved"),
        steps=[
            SimpleNamespace(
                expected_observations=["sinal"], completion_condition="concluído"
            )
        ],
    )
    result = ProceduralQualityService().evaluate(
        contract=SimpleNamespace(nodes=[SimpleNamespace(node_id="subject_foundation")]),
        methods=[method],
        sections=[SimpleNamespace(section_id="subject_foundation")],
        matrix=SimpleNamespace(method_ids=["metodo_a"], rules=[1, 2]),
        draft=draft,
        development=V3DevelopmentReview.model_construct(
            status="passed",
            promise_fulfilled=True,
            procedural_completeness_score=0.9,
            decision_usefulness_score=0.9,
        ),
        fact_check=V3FactCheckReview.model_construct(status="passed"),
        language=V3LanguageReview.model_construct(
            status="passed",
            naturalness_score=0.9,
            rhythm_score=0.9,
            template_language_score=0.1,
        ),
        accepted_source_count=5,
        independent_source_count=4,
    )

    assert result.status == "blocked"
    assert result.overall_score <= 0.59
    assert any("curto demais" in blocker for blocker in result.critical_blockers)


def test_v3_execution_manifest_pins_every_paid_output_contract():
    manifest = v3_prompt_contract_manifest()
    assert {
        "claim_extraction",
        "method_inventory",
        "knowledge_synthesis",
        "writer",
        "development_editor",
        "fact_checker",
        "language_editor",
        "block_revision",
        "quality_gate",
    }.issubset(manifest)
    assert all(item["contract_checksum"] for item in manifest.values())


def test_independent_research_vocabulary_does_not_create_scientific_authority():
    parser = SourceDocumentParser()
    document = parser.parse_html(
        """
        <html><head><title>Análise editorial independente</title><meta name="author" content="Autor"></head>
        <body><article><h1>Análise</h1>
        <p>Este texto comenta metodologia, resultados e discussão publicados em outros lugares, sem se apresentar como periódico acadêmico ou instituição científica.</p>
        <p>A análise é extensa o suficiente para contextualizar o tema, mas não contém prova de revisão por pares nem afiliação institucional.</p>
        </article></body></html>
        """,
        source=_search_document("https://editorial-independente.example/analise"),
    )

    assert document.document_type == SourcePageType.independent_article
    assert document.assessment.source_role.value == "independent_editorial"
    assert document.assessment.usage_policy.value == "corroborating_evidence"
    assert document.assessment.eligible_for_primary_evidence is False
    assert document.assessment.absolute_claim_support_allowed is False


def test_independent_procedural_guide_is_useful_but_not_self_authoritative():
    parser = SourceDocumentParser()
    procedural_paragraph = (
        "<p>Prepare os materiais, execute cada passo, observe o sinal esperado, "
        "reconheça o erro comum e confirme a condição antes de transferir para a etapa seguinte.</p>"
    )
    document = parser.parse_html(
        """
        <html><head><title>Guia técnico independente</title><meta name="author" content="Especialista"></head>
        <body><article><h1>Como executar</h1><h2>Materiais e passo a passo</h2>
        """
        + procedural_paragraph * 35
        + """
        <ol><li>Prepare os materiais necessários.</li><li>Execute o procedimento conforme a sequência.</li><li>Observe o resultado e os sinais de problema.</li></ol>
        </article></body></html>
        """,
        source=_search_document(
            "https://especialista-independente.example/guia-pratico"
        ),
    )

    assert document.document_type == SourcePageType.technical_guide
    assert document.assessment.source_role.value == "technical_procedural"
    assert document.assessment.usage_policy.value == "corroborating_evidence"
    assert document.assessment.eligible_for_primary_evidence is False
    assert document.assessment.requires_independent_corroboration is True
    assert document.assessment.absolute_claim_support_allowed is False


@pytest.mark.asyncio
async def test_source_reader_rejects_redirect_to_private_network_before_following_it():
    class RedirectResponse:
        status_code = 302
        headers = {"location": "http://127.0.0.1/private"}
        url = "https://public.example/source"

    class RedirectClient:
        def __init__(self):
            self.calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, _url):
            self.calls += 1
            return RedirectResponse()

    client = RedirectClient()
    parser = SourceDocumentParser(client_factory=lambda **_kwargs: client)
    result = await parser.read(_search_document("https://public.example/source"))

    assert client.calls == 1
    assert "live_fetch_unavailable_used_search_snapshot" in result.warnings
    assert "unstructured_snapshot_fallback" in result.warnings


def test_v3_skill_registry_loads_the_procedural_writer_definition():
    from app.services.skill_registry import SkillRegistry

    skills = SkillRegistry("skills/v3").load_defaults()

    assert "v3.procedural-writer" in skills
    assert "writer" in skills["v3.procedural-writer"].applies_to_agent


@pytest.mark.asyncio
async def test_source_reader_streams_and_truncates_oversized_response_body():
    class StreamResponse:
        status_code = 200
        headers = {"content-type": "text/html", "content-length": "999999"}
        url = "https://public.example/large-source"

        def raise_for_status(self):
            return None

        async def aiter_bytes(self):
            yield b"<html><body><main><h1>Guia tecnico</h1><p>"
            yield b"conteudo tecnico detalhado " * 30
            yield b"</p></main></body></html>"

    class StreamContext:
        async def __aenter__(self):
            return StreamResponse()

        async def __aexit__(self, *_args):
            return False

    class StreamClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def stream(self, method, url):
            assert method == "GET"
            assert url == "https://public.example/large-source"
            return StreamContext()

    parser = SourceDocumentParser(
        max_bytes=240,
        client_factory=lambda **_kwargs: StreamClient(),
    )
    result = await parser.read(
        _search_document("https://public.example/large-source")
    )

    assert "source_content_length_exceeded" in result.warnings
    assert "source_body_truncated" in result.warnings
    assert len(result.plain_text) >= 100
