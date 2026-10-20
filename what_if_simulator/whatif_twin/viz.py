# Charts for the what-if simulator (matplotlib, static PNGs for the notebook, slides and the app's report screens).
# Colour jobs: model identity (watch = blue, ICU model = violet), baseline vs scenario (grey vs orange), SHAP polarity
# (raises = red, lowers = blue, diverging around zero) and risk tier (status colours, always with a text label).
from __future__ import annotations

import math
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

INK, INK2, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#8a8a87", "#e4e3df", "#fcfcfb"
BLUE, ORANGE, VIOLET, AQUA, YELLOW, MAGENTA, GREEN = "#2a78d6", "#eb6834", "#6250d6", "#1baf7a", "#eda100", "#e87ba4", "#008300"
CATEGORICAL = [BLUE, ORANGE, AQUA, YELLOW, MAGENTA, GREEN, VIOLET]
MODEL_COLOR = {"watch": BLUE, "full": VIOLET}
MODEL_NAME = {"watch": "Phone + watch model", "full": "ICU reference model"}
BASE_C, SCEN_C = INK2, ORANGE
RAISE, LOWER = "#d03b3b", BLUE
TIER_C = {"LOW": "#0ca30c", "MODERATE": "#fab219", "HIGH": "#d03b3b"}


def style() -> None:
    plt.rcParams.update({"figure.dpi": 110, "savefig.dpi": 140, "figure.facecolor": SURFACE, "axes.facecolor": SURFACE,
                         "axes.edgecolor": GRID, "axes.labelcolor": INK2, "axes.titlecolor": INK, "axes.titleweight": "bold",
                         "axes.titlesize": 11, "axes.titlelocation": "left", "axes.spines.top": False,
                         "axes.spines.right": False, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.7,
                         "xtick.color": INK2, "ytick.color": INK2, "font.size": 9.5, "legend.frameon": False,
                         "lines.linewidth": 2})


def save(fig, path: str | Path | None):
    if path is not None:
        fig.savefig(path, bbox_inches="tight", facecolor=SURFACE)
    return fig


def pct(x) -> str:
    return f"{100 * x:.1f}%"


def _tier_bands(ax, thresholds: dict, xmax: float, orient: str = "x", labels: bool = True) -> None:
    edges = [0, thresholds["moderate"], thresholds["high"], xmax]
    for (a, b), t in zip(zip(edges[:-1], edges[1:]), ("LOW", "MODERATE", "HIGH")):
        (ax.axvspan if orient == "x" else ax.axhspan)(a, b, color=TIER_C[t], alpha=0.07, lw=0, zorder=0)
        if labels:
            if orient == "x":
                ax.text((a + b) / 2, 1.0, t, transform=ax.get_xaxis_transform(), ha="center", va="bottom", fontsize=7.5, color=INK2)
            else:
                ax.text(1.0, (a + b) / 2, " " + t, transform=ax.get_yaxis_transform(), ha="left", va="center", fontsize=7.5, color=INK2)
    for t in ("moderate", "high"):
        (ax.axvline if orient == "x" else ax.axhline)(thresholds[t], color=MUTED, lw=0.8, ls=(0, (3, 3)), zorder=1)


def _pct_axis(ax, axis="x"):
    fmt = matplotlib.ticker.FuncFormatter(lambda v, _: f"{100 * v:.0f}%" if v >= 0.1 or v == 0 else f"{100 * v:.1f}%")
    (ax.xaxis if axis == "x" else ax.yaxis).set_major_formatter(fmt)


