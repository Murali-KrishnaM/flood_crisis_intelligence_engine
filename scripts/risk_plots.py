"""Standalone validation plots (matplotlib only).

Rainfall axes are labelled in SOURCE UNITS because the physical unit is not independently verified.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

SPLIT_COLOURS = {"train": "tab:blue", "val": "tab:orange", "test": "tab:green"}


def plot_rainfall(panel, threshold_value, out_path):
    fig, ax = plt.subplots(figsize=(12, 4))
    r = panel["local_rainfall_mean"]
    ax.plot(r.index, r.values, lw=0.7, marker=".", ms=2, color="tab:blue")  # NaN stays a gap
    ax.axhline(threshold_value, color="red", ls="--", lw=1,
               label=f"high_rainfall_stress threshold ({threshold_value:.4g}, source units)")
    ax.set_title("Local mean daily rainfall (gaps = not observed, NOT zero)")
    ax.set_ylabel("rainfall (source units; physical unit unverified)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_path, dpi=130)
    plt.close(fig)


def plot_risk(preds, severity_thresholds, trigger_threshold, out_path):
    fig, ax = plt.subplots(figsize=(12, 4))
    for sp, col in SPLIT_COLOURS.items():
        sub = preds[preds["split"] == sp]
        if len(sub):
            ax.axvspan(sub["date"].min(), sub["date"].max(), color=col, alpha=0.07, label=f"{sp} period")
    ax.plot(preds["date"], preds["p_xgboost"], lw=0.8, color="black", label="XGBoost risk score")
    pos = preds[preds["y_true"] == 1]
    ax.scatter(pos["date"], np.full(len(pos), -0.03), marker="|", color="red", s=40,
               label="observed high_rainfall_stress (next day)")
    for name, v in severity_thresholds.items():
        ax.axhline(v, ls=":", lw=0.8, color="grey")
        ax.text(preds["date"].min(), v + 0.01, name, fontsize=7, color="grey")
    ax.axhline(trigger_threshold, color="red", ls="--", lw=0.8, label="trigger_candidate threshold")
    ax.set_ylim(-0.06, 1.02)
    ax.set_title("Internal risk score for next-day high_rainfall_stress (research proxy)")
    ax.legend(fontsize=7, loc="upper left")
    fig.tight_layout()
    fig.savefig(out_path, dpi=130)
    plt.close(fig)


def plot_prob_vs_target(preds, out_path, split="test"):
    sub = preds[preds["split"] == split]
    if sub.empty:
        sub, split = preds, "all"
    rng = np.random.default_rng(0)
    x = sub["y_true"].values + rng.uniform(-0.08, 0.08, len(sub))
    fig, ax = plt.subplots(figsize=(5, 5))
    ax.scatter(x, sub["p_xgboost"], s=10, alpha=0.6)
    ax.set_xticks([0, 1])
    ax.set_xticklabels(["no stress", "high_rainfall_stress"])
    ax.set_ylabel("predicted probability")
    ax.set_title(f"Predicted probability vs target ({split} split)")
    fig.tight_layout()
    fig.savefig(out_path, dpi=130)
    plt.close(fig)


def plot_confusion(cm, out_path, title):
    cm = np.asarray(cm)
    fig, ax = plt.subplots(figsize=(4, 4))
    ax.imshow(cm, cmap="Blues")
    for i in range(2):
        for j in range(2):
            ax.text(j, i, int(cm[i, j]), ha="center", va="center",
                    color="white" if cm[i, j] > cm.max() / 2 else "black")
    ax.set_xticks([0, 1]); ax.set_yticks([0, 1])
    ax.set_xticklabels(["pred 0", "pred 1"]); ax.set_yticklabels(["true 0", "true 1"])
    ax.set_title(title, fontsize=9)
    fig.tight_layout()
    fig.savefig(out_path, dpi=130)
    plt.close(fig)