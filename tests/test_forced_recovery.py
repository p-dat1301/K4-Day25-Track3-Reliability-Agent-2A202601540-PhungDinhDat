from __future__ import annotations

import time

from pytest import MonkeyPatch

from reliability_lab import chaos
from reliability_lab.config import LabConfig, ScenarioConfig


def test_forced_recovery_reports_reset_timeout_without_wall_clock_sleep(
    monkeypatch: MonkeyPatch,
) -> None:
    clock = [1_000.0]

    def monotonic() -> float:
        return clock[0]

    def epoch_time() -> float:
        return clock[0]

    def advance(seconds: float) -> None:
        clock[0] += seconds

    monkeypatch.setattr(time, "monotonic", monotonic)
    monkeypatch.setattr(time, "time", epoch_time)
    monkeypatch.setattr(time, "sleep", advance)
    config = LabConfig.model_validate(
        {
            "providers": [
                {
                    "name": "primary",
                    "fail_rate": 0.0,
                    "base_latency_ms": 1,
                    "cost_per_1k_tokens": 0.01,
                },
                {
                    "name": "backup",
                    "fail_rate": 0.0,
                    "base_latency_ms": 1,
                    "cost_per_1k_tokens": 0.01,
                },
            ],
            "circuit_breaker": {
                "failure_threshold": 1,
                "reset_timeout_seconds": 2.0,
                "success_threshold": 1,
            },
            "cache": {
                "enabled": False,
                "ttl_seconds": 60,
                "similarity_threshold": 0.9,
            },
            "load_test": {"requests": 1},
        }
    )
    scenario = ScenarioConfig(name="forced_recovery", force_recovery=True)

    metrics = chaos.run_scenario(config, ["probe query"], scenario)

    assert metrics.recovery_time_ms is not None
    assert metrics.recovery_time_ms >= 2_000.0
