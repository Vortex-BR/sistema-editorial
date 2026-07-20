import json
import re
from configparser import ConfigParser
from pathlib import Path


ROOT = Path(__file__).parents[2]


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_startup_emits_a_safe_structured_single_replica_policy():
    entrypoint = read("deploy/easypanel/entrypoint.sh")
    policy_line = next(
        line for line in entrypoint.splitlines() if "startup.replica_policy" in line
    )
    match = re.fullmatch(r"printf '%s\\n' '(\{.*\})'", policy_line)

    assert match is not None
    policy = json.loads(match.group(1))
    assert policy["event"] == "startup.replica_policy"
    assert policy["deployment_mode"] == "all-in-one"
    assert policy["supported_app_replicas"] == 1
    assert policy["beat_replicas"] == 1
    assert policy["horizontal_scaling_supported"] is False
    assert entrypoint.index(policy_line) < entrypoint.index("alembic upgrade head")
    assert entrypoint.index("alembic upgrade head") < entrypoint.index(
        "exec /usr/bin/supervisord"
    )

    serialized_policy = json.dumps(policy).lower()
    for secret_name in (
        "admin_api_token",
        "credential_master_key",
        "database_url",
        "redis_url",
    ):
        assert secret_name not in serialized_policy


def test_supervisor_starts_exactly_one_embedded_beat_process():
    supervisor_text = read("deploy/easypanel/supervisord.conf")
    compose = read("docker-compose.yml")
    supervisor = ConfigParser(interpolation=None)
    supervisor.read_string(supervisor_text)

    assert supervisor_text.count("[program:beat]") == 1
    assert supervisor.getint("program:beat", "numprocs") == 1
    assert supervisor.get("program:beat", "command").startswith(
        "celery -A app.workers.celery_app beat "
    )
    assert len(re.findall(r"^  beat:$", compose, re.MULTILINE)) == 1
    assert compose.count("celery -A app.workers.celery_app beat ") == 1


def test_operator_guides_require_one_app_replica_permanently():
    readme = read("README.md")
    easypanel = read("deploy/easypanel/README.md")

    for document in (readme, easypanel):
        assert "App replicas = 1" in document
        assert "regra permanente" in document.lower()
        assert re.search(
            r"Escala horizontal futura exige separar API, Worker e\s+>?\s*Beat",
            document,
        )

    assert "Mantenha uma única réplica do App durante migrations" not in readme
    assert "só escale após o head" not in easypanel.lower()
