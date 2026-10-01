"""Seeded synthetic SkyRoute transactions: a settled history window, a scored window, and planted fraud patterns."""

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from fraud_intel.config import AppConfig, GeneratorConfig
from fraud_intel.domain.schema import transaction_columns


@dataclass(frozen=True)
class _World:
    """Facts shared by every window: countries, the customer pool, and card BINs."""

    countries: np.ndarray
    country_shares: np.ndarray
    utc_offset_hours: dict[str, int]
    customers: dict[str, np.ndarray]
    customer_weights: dict[str, np.ndarray]
    bins: dict[str, np.ndarray]
    bad_bins: dict[str, np.ndarray]


@dataclass(frozen=True)
class _Rows:
    """Column arrays for one slice of transactions, before booking details are added."""

    timestamps: pd.DatetimeIndex
    customer_ids: np.ndarray
    countries: np.ndarray
    ip_countries: np.ndarray
    methods: np.ndarray
    bins: np.ndarray
    amounts: np.ndarray
    approved: np.ndarray
    is_fraud: np.ndarray
    lead_days: np.ndarray


def generate_transactions(config: AppConfig) -> pd.DataFrame:
    """Build 2 x `days_per_window` days of transactions; the same config always gives the same frame."""
    if config is None:
        raise ValueError("config is required")
    settings = config.generator
    rng = np.random.default_rng(config.seed)
    world = _build_world(rng=rng, config=config)

    window_end = datetime.combine(settings.end_date, time()) + timedelta(days=1)
    scored_start = window_end - timedelta(days=settings.days_per_window)
    history_start = scored_start - timedelta(days=settings.days_per_window)
    days = np.arange(settings.days_per_window)
    # the fraud wave starts a few days into the scored window, like the jump SkyRoute saw three weeks ago
    scored_rates = np.where(days < settings.spike_start_day, settings.history_fraud_rate, settings.scored_fraud_rate)
    history_rates = np.full(settings.days_per_window, settings.history_fraud_rate)

    slices = [
        _baseline(rng=rng, world=world, settings=settings, start=history_start, rates=history_rates, prefix="h"),
        _baseline(rng=rng, world=world, settings=settings, start=scored_start, rates=scored_rates, prefix="s"),
        _ar_cluster(rng=rng, world=world, settings=settings, scored_start=scored_start),
        _velocity_bursts(rng=rng, world=world, settings=settings, scored_start=scored_start),
        _card_testing(rng=rng, world=world, settings=settings, scored_start=scored_start),
        _legit_high_value(rng=rng, world=world, settings=settings, scored_start=scored_start),
    ]
    frame = pd.concat([_to_frame(rng=rng, rows=rows) for rows in slices], ignore_index=True)
    frame = frame.sort_values(["timestamp_utc", "customer_id"], kind="stable").reset_index(drop=True)
    frame.insert(0, "transaction_id", [f"tx_{index:06d}" for index in range(1, len(frame) + 1)])
    return frame.loc[:, list(transaction_columns())]


def write_transactions(frame: pd.DataFrame, path: Path) -> None:
    """Write gzipped CSV with a fixed gzip timestamp, so the same data gives a byte-identical file."""
    if frame is None or path is None:
        raise ValueError("frame and path are required")
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, compression={"method": "gzip", "mtime": 0}, date_format="%Y-%m-%d %H:%M:%S")


def _build_world(rng: np.random.Generator, config: AppConfig) -> _World:
    settings = config.generator
    countries = np.array(list(settings.country_shares))
    shares = np.array([settings.country_shares[country] for country in countries])
    customers, weights, bins, bad_bins = {}, {}, {}, {}
    for country, share in zip(countries, shares, strict=True):
        pool_size = round(share * settings.customers_per_window * 2)
        customers[country] = np.array([f"c_{country.lower()}{index:06d}" for index in range(pool_size)])
        # a few loyal customers book often and most book once, like real travel demand
        raw_weights = rng.pareto(a=2.0, size=pool_size) + 1
        weights[country] = raw_weights / raw_weights.sum()
        country_bins = rng.choice(np.arange(400000, 560000), size=settings.bins_per_country, replace=False)
        bins[country] = country_bins.astype(str)
        bad_bins[country] = bins[country][: settings.bad_bins_per_country]
    return _World(
        countries=countries,
        country_shares=shares / shares.sum(),
        utc_offset_hours=config.utc_offset_hours,
        customers=customers,
        customer_weights=weights,
        bins=bins,
        bad_bins=bad_bins,
    )


