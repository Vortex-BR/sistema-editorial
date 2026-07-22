from pydantic import ValidationError

from app.orchestration.v3.state import V3PipelineState
from app.schemas.editorial_v3 import ContentKnowledgeContract


class KnowledgeContractGateStage:
    """Re-validate persisted JSON before research is allowed to start."""

    async def __call__(self, state: V3PipelineState) -> V3PipelineState:
        try:
            contract = ContentKnowledgeContract.model_validate(state.contract)
        except ValidationError as exc:
            state.contract_validation = {
                "status": "blocked",
                "errors": exc.errors(include_url=False),
            }
            return state
        state.contract = contract.model_dump(mode="json")
        state.contract_validation = {
            "status": "passed",
            "node_count": len(contract.nodes),
            "edge_count": len(contract.edges),
        }
        return state
