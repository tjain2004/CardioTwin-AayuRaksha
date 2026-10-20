# Personalised wellness: heart-healthy Indian meal suggestions (vegetarian, eggetarian or non-vegetarian), activity
# suggestions matched to the person's abilities and today's risk status, hydration and sleep reminders, daily and weekly
# goals, progress tracking and short educational insights.
#
# Everything here is general wellness information, not medical advice. It never overrides the risk engine: an open
# emergency or a HIGH / re-measure state pauses the exercise suggestions, and restrictions a clinician puts on the record
# (EHR profile key "wellness_restrictions") always win over the person's own goals. Meals that conflict with conditions
# on the record (kidney function, diabetes range HbA1c, a blood thinner) are filtered out or flagged.
from __future__ import annotations

import hashlib
import re
from datetime import date, timedelta

from .auth import Principal
from .db import Database, dumps, loads
from .readings import iso, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS wellness_prefs (patient_id TEXT PRIMARY KEY, prefs_json TEXT NOT NULL, updated_at TEXT NOT NULL,
  updated_by TEXT);
CREATE TABLE IF NOT EXISTS wellness_log (patient_id TEXT NOT NULL, day TEXT NOT NULL, metric TEXT NOT NULL, value REAL NOT NULL,
  updated_at TEXT NOT NULL, PRIMARY KEY (patient_id, day, metric));
