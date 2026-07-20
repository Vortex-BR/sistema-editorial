from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "SEO Research Ledger"
    app_env: str = "development"
    app_commit_sha: str = "unversioned"
    app_build_version: str = "development"
    app_source_digest: str = "unversioned"
    app_build_info_path: str = "/app/build-info.json"
    database_url: str = "postgresql+asyncpg://seo:change-me@localhost:5432/seo_ledger"
    redis_url: str = "redis://localhost:6379/0"
    credential_master_key: str = ""
    frontend_origin: str = "http://localhost:3000"
    # A second cycle may only vary extraction while still seeing the same search
    # results. Three cycles give an uncovered question one genuinely fresh,
    # source-diversified search before the safe partial-delivery fallback.
    max_research_cycles: int = 3
    # One focused rewrite after the initial draft. Repeating the entire
    # writer/editor pair several times made runs slow without guaranteeing a
    # better outcome; remaining issues are surfaced for human review.
    max_editor_cycles: int = 1
    min_distinct_sources: int = Field(default=3, ge=2, le=10)
    min_facts_per_question: int = Field(default=2, ge=1, le=6)
    learned_skill_stability_threshold: int = Field(default=3, ge=3)
    learned_skill_min_independent_articles: int = Field(default=2, ge=2)
    max_learned_skills_per_prompt: int = Field(default=3, ge=1)
    max_learned_skill_characters_per_prompt: int = Field(default=4000, ge=200)
    skills_path: str = "/app/skills/default"
    superior_skills_path: str = "/app/skills/superior"
    superior_skills_mode: Literal["shadow", "enforced"] = "shadow"
    # V3 remains opt-in. Contract inspection and actual execution use separate
    # flags so production can validate configuration before routing real runs.
    editorial_pipeline_v3_enabled: bool = False
    editorial_pipeline_v3_execution_enabled: bool = False
    v3_max_research_tasks: int = Field(default=16, ge=8, le=30)
    v3_max_search_queries: int = Field(default=32, ge=8, le=80)
    v3_search_results_per_query: int = Field(default=5, ge=2, le=10)
    v3_max_source_documents: int = Field(default=48, ge=8, le=120)
    # V3.5 budgets count real provider traffic, not only logical planner queries.
    v3_max_search_provider_requests: int = Field(default=96, ge=8, le=400)
    v3_max_search_provider_retries: int = Field(default=32, ge=0, le=160)
    v3_max_search_estimated_credits: float = Field(default=96.0, gt=0, le=1000)
    v3_source_discovery_timeout_seconds: float = Field(default=240.0, ge=30, le=1800)
    v3_max_source_fetches: int = Field(default=64, ge=4, le=240)
    v3_max_source_recovery_rounds: int = Field(default=2, ge=0, le=4)
    v3_min_candidate_relevance: float = Field(default=0.18, ge=0.05, le=0.8)
    v3_max_documents_per_research_task: int = Field(default=6, ge=2, le=12)
    v3_min_approved_claims: int = Field(default=18, ge=8, le=100)
    v3_min_claims_per_method: int = Field(default=3, ge=1, le=12)
    v3_min_steps_per_method: int = Field(default=3, ge=1, le=12)
    v3_writer_repair_attempts: int = Field(default=1, ge=0, le=2)
    v3_min_word_count: int = Field(default=1800, ge=800, le=6000)
    v3_max_word_count: int = Field(default=3500, ge=1200, le=10000)
    admin_api_token: str = ""
    persona_context_cache_ttl_seconds: int = 900
    max_agent_memories_per_prompt: int = 6
    content_similarity_warning_threshold: float = 0.72
    content_duplicate_threshold: float = 0.90
    quality_min_overall_score: float = Field(default=0.75, ge=0, le=1)
    quality_min_axis_score: float = Field(default=0.55, ge=0, le=1)
    quality_min_claim_overlap: float = Field(default=0.12, ge=0, le=1)
    quality_max_duplicate_score: float = Field(default=0.90, ge=0, le=1)
    quality_min_word_count: int = Field(default=650, ge=300)
    quality_max_word_count: int = Field(default=1400, ge=400)
    quality_min_approved_facts: int = Field(default=6, ge=1)
    # Headings organize a real argument; they are not a quota. Four H2s are
    # enough for an 800–1,100 word article, while H3s remain optional.
    quality_min_h2_count: int = Field(default=3, ge=1)
    quality_min_h3_count: int = Field(default=0, ge=0)
    quality_max_sentence_words: int = Field(default=32, ge=5)
    max_pipeline_cost_usd: float = Field(default=0.80, gt=0, le=100)
    max_agent_cost_usd: float = Field(default=0.40, gt=0, le=100)
    # Public generation payload sent to an agent. Private trace input is never
    # sent automatically. A hard character ceiling prevents accidental context
    # explosions and makes truncation an explicit upstream decision.
    agent_task_data_max_characters: int = Field(default=400_000, ge=10_000, le=2_000_000)
    provider_connect_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    provider_read_timeout_seconds: float = Field(default=90.0, gt=0, le=600)
    pipeline_dispatch_batch_size: int = 50
    pipeline_dispatch_claim_ttl_seconds: int = 120
    pipeline_dispatch_delivery_timeout_seconds: int = 900
    pipeline_dispatch_retry_base_seconds: int = 30
    pipeline_dispatch_retry_max_seconds: int = 300
    pipeline_dispatch_interval_seconds: float = 60.0
    readiness_timeout_seconds: float = Field(default=3.0, gt=0, le=30)
    operational_heartbeat_interval_seconds: float = Field(default=5.0, gt=0, le=60)
    operational_heartbeat_max_age_seconds: float = Field(default=20.0, gt=0, le=300)
    operational_heartbeat_ttl_seconds: int = Field(default=30, ge=10, le=600)
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @model_validator(mode="after")
    def validate_operational_ranges(self):
        if self.quality_min_word_count > self.quality_max_word_count:
            raise ValueError(
                "QUALITY_MIN_WORD_COUNT cannot exceed QUALITY_MAX_WORD_COUNT"
            )
        if self.max_agent_cost_usd > self.max_pipeline_cost_usd:
            raise ValueError("MAX_AGENT_COST_USD cannot exceed MAX_PIPELINE_COST_USD")
        if (
            self.editorial_pipeline_v3_execution_enabled
            and not self.editorial_pipeline_v3_enabled
        ):
            raise ValueError(
                "EDITORIAL_PIPELINE_V3_EXECUTION_ENABLED requires EDITORIAL_PIPELINE_V3_ENABLED"
            )
        if self.v3_min_word_count > self.v3_max_word_count:
            raise ValueError("V3_MIN_WORD_COUNT cannot exceed V3_MAX_WORD_COUNT")
        if (
            self.pipeline_dispatch_retry_base_seconds
            > self.pipeline_dispatch_retry_max_seconds
        ):
            raise ValueError(
                "PIPELINE_DISPATCH_RETRY_BASE_SECONDS cannot exceed "
                "PIPELINE_DISPATCH_RETRY_MAX_SECONDS"
            )
        if (
            self.operational_heartbeat_max_age_seconds
            >= self.operational_heartbeat_ttl_seconds
        ):
            raise ValueError(
                "OPERATIONAL_HEARTBEAT_MAX_AGE_SECONDS must be lower than "
                "OPERATIONAL_HEARTBEAT_TTL_SECONDS"
            )
        return self

    @property
    def is_production(self) -> bool:
        return self.app_env.strip().lower() == "production"

    @property
    def api_documentation_enabled(self) -> bool:
        return self.app_env.strip().lower() in {"development", "test"}

    def was_explicitly_configured(self, field_name: str) -> bool:
        """Distinguish deploy input from development-safe field defaults."""
        return field_name in self.model_fields_set


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
