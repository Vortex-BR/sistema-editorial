import os
import json
import subprocess
import sys
import uuid
from pathlib import Path

import asyncpg
import pytest

from app.services.editorial_seal import canonical_checksum

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_ADMIN_URL"),
    reason="TEST_POSTGRES_ADMIN_URL is required for migration integration tests",
)

EXPECTED_POSTGRES_MAJOR = 17
EXPECTED_ALEMBIC_HEAD = "0036"


async def _status_schema(connection: asyncpg.Connection) -> dict[str, list[tuple]]:
    defaults = await connection.fetch(
        "SELECT table_name, udt_name, column_default, is_nullable "
        "FROM information_schema.columns "
        "WHERE table_schema = 'public' "
        "AND table_name = ANY($1::text[]) AND column_name = 'status' "
        "ORDER BY table_name",
        ["pipeline_runs", "projects"],
    )
    indexes = await connection.fetch(
        "SELECT table_rel.relname AS table_name, index_rel.relname AS index_name, "
        "pg_get_indexdef(index_data.indexrelid) AS definition, "
        "pg_get_expr(index_data.indpred, index_data.indrelid) AS predicate, "
        "index_data.indisvalid "
        "FROM pg_index index_data "
        "JOIN pg_class table_rel ON table_rel.oid = index_data.indrelid "
        "JOIN pg_class index_rel ON index_rel.oid = index_data.indexrelid "
        "WHERE table_rel.relnamespace = 'public'::regnamespace "
        "AND table_rel.relname = ANY($1::text[]) "
        "ORDER BY table_rel.relname, index_rel.relname",
        ["pipeline_runs", "projects"],
    )
    constraints = await connection.fetch(
        "SELECT table_rel.relname AS table_name, "
        "constraint_data.conname, "
        "pg_get_constraintdef(constraint_data.oid) AS definition "
        "FROM pg_constraint constraint_data "
        "JOIN pg_class table_rel ON table_rel.oid = constraint_data.conrelid "
        "WHERE table_rel.relnamespace = 'public'::regnamespace "
        "AND table_rel.relname = ANY($1::text[]) "
        "ORDER BY table_name, constraint_data.conname",
        ["pipeline_runs", "projects"],
    )
    return {
        "defaults": [tuple(row.values()) for row in defaults],
        "indexes": [tuple(row.values()) for row in indexes],
        "constraints": [tuple(row.values()) for row in constraints],
    }


async def _enum_labels(connection: asyncpg.Connection, type_name: str) -> list[str]:
    rows = await connection.fetch(
        "SELECT enum_data.enumlabel "
        "FROM pg_enum enum_data "
        "JOIN pg_type type_data ON type_data.oid = enum_data.enumtypid "
        "WHERE type_data.typname = $1 ORDER BY enum_data.enumsortorder",
        type_name,
    )
    return [row["enumlabel"] for row in rows]


@pytest.mark.asyncio
async def test_0017_blocked_status_upgrade_and_downgrade_preserve_failures():
    admin_url = os.environ["TEST_POSTGRES_ADMIN_URL"].replace(
        "postgresql+asyncpg://", "postgresql://"
    )
    database_name = f"seo_migration_0017_{uuid.uuid4().hex[:12]}"
    connection = await asyncpg.connect(admin_url)
    try:
        await connection.execute(f'CREATE DATABASE "{database_name}"')
    finally:
        await connection.close()

    base_url = admin_url.rsplit("/", 1)[0]
    database_url = f"{base_url}/{database_name}".replace(
        "postgresql://", "postgresql+asyncpg://", 1
    )
    environment = {**os.environ, "DATABASE_URL": database_url}
    backend = Path(__file__).parents[1]
    failed_project_id, blocked_project_id = uuid.uuid4(), uuid.uuid4()
    failed_run_id, blocked_run_id = uuid.uuid4(), uuid.uuid4()
    try:
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "0016"],
            cwd=backend,
            env=environment,
            check=True,
        )
        database = await asyncpg.connect(f"{base_url}/{database_name}")
        try:
            for project_id, status in (
                (failed_project_id, "failed"),
                (blocked_project_id, "running"),
            ):
                await database.execute(
                    "INSERT INTO projects (id, name, topic, search_intent, audience, "
                    "language, status, current_stage, research_cycles, editor_cycles, "
                    "content_type, event_sequence) VALUES "
                    "($1, 'Migration project', 'Topic', 'informational', 'Editors', "
                    "'pt-BR', $2::projectstatus, 'research_gatekeeper', 0, 0, "
                    "'article', 0)",
                    project_id,
                    status,
                )
            for run_id, project_id, status, key in (
                (failed_run_id, failed_project_id, "failed", "failed-history"),
                (blocked_run_id, blocked_project_id, "running", "policy-block"),
            ):
                await database.execute(
                    "INSERT INTO pipeline_runs (id, project_id, status, trigger_type, "
                    "current_stage, attempt, idempotency_key) VALUES "
                    "($1, $2, $3::pipelinerunstatus, 'api', "
                    "'research_gatekeeper', 1, $4)",
                    run_id,
                    project_id,
                    status,
                    key,
                )
        finally:
            await database.close()

        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "0017"],
            cwd=backend,
            env=environment,
            check=True,
        )
        database = await asyncpg.connect(f"{base_url}/{database_name}")
        try:
            assert "blocked" in await _enum_labels(database, "projectstatus")
            assert "blocked" in await _enum_labels(database, "pipelinerunstatus")
            await database.execute(
                "UPDATE projects SET status = 'blocked' WHERE id = $1",
                blocked_project_id,
            )
            await database.execute(
                "UPDATE pipeline_runs SET status = 'blocked' WHERE id = $1",
                blocked_run_id,
            )
        finally:
            await database.close()

        subprocess.run(
            [sys.executable, "-m", "alembic", "downgrade", "0016"],
            cwd=backend,
            env=environment,
            check=True,
        )
        database = await asyncpg.connect(f"{base_url}/{database_name}")
        try:
            assert "blocked" not in await _enum_labels(database, "projectstatus")
            assert "blocked" not in await _enum_labels(database, "pipelinerunstatus")
            assert (
                await database.fetchval(
                    "SELECT status::text FROM projects WHERE id = $1",
                    blocked_project_id,
                )
                == "failed"
            )
            assert (
                await database.fetchval(
                    "SELECT status::text FROM pipeline_runs WHERE id = $1",
                    blocked_run_id,
                )
                == "failed"
            )
            assert (
                await database.fetchval(
                    "SELECT status::text FROM projects WHERE id = $1", failed_project_id
                )
                == "failed"
            )
            assert (
                await database.fetchval(
                    "SELECT status::text FROM pipeline_runs WHERE id = $1",
                    failed_run_id,
                )
                == "failed"
            )
        finally:
            await database.close()
    finally:
        connection = await asyncpg.connect(admin_url)
        try:
            await connection.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = $1 AND pid <> pg_backend_pid()",
                database_name,
            )
            await connection.execute(f'DROP DATABASE IF EXISTS "{database_name}"')
        finally:
            await connection.close()


