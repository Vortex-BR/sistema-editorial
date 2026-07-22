"""Initial audit-first data model, frozen and independent from application models."""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


DDL = (
    "CREATE EXTENSION IF NOT EXISTS vector",
    "CREATE TYPE projectstatus AS ENUM ('draft','queued','running','needs_review','completed','failed')",
    "CREATE TYPE skillkind AS ENUM ('default','learned')",
    "CREATE TYPE runstatus AS ENUM ('pending','running','succeeded','failed','blocked')",
    "CREATE TYPE gatedecision AS ENUM ('approved','insufficient','rewrite','rejected')",
    "CREATE TYPE credentialprovider AS ENUM ('openai','anthropic','gemini','tavily','serper')",
    """CREATE TABLE projects (
        name VARCHAR(200) NOT NULL, topic TEXT NOT NULL, search_intent VARCHAR(50) NOT NULL,
        audience TEXT NOT NULL, language VARCHAR(10) NOT NULL, niche VARCHAR(120),
        status projectstatus NOT NULL, current_stage VARCHAR(50) NOT NULL,
        research_cycles INTEGER NOT NULL, editor_cycles INTEGER NOT NULL,
        id UUID, created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
        updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,
        CONSTRAINT pk_projects PRIMARY KEY(id))""",
    "CREATE INDEX ix_projects_status ON projects(status)",
    """CREATE TABLE sources (
        canonical_url TEXT NOT NULL, title TEXT NOT NULL, publisher VARCHAR(255),
        source_type VARCHAR(50) NOT NULL, published_at TIMESTAMPTZ,
        accessed_at TIMESTAMPTZ DEFAULT now() NOT NULL, content_hash VARCHAR(64) NOT NULL,
        snapshot_text TEXT NOT NULL, reliability_score FLOAT NOT NULL,
        metadata_json JSONB NOT NULL, id UUID,
        created_at TIMESTAMPTZ DEFAULT now() NOT NULL, updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,
        CONSTRAINT pk_sources PRIMARY KEY(id),
        CONSTRAINT uq_sources_canonical_url UNIQUE(canonical_url),
        CONSTRAINT ck_sources_source_reliability_range CHECK (reliability_score BETWEEN 0 AND 1))""",
    """CREATE TABLE skills (
        skill_id VARCHAR(160) NOT NULL, kind skillkind NOT NULL,
        applies_to_agents JSONB NOT NULL, niche VARCHAR(120), enabled BOOLEAN NOT NULL,
        stable BOOLEAN NOT NULL, promoted_at TIMESTAMPTZ, current_version VARCHAR(30) NOT NULL,
        id UUID, created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
        updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,
        CONSTRAINT pk_skills PRIMARY KEY(id), CONSTRAINT uq_skills_skill_id UNIQUE(skill_id))""",
    "CREATE INDEX ix_skills_kind ON skills(kind)",
    "CREATE INDEX ix_skills_niche ON skills(niche)",
    """CREATE TABLE credentials (
        provider credentialprovider NOT NULL, encrypted_value BYTEA NOT NULL,
        key_version INTEGER NOT NULL, last_four VARCHAR(4) NOT NULL, active BOOLEAN NOT NULL,
        verified_at TIMESTAMPTZ, id UUID,
        created_at TIMESTAMPTZ DEFAULT now() NOT NULL, updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,
        CONSTRAINT pk_credentials PRIMARY KEY(id),
        CONSTRAINT uq_credentials_provider UNIQUE(provider))""",
    """CREATE TABLE model_routes (
        agent_role VARCHAR(50) NOT NULL, primary_provider VARCHAR(30) NOT NULL,
        primary_model VARCHAR(100) NOT NULL, fallback_provider VARCHAR(30),
        fallback_model VARCHAR(100), parameters JSONB NOT NULL, id UUID,
        created_at TIMESTAMPTZ DEFAULT now() NOT NULL, updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,
        CONSTRAINT pk_model_routes PRIMARY KEY(id),
        CONSTRAINT uq_model_routes_agent_role UNIQUE(agent_role))""",
    """CREATE TABLE research_plans (
        project_id UUID NOT NULL,
        version INTEGER NOT NULL, status VARCHAR(30) NOT NULL, rationale TEXT NOT NULL,
        semantic_keywords JSONB NOT NULL, competitor_angles JSONB NOT NULL,
        content_gaps JSONB NOT NULL, id UUID,
        created_at TIMESTAMPTZ DEFAULT now() NOT NULL, updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,
        CONSTRAINT pk_research_plans PRIMARY KEY(id),
        CONSTRAINT uq_research_plans_project_id UNIQUE(project_id, version),
        CONSTRAINT fk_research_plans_project_id_projects FOREIGN KEY(project_id)
          REFERENCES projects(id) ON DELETE CASCADE)""",
    "CREATE INDEX ix_research_plans_project_id ON research_plans(project_id)",
    """CREATE TABLE articles (
        project_id UUID NOT NULL,
        current_version INTEGER NOT NULL, status VARCHAR(30) NOT NULL, final_markdown TEXT,
        final_html TEXT, seo_metadata JSONB NOT NULL, source_report JSONB NOT NULL,
        id UUID, created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
        updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,
        CONSTRAINT pk_articles PRIMARY KEY(id),
        CONSTRAINT uq_articles_project_id UNIQUE(project_id),
        CONSTRAINT fk_articles_project_id_projects FOREIGN KEY(project_id)
          REFERENCES projects(id) ON DELETE CASCADE)""",
    """CREATE TABLE agent_runs (
        project_id UUID NOT NULL,
        agent_role VARCHAR(50) NOT NULL, attempt INTEGER NOT NULL, status runstatus NOT NULL,
        input_json JSONB NOT NULL, output_json JSONB, decision gatedecision, feedback JSONB,
        provider VARCHAR(30), model VARCHAR(100), prompt_tokens INTEGER NOT NULL,
        completion_tokens INTEGER NOT NULL, estimated_cost_usd NUMERIC(12,6) NOT NULL,
        latency_ms INTEGER NOT NULL, started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ,
        error TEXT, id UUID, created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
        updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,
        CONSTRAINT pk_agent_runs PRIMARY KEY(id),
        CONSTRAINT fk_agent_runs_project_id_projects FOREIGN KEY(project_id)
          REFERENCES projects(id) ON DELETE CASCADE)""",
    "CREATE INDEX ix_agent_runs_project_id ON agent_runs(project_id)",
    "CREATE INDEX ix_agent_runs_agent_role ON agent_runs(agent_role)",
    """CREATE TABLE pipeline_events (
        project_id UUID NOT NULL,
        sequence INTEGER NOT NULL, event_type VARCHAR(50) NOT NULL, stage VARCHAR(50) NOT NULL,
        payload JSONB NOT NULL, created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
        id UUID, CONSTRAINT pk_pipeline_events PRIMARY KEY(id),
        CONSTRAINT uq_pipeline_events_project_id UNIQUE(project_id, sequence),
        CONSTRAINT fk_pipeline_events_project_id_projects FOREIGN KEY(project_id)
          REFERENCES projects(id) ON DELETE CASCADE)""",
    "CREATE INDEX ix_pipeline_events_project_id ON pipeline_events(project_id)",
    """CREATE TABLE research_questions (
        plan_id UUID NOT NULL,
        question TEXT NOT NULL, priority INTEGER NOT NULL, expected_source_types JSONB NOT NULL,
        coverage_status VARCHAR(30) NOT NULL, id UUID,
        created_at TIMESTAMPTZ DEFAULT now() NOT NULL, updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,
        CONSTRAINT pk_research_questions PRIMARY KEY(id),
        CONSTRAINT fk_research_questions_plan_id_research_plans FOREIGN KEY(plan_id)
          REFERENCES research_plans(id) ON DELETE CASCADE)""",
    "CREATE INDEX ix_research_questions_plan_id ON research_questions(plan_id)",
    """CREATE TABLE article_versions (
        article_id UUID NOT NULL,
        version INTEGER NOT NULL, title TEXT NOT NULL, outline JSONB NOT NULL,
        editorial_status VARCHAR(30) NOT NULL, change_reason TEXT, id UUID,
        created_at TIMESTAMPTZ DEFAULT now() NOT NULL, updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,
        CONSTRAINT pk_article_versions PRIMARY KEY(id),
        CONSTRAINT uq_article_versions_article_id UNIQUE(article_id, version),
        CONSTRAINT fk_article_versions_article_id_articles FOREIGN KEY(article_id)
          REFERENCES articles(id) ON DELETE CASCADE)""",
    "CREATE INDEX ix_article_versions_article_id ON article_versions(article_id)",
    """CREATE TABLE skill_versions (
        skill_id UUID NOT NULL,
        version VARCHAR(30) NOT NULL, description TEXT NOT NULL, definition JSONB NOT NULL,
        origin_article_id UUID, confidence_score FLOAT NOT NULL,
        validation_count INTEGER NOT NULL, reviewed_by_human BOOLEAN NOT NULL, id UUID,
        created_at TIMESTAMPTZ DEFAULT now() NOT NULL, updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,
        CONSTRAINT pk_skill_versions PRIMARY KEY(id),
        CONSTRAINT uq_skill_versions_skill_id UNIQUE(skill_id, version),
        CONSTRAINT fk_skill_versions_skill_id_skills FOREIGN KEY(skill_id)
          REFERENCES skills(id) ON DELETE CASCADE,
        CONSTRAINT fk_skill_versions_origin_article_id_articles FOREIGN KEY(origin_article_id)
          REFERENCES articles(id))""",
    "CREATE INDEX ix_skill_versions_skill_id ON skill_versions(skill_id)",
    """CREATE TABLE fact_ledger (
        project_id UUID NOT NULL, research_question_id UUID NOT NULL,
        source_id UUID NOT NULL, claim_text TEXT NOT NULL,
        exact_quote TEXT, source_locator VARCHAR(255) NOT NULL, extraction_method VARCHAR(40) NOT NULL,
        confidence_score FLOAT NOT NULL, approved BOOLEAN NOT NULL, approved_by_run_id UUID,
        conflict_group VARCHAR(100), superseded_by_id UUID, id UUID,
        created_at TIMESTAMPTZ DEFAULT now() NOT NULL, updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,
        CONSTRAINT pk_fact_ledger PRIMARY KEY(id),
        CONSTRAINT ck_fact_ledger_fact_confidence_range CHECK (confidence_score BETWEEN 0 AND 1),
        CONSTRAINT fk_fact_ledger_project_id_projects FOREIGN KEY(project_id)
          REFERENCES projects(id) ON DELETE CASCADE,
        CONSTRAINT fk_fact_ledger_research_question_id_research_questions
          FOREIGN KEY(research_question_id) REFERENCES research_questions(id) ON DELETE CASCADE,
        CONSTRAINT fk_fact_ledger_source_id_sources FOREIGN KEY(source_id)
          REFERENCES sources(id) ON DELETE RESTRICT,
        CONSTRAINT fk_fact_ledger_superseded_by_id_fact_ledger FOREIGN KEY(superseded_by_id)
          REFERENCES fact_ledger(id))""",
    "CREATE INDEX ix_fact_ledger_project_id ON fact_ledger(project_id)",
    "CREATE INDEX ix_fact_ledger_research_question_id ON fact_ledger(research_question_id)",
    "CREATE INDEX ix_fact_ledger_source_id ON fact_ledger(source_id)",
    "CREATE INDEX ix_fact_ledger_approved ON fact_ledger(approved)",
    "CREATE INDEX ix_fact_ledger_conflict_group ON fact_ledger(conflict_group)",
    "COMMENT ON COLUMN fact_ledger.source_locator IS 'Heading, page or text offsets'",
    """CREATE TABLE article_blocks (
        article_version_id UUID NOT NULL, parent_block_id UUID, block_type VARCHAR(20) NOT NULL,
        position INTEGER NOT NULL, text TEXT NOT NULL, supported BOOLEAN NOT NULL, id UUID,
        created_at TIMESTAMPTZ DEFAULT now() NOT NULL, updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,
        CONSTRAINT pk_article_blocks PRIMARY KEY(id),
        CONSTRAINT uq_article_blocks_article_version_id UNIQUE(article_version_id, position),
        CONSTRAINT fk_article_blocks_article_version_id_article_versions FOREIGN KEY(article_version_id)
          REFERENCES article_versions(id) ON DELETE CASCADE,
        CONSTRAINT fk_article_blocks_parent_block_id_article_blocks FOREIGN KEY(parent_block_id)
          REFERENCES article_blocks(id))""",
    "CREATE INDEX ix_article_blocks_article_version_id ON article_blocks(article_version_id)",
    """CREATE TABLE sentence_claims (
        block_id UUID NOT NULL,
        position INTEGER NOT NULL, text TEXT NOT NULL, is_factual BOOLEAN NOT NULL,
        support_status VARCHAR(30) NOT NULL, fidelity_status VARCHAR(30) NOT NULL, id UUID,
        created_at TIMESTAMPTZ DEFAULT now() NOT NULL, updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,
        CONSTRAINT pk_sentence_claims PRIMARY KEY(id),
        CONSTRAINT fk_sentence_claims_block_id_article_blocks FOREIGN KEY(block_id)
          REFERENCES article_blocks(id) ON DELETE CASCADE)""",
    "CREATE INDEX ix_sentence_claims_block_id ON sentence_claims(block_id)",
    "CREATE INDEX ix_sentence_claims_support_status ON sentence_claims(support_status)",
    """CREATE TABLE claim_evidence (
        sentence_claim_id UUID NOT NULL, fact_id UUID NOT NULL,
        entailment_score FLOAT NOT NULL, reviewer_approved BOOLEAN NOT NULL, id UUID,
        created_at TIMESTAMPTZ DEFAULT now() NOT NULL, updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,
        CONSTRAINT pk_claim_evidence PRIMARY KEY(id),
        CONSTRAINT uq_claim_evidence_sentence_claim_id UNIQUE(sentence_claim_id, fact_id),
        CONSTRAINT ck_claim_evidence_evidence_entailment_range CHECK (entailment_score BETWEEN 0 AND 1),
        CONSTRAINT fk_claim_evidence_sentence_claim_id_sentence_claims FOREIGN KEY(sentence_claim_id)
          REFERENCES sentence_claims(id) ON DELETE CASCADE,
        CONSTRAINT fk_claim_evidence_fact_id_fact_ledger FOREIGN KEY(fact_id)
          REFERENCES fact_ledger(id) ON DELETE RESTRICT)""",
    "CREATE INDEX ix_claim_evidence_sentence_claim_id ON claim_evidence(sentence_claim_id)",
    "CREATE INDEX ix_claim_evidence_fact_id ON claim_evidence(fact_id)",
)


def upgrade() -> None:
    for statement in DDL:
        op.execute(statement)


def downgrade() -> None:
    for table in (
        "claim_evidence", "sentence_claims", "article_blocks", "fact_ledger",
        "skill_versions", "article_versions", "research_questions", "pipeline_events",
        "agent_runs", "articles", "research_plans", "model_routes", "credentials",
        "skills", "sources", "projects",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    for type_name in (
        "credentialprovider", "gatedecision", "runstatus", "skillkind", "projectstatus"
    ):
        op.execute(f"DROP TYPE IF EXISTS {type_name}")
