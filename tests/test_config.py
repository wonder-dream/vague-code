from __future__ import annotations

import pytest

from vague_code.agent.config import AgentConfig, SupervisionConfig


# ── SupervisionConfig ────────────────────────────────────────────────────

def test_supervision_defaults_disabled():
    s = SupervisionConfig()
    assert s.enabled is False
    assert s.period == 6
    assert s.model is None
    assert s.max_input_tokens == 6000
    assert s.stuck_limit == 2


def test_supervision_validate_ok():
    SupervisionConfig(enabled=True, period=1, model="deepseek-v4-flash",
                      max_input_tokens=100, stuck_limit=1)


@pytest.mark.parametrize("kwargs", [
    {"period": 0},
    {"period": -3},
    {"stuck_limit": 0},
    {"stuck_limit": -1},
    {"max_input_tokens": 0},
    {"max_input_tokens": -100},
])
def test_supervision_invalid_values(kwargs):
    with pytest.raises(ValueError):
        SupervisionConfig(**kwargs)


def test_agent_config_has_supervision_field():
    c = AgentConfig()
    assert isinstance(c.supervision, SupervisionConfig)
    assert c.supervision.enabled is False


def test_agent_config_supervision_roundtrip_to_public_dict():
    c = AgentConfig(supervision=SupervisionConfig(enabled=True, period=3))
    d = c.to_public_dict()
    assert d["supervision"]["enabled"] is True
    assert d["supervision"]["period"] == 3


# ── max_turns 保险丝默认 500（ADR-0020 #1） ─────────────────────────────

def test_max_turns_default_is_fuse():
    assert AgentConfig().max_turns == 500


def test_max_turns_500_no_warning():
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        AgentConfig(max_turns=500)


def test_max_turns_above_fuse_warns():
    with pytest.warns(UserWarning):
        AgentConfig(max_turns=501)
