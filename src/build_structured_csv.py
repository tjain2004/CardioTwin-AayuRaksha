"""Build structured, linked CSVs for the digital twin from the Happiest_Health folder.

Needs only pandas and numpy (no wfdb, no duckdb). Reads the MIMIC zips directly,
so nothing has to be extracted first.

Usage (Windows, PowerShell):
  python build_structured_csv.py C:\\Users\\tjain\\Downloads\\Happiest_Health

Usage (Jupyter): %run build_structured_csv.py    (uses the Happiest_Health folder by default)
  or: import build_structured_csv as b; b.main([r"C:\\Users\\tjain\\Downloads\\Happiest_Health"])

Output goes to the repo's data folder (same file names as the twin_dataset plan):

  STATIC  (one row per patient / admission)
    patients.csv        subject_id   demographics, body size, lifetime condition flags, data availability
    admissions.csv      hadm_id      LOS, ICU use, condition flags, outcomes (death, 30-day readmission)
    diagnoses.csv       hadm_id      every ICD code with its title
    labs.csv            long         selected blood labs (subject_id, hadm_id, charttime, lab, value)
    meds.csv            long         cardiac drug orders by class
  DYNAMIC (time series)
    vitals_hourly.csv   stay_id,hour ICU charted vitals, one row per ICU stay-hour
    rhythm.csv          stay_id,time nurse-charted heart rhythm (AF, SR, ...)
    wearable_5min.csv   record,time  bedside monitor numerics (mimic4wdb), 5-minute means, wide
  ECG
    ecg.csv             study_id     12-lead ECG features computed from the raw signal (lead II)
  LABELS
    events.csv          subject_id   timed adverse events to predict
  SEPARATE COHORT
    icare_patients.csv  patient_id   I-CARE cardiac-arrest metadata (no shared IDs with MIMIC)

MIMIC tables all carry subject_id. MIMIC shifts dates per patient, so only
compare times within one patient. Never share these files publicly: the MIMIC
licence forbids it.
"""
import argparse
import glob
import io
import re
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

CONDITIONS = {
    "hf":     {9: ["428"], 10: ["I50"]},
    "af":     {9: ["42731"], 10: ["I48"]},
    "cad":    {9: ["410", "411", "412", "413", "414"], 10: ["I20", "I21", "I22", "I23", "I24", "I25"]},
    "mi":     {9: ["410"], 10: ["I21", "I22"]},
    "arrest": {9: ["4275"], 10: ["I46"]},
    "htn":    {9: ["401", "402", "403", "404", "405"], 10: ["I10", "I11", "I12", "I13", "I15", "I16"]},
    "dm":     {9: ["250"], 10: ["E08", "E09", "E10", "E11", "E13"]},
    "ckd":    {9: ["585"], 10: ["N18"]},
    "stroke": {9: ["433", "434", "436"], 10: ["I63", "I64"]},
    "sepsis": {9: ["99591", "99592", "78552"], 10: ["A40", "A41", "R652"]},
}

# lab name -> d_labitems itemid (blood), checked against the demo's d_labitems
LABS = {
    50971: "potassium", 50983: "sodium", 50912: "creatinine", 51006: "bun", 50882: "bicarbonate",
    51265: "platelets", 51222: "hemoglobin", 51301: "wbc", 50931: "glucose", 50960: "magnesium",
    50893: "calcium", 51237: "inr", 50813: "lactate", 50862: "albumin", 51003: "troponin_t",
    50910: "ck", 50911: "ck_mb", 50852: "hba1c", 50963: "ntprobnp", 50907: "chol_total",
    50904: "hdl", 50905: "ldl", 51000: "triglycerides",
}

# chartevents itemid -> column
VITALS = {
    220045: "hr", 220277: "spo2", 220210: "rr", 224690: "rr",
    220050: "sbp_art", 220051: "dbp_art", 220052: "map_art",
    220179: "sbp_nibp", 220180: "dbp_nibp", 220181: "map_nibp",
    223761: "temp_f", 223762: "temp_c_raw",
    220739: "gcs_eye", 223900: "gcs_verbal", 223901: "gcs_motor",
}
RHYTHM_ITEM = 220048
VASOPRESSORS = [221906, 221289, 221662, 222315, 221749]