@pytest.mark.asyncio
async def test_0018_migrates_editorial_routes_and_adds_provider_diagnostics():
    admin_url = os.environ["TEST_POSTGRES_ADMIN_URL"].replace(
        "postgresql+asyncpg://", "postgresql://"
    )
    database_name = f"seo_migration_0018_{uuid.uuid4().hex[:12]}"
    connection = await asyncpg.connect(admin_url)
    try:
        await connection.execute(f'CREATE DATABASE "{database_name}"')
    finally:
        await connection.close()

    base_url = admin_url.rsplit("/", 1)[0]
    database_url = f"{base_url}/{database_name}".replace(
        "postgresql://", "postgresql+asyncpg://", 1
    )
    environment = {**os.environ, "DATABASE_URL": database_url}
    backend = Path(__file__).parents[1]
    roles = (
        "planner",
        "researcher",
        "research_gatekeeper",
        "writer",
        "editor",
        "skill_curator",
    )
    try:
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "0017"],
            cwd=backend,
            env=environment,
            check=True,
        )
        database = await asyncpg.connect(f"{base_url}/{database_name}")
        try:
            for role in roles:
                await database.execute(
                    "INSERT INTO model_routes (id, agent_role, primary_provider, "
                    "primary_model, fallback_provider, fallback_model, parameters) "
                    "VALUES ($1, $2, 'openai', 'legacy-model', 'anthropic', "
                    "'legacy-fallback', '{\"max_retries\": 9}'::jsonb)",
                    uuid.uuid4(),
                    role,
                )
        finally:
            await database.close()

        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "0018"],
            cwd=backend,
            env=environment,
            check=True,
        )
        database = await asyncpg.connect(f"{base_url}/{database_name}")
        try:
            routes = await database.fetch(
                "SELECT agent_role, primary_provider, primary_model, "
                "fallback_provider, fallback_model, parameters->>'max_retries' "
                "AS max_retries FROM model_routes ORDER BY agent_role"
            )
            assert {row["agent_role"] for row in routes} == set(roles)
            assert all(row["primary_provider"] == "gemini" for row in routes)
            assert all(row["primary_model"] == "gemini-3.5-flash" for row in routes)
            assert all(row["fallback_provider"] is None for row in routes)
            assert all(row["fallback_model"] is None for row in routes)
            assert all(row["max_retries"] == "2" for row in routes)
            columns = await database.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'agent_runs' "
                "AND column_name = ANY($1::text[])",
                [
                    "error_code",
                    "error_category",
                    "http_status",
                    "retryable",
                    "correlation_id",
                ],
            )
            assert {row["column_name"] for row in columns} == {
                "error_code",
                "error_category",
                "http_status",
                "retryable",
                "correlation_id",
            }
            await database.execute(
                "UPDATE model_routes SET primary_model = 'gemini-3-flash-preview', "
                "fallback_provider = 'openai', fallback_model = 'legacy-fallback' "
                "WHERE agent_role = 'skill_curator'"
            )
        finally:
            await database.close()

        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "0019"],
            cwd=backend,
            env=environment,
            check=True,
        )
        database = await asyncpg.connect(f"{base_url}/{database_name}")
        try:
            repaired = await database.fetchrow(
                "SELECT primary_provider, primary_model, fallback_provider, "
                "fallback_model FROM model_routes WHERE agent_role = 'skill_curator'"
            )
            assert repaired is not None
            assert repaired["primary_provider"] == "gemini"
            assert repaired["primary_model"] == "gemini-3.5-flash"
            assert repaired["fallback_provider"] is None
            assert repaired["fallback_model"] is None
        finally:
            await database.close()

        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "0021"],
            cwd=backend,
            env=environment,
            check=True,
        )
        database = await asyncpg.connect(f"{base_url}/{database_name}")
        try:
            migrated = await database.fetch(
                "SELECT agent_role, primary_provider, primary_model, "
                "fallback_provider, fallback_model FROM model_routes "
                "ORDER BY agent_role"
            )
            assert {row["agent_role"] for row in migrated} == set(roles)
            assert all(row["primary_provider"] == "openai" for row in migrated)
            assert all(row["primary_model"] == "gpt-4o-mini" for row in migrated)
            assert all(row["fallback_provider"] is None for row in migrated)
            assert all(row["fallback_model"] is None for row in migrated)
        finally:
            await database.close()

        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "0022"],
            cwd=backend,
            env=environment,
            check=True,
        )
        database = await asyncpg.connect(f"{base_url}/{database_name}")
        try:
            routed = {
                row["agent_role"]: row
                for row in await database.fetch(
                    "SELECT agent_role, primary_provider, primary_model, "
                    "fallback_provider, fallback_model FROM model_routes"
                )
            }
            assert routed["researcher"]["primary_model"] == "gpt-4o-mini"
            assert routed["writer"]["primary_model"] == "gpt-5-mini"
            assert routed["editor"]["primary_model"] == "gpt-5-mini"
            for role in (
                "planner",
                "research_gatekeeper",
                "skill_curator",
            ):
                assert routed[role]["primary_model"] == "gpt-4.1-mini"
            assert all(
                row["primary_provider"] == "openai"
                and row["fallback_provider"] is None
                and row["fallback_model"] is None
                for row in routed.values()
            )
            recovery_columns = await database.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'agent_runs' "
                "AND column_name = ANY($1::text[])",
                ["recovered", "recovery_code", "recovered_by_agent_run_id"],
            )
            assert {row["column_name"] for row in recovery_columns} == {
                "recovered",
                "recovery_code",
                "recovered_by_agent_run_id",
            }
            seo_brief_column = await database.fetchval(
                "SELECT COUNT(*) FROM information_schema.columns "
                "WHERE table_schema = 'public' "
                "AND table_name = 'research_plans' "
                "AND column_name = 'seo_brief'"
            )
            assert seo_brief_column == 1
        finally:
            await database.close()
    finally:
        connection = await asyncpg.connect(admin_url)
        try:
            await connection.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = $1 AND pid <> pg_backend_pid()",
                database_name,
            )
            await connection.execute(f'DROP DATABASE IF EXISTS "{database_name}"')
        finally:
            await connection.close()


