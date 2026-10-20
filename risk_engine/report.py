# Downloadable, timestamped risk report (Markdown and HTML) built only from a RiskResult.
from __future__ import annotations

import html
from pathlib import Path

from .schemas import RiskResult, Status


def to_markdown(r: RiskResult) -> str:
    ok = r.status == Status.OK
    L = [f"# CardioTwin risk report", "",
         f"**Generated:** {r.evaluated_at.isoformat()} UTC · **Patient reference:** {r.patient_reference} · "
         f"**Audit id:** {r.audit.audit_id}", ""]
    if r.is_demo_data:
        L += ["> DEMO DATA: replayed MIMIC-IV ICU readings, not real watch data.", ""]
    if r.emergency.state.value == "TRIGGERED":
        L += [f"> ⚠ **EMERGENCY RULE MET ({r.emergency.rule_id})**: {r.emergency.reason}", ""]
    L += [f"**Summary:** {r.sar.one_line}", "",
          "| | |", "|---|---|",
          f"| Status | {r.status.value}{' — ' + (r.status_reason or '') if not ok else ''} |",
          f"| Score | {((r.risk.calibrated_probability or r.risk.raw_output) * 100):.1f}% ({r.risk.score_scale}) |" if ok else "| Score | none |",
          f"| Tier | {r.risk.tier.value if ok and r.risk.tier else '—'} (provisional policy {r.risk.policy_version}) |",
          f"| Target / horizon | {r.model.target} / {r.model.horizon} |",
          f"| Model | {r.model.name} {r.model.version} ({r.model.calibration_status}) |",
          f"| Emergency state | {r.emergency.state.value} |",
          f"| Data quality | {r.data_quality.status}, coverage {r.data_quality.coverage_pct}% |", ""]
    for part in ("situation", "assessment", "recommendation"):
        L += [f"## {part.title()}", ""]
        for s in r.sar.sentences.get(part, []):
            ev = "; ".join(_ev(e) for e in s.evidence)
            L += [f"- {s.text}" + (f"  \n  <sub>Evidence: {ev}</sub>" if ev else "")]
        L += [""]
    L += ["## Limitations", ""] + [f"- {x}" for x in r.sar.limitations] + [""]
    if r.explanation.top_features:
        L += ["## Technical details", "", f"Explanation: {r.explanation.method}; output space: {r.explanation.output_space}; "
              f"base {r.explanation.baseline_output:+.3f} → output {r.explanation.explained_output:+.3f} log-odds.", "",
              "| Input | Value | Unit | SHAP (log-odds) |", "|---|---|---|---|"]
        L += [f"| {t['label']} | {t['value']} | {t['unit'] or ''} | {t['shap_log_odds']:+.3f} |" for t in r.explanation.top_features]
        L += [""]
    L += [f"*{r.disclaimer}*", ""]
    return "\n".join(L)


def _ev(e: dict) -> str:
    keys = [k for k in ("type", "signal", "feature", "timestamp", "time_window", "rule_id", "model_version", "policy_version") if e.get(k)]
    return ", ".join(f"{k}={e[k]}" for k in keys)


def to_html(r: RiskResult) -> str:
    md = to_markdown(r)
    try:
        import subprocess
        out = subprocess.run(["pandoc", "-f", "gfm", "-t", "html5", "--standalone", "--metadata", "title=CardioTwin risk report"],
                             input=md, capture_output=True, text=True, timeout=30)
        if out.returncode == 0:
            return out.stdout.replace("</head>", "<style>body{font-family:system-ui,sans-serif;max-width:900px;margin:auto;padding:16px}"
                                                  "table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:4px 8px}"
                                                  "blockquote{border-left:6px solid #B00020;padding:4px 12px;background:#fdecee}</style></head>")
    except Exception:
        pass
    return f"<!doctype html><html><head><meta charset='utf-8'><title>CardioTwin risk report</title></head><body><pre>{html.escape(md)}</pre></body></html>"


def save(r: RiskResult, folder: str | Path, name: str) -> dict[str, str]:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.md").write_text(to_markdown(r), encoding="utf-8")
    (folder / f"{name}.html").write_text(to_html(r), encoding="utf-8")
    return {"md": str(folder / f"{name}.md"), "html": str(folder / f"{name}.html")}