DRUG_CLASSES = [
    ("beta_blocker", r"olol\b|carvedilol|labetalol"),
    ("anticoagulant", r"heparin|enoxaparin|warfarin|apixaban|rivaroxaban|dabigatran|edoxaban"),
    ("antiplatelet", r"aspirin|clopidogrel|ticagrelor|prasugrel"),
    ("statin", r"statin\b"),
    ("diuretic", r"furosemide|torsemide|bumetanide|hydrochlorothiazide|chlorthalidone|spironolactone|eplerenone"),
    ("ace_arb", r"pril\b|sartan\b|sacubitril"),
    ("nitrate", r"nitroglycerin|isosorbide"),
    ("antiarrhythmic", r"amiodarone|sotalol|dofetilide|flecainide|propafenone|digoxin|dronedarone"),
    ("vasopressor", r"norepinephrine|epinephrine|vasopressin|phenylephrine|dopamine"),
]

# mimic4wdb numerics column -> clean name (columns absent in a record stay empty)
WEARABLE = {
    "HR [bpm]": "hr", "btbHR [bpm]": "hr_beat", "Pulse (SpO2) [bpm]": "pulse_spo2", "SpO2 [%]": "spo2",
    "Perf [NU]": "perfusion_index", "RR [rpm]": "rr", "NBPs [mmHg]": "nbp_sys", "NBPd [mmHg]": "nbp_dia",
    "NBPm [mmHg]": "nbp_mean", "ABPs [mmHg]": "abp_sys", "ABPd [mmHg]": "abp_dia", "ABPm [mmHg]": "abp_mean",
    "PVC [/min]": "pvc_per_min", "QT [msec]": "qt_ms", "QTc [msec]": "qtc_ms", "ST-III [mm]": "st_iii_mm",
    "ST-V [mm]": "st_v_mm", "Tblood [degC]": "temp_c",
}


class Source:
    """A dataset either still zipped or extracted; open(relpath) works for both."""

    def __init__(self, root, pattern):
        zips = sorted(root.glob(pattern + "*.zip"))
        dirs = [d for d in root.rglob(pattern + "*") if d.is_dir()]
        self.zip, self.dir = (zipfile.ZipFile(zips[0]), None) if zips else (None, dirs[0] if dirs else None)
        self.ok = bool(self.zip or self.dir)
        if self.zip:
            self.names = self.zip.namelist()
            self.prefix = self.names[0].split("/")[0] + "/"
        self.where = zips[0].name if zips else self.dir

    def open(self, rel):
        return self.zip.open(self.prefix + rel) if self.zip else open(self.dir / rel, "rb")

    def table(self, rel, **kw):
        return pd.read_csv(self.open(rel), compression="gzip" if rel.endswith(".gz") else None, **kw)


def write(df, out, name):
    df.to_csv(out / name, index=False)
    print(f"  wrote {name:<20} {len(df):>8,} rows  {df.shape[1]} cols")


def flag_conditions(dx):
    code = dx.icd_code.astype(str).str.strip()
    for cond, by_version in CONDITIONS.items():
        hit = pd.Series(False, index=dx.index)
        for version, prefixes in by_version.items():
            hit |= (dx.icd_version == version) & code.str.startswith(tuple(prefixes))
        dx[cond] = hit.astype(int)
    return dx