# ---------------------------------------------------------------- baseline
def plot_baseline(sim, case, baseline: dict, path=None, top: int = 10):
    style()
    T = sim.art.T
    fig = plt.figure(figsize=(12, 4.6))
    gs = fig.add_gridspec(1, 2, width_ratios=[1, 1.35], wspace=0.35)
    ax = fig.add_subplot(gs[0])
    ms = list(baseline)
    xmax = max(0.2, max(b.get("p95", b["prob"]) for b in baseline.values()) * 1.15)
    for i, m in enumerate(ms):
        b, tm = baseline[m], sim.models[m]
        y = len(ms) - 1 - i
        for t, c in (("moderate", TIER_C["MODERATE"]), ("high", TIER_C["HIGH"])):
            ax.plot([tm.thresholds[t]] * 2, [y - 0.32, y + 0.32], color=c, lw=2)
        if "p05" in b:
            ax.plot([b["p05"], b["p95"]], [y, y], color=MODEL_COLOR[m], lw=2, alpha=0.35, solid_capstyle="round")
        ax.scatter(b["prob"], y, s=110, color=MODEL_COLOR[m], zorder=3, edgecolor=SURFACE, linewidth=2)
        ax.annotate(f"{pct(b['prob'])} · {b['tier']}", (b["prob"], y), xytext=(8, 9), textcoords="offset points",
                    fontsize=9, color=INK)
    ax.set_yticks(range(len(ms)), [MODEL_NAME[m] for m in reversed(ms)])
    ax.set_xlim(0, xmax)
    ax.set_ylim(-0.7, len(ms) - 0.4)
    _pct_axis(ax)
    ax.set_xlabel("risk of deterioration within 24 h")
    ax.set_title("Baseline model output")
    ax.text(0, -0.2, "Coloured ticks: each model's MODERATE / HIGH cut-off. Faint bar: 5-95% of the 50 bootstrap refits.",
            transform=ax.transAxes, fontsize=7.5, color=MUTED)

    ax = fig.add_subplot(gs[1])
    m = "watch" if "watch" in baseline else ms[0]
    phi = baseline[m]["shap"]
    s = phi.reindex(phi.abs().sort_values(ascending=False).index)[:top][::-1]
    labels = []
    for f in s.index:
        v = case.values.get(f)
        lab = T.TXT.get(f, (f,))[0]
        val = "missing (filled)" if pd.isna(v) else f"{v:.4g}"
        labels.append(f"{lab[:38]} = {val}")
    ax.barh(range(len(s)), s.values, color=[RAISE if x > 0 else LOWER for x in s.values], height=0.62)
    ax.set_yticks(range(len(s)), labels, fontsize=8)
    ax.axvline(0, color=INK2, lw=0.8)
    ax.set_xlabel("SHAP value (log-odds); right raises the risk, left lowers it")
    ax.set_title(f"Why: top {top} inputs of the {MODEL_NAME[m].lower()}")
    ax.grid(axis="y", visible=False)
    fig.suptitle(f"{case.label}  ·  {case.source}", x=0.01, ha="left", fontsize=9, color=MUTED, y=1.02)
    return save(fig, path)


# ---------------------------------------------------------------- one scenario
def _bridge(o, T, cal, k: int = 6):
    """Groups' SHAP changes applied in order of size; each cumulative raw log-odds is passed through the model's own
    calibrator, so the first step starts at the baseline and the last ends exactly at the scenario output."""
    g = o.group_delta(T)
    g = g[g.delta.abs() > 1e-9]
    to_p = lambda lo: float(cal(np.array([1 / (1 + math.exp(-lo))]))[0])  # noqa: E731
    run = math.log(o.baseline["raw"] / (1 - o.baseline["raw"]))
    head, rest = g.iloc[:k], g.iloc[k:]
    items = list(head.delta.items()) + ([("Other inputs", rest.delta.sum())] if len(rest) else [])
    steps = []
    for name, d in items:
        steps.append((name, to_p(run), to_p(run + d)))
        run += d
    return steps


