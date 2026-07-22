from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.api.routes import AGENT_ROLES, get_config


class _ScalarRows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class ConfigDb:
    def __init__(self, routes):
        self._scalar_sets = [routes, [], []]

    async def scalars(self, _query):
        return _ScalarRows(self._scalar_sets.pop(0))

    async def scalar(self, _query):
        return None


@pytest.mark.asyncio
async def test_config_round_trip_includes_exact_route_parameters_and_server_defaults():
    persisted_parameters = {
        "max_output_tokens": 4096,
        "timeout_seconds": 180,
        "max_retries": 2,
        "input_cost_per_million": 3.0,
        "output_cost_per_million": 15.0,
    }
    route = SimpleNamespace(
        agent_role="writer",
        primary_provider="anthropic",
        primary_model="claude-sonnet-5",
        fallback_provider=None,
        fallback_model=None,
        parameters=persisted_parameters,
    )

    response = await get_config(ConfigDb([route]))

    assert response["routes"] == [
        {
            "agent_role": "writer",
            "primary_provider": "anthropic",
            "primary_model": "claude-sonnet-5",
            "fallback_provider": None,
            "fallback_model": None,
            "parameters": persisted_parameters,
        }
    ]
    assert set(response["route_defaults"]) == {"openai", "anthropic", "gemini"}
    assert set(response["route_defaults"]["anthropic"]) == set(AGENT_ROLES)
    anthropic_writer = response["route_defaults"]["anthropic"]["writer"]
    assert anthropic_writer["primary_model"] == "claude-sonnet-5"
    assert anthropic_writer["parameters"]["input_cost_per_million"] > 0
    assert anthropic_writer["parameters"]["output_cost_per_million"] > 0
    assert "reasoning_effort" not in anthropic_writer["parameters"]
