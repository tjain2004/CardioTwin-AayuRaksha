# Interactive what-if panel for Jupyter (ipywidgets). Pick a patient, move sliders (one or several levers), choose
# recent / persistent for vital signs, run, and add the result to a comparison table. Falls back to a message when
# ipywidgets or a widget front end is not available (the notebook's static sections show the same results).
from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .levers import plausible_range
from .scenario import Change, Scenario
from . import viz


def build_panel(sim, default_case: str | None = None, levers: list[str] | None = None):
    try:
        import ipywidgets as W
        from IPython.display import display
    except ImportError:
        print("ipywidgets is not installed: `pip install ipywidgets` for the interactive panel. "
              "Use sim.simulate(case, Scenario.of(...)) instead.")
        return None

    ids = sim.patients.ids()
    pick = W.Dropdown(options=[(sim.patients.get(i).label + (" [SYNTHETIC]" if sim.patients.get(i).is_synthetic else ""), i)
                               for i in ids], value=default_case or ids[0], description="Patient", layout=W.Layout(width="520px"))
    mode = W.ToggleButtons(options=[("last 6 h", "recent"), ("whole history", "persistent")], value="recent",
                           description="Vitals edit")
    name = W.Text(value="My scenario", description="Name")
    run, add, reset = W.Button(description="Run", button_style="primary"), W.Button(description="Add to comparison"), W.Button(description="Reset")
    box_sliders = W.VBox()
    out_main, out_cmp = W.Output(), W.Output()
    state = {"sliders": {}, "rows": []}

    def make_sliders(*_):
        case = sim.patients.get(pick.value)
        keys = [k for k in (levers or sim.available_levers(case)) if k in sim.available_levers(case)]
        sl = {}
        for k in keys:
            lv = sim.levers[k]
            cur = lv.current(case.values)
            if lv.discrete:
                w = W.ToggleButtons(options=[("no", 0.0), ("yes", 1.0)], value=float(cur), description=lv.label,
                                    style={"description_width": "190px"})
            else:
                lo, hi = plausible_range(lv, sim.art.reference) or lv.hard_range
                lo, hi = min(lo, cur), max(hi, cur)
                step = 10 ** np.floor(np.log10(max((hi - lo) / 100, 1e-3)))
                w = W.FloatSlider(value=round(cur, 2), min=round(lo, 2), max=round(hi, 2), step=step,
                                  description=f"{lv.label} ({lv.unit})", continuous_update=False,
                                  style={"description_width": "190px"}, layout=W.Layout(width="560px"))
            w._current = cur
            sl[k] = w
        state["sliders"] = sl
        box_sliders.children = list(sl.values())

    def scenario():
        ch = [Change(k, value=float(w.value), mode=mode.value) for k, w in state["sliders"].items()
              if abs(float(w.value) - float(w._current)) > 1e-9]
        return Scenario(name.value or "scenario", ch)

    def on_run(*_):
        case = sim.patients.get(pick.value)
        sc = scenario()
        out_main.clear_output()
        with out_main:
            if not sc.changes:
                print("Move at least one slider.")
                return
            res = sim.simulate(case, sc)
            print(res.label)
            print(res.headline())
            if res.status == "REJECTED":
                return
            for m, o in res.outcomes.items():
                for w_ in o.direction_warnings + [f"{d['feature']} = {d['value']}: {d['warning']}" for d in o.out_of_distribution]:
                    print(f"  ⚠ [{m}] {w_}")
            if res.applied.held:
                print("  held constant:", ", ".join(res.applied.held))
            fig = viz.plot_result(sim, res)
            plt.show(fig)
            state["last"] = res

    def on_add(*_):
        if "last" not in state:
            return
        state["rows"].append(state["last"].table())
        out_cmp.clear_output()
        with out_cmp:
            t = pd.concat(state["rows"], ignore_index=True)
            display(t[["scenario", "model", "baseline_prob", "scenario_prob", "delta", "tier_change", "robustness"]]
                    .style.format({"baseline_prob": "{:.1%}", "scenario_prob": "{:.1%}", "delta": "{:+.2%}"}))

    pick.observe(make_sliders, names="value")
    reset.on_click(make_sliders)
    run.on_click(on_run)
    add.on_click(on_add)
    make_sliders()
    panel = W.VBox([W.HTML("<b>CardioTwin what-if simulator</b> · hypothetical model simulation, not medical advice"),
                    pick, mode, box_sliders, W.HBox([name, run, add, reset]), out_main, W.HTML("<b>Comparison</b>"), out_cmp])
    display(panel)
    return panel
