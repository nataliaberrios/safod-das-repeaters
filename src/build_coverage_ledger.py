#!/usr/bin/env python
"""CLI for the sampled raw-header and archive coverage ledger."""

from __future__ import annotations

import argparse
from pathlib import Path

from .common import load_config, project_root
from .coverage import build_coverage, write_rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument(
        "--output-dir", type=Path, default=project_root() / "outputs" / "coverage"
    )
    args = parser.parse_args()
    config = load_config(args.config)
    summaries, headers = build_coverage(config)
    write_rows(args.output_dir / "coverage_sources.csv", summaries)
    write_rows(args.output_dir / "sampled_h5_headers.csv", headers)
    print("wrote {} source rows and {} sampled headers to {}".format(
        len(summaries), len(headers), args.output_dir
    ))


if __name__ == "__main__":
    main()

