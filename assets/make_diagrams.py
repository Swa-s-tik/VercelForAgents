#!/usr/bin/env python3
"""Render agentctl's architecture + pipeline diagrams to PNG (no external deps beyond matplotlib).

    python assets/make_diagrams.py     # writes assets/architecture.png and assets/pipeline.png

Kept in-repo so the diagrams are reproducible and stay in sync with the design.
"""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

# ---- palette (matches the landing page) ------------------------------------
BG = "#0b0e16"
PANEL = "#141b2b"
TXT = "#e8eef7"
MUT = "#93a1b8"
CYAN = "#37e0c8"
VIOLET = "#8b7bff"
AMBER = "#ffb14e"
GREEN = "#37d17f"
RED = "#ff6b6b"
BLUE = "#4aa8ff"
GREY = "#3a4866"


def box(ax, x, y, w, h, title, sub="", accent=GREY, fs_title=13, fs_sub=10.0):
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h, boxstyle="round,pad=0.4,rounding_size=2.4",
        linewidth=2.0, edgecolor=accent, facecolor=PANEL, zorder=2))
    cx = x + w / 2
    if sub:
        ax.text(cx, y + h * 0.64, title, ha="center", va="center",
                color=accent, fontsize=fs_title, fontweight="bold", zorder=3)
        ax.text(cx, y + h * 0.31, sub, ha="center", va="center",
                color=MUT, fontsize=fs_sub, linespacing=1.5, zorder=3)
    else:
        ax.text(cx, y + h / 2, title, ha="center", va="center",
                color=accent, fontsize=fs_title, fontweight="bold", zorder=3)


def arrow(ax, p1, p2, label="", color=MUT, rad=0.0, doubled=False, lw=1.8,
          fs=9.0, ls="-", lab_color=None):
    style = "<|-|>" if doubled else "-|>"
    ax.add_patch(FancyArrowPatch(
        p1, p2, connectionstyle=f"arc3,rad={rad}", arrowstyle=style,
        mutation_scale=16, lw=lw, color=color, linestyle=ls,
        shrinkA=3, shrinkB=3, zorder=1))
    if label:
        mx, my = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2
        my += rad * abs(p2[0] - p1[0]) * 0.5
        ax.text(mx, my, label, ha="center", va="center",
                color=lab_color or color, fontsize=fs, fontweight="medium",
                bbox=dict(boxstyle="round,pad=0.3", fc=BG, ec="none", alpha=0.92),
                zorder=4)


