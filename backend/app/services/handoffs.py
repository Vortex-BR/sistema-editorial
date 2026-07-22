import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AgentHandoff, PipelineRun
from app.services.pipeline_control import EventContext, EventService
from app.core.sanitization import sanitize_nul


class HandoffService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def persist(
        self,
        project_id: uuid.UUID,
        pipeline_run: PipelineRun,
        from_role: str,
        to_role: str,
        payload: dict,
        fact_ids: list[str] | None = None,
        confidence_score: float = 1,
        research_cycle: int = 0,
        editor_cycle: int = 0,
        producer_agent_run_id: uuid.UUID | None = None,
        event_context: EventContext | None = None,
    ) -> AgentHandoff:
        from_role = sanitize_nul(from_role)
        to_role = sanitize_nul(to_role)
        payload = sanitize_nul(payload)
        fact_ids = sanitize_nul(fact_ids or [])
        locked_run = await self.db.scalar(
            select(PipelineRun)
            .where(PipelineRun.id == pipeline_run.id)
            .with_for_update()
        )
        if locked_run is None:
            raise ValueError("Pipeline run not found")
        key = self.idempotency_key(
            from_role,
            to_role,
            locked_run.attempt,
            research_cycle,
            editor_cycle,
            producer_agent_run_id,
        )
        existing = await self.db.scalar(
            select(AgentHandoff).where(
                AgentHandoff.pipeline_run_id == pipeline_run.id,
                AgentHandoff.idempotency_key == key,
            )
        )
        if existing:
            await self._record_event(locked_run, existing, event_context)
            return existing
        locked_run.handoff_sequence += 1
        handoff = AgentHandoff(
            project_id=project_id,
            pipeline_run_id=pipeline_run.id,
            idempotency_key=key,
            sequence=locked_run.handoff_sequence,
            producer_agent_run_id=producer_agent_run_id,
            from_role=from_role,
            to_role=to_role,
            payload=payload,
            fact_ids=fact_ids,
            confidence_score=confidence_score,
        )
        self.db.add(handoff)
        await self.db.flush()
        await self._record_event(locked_run, handoff, event_context)
        return handoff

    async def _record_event(
        self,
        run: PipelineRun,
        handoff: AgentHandoff,
        event_context: EventContext | None,
    ) -> None:
        context = event_context
        if context and handoff.producer_agent_run_id:
            context = context.with_agent(handoff.producer_agent_run_id)
        await EventService(self.db).append(
            handoff.project_id,
            run.id,
            "handoff.created",
            handoff.from_role,
            {
                "handoff_id": str(handoff.id),
                "sequence": handoff.sequence,
                "from_role": handoff.from_role,
                "to_role": handoff.to_role,
            },
            idempotency_key=f"handoff.created:{handoff.id}",
            context=context,
        )

    @staticmethod
    def idempotency_key(
        from_role: str,
        to_role: str,
        run_attempt: int,
        research_cycle: int,
        editor_cycle: int,
        producer_agent_run_id: uuid.UUID | None,
    ) -> str:
        producer = str(producer_agent_run_id) if producer_agent_run_id else "aggregate"
        return (
            f"{from_role}:{to_role}:research-cycle-{research_cycle}:"
            f"editor-cycle-{editor_cycle}:run-attempt-{run_attempt}:producer-{producer}"
        )
