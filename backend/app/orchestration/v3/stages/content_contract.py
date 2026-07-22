from app.orchestration.v3.state import V3PipelineState
from app.services.editorial_v3.knowledge_contract import (
    KnowledgeContractBuilder,
    KnowledgeContractInput,
)


class ContentContractStage:
    """Pure first V3 stage; it performs no external calls."""

    def __init__(self, project):
        self.project = project
        self.builder = KnowledgeContractBuilder()

    async def __call__(self, state: V3PipelineState) -> V3PipelineState:
        contract = self.builder.build(KnowledgeContractInput.from_project(self.project))
        state.contract = contract.model_dump(mode="json")
        return state
