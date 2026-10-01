from fraud_intel.config import AppConfig


def test_config_loads_and_method_mix_sums_to_one(config: AppConfig) -> None:
    for country, mix in config.generator.method_mix.items():
        assert abs(sum(mix.values()) - 1.0) < 1e-3, country
    assert set(config.generator.country_shares) == set(config.utc_offset_hours)
    assert config.database_path.is_absolute()
