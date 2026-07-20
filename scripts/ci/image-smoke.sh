#!/usr/bin/env bash
set -Eeuo pipefail

: "${IMAGE_TAG:?IMAGE_TAG is required}"
: "${RESOURCE_PREFIX:?RESOURCE_PREFIX is required}"
: "${CANDIDATE_SHA:?CANDIDATE_SHA is required}"
: "${CI_MERGE_SHA:?CI_MERGE_SHA is required}"
: "${EXPECTED_APP_COMMIT_SHA:?EXPECTED_APP_COMMIT_SHA is required}"
: "${EXPECTED_APP_BUILD_VERSION:?EXPECTED_APP_BUILD_VERSION is required}"
: "${EXPECTED_APP_SOURCE_DIGEST:?EXPECTED_APP_SOURCE_DIGEST is required}"
: "${EXPECTED_IMAGE_SOURCE:?EXPECTED_IMAGE_SOURCE is required}"
: "${EXPECTED_ALEMBIC_HEAD:?EXPECTED_ALEMBIC_HEAD is required}"
: "${EXPECTED_PGVECTOR_VERSION:?EXPECTED_PGVECTOR_VERSION is required}"

PREFIX="$(
  printf '%s' "${RESOURCE_PREFIX}" |
    tr '[:upper:]_/' '[:lower:]--' |
    sed -E 's/[^a-z0-9.-]+/-/g; s/^-+//; s/-+$//'
)"
NETWORK_NAME="${PREFIX}-network"
POSTGRES_NAME="${PREFIX}-postgres"
REDIS_NAME="${PREFIX}-redis"
INVALID_APP_NAME="${PREFIX}-invalid-app"
VALID_APP_NAME="${PREFIX}-app"
MIGRATION_NAME="${PREFIX}-migrate"
SEED_NAME="${PREFIX}-seed"

POSTGRES_DATABASE="seo_image_smoke"
POSTGRES_USER="seo_smoke"
POSTGRES_PASSWORD=""
ADMIN_TOKEN=""
MASTER_KEY=""
PROVIDER_CREDENTIAL=""
DATABASE_URL=""
REDIS_URL=""

fail() {
  printf '::error::%s\n' "$*" >&2
  return 1
}

container_exists() {
  docker container inspect "$1" >/dev/null 2>&1
}

cleanup_resources() {
  local container
  set +e
  for container in \
    "${VALID_APP_NAME}" \
    "${INVALID_APP_NAME}" \
    "${MIGRATION_NAME}" \
    "${SEED_NAME}" \
    "${REDIS_NAME}" \
    "${POSTGRES_NAME}"
  do
    if container_exists "${container}"; then
      docker container rm --force "${container}" >/dev/null 2>&1
    fi
  done
  if docker network inspect "${NETWORK_NAME}" >/dev/null 2>&1; then
    docker network rm "${NETWORK_NAME}" >/dev/null 2>&1
  fi
  if docker image inspect "${IMAGE_TAG}" >/dev/null 2>&1; then
    docker image rm "${IMAGE_TAG}" >/dev/null 2>&1
  fi
  set -e
}

container_env_value() {
  local container="$1"
  local variable_name="$2"
  if ! container_exists "${container}"; then
    return 0
  fi
  docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "${container}" |
    sed -n "s/^${variable_name}=//p" |
    head -n 1
}

redacted_logs() {
  local container="$1"
  local logs
  local secret
  local secrets=()
  if ! container_exists "${container}"; then
    return 0
  fi
  logs="$(docker logs --timestamps --tail 500 "${container}" 2>&1 || true)"
  secrets+=(
    "${POSTGRES_PASSWORD}"
    "${ADMIN_TOKEN}"
    "${MASTER_KEY}"
    "${PROVIDER_CREDENTIAL}"
    "$(container_env_value "${container}" POSTGRES_PASSWORD)"
    "$(container_env_value "${container}" ADMIN_API_TOKEN)"
    "$(container_env_value "${container}" CREDENTIAL_MASTER_KEY)"
    "$(container_env_value "${container}" DATABASE_URL)"
    "$(container_env_value "${container}" REDIS_URL)"
  )
  for secret in "${secrets[@]}"; do
    if [[ -n "${secret}" ]]; then
      logs="${logs//"${secret}"/[REDACTED]}"
    fi
  done
  printf '%s\n' "${logs}" |
    sed -E 's#(postgresql(\+asyncpg)?|redis)://[^[:space:]]+#\1://[REDACTED]#g' |
    tail -n 500
}