# =========================================================================== #
# 1. Architecture
# =========================================================================== #
def architecture(path="assets/architecture.png"):
    fig, ax = plt.subplots(figsize=(12.6, 8.0), dpi=200)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.axis("off")

    ax.text(50, 96.5, "agentctl", ha="center", color=TXT, fontsize=24, fontweight="bold")
    ax.text(50, 91.3, "one control plane for the agent lifecycle  ·  push → preview → eval-gate → rollout → rollback",
            ha="center", color=MUT, fontsize=11)

    # actors
    box(ax, 5, 80, 23, 8, "Developer", "agentctl push", accent=TXT, fs_title=11, fs_sub=9.5)
    box(ax, 72, 80, 23, 8, "Browser / SDK", "gRPC bidi · WebSocket", accent=TXT, fs_title=11, fs_sub=9.0)

    # planes - control plane and data plane SIDE BY SIDE so nothing crosses them
    box(ax, 4, 56, 24, 15, "CLI · typer + rich",
        "pack · preview\neval · merge / block", accent=CYAN)
    box(ax, 5, 32, 45, 18, "CONTROL PLANE · Python",
        "webhook → deployment\n→ isolated preview\neval-gate  ·  rollback", accent=GREEN)
    box(ax, 56, 32, 40, 18, "GO DATA PLANE",
        "gateway_core\nsticky canary · shadow\ntoken streaming", accent=VIOLET)

    # stores
    box(ax, 3, 8, 19, 14, "DuckDB", "eval traces\n(local)", accent=CYAN, fs_title=12)
    box(ax, 26, 8, 34, 14, "Postgres · SoR",
        "deployments · routing\ncheckpoints · audit", accent=BLUE)
    box(ax, 65, 8, 29, 14, "ClickHouse", "telemetry\n(opt-in)", accent=AMBER, fs_title=12)

    # flows - all short, none crossing a box
    arrow(ax, (16, 80), (16, 71))                                   # dev -> cli
    arrow(ax, (83, 80), (80, 50), label="gRPC bidi", color=VIOLET)  # client -> data plane
    arrow(ax, (22, 56), (27, 50), label="webhook\n(in-process)", color=CYAN, fs=8.5)  # cli -> control
    arrow(ax, (32, 32), (38, 22), doubled=True, color=BLUE, label="ACID")             # control <-> postgres
    arrow(ax, (58, 22), (64, 32), rad=0.05, color=BLUE, ls="--",
          label="LISTEN/NOTIFY\nrouting flip · 0 dropped", lab_color=BLUE, fs=8.5)    # postgres -> data plane
    arrow(ax, (16, 32), (12, 22), color=CYAN, label="eval traces", fs=8.5)            # control -> duckdb
    arrow(ax, (82, 32), (80, 22), color=AMBER, label="OTel spans", fs=8.5)            # data plane -> clickhouse

    # vertical legend
    for i, (c, t) in enumerate([(CYAN, "A · probabilistic eval-gate"),
                                (VIOLET, "B · streaming data plane"),
                                (AMBER, "C · stateful rollback")]):
        lx = 6 + i * 31
        ax.add_patch(FancyBboxPatch((lx, 0.5), 1.6, 1.6, boxstyle="round,pad=0,rounding_size=0.6",
                                    fc=c, ec="none", zorder=3))
        ax.text(lx + 2.6, 1.3, t, ha="left", va="center", color=MUT, fontsize=9)

    fig.savefig(path, facecolor=BG, bbox_inches="tight", pad_inches=0.25)
    plt.close(fig)
    print("wrote", path)


# =========================================================================== #
# 2. Pipeline
# =========================================================================== #
def pipeline(path="assets/pipeline.png"):
    fig, ax = plt.subplots(figsize=(13.0, 3.5), dpi=200)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 26)
    ax.axis("off")

    stages = [
        ("①  pack", "tar + sha256", GREY),
        ("②  preview", "isolated agent", GREY),
        ("eval-gate", "Wilson CI · SPRT", CYAN),
        ("③  rollout", "atomic flip", AMBER),
        ("rollback", "1-click · honest", VIOLET),
    ]
    n = len(stages)
    w, h = 16.0, 9.0
    gap = (100 - n * w) / (n + 1)
    ys = 12.0
    centers = []
    for i, (title, sub, accent) in enumerate(stages):
        x = gap + i * (w + gap)
        box(ax, x, ys, w, h, title, sub, accent=accent, fs_title=12, fs_sub=9.0)
        centers.append((x, x + w))

    for i in range(n - 1):
        arrow(ax, (centers[i][1], ys + h / 2), (centers[i + 1][0], ys + h / 2),
              color=MUT, lw=2.0)

    # gate outcomes
    gx0, gx1 = centers[2]
    gcx = (gx0 + gx1) / 2
    ax.text(gcx, ys + h + 4.2, "ALLOW  ✓  non-inferior", ha="center", color=GREEN,
            fontsize=9.5, fontweight="bold")
    arrow(ax, (gcx, ys + h), (gcx, ys + h + 3.0), color=GREEN, lw=1.6)
    ax.text(gcx, ys - 4.0, "BLOCK  ✗  regression → PR protected", ha="center", color=RED,
            fontsize=9.5, fontweight="bold")
    arrow(ax, (gcx, ys), (gcx, ys - 3.0), color=RED, lw=1.6)

    fig.savefig(path, facecolor=BG, bbox_inches="tight", pad_inches=0.25)
    plt.close(fig)
    print("wrote", path)


if __name__ == "__main__":
    architecture()
    pipeline()