@pytest.mark.asyncio
async def test_empty_database_upgrade_and_supported_downgrade_cycle():
    admin_url = os.environ["TEST_POSTGRES_ADMIN_URL"].replace(
        "postgresql+asyncpg://", "postgresql://"
    )
    database_name = f"seo_migration_{uuid.uuid4().hex[:12]}"
    connection = await asyncpg.connect(admin_url)
    try:
        server_version_num = int(await connection.fetchval("SHOW server_version_num"))
        assert server_version_num // 10_000 == EXPECTED_POSTGRES_MAJOR
        await connection.execute(f'CREATE DATABASE "{database_name}"')
    finally:
        await connection.close()

    base_url = admin_url.rsplit("/", 1)[0]
    database_url = f"{base_url}/{database_name}".replace(
        "postgresql://", "postgresql+asyncpg://", 1
    )
    environment = {**os.environ, "DATABASE_URL": database_url}
    backend = Path(__file__).parents[1]
    project_id = uuid.uuid4()
    run_id = uuid.uuid4()
    event_id = uuid.uuid4()
    source_id = uuid.uuid4()
    snapshot_id = uuid.uuid4()
    try:
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "0007"],
            cwd=backend,
            env=environment,
            check=True,
        )
        legacy = await asyncpg.connect(f"{base_url}/{database_name}")
        try:
            await legacy.execute(
                """
                INSERT INTO projects (
                  id, name, topic, search_intent, audience, language, status,
                  current_stage, research_cycles, editor_cycles, event_sequence
                ) VALUES ($1, 'Legacy', 'Legacy topic', 'informational',
                  'auditors', 'pt-BR', 'draft', 'planner', 0, 0, 1)
                """,
                project_id,
            )
            await legacy.execute(
                """
                INSERT INTO pipeline_runs (
                  id, project_id, status, trigger_type, current_stage,
                  attempt, idempotency_key
                ) VALUES ($1, $2, 'queued', 'api', 'planner', 1, 'legacy-run')
                """,
                run_id,
                project_id,
            )
            await legacy.execute(
                """
                INSERT INTO pipeline_events (
                  id, project_id, pipeline_run_id, sequence, event_type,
                  stage, payload, idempotency_key
                ) VALUES ($1, $2, $3, 1, 'legacy.event', 'planner', '{}',
                  'legacy-event')
                """,
                event_id,
                project_id,
                run_id,
            )
            await legacy.execute(
                """
                INSERT INTO sources (
                  id, canonical_url, title, publisher, source_type,
                  published_at, accessed_at, content_hash, snapshot_text,
                  reliability_score, metadata_json
                ) VALUES (
                  $1, 'https://user:secret@legacy.example/article?id=2&access_token=private',
                  'Legacy captured title',
                  'Legacy Press', 'news', '2026-06-01T00:00:00Z',
                  '2026-06-02T00:00:00Z', $2, 'Legacy captured evidence body',
                  0.88, '{"author":"Legacy Author"}'::jsonb
                )
                """,
                source_id,
                "d" * 64,
            )
            await legacy.execute(
                """
                INSERT INTO source_snapshots (
                  id, source_id, pipeline_run_id, content_hash, snapshot_text,
                  accessed_at, metadata_json
                ) VALUES (
                  $1, $2, $3, $4, 'Legacy captured evidence body',
                  '2026-06-02T00:00:00Z',
                  jsonb_build_object(
                    'canonical_url',
                    'https://user:secret@legacy.example/article?id=2&access_token=private',
                    'extraction_method', 'legacy_fetch'
                  )
                )
                """,
                snapshot_id,
                source_id,
                run_id,
                "d" * 64,
            )
        finally:
            await legacy.close()
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=backend,
            env=environment,
            check=True,
        )
        migrated = await asyncpg.connect(f"{base_url}/{database_name}")
        try:
            vector_version = await migrated.fetchval(
                "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
            )
            assert vector_version
            alembic_head = await migrated.fetchval(
                "SELECT version_num FROM alembic_version"
            )
            assert alembic_head == EXPECTED_ALEMBIC_HEAD
            lifecycle_tables = await migrated.fetch(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = ANY($1::text[])",
                [
                    "skill_validations",
                    "skill_lifecycle_events",
                    "human_editorial_reviews",
                    "execution_manifests",
                    "quality_evaluations",
                    "publication_profiles",
                ],
            )
            assert {row["table_name"] for row in lifecycle_tables} == {
                "skill_validations",
                "skill_lifecycle_events",
                "human_editorial_reviews",
                "execution_manifests",
                "quality_evaluations",
                "publication_profiles",
            }
            project_context_columns = {
                row["column_name"]
                for row in await migrated.fetch(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'projects' "
                    "AND column_name = ANY($1::text[])",
                    ["publication_profile_id", "briefing"],
                )
            }
            assert project_context_columns == {
                "publication_profile_id",
                "briefing",
            }
            immutable_trigger = await migrated.fetchval(
                "SELECT EXISTS ("
                "SELECT 1 FROM pg_trigger "
                "WHERE tgname = 'execution_manifests_immutable' AND NOT tgisinternal)"
            )
            assert immutable_trigger is True
            quality_immutable_trigger = await migrated.fetchval(
                "SELECT EXISTS ("
                "SELECT 1 FROM pg_trigger "
                "WHERE tgname = 'quality_evaluations_immutable' AND NOT tgisinternal)"
            )
            assert quality_immutable_trigger is True
            snapshot_immutable_trigger = await migrated.fetchval(
                "SELECT EXISTS ("
                "SELECT 1 FROM pg_trigger "
                "WHERE tgname = 'source_snapshots_immutable' AND NOT tgisinternal)"
            )
            assert snapshot_immutable_trigger is True
            provenance_columns = await migrated.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'source_snapshots' "
                "AND column_name = ANY($1::text[])",
                [
                    "title",
                    "author",
                    "publisher",
                    "published_at",
                    "canonical_url",
                    "domain",
                    "source_type",
                    "reliability_score",
                    "extraction_method",
                ],
            )
            assert {row["column_name"] for row in provenance_columns} == {
                "title",
                "author",
                "publisher",
                "published_at",
                "canonical_url",
                "domain",
                "source_type",
                "reliability_score",
                "extraction_method",
            }
            provenance = await migrated.fetchrow(
                """
                SELECT title, author, publisher, published_at, canonical_url,
                       domain, source_type, reliability_score, extraction_method,
                       content_hash, accessed_at
                FROM source_snapshots WHERE id = $1
                """,
                snapshot_id,
            )
            assert provenance is not None
            assert provenance["title"] == "Legacy captured title"
            assert provenance["author"] == "Legacy Author"
            assert provenance["canonical_url"] == "https://legacy.example/article?id=2"
            assert provenance["domain"] == "legacy.example"
            assert provenance["reliability_score"] == 0.88
            assert provenance["extraction_method"] == "legacy_fetch"
            assert provenance["content_hash"] == "d" * 64
            await migrated.execute(
                "UPDATE sources SET title = 'Later aggregate title', "
                "reliability_score = 0.1 WHERE id = $1",
                source_id,
            )
            unchanged = await migrated.fetchrow(
                "SELECT title, reliability_score FROM source_snapshots WHERE id = $1",
                snapshot_id,
            )
            assert unchanged["title"] == "Legacy captured title"
            assert unchanged["reliability_score"] == 0.88
            with pytest.raises(asyncpg.PostgresError) as immutable_error:
                await migrated.execute(
                    "UPDATE source_snapshots SET title = 'Forbidden' WHERE id = $1",
                    snapshot_id,
                )
            assert immutable_error.value.sqlstate == "55000"
            lifecycle_columns = await migrated.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'skills' "
                "AND column_name = ANY($1::text[])",
                ["project_id", "fingerprint", "lifecycle_status", "auto_inject"],
            )
            assert {row["column_name"] for row in lifecycle_columns} == {
                "project_id",
                "fingerprint",
                "lifecycle_status",
                "auto_inject",
            }
            labels = await migrated.fetch(
                "SELECT enumlabel FROM pg_enum "
                "JOIN pg_type ON pg_type.oid = pg_enum.enumtypid "
                "WHERE pg_type.typname = 'pipelinerunstatus' "
                "ORDER BY enumsortorder"
            )
            pipeline_labels = {row["enumlabel"] for row in labels}
            assert {
                "needs_review",
                "needs_human_approval",
                "rejected",
            }.issubset(pipeline_labels)
            project_labels = await migrated.fetch(
                "SELECT enumlabel FROM pg_enum "
                "JOIN pg_type ON pg_type.oid = pg_enum.enumtypid "
                "WHERE pg_type.typname = 'projectstatus'"
            )
            assert {"needs_human_approval", "rejected"}.issubset(
                {row["enumlabel"] for row in project_labels}
            )
            event = await migrated.fetchrow(
                """
                SELECT stage_occurrence_id, research_cycle, editor_cycle,
                       run_attempt, stage_attempt, checkpoint_sequence,
                       agent_run_id
                FROM pipeline_events WHERE id = $1
                """,
                event_id,
            )
            assert event is not None
            assert all(value is None for value in event.values())
            dispatch = await migrated.fetchrow(
                """
                SELECT dispatch_token, dispatch_status, dispatch_claimed_at,
                       dispatch_expires_at, dispatch_attempt,
                       dispatch_not_before, last_dispatch_error,
                       last_dispatched_at, celery_task_id,
                       cancellation_requested_at
                FROM pipeline_runs WHERE id = $1
                """,
                run_id,
            )
            assert dispatch is not None
            assert dispatch["dispatch_attempt"] == 0
            assert all(
                value is None
                for key, value in dispatch.items()
                if key != "dispatch_attempt"
            )
        finally:
            await migrated.close()
        subprocess.run(
            [sys.executable, "-m", "alembic", "downgrade", "0006"],
            cwd=backend,
            env=environment,
            check=True,
        )
        downgraded = await asyncpg.connect(f"{base_url}/{database_name}")
        try:
            labels = await downgraded.fetch(
                "SELECT enumlabel FROM pg_enum "
                "JOIN pg_type ON pg_type.oid = pg_enum.enumtypid "
                "WHERE pg_type.typname = 'pipelinerunstatus'"
            )
            assert "needs_review" not in {row["enumlabel"] for row in labels}
        finally:
            await downgraded.close()
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=backend,
            env=environment,
            check=True,
        )
        subprocess.run(
            [sys.executable, "-m", "alembic", "downgrade", "0003"],
            cwd=backend,
            env=environment,
            check=True,
        )
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=backend,
            env=environment,
            check=True,
        )
    finally:
        connection = await asyncpg.connect(admin_url)
        try:
            await connection.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = $1 AND pid <> pg_backend_pid()",
                database_name,
            )
            await connection.execute(f'DROP DATABASE IF EXISTS "{database_name}"')
        finally:
            await connection.close()


