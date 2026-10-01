"""Command line entry point: generate data, ingest batches, export reports."""

import argparse
from pathlib import Path

from fraud_intel.config import AppConfig, load_config
from fraud_intel.infrastructure.generator import generate_transactions, write_transactions


def main() -> None:
    parser = argparse.ArgumentParser(prog="fraud-intel")
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("generate", help="write the seeded synthetic dataset to data_path")
    args = parser.parse_args()
    config = load_config(path=args.config)
    if args.command == "generate":
        _generate(config=config)


def _generate(config: AppConfig) -> None:
    frame = generate_transactions(config=config)
    write_transactions(frame=frame, path=config.data_path)
    print(f"wrote {len(frame):,} transactions to {config.data_path}")