show_diagnostics() {
  local id
  local name
  local configured_environment_names
  set +e

  printf '%s\n' '=== CONTAINERS ==='
  docker ps -a --no-trunc

  printf '%s\n' '=== CONTAINER INSPECTION ==='
  while read -r id; do
    [[ -n "${id}" ]] || continue
    printf '%s\n' '----------------------------------------'
    docker inspect \
      --format='Name={{.Name}} ID={{.Id}} Image={{.Config.Image}} Status={{.State.Status}} ExitCode={{.State.ExitCode}} OOMKilled={{.State.OOMKilled}} Error={{.State.Error}} RestartCount={{.RestartCount}} Health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} Ports={{json .NetworkSettings.Ports}} PortBindings={{json .HostConfig.PortBindings}}' \
      "${id}" || true
    configured_environment_names="$(
      docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "${id}" |
        sed 's/=.*//' |
        grep -E '^(APP_[A-Z0-9_]*|DATABASE_URL|REDIS_URL|ADMIN_API_TOKEN|CREDENTIAL_MASTER_KEY|SUPERIOR_SKILLS_MODE|POSTGRES_[A-Z0-9_]*|CELERY_[A-Z0-9_]*|HOME|TMPDIR|SKILLS_PATH)$' |
        sort -u |
        paste -sd, -
    )"
    printf 'ConfiguredEnvironmentNames=%s\n' "${configured_environment_names:-none}"
    docker inspect \
      --format='{{if .State.Health}}{{range .State.Health.Log}}HealthCheck ExitCode={{.ExitCode}} Output={{json .Output}}{{println}}{{end}}{{end}}' \
      "${id}" || true
  done < <(docker ps -aq --filter "label=seo.image-smoke.prefix=${PREFIX}")

  printf '%s\n' '=== CONTAINER LOGS ==='
  while read -r id; do
    [[ -n "${id}" ]] || continue
    name="$(docker inspect --format '{{.Name}}' "${id}" 2>/dev/null || true)"
    printf '%s\n' '----------------------------------------'
    printf 'Container: %s Name=%s\n' "${id}" "${name:-unknown}"
    redacted_logs "${id}"
  done < <(docker ps -aq --filter "label=seo.image-smoke.prefix=${PREFIX}")

  set -e
}

on_error() {
  local exit_code=$?
  trap - ERR EXIT
  printf '::error::Image smoke failed; preserving scoped containers for the diagnostic step.\n' >&2
  exit "${exit_code}"
}

if [[ "${1:-run}" == "cleanup" ]]; then
  cleanup_resources
  printf 'IMAGE_SMOKE_CLEANUP: scoped resources removed\n'
  exit 0
fi

if [[ "${1:-run}" == "diagnostics" ]]; then
  show_diagnostics
  exit 0
fi

trap on_error ERR
trap cleanup_resources EXIT

wait_for_healthy_container() {
  local container="$1"
  local attempts="$2"
  local state
  local health
  local attempt

  for ((attempt = 1; attempt <= attempts; attempt += 1)); do
    state="$(docker inspect --format '{{.State.Status}}' "${container}")"
    health="$(
      docker inspect \
        --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' \
        "${container}"
    )"
    if [[ "${state}" == "running" && "${health}" == "healthy" ]]; then
      return 0
    fi
    if [[ "${state}" == "exited" || "${state}" == "dead" ]]; then
      fail "${container} exited before becoming healthy"
    fi
    sleep 2
  done
  fail "${container} did not become healthy before the timeout"
}

wait_for_http() {
  local url="$1"
  local expected_status="$2"
  local attempts="$3"
  local status
  local attempt

  for ((attempt = 1; attempt <= attempts; attempt += 1)); do
    status="$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' "${url}" || true)"
    if [[ "${status}" == "${expected_status}" ]]; then
      return 0
    fi
    if [[ "$(docker inspect --format '{{.State.Status}}' "${VALID_APP_NAME}")" != "running" ]]; then
      fail "production container exited while waiting for ${url}"
    fi
    sleep 2
  done
  fail "${url} did not return ${expected_status} before the timeout"
}