@pytest.mark.asyncio
async def test_human_editorial_0012_downgrade_cycle_preserves_schema_and_data():
    admin_url = os.environ["TEST_POSTGRES_ADMIN_URL"].replace(
        "postgresql+asyncpg://", "postgresql://"
    )
    database_name = f"seo_migration_0012_{uuid.uuid4().hex[:12]}"
    connection = await asyncpg.connect(admin_url)
    try:
        server_version_num = int(await connection.fetchval("SHOW server_version_num"))
        assert server_version_num // 10_000 == EXPECTED_POSTGRES_MAJOR
        await connection.execute(f'CREATE DATABASE "{database_name}"')
    finally:
        await connection.close()

    base_url = admin_url.rsplit("/", 1)[0]
    database_url = f"{base_url}/{database_name}".replace(
        "postgresql://", "postgresql+asyncpg://", 1
    )
    environment = {**os.environ, "DATABASE_URL": database_url}
    backend = Path(__file__).parents[1]
    project_statuses = [
        "draft",
        "queued",
        "running",
        "needs_review",
        "needs_human_approval",
        "completed",
        "rejected",
        "failed",
    ]
    pipeline_statuses = [
        "queued",
        "running",
        "waiting_retry",
        "needs_review",
        "needs_human_approval",
        "failed",
        "cancelled",
        "completed",
        "rejected",
    ]
    project_expectations: dict[uuid.UUID, str] = {}
    pipeline_expectations: dict[uuid.UUID, str] = {}

    try:
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "0011"],
            cwd=backend,
            env=environment,
            check=True,
        )
        migrated = await asyncpg.connect(f"{base_url}/{database_name}")
        try:
            schema_0011 = await _status_schema(migrated)
            assert schema_0011["defaults"] == [
                (
                    "pipeline_runs",
                    "pipelinerunstatus",
                    "'queued'::pipelinerunstatus",
                    "NO",
                ),
                ("projects", "projectstatus", None, "NO"),
            ]
            dispatch_index = next(
                index
                for index in schema_0011["indexes"]
                if index[1] == "ix_pipeline_runs_dispatch_eligibility"
            )
            assert dispatch_index[3] is not None
            assert "'queued'::pipelinerunstatus" in dispatch_index[3]
            assert "'waiting_retry'::pipelinerunstatus" in dispatch_index[3]
            assert all(index[4] is True for index in schema_0011["indexes"])
        finally:
            await migrated.close()

        for cycle in range(2):
            subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", "0012"],
                cwd=backend,
                env=environment,
                check=True,
            )
            migrated = await asyncpg.connect(f"{base_url}/{database_name}")
            try:
                assert (
                    await migrated.fetchval("SELECT version_num FROM alembic_version")
                    == "0012"
                )
                assert await migrated.fetchval(
                    "SELECT to_regclass('public.human_editorial_reviews') IS NOT NULL"
                )
                assert await _enum_labels(migrated, "projectstatus") == [
                    "draft",
                    "queued",
                    "running",
                    "needs_review",
                    "needs_human_approval",
                    "completed",
                    "rejected",
                    "failed",
                ]
                assert await _enum_labels(migrated, "pipelinerunstatus") == [
                    "queued",
                    "running",
                    "waiting_retry",
                    "needs_review",
                    "needs_human_approval",
                    "failed",
                    "cancelled",
                    "completed",
                    "rejected",
                ]
                assert await _status_schema(migrated) == schema_0011

                cycle_project_ids: list[uuid.UUID] = []
                for status in project_statuses:
                    project_id = uuid.uuid4()
                    cycle_project_ids.append(project_id)
                    project_expectations[project_id] = {
                        "needs_human_approval": "needs_review",
                        "rejected": "failed",
                    }.get(status, status)
                    await migrated.execute(
                        """
                        INSERT INTO projects (
                          id, name, topic, search_intent, audience, language,
                          status, current_stage, research_cycles, editor_cycles,
                          event_sequence
                        ) VALUES (
                          $1, $2, 'Migration topic', 'informational', 'auditors',
                          'pt-BR', $3::text::projectstatus, 'planner', 0, 0, 0
                        )
                        """,
                        project_id,
                        f"0012 cycle {cycle} {status}",
                        status,
                    )

                for position, status in enumerate(pipeline_statuses):
                    run_id = uuid.uuid4()
                    pipeline_expectations[run_id] = {
                        "needs_human_approval": "needs_review",
                        "rejected": "failed",
                    }.get(status, status)
                    await migrated.execute(
                        """
                        INSERT INTO pipeline_runs (
                          id, project_id, status, trigger_type, current_stage,
                          attempt, idempotency_key
                        ) VALUES (
                          $1, $2, $3::text::pipelinerunstatus, 'api', 'planner',
                          1, $4
                        )
                        """,
                        run_id,
                        cycle_project_ids[position % len(cycle_project_ids)],
                        status,
                        f"0012-cycle-{cycle}-{status}",
                    )

                project_values = await migrated.fetch(
                    "SELECT DISTINCT status::text AS status FROM projects"
                )
                assert {row["status"] for row in project_values} == set(
                    project_statuses
                )
                pipeline_values = await migrated.fetch(
                    "SELECT DISTINCT status::text AS status FROM pipeline_runs"
                )
                assert {row["status"] for row in pipeline_values} == set(
                    pipeline_statuses
                )
            finally:
                await migrated.close()

            subprocess.run(
                [sys.executable, "-m", "alembic", "downgrade", "0011"],
                cwd=backend,
                env=environment,
                check=True,
            )
            migrated = await asyncpg.connect(f"{base_url}/{database_name}")
            try:
                assert (
                    await migrated.fetchval("SELECT version_num FROM alembic_version")
                    == "0011"
                )
                assert not await migrated.fetchval(
                    "SELECT to_regclass('public.human_editorial_reviews') IS NOT NULL"
                )
                assert await _enum_labels(migrated, "projectstatus") == [
                    "draft",
                    "queued",
                    "running",
                    "needs_review",
                    "completed",
                    "failed",
                ]
                assert await _enum_labels(migrated, "pipelinerunstatus") == [
                    "queued",
                    "running",
                    "waiting_retry",
                    "needs_review",
                    "failed",
                    "cancelled",
                    "completed",
                ]
                assert await _status_schema(migrated) == schema_0011

                project_rows = await migrated.fetch(
                    "SELECT id, status::text AS status FROM projects "
                    "WHERE id = ANY($1::uuid[])",
                    list(project_expectations),
                )
                assert {
                    row["id"]: row["status"] for row in project_rows
                } == project_expectations
                pipeline_rows = await migrated.fetch(
                    "SELECT id, status::text AS status FROM pipeline_runs "
                    "WHERE id = ANY($1::uuid[])",
                    list(pipeline_expectations),
                )
                assert {
                    row["id"]: row["status"] for row in pipeline_rows
                } == pipeline_expectations
            finally:
                await migrated.close()
    finally:
        connection = await asyncpg.connect(admin_url)
        try:
            await connection.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = $1 AND pid <> pg_backend_pid()",
                database_name,
            )
            await connection.execute(f'DROP DATABASE IF EXISTS "{database_name}"')
        finally:
            await connection.close()


