"""Command line entry point: generate data, ingest batches, export reports."""

import argparse
from datetime import date
from pathlib import Path

from fraud_intel.application.stream import run_pipeline
from fraud_intel.config import AppConfig, load_config
from fraud_intel.domain.report import daily_top, summary_insight
from fraud_intel.infrastructure.generator import generate_transactions, write_transactions
from fraud_intel.infrastructure.storage import ConsoleNotifier, DuckDbStore, read_transactions_file


def main() -> None:
    parser = argparse.ArgumentParser(prog="fraud-intel")
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("generate", help="write the seeded synthetic dataset to data_path")
    ingest = commands.add_parser("ingest", help="add a CSV/JSON batch, then replay the stream to rescore")
    ingest.add_argument("--file", type=Path, required=True)
    ingest.add_argument("--quiet", action="store_true", help="do not print each alert")
    report = commands.add_parser("report", help="export the daily Top 50 high-risk transactions")
    report.add_argument("--date", type=date.fromisoformat, required=True)
    report.add_argument("--out", type=Path, default=Path("reports"))
    args = parser.parse_args()

    config = load_config(path=args.config)
    if args.command == "generate":
        _generate(config=config)
    elif args.command == "ingest":
        _ingest(config=config, path=args.file, quiet=args.quiet)
    elif args.command == "report":
        _report(config=config, day=args.date, out_dir=args.out)


def _generate(config: AppConfig) -> None:
    frame = generate_transactions(config=config)
    write_transactions(frame=frame, path=config.data_path)
    print(f"wrote {len(frame):,} transactions to {config.data_path}")


class _QuietNotifier:
    def notify(self, alert) -> None:
        return None


def _ingest(config: AppConfig, path: Path, quiet: bool) -> None:
    store = DuckDbStore(path=config.database_path, read_only=False)
    added = store.append_raw(frame=read_transactions_file(path=path))
    notifier = _QuietNotifier() if quiet else ConsoleNotifier()
    result = run_pipeline(store=store, config=config, notifier=notifier)
    print(
        f"added {added:,} new transactions; streamed {result.events:,}, scored {result.scored:,}, "
        f"{result.high_risk:,} high risk, {result.alerts:,} alerts"
    )


def _report(config: AppConfig, day: date, out_dir: Path) -> None:
    scored = DuckDbStore(path=config.database_path, read_only=True).load_scored()
    top = daily_top(frame=scored, day=day, top_n=50)
    out_dir.mkdir(parents=True, exist_ok=True)
    top.to_csv(out_dir / f"top50_{day}.csv", index=False)
    top.to_json(out_dir / f"top50_{day}.json", orient="records", date_format="iso", indent=2)
    print(summary_insight(top=top))
    print(f"wrote {len(top)} rows to {out_dir}/top50_{day}.csv and .json")
