import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from market_sim.config import ExperimentConfig  # noqa: E402


def small_config(tmp_path: Path, **overrides) -> ExperimentConfig:
    data = {
        "name": "test",
        "seed": 123,
        "rounds": 8,
        "runs_dir": str(tmp_path / "runs"),
        "goods": [{"id": "w", "initial_price": 90}],
        "llm": {"provider": "mock", "max_concurrency": 4, "timeout_s": 2, "max_retries": 1, "retry_backoff_s": 0,
                "cache": {"mode": "off", "path": str(tmp_path / "cache.sqlite")}},
        "agents": [
            {"role": "buyer", "count": 6, "decision": "llm", "cash": 2000, "income_per_round": 200, "units_per_round": 2,
             "valuations": {"w": {"dist": "uniform", "low": 60, "high": 140}}},
            {"role": "seller", "count": 3, "decision": "llm", "cash": 500,
             "costs": {"w": {"dist": "uniform", "low": 40, "high": 100}}, "capacity": {"w": 4}},
            {"role": "speculator", "count": 2, "decision": "llm", "cash": 3000, "inventory": {"w": 5}, "info_lag": 1},
        ],
    }
    for k, v in overrides.items():
        data[k] = v
    return ExperimentConfig.model_validate(data)


@pytest.fixture
def make_config(tmp_path):
    return lambda **kw: small_config(tmp_path, **kw)
