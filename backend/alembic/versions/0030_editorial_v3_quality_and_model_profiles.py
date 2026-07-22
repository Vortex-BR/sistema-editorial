"""Editorial V3 quality repair and trustworthy OpenAI route profiles.

Revision ID: 0030
Revises: 0029
"""

from collections.abc import Sequence

from alembic import op


revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_ROUTE_PROFILES = (
    ("planner", "gpt-5-mini", "low", 4096, 120, 1, 0.25, 2.0),
    ("researcher", "gpt-5-mini", "low", 4096, 120, 1, 0.25, 2.0),
    (
        "research_gatekeeper",
        "gpt-5.4-mini",
        "medium",
        4096,
        150,
        1,
        0.75,
        4.5,
    ),
    ("writer", "gpt-5.4", "low", 12000, 240, 1, 2.5, 15.0),
    ("editor", "gpt-5.4-mini", "medium", 8192, 180, 1, 0.75, 4.5),
    ("skill_curator", "gpt-5-mini", "low", 2048, 90, 0, 0.25, 2.0),
)


def upgrade() -> None:
    # Only exact provider/model/role matches are reconciled.  Custom routes and
    # other providers remain untouched.
    for (
        role,
        model,
        reasoning_effort,
        max_output_tokens,
        timeout_seconds,
        max_retries,
        input_rate,
        output_rate,
    ) in _ROUTE_PROFILES:
        op.execute(
            f"""
            UPDATE model_routes
               SET parameters = (COALESCE(parameters, '{{}}'::jsonb) - 'temperature')
                    || jsonb_build_object(
                         'reasoning_effort', '{reasoning_effort}',
                         'max_output_tokens', {max_output_tokens},
                         'timeout_seconds', {timeout_seconds},
                         'max_retries', {max_retries},
                         'input_cost_per_million', {input_rate},
                         'output_cost_per_million', {output_rate}
                       )
             WHERE agent_role = '{role}'
               AND primary_provider = 'openai'
               AND primary_model = '{model}'
            """
        )


def downgrade() -> None:
    # Route settings are operational configuration.  Reverting the schema must
    # not silently restore stale prices or reduce output limits.
    pass