assert_no_exact_secret() {
  local value="$1"
  local source="$2"
  shift 2
  local secret
  for secret in "$@"; do
    if [[ -n "${secret}" && "${value}" == *"${secret}"* ]]; then
      fail "${source} exposed a disposable runtime secret"
    fi
  done
}

checkout_sha="$(git rev-parse HEAD)"
[[ "${checkout_sha}" == "${CANDIDATE_SHA}" ]] ||
  fail "checkout HEAD does not match CANDIDATE_SHA"
[[ "${EXPECTED_APP_COMMIT_SHA}" == "${CANDIDATE_SHA}" ]] ||
  fail "APP_COMMIT_SHA expectation is not bound to CANDIDATE_SHA"

image_id="$(docker image inspect --format '{{.Id}}' "${IMAGE_TAG}")"
image_digests="$(docker image inspect --format '{{json .RepoDigests}}' "${IMAGE_TAG}")"
image_user="$(docker image inspect --format '{{.Config.User}}' "${IMAGE_TAG}")"
image_environment="$(docker image inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "${IMAGE_TAG}")"
image_revision="$(
  docker image inspect \
    --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' \
    "${IMAGE_TAG}"
)"
image_version="$(
  docker image inspect \
    --format '{{index .Config.Labels "org.opencontainers.image.version"}}' \
    "${IMAGE_TAG}"
)"
image_source="$(
  docker image inspect \
    --format '{{index .Config.Labels "org.opencontainers.image.source"}}' \
    "${IMAGE_TAG}"
)"
observed_app_commit_sha="$(
  sed -n 's/^APP_COMMIT_SHA=//p' <<<"${image_environment}" | head -n 1
)"
observed_app_build_version="$(
  sed -n 's/^APP_BUILD_VERSION=//p' <<<"${image_environment}" | head -n 1
)"
observed_app_source_digest="$(
  sed -n 's/^APP_SOURCE_DIGEST=//p' <<<"${image_environment}" | head -n 1
)"
baked_build_info="$(
  docker run --rm --entrypoint python "${IMAGE_TAG}" -c \
    "import json; p=json.load(open('/app/build-info.json', encoding='utf-8')); print(p['commit_sha']+'|'+p['build_version']+'|'+p['source_digest'])"
)"
expected_build_info="${CANDIDATE_SHA}|${EXPECTED_APP_BUILD_VERSION}|${EXPECTED_APP_SOURCE_DIGEST}"

[[ "${image_user}" == "10001:10001" ]] || fail "image user is not 10001:10001"
[[ "${observed_app_commit_sha}" == "${CANDIDATE_SHA}" ]] ||
  fail "APP_COMMIT_SHA metadata does not match the checked-out SHA"
[[ "${observed_app_build_version}" == "${EXPECTED_APP_BUILD_VERSION}" ]] ||
  fail "APP_BUILD_VERSION metadata is not the immutable CI version"
[[ "${observed_app_source_digest}" == "${EXPECTED_APP_SOURCE_DIGEST}" ]] ||
  fail "APP_SOURCE_DIGEST metadata does not match the checked-out tree"
[[ "${baked_build_info}" == "${expected_build_info}" ]] ||
  fail "baked build information does not match the immutable candidate"
[[ -n "${observed_app_build_version}" && "${observed_app_build_version}" != "development" ]] ||
  fail "APP_BUILD_VERSION is empty or mutable"
[[ "${image_revision}" == "${CANDIDATE_SHA}" ]] ||
  fail "OCI revision does not match CANDIDATE_SHA"
[[ "${image_version}" == "${observed_app_build_version}" ]] ||
  fail "OCI version does not match APP_BUILD_VERSION"
[[ "${image_source}" == "${EXPECTED_IMAGE_SOURCE}" ]] ||
  fail "OCI source does not match the repository"

printf 'CANDIDATE_SHA: %s\n' "${CANDIDATE_SHA}"
printf 'CI_MERGE_SHA: %s\n' "${CI_MERGE_SHA}"
printf 'CHECKOUT_SHA: %s\n' "${checkout_sha}"
printf 'IMAGE_ID: %s\n' "${image_id}"
printf 'IMAGE_REPO_DIGESTS: %s\n' "${image_digests}"
printf 'IMAGE_USER: %s\n' "${image_user}"
printf 'APP_COMMIT_SHA_OBSERVED: %s\n' "${observed_app_commit_sha}"
printf 'APP_BUILD_VERSION_OBSERVED: %s\n' "${observed_app_build_version}"
printf 'APP_SOURCE_DIGEST_OBSERVED: %s\n' "${observed_app_source_digest}"
printf 'BAKED_BUILD_INFO: verified\n'
printf 'OCI_REVISION: %s\n' "${image_revision}"
printf 'OCI_VERSION: %s\n' "${image_version}"
printf 'OCI_SOURCE: %s\n' "${image_source}"

