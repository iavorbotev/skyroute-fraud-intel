"""Load config.toml into frozen dataclasses."""

import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass(frozen=True)
class GeneratorConfig:
    end_date: date
    days_per_window: int
    transactions_per_window: int
    customers_per_window: int
    approval_rate: float
    history_fraud_rate: float
    scored_fraud_rate: float
    spike_start_day: int
    mean_amount_usd: float
    amount_sigma: float
    legit_ip_abroad_share: float
    last_minute_share: float
    bins_per_country: int
    bad_bins_per_country: int
    ar_cluster_share: float
    ar_cluster_start_day: int
    velocity_customer_share: float
    card_testing_customer_share: float
    legit_high_value_share: float
    country_shares: dict[str, float]
    method_mix: dict[str, dict[str, float]]


@dataclass(frozen=True)
class RiskConfig:
    settled_prior_strength: float
    velocity_window_minutes: int
    velocity_min_attempts: int
    card_testing_min_declines: int
    card_testing_min_bins: int
    high_value_usd: float
    value_outlier_quantile: float
    last_minute_hours: int
    last_minute_min_usd: float
    night_start_hour: int
    night_end_hour: int
    risky_segment_multiplier: float
    risky_bin_multiplier: float
    medium_threshold: int
    high_threshold: int
    points: dict[str, int]


@dataclass(frozen=True)
class AlertConfig:
    velocity_burst_min_attempts: int
    card_testing_min_declines: int
    card_testing_window_minutes: int
    share_spike_multiplier: float
    share_spike_min_high_risk: int
    country_burst_multiplier: float
    country_burst_min_count: int


@dataclass(frozen=True)
class PatternConfig:
    burst_window_days: int
    burst_min_ratio: float
    burst_min_count: int
    big_booking_usd: float


@dataclass(frozen=True)
class AppConfig:
    seed: int
    data_path: Path
    database_path: Path
    scored_window_days: int
    chargeback_fee_usd: float
    utc_offset_hours: dict[str, int]
    generator: GeneratorConfig
    risk: RiskConfig
    alerts: AlertConfig
    patterns: PatternConfig


def load_config(path: Path) -> AppConfig:
    """Read the TOML config; relative paths resolve against the config file's folder."""
    if not path.is_file():
        raise FileNotFoundError(f"config file not found: {path}")
    raw = tomllib.loads(path.read_text())
    base_dir = path.resolve().parent
    generator_raw = dict(raw["generator"])
    # tomllib already parses unquoted dates, but a quoted date is easier to read in the file
    generator_raw["end_date"] = date.fromisoformat(str(generator_raw["end_date"]))
    return AppConfig(
        seed=raw["seed"],
        data_path=base_dir / raw["data_path"],
        database_path=base_dir / raw["database_path"],
        scored_window_days=raw["scored_window_days"],
        chargeback_fee_usd=raw["chargeback_fee_usd"],
        utc_offset_hours=raw["utc_offset_hours"],
        generator=GeneratorConfig(**generator_raw),
        risk=RiskConfig(**raw["risk"]),
        alerts=AlertConfig(**raw["alerts"]),
        patterns=PatternConfig(**raw["patterns"]),
    )