def _baseline(
    rng: np.random.Generator,
    world: _World,
    settings: GeneratorConfig,
    start: datetime,
    rates: np.ndarray,
    prefix: str,
) -> _Rows:
    size = settings.transactions_per_window
    countries = rng.choice(world.countries, size=size, p=world.country_shares)
    methods = _draw_methods(rng=rng, settings=settings, countries=countries)
    customer_ids = _draw_customers(rng=rng, world=world, countries=countries)
    day = rng.integers(0, settings.days_per_window, size=size)
    local_hour = rng.choice(24, size=size, p=_hour_profile())
    amounts = _amounts(rng=rng, settings=settings, size=size)
    lead_days = _lead_days(rng=rng, settings=settings, size=size)
    approved = rng.random(size) < settings.approval_rate
    # some countries carry more fraud; normalize so the window still hits its target rate
    country_weight = pd.Series(countries).map(_country_fraud_weight()).to_numpy()
    fraud_probability = rates[day] * country_weight / country_weight.mean()
    is_fraud = approved & (rng.random(size) < fraud_probability)
    ip_countries = countries.copy()
    abroad = rng.random(size) < settings.legit_ip_abroad_share
    ip_countries[abroad] = rng.choice(_travel_countries(), size=int(abroad.sum()))

    # fraud leans toward the signals analysts know: new accounts, cards, night, foreign IPs, last-minute trips
    fraud_count = int(is_fraud.sum())
    customer_ids[is_fraud] = [f"c_{prefix}f{index:06d}" for index in range(fraud_count)]
    methods[is_fraud] = np.where(rng.random(fraud_count) < 0.8, "card", methods[is_fraud])
    local_hour[is_fraud] = np.where(
        rng.random(fraud_count) < 0.4, rng.integers(0, 6, size=fraud_count), local_hour[is_fraud]
    )
    amounts[is_fraud] = np.round(amounts[is_fraud] * rng.uniform(1.2, 2.5, size=fraud_count), 2)
    ip_countries[is_fraud] = np.where(
        rng.random(fraud_count) < 0.45, rng.choice(_foreign_ip_countries(), size=fraud_count), countries[is_fraud]
    )
    lead_days[is_fraud] = np.where(
        rng.random(fraud_count) < 0.35, rng.uniform(0.0, 2.0, size=fraud_count), lead_days[is_fraud]
    )
    bad_bin_probability = np.where(is_fraud, 0.35, 0.0)
    bins = _draw_bins(rng=rng, world=world, countries=countries, methods=methods, bad_probability=bad_bin_probability)
    timestamps = _timestamps(rng=rng, world=world, start=start, day=day, local_hour=local_hour, countries=countries)
    return _Rows(
        timestamps, customer_ids, countries, ip_countries, methods, bins, amounts, approved, is_fraud, lead_days
    )