if ! layer_audit="$(
  docker image save "${IMAGE_TAG}" |
    python3 scripts/ci/audit_image_layers.py 2>&1
)"; then
  printf '%s\n' "${layer_audit}" >&2
  fail "production image layers contain forbidden application residue"
fi
printf '%s\n' "${layer_audit}"
printf 'IMAGE_LAYER_POLICY: passed\n'

POSTGRES_PASSWORD="$(openssl rand -hex 24)"
ADMIN_TOKEN="$(openssl rand -hex 32)"
MASTER_KEY="$(openssl rand -base64 32 | tr '+/' '-_' | tr -d '\n')"
PROVIDER_CREDENTIAL="$(openssl rand -hex 24)"
DATABASE_URL="postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_NAME}:5432/${POSTGRES_DATABASE}"
REDIS_URL="redis://${REDIS_NAME}:6379/0"

docker network create \
  --label "seo.image-smoke.prefix=${PREFIX}" \
  "${NETWORK_NAME}" >/dev/null

docker run --detach \
  --name "${POSTGRES_NAME}" \
  --network "${NETWORK_NAME}" \
  --network-alias "${POSTGRES_NAME}" \
  --label "seo.image-smoke.prefix=${PREFIX}" \
  --tmpfs /var/lib/postgresql/data:rw,noexec,nosuid,size=768m \
  --env "POSTGRES_DB=${POSTGRES_DATABASE}" \
  --env "POSTGRES_USER=${POSTGRES_USER}" \
  --env "POSTGRES_PASSWORD=${POSTGRES_PASSWORD}" \
  --health-cmd "pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DATABASE}" \
  --health-interval 2s \
  --health-timeout 3s \
  --health-retries 30 \
  pgvector/pgvector:pg17 >/dev/null

docker run --detach \
  --name "${REDIS_NAME}" \
  --network "${NETWORK_NAME}" \
  --network-alias "${REDIS_NAME}" \
  --label "seo.image-smoke.prefix=${PREFIX}" \
  --tmpfs /data:rw,noexec,nosuid,size=128m \
  --health-cmd "redis-cli ping" \
  --health-interval 2s \
  --health-timeout 3s \
  --health-retries 30 \
  redis:7.4-alpine redis-server --save '' --appendonly no >/dev/null

wait_for_healthy_container "${POSTGRES_NAME}" 45
wait_for_healthy_container "${REDIS_NAME}" 45

postgres_version="$(
  docker exec "${POSTGRES_NAME}" \
    psql --username "${POSTGRES_USER}" --dbname "${POSTGRES_DATABASE}" \
      --tuples-only --no-align --command "SHOW server_version"
)"
[[ "${postgres_version}" == 17.* ]] || fail "PostgreSQL 17 was not started"

initial_schema_state="$(
  docker exec "${POSTGRES_NAME}" \
    psql --username "${POSTGRES_USER}" --dbname "${POSTGRES_DATABASE}" \
      --tuples-only --no-align \
      --command "SELECT to_regclass('public.alembic_version') IS NULL"
)"
[[ "${initial_schema_state}" == "t" ]] || fail "smoke database was not empty"
printf 'POSTGRESQL_VERSION: %s\n' "${postgres_version}"
printf 'DATABASE_INITIAL_STATE: empty\n'

docker create \
  --name "${INVALID_APP_NAME}" \
  --network "${NETWORK_NAME}" \
  --label "seo.image-smoke.prefix=${PREFIX}" \
  --env APP_ENV=production \
  --env "DATABASE_URL=${DATABASE_URL}" \
  --env "REDIS_URL=${REDIS_URL}" \
  --env "CREDENTIAL_MASTER_KEY=${MASTER_KEY}" \
  --env SUPERIOR_SKILLS_MODE=enforced \
  "${IMAGE_TAG}" >/dev/null
docker start "${INVALID_APP_NAME}" >/dev/null

if ! invalid_exit_code="$(timeout 60s docker wait "${INVALID_APP_NAME}")"; then
  fail "invalid production container did not fail closed before the timeout"
