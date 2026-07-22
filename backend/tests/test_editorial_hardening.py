from datetime import datetime, timedelta, timezone
import uuid

import pytest

from app.orchestration.executor import PipelineExecutor
from app.services.agent_runtime import AgentRuntime
from app.services.research_engine import SearchDocument


def document(
    url: str,
    *,
    reliability: float,
    published_at: datetime | None = None,
) -> SearchDocument:
    return SearchDocument(
        url=url,
        title="Fonte editorial",
        content="Conteúdo verificável para teste.",
        publisher="Publisher",
        source_type="scientific",
        reliability_score=reliability,
        accessed_at=datetime.now(timezone.utc),
        published_at=published_at,
    )


def test_source_policy_rejects_prohibited_and_stale_then_prioritizes_preferred():
    executor = object.__new__(PipelineExecutor)
    executor._source_policy_payload = lambda: {
        "preferred_sources": ["preferred.example"],
        "prohibited_sources": ["blocked.example"],
        "maximum_source_age_days": 365,
    }
    documents = [
        document("https://blocked.example/report", reliability=0.99),
        document(
            "https://stale.example/report",
            reliability=0.98,
            published_at=datetime.now(timezone.utc) - timedelta(days=500),
        ),
        document("https://other.example/report", reliability=0.95),
        document("https://preferred.example/report", reliability=0.70),
    ]

    accepted, stats = executor._apply_source_policy(documents)

    assert [item.url for item in accepted] == [
        "https://preferred.example/report",
        "https://other.example/report",
    ]
    assert stats == {
        "rejected_count": 2,
        "prohibited_count": 1,
        "stale_count": 1,
    }


def test_source_policy_handles_naive_publication_datetime_as_utc():
    executor = object.__new__(PipelineExecutor)
    executor._source_policy_payload = lambda: {
        "preferred_sources": [],
        "prohibited_sources": [],
        "maximum_source_age_days": 30,
    }
    recent_naive = (datetime.now(timezone.utc) - timedelta(days=5)).replace(tzinfo=None)

    accepted, stats = executor._apply_source_policy(
        [
            document(
                "https://example.org/report", reliability=0.8, published_at=recent_naive
            )
        ]
    )

    assert len(accepted) == 1
    assert stats["stale_count"] == 0


def test_editor_revisions_replace_only_known_blocks_and_preserve_positions():
    first_id = uuid.uuid4()
    second_id = uuid.uuid4()
    prior = {
        "title": "Um título editorial completo",
        "title_evidence": [],
        "blocks": [
            {
                "block_id": str(first_id),
                "type": "h2",
                "position": 0,
                "sentences": [
                    {
                        "text": "Preparação antes da decisão",
                        "is_factual": False,
                        "evidence": [],
                    }
                ],
            },
            {
                "block_id": str(second_id),
                "type": "paragraph",
                "position": 1,
                "sentences": [
                    {
                        "text": "Uma transição editorial orienta a leitura.",
                        "is_factual": False,
                        "evidence": [],
                    }
                ],
            },
        ],
        "unsupported_claims": [],
    }
    revision = [
        {
            "block_id": str(second_id),
            "type": "paragraph",
            "position": 99,
            "sentences": [
                {
                    "text": "A revisão melhora a progressão sem mudar o restante.",
                    "is_factual": False,
                    "evidence": [],
                }
            ],
        }
    ]

    merged = PipelineExecutor._apply_editor_revisions(prior, revision)

    assert merged["blocks"][0] == prior["blocks"][0]
    assert merged["blocks"][1]["position"] == 1
    assert merged["blocks"][1]["sentences"][0]["text"].startswith("A revisão")


def test_editor_revisions_reject_unknown_block_ids():
    prior = {
        "title": "Um título editorial completo",
        "title_evidence": [],
        "blocks": [
            {
                "block_id": str(uuid.uuid4()),
                "type": "paragraph",
                "position": 0,
                "sentences": [
                    {
                        "text": "Uma abertura editorial prepara a leitura.",
                        "is_factual": False,
                        "evidence": [],
                    }
                ],
            }
        ],
        "unsupported_claims": [],
    }

    with pytest.raises(ValueError, match="unknown blocks"):
        PipelineExecutor._apply_editor_revisions(
            prior,
            [
                {
                    "block_id": str(uuid.uuid4()),
                    "type": "paragraph",
                    "position": 0,
                    "sentences": [
                        {
                            "text": "Trecho sem identidade editorial conhecida.",
                            "is_factual": False,
                            "evidence": [],
                        }
                    ],
                }
            ],
        )


def test_token_cost_uses_fallback_specific_rates():
    parameters = {
        "input_cost_per_million": 1.0,
        "output_cost_per_million": 2.0,
        "fallback_input_cost_per_million": 3.0,
        "fallback_output_cost_per_million": 4.0,
    }

    assert AgentRuntime._token_cost(parameters, 1_000_000, 1_000_000) == 3.0
    assert (
        AgentRuntime._token_cost(
            parameters, 1_000_000, 1_000_000, target_kind="fallback"
        )
        == 7.0
    )