def plot_result(sim, res, path=None, model: str | None = None):
    style()
    T = sim.art.T
    outs = res.outcomes
    m = model or ("watch" if "watch" in outs and outs["watch"].affected else next(iter(outs)))
    o = outs[m]
    fig = plt.figure(figsize=(13, 4.8))
    gs = fig.add_gridspec(1, 3, width_ratios=[1, 1.1, 1.15], wspace=0.55)

    ax = fig.add_subplot(gs[0])
    ms = list(outs)
    xmax = max(0.15, 1.15 * max(max(x.baseline["prob"], x.scenario["prob"], (x.uncertainty or {}).get("scenario_p95", 0)) for x in outs.values()))
    for i, mm in enumerate(ms):
        x = outs[mm]
        y = len(ms) - 1 - i
        tm = sim.models[mm]
        for t, c in (("moderate", TIER_C["MODERATE"]), ("high", TIER_C["HIGH"])):
            ax.plot([tm.thresholds[t]] * 2, [y - 0.3, y + 0.3], color=c, lw=2)
        u = x.uncertainty or {}
        if u.get("available"):
            ax.plot([u["scenario_p05"], u["scenario_p95"]], [y - 0.1] * 2, color=SCEN_C, alpha=0.3, lw=2, solid_capstyle="round")
            ax.plot([u["baseline_p05"], u["baseline_p95"]], [y + 0.1] * 2, color=BASE_C, alpha=0.25, lw=2, solid_capstyle="round")
        ax.annotate("", xy=(x.scenario["prob"], y), xytext=(x.baseline["prob"], y),
                    arrowprops=dict(arrowstyle="-|>", color=INK2, lw=1.2, shrinkA=6, shrinkB=6))
        ax.scatter([x.baseline["prob"]], [y], s=90, color=BASE_C, zorder=3, edgecolor=SURFACE, linewidth=2)
        ax.scatter([x.scenario["prob"]], [y], s=90, color=SCEN_C, zorder=3, edgecolor=SURFACE, linewidth=2)
        txt = f"{pct(x.baseline['prob'])} → {pct(x.scenario['prob'])}  ({100 * x.delta:+.1f} pts, {x.tier_change})" if x.affected \
            else "not affected (no input changes)"
        ax.text(0.0, y + 0.36, f"{MODEL_NAME[mm]}: {txt}", fontsize=8, color=INK, transform=ax.get_yaxis_transform())
    ax.set_yticks([])
    ax.set_xlim(0, xmax)
    ax.set_ylim(-0.6, len(ms) - 0.2)
    _pct_axis(ax)
    ax.set_xlabel("risk within 24 h")
    ax.scatter([], [], color=BASE_C, label="baseline")
    ax.scatter([], [], color=SCEN_C, label="scenario")
    ax.legend(loc="lower right", fontsize=8)
    ax.set_title("Baseline vs scenario")

    ax = fig.add_subplot(gs[1])
    g = o.group_delta(T)
    g = g[g.delta.abs() > 1e-6].iloc[:8][::-1] if g is not None else pd.DataFrame()
    if len(g):
        ax.barh(range(len(g)), g.delta.values, color=[RAISE if v > 0 else LOWER for v in g.delta.values], height=0.6)
        ax.set_yticks(range(len(g)), [n[:30] for n in g.index], fontsize=8.5)
        for i, v in enumerate(g.delta.values):
            ax.text(v, i, f" {v:+.3f} ", va="center", ha="left" if v > 0 else "right", fontsize=7.5, color=INK2)
        lim = max(abs(g.delta).max() * 1.6, 0.02)
        ax.set_xlim(-lim, lim)
        ax.set_ylim(-0.6, max(len(g), 5) - 0.4)
    ax.axvline(0, color=INK2, lw=0.8)
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("change in SHAP (log-odds)")
    ax.set_title(f"What moved the {MODEL_NAME[m].lower()}")

    ax = fig.add_subplot(gs[2])
    steps = _bridge(o, T, sim.models[m].calibrator.transform) if o.affected else []
    for i, (name, p0, p1) in enumerate(steps):
        ax.plot([p0, p1], [i, i], color=RAISE if p1 > p0 else LOWER, lw=9, solid_capstyle="butt")
        ax.text(max(p0, p1), i, f"  {100 * (p1 - p0):+.2f} pts", va="center", fontsize=7.5, color=INK2)
        if i:
            ax.plot([p0, p0], [i - 1, i], color=MUTED, lw=0.7, ls=":")
    ax.set_yticks(range(len(steps)), [s[0][:28] for s in steps], fontsize=8.5)
    ax.set_ylim(max(len(steps), 5) - 0.4, -0.6)
    _pct_axis(ax)
    if steps:
        lo = min(min(s[1], s[2]) for s in steps)
        hi = max(max(s[1], s[2]) for s in steps)
        pad = (hi - lo) * 0.45 + 1e-3
        ax.set_xlim(lo - pad * 0.3, hi + pad)
        ax.axvline(steps[0][1], color=BASE_C, lw=1, ls="--")
        ax.axvline(steps[-1][2], color=SCEN_C, lw=1, ls="--")
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("risk (each step in order, largest first)")
    ax.set_title("Bridge from baseline to scenario")
    fig.suptitle(f"{res.scenario.name}: {'; '.join(res.applied.changes_text)}   ·   {case_tag(res.case)}",
                 x=0.01, ha="left", fontsize=9.5, color=INK2, y=1.03)
    fig.text(0.01, -0.06, "HYPOTHETICAL MODEL SIMULATION, not a predicted treatment effect and not medical advice.",
             fontsize=7.5, color=MUTED)
    return save(fig, path)


