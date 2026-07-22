import uuid
import pytest
from pydantic import ValidationError
from app.schemas.agents import (
    DraftBlock,
    DraftSentence,
    ResearchAuditOutput,
    WriterOutput,
    WriterRevisionOutput,
)


def test_factual_sentence_requires_evidence():
    with pytest.raises(ValidationError):
        DraftSentence(
            text="A temperatura altera a germinação.", is_factual=True, evidence=[]
        )


def test_non_factual_sentence_accepts_empty_evidence():
    sentence = DraftSentence(
        text="Como preparar as sementes", is_factual=False, evidence=[]
    )

    assert sentence.evidence == []


def test_non_factual_sentence_rejects_fact_evidence():
    with pytest.raises(ValidationError):
        DraftSentence(
            text="Uma transição editorial conduz a leitura.",
            is_factual=False,
            evidence=[{"fact_id": uuid.uuid4(), "entailment_score": 1}],
        )


def test_editorial_heading_accepts_empty_evidence():
    block = DraftBlock(
        block_id=None,
        type="h2",
        position=0,
        sentences=[
            DraftSentence(
                text="Como preparar",
                is_factual=False,
                evidence=[],
            )
        ],
    )

    assert block.sentences[0].evidence == []


def test_gatekeeper_cannot_approve_low_diversity():
    with pytest.raises(ValidationError):
        ResearchAuditOutput(
            decision="approved",
            coverage_by_question={"q": 1},
            source_diversity_score=0.4,
            approved_fact_ids=[uuid.uuid4()],
        )


def test_gatekeeper_fact_cannot_be_approved_and_rejected():
    fact_id = uuid.uuid4()

    with pytest.raises(ValidationError):
        ResearchAuditOutput(
            decision="insufficient",
            coverage_by_question={"q": 1},
            source_diversity_score=1,
            approved_fact_ids=[fact_id],
            fact_rejections=[{"fact_id": fact_id, "reason_code": "off_topic"}],
        )


def test_gatekeeper_rejection_codes_are_closed():
    with pytest.raises(ValidationError):
        ResearchAuditOutput(
            decision="insufficient",
            coverage_by_question={"q": 0},
            source_diversity_score=0,
            fact_rejections=[{"fact_id": uuid.uuid4(), "reason_code": "arbitrary"}],
        )


def test_writer_output_blocks_unsupported_claims():
    with pytest.raises(ValidationError):
        WriterOutput(
            title="Título editorial completo",
            title_evidence=[{"fact_id": uuid.uuid4(), "entailment_score": 1}],
            blocks=[
                {
                    "block_id": None,
                    "type": "paragraph",
                    "position": 0,
                    "sentences": [
                        {
                            "text": "Texto validado.",
                            "is_factual": False,
                            "evidence": [],
                        }
                    ],
                }
            ],
            unsupported_claims=["Afirmação sem fonte"],
        )


def test_writer_editorial_title_may_be_evidence_free():
    output = WriterOutput(
        title="Título editorial completo",
        title_evidence=[],
        blocks=[
            {
                "block_id": None,
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
        unsupported_claims=[],
    )

    assert output.title_evidence == []


def valid_writer_payload():
    fact_id = uuid.uuid4()
    return {
        "title": "Guia completo para energia solar",
        "title_evidence": [{"fact_id": fact_id, "entailment_score": 1}],
        "blocks": [
            {
                "block_id": uuid.uuid4(),
                "type": "paragraph",
                "position": 0,
                "sentences": [
                    {
                        "text": "A orientação permanece rastreável.",
                        "is_factual": False,
                        "evidence": [],
                    }
                ],
            }
        ],
        "unsupported_claims": [],
    }


def test_writer_strict_contract_rejects_extra_properties():
    payload = valid_writer_payload()
    payload["unexpected"] = True

    with pytest.raises(ValidationError):
        WriterOutput.model_validate(payload)


def test_writer_strict_contract_rejects_missing_fields():
    payload = valid_writer_payload()
    del payload["blocks"][0]["sentences"][0]["is_factual"]

    with pytest.raises(ValidationError):
        WriterOutput.model_validate(payload)


def test_writer_strict_contract_rejects_invalid_uuid():
    payload = valid_writer_payload()
    payload["blocks"][0]["block_id"] = "not-a-uuid"

    with pytest.raises(ValidationError):
        WriterOutput.model_validate(payload)


def test_writer_revision_contract_contains_only_blocks_and_empty_unsupported():
    payload = valid_writer_payload()
    revision = WriterRevisionOutput.model_validate(
        {
            "blocks": payload["blocks"],
            "unsupported_claims": [],
        }
    )

    assert revision.blocks[0].block_id == payload["blocks"][0]["block_id"]
