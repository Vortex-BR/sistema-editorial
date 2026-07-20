"""Add isolated pipeline runs, checkpoints and immutable block revisions."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


content_type = postgresql.ENUM(
    "article",
    "existing_article_update",
    "institutional_page",
    "service_page",
    "landing_page",
    "category_page",
    "product_page",
    "product_description",
    name="contenttype",
    create_type=False,
)
run_status = postgresql.ENUM(
    "queued",
    "running",
    "waiting_retry",
    "failed",
    "cancelled",
    "completed",
    name="pipelinerunstatus",
    create_type=False,
)
trigger_type = postgresql.ENUM(
    "api", "automatic", "retry", "resume", "legacy", name="triggertype", create_type=False
)


def upgrade() -> None:
    bind = op.get_bind()
    content_type.create(bind, checkfirst=True)
    run_status.create(bind, checkfirst=True)
    trigger_type.create(bind, checkfirst=True)

    op.add_column(
        "projects",
        sa.Column("content_type", content_type, nullable=False, server_default="article"),
    )
    op.add_column("projects", sa.Column("creation_idempotency_key", sa.String(160)))
    op.create_unique_constraint(
        "uq_projects_creation_idempotency_key",
        "projects",
        ["creation_idempotency_key"],
    )
    op.add_column(
        "projects", sa.Column("event_sequence", sa.Integer(), nullable=False, server_default="0")
    )
    op.execute(
        """
        UPDATE projects AS p SET event_sequence = COALESCE(
          (SELECT MAX(e.sequence) FROM pipeline_events AS e WHERE e.project_id = p.id), 0
        )
        """
    )

    op.create_table(
        "pipeline_runs",
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", run_status, nullable=False, server_default="queued"),
        sa.Column("trigger_type", trigger_type, nullable=False, server_default="api"),
        sa.Column("current_stage", sa.String(50), nullable=False, server_default="planner"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("failed_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(100)),
        sa.Column("error_message", sa.Text()),
        sa.Column("retryable", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("next_retry_at", sa.DateTime(timezone=True)),
        sa.Column("last_successful_checkpoint", sa.String(50)),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("lock_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_owner", sa.String(160)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("project_id", "idempotency_key", name="uq_pipeline_run_idempotency"),
        sa.CheckConstraint("attempt >= 1", name="pipeline_run_attempt_positive"),
    )
    op.create_index("ix_pipeline_runs_project_id", "pipeline_runs", ["project_id"])
    op.create_index("ix_pipeline_runs_status", "pipeline_runs", ["status"])

    op.execute(
        """
        INSERT INTO pipeline_runs (
          id, project_id, status, trigger_type, current_stage, attempt,
          idempotency_key, started_at, finished_at, failed_at, retryable,
          metadata, lock_version, created_at, updated_at
        )
        SELECT md5(id::text || '-legacy')::uuid, id,
          CASE
            WHEN status::text = 'completed' THEN 'completed'::pipelinerunstatus
            WHEN status::text = 'failed' THEN 'failed'::pipelinerunstatus
            ELSE 'cancelled'::pipelinerunstatus
          END,
          'legacy'::triggertype, current_stage, 1, 'legacy-import',
          created_at,
          CASE WHEN status::text IN ('completed', 'failed') THEN updated_at ELSE NULL END,
          CASE WHEN status::text = 'failed' THEN updated_at ELSE NULL END,
          false, jsonb_build_object('migration', '0004'), 0, created_at, updated_at
        FROM projects
        WHERE EXISTS (
          SELECT 1 FROM research_plans rp WHERE rp.project_id = projects.id
          UNION ALL SELECT 1 FROM agent_runs ar WHERE ar.project_id = projects.id
          UNION ALL SELECT 1 FROM pipeline_events pe WHERE pe.project_id = projects.id
        )
        """
    )

    op.create_table(
        "pipeline_checkpoints",
        sa.Column("pipeline_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stage", sa.String(50), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("contract_version", sa.String(30), nullable=False, server_default="1.0"),
        sa.Column("next_stage", sa.String(50), nullable=False),
        sa.Column("state_json", postgresql.JSONB(), nullable=False),
        sa.Column("result_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("resumable", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["pipeline_run_id"], ["pipeline_runs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("pipeline_run_id", "idempotency_key", name="uq_checkpoint_idempotency"),
    )
    op.create_index("ix_pipeline_checkpoints_pipeline_run_id", "pipeline_checkpoints", ["pipeline_run_id"])

    op.create_table(
        "pipeline_state_transitions",
        sa.Column("pipeline_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_status", sa.String(30), nullable=False),
        sa.Column("to_status", sa.String(30), nullable=False),
        sa.Column("stage", sa.String(50), nullable=False),
        sa.Column("origin", sa.String(80), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("error_code", sa.String(100)),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["pipeline_run_id"], ["pipeline_runs.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_pipeline_state_transitions_pipeline_run_id", "pipeline_state_transitions", ["pipeline_run_id"])
    op.create_index("ix_pipeline_state_transitions_created_at", "pipeline_state_transitions", ["created_at"])

    for table in ("agent_memories", "style_sources", "style_patterns"):
        op.add_column(
            table, sa.Column("origin_pipeline_run_id", postgresql.UUID(as_uuid=True))
        )
        op.create_foreign_key(
            f"fk_{table}_origin_pipeline_run_id",
            table,
            "pipeline_runs",
            ["origin_pipeline_run_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.create_index(
            f"ix_{table}_origin_pipeline_run_id", table, ["origin_pipeline_run_id"]
        )

    for table, delete_rule in (
        ("research_plans", "CASCADE"),
        ("fact_ledger", "CASCADE"),
        ("agent_handoffs", "CASCADE"),
        ("agent_runs", "CASCADE"),
        ("pipeline_events", "CASCADE"),
        ("article_versions", "SET NULL"),
    ):
        op.add_column(table, sa.Column("pipeline_run_id", postgresql.UUID(as_uuid=True)))
        op.create_foreign_key(
            f"fk_{table}_pipeline_run_id",
            table,
            "pipeline_runs",
            ["pipeline_run_id"],
            ["id"],
            ondelete=delete_rule,
        )
        op.create_index(f"ix_{table}_pipeline_run_id", table, ["pipeline_run_id"])
        op.execute(
            f"""
            UPDATE {table} AS child SET pipeline_run_id = legacy.id
            FROM pipeline_runs AS legacy
            WHERE child.project_id = legacy.project_id
              AND legacy.idempotency_key = 'legacy-import'
            """
            if table not in {"article_versions"}
            else """
            UPDATE article_versions AS child SET pipeline_run_id = legacy.id
            FROM articles a, pipeline_runs legacy
            WHERE child.article_id = a.id AND a.project_id = legacy.project_id
              AND legacy.idempotency_key = 'legacy-import'
            """
        )

    op.add_column("agent_handoffs", sa.Column("idempotency_key", sa.String(200)))
    op.add_column("research_plans", sa.Column("idempotency_key", sa.String(160)))
    op.execute("UPDATE research_plans SET idempotency_key = 'legacy-' || id::text")
    op.add_column("agent_runs", sa.Column("idempotency_key", sa.String(200)))
    op.add_column(
        "agent_runs",
        sa.Column("fallback_used", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("pipeline_events", sa.Column("idempotency_key", sa.String(200)))
    op.add_column("article_versions", sa.Column("idempotency_key", sa.String(200)))
    op.add_column("article_versions", sa.Column("final_markdown", sa.Text()))
    op.add_column("article_versions", sa.Column("final_html", sa.Text()))
    op.add_column(
        "article_versions",
        sa.Column(
            "seo_metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "article_versions",
        sa.Column(
            "source_report",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_unique_constraint("uq_handoff_idempotency", "agent_handoffs", ["pipeline_run_id", "idempotency_key"])
    op.create_unique_constraint(
        "uq_research_plan_idempotency",
        "research_plans",
        ["pipeline_run_id", "idempotency_key"],
    )
    op.create_unique_constraint("uq_agent_run_idempotency", "agent_runs", ["pipeline_run_id", "idempotency_key"])
    op.create_unique_constraint("uq_event_idempotency", "pipeline_events", ["pipeline_run_id", "idempotency_key"])
    op.create_unique_constraint("uq_article_version_idempotency", "article_versions", ["article_id", "idempotency_key"])
    op.create_unique_constraint("uq_fact_per_pipeline_run", "fact_ledger", ["pipeline_run_id", "source_id", "claim_text"])
    op.create_index(
        "ix_pipeline_events_run_order",
        "pipeline_events",
        ["project_id", "pipeline_run_id", "sequence", "created_at"],
    )

    op.add_column("articles", sa.Column("content_type", content_type, nullable=False, server_default="article"))
    op.add_column("articles", sa.Column("active_pipeline_run_id", postgresql.UUID(as_uuid=True)))
    op.create_foreign_key(
        "fk_articles_active_pipeline_run_id",
        "articles",
        "pipeline_runs",
        ["active_pipeline_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_articles_active_pipeline_run_id", "articles", ["active_pipeline_run_id"])
    op.execute(
        """
        UPDATE articles a SET active_pipeline_run_id = legacy.id
        FROM pipeline_runs legacy
        WHERE a.project_id = legacy.project_id AND legacy.idempotency_key = 'legacy-import'
        """
    )

    op.add_column("article_blocks", sa.Column("logical_block_id", postgresql.UUID(as_uuid=True)))
    op.add_column("article_blocks", sa.Column("replaces_block_id", postgresql.UUID(as_uuid=True)))
    op.add_column("article_blocks", sa.Column("revision_reason", sa.Text()))
    op.execute("UPDATE article_blocks SET logical_block_id = id WHERE logical_block_id IS NULL")
    op.alter_column("article_blocks", "logical_block_id", nullable=False)
    op.create_foreign_key(
        "fk_article_blocks_replaces_block_id",
        "article_blocks",
        "article_blocks",
        ["replaces_block_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_article_blocks_logical_block_id", "article_blocks", ["logical_block_id"])
    op.create_index("ix_article_blocks_replaces_block_id", "article_blocks", ["replaces_block_id"])

    op.create_table(
        "source_snapshots",
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pipeline_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("snapshot_text", sa.Text(), nullable=False),
        sa.Column("accessed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("reused_from_snapshot_id", postgresql.UUID(as_uuid=True)),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["pipeline_run_id"], ["pipeline_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reused_from_snapshot_id"], ["source_snapshots.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("pipeline_run_id", "source_id", "content_hash", name="uq_source_snapshot_run"),
    )
    op.create_index("ix_source_snapshots_source_id", "source_snapshots", ["source_id"])
    op.create_index("ix_source_snapshots_pipeline_run_id", "source_snapshots", ["pipeline_run_id"])
    op.add_column(
        "fact_ledger", sa.Column("source_snapshot_id", postgresql.UUID(as_uuid=True))
    )
    op.execute(
        """
        INSERT INTO source_snapshots (
          id, source_id, pipeline_run_id, content_hash, snapshot_text,
          accessed_at, metadata_json, created_at, updated_at
        )
        SELECT DISTINCT md5(
          f.pipeline_run_id::text || '-' || s.id::text || '-' || s.content_hash
        )::uuid, s.id, f.pipeline_run_id, s.content_hash, s.snapshot_text,
          s.accessed_at, jsonb_build_object('migration', '0004-legacy-snapshot'),
          s.created_at, s.updated_at
        FROM fact_ledger f
        JOIN sources s ON s.id = f.source_id
        WHERE f.pipeline_run_id IS NOT NULL
        ON CONFLICT (pipeline_run_id, source_id, content_hash) DO NOTHING
        """
    )
    op.execute(
        """
        UPDATE fact_ledger f SET source_snapshot_id = snapshot.id
        FROM source_snapshots snapshot
        WHERE snapshot.pipeline_run_id = f.pipeline_run_id
          AND snapshot.source_id = f.source_id
        """
    )
    op.create_foreign_key(
        "fk_fact_ledger_source_snapshot_id",
        "fact_ledger",
        "source_snapshots",
        ["source_snapshot_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_fact_ledger_source_snapshot_id", "fact_ledger", ["source_snapshot_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_fact_ledger_source_snapshot_id", table_name="fact_ledger")
    op.drop_constraint(
        "fk_fact_ledger_source_snapshot_id", "fact_ledger", type_="foreignkey"
    )
    op.drop_column("fact_ledger", "source_snapshot_id")
    op.drop_table("source_snapshots")
    op.drop_index("ix_article_blocks_replaces_block_id", table_name="article_blocks")
    op.drop_index("ix_article_blocks_logical_block_id", table_name="article_blocks")
    op.drop_constraint("fk_article_blocks_replaces_block_id", "article_blocks", type_="foreignkey")
    op.drop_column("article_blocks", "revision_reason")
    op.drop_column("article_blocks", "replaces_block_id")
    op.drop_column("article_blocks", "logical_block_id")
    op.drop_index("ix_articles_active_pipeline_run_id", table_name="articles")
    op.drop_constraint("fk_articles_active_pipeline_run_id", "articles", type_="foreignkey")
    op.drop_column("articles", "active_pipeline_run_id")
    op.drop_column("articles", "content_type")
    op.drop_index("ix_pipeline_events_run_order", table_name="pipeline_events")
    for name, table in (
        ("uq_fact_per_pipeline_run", "fact_ledger"),
        ("uq_article_version_idempotency", "article_versions"),
        ("uq_event_idempotency", "pipeline_events"),
        ("uq_agent_run_idempotency", "agent_runs"),
        ("uq_handoff_idempotency", "agent_handoffs"),
        ("uq_research_plan_idempotency", "research_plans"),
    ):
        op.drop_constraint(name, table, type_="unique")
    for table in ("article_versions", "pipeline_events", "agent_runs", "agent_handoffs", "fact_ledger", "research_plans"):
        if table in {"article_versions", "pipeline_events", "agent_runs", "agent_handoffs"}:
            op.drop_column(table, "idempotency_key")
        if table == "article_versions":
            op.drop_column(table, "source_report")
            op.drop_column(table, "seo_metadata")
            op.drop_column(table, "final_html")
            op.drop_column(table, "final_markdown")
        if table == "agent_runs":
            op.drop_column(table, "fallback_used")
        op.drop_index(f"ix_{table}_pipeline_run_id", table_name=table)
        op.drop_constraint(f"fk_{table}_pipeline_run_id", table, type_="foreignkey")
        op.drop_column(table, "pipeline_run_id")
    op.drop_column("research_plans", "idempotency_key")
    for table in ("style_patterns", "style_sources", "agent_memories"):
        op.drop_index(f"ix_{table}_origin_pipeline_run_id", table_name=table)
        op.drop_constraint(
            f"fk_{table}_origin_pipeline_run_id", table, type_="foreignkey"
        )
        op.drop_column(table, "origin_pipeline_run_id")
    op.drop_table("pipeline_state_transitions")
    op.drop_table("pipeline_checkpoints")
    op.drop_table("pipeline_runs")
    op.drop_column("projects", "event_sequence")
    op.drop_constraint(
        "uq_projects_creation_idempotency_key", "projects", type_="unique"
    )
    op.drop_column("projects", "creation_idempotency_key")
    op.drop_column("projects", "content_type")
    trigger_type.drop(op.get_bind(), checkfirst=True)
    run_status.drop(op.get_bind(), checkfirst=True)
    content_type.drop(op.get_bind(), checkfirst=True)
