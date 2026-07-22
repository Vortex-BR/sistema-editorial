import asyncio
import os
import re
import signal
import subprocess
import sys
import tempfile
import uuid
from builtins import BaseExceptionGroup
from pathlib import Path

import pytest
from celery import Celery
from redis import Redis
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import PipelineEvent, PipelineRunStatus, Project
from app.services.operational_heartbeat import HEARTBEAT_KEYS
from app.services.pipeline_control import PipelineRunService


pytestmark = pytest.mark.skipif(
    not (
        os.getenv("RUN_CELERY_E2E") == "1"
        and os.getenv("TEST_DATABASE_URL")
        and os.getenv("TEST_REDIS_URL")
    ),
    reason=(
        "RUN_CELERY_E2E=1, TEST_DATABASE_URL and TEST_REDIS_URL are required "
        "for the real worker/Beat test"
    ),
)

PROBE_LABELS = ["success-before", "failure", "success-after-one", "success-after-two"]
FORBIDDEN_LOOP_ERRORS = (
    "Future attached to a different loop",
    "Event loop is closed",
)


def _python_subprocess(environment: dict[str, str]) -> str:
    base_executable = getattr(sys, "_base_executable", sys.executable)
    if os.name == "nt" and base_executable != sys.executable:
        # The Windows venv launcher starts another python.exe and exits before
        # that child necessarily releases inherited log handles. Launch the
        # real interpreter while preserving the venv identity instead.
        environment["__PYVENV_LAUNCHER__"] = sys.executable
        return base_executable
    return sys.executable


def _process_group_options() -> dict[str, bool]:
    return {} if os.name == "nt" else {"start_new_session": True}


def _kill_process_tree(process: subprocess.Popen) -> None:
    if os.name == "nt":
        completed = subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0 and process.poll() is None:
            output = (completed.stdout + completed.stderr).strip()
            raise RuntimeError(
                f"taskkill failed for test process tree {process.pid}: {output}"
            )
        return

    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def _close_process_streams(process: subprocess.Popen) -> list[BaseException]:
    failures = []
    closed_streams = set()
    for name in ("stdin", "stdout", "stderr"):
        stream = getattr(process, name, None)
        if stream is None or id(stream) in closed_streams:
            continue
        closed_streams.add(id(stream))
        try:
            stream.close()
        except BaseException as exc:
            failures.append(exc)
    return failures


def stop_process(
    process: subprocess.Popen | None, *, timeout_seconds: float
) -> None:
    if process is None:
        return

    failures = []
    try:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                _kill_process_tree(process)
                process.wait(timeout=timeout_seconds)
        else:
            process.wait(timeout=timeout_seconds)
    except BaseException as exc:
        failures.append(exc)
    finally:
        failures.extend(_close_process_streams(process))

    if failures:
        raise BaseExceptionGroup(
            f"failed to stop test process {process.pid}", failures
        )


def _raise_cleanup_failures(failures: list[BaseException]) -> None:
    if len(failures) == 1:
        raise failures[0]
    raise BaseExceptionGroup("Celery E2E cleanup failed", failures)


def _raise_body_failure(
    body_failure: tuple[BaseException, object],
    cleanup_failures: list[BaseException],
) -> None:
    failure, traceback = body_failure
    if cleanup_failures:
        raise failure.with_traceback(traceback) from BaseExceptionGroup(
            "cleanup also failed after the Celery E2E body failure",
            cleanup_failures,
        )
    raise failure.with_traceback(traceback)


async def _create_run(sessions, label: str, *, terminal: bool = False):
    async with sessions() as session:
        project = Project(
            name=f"Celery E2E {label}",
            topic="asyncpg event-loop isolation",
            search_intent="informational",
            audience="engineers",
            status="completed" if terminal else "queued",
        )
        session.add(project)
        await session.commit()
        run, _ = await PipelineRunService(session).create(
            project.id, f"celery-e2e-{label}-{uuid.uuid4()}"
        )
        if terminal:
            run.status = PipelineRunStatus.completed
        await session.commit()
        return project.id, run.id


async def _wait_for_event(sessions, run_id, event_type: str, timeout: float = 45):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        async with sessions() as session:
            found = await session.scalar(
                select(PipelineEvent.id).where(
                    PipelineEvent.pipeline_run_id == run_id,
                    PipelineEvent.event_type == event_type,
                )
            )
        if found:
            return
        await asyncio.sleep(0.25)
    raise AssertionError(f"Timed out waiting for {event_type} on run {run_id}")