@pytest.mark.asyncio
async def test_0016_seals_approved_content_and_preserves_editable_new_versions():
    admin_url = os.environ["TEST_POSTGRES_ADMIN_URL"].replace(
        "postgresql+asyncpg://", "postgresql://"
    )
    database_name = f"seo_migration_0016_{uuid.uuid4().hex[:12]}"
    connection = await asyncpg.connect(admin_url)
    try:
        server_version_num = int(await connection.fetchval("SHOW server_version_num"))
        assert server_version_num // 10_000 == EXPECTED_POSTGRES_MAJOR
        await connection.execute(f'CREATE DATABASE "{database_name}"')
    finally:
        await connection.close()

    base_url = admin_url.rsplit("/", 1)[0]
    database_url = f"{base_url}/{database_name}".replace(
        "postgresql://", "postgresql+asyncpg://", 1
    )
    environment = {**os.environ, "DATABASE_URL": database_url}
    backend = Path(__file__).parents[1]
    project_id = uuid.uuid4()
    approved_run_id = uuid.uuid4()
    draft_run_id = uuid.uuid4()
    article_id = uuid.uuid4()
    approved_version_id = uuid.uuid4()
    draft_version_id = uuid.uuid4()
    review_id = uuid.uuid4()

    try:
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "0015"],
            cwd=backend,
            env=environment,
            check=True,
        )
        legacy = await asyncpg.connect(f"{base_url}/{database_name}")
        try:
            await legacy.execute(
                """
                INSERT INTO projects (
                  id, name, topic, search_intent, audience, language, status,
                  current_stage, research_cycles, editor_cycles, event_sequence
                ) VALUES (
                  $1, 'Editorial seal', 'Immutable review', 'informational',
                  'auditors', 'pt-BR', 'completed', 'completed', 0, 0, 0
                )
                """,
                project_id,
            )
            await legacy.execute(
                """
                INSERT INTO pipeline_runs (
                  id, project_id, status, trigger_type, current_stage,
                  attempt, idempotency_key
                ) VALUES
                  ($1, $3, 'completed', 'api', 'completed', 1, 'approved-run'),
                  ($2, $3, 'queued', 'api', 'planner', 1, 'draft-run')
                """,
                approved_run_id,
                draft_run_id,
                project_id,
            )
            await legacy.execute(
                """
                INSERT INTO articles (
                  id, project_id, current_version, status, final_markdown,
                  final_html, seo_metadata, source_report, active_pipeline_run_id
                ) VALUES (
                  $1, $2, 2, 'draft', '# New draft', '<h1>New draft</h1>',
                  '{"title":"New draft"}'::jsonb, '{}'::jsonb, $3
                )
                """,
                article_id,
                project_id,
                draft_run_id,
            )
            await legacy.execute(
                """
                INSERT INTO article_versions (
                  id, article_id, pipeline_run_id, idempotency_key, version,
                  title, outline, editorial_status, change_reason,
                  final_markdown, final_html, seo_metadata, source_report
                ) VALUES
                  ($1, $3, $4, 'approved-v1', 1, 'Approved title',
                   '["Approved outline"]'::jsonb, 'human_approved', 'reviewed',
                   '# Approved', '<h1>Approved</h1>',
                   '{"title":"Approved SEO"}'::jsonb,
                   '{"unsupported_claim_count":0}'::jsonb),
                  ($2, $3, $5, 'draft-v2', 2, 'Draft title',
                   '["Draft outline"]'::jsonb, 'pending', 'new run',
                   '# New draft', '<h1>New draft</h1>',
                   '{"title":"Draft SEO"}'::jsonb,
                   '{"unsupported_claim_count":0}'::jsonb)
                """,
                approved_version_id,
                draft_version_id,
                article_id,
                approved_run_id,
                draft_run_id,
            )
            await legacy.execute(
                """
                INSERT INTO human_editorial_reviews (
                  id, project_id, pipeline_run_id, article_version_id,
                  reviewer, decision, review_package_json, reviewed_at
                ) VALUES (
                  $1, $2, $3, $4, 'Migration reviewer', 'approved',
                  jsonb_build_object(
                    'article_version_id', $4::uuid,
                    'article_version', 1,
                    'pipeline_run_id', $3::uuid,
                    'changes', jsonb_build_object(
                      'current_title', 'Approved title'
                    )
                  ), now()
                )
                """,
                review_id,
                project_id,
                approved_run_id,
                approved_version_id,
            )
        finally:
            await legacy.close()

        for _cycle in range(2):
            subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", "0016"],
                cwd=backend,
                env=environment,
                check=True,
            )
            migrated = await asyncpg.connect(f"{base_url}/{database_name}")
            try:
                assert (
                    await migrated.fetchval("SELECT version_num FROM alembic_version")
                    == "0016"
                )
                trigger_names = {
                    row["tgname"]
                    for row in await migrated.fetch(
                        "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal "
                        "AND tgname = ANY($1::text[])",
                        [
                            "article_versions_sealed_immutable",
                            "human_editorial_review_packages_immutable",
                        ],
                    )
                }
                assert trigger_names == {
                    "article_versions_sealed_immutable",
                    "human_editorial_review_packages_immutable",
                }
                approved = await migrated.fetchrow(
                    """
                    SELECT pipeline_run_id, version, outline::text AS outline,
                           final_markdown, final_html,
                           seo_metadata::text AS seo_metadata,
                           source_report::text AS source_report,
                           content_checksum, sealed_at
                    FROM article_versions WHERE id = $1
                    """,
                    approved_version_id,
                )
                expected_content_checksum = canonical_checksum(
                    {
                        "final_html": approved["final_html"],
                        "final_markdown": approved["final_markdown"],
                        "outline": json.loads(approved["outline"]),
                        "pipeline_run_id": str(approved["pipeline_run_id"]),
                        "seo_metadata": json.loads(approved["seo_metadata"]),
                        "source_report": json.loads(approved["source_report"]),
                        "version_number": approved["version"],
                    }
                )
                assert approved["content_checksum"] == expected_content_checksum
                assert approved["sealed_at"] is not None
                package_text = await migrated.fetchval(
                    "SELECT review_package_json::text "
                    "FROM human_editorial_reviews WHERE id = $1",
                    review_id,
                )
                package = json.loads(package_text)
                assert package["article_version_checksum"] == expected_content_checksum
                assert await migrated.fetchval(
                    "SELECT review_package_checksum "
                    "FROM human_editorial_reviews WHERE id = $1",
                    review_id,
                ) == canonical_checksum(package)

                for statement in (
                    "UPDATE article_versions SET final_markdown = '# Mutated' "
                    "WHERE id = $1",
                    'UPDATE article_versions SET seo_metadata = \'{"title":"Mutated"}\' '
                    "WHERE id = $1",
                ):
                    with pytest.raises(asyncpg.PostgresError) as immutable_error:
                        await migrated.execute(statement, approved_version_id)
                    assert immutable_error.value.sqlstate == "55000"
                with pytest.raises(asyncpg.PostgresError) as package_error:
                    await migrated.execute(
                        "UPDATE human_editorial_reviews SET review_package_json = "
                        "review_package_json || '{\"tampered\":true}'::jsonb "
                        "WHERE id = $1",
                        review_id,
                    )
                assert package_error.value.sqlstate == "55000"

                draft = await migrated.fetchrow(
                    """
                    SELECT pipeline_run_id, version, outline::text AS outline,
                           final_html, seo_metadata::text AS seo_metadata,
                           source_report::text AS source_report, sealed_at
                    FROM article_versions WHERE id = $1
                    """,
                    draft_version_id,
                )
                evolved_markdown = "# Draft evolved"
                evolved_checksum = canonical_checksum(
                    {
                        "final_html": draft["final_html"],
                        "final_markdown": evolved_markdown,
                        "outline": json.loads(draft["outline"]),
                        "pipeline_run_id": str(draft["pipeline_run_id"]),
                        "seo_metadata": json.loads(draft["seo_metadata"]),
                        "source_report": json.loads(draft["source_report"]),
                        "version_number": draft["version"],
                    }
                )
                assert draft["sealed_at"] is None
                await migrated.execute(
                    "UPDATE article_versions SET final_markdown = $1, "
                    "content_checksum = $2 WHERE id = $3",
                    evolved_markdown,
                    evolved_checksum,
                    draft_version_id,
                )
                assert (
                    await migrated.fetchval(
                        "SELECT final_markdown FROM article_versions WHERE id = $1",
                        draft_version_id,
                    )
                    == evolved_markdown
                )
            finally:
                await migrated.close()

            subprocess.run(
                [sys.executable, "-m", "alembic", "downgrade", "0015"],
                cwd=backend,
                env=environment,
                check=True,
            )
            downgraded = await asyncpg.connect(f"{base_url}/{database_name}")
            try:
                columns = {
                    row["column_name"]
                    for row in await downgraded.fetch(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = 'public' "
                        "AND table_name = ANY($1::text[]) "
                        "AND column_name = ANY($2::text[])",
                        ["article_versions", "human_editorial_reviews"],
                        [
                            "content_checksum",
                            "sealed_at",
                            "review_package_checksum",
                        ],
                    )
                }
                assert columns == set()
                assert (
                    await downgraded.fetchval(
                        "SELECT review_package_json ? 'article_version_checksum' "
                        "FROM human_editorial_reviews WHERE id = $1",
                        review_id,
                    )
                    is False
                )
                assert (
                    await downgraded.fetchval(
                        "SELECT final_markdown FROM article_versions WHERE id = $1",
                        draft_version_id,
                    )
                    == "# Draft evolved"
                )
            finally:
                await downgraded.close()

        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=backend,
            env=environment,
            check=True,
        )
    finally:
        connection = await asyncpg.connect(admin_url)
        try:
            await connection.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = $1 AND pid <> pg_backend_pid()",
                database_name,
            )
            await connection.execute(f'DROP DATABASE IF EXISTS "{database_name}"')
        finally:
            await connection.close()
