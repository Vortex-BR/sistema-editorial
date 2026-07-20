import uuid
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.api.routes import dashboard, list_projects
from app.db.models import PipelineRunStatus, ProjectStatus


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


def project(status: ProjectStatus):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=uuid.uuid4(),
        name="Cancellation semantics",
        topic="Separate editorial state from run state",
        search_intent="informational",
        audience="operators",
        language="pt-BR",
        niche=None,
        content_type="article",
        status=status,
        current_stage="completed",
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_project_filters_keep_project_and_last_run_status_independent():
    approved_project = project(ProjectStatus.completed)

    class Db:
        statement = None

        async def execute(self, statement):
            self.statement = statement
            return Rows([(approved_project, PipelineRunStatus.cancelled)])

    db = Db()
    result = await list_projects(
        project_status=ProjectStatus.completed,
        last_run_status=PipelineRunStatus.cancelled,
        db=db,
    )

    assert result[0].status == ProjectStatus.completed.value
    assert result[0].last_run_status == PipelineRunStatus.cancelled.value
    parameters = db.statement.compile().params.values()
    assert ProjectStatus.completed in parameters
    assert PipelineRunStatus.cancelled in parameters


@pytest.mark.asyncio
async def test_dashboard_counts_blocked_cancelled_and_failed_runs_separately():
    approved_project = project(ProjectStatus.completed)

    class Db:
        def __init__(self):
            self.results = iter((2, 1, 1, 2, 1, 12, 4, Decimal("2.50")))

        async def scalar(self, _statement):
            return next(self.results)

        async def execute(self, _statement):
            return Rows([(approved_project, PipelineRunStatus.cancelled)])

    result = await dashboard(db=Db())

    assert result["stats"]["failed_runs"] == 1
    assert result["stats"]["blocked_runs"] == 2
    assert result["stats"]["cancelled_runs"] == 1
    recent = result["recent_projects"][0]
    assert recent.status == ProjectStatus.completed.value
    assert recent.last_run_status == PipelineRunStatus.cancelled.value