def build_mimic(src, out):
    print(f"MIMIC-IV clinical from {src.where}")
    T = lambda n, **kw: src.table(n, **kw)
    ts = lambda df, *cols: df.assign(**{c: pd.to_datetime(df[c], errors="coerce") for c in cols})

    pats = ts(T("hosp/patients.csv.gz"), "dod")
    adm = ts(T("hosp/admissions.csv.gz"), "admittime", "dischtime", "deathtime")
    stays = ts(T("icu/icustays.csv.gz"), "intime", "outtime")
    dx = flag_conditions(T("hosp/diagnoses_icd.csv.gz"))
    d_icd = T("hosp/d_icd_diagnoses.csv.gz")

    # diagnoses.csv
    write(dx.merge(d_icd, on=["icd_code", "icd_version"], how="left")
            [["subject_id", "hadm_id", "seq_num", "icd_code", "icd_version", "long_title"]]
            .sort_values(["subject_id", "hadm_id", "seq_num"]), out, "diagnoses.csv")
    flags = dx.groupby(["subject_id", "hadm_id"])[list(CONDITIONS)].max().reset_index()

    # admissions.csv
    a = adm.sort_values(["subject_id", "admittime"]).copy()
    a["next_admit"] = a.groupby("subject_id").admittime.shift(-1)
    a["readmit_30d"] = ((a.next_admit - a.dischtime) <= pd.Timedelta(days=30)).astype(int)
    a["los_days"] = ((a.dischtime - a.admittime).dt.total_seconds() / 86400).round(2)
    icu = stays.groupby("hadm_id").agg(icu_stays=("stay_id", "size"), icu_los_days=("los", "sum")).reset_index()
    a = a.merge(icu, on="hadm_id", how="left").merge(flags, on=["subject_id", "hadm_id"], how="left")
    a["icu_stays"] = a.icu_stays.fillna(0).astype(int)
    a[list(CONDITIONS)] = a[list(CONDITIONS)].fillna(0).astype(int)
    a["age_at_admit"] = a.merge(pats, on="subject_id", how="left").pipe(
        lambda m: m.anchor_age + (m.admittime.dt.year - m.anchor_year)).values
    write(a[["subject_id", "hadm_id", "admittime", "dischtime", "deathtime", "age_at_admit", "admission_type",
             "admission_location", "discharge_location", "insurance", "race", "los_days", "icu_stays",
             "icu_los_days", *CONDITIONS, "hospital_expire_flag", "readmit_30d"]], out, "admissions.csv")

    # labs.csv
    le = T("hosp/labevents.csv.gz", usecols=["subject_id", "hadm_id", "itemid", "charttime", "valuenum",
                                            "valueuom", "ref_range_lower", "ref_range_upper", "flag"])
    le = le[le.itemid.isin(LABS) & le.valuenum.notna()].copy()
    le["lab"] = le.itemid.map(LABS)
    le["hadm_id"] = le.hadm_id.astype("Int64")
    labs = ts(le, "charttime").rename(columns={"valuenum": "value", "valueuom": "unit"})
    labs = labs[["subject_id", "hadm_id", "charttime", "lab", "value", "unit", "ref_range_lower",
                 "ref_range_upper", "flag"]].sort_values(["subject_id", "charttime", "lab"])
    write(labs, out, "labs.csv")

    # meds.csv
    rx = T("hosp/prescriptions.csv.gz", usecols=["subject_id", "hadm_id", "starttime", "stoptime", "drug",
                                                "dose_val_rx", "dose_unit_rx", "route"])
    low = rx.drug.fillna("").str.lower()
    rx["drug_class"] = None
    for cls, pat in reversed(DRUG_CLASSES):
        rx.loc[low.str.contains(pat, regex=True), "drug_class"] = cls
    write(ts(rx[rx.drug_class.notna()], "starttime", "stoptime").sort_values(["subject_id", "starttime"]),
          out, "meds.csv")

    # vitals_hourly.csv + rhythm.csv
    ce = T("icu/chartevents.csv.gz", usecols=["subject_id", "hadm_id", "stay_id", "itemid", "charttime",
                                             "value", "valuenum"])
    ce = ce[ce.itemid.isin(list(VITALS) + [RHYTHM_ITEM])]
    ce = ts(ce, "charttime")
    rhythm = ce[ce.itemid == RHYTHM_ITEM][["subject_id", "hadm_id", "stay_id", "charttime", "value"]]
    write(rhythm.rename(columns={"value": "rhythm"}).sort_values(["subject_id", "charttime"]), out, "rhythm.csv")

    v = ce[ce.itemid.isin(VITALS) & ce.valuenum.notna()].copy()
    v["name"] = v.itemid.map(VITALS)
    v["hour"] = v.charttime.dt.floor("h")
    w = v.pivot_table(index=["subject_id", "hadm_id", "stay_id", "hour"], columns="name", values="valuenum",
                      aggfunc="mean").reset_index()
    for c in set(VITALS.values()) - set(w.columns):
        w[c] = np.nan
    w["sbp"] = w.sbp_art.fillna(w.sbp_nibp)
    w["dbp"] = w.dbp_art.fillna(w.dbp_nibp)
    w["map"] = w.map_art.fillna(w.map_nibp)
    w["temp_c"] = w.temp_c_raw.fillna((w.temp_f - 32) * 5 / 9)
    w["gcs_total"] = w[["gcs_eye", "gcs_verbal", "gcs_motor"]].sum(axis=1, min_count=3)
    w = w.merge(stays[["stay_id", "intime"]], on="stay_id", how="left")
    w["hours_since_icu_admit"] = ((w.hour - w.intime).dt.total_seconds() / 3600).round(1)
    w["bp_source"] = np.where(w.sbp_art.notna(), "arterial", np.where(w.sbp_nibp.notna(), "cuff", None))
    cols = ["subject_id", "hadm_id", "stay_id", "hour", "hours_since_icu_admit", "hr", "spo2", "rr", "sbp",
            "dbp", "map", "bp_source", "temp_c", "gcs_total"]
    w[["hr", "spo2", "rr", "sbp", "dbp", "map", "temp_c", "gcs_total"]] = \
        w[["hr", "spo2", "rr", "sbp", "dbp", "map", "temp_c", "gcs_total"]].round(1)
    write(w[cols].sort_values(["subject_id", "stay_id", "hour"]), out, "vitals_hourly.csv")

    # events.csv: timed adverse events (labels)
    ev = [
        adm[adm.deathtime.notna()].assign(event="death_in_hospital", event_time=lambda d: d.deathtime),
        stays.assign(event="icu_admit", event_time=lambda d: d.intime),
        rhythm[rhythm.value.astype(str).str.match(r"^A[ -]?Fib|^AF\b", case=False)]
            .groupby(["subject_id", "hadm_id"]).charttime.min().reset_index()
            .assign(event="af_charted", event_time=lambda d: d.charttime),
        labs[(labs.lab == "troponin_t") & (labs.value > 0.01)].groupby(["subject_id", "hadm_id"]).charttime.min()
            .reset_index().assign(event="troponin_elevated", event_time=lambda d: d.charttime),
        labs[(labs.lab == "lactate") & (labs.value >= 4)].groupby(["subject_id", "hadm_id"]).charttime.min()
            .reset_index().assign(event="lactate_ge_4", event_time=lambda d: d.charttime),
    ]
    ie = ts(T("icu/inputevents.csv.gz", usecols=["subject_id", "hadm_id", "stay_id", "itemid", "starttime"]),
            "starttime")
    ev.append(ie[ie.itemid.isin(VASOPRESSORS)].groupby(["subject_id", "hadm_id"]).starttime.min().reset_index()
              .assign(event="vasopressor_start", event_time=lambda d: d.starttime))
    events = pd.concat([e[["subject_id", "hadm_id", "event_time", "event"]] for e in ev])
    events["hadm_id"] = events.hadm_id.astype("Int64")
    write(events.dropna(subset=["event_time"]).sort_values(["subject_id", "event_time"]), out, "events.csv")

    # patients.csv
    omr = ts(T("hosp/omr.csv.gz"), "chartdate").sort_values("chartdate")
    latest = omr.groupby(["subject_id", "result_name"]).result_value.last().unstack()
    body = pd.DataFrame(index=latest.index)
    for col, name in [("bmi", "BMI (kg/m2)"), ("height_in", "Height (Inches)"), ("weight_lbs", "Weight (Lbs)")]:
        body[col] = pd.to_numeric(latest.get(name), errors="coerce")
    bp = latest.get("Blood Pressure", pd.Series(dtype=str)).astype(str).str.extract(r"(\d+)/(\d+)").astype(float)
    body["sbp_outpatient"], body["dbp_outpatient"] = bp[0], bp[1]
    ever = flags.groupby("subject_id")[list(CONDITIONS)].max().add_prefix("ever_")
    n_adm = adm.groupby("subject_id").agg(n_admissions=("hadm_id", "size"))
    p = (pats.set_index("subject_id")[["gender", "anchor_age", "anchor_year", "anchor_year_group", "dod"]]
         .join(body).join(n_adm).join(ever).join(stays.groupby("subject_id").size().rename("n_icu_stays")))
    p["died"] = p.dod.notna().astype(int)
    p = p.fillna({c: 0 for c in ["n_admissions", "n_icu_stays", *ever.columns]})
    return p.reset_index(), adm, stays