fi
[[ "${invalid_exit_code}" != "0" ]] || fail "invalid production configuration exited successfully"
invalid_health="$(
  docker inspect \
    --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' \
    "${INVALID_APP_NAME}"
)"
[[ "${invalid_health}" != "healthy" ]] || fail "invalid production configuration became healthy"
invalid_logs="$(docker logs "${INVALID_APP_NAME}" 2>&1 || true)"
grep -Fq 'ADMIN_API_TOKEN' <<<"${invalid_logs}" ||
  fail "invalid startup did not identify the missing requirement"
assert_no_exact_secret \
  "${invalid_logs}" \
  "invalid startup logs" \
  "${POSTGRES_PASSWORD}" "${MASTER_KEY}" "${PROVIDER_CREDENTIAL}"

schema_after_invalid="$(
  docker exec "${POSTGRES_NAME}" \
    psql --username "${POSTGRES_USER}" --dbname "${POSTGRES_DATABASE}" \
      --tuples-only --no-align \
      --command "SELECT to_regclass('public.alembic_version') IS NULL"
)"
[[ "${schema_after_invalid}" == "t" ]] ||
  fail "invalid startup ran migrations instead of failing closed"
printf 'INVALID_CONFIGURATION: failed closed with exit %s and health %s\n' \
  "${invalid_exit_code}" "${invalid_health}"

docker run --rm \
  --name "${MIGRATION_NAME}" \
  --network "${NETWORK_NAME}" \
  --label "seo.image-smoke.prefix=${PREFIX}" \
  --env "DATABASE_URL=${DATABASE_URL}" \
  --entrypoint alembic \
  "${IMAGE_TAG}" upgrade head

alembic_head="$(
  docker exec "${POSTGRES_NAME}" \
    psql --username "${POSTGRES_USER}" --dbname "${POSTGRES_DATABASE}" \
      --tuples-only --no-align \
      --command "SELECT version_num FROM alembic_version"
)"
[[ "${alembic_head}" == "${EXPECTED_ALEMBIC_HEAD}" ]] ||
  fail "database did not reach Alembic ${EXPECTED_ALEMBIC_HEAD}"

pgvector_version="$(
  docker exec "${POSTGRES_NAME}" \
    psql --username "${POSTGRES_USER}" --dbname "${POSTGRES_DATABASE}" \
      --tuples-only --no-align \
      --command "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
)"
[[ "${pgvector_version}" == "${EXPECTED_PGVECTOR_VERSION}" ]] ||
  fail "pgvector version was ${pgvector_version}, expected ${EXPECTED_PGVECTOR_VERSION}"
printf 'ALEMBIC_HEAD: %s\n' "${alembic_head}"
printf 'PGVECTOR_VERSION: %s\n' "${pgvector_version}"

docker run --rm --interactive \
  --name "${SEED_NAME}" \
  --network "${NETWORK_NAME}" \
  --label "seo.image-smoke.prefix=${PREFIX}" \
  --env "DATABASE_URL=${DATABASE_URL}" \
  --env "CREDENTIAL_MASTER_KEY=${MASTER_KEY}" \
  --env "SMOKE_PROVIDER_CREDENTIAL=${PROVIDER_CREDENTIAL}" \
  --entrypoint python \
  "${IMAGE_TAG}" - <<'PY'
import asyncio
import os

from cryptography.fernet import Fernet

from app.db.models import Credential, CredentialProvider, ModelRoute
from app.db.session import SessionLocal
from app.services.model_catalog import default_openai_route_configuration
from app.services.model_route_policy import normalize_model_route_configuration
from app.startup import REQUIRED_EDITORIAL_ROLES


async def main() -> None:
    vault = Fernet(os.environ["CREDENTIAL_MASTER_KEY"].encode())
    provider_value = os.environ["SMOKE_PROVIDER_CREDENTIAL"].encode()
    async with SessionLocal() as database:
        database.add(
            Credential(
                provider=CredentialProvider.openai,
                encrypted_value=vault.encrypt(provider_value),
                key_version=1,
                last_four="test",
                active=True,
            )
        )
        for role in REQUIRED_EDITORIAL_ROLES:
            route = normalize_model_route_configuration(
                default_openai_route_configuration(role)
            )
            database.add(ModelRoute(**route))
        await database.commit()
    print("DISPOSABLE_EDITORIAL_CONFIGURATION: seeded")


