#!/usr/bin/env python
"""Plot how the DAS detector ranked known local events in development data."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "deep_das"


def main() -> None:
    rows = pd.read_csv(ROOT / "outputs" / "development_das" / "network_comparison_adjudicated.csv")
    raw = pd.read_csv(ROOT / "outputs" / "development_das" / "candidate_detections_raw.csv")
    matched = rows[rows["cross_sensor_known_event_match"].astype(str).str.lower() == "true"]
    score = raw[["candidate_id", "coincidence_score"]].copy()
    score = score.sort_values("coincidence_score", ascending=False).reset_index(drop=True)
    score["rank"] = np.arange(1, len(score) + 1)
    match_by_candidate = {str(candidate): str(int(float(event_id))) for candidate, event_id in zip(matched["DAS_candidate_id"].astype(str), matched["network_known_event_id"])}
    score["known_event_id"] = score["candidate_id"].astype(str).map(match_by_candidate)
    score["is_known"] = score["known_event_id"].notna()
    colors = np.where(score["is_known"], "#1b9e77", "#bdbdbd")
    fig, axes = plt.subplots(1, 2, figsize=(12.5, 5.3), gridspec_kw={"width_ratios": [1.65, 1]}, constrained_layout=True)
    axes[0].bar(score["rank"], score["coincidence_score"], color=colors, width=0.88, edgecolor="0.25", linewidth=0.25)
    threshold = float(raw["coincidence_score"].min())
    axes[0].axhline(threshold, color="#d95f02", linestyle="--", linewidth=1.2, label="DAS detection threshold")
    for _, row in score[score["is_known"]].iterrows():
        axes[0].annotate(
            f"known event {row['known_event_id']}",
            xy=(row["rank"], row["coincidence_score"]),
            xytext=(8, 8),
            textcoords="offset points",
            fontsize=9,
            color="#1b6e52",
            arrowprops={"arrowstyle": "-", "color": "#1b9e77", "lw": 0.8},
        )
    axes[0].set_xlabel("DAS detection rank (1 = strongest)")
    axes[0].set_ylabel("DAS spatial-coincidence score")
    axes[0].set_title("The two known events were the strongest DAS detections")
    axes[0].set_xlim(0, len(score) + 1)
    axes[0].grid(axis="y", alpha=0.2)
    axes[0].legend(loc="upper right", frameon=False, fontsize=8)

    known_scores = score.loc[score["is_known"], "coincidence_score"]
    other_scores = score.loc[~score["is_known"], "coincidence_score"]
    axes[1].boxplot([known_scores, other_scores], labels=["2 known\nlocal events", "63 other DAS\ndetections"], patch_artist=True, widths=0.55, showfliers=False,
                    boxprops={"facecolor": "#d9f0e3", "color": "#1b9e77"},
                    medianprops={"color": "#1b6e52", "linewidth": 2},
                    whiskerprops={"color": "#555"}, capprops={"color": "#555"})
    axes[1].scatter(np.ones(len(known_scores)) + 0.05 * np.linspace(-1, 1, len(known_scores)), known_scores, color="#1b9e77", zorder=3, s=28)
    axes[1].scatter(2 + 0.05 * np.linspace(-1, 1, len(other_scores)), other_scores, color="#777", alpha=0.2, zorder=2, s=12)
    axes[1].set_ylabel("DAS spatial-coincidence score")
    axes[1].set_title("Score separation")
    axes[1].grid(axis="y", alpha=0.2)
    fig.suptitle("DAS ranked both known local earthquakes above all other detections", fontsize=15, fontweight="bold")
    fig.text(
        0.5,
        -0.015,
        "Development result: 65 initial DAS detections in one 50-minute interval. The two highlighted detections were matched afterward to known local catalog events.\n"
        "The remaining detections are not automatically earthquakes; this is evidence that the detector can recover known events, not a catalog-extension claim.",
        ha="center",
        va="top",
        fontsize=9.5,
        color="0.25",
    )
    OUT.mkdir(parents=True, exist_ok=True)
    for suffix, kwargs in [("png", {"dpi": 300}), ("pdf", {}), ("svg", {})]:
        fig.savefig(OUT / f"figure_deep_das_known_event_ranking.{suffix}", **kwargs)
    plt.close(fig)
    status = {
        "interval": "2025-01-20T04:55:00Z to 2025-01-20T05:45:00Z",
        "initial_das_detection_count": int(len(score)),
        "known_local_event_count": int(score["is_known"].sum()),
        "known_local_event_ranks": [int(x) for x in score.loc[score["is_known"], "rank"]],
        "known_local_event_ids": sorted(score.loc[score["is_known"], "known_event_id"].astype(str).tolist()),
        "next_best_other_score": float(other_scores.max()),
        "minimum_known_score": float(known_scores.min()),
        "score_margin": float(known_scores.min() - other_scores.max()),
        "interpretation": "In this development interval, both known local target events were the two strongest DAS detections. This supports DAS recovery of known events, but the other detections have not been independently validated as earthquakes.",
    }
    (OUT / "figure_deep_das_known_event_ranking_status.json").write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