def _ar_cluster(rng: np.random.Generator, world: _World, settings: GeneratorConfig, scored_start: datetime) -> _Rows:
    size = round(settings.ar_cluster_share * settings.transactions_per_window)
    countries = np.full(size, "AR")
    methods = np.full(size, "card")
    cluster_start = scored_start + pd.Timedelta(days=settings.ar_cluster_start_day)
    timestamps = pd.DatetimeIndex(cluster_start + pd.to_timedelta(np.sort(rng.uniform(0, 72, size=size)), unit="h"))
    ip_countries = np.where(rng.random(size) < 0.6, rng.choice(_foreign_ip_countries(), size=size), "AR")
    lead_days = np.where(rng.random(size) < 0.5, rng.uniform(0.0, 2.0, size=size), _lead_days(rng, settings, size))
    bins = _draw_bins(rng=rng, world=world, countries=countries, methods=methods, bad_probability=np.full(size, 0.5))
    return _Rows(
        timestamps=timestamps,
        customer_ids=np.array([f"c_arf{index:05d}" for index in range(size)]),
        countries=countries,
        ip_countries=ip_countries,
        methods=methods,
        bins=bins,
        amounts=np.round(rng.uniform(800, 2600, size=size), 2),
        approved=np.full(size, True),
        is_fraud=np.full(size, True),
        lead_days=lead_days,
    )


def _velocity_bursts(
    rng: np.random.Generator, world: _World, settings: GeneratorConfig, scored_start: datetime
) -> _Rows:
    burst_count = round(settings.velocity_customer_share * settings.transactions_per_window)
    parts = []
    for burst in range(burst_count):
        attempts = int(rng.integers(3, 5))
        country = str(rng.choice(world.countries, p=world.country_shares))
        burst_start = scored_start + pd.Timedelta(minutes=float(rng.uniform(0, settings.days_per_window * 1440 - 60)))
        timestamps = pd.DatetimeIndex(burst_start + pd.to_timedelta(np.sort(rng.uniform(0, 55, attempts)), unit="m"))
        countries = np.full(attempts, country)
        methods = np.full(attempts, "card")
        # about a third are families booking several tickets at once: same pattern, no fraud
        is_family = rng.random() < 0.3
        if is_family:
            customer = _draw_customers(rng=rng, world=world, countries=np.array([country]))[0]
            approved = np.full(attempts, True)
            is_fraud = np.full(attempts, False)
            ip_countries = countries.copy()
        else:
            customer = f"c_vbf{burst:05d}"
            approved = rng.random(attempts) < 0.75
            is_fraud = approved & (rng.random(attempts) < 0.85)
            ip_countries = np.full(attempts, rng.choice(_foreign_ip_countries()) if rng.random() < 0.4 else country)
        parts.append(
            _Rows(
                timestamps=timestamps,
                customer_ids=np.full(attempts, customer),
                countries=countries,
                ip_countries=ip_countries,
                methods=methods,
                bins=_draw_bins(rng, world, countries, methods, np.full(attempts, 0.3)),
                amounts=np.round(rng.uniform(300, 1200, size=attempts), 2),
                approved=approved,
                is_fraud=is_fraud,
                lead_days=_lead_days(rng=rng, settings=settings, size=attempts),
            )
        )
    return _concat_rows(parts=parts)


def _card_testing(rng: np.random.Generator, world: _World, settings: GeneratorConfig, scored_start: datetime) -> _Rows:
    tester_count = round(settings.card_testing_customer_share * settings.transactions_per_window)
    parts = []
    for tester in range(tester_count):
        attempts = int(rng.integers(5, 8))
        country = str(rng.choice(world.countries, p=world.country_shares))
        start = scored_start + pd.Timedelta(minutes=float(rng.uniform(0, settings.days_per_window * 1440 - 60)))
        timestamps = pd.DatetimeIndex(start + pd.to_timedelta(np.sort(rng.uniform(0, 40, attempts)), unit="m"))
        # small probes get declined on stolen cards until one works, then the big booking goes through
        approved = np.full(attempts, False)
        approved[-1] = rng.random() < 0.7
        amounts = np.round(rng.uniform(50, 300, size=attempts), 2)
        amounts[-1] = round(float(rng.uniform(600, 1800)), 2)
        parts.append(
            _Rows(
                timestamps=timestamps,
                customer_ids=np.full(attempts, f"c_ctf{tester:05d}"),
                countries=np.full(attempts, country),
                ip_countries=np.full(attempts, rng.choice(_foreign_ip_countries())),
                methods=np.full(attempts, "card"),
                bins=rng.choice(world.bins[country], size=attempts, replace=False),
                amounts=amounts,
                approved=approved,
                is_fraud=approved.copy(),
                lead_days=rng.uniform(0.0, 3.0, size=attempts),
            )
        )
    return _concat_rows(parts=parts)