asyncio.run(main())
PY

docker run --detach \
  --name "${VALID_APP_NAME}" \
  --network "${NETWORK_NAME}" \
  --label "seo.image-smoke.prefix=${PREFIX}" \
  --publish 127.0.0.1::8080 \
  --env APP_ENV=production \
  --env "DATABASE_URL=${DATABASE_URL}" \
  --env "REDIS_URL=${REDIS_URL}" \
  --env "ADMIN_API_TOKEN=${ADMIN_TOKEN}" \
  --env "CREDENTIAL_MASTER_KEY=${MASTER_KEY}" \
  --env SUPERIOR_SKILLS_MODE=enforced \
  "${IMAGE_TAG}" >/dev/null

published_port="$(
  docker port "${VALID_APP_NAME}" 8080/tcp |
    awk -F: 'NR == 1 {print $NF}'
)"
[[ "${published_port}" =~ ^[0-9]+$ ]] || fail "application port was not published"
base_url="http://127.0.0.1:${published_port}"

wait_for_http "${base_url}/api/v1/health" 200 90
wait_for_http "${base_url}/api/v1/readiness" 200 90
wait_for_healthy_container "${VALID_APP_NAME}" 60

liveness_body="$(curl --silent --show-error "${base_url}/api/v1/health")"
readiness_body="$(curl --silent --show-error "${base_url}/api/v1/readiness")"
jq --exit-status \
  '.status == "ready" and all(.components[]; .status == "ready")' \
  <<<"${readiness_body}" >/dev/null || fail "readiness components were not all ready"
assert_no_exact_secret \
  "${liveness_body}${readiness_body}" \
  "health responses" \
  "${POSTGRES_PASSWORD}" "${ADMIN_TOKEN}" "${MASTER_KEY}" "${PROVIDER_CREDENTIAL}"
if grep -Eqi '(postgresql(\+asyncpg)?|redis)://|authorization[[:space:]]*:' \
  <<<"${liveness_body}${readiness_body}"; then
  fail "health response exposed internal connection or authorization data"
fi
if grep -Eqi 'traceback|stack trace|internal server error|/app/backend/' \
  <<<"${liveness_body}${readiness_body}"; then
  fail "health response exposed an internal error detail"
fi
printf 'LIVENESS: 200\n'
printf 'READINESS: 200 with all components ready\n'

for token_case in missing empty placeholder; do
  case "${token_case}" in
    missing)
      protected_status="$(
        curl --silent --show-error --output /dev/null --write-out '%{http_code}' \
          "${base_url}/api/v1/projects"
      )"
      ;;
    empty)
      protected_status="$(
        curl --silent --show-error --output /dev/null --write-out '%{http_code}' \
          --header 'X-Admin-Token:' "${base_url}/api/v1/projects"
      )"
      ;;
    placeholder)
      protected_status="$(
        curl --silent --show-error --output /dev/null --write-out '%{http_code}' \
          --header 'X-Admin-Token: replace-with-a-strong-random-secret' \
          "${base_url}/api/v1/projects"
      )"
      ;;
  esac
  [[ "${protected_status}" == "401" ]] ||
    fail "protected route accepted the ${token_case} token case"
done
unauthorized_body="$(curl --silent --show-error "${base_url}/api/v1/projects")"
assert_no_exact_secret \
  "${unauthorized_body}" \
  "unauthorized response" \
  "${POSTGRES_PASSWORD}" "${ADMIN_TOKEN}" "${MASTER_KEY}" "${PROVIDER_CREDENTIAL}"
if grep -Eqi 'traceback|stack trace|internal server error|/app/backend/' \
  <<<"${unauthorized_body}"; then
  fail "unauthorized response exposed an internal error detail"
fi
printf 'PROTECTED_ROUTE: 401 for missing, empty, and placeholder tokens\n'

for documentation_path in /api/docs /api/redoc /api/openapi.json; do
  documentation_status="$(
    curl --silent --show-error --output /dev/null --write-out '%{http_code}' \
      "${base_url}${documentation_path}"
  )"
  [[ "${documentation_status}" == "404" ]] ||
    fail "${documentation_path} was exposed with status ${documentation_status}"
done
for public_path in /docs /redoc /openapi.json; do
  public_body="$(curl --silent --show-error "${base_url}${public_path}")"
  if grep -Eqi 'SwaggerUIBundle|redoc\.standalone|"openapi"[[:space:]]*:' \
    <<<"${public_body}"; then
    fail "${public_path} exposed API documentation in production"
  fi