def case_tag(case) -> str:
    return ("SYNTHETIC DEMO PATIENT" if case.is_synthetic else case.label)


# ---------------------------------------------------------------- many scenarios
def plot_scenarios(sim, table: pd.DataFrame, case, path=None):
    style()
    t = table[table.model.isin(["watch", "full"])]
    ms = [m for m in ("watch", "full") if m in set(t.model)]
    names = list(dict.fromkeys(t.scenario))
    fig, axes = plt.subplots(1, len(ms), figsize=(6.2 * len(ms), 0.55 * len(names) + 1.8), sharey=True, squeeze=False)
    for ax, m in zip(axes[0], ms):
        d = t[t.model == m].set_index("scenario").reindex(names)
        hi = d[["baseline_prob", "scenario_prob"]].to_numpy(float)
        if "delta_p95" in d:
            hi = np.concatenate([hi.ravel(), (d.baseline_prob + d.delta_p95.astype(float)).to_numpy()])
        xmax = max(0.12, 1.3 * np.nanmax(hi))
        _tier_bands(ax, sim.models[m].thresholds, xmax)
        for i, (n, r) in enumerate(d.iterrows()):
            if pd.isna(r.baseline_prob):
                ax.text(0.002, i, "rejected", va="center", fontsize=8, color=MUTED)
                continue
            if pd.notna(r.get("delta_p05")):
                ax.plot([r.baseline_prob + r.delta_p05, r.baseline_prob + r.delta_p95], [i, i], color=SCEN_C, alpha=0.25, lw=7,
                        solid_capstyle="round")
            ax.plot([r.baseline_prob, r.scenario_prob], [i, i], color=MUTED, lw=1.2)
            ax.scatter(r.baseline_prob, i, color=BASE_C, s=40, zorder=3)
            ax.scatter(r.scenario_prob, i, color=SCEN_C, s=70, zorder=3, edgecolor=SURFACE, linewidth=1.5)
            lab = f"{100 * r.delta:+.1f} pts" + ("" if r.tier_change == "unchanged" else f" · {r.tier_change}")
            right = max(r.scenario_prob, r.baseline_prob, r.baseline_prob + (r.delta_p95 if pd.notna(r.get("delta_p95")) else 0))
            ax.text(right + xmax * 0.015, i, lab, va="center", fontsize=7.8, color=INK2)
        ax.set_yticks(range(len(names)), names)
        ax.set_xlim(0, xmax)
        ax.invert_yaxis()
        _pct_axis(ax)
        ax.set_title(MODEL_NAME[m], pad=14)
        ax.grid(axis="y", visible=False)
        ax.set_xlabel("risk within 24 h  (grey = baseline, orange = scenario, band = 5-95% of refits)")
    fig.suptitle(f"Scenario comparison · {case_tag(case)}", x=0.01, ha="left", fontsize=10, color=INK2, y=1.02)
    return save(fig, path)


