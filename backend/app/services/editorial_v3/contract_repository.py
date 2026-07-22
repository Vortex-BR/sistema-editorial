"""Persistence for validated V3 knowledge contracts."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ContentKnowledgeContractRecord,
    KnowledgeEdgeRecord,
    KnowledgeNodeRecord,
)
from app.schemas.editorial_v3 import ContentKnowledgeContract
from app.services.editorial_v3.knowledge_contract import (
    KnowledgeContractBuilder,
    KnowledgeContractInput,
)


@dataclass(frozen=True)
class MaterializedKnowledgeContract:
    row: ContentKnowledgeContractRecord
    contract: ContentKnowledgeContract
    created: bool


class KnowledgeContractRepository:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.builder = KnowledgeContractBuilder()

    def _build(self, project) -> tuple[ContentKnowledgeContract, str]:
        contract = self.builder.build(KnowledgeContractInput.from_project(project))
        return contract, self.builder.checksum(contract)

    async def find_existing(self, project) -> MaterializedKnowledgeContract | None:
        contract, checksum = self._build(project)
        existing = await self.db.scalar(
            select(ContentKnowledgeContractRecord).where(
                ContentKnowledgeContractRecord.project_id == project.id,
                ContentKnowledgeContractRecord.checksum == checksum,
            )
        )
        if existing is None:
            return None
        return MaterializedKnowledgeContract(existing, contract, False)

    async def materialize(
        self,
        project,
        *,
        pipeline_run_id: UUID | None = None,
    ) -> MaterializedKnowledgeContract:
        contract, checksum = self._build(project)
        existing = await self.db.scalar(
            select(ContentKnowledgeContractRecord).where(
                ContentKnowledgeContractRecord.project_id == project.id,
                ContentKnowledgeContractRecord.checksum == checksum,
            )
        )
        if existing is not None:
            if existing.status not in {"validated", "active"}:
                await self.db.execute(
                    update(ContentKnowledgeContractRecord)
                    .where(
                        ContentKnowledgeContractRecord.project_id == project.id,
                        ContentKnowledgeContractRecord.id != existing.id,
                        ContentKnowledgeContractRecord.status.in_(
                            ["validated", "active"]
                        ),
                    )
                    .values(status="superseded")
                )
                existing.status = "validated"
                if pipeline_run_id is not None:
                    existing.pipeline_run_id = pipeline_run_id
                await self.db.flush()
            return MaterializedKnowledgeContract(existing, contract, False)

        latest_version = await self.db.scalar(
            select(
                func.coalesce(func.max(ContentKnowledgeContractRecord.version), 0)
            ).where(ContentKnowledgeContractRecord.project_id == project.id)
        )
        await self.db.execute(
            update(ContentKnowledgeContractRecord)
            .where(
                ContentKnowledgeContractRecord.project_id == project.id,
                ContentKnowledgeContractRecord.status.in_(["validated", "active"]),
            )
            .values(status="superseded")
        )
        row = ContentKnowledgeContractRecord(
            project_id=project.id,
            pipeline_run_id=pipeline_run_id,
            contract_version=contract.contract_version,
            content_type=contract.content_type.value,
            topic=contract.topic,
            reader_start_state=contract.reader_start_state,
            reader_final_state=contract.reader_final_state,
            article_promise=contract.article_promise,
            scope_limit=contract.scope_limit,
            contract_json=contract.model_dump(mode="json"),
            status="validated",
            checksum=checksum,
            version=int(latest_version or 0) + 1,
            producer=str(contract.metadata.get("builder") or "deterministic"),
        )
        self.db.add(row)
        await self.db.flush()

        for node in contract.nodes:
            self.db.add(
                KnowledgeNodeRecord(
                    contract_id=row.id,
                    node_key=node.node_id,
                    sequence=node.sequence,
                    node_type=node.kind.value,
                    title_function=node.title_function,
                    editorial_goal=node.editorial_goal,
                    reader_state_before=node.reader_state_before,
                    reader_state_after=node.reader_state_after,
                    central_question=node.central_question,
                    depends_on=node.depends_on,
                    required_knowledge=node.required_knowledge,
                    required_decisions=node.required_decisions,
                    required_evidence_roles=[
                        role.value for role in node.required_evidence_roles
                    ],
                    completion_criteria=node.completion_criteria,
                    branches=node.branches,
                    convergence_node_key=node.convergence_node_id,
                    metadata_json=node.metadata,
                )
            )
        for edge in contract.edges:
            self.db.add(
                KnowledgeEdgeRecord(
                    contract_id=row.id,
                    from_node_key=edge.from_node_id,
                    to_node_key=edge.to_node_id,
                    relation=edge.relation.value,
                    rationale=edge.rationale,
                    metadata_json={},
                )
            )
        await self.db.flush()
        return MaterializedKnowledgeContract(row, contract, True)
