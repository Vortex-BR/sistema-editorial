import ast
import logging
from pathlib import Path

import pytest

from app.workers import async_executor


APP_ROOT = Path(__file__).parents[1] / "app"
EXECUTOR_PATH = APP_ROOT / "workers" / "async_executor.py"
ASYNC_TASKS = {
    "pipeline.run": "run_pipeline",
    "pipeline.resume-due": "resume_due_pipeline_runs",
    "style.discover": "discover_style_patterns",
    "learning.reindex-embeddings": "reindex_learning_embeddings",
}


def _asyncio_run_calls(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    module_aliases = {"asyncio"}
    run_aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                if item.name == "asyncio":
                    module_aliases.add(item.asname or item.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "asyncio":
            for item in node.names:
                if item.name == "run":
                    run_aliases.add(item.asname or item.name)

    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        is_module_call = (
            isinstance(function, ast.Attribute)
            and function.attr == "run"
            and isinstance(function.value, ast.Name)
            and function.value.id in module_aliases
        )
        is_imported_call = isinstance(function, ast.Name) and function.id in run_aliases
        if is_module_call or is_imported_call:
            calls.append(node.lineno)
    return calls


def _celery_task_name(decorator: ast.expr) -> str | None:
    if not isinstance(decorator, ast.Call):
        return None
    function = decorator.func
    if not (
        isinstance(function, ast.Attribute)
        and function.attr == "task"
        and isinstance(function.value, ast.Name)
        and function.value.id == "celery"
    ):
        return None
    for keyword in decorator.keywords:
        if (
            keyword.arg == "name"
            and isinstance(keyword.value, ast.Constant)
            and isinstance(keyword.value.value, str)
        ):
            return keyword.value.value
    return None


def test_asyncio_run_is_exclusive_to_the_internal_executor():
    violations = {}
    for path in APP_ROOT.rglob("*.py"):
        calls = _asyncio_run_calls(path)
        if calls and path != EXECUTOR_PATH:
            violations[str(path.relative_to(APP_ROOT))] = calls

    assert violations == {}
    assert len(_asyncio_run_calls(EXECUTOR_PATH)) == 1


def test_all_async_celery_tasks_delegate_to_the_internal_executor():
    tasks_path = APP_ROOT / "workers" / "tasks.py"
    tree = ast.parse(tasks_path.read_text(encoding="utf-8"), filename=str(tasks_path))
    registered = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            task_name = _celery_task_name(decorator)
            if task_name:
                registered[task_name] = node

    assert {name: node.name for name, node in registered.items()} == ASYNC_TASKS
    for task_name, node in registered.items():
        executor_calls = [
            call
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "run_async_task"
        ]
        assert len(executor_calls) == 1, task_name


class RecordingEngine:
    def __init__(self, error: BaseException | None = None):
        self.error = error
        self.dispose_calls = 0

    async def dispose(self):
        self.dispose_calls += 1
        if self.error is not None:
            raise self.error


def test_executor_disposes_after_success(monkeypatch):
    engine = RecordingEngine()
    monkeypatch.setattr(async_executor, "engine", engine)

    async def succeed():
        return "done"

    assert async_executor.run_async_task(succeed()) == "done"
    assert engine.dispose_calls == 1


def test_executor_propagates_dispose_error_after_success(monkeypatch):
    dispose_error = RuntimeError("dispose failed")
    engine = RecordingEngine(dispose_error)
    monkeypatch.setattr(async_executor, "engine", engine)

    async def succeed():
        return "done"

    with pytest.raises(RuntimeError) as raised:
        async_executor.run_async_task(succeed())

    assert raised.value is dispose_error
    assert engine.dispose_calls == 1


def test_executor_preserves_task_error_when_dispose_also_fails(
    monkeypatch, caplog
):
    task_error = ValueError("original task failure")
    dispose_error = RuntimeError("dispose failed")
    engine = RecordingEngine(dispose_error)
    monkeypatch.setattr(async_executor, "engine", engine)

    async def fail_task():
        raise task_error

    with caplog.at_level(logging.ERROR), pytest.raises(ValueError) as raised:
        async_executor.run_async_task(fail_task())

    assert raised.value is task_error
    assert engine.dispose_calls == 1
    assert "fail_task" in [frame.name for frame in raised.traceback]
    assert "preserving the original task exception" in caplog.text
    assert "dispose failed" in caplog.text