# ---------------------------------------------------------------- sensitivity
def plot_sensitivity(sim, curves: list[pd.DataFrame], case, path=None, ncols: int = 3):
    style()
    n = len(curves)
    nrows = math.ceil(n / ncols)
    fig, axes = plt.subplots(nrows, ncols, figsize=(4.4 * ncols, 3.1 * nrows), squeeze=False)
    for ax, df in zip(axes.flat, curves):
        lv = sim.levers[df.attrs["lever"]]
        ymax = 0
        for m in ("watch", "full"):
            if f"prob_{m}" not in df:
                continue
            if f"p05_{m}" in df:
                ax.fill_between(df.value, df[f"p05_{m}"], df[f"p95_{m}"], color=MODEL_COLOR[m], alpha=0.07, lw=0)
            style_kw = dict(drawstyle="steps-mid") if lv.discrete else {}
            ax.plot(df.value, df[f"prob_{m}"], color=MODEL_COLOR[m], label=MODEL_NAME[m], marker="o" if lv.discrete else None, **style_kw)
            ymax = max(ymax, df[f"prob_{m}"].max())
        th = sim.models["watch"].thresholds
        for t, c in (("moderate", TIER_C["MODERATE"]), ("high", TIER_C["HIGH"])):
            ax.axhline(th[t], color=c, lw=1, ls=(0, (3, 3)))
        out = ~df.in_training_range
        if out.any():
            for x in df.value[out]:
                ax.axvspan(x - 1e-9, x + 1e-9, color=MUTED, alpha=0.0)
            lo_out, hi_out = df.value[~out].min(), df.value[~out].max()
            ax.axvspan(df.value.min(), lo_out, color=GRID, alpha=0.6, lw=0)
            ax.axvspan(hi_out, df.value.max(), color=GRID, alpha=0.6, lw=0)
        cur = df.attrs.get("current")
        if cur is not None:
            ax.axvline(cur, color=INK, lw=1)
            ax.text(cur, 0.02, " now", transform=ax.get_xaxis_transform(), fontsize=7.5, color=INK, va="bottom")
        if lv.discrete == (0.0, 1.0):
            ax.set_xticks([0, 1], ["no", "yes"])
        ax.set_ylim(0, max(ymax * 1.25, th["high"] * 1.3))
        _pct_axis(ax, "y")
        ax.set_title(f"{lv.label} ({lv.unit})", fontsize=10)
    for ax in list(axes.flat)[n:]:
        ax.axis("off")
    h, l = axes.flat[0].get_legend_handles_labels()
    fig.legend(h, l, loc="upper right", ncol=2, fontsize=8.5, bbox_to_anchor=(1, 1.02))
    fig.suptitle(f"One-way sensitivity (this patient, everything else held) · {case_tag(case)}\n"
                 "dashed: watch-model MODERATE / HIGH cut-offs · shaded band: 5-95% of refits · grey: outside the training range",
                 x=0.01, ha="left", fontsize=9, color=INK2, y=1.04)
    fig.tight_layout()
    return save(fig, path)


def plot_tornado(df: pd.DataFrame, case, model: str, base: float, path=None, top: int = 12):
    style()
    d = df.head(top).iloc[::-1]
    fig, ax = plt.subplots(figsize=(8.5, 0.42 * len(d) + 1.4))
    for i, r in enumerate(d.itertuples()):
        for col, sign in (("delta_down", "−"), ("delta_up", "+"), ("delta_toggle", "⇄")):
            v = getattr(r, col, np.nan)
            if pd.isna(v):
                continue
            ax.barh(i, v, color=RAISE if v > 0 else LOWER, height=0.55, alpha=0.9 if col != "delta_down" else 0.55)
            step = "toggle" if col == "delta_toggle" else f"{sign}{r.step:g}"
            ax.text(v, i, f" {step} " if v >= 0 else f" {step} ", ha="left" if v >= 0 else "right", va="center",
                    fontsize=7.5, color=INK2)
    ax.set_yticks(range(len(d)), [f"{r.label} (now {r.current:.4g} {r.unit})" if r.unit not in ("0/1",) else
                                  f"{r.label} (now {'yes' if r.current else 'no'})" for r in d.itertuples()], fontsize=8.5)
    ax.axvline(0, color=INK2, lw=0.8)
    xmax = d.filter(like="delta_").abs().max().max() * 1.35
    ax.set_xlim(-xmax, xmax)
    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{100 * v:+.1f}"))
    ax.set_xlabel(f"change in risk (percentage points) from {pct(base)}; red raises, blue lowers; pale bar = step down")
    ax.grid(axis="y", visible=False)
    ax.set_title(f"Which lever moves the {MODEL_NAME[model].lower()} most · {case_tag(case)}")
    return save(fig, path)


