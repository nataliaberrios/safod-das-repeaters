"""Build a coverage/configuration ledger from raw headers and manifests.

The pilot ledger samples HDF5 headers while counting every filename.  Its
``coverage_level`` is therefore explicit: it is not a substitute for the full
header/UUID audit needed before completeness or recurrence-interval claims.
"""

from __future__ import annotations

import csv
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

import numpy as np

from .common import iso_utc, utc_now
from .h5io import discover_h5, filename_epoch, read_header


ANY_ISO_TIME = re.compile(r"(20\d{2}-\d{2}-\d{2}T\d{6}Z)")


def _uniform_sample(paths: Sequence[Path], count: int) -> List[Path]:
    if not paths:
        return []
    count = max(1, min(int(count), len(paths)))
    indices = np.unique(np.linspace(0, len(paths) - 1, count).round().astype(int))
    return [paths[int(index)] for index in indices]


def _filename_span(paths: Sequence[Path]) -> Tuple[str, str]:
    epochs = [filename_epoch(path) for path in paths]
    valid = [epoch for epoch in epochs if epoch is not None]
    if not valid:
        return "", ""
    return iso_utc(min(valid)), iso_utc(max(valid))


def summarize_h5_root(
    name: str, root: Path, header_sample_count: int
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    paths = discover_h5(root)
    filename_start, filename_end = _filename_span(paths)
    sample = _uniform_sample(paths, header_sample_count)
    headers = [read_header(path).to_dict() for path in sample]
    ok = [row for row in headers if row["status"] == "ok"]
    config_counter = Counter(
        (
            row["sample_rate_hz"],
            row["channel_count"],
            row["channel_spacing_m"],
            row["gauge_length_m"],
            row["start_locus_index"],
            row["raw_data_unit"],
        )
        for row in ok
    )
    root_uuids = [row["root_uuid"] for row in ok if row["root_uuid"]]
    duplicate_sample_uuids = len(root_uuids) - len(set(root_uuids))
    summary = {
        "source_name": name,
        "kind": "optasense_h5_root",
        "path": str(root),
        "exists": root.exists(),
        "file_count": len(paths),
        "valid_record_count": "",
        "placeholder_count": "",
        "filename_start_utc": filename_start,
        "filename_end_utc": filename_end,
        "headers_read": len(headers),
        "header_errors": len(headers) - len(ok),
        "sample_duplicate_root_uuids": duplicate_sample_uuids,
        "sampled_configurations": json.dumps(
            [
                {
                    "sample_rate_hz": key[0],
                    "channel_count": key[1],
                    "channel_spacing_m": key[2],
                    "gauge_length_m": key[3],
                    "start_locus_index": key[4],
                    "raw_data_unit": key[5],
                    "sample_count": value,
                }
                for key, value in sorted(config_counter.items(), key=lambda item: str(item[0]))
            ],
            separators=(",", ":"),
        ),
        "coverage_level": "filename_complete_header_sampled",
        "generated_utc": utc_now(),
    }
    for row in headers:
        row["source_name"] = name
    return summary, headers


def summarize_manifest(name: str, path: Path) -> Dict[str, Any]:
    total = 0
    valid = 0
    placeholder = 0
    starts: List[str] = []
    ends: List[str] = []
    modes: Counter = Counter()
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        header = handle.readline().split()
        expected = [
            "system",
            "file",
            "nSamples",
            "fs",
            "Desample",
            "startTime",
            "endTime",
            "nChannels",
            "dCh",
            "GaugeLen",
            "firstSample",
        ]
        if header != expected:
            raise ValueError("unexpected manifest header: {}".format(header))
        for line in handle:
            if not line.strip():
                continue
            total += 1
            fields = line.split()
            if len(fields) < len(expected):
                placeholder += 1
                continue
            row = dict(zip(expected, fields[: len(expected)]))
            try:
                n_samples = int(row["nSamples"])
                pulse_rate = float(row["fs"])
                decimation = int(row["Desample"])
                channel_count = int(row["nChannels"])
                spacing = float(row["dCh"])
                gauge = float(row["GaugeLen"])
            except ValueError:
                placeholder += 1
                continue
            if n_samples <= 0 or pulse_rate <= 0 or decimation <= 0:
                placeholder += 1
                continue
            valid += 1
            if row["startTime"] != "-1":
                starts.append(row["startTime"])
            if row["endTime"] != "-1":
                ends.append(row["endTime"])
            output_rate = pulse_rate / decimation
            modes[(output_rate, channel_count, spacing, gauge)] += 1
    return {
        "source_name": name,
        "kind": "whitespace_manifest",
        "path": str(path),
        "exists": path.exists(),
        "file_count": total,
        "valid_record_count": valid,
        "placeholder_count": placeholder,
        "filename_start_utc": min(starts) if starts else "",
        "filename_end_utc": max(ends) if ends else "",
        "headers_read": 0,
        "header_errors": "",
        "sample_duplicate_root_uuids": "",
        "sampled_configurations": json.dumps(
            [
                {
                    "sample_rate_hz": key[0],
                    "channel_count": key[1],
                    "channel_spacing_m": key[2],
                    "gauge_length_m": key[3],
                    "record_count": value,
                }
                for key, value in sorted(modes.items())
            ],
            separators=(",", ":"),
        ),
        "coverage_level": "manifest_rows_complete_headers_not_rechecked",
        "generated_utc": utc_now(),
    }


def summarize_protobuf_root(name: str, root: Path) -> Dict[str, Any]:
    paths = sorted(root.rglob("*.pb"), key=lambda item: str(item))
    timestamps = []
    for path in paths:
        match = ANY_ISO_TIME.search(path.name)
        if match is not None:
            timestamps.append(match.group(1))
    return {
        "source_name": name,
        "kind": "protobuf_root",
        "path": str(root),
        "exists": root.exists(),
        "file_count": len(paths),
        "valid_record_count": "",
        "placeholder_count": "",
        "filename_start_utc": min(timestamps) if timestamps else "",
        "filename_end_utc": max(timestamps) if timestamps else "",
        "headers_read": 0,
        "header_errors": "",
        "sample_duplicate_root_uuids": "",
        "sampled_configurations": "[]",
        "coverage_level": "filename_only_requires_protobuf_packet_audit",
        "generated_utc": utc_now(),
    }


def build_coverage(config: Mapping[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    summaries: List[Dict[str, Any]] = []
    headers: List[Dict[str, Any]] = []
    sample_count = int(config["coverage"]["header_samples_per_root"])
    for source in config["coverage"]["sources"]:
        name = source["name"]
        kind = source["kind"]
        path = Path(config["paths"][source["path_key"]])
        if not path.exists():
            summaries.append(
                {
                    "source_name": name,
                    "kind": kind,
                    "path": str(path),
                    "exists": False,
                    "file_count": 0,
                    "coverage_level": "missing",
                    "generated_utc": utc_now(),
                }
            )
        elif kind == "optasense_h5_root":
            summary, source_headers = summarize_h5_root(name, path, sample_count)
            summaries.append(summary)
            headers.extend(source_headers)
        elif kind == "whitespace_manifest":
            summaries.append(summarize_manifest(name, path))
        elif kind == "protobuf_root":
            summaries.append(summarize_protobuf_root(name, path))
        else:
            raise ValueError("unsupported coverage source kind {}".format(kind))
    return summaries, headers


def write_rows(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        raise ValueError("cannot write an empty table")
    keys: List[str] = []
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