def _log_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


async def _get_result(result, *, propagate: bool):
    return await asyncio.to_thread(result.get, timeout=45, propagate=propagate)


@pytest.mark.parametrize("pool", ["solo", "prefork"])
@pytest.mark.asyncio
async def test_real_beat_and_worker_survive_sequential_event_loops(pool):
    if pool == "prefork" and not sys.platform.startswith("linux"):
        pytest.skip("Celery prefork E2E is executed only on Linux")

    database_url = os.environ["TEST_DATABASE_URL"]
    redis_url = os.environ["TEST_REDIS_URL"]
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    redis = Redis.from_url(redis_url, decode_responses=True)

    project_ids = []
    results = []
    redis_keys_before = None

    backend = Path(__file__).parents[1]
    environment = {
        **os.environ,
        "DATABASE_URL": database_url,
        "REDIS_URL": redis_url,
        "PIPELINE_DISPATCH_INTERVAL_SECONDS": "1",
        "PYTHONUNBUFFERED": "1",
    }
    python_executable = _python_subprocess(environment)
    client = Celery(f"celery-e2e-{pool}", broker=redis_url, backend=redis_url)

    async def cleanup():
        failures = []
        for result in results:
            try:
                result.forget()
            except BaseException as exc:
                failures.append(exc)
        try:
            redis.delete("celery", *HEARTBEAT_KEYS.values())
            if redis_keys_before is not None:
                created_keys = set(redis.scan_iter()) - redis_keys_before
                if created_keys:
                    redis.delete(*created_keys)
        except BaseException as exc:
            failures.append(exc)
        if project_ids:
            try:
                async with sessions() as session:
                    await session.execute(
                        delete(Project).where(Project.id.in_(project_ids))
                    )
                    await session.commit()
            except BaseException as exc:
                failures.append(exc)
        try:
            await engine.dispose()
        except BaseException as exc:
            failures.append(exc)
        try:
            redis.close()
        except BaseException as exc:
            failures.append(exc)
        try:
            client.close()
        except BaseException as exc:
            failures.append(exc)
        if failures:
            _raise_cleanup_failures(failures)

    body_failure = None
    cleanup_failures = []
    temporary_path = None
    try:
        with tempfile.TemporaryDirectory(prefix=f"celery-e2e-{pool}-") as temporary:
            temporary_path = Path(temporary)
            schedule = str(temporary_path / "celerybeat-schedule")
            worker_log_path = temporary_path / "worker.log"
            beat_log_path = temporary_path / "beat.log"
            worker_log = None
            beat_log = None
            worker = None
            beat = None
            try:
                worker_log = worker_log_path.open("w", encoding="utf-8")
                beat_log = beat_log_path.open("w", encoding="utf-8")
                redis.ping()
                redis.delete("celery", *HEARTBEAT_KEYS.values())
                redis_keys_before = set(redis.scan_iter())

                first_project, first_run = await _create_run(
                    sessions, f"{pool}-first"
                )
                probe_project, probe_run = await _create_run(
                    sessions, f"{pool}-probe", terminal=True
                )
                project_ids.extend([first_project, probe_project])

                worker = subprocess.Popen(
                    [
                        python_executable,
                        "-m",
                        "celery",
                        "-A",
                        "app.workers.celery_app",
                        "worker",
                        "--loglevel=INFO",
                        f"--pool={pool}",
                        "--concurrency=1",
                        "--include=tests.celery_probe_tasks",
                        f"--hostname=e2e-{pool}@%h",
                    ],
                    cwd=backend,
                    env=environment,
                    stdout=worker_log,
                    stderr=subprocess.STDOUT,
                    **_process_group_options(),
                )
                beat = subprocess.Popen(
                    [
                        python_executable,
                        "-m",
                        "celery",
                        "-A",
                        "app.workers.celery_app",
                        "beat",
                        "--loglevel=INFO",
                        f"--schedule={schedule}",
                    ],
                    cwd=backend,
                    env=environment,
                    stdout=beat_log,
                    stderr=subprocess.STDOUT,
                    **_process_group_options(),
                )

                await _wait_for_event(sessions, first_run, "worker.lease_acquired")
                second_project, second_run = await _create_run(
                    sessions, f"{pool}-second"
                )
                project_ids.append(second_project)
                await _wait_for_event(sessions, second_run, "worker.lease_acquired")

                for label in PROBE_LABELS:
                    result = client.send_task(
                        "test.celery.loop-probe",
                        args=[
                            str(probe_project),
                            str(probe_run),
                            label,
                            label == "failure",
                        ],
                    )
                    results.append(result)
                    value = await _get_result(result, propagate=False)
                    if label == "failure":
                        assert result.status == "FAILURE"
                        assert "intentional Celery loop probe failure" in str(value)
                    else:
                        assert result.status == "SUCCESS"
                        assert value["label"] == label

                async with sessions() as session:
                    probe_events = (
                        await session.scalars(
                            select(PipelineEvent)
                            .where(
                                PipelineEvent.pipeline_run_id == probe_run,
                                PipelineEvent.event_type == "test.celery.loop_probe",
                            )
                            .order_by(PipelineEvent.sequence)
                        )
                    ).all()
                    assert [
                        event.payload["label"] for event in probe_events
                    ] == PROBE_LABELS
                    worker_pids = {
                        int(event.payload["worker_pid"]) for event in probe_events
                    }
                    assert len(worker_pids) == 1
                    connection_identities = {
                        (
                            int(event.payload["pg_backend_pid"]),
                            event.payload["backend_start"],
                        )
                        for event in probe_events
                    }
                    assert len(connection_identities) == len(PROBE_LABELS)

                    for run_id in (first_run, second_run):
                        counts = dict(
                            (
                                await session.execute(
                                    select(
                                        PipelineEvent.event_type,
                                        func.count(PipelineEvent.id),
                                    )
                                    .where(
                                        PipelineEvent.pipeline_run_id == run_id,
                                        PipelineEvent.event_type.in_(
                                            [
                                                "dispatch.claimed",
                                                "dispatch.sent",
                                                "worker.lease_acquired",
                                            ]
                                        ),
                                    )
                                    .group_by(PipelineEvent.event_type)
                                )
                            ).all()
                        )
                        assert counts == {
                            "dispatch.claimed": 1,
                            "dispatch.sent": 1,
                            "worker.lease_acquired": 1,
                        }

                await asyncio.sleep(1)
                worker_log.flush()
                beat_log.flush()
                worker_output = _log_text(worker_log_path)
                beat_output = _log_text(beat_log_path)
                assert len(
                    re.findall(
                        r"Task pipeline\.resume-due\[[^]]+\] succeeded",
                        worker_output,
                    )
                ) >= 2
                assert "intentional Celery loop probe failure" in worker_output
                assert len(
                    re.findall(
                        r"Task test\.celery\.loop-probe\[[^]]+\] succeeded",
                        worker_output,
                    )
                ) >= 3
                for message in FORBIDDEN_LOOP_ERRORS:
                    assert message not in worker_output
                    assert message not in beat_output
                assert worker.poll() is None
                assert beat.poll() is None
            except BaseException as exc:
                body_failure = (exc, exc.__traceback__)
            finally:
                for process in (worker, beat):
                    try:
                        stop_process(process, timeout_seconds=10)
                    except BaseException as exc:
                        cleanup_failures.append(exc)

                for log_handle in (worker_log, beat_log):
                    if log_handle is None:
                        continue
                    try:
                        log_handle.close()
                    except BaseException as exc:
                        cleanup_failures.append(exc)

                if body_failure is not None:
                    failure = body_failure[0]
                    try:
                        worker_output = _log_text(worker_log_path)[-12000:]
                        beat_output = _log_text(beat_log_path)[-12000:]
                        failure.add_note(
                            f"WORKER LOG:\n{worker_output}\n\nBEAT LOG:\n{beat_output}"
                        )
                    except BaseException as exc:
                        cleanup_failures.append(exc)

                try:
                    await cleanup()
                except BaseException as exc:
                    cleanup_failures.append(exc)
    except BaseException as exc:
        cleanup_failures.append(exc)

    if body_failure is not None:
        _raise_body_failure(body_failure, cleanup_failures)

    if cleanup_failures:
        _raise_cleanup_failures(cleanup_failures)

    assert temporary_path is not None
    assert not temporary_path.exists()