def plot_surface(sim, s: dict, case, path=None):
    style()
    lx, ly = sim.levers[s["lever_x"]], sim.levers[s["lever_y"]]
    fig, ax = plt.subplots(figsize=(7.2, 5.2))
    cmap = matplotlib.colors.LinearSegmentedColormap.from_list("seq", ["#eef4fc", "#a9c9f0", "#4f92e0", "#1b5aa6", "#0b2f5e"])
    pcm = ax.pcolormesh(s["x"], s["y"], s["z"], cmap=cmap, shading="nearest")
    cs = ax.contour(s["x"], s["y"], s["z"], levels=[s["thresholds"]["moderate"], s["thresholds"]["high"]],
                    colors=[INK, INK], linewidths=[1.2, 1.8], linestyles=["--", "-"])
    ax.clabel(cs, fmt={s["thresholds"]["moderate"]: "MODERATE", s["thresholds"]["high"]: "HIGH"}, fontsize=8)
    cx, cy = s["current"]
    ax.scatter([cx], [cy], s=120, color=ORANGE, edgecolor=SURFACE, linewidth=2, zorder=4)
    ax.annotate("this patient now", (cx, cy), xytext=(-10, 10), textcoords="offset points", fontsize=8.5, color=INK, ha="right")
    cb = fig.colorbar(pcm, ax=ax, format=matplotlib.ticker.FuncFormatter(lambda v, _: f"{100 * v:.0f}%"))
    cb.set_label("risk within 24 h")
    ax.set_xlabel(f"{lx.label} ({lx.unit})")
    ax.set_ylabel(f"{ly.label} ({ly.unit})")
    ax.grid(False)
    ax.set_title(f"Two-lever response surface · {MODEL_NAME[s['model']]}")
    fig.text(0.01, -0.02, f"{case_tag(case)}; all other inputs held at this patient's values. Lines: tier cut-offs.",
             fontsize=7.5, color=MUTED)
    return save(fig, path)


def plot_counterfactuals(cf: pd.DataFrame, case, path=None):
    style()
    d = cf.iloc[::-1]
    fig, ax = plt.subplots(figsize=(9.5, 0.5 * len(d) + 1.6))
    base, limit = cf.attrs["baseline_prob"], cf.attrs["limit"]
    for i, r in enumerate(d.itertuples()):
        ax.plot([base, r.prob], [i, i], color=MUTED, lw=1)
        ax.scatter(r.prob, i, s=70, color=AQUA if r.reaches_target else ORANGE, zorder=3, edgecolor=SURFACE, linewidth=1.5)
        ax.text(r.prob, i + 0.3, f"{pct(r.prob)} ({r.tier}) · cost {r.cost_steps:g} steps", fontsize=7.5, color=INK2, ha="center")
    ax.axvline(base, color=BASE_C, lw=1.2)
    ax.text(base, 1.0, " baseline", transform=ax.get_xaxis_transform(), fontsize=8, color=INK2, va="bottom")
    ax.axvline(limit, color=TIER_C["MODERATE" if cf.attrs["target_tier"] == "LOW" else "HIGH"], lw=1.2, ls="--")
    ax.text(limit, 1.0, f" {cf.attrs['target_tier']} below here", transform=ax.get_xaxis_transform(), fontsize=8, color=INK2,
            va="bottom", ha="right")
    ax.set_yticks(range(len(d)), [c[:70] for c in d.changes], fontsize=8)
    _pct_axis(ax)
    lo = min(d.prob.min(), limit)
    ax.set_xlim(lo * 0.85, base * 1.08)
    ax.set_ylim(-0.6, len(d) - 0.2)
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("risk within 24 h after the change (aqua = reaches the target tier)")
    ax.set_title(f"Smallest model counterfactuals · {MODEL_NAME[cf.attrs['model']]} · {case_tag(case)}", pad=16)
    fig.text(0.01, -0.03, "Levers only move toward their usual target band (e.g. HR toward 60-100, quitting smoking), within the "
             "training range. Model counterfactuals, not treatment recommendations.", fontsize=7.5, color=MUTED)
    return save(fig, path)