done
printf 'API_DOCUMENTATION: unavailable in production\n'

configured_user="$(docker image inspect --format '{{.Config.User}}' "${IMAGE_TAG}")"
effective_uid="$(docker exec "${VALID_APP_NAME}" id --user)"
pid_one_uid="$(
  docker exec "${VALID_APP_NAME}" \
    awk '/^Uid:/ {print $2}' /proc/1/status
)"
[[ "${configured_user}" == "10001:10001" ]] || fail "image user changed at runtime"
[[ "${effective_uid}" == "10001" ]] || fail "container exec user is root"
[[ "${pid_one_uid}" == "10001" ]] || fail "PID 1 is root"
for writable_path in \
  /var/lib/seo \
  /var/lib/seo/run \
  /var/lib/seo/log \
  /var/lib/seo/tmp \
  /var/lib/seo/celery \
  /var/lib/seo/nginx
do
  path_permissions="$(
    docker exec "${VALID_APP_NAME}" \
      stat --format '%u:%g:%a' "${writable_path}"
  )"
  [[ "${path_permissions}" == "10001:10001:750" ]] ||
    fail "${writable_path} has unexpected ownership or permissions"
done
docker exec "${VALID_APP_NAME}" test ! -w /app/backend ||
  fail "/app/backend is writable by the runtime user"
docker exec "${VALID_APP_NAME}" test ! -w /app/skills ||
  fail "/app/skills is writable by the runtime user"

process_table="$(docker top "${VALID_APP_NAME}" -eo user,pid,ppid,args)"
printf '%s\n' "${process_table}"
if tail -n +2 <<<"${process_table}" | awk '$1 == "root" || $1 == "0" {found=1} END {exit !found}'; then
  fail "at least one application process runs as root"
fi
grep -Eq 'supervisord .*seo-supervisord\.conf' <<<"${process_table}" ||
  fail "Supervisor is not active"
grep -Eq 'uvicorn .*app\.main:app' <<<"${process_table}" || fail "API is not active"
grep -Eq 'celery .* worker([[:space:]]|$)' <<<"${process_table}" || fail "Worker is not active"
grep -Eq 'nginx: master process|nginx .*daemon off' <<<"${process_table}" ||
  fail "Nginx is not active"
beat_count="$(grep -Ec 'celery .* beat([[:space:]]|$)' <<<"${process_table}")"
[[ "${beat_count}" == "1" ]] || fail "expected exactly one Beat process, found ${beat_count}"

process_fingerprint_before="$(
  tail -n +2 <<<"${process_table}" |
    grep -E 'supervisord|uvicorn|celery|nginx' |
    awk '{print $2 ":" $0}' |
    sort
)"
sleep 15
process_table_after="$(docker top "${VALID_APP_NAME}" -eo user,pid,ppid,args)"
process_fingerprint_after="$(
  tail -n +2 <<<"${process_table_after}" |
    grep -E 'supervisord|uvicorn|celery|nginx' |
    awk '{print $2 ":" $0}' |
    sort
)"
[[ "${process_fingerprint_before}" == "${process_fingerprint_after}" ]] ||
  fail "application processes restarted during the observation window"
[[ "$(docker inspect --format '{{.RestartCount}}' "${VALID_APP_NAME}")" == "0" ]] ||
  fail "application container restarted"
[[ "$(docker inspect --format '{{.State.Status}}' "${VALID_APP_NAME}")" == "running" ]] ||
  fail "application container did not remain running"
printf 'CONTAINER_USER: uid=%s pid1_uid=%s\n' "${effective_uid}" "${pid_one_uid}"
printf 'RUNTIME_PERMISSIONS: writable state is 10001:10001 mode 0750; code and skills read-only\n'
printf 'PROCESSES: Nginx, API, Worker, Supervisor active; Beat=%s; stable=15s\n' \
  "${beat_count}"

valid_logs="$(docker logs "${VALID_APP_NAME}" 2>&1 || true)"
assert_no_exact_secret \
  "${valid_logs}" \
  "production container logs" \
  "${POSTGRES_PASSWORD}" "${ADMIN_TOKEN}" "${MASTER_KEY}" "${PROVIDER_CREDENTIAL}"