def read_ecg_record(src, rel):
    """Read a WFDB format-16 record from MIMIC-IV-ECG; returns (signals mV [n, leads], names, fs)."""
    hea = src.open(rel + ".hea").read().decode().splitlines()
    head = hea[0].split()
    nsig, fs, nsamp = int(head[1]), float(head[2]), int(head[3])
    gains, base, names = [], [], []
    for line in hea[1:1 + nsig]:
        f = line.split()
        m = re.match(r"([\d.]+)(?:\((-?\d+)\))?", f[2])
        gains.append(float(m.group(1)))
        base.append(int(m.group(2) or 0))
        names.append(f[-1])
    raw = np.frombuffer(src.open(rel + ".dat").read(), dtype="<i2")[: nsamp * nsig].reshape(-1, nsig)
    sig = (raw.astype(float) - base) / gains
    sig[raw == -32768] = np.nan
    return sig, names, fs


def r_peaks(x, fs):
    """R-peak detector: band-pass (~5-30 Hz), slope energy, 300 ms refractory period.
    Agrees across leads II/V2/V5 within one beat on ~94% of the demo ECGs."""
    x = np.nan_to_num(x - np.nanmedian(x))
    ma = lambda s, k: np.convolve(s, np.ones(max(int(k), 1)) / max(int(k), 1), "same")
    bp = ma(x, fs / 30) - ma(x, fs / 5)
    e = ma(np.abs(np.gradient(bp)), 0.08 * fs)
    thr, ref, w = 0.35 * np.percentile(e, 98), int(0.3 * fs), int(0.06 * fs)
    cand = np.flatnonzero((e[1:-1] > e[:-2]) & (e[1:-1] >= e[2:]) & (e[1:-1] > thr)) + 1
    pk = []
    for c in cand:
        if pk and c - pk[-1] < ref:
            if e[c] > e[pk[-1]]:
                pk[-1] = c
            continue
        pk.append(c)
    hp = x - ma(x, 0.2 * fs)
    peaks = np.array([max(c - w, 0) + int(np.argmax(np.abs(bp[max(c - w, 0):c + w]))) for c in pk], dtype=int)
    return peaks, hp


