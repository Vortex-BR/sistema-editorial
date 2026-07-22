"""Add superior skills, durable memory, handoffs and style learning."""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


DDL = (
    "CREATE EXTENSION IF NOT EXISTS vector",
    "CREATE TYPE superiorskillscope AS ENUM ('global_core','agent')",
    "CREATE TYPE learningstatus AS ENUM ('quarantine','approved','rejected','archived')",
    """CREATE TABLE superior_skills (
        skill_id VARCHAR(160) NOT NULL, scope superiorskillscope NOT NULL,
        agent_role VARCHAR(50), enabled BOOLEAN NOT NULL, current_version VARCHAR(30) NOT NULL,
        id UUID, created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
        updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,
        CONSTRAINT pk_superior_skills PRIMARY KEY(id),
        CONSTRAINT uq_superior_skills_skill_id UNIQUE(skill_id),
        CONSTRAINT uq_superior_skills_agent_role UNIQUE(agent_role))""",
    """CREATE TABLE superior_skill_versions (
        superior_skill_id UUID NOT NULL,
        version VARCHAR(30) NOT NULL, definition JSONB NOT NULL, checksum VARCHAR(64) NOT NULL,
        status VARCHAR(30) NOT NULL, reviewed_by_human BOOLEAN NOT NULL, approved_at TIMESTAMPTZ,
        created_by VARCHAR(120) NOT NULL, id UUID,
        created_at TIMESTAMPTZ DEFAULT now() NOT NULL, updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,
        CONSTRAINT pk_superior_skill_versions PRIMARY KEY(id),
        CONSTRAINT uq_superior_skill_versions_superior_skill_id UNIQUE(superior_skill_id, version),
        CONSTRAINT fk_superior_skill_versions_superior_skill_id_superior_skills
          FOREIGN KEY(superior_skill_id) REFERENCES superior_skills(id) ON DELETE CASCADE)""",
    "CREATE INDEX ix_superior_skill_versions_superior_skill_id ON superior_skill_versions(superior_skill_id)",
    """CREATE TABLE agent_memories (
        agent_role VARCHAR(50) NOT NULL, project_id UUID,
        niche VARCHAR(120), memory_kind VARCHAR(50) NOT NULL, content TEXT NOT NULL,
        source_type VARCHAR(50) NOT NULL, source_id VARCHAR(160), confidence_score FLOAT NOT NULL,
        status learningstatus NOT NULL, persona_version VARCHAR(30), embedding VECTOR,
        embedding_provider VARCHAR(30), embedding_model VARCHAR(100), embedding_dimensions INTEGER,
        last_used_at TIMESTAMPTZ, id UUID, created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
        updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,
        CONSTRAINT pk_agent_memories PRIMARY KEY(id),
        CONSTRAINT ck_agent_memories_agent_memory_confidence_range CHECK (confidence_score BETWEEN 0 AND 1),
        CONSTRAINT fk_agent_memories_project_id_projects FOREIGN KEY(project_id)
          REFERENCES projects(id) ON DELETE CASCADE)""",
    "CREATE INDEX ix_agent_memories_agent_role ON agent_memories(agent_role)",
    "CREATE INDEX ix_agent_memories_project_id ON agent_memories(project_id)",
    "CREATE INDEX ix_agent_memories_niche ON agent_memories(niche)",
    "CREATE INDEX ix_agent_memories_memory_kind ON agent_memories(memory_kind)",
    "CREATE INDEX ix_agent_memories_status ON agent_memories(status)",
    """CREATE TABLE agent_handoffs (
        project_id UUID NOT NULL,
        from_role VARCHAR(50) NOT NULL, to_role VARCHAR(50) NOT NULL, payload JSONB NOT NULL,
        fact_ids JSONB NOT NULL, confidence_score FLOAT NOT NULL, id UUID,
        created_at TIMESTAMPTZ DEFAULT now() NOT NULL, updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,
        CONSTRAINT pk_agent_handoffs PRIMARY KEY(id),
        CONSTRAINT ck_agent_handoffs_agent_handoff_confidence_range CHECK (confidence_score BETWEEN 0 AND 1),
        CONSTRAINT fk_agent_handoffs_project_id_projects FOREIGN KEY(project_id)
          REFERENCES projects(id) ON DELETE CASCADE)""",
    "CREATE INDEX ix_agent_handoffs_project_id ON agent_handoffs(project_id)",
    "CREATE INDEX ix_agent_handoffs_from_role ON agent_handoffs(from_role)",
    "CREATE INDEX ix_agent_handoffs_to_role ON agent_handoffs(to_role)",
    """CREATE TABLE style_sources (
        project_id UUID, canonical_url TEXT NOT NULL,
        title TEXT NOT NULL, publisher VARCHAR(255), domain VARCHAR(255) NOT NULL,
        content_hash VARCHAR(64) NOT NULL, excerpts JSONB NOT NULL, metadata_json JSONB NOT NULL,
        status learningstatus NOT NULL, id UUID, created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
        updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,
        CONSTRAINT pk_style_sources PRIMARY KEY(id),
        CONSTRAINT uq_style_sources_project_id UNIQUE(project_id, canonical_url, content_hash),
        CONSTRAINT fk_style_sources_project_id_projects FOREIGN KEY(project_id)
          REFERENCES projects(id) ON DELETE CASCADE)""",
    "CREATE INDEX ix_style_sources_project_id ON style_sources(project_id)",
    "CREATE INDEX ix_style_sources_domain ON style_sources(domain)",
    "CREATE INDEX ix_style_sources_status ON style_sources(status)",
    """CREATE TABLE style_patterns (
        project_id UUID,
        target_agent_role VARCHAR(50) NOT NULL, niche VARCHAR(120), pattern_type VARCHAR(80) NOT NULL,
        description TEXT NOT NULL, source_ids JSONB NOT NULL, independent_domain_count INTEGER NOT NULL,
        validation_count INTEGER NOT NULL, status learningstatus NOT NULL, approved_at TIMESTAMPTZ,
        embedding VECTOR, embedding_provider VARCHAR(30), embedding_model VARCHAR(100),
        embedding_dimensions INTEGER, id UUID, created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
        updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,
        CONSTRAINT pk_style_patterns PRIMARY KEY(id),
        CONSTRAINT ck_style_patterns_style_pattern_counts_nonnegative CHECK (
            independent_domain_count >= 0 AND validation_count >= 0),
        CONSTRAINT fk_style_patterns_project_id_projects FOREIGN KEY(project_id)
          REFERENCES projects(id) ON DELETE CASCADE)""",
    "CREATE INDEX ix_style_patterns_project_id ON style_patterns(project_id)",
    "CREATE INDEX ix_style_patterns_niche ON style_patterns(niche)",
    "CREATE INDEX ix_style_patterns_pattern_type ON style_patterns(pattern_type)",
    "CREATE INDEX ix_style_patterns_status ON style_patterns(status)",
    """CREATE TABLE embedding_routes (
        provider VARCHAR(30) NOT NULL, model VARCHAR(100) NOT NULL, dimensions INTEGER,
        active BOOLEAN NOT NULL, id UUID, created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
        updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,
        CONSTRAINT pk_embedding_routes PRIMARY KEY(id))""",
    "CREATE INDEX ix_embedding_routes_active ON embedding_routes(active)",
    "CREATE UNIQUE INDEX uq_embedding_routes_single_active ON embedding_routes(active) WHERE active",
)


def upgrade() -> None:
    for statement in DDL:
        op.execute(statement)


def downgrade() -> None:
    for table in (
        "embedding_routes", "style_patterns", "style_sources", "agent_handoffs",
        "agent_memories", "superior_skill_versions", "superior_skills",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("DROP TYPE IF EXISTS learningstatus")
    op.execute("DROP TYPE IF EXISTS superiorskillscope")