if grep -Eqi '(postgresql(\+asyncpg)?|redis)://[^[:space:]]+:[^[:space:]@]+@|authorization[[:space:]]*:' \
  <<<"${valid_logs}"; then
  fail "production logs may expose credentials"
fi
printf 'RUNTIME_SECRET_SCAN: no exposure identified\n'

if ! image_audit="$(
  docker run --rm --interactive --entrypoint python "${IMAGE_TAG}" - <<'PY'
from importlib import metadata
from pathlib import Path
import shutil


findings: list[str] = []
for path in (
    Path("/app/backend/tests"),
    Path("/app/backend/fixtures"),
    Path("/app/backend/.git"),
    Path("/app/.git"),
    Path("/.git"),
):
    if path.exists():
        findings.append(f"forbidden runtime path: {path}")

for root in (Path("/app/backend"), Path("/app/skills"), Path("/usr/share/nginx/html")):
    if not root.exists():
        continue
    for path in root.rglob("*"):
        if path.is_dir() and path.name.lower() in {
            "tests",
            "test",
            "fixtures",
            ".git",
            "__pycache__",
            ".pytest_cache",
            ".ruff_cache",
            ".mypy_cache",
            "htmlcov",
            "node_modules",
        }:
            findings.append(f"forbidden runtime residue: {path}")
        if not path.is_file():
            continue
        name = path.name.lower()
        suffix = path.suffix.lower()
        is_test_artifact = (
            name == "conftest.py"
            or (name.startswith("test_") and suffix == ".py")
            or (name.endswith("_test.py"))
            or ".test." in name
            or ".spec." in name
        )
        is_sensitive_or_local = (
            name
            in {
                ".env",
                ".npmrc",
                ".pypirc",
                ".coverage",
                "coverage.xml",
                "credentials.json",
                ".ds_store",
                "thumbs.db",
                "desktop.ini",
            }
            or name.startswith(".env.")
            or name.startswith("id_rsa")
            or name.startswith("secrets.")
            or suffix
            in {
                ".pyc",
                ".pem",
                ".key",
                ".zip",
                ".7z",
                ".rar",
                ".tar",
                ".gz",
                ".tgz",
                ".db",
                ".sqlite",
                ".sqlite3",
                ".dump",
                ".bak",
                ".log",
            }
        )
        is_internal_documentation = root == Path("/app/backend") and (
            name.startswith("readme") or suffix in {".md", ".rst"}
        )
        if is_test_artifact or is_sensitive_or_local or is_internal_documentation:
            findings.append(f"forbidden runtime residue: {path}")

for tool in ("gcc", "g++", "make", "npm", "node"):
    if shutil.which(tool):
        findings.append(f"unnecessary runtime build tool: {tool}")

installed_distributions = {
    distribution.metadata["Name"].lower()
    for distribution in metadata.distributions()
    if distribution.metadata["Name"]
}
for development_distribution in ("pytest", "pytest-asyncio", "ruff"):
    if development_distribution in installed_distributions:
        findings.append(
            f"unnecessary runtime development dependency: {development_distribution}"
        )

if findings:
    print("\n".join(sorted(set(findings))))
    raise SystemExit(1)
PY
)"; then
  printf '%s\n' "${image_audit}"
  fail "production image contains forbidden runtime residue"
fi

os_inventory="$(
  docker run --rm --entrypoint sh "${IMAGE_TAG}" \
    -c "dpkg-query --show --showformat='\${Package}=\${Version}\n' | sort"
)"
python_inventory="$(
  docker run --rm --entrypoint python "${IMAGE_TAG}" \
    -m pip list --format=freeze --disable-pip-version-check
)"
os_package_count="$(wc -l <<<"${os_inventory}" | tr -d ' ')"
python_package_count="$(wc -l <<<"${python_inventory}" | tr -d ' ')"
os_inventory_sha="$(printf '%s' "${os_inventory}" | sha256sum | awk '{print $1}')"
python_inventory_sha="$(printf '%s' "${python_inventory}" | sha256sum | awk '{print $1}')"
printf 'IMAGE_AUDIT: passed\n'
printf 'RUNTIME_INVENTORY: os_packages=%s os_sha256=%s python_packages=%s python_sha256=%s\n' \
  "${os_package_count}" "${os_inventory_sha}" \
  "${python_package_count}" "${python_inventory_sha}"
printf 'IMAGE_SMOKE_RESULT: passed\n'