def _legit_high_value(
    rng: np.random.Generator, world: _World, settings: GeneratorConfig, scored_start: datetime
) -> _Rows:
    # business trips and family holidays: big, honest bookings that a naive "high value" rule would flag
    size = round(settings.legit_high_value_share * settings.transactions_per_window)
    countries = rng.choice(world.countries, size=size, p=world.country_shares)
    customer_ids = _draw_customers(rng=rng, world=world, countries=countries)
    first_timers = rng.random(size) < 0.3
    customer_ids[first_timers] = [f"c_lhv{index:05d}" for index in range(int(first_timers.sum()))]
    methods = np.where(rng.random(size) < 0.7, "card", _draw_methods(rng=rng, settings=settings, countries=countries))
    ip_countries = np.where(rng.random(size) < 0.2, rng.choice(_travel_countries(), size=size), countries)
    day = rng.integers(0, settings.days_per_window, size=size)
    local_hour = rng.integers(8, 21, size=size)
    return _Rows(
        timestamps=_timestamps(
            rng=rng, world=world, start=scored_start, day=day, local_hour=local_hour, countries=countries
        ),
        customer_ids=customer_ids,
        countries=countries,
        ip_countries=ip_countries,
        methods=methods,
        bins=_draw_bins(rng, world, countries, methods, np.zeros(size)),
        amounts=np.round(rng.uniform(1500, 5000, size=size), 2),
        approved=np.full(size, True),
        is_fraud=np.full(size, False),
        lead_days=_lead_days(rng=rng, settings=settings, size=size),
    )


def _to_frame(rng: np.random.Generator, rows: _Rows) -> pd.DataFrame:
    size = len(rows.timestamps)
    domestic = rng.random(size) < 0.6
    destinations = np.where(domestic, rows.countries, rng.choice(_travel_countries(), size=size))
    # departure at midday local, never before the booking time
    departure = (rows.timestamps + pd.to_timedelta(rows.lead_days * 24 + 12, unit="h")).normalize()
    return pd.DataFrame(
        {
            "timestamp_utc": rows.timestamps.to_numpy().astype("datetime64[s]"),
            "customer_id": rows.customer_ids,
            "customer_email": [
                f"{customer}@{domain}"
                for customer, domain in zip(
                    rows.customer_ids,
                    rng.choice(["gmail.com", "hotmail.com", "outlook.com", "yahoo.com"], size=size),
                    strict=True,
                )
            ],
            "billing_country": rows.countries,
            "ip_country": rows.ip_countries,
            "payment_method": rows.methods,
            "card_bin": rows.bins,
            "amount_usd": rows.amounts,
            "status": np.where(rows.approved, "approved", "declined"),
            "is_fraud": rows.is_fraud,
            "booking_type": rng.choice(["flight", "hotel"], size=size, p=[0.7, 0.3]),
            "destination_country": destinations,
            "departure_date": departure.strftime("%Y-%m-%d"),
        }
    )


def _concat_rows(parts: list[_Rows]) -> _Rows:
    return _Rows(
        timestamps=pd.DatetimeIndex(np.concatenate([part.timestamps.to_numpy() for part in parts])),
        customer_ids=np.concatenate([part.customer_ids for part in parts]),
        countries=np.concatenate([part.countries for part in parts]),
        ip_countries=np.concatenate([part.ip_countries for part in parts]),
        methods=np.concatenate([part.methods for part in parts]),
        bins=np.concatenate([part.bins for part in parts]),
        amounts=np.concatenate([part.amounts for part in parts]),
        approved=np.concatenate([part.approved for part in parts]),
        is_fraud=np.concatenate([part.is_fraud for part in parts]),
        lead_days=np.concatenate([part.lead_days for part in parts]),
    )


