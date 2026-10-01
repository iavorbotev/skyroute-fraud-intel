from pathlib import Path

import pytest

from fraud_intel.config import AppConfig, load_config


@pytest.fixture(scope="session")
def config() -> AppConfig:
    return load_config(path=Path(__file__).parents[1] / "config.toml")
