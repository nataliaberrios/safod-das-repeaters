#!/usr/bin/env python
"""Make a plain-language figure for the nearby-event family screen.

The screen is deliberately separated from DAS detectability: the five nearby
catalog events were selected by location and scored with the frozen seismic-
network comparison, while the 2026 DAS event and same-fiber control were
scored in the same way.  This figure does not assign new family membership.
"""

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
    nearby = pd.read_csv(
        ROOT / "outputs" / "incremental_value" / "prospective_network" / "frozen_network_decisions.csv"
    )
    named = pd.read_csv(
        ROOT / "outputs" / "incremental_value" / "deep_named_network" / "decisions.csv"
    )
    nearby = nearby[["event_id", "median_target_correlation", "median_target_differential_lag_rms_s", "frozen_decision"]].copy()
    nearby["group"] = "Nearby catalog event"
    target = named[named["event_id"].astype(str) == "75336682"].iloc[0]
    control = named[named["event_id"].astype(str) == "75343317"].iloc[0]
    rows = pd.concat(
        [
            nearby,
            pd.DataFrame(
                [
                    {
                        "event_id": "75336682",
                        "median_target_correlation": target["median_target_correlation"],
                        "median_target_differential_lag_rms_s": target["median_target_differential_lag_rms_s"],
                        "frozen_decision": target["frozen_decision"],
                        "group": "Deep DAS event",
                    },
                    {
                        "event_id": "75343317",
                        "median_target_correlation": control["median_target_correlation"],
                        "median_target_differential_lag_rms_s": control["median_target_differential_lag_rms_s"],
                        "frozen_decision": control["frozen_decision"],
                        "group": "Same-fiber comparison event",
                    },
                ]
            ),
        ],
        ignore_index=True,
    )
    rows["label"] = rows["event_id"].astype(str)
    rows.loc[rows["group"] == "Nearby catalog event", "label"] = "nearby " + rows.loc[rows["group"] == "Nearby catalog event", "event_id"].astype(str)
    rows = rows.sort_values("median_target_correlation", ascending=False).reset_index(drop=True)
    y = np.arange(len(rows))
    colors = rows["group"].map(
        {"Deep DAS event": "#1b9e77", "Same-fiber comparison event": "#7570b3", "Nearby catalog event": "#bdbdbd"}
    )
    fig, axes = plt.subplots(1, 2, figsize=(12.5, 5.4), constrained_layout=True, sharey=True)
    axes[0].barh(y, rows["median_target_correlation"], color=colors, edgecolor="0.2", linewidth=0.4)
    axes[0].axvline(float(target["correlation_threshold"]), color="#d95f02", linestyle="--", linewidth=1.5)
    axes[0].set_xlim(0, 1.04)
    axes[0].set_xlabel("Median waveform correlation")
    axes[0].set_title("Waveform similarity")
    axes[0].grid(axis="x", alpha=0.2)
    axes[1].barh(y, 1000.0 * rows["median_target_differential_lag_rms_s"], color=colors, edgecolor="0.2", linewidth=0.4)
    axes[1].axvline(1000.0 * float(target["differential_lag_rms_threshold_s"]), color="#d95f02", linestyle="--", linewidth=1.5)
    axes[1].set_xlabel("Median station timing mismatch (ms)")
    axes[1].set_title("Arrival-time consistency")
    axes[1].grid(axis="x", alpha=0.2)
    axes[0].set_yticks(y, rows["label"])
    axes[0].invert_yaxis()
    axes[0].set_ylabel("Event")
    axes[0].text(0.02, 1.02, "higher is better", transform=axes[0].transAxes, fontsize=9, color="0.35")
    axes[1].text(0.02, 1.02, "lower is better", transform=axes[1].transAxes, fontsize=9, color="0.35")
    fig.suptitle("Only the deep DAS event passes the known-family test", fontsize=15, fontweight="bold")
    fig.text(
        0.5,
        -0.015,
        "Five nearby catalog events and one same-fiber comparison event fail the same frozen seismic-network test.\n"
        "The family label is provisional because published catalogs disagree; this is not a new-family claim.",
        ha="center",
        va="top",
        fontsize=9.5,
        color="0.25",
    )
    handles = [
        plt.Rectangle((0, 0), 1, 1, color="#1b9e77", label="Deep DAS event"),
        plt.Rectangle((0, 0), 1, 1, color="#7570b3", label="Same-fiber comparison event"),
        plt.Rectangle((0, 0), 1, 1, color="#bdbdbd", label="Nearby catalog event"),
    ]
    fig.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, 0.93), ncol=3, frameon=False)
    OUT.mkdir(parents=True, exist_ok=True)
    for suffix, kwargs in [("png", {"dpi": 300}), ("pdf", {}), ("svg", {})]:
        fig.savefig(OUT / f"figure_deep_das_neighborhood_screen.{suffix}", **kwargs)
    plt.close(fig)
    status = {
        "nearby_catalog_event_count": int(len(nearby)),
        "nearby_catalog_events_passing": int((nearby["frozen_decision"] == "target_family").sum()),
        "deep_das_event_id": "75336682",
        "deep_das_event_decision": str(target["frozen_decision"]),
        "same_fiber_comparison_event_id": "75343317",
        "same_fiber_comparison_decision": str(control["frozen_decision"]),
        "interpretation": "The deep DAS event is the only event in this small comparison set that passes the frozen known-family test. Nearby events were selected by location, not by family waveform score. This is a family-verification result, not a catalog-extension, slip-rate, or stress-drop result.",
    }
    (OUT / "figure_deep_das_neighborhood_screen_status.json").write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