def _draw_methods(rng: np.random.Generator, settings: GeneratorConfig, countries: np.ndarray) -> np.ndarray:
    methods = np.empty(len(countries), dtype=object)
    for country, mix in settings.method_mix.items():
        in_country = countries == country
        names = list(mix)
        shares = np.array([mix[name] for name in names])
        methods[in_country] = rng.choice(names, size=int(in_country.sum()), p=shares / shares.sum())
    return methods.astype(str)


def _draw_customers(rng: np.random.Generator, world: _World, countries: np.ndarray) -> np.ndarray:
    customer_ids = np.empty(len(countries), dtype=object)
    for country in world.countries:
        in_country = countries == country
        customer_ids[in_country] = rng.choice(
            world.customers[country], size=int(in_country.sum()), p=world.customer_weights[country]
        )
    return customer_ids.astype(str)


def _draw_bins(
    rng: np.random.Generator, world: _World, countries: np.ndarray, methods: np.ndarray, bad_probability: np.ndarray
) -> np.ndarray:
    bins = np.full(len(countries), "", dtype=object)
    use_bad = rng.random(len(countries)) < bad_probability
    for country in world.countries:
        normal_rows = (countries == country) & (methods == "card") & ~use_bad
        bad_rows = (countries == country) & (methods == "card") & use_bad
        bins[normal_rows] = rng.choice(world.bins[country], size=int(normal_rows.sum()))
        bins[bad_rows] = rng.choice(world.bad_bins[country], size=int(bad_rows.sum()))
    return bins.astype(str)


def _timestamps(
    rng: np.random.Generator,
    world: _World,
    start: datetime,
    day: np.ndarray,
    local_hour: np.ndarray,
    countries: np.ndarray,
) -> pd.DatetimeIndex:
    offsets = pd.Series(countries).map(world.utc_offset_hours).to_numpy()
    # local hour minus the UTC offset gives the UTC hour; wrap so late-evening bookings stay inside the window
    window_hours = (int(day.max()) + 1) * 24 if len(day) else 24
    hours = (day * 24 + local_hour - offsets + rng.uniform(0, 1, size=len(day))) % window_hours
    return pd.DatetimeIndex(start + pd.to_timedelta(hours, unit="h"))


def _amounts(rng: np.random.Generator, settings: GeneratorConfig, size: int) -> np.ndarray:
    # lognormal keeps amounts positive with a long tail; mu is set so the mean lands on mean_amount_usd
    mu = np.log(settings.mean_amount_usd) - settings.amount_sigma**2 / 2
    return np.round(rng.lognormal(mean=mu, sigma=settings.amount_sigma, size=size), 2)


def _lead_days(rng: np.random.Generator, settings: GeneratorConfig, size: int) -> np.ndarray:
    lead = np.exp(rng.normal(loc=np.log(21), scale=0.8, size=size))
    last_minute = rng.random(size) < settings.last_minute_share
    lead[last_minute] = rng.uniform(0.0, 2.0, size=int(last_minute.sum()))
    return lead


def _hour_profile() -> np.ndarray:
    # people book during the day and evening; nights are quiet
    weights = np.array([1, 0.6, 0.4, 0.3, 0.3, 0.4, 1, 2, 3, 4, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 4, 3, 2, 1.5])
    return weights / weights.sum()


def _country_fraud_weight() -> dict[str, float]:
    return {"BR": 0.7, "MX": 1.3, "CO": 1.0, "AR": 2.5, "CL": 0.8}


def _travel_countries() -> list[str]:
    return ["BR", "MX", "CO", "AR", "CL", "US", "ES", "PE"]


def _foreign_ip_countries() -> list[str]:
    return ["RU", "UA", "RO", "NG", "VN", "US", "BR", "MX", "CO", "AR", "CL"]