# ---------------------------------------------------------------- reading-level twin
def plot_trajectory(out: dict, sim, signal: str = "hr", path=None, title: str = ""):
    style()
    tb, ts = out["trajectory_baseline"], out["trajectory_scenario"]
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(11, 6), sharex=True, gridspec_kw={"height_ratios": [1.4, 1]})
    th = sim.models["watch"].thresholds
    ymax = max(tb.prob.max(), ts.prob.max(), th["high"]) * 1.3
    _tier_bands(a1, th, ymax, orient="y")
    a1.plot(tb.hour, tb.prob, color=BASE_C, label="baseline (as measured)")
    a1.plot(ts.hour, ts.prob, color=SCEN_C, label="hypothetical")
    a1.fill_between(ts.hour, tb.prob, ts.prob, color=SCEN_C, alpha=0.12, lw=0)
    a1.set_ylim(0, ymax)
    _pct_axis(a1, "y")
    a1.set_ylabel("risk within 24 h")
    a1.legend(loc="upper left", fontsize=8.5)
    a1.set_title(title or "Digital twin timeline: hour-by-hour risk, measured vs hypothetical readings")
    unit = {"hr": "bpm", "spo2": "%", "rr": "/min"}[signal]
    a2.plot(tb.hour, tb[signal], color=BASE_C)
    a2.plot(ts.hour, ts[signal], color=SCEN_C)
    a2.set_ylabel(f"{signal.upper()} hourly mean ({unit})")
    a2.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%H:%M"))
    fig.text(0.01, -0.01, "SYNTHETIC DEMONSTRATION PATIENT. Every input rebuilt from the edited readings with the engine's "
             "training feature code. Hypothetical model simulation, not medical advice.", fontsize=7.5, color=MUTED)
    fig.tight_layout()
    return save(fig, path)


def plot_consistency(df: pd.DataFrame, path=None):
    style()
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4.2))
    for i, (sig, g) in enumerate(df.groupby("signal", sort=False)):
        c = CATEGORICAL[i]
        a1.scatter(g.reading_prob, g.feature_prob, s=36, color=c, label=sig, edgecolor=SURFACE, linewidth=1)
        a2.plot(g.change, 100 * (g.feature_prob - g.reading_prob), marker="o", color=c, label=sig)
    lim = [0, max(df.reading_prob.max(), df.feature_prob.max()) * 1.1]
    a1.plot(lim, lim, color=MUTED, lw=1, ls="--")
    a1.set_xlim(lim)
    a1.set_ylim(lim)
    _pct_axis(a1)
    _pct_axis(a1, "y")
    a1.set_xlabel("reading-level twin (every input rebuilt from edited readings)")
    a1.set_ylabel("feature-level simulator")
    a1.set_title("Feature-level edits vs full rebuild")
    a1.legend(fontsize=8.5)
    a2.axhline(0, color=INK2, lw=0.8)
    a2.set_xlabel("edit applied to the last 6 h (signal units)")
    a2.set_ylabel("difference (percentage points)")
    a2.set_title("Approximation error of the feature-level update")
    return save(fig, path)


def plot_population(df: pd.DataFrame, path=None):
    style()
    d = df[df.status != "REJECTED"].copy()
    names = list(dict.fromkeys(d.scenario))
    fig, ax = plt.subplots(figsize=(9, 0.6 * len(names) + 1.6))
    rng = np.random.default_rng(0)
    for i, n in enumerate(names):
        g = d[d.scenario == n]
        y = i + rng.uniform(-0.18, 0.18, len(g))
        moved = g.baseline_tier != g.scenario_tier
        ax.scatter(100 * g.delta[~moved], y[~moved], s=28, color=BLUE, alpha=0.75, edgecolor=SURFACE, linewidth=0.8)
        ax.scatter(100 * g.delta[moved], y[moved], s=44, color=ORANGE, edgecolor=SURFACE, linewidth=0.8, marker="D")
        ax.plot([100 * g.delta.median()] * 2, [i - 0.3, i + 0.3], color=INK, lw=2)
        ax.text(100 * g.delta.max(), i, f"   median {100 * g.delta.median():+.2f} · tier changed {int(moved.sum())}/{len(g)}",
                va="center", fontsize=7.8, color=INK2)
    ax.axvline(0, color=INK2, lw=0.8)
    ax.set_yticks(range(len(names)), names)
    ax.invert_yaxis()
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("change in risk (percentage points), each dot one test patient's own baseline")
    ax.scatter([], [], color=BLUE, label="tier unchanged")
    ax.scatter([], [], color=ORANGE, marker="D", label="tier changed")
    ax.legend(loc="lower right", fontsize=8)
    ax.set_title("Same scenario, every test patient (phone + watch model)")
    xr = ax.get_xlim()
    ax.set_xlim(xr[0], xr[1] + (xr[1] - xr[0]) * 0.55)
    return save(fig, path)