"""

DISCLAIMER = ("General wellness information, not a diagnosis or a treatment plan. Follow your own doctor's advice first, "
              "especially about salt, fluids, exercise and medicines.")
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

DIETS = {"veg": "Vegetarian", "eggetarian": "Vegetarian with eggs", "non_veg": "Non-vegetarian"}
REGIONS = {"any": "Any", "north": "North Indian", "south": "South Indian", "east": "East Indian", "west": "West Indian"}
ACTIVITY = {"rest": "Mostly resting / very limited", "light": "Light (short walks)", "moderate": "Moderate (regular walks)"}
LIMITATIONS = {"joint_pain": "Knee or joint pain", "walking_aid": "Uses a walking aid", "breathless_on_exertion": "Breathless when active",
               "recent_hospital_stay": "Recent hospital stay"}
METRICS = {  # metric -> (label, unit, max allowed per day)
    "water_glasses": ("Water", "glasses", 30), "active_minutes": ("Active time", "min", 600), "sleep_hours": ("Sleep", "h", 16),
    "fruit_veg_servings": ("Fruit and vegetables", "servings", 20), "steps": ("Steps", "steps", 60000),
}
DEFAULT_PREFS = {
    "diet": "veg", "region": "any", "activity_level": "light", "limitations": [], "avoid_foods": [],
    "goals": {"steps": 4000, "water_glasses": 8, "sleep_hours": 7, "active_minutes": 20, "fruit_veg_servings": 5},
    "reminders": {"hydration": True, "sleep": True, "wake_time": "06:30", "bed_time": "22:30"},
    "set_by_user": False,
}
GOAL_LIMITS = {"steps": (500, 20000), "water_glasses": (1, 16), "sleep_hours": (4, 10), "active_minutes": (5, 120),
               "fruit_veg_servings": (1, 10)}

ANTICOAGULANTS = ("warfarin", "acenocoumarol", "apixaban", "rivaroxaban", "dabigatran", "edoxaban")
VITAMIN_K_SENSITIVE = ("warfarin", "acenocoumarol")
BETA_BLOCKERS = ("metoprolol", "atenolol", "bisoprolol", "carvedilol", "propranolol", "nebivolol", "labetalol")

# Heart-healthy Indian meals. Tags: diet (veg / egg / non_veg), region, and flags used to filter against the record:
# k = high potassium (left out when kidney function is reduced), vk = leafy greens rich in vitamin K (kept, with a note,
# for people on warfarin), sweet = naturally sweet (portion note in the diabetes range).
MEALS = [
    # breakfast
    ("breakfast", "veg", "west", "Vegetable poha with peanuts", "Flattened rice with peas, carrots and a few peanuts. Use little oil and salt.", "Fibre from vegetables; peanuts give healthy fats.", ()),
    ("breakfast", "veg", "north", "Moong dal chilla with mint chutney", "Thin lentil pancakes with grated vegetables.", "Protein and fibre from lentils, very little oil.", ()),
    ("breakfast", "veg", "any", "Vegetable dalia upma", "Broken wheat cooked with mixed vegetables.", "Whole grain keeps you full and helps cholesterol.", ()),
    ("breakfast", "veg", "south", "Idli with vegetable sambar", "Two or three idlis with a bowl of sambar. Skip the extra salt and podi.", "Steamed, not fried; sambar adds lentils and vegetables.", ()),
    ("breakfast", "veg", "south", "Ragi porridge, unsweetened", "Finger millet cooked in low-fat milk or water, with a few chopped nuts.", "Millet fibre; no added sugar.", ()),
    ("breakfast", "veg", "any", "Oats with low-fat milk, apple and walnuts", "Plain oats (not the flavoured packets), half an apple, 3 or 4 walnut halves.", "Oat fibre lowers LDL cholesterol; walnuts give omega-3.", ("sweet",)),
    ("breakfast", "veg", "north", "Besan vegetable cheela", "Chickpea-flour pancake with onion, tomato and coriander.", "Plant protein, cooked on a non-stick pan.", ()),
    ("breakfast", "egg", "any", "Egg-white bhurji with whole-wheat toast", "Scrambled egg whites with onion, tomato and capsicum, one or two slices of toast.", "Lean protein with little saturated fat.", ()),
    ("breakfast", "egg", "any", "Boiled egg and vegetable sandwich", "One boiled egg with a whole-wheat vegetable sandwich, no butter or cheese spread.", "Balanced protein and fibre.", ()),
    # lunch
    ("lunch", "veg", "north", "Rajma with brown rice and salad", "One bowl rajma, a small portion of brown rice, cucumber-tomato salad.", "Beans give fibre and plant protein.", ("k",)),
    ("lunch", "veg", "north", "Chole (less oil), two phulkas, cucumber raita", "Home-made chole with little oil and no extra salt.", "Chickpeas are rich in fibre; phulkas have no added fat.", ()),
    ("lunch", "veg", "any", "Dal tadka, mixed vegetable sabzi, two phulkas", "A teaspoon of ghee or oil for the tadka is enough.", "Classic balanced plate: half vegetables, a quarter dal, a quarter grains.", ()),
    ("lunch", "veg", "south", "Sambar, beans poriyal and a small portion of red rice", "Go easy on coconut and salt in the poriyal.", "Lentils and vegetables with a whole grain.", ()),
    ("lunch", "veg", "north", "Palak with low-fat paneer and phulka", "Spinach cooked with a little low-fat paneer, no cream.", "Leafy greens and protein.", ("k", "vk")),
    ("lunch", "veg", "west", "Jowar bhakri with methi sabzi and dal", "Sorghum flatbread with fenugreek greens.", "Millet and greens add fibre.", ("vk",)),
    ("lunch", "non_veg", "east", "Steamed or lightly cooked fish curry with rice and greens", "Rohu, pomfret or mackerel in a light curry; small rice portion.", "Fish gives omega-3 fats that are good for the heart.", ()),
    ("lunch", "non_veg", "north", "Tandoori chicken (skinless) with dal and salad", "Grilled, not fried; skip the butter and extra salt.", "Lean protein without the fat of curries.", ()),
    ("lunch", "egg", "any", "Egg curry (less oil) with two phulkas", "One egg in a tomato-onion gravy with little oil.", "Protein with a light gravy.", ()),
    # snacks
    ("snack", "veg", "any", "Roasted chana, unsalted", "A small handful.", "Protein and fibre without frying.", ()),
    ("snack", "veg", "any", "A guava or an apple", "One whole fruit, not juice.", "Fibre and vitamin C; whole fruit is better than juice.", ("sweet",)),
    ("snack", "veg", "any", "Sprouts chaat with lemon", "Moong sprouts, onion, tomato, lemon; go light on chaat masala (it is salty).", "Protein and fibre.", ()),
    ("snack", "veg", "any", "A small handful of unsalted almonds and walnuts", "About 6 to 8 nuts.", "Healthy fats; unsalted keeps sodium down.", ()),
    ("snack", "veg", "any", "Chaas (buttermilk) with roasted jeera", "One glass, little or no salt. Counts towards your fluids.", "Light, cooling and low in fat.", ()),
    ("snack", "veg", "any", "Roasted makhana", "Dry-roasted with a pinch of pepper instead of salt.", "Light snack instead of namkeen or chips.", ()),
    ("snack", "veg", "south", "Tender coconut water", "One glass. Counts towards your fluids.", "Natural drink with no added sugar.", ("k", "sweet")),
    # dinner
    ("dinner", "veg", "any", "Moong dal khichdi with vegetables", "Rice or millet with moong dal and vegetables, a teaspoon of ghee at most.", "Light and easy to digest in the evening.", ()),
    ("dinner", "veg", "west", "Bajra or jowar roti with lauki sabzi and dal", "Bottle gourd cooked with little oil.", "Millet and a light vegetable for dinner.", ()),
    ("dinner", "veg", "north", "Vegetable soup with grilled paneer tikka", "Home-made soup (packet soups are very salty) and a few pieces of low-fat paneer.", "Light, protein-rich dinner.", ()),
    ("dinner", "veg", "any", "Masoor dal, turai sabzi and phulka", "Ridge gourd with little oil.", "Lentil protein and a light vegetable.", ()),
    ("dinner", "non_veg", "any", "Grilled fish with sautéed vegetables", "Any local fish, grilled or pan-cooked with little oil.", "Omega-3 fats and vegetables.", ()),
    ("dinner", "non_veg", "south", "Chicken and vegetable stew (little oil) with one roti", "Skinless chicken with carrots, beans and potatoes; light on coconut milk.", "Lean protein in a light stew.", ()),
    ("dinner", "egg", "any", "Vegetable egg bhurji with one roti", "One egg with plenty of vegetables.", "Protein with vegetables, light on oil.", ()),
]
DIET_ALLOWS = {"veg": {"veg"}, "eggetarian": {"veg", "egg"}, "non_veg": {"veg", "egg", "non_veg"}}
LIMIT_FOODS = ["Pickles, papad and namkeen (very salty)", "Fried snacks: samosa, pakora, bhujia", "Sweets and sugary drinks",
               "Processed meats and packet soups or noodles", "Extra salt at the table (aim for under one teaspoon, 5 g, a day)"]

EXERCISES = {
    "rest": [("Seated ankle pumps and arm raises", "5 minutes, two or three times a day, sitting in a chair."),
             ("Slow breathing", "Breathe in for 4 counts and out for 6, for 3 to 5 minutes. Do not hold your breath.")],
    "light": [("Easy walk after meals", "10 minutes after two meals a day, at a pace where you can talk comfortably."),
              ("Chair exercises", "Sit-to-stand from a chair, 5 to 10 times, with a rest between sets."),
              ("Gentle stretching", "5 to 10 minutes of slow stretches; stop if anything hurts.")],
    "moderate": [("Brisk walk", "20 to 30 minutes most days, at a pace where you can talk but not sing."),
                 ("Gentle yoga", "Simple asanas and slow breathing. Avoid headstands, very deep bends and holding your breath."),
                 ("Stationary cycling", "15 to 20 minutes at an easy to moderate effort.")],
}
CONDITION_TIPS = {
    "hist_af": "Irregular heartbeat on your record: keep wearing the watch so rhythm changes are noticed, and limit caffeine and alcohol.",
    "smoker": "Smoking on your record: stopping is the biggest single thing you can do for your heart. The national quitline is 1800-11-2356.",
    "bmi_30": "Weight above the healthy range: small steady changes work best, like smaller portions and fewer fried snacks.",
    "hba1c": "Blood sugar in the diabetes range: keep sweets and sweet fruit to small portions, choose whole grains, and spread meals through the day.",
    "egfr": "Reduced kidney function: some foods high in potassium (banana, coconut water, spinach, rajma) are left out. Ask your doctor about your own potassium and protein limits.",
    "ix_bleeding": "Past bleeding on your record: tell your doctor about any unusual bleeding or bruising.",
}


def _day(d: str | None) -> date:
    if d is None:
        return date.today()
    if not DAY_RE.match(d or ""):
        raise ValueError("day must be YYYY-MM-DD.")
    return date.fromisoformat(d)


class Wellness:
    def __init__(self, db: Database, app, svc=None):
        self.db, self.app, self.svc = db, app, svc            # app: PatientApp (writer checks, medicines, health profile)
        db.ensure_schema(SCHEMA)

    # ================================================================ preferences
    def prefs(self, patient_id: str) -> dict:
        r = self.db.one("SELECT prefs_json, updated_at FROM wellness_prefs WHERE patient_id=?", (patient_id,))
        saved = loads(r["prefs_json"], {}) if r else {}
        p = {**DEFAULT_PREFS, **saved}
        p["goals"] = {**DEFAULT_PREFS["goals"], **saved.get("goals", {})}
        p["reminders"] = {**DEFAULT_PREFS["reminders"], **saved.get("reminders", {})}
        p["updated_at"] = r["updated_at"] if r else None
        p["options"] = {"diet": DIETS, "region": REGIONS, "activity_level": ACTIVITY, "limitations": LIMITATIONS}
        return p

    def update_prefs(self, p: Principal, patient_id: str, changes: dict) -> dict:
        self.app._writer(p, patient_id)
        cur = {k: v for k, v in self.prefs(patient_id).items() if k not in ("updated_at", "options")}
        if "diet" in changes and changes["diet"] is not None:
            if changes["diet"] not in DIETS:
                raise ValueError(f"diet must be one of {tuple(DIETS)}")
            cur["diet"] = changes["diet"]
        if changes.get("region") is not None:
            if changes["region"] not in REGIONS:
                raise ValueError(f"region must be one of {tuple(REGIONS)}")
            cur["region"] = changes["region"]
        if changes.get("activity_level") is not None:
            if changes["activity_level"] not in ACTIVITY:
                raise ValueError(f"activity_level must be one of {tuple(ACTIVITY)}")
            cur["activity_level"] = changes["activity_level"]
        if changes.get("limitations") is not None:
            bad = [x for x in changes["limitations"] if x not in LIMITATIONS]
            if bad:
                raise ValueError(f"Unknown limitations {bad}; use {tuple(LIMITATIONS)}")
            cur["limitations"] = sorted(set(changes["limitations"]))
        if changes.get("avoid_foods") is not None:
            foods = [re.sub(r"[^\w\s-]", "", f).strip().lower()[:30] for f in changes["avoid_foods"]]
            cur["avoid_foods"] = [f for f in dict.fromkeys(foods) if f][:10]
        if changes.get("goals") is not None:
            g = dict(cur["goals"])
            for k, v in changes["goals"].items():
                if k not in GOAL_LIMITS:
                    raise ValueError(f"Unknown goal {k}")
                lo, hi = GOAL_LIMITS[k]
                if v is None or not (lo <= float(v) <= hi):
                    raise ValueError(f"Goal {k} must be between {lo} and {hi}.")
                g[k] = int(v) if k != "sleep_hours" else round(float(v), 1)
            cur["goals"] = g
        if changes.get("reminders") is not None:
            r = dict(cur["reminders"])
            for k, v in changes["reminders"].items():
                if k in ("hydration", "sleep"):
                    r[k] = bool(v)
                elif k in ("wake_time", "bed_time"):
                    if not TIME_RE.match(str(v)):
                        raise ValueError(f"{k} must be HH:MM")
                    r[k] = v
                else:
                    raise ValueError(f"Unknown reminder setting {k}")
            cur["reminders"] = r
        cur["set_by_user"] = True
        self.db.execute("INSERT OR REPLACE INTO wellness_prefs VALUES (?,?,?,?)", (patient_id, dumps(cur), iso(utcnow()), p.id))
        return self.prefs(patient_id)

    # ================================================================ logging
    def log(self, p: Principal, patient_id: str, day: str, metric: str, value: float, mode: str = "set") -> dict:
        self.app._writer(p, patient_id)
        _day(day)
        if metric not in METRICS:
            raise ValueError(f"metric must be one of {tuple(METRICS)}")
        if mode not in ("set", "add"):
            raise ValueError("mode must be set or add")
        cur = self.db.one("SELECT value FROM wellness_log WHERE patient_id=? AND day=? AND metric=?", (patient_id, day, metric))
        v = (cur["value"] if cur and mode == "add" else 0) + float(value)
        if not (0 <= v <= METRICS[metric][2]):
            raise ValueError(f"{METRICS[metric][0]} must be between 0 and {METRICS[metric][2]} {METRICS[metric][1]} a day.")
        self.db.execute("INSERT OR REPLACE INTO wellness_log VALUES (?,?,?,?,?)", (patient_id, day, metric, v, iso(utcnow())))
        return self.progress(patient_id, day)

    def _logged(self, patient_id: str, start: str, end: str) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        for r in self.db.all("SELECT day, metric, value FROM wellness_log WHERE patient_id=? AND day>=? AND day<=?", (patient_id, start, end)):
            out.setdefault(r["day"], {})[r["metric"]] = r["value"]
        return out

    # ================================================================ context from the existing record and engine
    def context(self, patient_id: str) -> dict:
        """What the plan is allowed to use: conditions and labs on the record, medicines, clinician restrictions, the
        watch baseline and today's risk status. Read from the existing services; nothing here is computed afresh."""
        svc = self.svc
        ehr = svc.ehr(patient_id) if svc else {"profile": {}, "clinical": []}
        hp = self.app.health_profile(ehr)
        meds = [m["name"].lower() for m in self.app.medications(patient_id)]
        restr = (ehr.get("profile") or {}).get("wellness_restrictions") or {}
        st = svc.latest_status(patient_id) if svc else None
        open_alerts = svc.alerts.list(patient_id, open_only=True) if svc else []
        feats = None
        if svc:
            try:
                feats = svc.build_request(patient_id)[1]
            except Exception:
                feats = None
        return {"profile": ehr.get("profile") or {}, "conditions": [c["code"] for c in hp["conditions"]],
                "condition_names": [c["name"] for c in hp["conditions"]], "meds": meds, "restrictions": restr,
                "status": st, "open_alerts": open_alerts, "features": feats,
                "simulated": bool(st and st.get("simulated"))}

    @staticmethod
    def activity_gate(ctx: dict) -> dict:
        """Exercise suggestions follow the risk engine and the clinician, never the other way round."""
        st, restr = ctx.get("status"), ctx.get("restrictions") or {}
        critical = [a for a in ctx.get("open_alerts", []) if a["severity"] == "critical"]
        if restr.get("no_exercise"):
            return {"state": "PAUSED", "reason": "Your doctor has asked you not to exercise for now. " + (restr.get("note") or "")}
        if critical or (st and st.get("emergency_state") == "TRIGGERED"):
            return {"state": "PAUSED", "reason": "There is an open emergency alert. Do not exercise. Follow the emergency screen and call 112 if you feel unwell."}
        if st and st.get("emergency_state") == "REVIEW_REQUIRED":
            return {"state": "PAUSED", "reason": "Your watch asked for a re-measure. Rest now and re-measure before any activity."}
        if st and st.get("tier") == "HIGH":
            return {"state": "GENTLE_ONLY", "reason": "Your latest check shows a high-risk pattern. Keep to rest and very gentle movement until your care team has reviewed it."}
        if not st or st.get("status") != "OK":
            return {"state": "GENTLE_ONLY", "reason": "There is no up-to-date heart check, so only gentle activity is suggested."}
        return {"state": "OK", "reason": None}

    # ================================================================ the plan
    def plan(self, patient_id: str, day: str | None = None) -> dict:
        d = _day(day)
        prefs = self.prefs(patient_id)
        ctx = self.context(patient_id)
        conds, meds = set(ctx["conditions"]), ctx["meds"]
        kidney, diabetes = "egfr" in conds, "hba1c" in conds
        vit_k = any(m.startswith(x) for m in meds for x in VITAMIN_K_SENSITIVE)
        anticoag = any(m.startswith(x) for m in meds for x in ANTICOAGULANTS)
        beta = any(m.startswith(x) for m in meds for x in BETA_BLOCKERS)
        avoid = prefs["avoid_foods"]
        allowed = DIET_ALLOWS[prefs["diet"]]

        def ok(m):
            slot, diet, region, name, desc, why, flags = m
            if diet not in allowed:
                return False
            if kidney and "k" in flags:
                return False
            text = f"{name} {desc}".lower()
            return not any(a and a in text for a in avoid)

        seed = int(hashlib.sha256(f"{patient_id}|{d.isoformat()}".encode()).hexdigest()[:8], 16)
        meals = []
        for i, slot in enumerate(("breakfast", "lunch", "snack", "dinner")):
            cands = [m for m in MEALS if m[0] == slot and ok(m)]
            if prefs["region"] != "any":
                local = [m for m in cands if m[2] in (prefs["region"], "any")]
                cands = local or cands
            if prefs["diet"] == "non_veg" and slot in ("lunch", "dinner"):
                nv = [m for m in cands if m[1] == "non_veg"]
                cands = nv if nv and (seed >> i) % 2 == 0 else cands
            if not cands:
                continue
            m = cands[(seed + i * 7) % len(cands)]
            notes = []
            if vit_k and "vk" in m[6]:
                notes.append("You take warfarin: keep the amount of leafy greens about the same from week to week, not none and not lots.")
            if diabetes and "sweet" in m[6]:
                notes.append("Blood sugar on your record: keep this to a small portion.")
            alts = [x[3] for x in cands if x[3] != m[3]][:2]
            meals.append({"slot": slot, "name": m[3], "description": m[4], "why": m[5], "diet": m[1],
                          "diet_label": {"veg": "Veg", "egg": "Egg", "non_veg": "Non-veg"}[m[1]], "notes": notes, "alternatives": alts})

        gate = self.activity_gate(ctx)
        level = prefs["activity_level"]
        lim = set(prefs["limitations"])
        if gate["state"] == "GENTLE_ONLY" or lim & {"walking_aid", "breathless_on_exertion"} or "recent_hospital_stay" in lim:
            level = "rest" if level == "rest" else "light"
        exercises = [] if gate["state"] == "PAUSED" else [{"name": n, "how": h} for n, h in EXERCISES[level]]
        if "joint_pain" in lim and exercises:
            exercises = [e for e in exercises if "Brisk" not in e["name"]] + [{"name": "Chair or water-based exercise", "how": "Easier on the knees than walking far; 10 to 20 minutes."}]
        hr_zone = self._hr_zone(ctx, beta) if gate["state"] == "OK" and level != "rest" else None

        goals = dict(prefs["goals"])
        restr = ctx["restrictions"]
        fluid_note = None
        if restr.get("fluid_limit_ml"):
            cap = max(1, int(float(restr["fluid_limit_ml"]) // 250))
            goals["water_glasses"] = min(goals["water_glasses"], cap)
            fluid_note = f"Your doctor set a fluid limit of {int(restr['fluid_limit_ml'])} ml a day (all drinks, about {cap} glasses). Do not go over it."
        if gate["state"] == "PAUSED":
            goals["active_minutes"] = 0
            goals["steps"] = 0

        tips = [CONDITION_TIPS[c] for c in ctx["conditions"] if c in CONDITION_TIPS]
        if anticoag:
            tips.append("You take a blood thinner: do not start herbal supplements or new painkillers without asking your doctor.")
        if restr.get("salt_limit_g"):
            tips.append(f"Your doctor set a salt limit of {restr['salt_limit_g']} g a day.")
        if restr.get("note") and gate["state"] != "PAUSED":
            tips.append(f"From your doctor: {restr['note']}")

        return {"patient_id": patient_id, "day": d.isoformat(), "prefs": prefs, "meals": meals, "limit_foods": LIMIT_FOODS,
                "activity": {"gate": gate, "level": level, "level_label": ACTIVITY[level], "suggestions": exercises,
                             "heart_rate_zone": hr_zone,
                             "talk_test": "A safe pace is one where you can still talk in full sentences. Stop and rest if you get chest pain, "
                                          "feel faint or very breathless; call 112 if it does not settle within a few minutes."},
                "goals": goals, "fluid_note": fluid_note, "tips": tips,
                "based_on": {"conditions": ctx["condition_names"], "medicines": [m.title() for m in meds],
                             "clinician_restrictions": restr or None, "risk_status": (ctx["status"] or {}).get("tier"),
                             "simulated": ctx["simulated"]},
                "disclaimer": DISCLAIMER}

    @staticmethod
    def _hr_zone(ctx: dict, beta: bool) -> dict | None:
        age = ctx["profile"].get("age_at_admit")
        hr = ((ctx.get("features") or {}).get("signals") or {}).get("hr", {}).get("baseline", {})
        if not isinstance(age, (int, float)) or hr.get("status") not in ("AVAILABLE", "UNRELIABLE") or hr.get("median") is None:
            return None
        try:
            import twin_rules                     # the project's rule file (Karvonen zone), importable once the engine loaded
        except ImportError:
            return None
        z = twin_rules.safe_exercise_zone(float(age), float(hr["median"]), on_beta_blocker=beta)
        return {"low": z["zone"][0], "high": z["zone"][1], "hr_max": z["hr_max"], "resting_hr": round(hr["median"]),
                "method": "Karvonen heart-rate reserve at 40-60% (twin_rules.safe_exercise_zone)" + (", beta-blocker formula" if beta else ""),
                "note": "A guide from your watch baseline, not a prescription. Ask your doctor or cardiac rehab team for your own range." +
                        (" On a beta blocker, heart-rate targets are less reliable: use the talk test." if beta else ""),
                "simulated": ctx["simulated"]}

    # ================================================================ progress and insights
    def progress(self, patient_id: str, day: str | None = None) -> dict:
        d = _day(day)
        plan_goals = self.plan(patient_id, d.isoformat())["goals"]
        start = (d - timedelta(days=6)).isoformat()
        logged = self._logged(patient_id, start, d.isoformat())
        watch_steps = None
        if self.svc:
            try:
                f = self.svc.build_request(patient_id)[1]
                s = f["signals"].get("steps", {}).get("24h")
                watch_steps = int(s) if s else None
            except Exception:
                watch_steps = None
        days = []
        for i in range(6, -1, -1):
            dd = (d - timedelta(days=i)).isoformat()
            vals = dict(logged.get(dd, {}))
            days.append({"day": dd, "values": vals,
                         "met": {k: (vals.get(k, 0) >= g if g else None) for k, g in plan_goals.items()}})
        today = days[-1]["values"]
        items = []
        for k, (label, unit, _) in METRICS.items():
            goal = plan_goals.get(k)
            v = today.get(k)
            src = "you"
            if k == "steps" and v is None and watch_steps is not None:
                v, src = watch_steps, "watch, last 24 h"
            items.append({"metric": k, "label": label, "unit": unit, "value": v or 0, "goal": goal, "source": src,
                          "pct": (min(100, round(100 * (v or 0) / goal)) if goal else None)})
        adherence = self.app.adherence(patient_id, 7, d.isoformat())["days"]
        return {"patient_id": patient_id, "day": d.isoformat(), "today": items, "week": days,
                "insights": self._insights(days, plan_goals, adherence), "medicine_adherence": adherence,
                "simulated_steps": bool(watch_steps is not None and self.svc and self.svc.is_simulated(patient_id))}

    @staticmethod
    def _insights(days: list[dict], goals: dict, adherence: list[dict]) -> list[str]:
        out = []
        logged_days = [x for x in days if x["values"]]
        if not logged_days:
            return ["Log your water, sleep and activity for a few days to see your weekly picture here."]
        for k, label in (("water_glasses", "water"), ("active_minutes", "activity"), ("fruit_veg_servings", "fruit and vegetables")):
            if goals.get(k):
                met = sum(1 for x in days if x["values"].get(k, 0) >= goals[k])
                out.append(f"You met your {label} goal on {met} of the last 7 days.")
        sleeps = [x["values"]["sleep_hours"] for x in days if "sleep_hours" in x["values"]]
        if sleeps:
            short = sum(1 for s in sleeps if s < 6)
            avg = sum(sleeps) / len(sleeps)
            out.append(f"Average sleep {avg:.1f} h over {len(sleeps)} logged nights." +
                       (f" {short} night{'s' if short > 1 else ''} under 6 h; regular short sleep is linked to higher blood pressure." if short else ""))
        due = sum(a["due"] for a in adherence)
        if due:
            pct = round(100 * sum(a["taken"] for a in adherence) / due)
            out.append(f"Medicines marked taken: {pct}% of doses in the last 7 days." + (" Reminders in Medicines can help." if pct < 80 else ""))
        return out

    # ================================================================ reminders
    def reminders(self, patient_id: str) -> list[dict]:
        r = self.prefs(patient_id)["reminders"]
        out = []
        wake, bed = r["wake_time"], r["bed_time"]
        if r["hydration"]:
            h = int(wake[:2]) + 2
            while h < int(bed[:2]) - 1:
                out.append({"kind": "hydration", "time": f"{h:02d}:00", "text": "Time for a glass of water (within any fluid limit your doctor set)."})
                h += 2
        if r["sleep"]:
            bh, bm = int(bed[:2]), int(bed[3:])
            t = (bh * 60 + bm - 30) % (24 * 60)
            out.append({"kind": "sleep", "time": f"{t // 60:02d}:{t % 60:02d}", "text": "Wind down for bed in 30 minutes: screens off, lights low."})
        return out

    # ================================================================ for the chatbot
    def summary_for_chat(self, patient_id: str) -> dict:
        pl = self.plan(patient_id)
        return {"diet": DIETS[pl["prefs"]["diet"]], "activity_gate": pl["activity"]["gate"], "activity_level": pl["activity"]["level_label"],
                "goals": pl["goals"], "fluid_note": pl["fluid_note"], "todays_meals": [m["name"] for m in pl["meals"]],
                "limitations": [LIMITATIONS[x] for x in pl["prefs"]["limitations"]]}
