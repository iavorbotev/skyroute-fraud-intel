"""Command line entry point: generate data, ingest batches, export reports."""

import argparse
from pathlib import Path

from fraud_intel.config import load_config


def main() -> None:
    parser = argparse.ArgumentParser(prog="fraud-intel")
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    parser.add_subparsers(dest="command", required=True)
    args = parser.parse_args()
    load_config(path=args.config)
