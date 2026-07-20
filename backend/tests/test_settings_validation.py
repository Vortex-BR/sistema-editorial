import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_default_settings_have_consistent_operational_limits():
    configured = Settings(_env_file=None)

    assert configured.max_agent_cost_usd == 0.40
    assert configured.max_agent_cost_usd <= configured.max_pipeline_cost_usd
    assert configured.quality_min_word_count <= configured.quality_max_word_count
    assert (
        configured.operational_heartbeat_max_age_seconds
        < configured.operational_heartbeat_ttl_seconds
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"quality_min_word_count": 1200, "quality_max_word_count": 800},
        {"max_agent_cost_usd": 0.50, "max_pipeline_cost_usd": 0.40},
        {
            "pipeline_dispatch_retry_base_seconds": 301,
            "pipeline_dispatch_retry_max_seconds": 300,
        },
        {
            "operational_heartbeat_max_age_seconds": 30,
            "operational_heartbeat_ttl_seconds": 30,
        },
    ],
)
def test_inconsistent_operational_limits_are_rejected(overrides):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **overrides)