def build_ecg(src, out, adm):
    print(f"MIMIC-IV-ECG from {src.where}")
    rl = src.table("record_list.csv", parse_dates=["ecg_time"])
    rows = []
    for i, r in enumerate(rl.itertuples(), 1):
        row = {"subject_id": r.subject_id, "study_id": r.study_id, "ecg_time": r.ecg_time}
        try:
            sig, names, fs = read_ecg_record(src, r.path)
            # detect on three leads and keep the one whose beat count is the median (robust to one bad lead)
            found = {l: r_peaks(sig[:, names.index(l)], fs) for l in ("II", "V2", "V5") if l in names}
            counts = sorted(len(v[0]) for v in found.values())
            lead = next(l for l, v in found.items() if len(v[0]) == counts[len(counts) // 2])
            pk, hp = found[lead]
            rr = np.diff(pk) / fs * 1000
            amp = np.abs(hp[pk])
            row.update(
                lead_used=lead, n_beats=len(pk), leads_agree=int(counts[-1] - counts[0] <= 1),
                hr_bpm=round(60000 / rr.mean(), 1) if len(rr) else np.nan,
                rr_mean_ms=round(rr.mean(), 1) if len(rr) else np.nan,
                sdnn_ms=round(rr.std(ddof=1), 1) if len(rr) > 2 else np.nan,
                rmssd_ms=round(np.sqrt(np.mean(np.diff(rr) ** 2)), 1) if len(rr) > 2 else np.nan,
                pnn50=round(np.mean(np.abs(np.diff(rr)) > 50), 3) if len(rr) > 2 else np.nan,
                rr_cv=round(rr.std(ddof=1) / rr.mean(), 3) if len(rr) > 2 else np.nan,
                r_amp_mv=round(float(np.median(amp)), 3) if len(amp) else np.nan,
                missing_frac=round(float(np.isnan(sig).mean()), 4),
                **{f"rms_{n}_mv": round(float(np.sqrt(np.nanmean(sig[:, j] ** 2))), 3) for j, n in enumerate(names)},
            )
        except Exception as e:  # keep the row so the gap is visible
            row["error"] = str(e)[:120]
        rows.append(row)
        if i % 100 == 0:
            print(f"    {i}/{len(rl)} ECGs")
    ecg = pd.DataFrame(rows)
    # attach the admission the ECG was taken in, if any
    m = ecg.merge(adm[["subject_id", "hadm_id", "admittime", "dischtime"]], on="subject_id", how="left")
    m = m[(m.ecg_time >= m.admittime) & (m.ecg_time <= m.dischtime)].drop_duplicates("study_id")
    ecg = ecg.merge(m[["study_id", "hadm_id"]], on="study_id", how="left")
    ecg["hadm_id"] = ecg.hadm_id.astype("Int64")
    ecg = ecg[["subject_id", "study_id", "hadm_id", "ecg_time"] + [c for c in ecg.columns if c not in
              ("subject_id", "study_id", "hadm_id", "ecg_time")]]
    write(ecg.sort_values(["subject_id", "ecg_time"]), out, "ecg.csv")
    return set(ecg.subject_id)


def read_partial_gz(path):
    """Read a .csv.gz, keeping the readable rows if the download was cut off."""
    import gzip
    lines, complete = [], True
    try:
        with gzip.open(path, "rt") as fh:
            for line in fh:
                lines.append(line)
    except (EOFError, OSError):
        complete = False
        if lines and not lines[-1].endswith("\n"):
            lines.pop()
    if len(lines) < 2:
        return None, complete
    return pd.read_csv(io.StringIO("".join(lines))), complete


def build_wearable(root, out, stays):
    files = sorted(glob.glob(str(root / "**" / "*n.csv.gz"), recursive=True))
    files = [f for f in files if Path(f).stat().st_size > 0]
    print(f"Waveform DB numerics: {len(files)} non-empty records")
    seen, frames = set(), []
    for f in files:
        f = Path(f)
        rec = f.name[: -len("n.csv.gz")]
        if rec in seen:  # same record downloaded into two folders
            continue
        hea = f.parent / f"{rec}.hea"
        head = [l for l in (hea.read_text().splitlines() if hea.exists() else []) if l.strip() and not l.startswith("#")]
        if not head or len(head[0].split()) < 6:
            print(f"    skip {rec}: header {hea.name} missing or empty, so no start time")
            continue
        head = head[0].split()
        freq = float(head[2].split("/")[1]) if "/" in head[2] else float(head[2])
        start = pd.to_datetime(f"{head[5]} {head[4]}", format="%d/%m/%Y %H:%M:%S.%f", errors="coerce")
        if pd.isna(start):
            start = pd.to_datetime(f"{head[5]} {head[4]}", dayfirst=True, errors="coerce")
        df, complete = read_partial_gz(f)
        if df is None:
            print(f"    skip {rec}: unreadable")
            continue
        if not complete:
            print(f"    {rec}: download truncated, kept the {len(df):,} rows that were readable")
        df.index = start + pd.to_timedelta(df.pop("time") / freq, unit="s")
        df = df[[c for c in WEARABLE if c in df.columns]].rename(columns=WEARABLE).apply(pd.to_numeric, errors="coerce")
        df = df.T.groupby(level=0).mean().T  # merge duplicate clean names
        g = df.resample("5min").mean().dropna(how="all").round(2)
        g.insert(0, "record_id", int(rec))
        g.insert(0, "subject_id", int(re.search(r"p(\d{8})", str(f)).group(1)))
        frames.append(g.rename_axis("time").reset_index())
        seen.add(rec)
    if not frames:
        return set()
    w = pd.concat(frames, ignore_index=True)
    for c in dict.fromkeys(WEARABLE.values()):
        if c not in w.columns:
            w[c] = np.nan
    # link to an ICU stay when the subject is also in the clinical demo
    m = w.reset_index().merge(stays[["subject_id", "stay_id", "intime", "outtime"]], on="subject_id")
    m = m[(m.time >= m.intime) & (m.time <= m.outtime)].drop_duplicates("index").set_index("index")
    w["stay_id"] = m.stay_id.astype("Int64")
    w = w[["subject_id", "record_id", "stay_id", "time", *dict.fromkeys(WEARABLE.values())]]
    write(w.sort_values(["subject_id", "record_id", "time"]), out, "wearable_5min.csv")
    return set(w.subject_id)


def build_icare(root, out):
    rows = []
    for txt in sorted(root.rglob("*.txt")):
        if txt.stem != txt.parent.name:
            continue
        rows.append(dict(l.split(": ", 1) for l in txt.read_text(errors="ignore").splitlines() if ": " in l))
    if not rows:
        return
    df = pd.DataFrame(rows)
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    df = df.rename(columns={"patient": "patient_id"})
    for c in ("age", "rosc", "ttm", "cpc"):
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in ("ohca", "shockable_rhythm"):
        if c in df:
            df[c] = df[c].map({"True": 1, "False": 0})
    if "outcome" in df:
        df["poor_outcome"] = (df.outcome == "Poor").astype(int)
    write(df, out, "icare_patients.csv")


DEFAULT_ROOT = Path(__file__).resolve().parents[1] / "data" / "raw"   # put the PhysioNet downloads here


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", type=Path, nargs="?", default=DEFAULT_ROOT,
                    help=f"folder holding the downloads (default {DEFAULT_ROOT})")
    ap.add_argument("--out", type=Path)
    # parse_known_args: Jupyter passes its own "-f kernel.json" argument, which would otherwise crash argparse
    args, _ = ap.parse_known_args(argv)
    out = args.out or Path(__file__).resolve().parents[1] / "data"
    out.mkdir(parents=True, exist_ok=True)

    clin = Source(args.root, "mimic-iv-clinical-database-demo")
    if not clin.ok:
        sys.exit("MIMIC-IV clinical demo not found")
    patients, adm, stays = build_mimic(clin, out)

    ecg = Source(args.root, "mimic-iv-ecg-demo")
    ecg_subjects = build_ecg(ecg, out, adm) if ecg.ok else set()
    wdb_dir = next((d for d in [args.root / "waveform_data", args.root / "mimic4wdb"] if d.exists()), args.root)
    wear_subjects = build_wearable(wdb_dir, out, stays)

    patients["has_ecg"] = patients.subject_id.isin(ecg_subjects).astype(int)
    patients["has_wearable"] = patients.subject_id.isin(wear_subjects).astype(int)
    write(patients, out, "patients.csv")

    icare = args.root / "icare_data"
    if icare.exists():
        build_icare(icare, out)
    print(f"Done. Files are in {out}")


if __name__ == "__main__":
    main()
