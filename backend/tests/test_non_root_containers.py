import re
from configparser import ConfigParser
from pathlib import Path


ROOT = Path(__file__).parents[2]
APP_UID_GID = "10001:10001"


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def runtime_stage(dockerfile: str) -> str:
    stages = re.split(r"(?m)(?=^FROM\s+)", dockerfile)
    return next(stage for stage in reversed(stages) if stage.startswith("FROM "))


def exposed_ports(stage: str) -> list[int]:
    return [
        int(port)
        for line in re.findall(r"(?m)^EXPOSE\s+(.+)$", stage)
        for port in re.findall(r"\b\d+\b", line)
    ]


def test_all_runtime_images_use_the_fixed_non_root_identity():
    expected_ports = {
        "Dockerfile": [8080],
        "backend/Dockerfile": [8000],
        "frontend/Dockerfile": [8080],
    }

    for path, expected in expected_ports.items():
        stage = runtime_stage(read(path))
        user_declarations = re.findall(r"(?m)^USER\s+([^\s]+)", stage)

        assert user_declarations == [APP_UID_GID]
        assert not re.search(r"(?m)^USER\s+(?:0|root)(?::[^\s]+)?\s*$", stage)
        assert "10001" in stage
        assert "/nologin seo" in stage
        assert exposed_ports(stage) == expected
        assert all(port > 1024 for port in exposed_ports(stage))
        assert stage.index(f"USER {APP_UID_GID}") < max(
            stage.rfind("CMD ["), stage.rfind("ENTRYPOINT [")
        )


def test_images_grant_write_access_only_to_runtime_state():
    dockerfiles = [
        runtime_stage(read("Dockerfile")),
        runtime_stage(read("backend/Dockerfile")),
        runtime_stage(read("frontend/Dockerfile")),
    ]

    for stage in dockerfiles:
        assert "chown -R 10001:10001 /var/lib/seo" in stage
        assert not re.search(
            r"chown\s+-R\s+(?:10001(?::10001)?|seo(?::seo)?)\s+/(?:app|usr/share/nginx)",
            stage,
        )
        assert not re.search(r"chmod\s+[^\n]*(?:0?777|a\+rwx|o\+w)", stage)
        assert not re.search(r"\b(?:sudo|gosu|su-exec|runuser)\b", stage)

    root_image = dockerfiles[0]
    assert "COPY --chown=root:seo skills/ /app/skills/" in root_image
    assert "chmod -R u=rwX,g=rX,o=" in root_image
    assert "HOME=/var/lib/seo" in root_image
    assert "TMPDIR=/var/lib/seo/tmp" in root_image

    frontend_image = dockerfiles[2]
    assert 'ENTRYPOINT ["/usr/sbin/nginx"]' in frontend_image
    assert 'CMD ["-g", "daemon off;"]' in frontend_image


def test_nginx_uses_unprivileged_ports_and_writable_runtime_paths():
    for path in ("deploy/easypanel/nginx.conf", "frontend/nginx.conf"):
        nginx = read(path)
        listen_ports = [
            int(port) for port in re.findall(r"(?m)^\s*listen\s+(\d+)", nginx)
        ]

        assert listen_ports == [8080]
        assert all(port > 1024 for port in listen_ports)
        assert not re.search(r"(?m)^\s*user\s+", nginx)
        assert "pid /var/lib/seo/run/nginx.pid;" in nginx
        assert "error_log /dev/stderr" in nginx
        assert "access_log /dev/stdout" in nginx
        assert nginx.count("_temp_path /var/lib/seo/nginx/") == 5
        assert "/var/run" not in nginx
        assert "/var/cache" not in nginx
        assert "server_tokens off;" in nginx
        assert 'add_header X-Content-Type-Options "nosniff" always;' in nginx
        assert 'add_header X-Frame-Options "DENY" always;' in nginx
        assert (
            'add_header Referrer-Policy "strict-origin-when-cross-origin" always;'
            in nginx
        )


def test_supervisor_and_entrypoint_need_no_privilege_escalation():
    supervisor_text = read("deploy/easypanel/supervisord.conf")
    entrypoint = read("deploy/easypanel/entrypoint.sh")
    supervisor = ConfigParser(interpolation=None)
    supervisor.read_string(supervisor_text)

    assert supervisor.get("supervisord", "pidfile").startswith("/var/lib/seo/run/")
    assert supervisor.get("supervisord", "childlogdir") == "/var/lib/seo/log"
    assert supervisor.get("program:beat", "command").endswith(
        "--schedule=/var/lib/seo/celery/celerybeat-schedule"
    )
    assert supervisor.get("program:api", "command").endswith("--port 8000")
    assert supervisor.get("program:nginx", "command") == (
        '/usr/sbin/nginx -g "daemon off;"'
    )
    assert "user=root" not in supervisor_text
    assert "/tmp/" not in supervisor_text

    assert entrypoint.index("alembic upgrade head") < entrypoint.index(
        "exec /usr/bin/supervisord"
    )
    assert "/etc/supervisor/seo-supervisord.conf" in entrypoint
    assert not re.search(r"\b(?:sudo|gosu|su-exec|runuser)\b", entrypoint)


def test_compose_keeps_skills_read_only_and_matches_non_root_ports():
    compose = read("docker-compose.yml")

    assert compose.count("./skills:/app/skills:ro") == 3
    assert '"3000:8080"' in compose
    assert '"3000:80"' not in compose
    assert "--schedule=/var/lib/seo/celery/celerybeat-schedule" in compose


def test_operator_docs_describe_the_non_root_contract():
    readme = read("README.md")
    easypanel = read("deploy/easypanel/README.md")

    for document in (readme, easypanel):
        assert "UID/GID 10001" in document
        assert "porta interna 8080" in document
        assert "/var/lib/seo" in document
        assert "somente leitura" in document


def test_frontend_container_build_is_lockfile_reproducible_and_patched():
    dockerfile = read("frontend/Dockerfile")

    assert "COPY package.json package-lock.json ./" in dockerfile
    assert "RUN npm ci" in dockerfile
    assert "RUN npm install" not in dockerfile
    assert "FROM nginx:1.30.4-alpine-slim" in dockerfile
