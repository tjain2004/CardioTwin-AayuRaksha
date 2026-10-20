# AI health assistant ("Ask CardioTwin"): general heart-health and wellness questions, plain-language explanations of the
# risk engine's existing output, and conversation history.
#
# Safety design:
# * Emergency triage runs first, on every message, with fixed rules (no AI). Urgent symptoms get the emergency card at
#   once (call 112 through the phone dialler, one-tap symptom check-in into the risk engine's own emergency rules, nearby
#   hospitals) and the AI model is not called at all, so the instructions never wait on, or depend on, a model reply.
# * The assistant only explains what the risk engine already produced (tier, reasons, data freshness). It never
#   computes a score, never shows a patient a score number (risk_policy: patients see words only), never diagnoses and
#   never changes medicines.
# * The patient's own data reaches the AI only after the patient (or their caregiver) switches on "Use my health data".
#   Names, ids and dates of birth are never sent. Message text is stored in the CardioTwin database and never logged.
# * With no ANTHROPIC_API_KEY (or CARDIOTWIN_CHAT_PROVIDER=offline) a rule-based assistant answers from the same
#   context and a small curated knowledge base, and every such reply is labelled OFFLINE.
from __future__ import annotations

import logging
import os
import re
import threading
import time
import uuid
from collections import defaultdict, deque
from typing import Any, Callable

from .auth import AuthError, Principal
from .db import Database, dumps, loads
from .readings import iso, utcnow

log = logging.getLogger("cardiotwin.chat")
LANGS = ("en", "hi")                    # assistant languages; the app's other languages fall back to English

SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_conversations (
  conversation_id TEXT PRIMARY KEY, patient_id TEXT NOT NULL, user_id TEXT NOT NULL, title TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_chatconv_pt ON chat_conversations (patient_id, user_id, updated_at);
CREATE TABLE IF NOT EXISTS chat_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
  created_at TEXT NOT NULL, source TEXT, used_health_data INTEGER NOT NULL DEFAULT 0, emergency_json TEXT, meta_json TEXT);
CREATE INDEX IF NOT EXISTS ix_chatmsg_conv ON chat_messages (conversation_id, id);
CREATE TABLE IF NOT EXISTS chat_consent (
  patient_id TEXT PRIMARY KEY, share_health_data INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL, updated_by TEXT);
"""

MAX_MESSAGE_CHARS = 2000
HISTORY_TURNS = 20
DISCLAIMER = ("CardioTwin's assistant gives general information, not a diagnosis. It cannot see you, examine you or change "
              "your medicines. If you feel unwell, contact your doctor; in an emergency call 112.")
CONSENT_TEXT = ("When this is on, the assistant may use your latest heart check (risk level in words and the reasons the model "
                "gave), recent watch readings, conditions and tests on your record, your medicine names and your wellness plan to "
                "answer you. With the AI model switched on, this summary is sent to the AI service (Anthropic) for each answer; your "
                "name and patient number are not sent. You can switch it off at any time.")
CALL_ACTION = {"type": "open_dialer", "number": "112", "uri": "tel:112", "label": "Call 112", "auto_dial": False,
               "requires_user_confirmation": True, "dispatch_status": "NOT_DISPATCHED",
               "confirm_text": "This opens your phone's dialler with 112 filled in. You press Call yourself. CardioTwin does not "
                               "contact emergency services and cannot send an ambulance."}

# ---------------------------------------------------------------------------------------------------- triage (no AI)
# Phrases mapped to the symptom keys of the project's own rule (twin_rules.SYMPTOMS), so a one-tap check-in sends exactly
# what the risk engine's emergency rule EMG-SYM-01/02 understands. English plus common Hindi / Hinglish wording.
SYMPTOM_PATTERNS = {
    "chest_pain": r"chest (pain|pressure|tightness|discomfort|heaviness|hurts?|is tight)|pain in (my )?chest|tight(ness)? in (my )?chest|"
                  r"crushing|heart attack|angina|seene (me|mein|mai) (dard|bhaari)|chhati (me|mein) dard|सीने में दर्द|छाती में दर्द",
    "pain_spreads_arm_jaw_back": r"pain (in|to|down|into|spreading (to|into|down))( my)?( left| right)? (arm|jaw|back|shoulder|neck)|"
                                 r"(arm|jaw|shoulder) (pain|hurts|is hurting)|(left|right) arm (is )?(numb|heavy)",
    "breathless": r"can'?t breathe|cannot breathe|unable to breathe|short(ness)? of breath|breathless|difficulty breathing|"
                  r"trouble breathing|hard to breathe|gasping|out of breath|saans (nahi|lene me|phool)|सांस",
    "cold_sweat": r"cold sweat|sweating (a lot|heavily|profusely)|clammy|pasina",
    "nausea": r"nause|vomit|throwing up|ulti|matli",
    "dizzy_or_fainted": r"faint(ed|ing)?|passed out|blacked? out|lost consciousness|collapsed?|dizzy|light-?headed|chakkar|behosh",
}
CRITICAL_PATTERNS = {
    "cardiac": r"not breathing|stopped breathing|no pulse|unresponsive|cardiac arrest|heart (has )?stopped|not waking up|turning blue",
    "stroke": r"face (is )?droop|drooping face|slurred speech|can'?t (speak|talk) properly|(one side|arm|leg) (is )?(weak|numb|paralys)|"
              r"sudden (weakness|numbness)|stroke",
    "self_harm": r"kill myself|suicid|end my life|want to die|self[- ]harm|hurt myself",
}
NEGATION = re.compile(r"\b(no|not|never|without|don'?t|doesn'?t|didn'?t|isn'?t|nahi|nahin)\b[\w\s']{0,20}$")
INFO_QUESTION = re.compile(r"\b(what|which|how|why|when) (are|is|do|does|can|should)\b.*\b(symptoms?|signs?|warning|causes?|mean)\b|"
                           r"\b(symptoms?|signs?) of\b|\bwhat to do (if|when)\b")
FIRST_PERSON = re.compile(r"\b(i|i'm|im|i've|my|me|mujhe|mera|meri|mere)\b")


def _hits(pattern: str, text: str) -> list[str]:
    out = []
    for m in re.finditer(pattern, text):
        if not NEGATION.search(text[:m.start()][-40:]):
            out.append(m.group(0))
    return out


def triage(message: str) -> dict:
    """Fixed-rule emergency screen of one message. level: none | possible | urgent. Over-triggers on purpose."""
    t = " " + re.sub(r"\s+", " ", (message or "").lower()) + " "
    symptoms, matched = {}, []
    for k, p in SYMPTOM_PATTERNS.items():
        h = _hits(p, t)
        if h:
            symptoms[k] = True
            matched += h
    critical = {k: _hits(p, t) for k, p in CRITICAL_PATTERNS.items()}
    critical = {k: v for k, v in critical.items() if v}
    try:
        import twin_rules                                  # the project's symptom rule, the same one the engine uses
        sc = twin_rules.symptom_check(symptoms)
    except ImportError:                                    # pragma: no cover - the engine always provides it
        score = 3 * symptoms.get("chest_pain", 0) + sum(2 for k in ("pain_spreads_arm_jaw_back", "breathless", "cold_sweat", "dizzy_or_fainted") if symptoms.get(k)) + symptoms.get("nausea", 0)
        sc = {"score": score, "tier": "red" if score >= 6 else "amber" if score >= 3 else "green"}
    informational = bool(INFO_QUESTION.search(t)) and not FIRST_PERSON.search(t)
    if critical:
        level = "possible" if informational and "self_harm" not in critical else "urgent"
        kind = "self_harm" if "self_harm" in critical else "stroke" if "stroke" in critical else "cardiac"
    elif sc["tier"] == "red" or symptoms.get("chest_pain"):
        level, kind = ("possible" if informational else "urgent"), "cardiac"
    elif symptoms:
        level, kind = "possible", "cardiac"
    else:
        return {"level": "none"}
    matched += [x for v in critical.values() for x in v]
    return {"level": level, "kind": kind, "symptoms": symptoms, "symptom_score": sc["score"], "symptom_tier": sc["tier"],
            "matched": sorted(set(m.strip() for m in matched)), "informational": informational}


HI_CARD = {
    "self_harm": ("आप अकेले नहीं हैं",
                  "अगर आपको लगता है कि आप खुद को नुकसान पहुँचा सकते हैं, तो अभी 112 पर कॉल करें। आप Tele-MANAS 14416 (मुफ़्त, 24 घंटे) पर भी प्रशिक्षित परामर्शदाता से बात कर सकते हैं।"),
    "stroke": ("ये स्ट्रोक (लकवा) के लक्षण हो सकते हैं",
               "अचानक चेहरा टेढ़ा होना, हाथ में कमज़ोरी या बोलने में दिक्कत के लिए तुरंत इलाज ज़रूरी है। अभी 112 पर कॉल करें और लक्षण शुरू होने का समय नोट करें।"),
    "urgent": ("ये लक्षण आपातकाल हो सकते हैं",
               "सीने में दर्द के साथ साँस फूलना, पसीना, बेहोशी या बाँह, जबड़े या पीठ तक दर्द दिल का दौरा (heart attack) हो सकता है। "
               "अभी 112 पर कॉल करें। कोई भी काम रोकें और बैठ या लेट जाएँ। खुद गाड़ी न चलाएँ।"),
    "possible": ("अगर आपको अभी ये लक्षण हैं",
                 "अगर आपको अभी सीने में दर्द, साँस लेने में दिक्कत या चक्कर आ रहे हैं, तो 112 पर कॉल करें। अपनी केयर टीम और CardioTwin "
                 "की आपातकालीन जाँच तक लक्षण पहुँचाने के लिए उन्हें चेक-इन के रूप में भेजें।"),
}
HI_NOTE = "यह आपके संदेश के शब्दों से तय नियमों द्वारा पहचाना गया है, AI द्वारा नहीं। यह निदान (diagnosis) नहीं है।"


def emergency_card(tri: dict, language: str = "en") -> dict:
    """The deterministic card the app shows; the same wording whether or not the AI answers."""
    card = _emergency_card_en(tri)
    if language == "hi":
        key = tri["kind"] if tri["kind"] in ("self_harm", "stroke") else ("urgent" if tri["level"] == "urgent" else "possible")
        card["title"], card["message"] = HI_CARD[key]
        if "note" in card:
            card["note"] = HI_NOTE
        card["call_action"] = {**card["call_action"], "label": "112 पर कॉल करें"}
        card["other_numbers"] = [{**n, "label": "एम्बुलेंस 108" if n["number"] == "108" else "Tele-MANAS 14416 (24 घंटे)"} for n in card["other_numbers"]]
        card["language"] = "hi"
    return card


def _emergency_card_en(tri: dict) -> dict:
    kind, urgent = tri["kind"], tri["level"] == "urgent"
    if kind == "self_harm":
        return {"level": tri["level"], "kind": kind, "title": "You don't have to face this alone",
                "message": "If you might act on these thoughts, call 112 now. You can also call Tele-MANAS on 14416 (free, 24 hours) "
                           "to talk to a trained counsellor.",
                "call_action": CALL_ACTION, "other_numbers": [{"label": "Tele-MANAS (mental health, 24 h)", "number": "14416", "uri": "tel:14416"}],
                "checkin_symptoms": None, "actions": ["call_112", "call_other", "contacts"]}
    title = ("These could be signs of a stroke" if kind == "stroke" else "These symptoms can be an emergency") if urgent else \
        "If you have these symptoms now"
    if kind == "stroke":
        msg = "Sudden face drooping, arm weakness or slurred speech needs emergency care. Call 112 now and note the time it started."
    elif urgent:
        msg = ("Chest pain with breathlessness, sweating, fainting or pain spreading to the arm, jaw or back can be a heart attack. "
               "Call 112 now. Stop any activity and sit or lie down. Do not drive yourself.")
    else:
        msg = ("If you have chest pain, trouble breathing or feel faint right now, call 112. To let your care team and the "
               "CardioTwin emergency check see your symptoms, send them as a check-in.")
    card = {"level": tri["level"], "kind": kind, "title": title, "message": msg, "call_action": CALL_ACTION,
            "other_numbers": [{"label": "Ambulance (108, most states)", "number": "108", "uri": "tel:108"}],
            "checkin_symptoms": tri.get("symptoms") or None, "matched": tri.get("matched", []),
            "actions": ["call_112", "send_checkin", "nearby_help", "contacts"],
            "note": "Detected from the words in your message by fixed rules, not by AI. It is not a diagnosis."}
    return card


# ---------------------------------------------------------------------------------------------------- knowledge base
# Short, sourced statements the assistant may use. Kept deliberately small and general.
KNOWLEDGE = [
    ("salt", "Adults should eat less than 5 g of salt a day (about one teaspoon), counting salt already in food (WHO). Pickles, papad, namkeen, packet soups and instant noodles are major hidden sources."),
    ("activity", "Adults are advised 150 to 300 minutes of moderate activity a week, such as brisk walking (WHO 2020). People with a heart condition should agree their level with their doctor or a cardiac rehabilitation team and build up slowly."),
    ("sleep", "Adults generally need 7 to 9 hours of sleep a night (American Heart Association, Life's Essential 8). Loud snoring with pauses in breathing should be checked by a doctor."),
    ("diet", "A heart-healthy plate is about half vegetables and fruit, a quarter whole grains or millets, and a quarter protein such as dal, legumes, eggs, fish or lean meat, cooked with little oil and salt (ICMR-NIN Dietary Guidelines for Indians, 2024)."),
    ("fats", "Limit fried foods, vanaspati and other trans fats, and reduce ghee, butter and coconut oil; small amounts of vegetable oils, nuts and seeds are better choices."),
    ("smoking", "Stopping smoking sharply lowers heart risk within a year. India's national quitline is 1800-11-2356."),
    ("alcohol", "No amount of alcohol is good for the heart; it can also trigger an irregular heartbeat (atrial fibrillation)."),
    ("hydration", "Most adults do well with about 6 to 8 glasses of fluid a day, more in hot weather. People told to limit fluids (for example with heart failure or kidney disease) must follow their doctor's limit instead."),
    ("warning_signs", "Warning signs of a heart attack include chest pain or pressure, pain spreading to the arm, jaw or back, breathlessness, cold sweat, nausea and light-headedness. Call 112 straight away."),
    ("stroke", "Signs of a stroke: Face drooping, Arm weakness, Speech difficulty: Time to call 112 (FAST)."),
    ("cad", "Coronary artery disease (CAD) means the arteries that supply the heart muscle are narrowed, usually by plaque (atherosclerosis). It can cause chest pain on exertion (angina) or a heart attack if a plaque ruptures and a clot blocks the artery. Only a doctor can diagnose it, using tests (AHA; ESC 2024 chronic coronary syndrome guideline)."),
    ("atherosclerosis", "Atherosclerosis is the slow build-up of plaque (cholesterol, fat, calcium and inflammatory cells) in artery walls. High LDL cholesterol, high blood pressure, diabetes, smoking and inflammation damage the artery lining and speed it up (AHA)."),
    ("cholesterol", "LDL is the 'bad' cholesterol that builds plaque; HDL helps remove it. Targets depend on a person's overall risk and are set by their doctor (Lipid Association of India; ESC/EAS). Less saturated and trans fat, more fibre and regular activity help; some people also need medicine."),
    ("blood_pressure", "High blood pressure strains the arteries and the heart. 130/80 mmHg or above is raised (ACC/AHA 2017); lowering it with less salt, activity, weight loss and medicine when prescribed reduces heart attacks and strokes."),
    ("diabetes", "Diabetes roughly doubles the risk of heart disease. HbA1c 5.7 to 6.4% is the prediabetes range and 6.5% or more is the diabetes range (ADA). Good sugar, blood pressure and cholesterol control together protect the heart."),
    ("risk_factors", "Risk factors for CAD that can be changed: smoking, high blood pressure, high LDL cholesterol, diabetes, excess weight (for Indian adults, BMI 23 or more is above the healthy range; waist above 90 cm for men or 80 cm for women), inactivity, unhealthy diet, stress and poor sleep. Ones that cannot be changed: age, male sex, family history of early heart disease, and South Asian ancestry, which carries higher risk at younger ages."),
    ("tests", "Common heart tests a doctor may order: ECG, blood tests (lipid profile, HbA1c, kidney function), echocardiogram (ultrasound of the heart), treadmill stress test (TMT), CT coronary angiography or calcium score, and coronary angiography (a dye test through a catheter). Which ones are needed depends on the person; only a doctor can decide."),
    ("treatment", "CAD treatment can include lifestyle changes, medicines (for example to lower cholesterol, blood pressure or the chance of clots), and procedures such as angioplasty with a stent or bypass surgery. Decisions are made with a cardiologist; never start or stop a medicine on your own."),
]

SYSTEM_PROMPT = """You are "Ask CardioTwin", the health assistant inside CardioTwin, a research-prototype heart monitoring app used in India. You talk with a patient, their caregiver, or their doctor.

What you do:
- Answer general questions about heart health, nutrition (including Indian vegetarian and non-vegetarian food), exercise, hydration, sleep, stress and healthy habits, in plain, warm, short language.
- Explain the person's CardioTwin results in understandable words, using ONLY the data in the <cardiotwin_context> block. That block comes from CardioTwin's risk engine and records.
- Keep track of the conversation and answer follow-up questions.

Rules you always follow:
- Never invent facts, numbers, readings, test results, risk scores or reasons. If something is not in the context, say you don't have it. If health data sharing is off, say you can only answer generally and that they can switch on "Use my health data".
- The risk level comes only from the CardioTwin risk engine. Never compute, estimate, adjust or second-guess a score or a risk level, and never say a result is wrong. If the context says the check could not run or the data is stale, say so plainly; never present that as a low or reassuring result.
- If the context says shows_score_number is false, never give any score, probability, percentage or model number for the risk. Use the risk level words only.
- Do not diagnose. Do not say whether someone is or is not having a heart attack, cardiac arrest, stroke or arrhythmia. A watch reading or a model result is not a diagnosis.
- Never start, stop, skip, change or double any medicine or dose, and never suggest a dose. Tell them to ask their doctor or pharmacist.
- Recommend a doctor visit when a question needs individual medical advice, when symptoms are new or worsening, or when the person has a condition that changes what is safe (for example fluid limits or exercise limits).
- If the person describes symptoms that could be an emergency (chest pain or pressure, trouble breathing, fainting, signs of a stroke, thoughts of self-harm), tell them first to call 112 now, before anything else. The app has already shown them an emergency card.
- Respect restrictions in the context (clinician restrictions, paused activity). Never suggest exercise when activity is paused.
- Use the reference facts below when they help, and say where a fact comes from when you use one.
- CardioTwin's model is a 24-hour deterioration check (the chance of a sudden worsening needing urgent hospital treatment in the next 24 hours). It is NOT a coronary artery disease (CAD) model and gives no CAD score. When people ask about their CAD or heart disease risk, say this clearly, then use the risk-factor profile in the context (heart_health) and any ASCVD band from their record. Always keep a prediction or risk estimate separate from a diagnosis: only a doctor can diagnose CAD, using tests.
- When you explain why a result changed, use only the reasons, trends and missing or stale data listed in the context. If the context does not show why, say you cannot tell.
- Keep answers short: usually 2 to 6 sentences or a few bullet points. No tables. Answer in the language the person writes in, or the language the app asks for below.

Reference facts:
{knowledge}
"""


# ---------------------------------------------------------------------------------------------------- LLM client
class AnthropicLLM:
    """Claude through the official Anthropic SDK. The key is read from the server environment only."""

    def __init__(self, model: str, effort: str, timeout_s: float):
        import anthropic
        self.anthropic = anthropic
        self.model, self.effort = model, effort
        self.client = anthropic.Anthropic(timeout=timeout_s, max_retries=1)

    def __call__(self, system: list[dict], messages: list[dict]) -> dict:
        a = self.anthropic
        try:
            r = self.client.beta.messages.create(
                model=self.model, max_tokens=4000, system=system, messages=messages,
                output_config={"effort": self.effort},
                betas=["server-side-fallback-2026-07-01"], fallbacks="default")   # a policy decline re-runs on Anthropic's recommended model
        except a.APITimeoutError:
            return {"error": "timeout"}
        except a.RateLimitError:
            return {"error": "rate_limited"}
        except a.AuthenticationError:
            return {"error": "bad_api_key"}
        except a.APIStatusError as e:
            return {"error": f"api_{e.status_code}"}
        except a.APIConnectionError:
            return {"error": "network"}
        if r.stop_reason == "refusal":
            return {"error": "refusal"}
        text = "".join(b.text for b in r.content if b.type == "text").strip()
        return {"text": text, "model": r.model, "stop_reason": r.stop_reason} if text else {"error": "empty"}


# ---------------------------------------------------------------------------------------------------- the assistant
class HealthChat:
    def __init__(self, db: Database, svc, settings, llm: Callable | None = None):
        self.db, self.svc, self.settings = db, svc, settings
        db.ensure_schema(SCHEMA)
        self.llm = llm
        self.llm_error: str | None = None
        if llm is None and self.provider() == "anthropic":
            try:
                self.llm = AnthropicLLM(settings.chat_model, settings.chat_effort, settings.chat_timeout_s)
            except Exception as e:                    # SDK missing or misconfigured: stay usable, offline
                self.llm_error = f"{type(e).__name__}: {e}"[:200]
        self._rate: dict[str, deque] = defaultdict(deque)
        self._rate_lock = threading.Lock()

    def provider(self) -> str:
        p = (self.settings.chat_provider or "auto").lower()
        if p == "offline":
            return "offline"
        if p == "anthropic":
            return "anthropic"
        has_key = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
        return "anthropic" if has_key else "offline"

    def status(self) -> dict:
        live = self.llm is not None
        return {"mode": "ai" if live else "offline", "model": self.settings.chat_model if live and isinstance(self.llm, AnthropicLLM) else None,
                "label": None if live else "OFFLINE",
                "note": None if live else ("The AI model is not configured on the server, so a simpler offline assistant answers. "
                                           "It covers common questions only."),
                "disclaimer": DISCLAIMER, "consent_text": CONSENT_TEXT, "max_message_chars": MAX_MESSAGE_CHARS}

    # ------------------------------------------------------------------ consent
    def consent(self, patient_id: str) -> dict:
        r = self.db.one("SELECT * FROM chat_consent WHERE patient_id=?", (patient_id,))
        return {"patient_id": patient_id, "share_health_data": bool(r and r["share_health_data"]),
                "updated_at": r["updated_at"] if r else None, "text": CONSENT_TEXT}

    def set_consent(self, p: Principal, patient_id: str, share: bool) -> dict:
        self.svc.auth.require_access(p, patient_id)
        if p.kind != "user" or p.role not in ("patient", "caregiver"):
            raise AuthError("Only the patient or their caregiver can change what the assistant may use.")
        self.db.execute("INSERT OR REPLACE INTO chat_consent VALUES (?,?,?,?)", (patient_id, int(bool(share)), iso(utcnow()), p.id))
        return self.consent(patient_id)

    # ------------------------------------------------------------------ conversations
    def _conv(self, p: Principal, conversation_id: str) -> dict:
        c = self.db.one("SELECT * FROM chat_conversations WHERE conversation_id=? AND deleted=0", (conversation_id,))
        if not c or c["user_id"] != p.id:
            raise KeyError(conversation_id)
        self.svc.auth.require_access(p, c["patient_id"])
        return c

    def conversations(self, p: Principal, patient_id: str) -> list[dict]:
        self.svc.auth.require_access(p, patient_id)
        return self.db.all("SELECT c.conversation_id, c.title, c.created_at, c.updated_at, "
                           "(SELECT COUNT(*) FROM chat_messages m WHERE m.conversation_id=c.conversation_id) AS n_messages "
                           "FROM chat_conversations c WHERE c.patient_id=? AND c.user_id=? AND c.deleted=0 ORDER BY c.updated_at DESC LIMIT 50",
                           (patient_id, p.id))

    def messages(self, p: Principal, conversation_id: str) -> dict:
        c = self._conv(p, conversation_id)
        rows = self.db.all("SELECT * FROM chat_messages WHERE conversation_id=? ORDER BY id", (conversation_id,))
        return {**{k: c[k] for k in ("conversation_id", "patient_id", "title", "created_at", "updated_at")},
                "messages": [self._msg(r) for r in rows]}

    def delete(self, p: Principal, conversation_id: str) -> dict:
        self._conv(p, conversation_id)
        self.db.execute("DELETE FROM chat_messages WHERE conversation_id=?", (conversation_id,))
        self.db.execute("UPDATE chat_conversations SET deleted=1, title=NULL WHERE conversation_id=?", (conversation_id,))
        return {"ok": True}

    @staticmethod
    def _msg(r: dict) -> dict:
        return {"id": r["id"], "role": r["role"], "content": r["content"], "created_at": r["created_at"], "source": r["source"],
                "used_health_data": bool(r["used_health_data"]), "emergency": loads(r["emergency_json"], None),
                "meta": loads(r["meta_json"], None)}

    def _add(self, cid: str, role: str, content: str, source: str | None = None, used: bool = False,
             emergency: dict | None = None, meta: dict | None = None) -> dict:
        now = iso(utcnow())
        cur = self.db.execute("INSERT INTO chat_messages (conversation_id, role, content, created_at, source, used_health_data, "
                              "emergency_json, meta_json) VALUES (?,?,?,?,?,?,?,?)",
                              (cid, role, content, now, source, int(used), dumps(emergency) if emergency else None, dumps(meta) if meta else None))
        self.db.execute("UPDATE chat_conversations SET updated_at=? WHERE conversation_id=?", (now, cid))
        return self._msg(self.db.one("SELECT * FROM chat_messages WHERE id=?", (cur.lastrowid,)))

    def _check_rate(self, user_id: str) -> None:
        now = time.monotonic()
        with self._rate_lock:
            q = self._rate[user_id]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= self.settings.chat_rate_per_minute:
                raise AuthError("Too many messages in a minute. Please wait a moment.", 429)
            q.append(now)

    # ------------------------------------------------------------------ context from the existing engine and records
    def context(self, p: Principal, patient_id: str) -> dict | None:
        """The authorised, de-identified summary the assistant may use, or None when sharing is off. Everything comes from
        the risk engine's stored result and the existing records; nothing is computed here."""
        if not self.consent(patient_id)["share_health_data"]:
            return None
        svc = self.svc
        technical = svc.auth.can(p, "view_technical")
        st = svc.latest_status(patient_id)
        ctx: dict[str, Any] = {"viewer": "clinician" if technical else p.role, "shows_score_number": technical}
        if st is None:
            ctx["heart_check"] = {"status": "NO_EVALUATION_YET", "meaning": "No heart check has run yet (no watch readings)."}
        else:
            res = st.get("result") or {}
            pv = (st.get("views") or {}).get("phone_patient") or {}
            hc: dict[str, Any] = {"status": st["status"], "risk_level": pv.get("tier") if st["status"] == "OK" else None,
                                  "screen": pv.get("screen"), "message_shown_to_patient": pv.get("message"),
                                  "emergency_state": st.get("emergency_state"), "checked_at": st.get("evaluated_at"),
                                  "horizon": (res.get("model") or {}).get("horizon"),
                                  "simulated_data": bool(st.get("simulated")),
                                  "data_freshness": {k: v.get("status") for k, v in (pv.get("freshness_badges") or {}).items()},
                                  "status_reason": res.get("status_reason")}
            em = res.get("emergency") or {}
            if em.get("state") and em["state"] != "NOT_TRIGGERED":
                hc["emergency_reason"] = em.get("reason")
                hc["emergency_guidance"] = em.get("guidance")
            ex = res.get("explanation") or {}
            if ex.get("status") == "AVAILABLE":
                hc["reasons"] = [{"factor": c.get("reason"), "effect": c.get("direction"),
                                  "details": [i.get("text") for i in (c.get("inputs") or []) if i.get("text")][:3]}
                                 for c in (ex.get("top_contributors") or [])[:5]]
                hc["reasons_meaning"] = ("The model's own top reasons for this result (SHAP explanation): 'raises' pushed the risk "
                                         "level up, 'lowers' pushed it down. They describe the model, not a diagnosis.")
            else:
                hc["reasons"] = None
                hc["reasons_unavailable"] = ex.get("reason") or "No explanation available for this check."
            if technical:
                risk = res.get("risk") or {}
                hc.update(score=st.get("score"), score_scale=risk.get("score_scale"), thresholds=risk.get("thresholds"),
                          tier_reason=risk.get("tier_reason"), uncertainty_note=(risk.get("uncertainty") or {}).get("note"),
                          sar_one_line=(res.get("sar") or {}).get("one_line"), policy=risk.get("policy_version"))
            ctx["heart_check"] = hc
        try:
            feats = svc.build_request(patient_id)[1]["signals"]
            ctx["recent_watch_readings"] = {
                name: {"latest": s["last"]["value"], "minutes_old": s["last"]["minutes_old"],
                       "usual": (s.get("baseline") or {}).get("median"), "baseline_status": (s.get("baseline") or {}).get("status")}
                for name, s in feats.items() if isinstance(s, dict) and s.get("last")}
            ctx["steps_last_24h"] = (feats.get("steps") or {}).get("24h")
        except Exception:
            ctx["recent_watch_readings"] = None
        hp = svc.app.health_profile(svc.ehr(patient_id))
        ctx["record"] = {"measures": {m["label"]: m["value"] for m in hp["measures"]},
                         "conditions": [f"{c['name']} ({c['note']})" for c in hp["conditions"]],
                         "tests": [f"{l['name']}: {l['value']} {l['unit'] or ''}".strip() for l in hp["labs"]],
                         "sample_record": hp["is_demo"]}
        ctx["medicines"] = [f"{m['name']} {m.get('strength') or ''}".strip() for m in svc.app.medications(patient_id)]
        ctx["open_alerts"] = [{"title": a["title"], "severity": a["severity"], "state": a["state"]}
                              for a in svc.alerts.list(patient_id, open_only=True)][:5]
        try:
            hs = svc.heart.summary(p, patient_id)["sections"]
            ctx["heart_health"] = {"meaning": "CardioTwin's deterministic risk-factor profile from the record and logs (not a CAD score)",
                                   "positive_trends": hs["positive_trends"], "areas_to_watch": hs["areas_to_watch"],
                                   "missing_information": hs["missing_information"], "lifestyle_focus": hs["lifestyle_focus"]}
        except Exception:
            ctx["heart_health"] = None
        try:
            ctx["wellness"] = svc.wellness.summary_for_chat(patient_id)
        except Exception:
            ctx["wellness"] = None
        return ctx

    # ------------------------------------------------------------------ one turn
    def language(self, patient_id: str, language: str | None) -> str:
        if language in LANGS:
            return language
        try:
            saved = self.svc.app.settings(patient_id)["language"]
        except Exception:
            saved = "en"
        return saved if saved in LANGS else "en"

    def send(self, p: Principal, patient_id: str, message: str, conversation_id: str | None = None,
             language: str | None = None) -> dict:
        """One text turn. This is the single entry point, so a future voice module only needs speech-to-text in front."""
        self.svc.auth.require_access(p, patient_id)
        if p.kind != "user":
            raise AuthError("Devices cannot use the assistant.")
        text = (message or "").strip()
        if not text:
            raise ValueError("Type a question first.")
        if len(text) > MAX_MESSAGE_CHARS:
            raise ValueError(f"Please keep messages under {MAX_MESSAGE_CHARS} characters.")
        self._check_rate(p.id)
        if conversation_id:
            conv = self._conv(p, conversation_id)
            if conv["patient_id"] != patient_id:
                raise ValueError("That conversation belongs to another patient.")
            cid = conversation_id
        else:
            cid, now = str(uuid.uuid4()), iso(utcnow())
            self.db.execute("INSERT INTO chat_conversations VALUES (?,?,?,?,?,?,0)", (cid, patient_id, p.id, text[:60], now, now))

        lang = self.language(patient_id, language)
        tri = triage(text)
        card = emergency_card(tri, lang) if tri["level"] != "none" else None
        history = self.db.all("SELECT role, content FROM chat_messages WHERE conversation_id=? ORDER BY id DESC LIMIT ?",
                              (cid, HISTORY_TURNS * 2))[::-1]
        user_msg = self._add(cid, "user", text, emergency=card)
        started = time.monotonic()
        ctx = None
        if tri["level"] == "urgent":
            # Fixed reply; the AI model is not called, so nothing here can be delayed or contradicted by a model.
            answer, source, meta = card["message"], "safety_rules", {"triage": tri["level"], "language": lang}
        else:
            ctx = self.context(p, patient_id)
            answer, source, meta = None, "offline", {}
            if self.llm is not None:
                out = self.llm(self._system(ctx, lang), self._history(history, text, card))
                if out.get("text"):
                    answer, source, meta = out["text"], "ai", {"model": out.get("model"), "language": lang}
                else:
                    meta = {"ai_error": out.get("error")}
            if answer is None:
                answer = offline_answer(text, ctx, p.role)
                if meta.get("ai_error"):
                    answer = "The AI assistant is unavailable right now, so here is the offline answer.\n\n" + answer
                if lang == "hi":
                    answer = "(ऑफ़लाइन सहायक अभी केवल अंग्रेज़ी में उत्तर देता है। AI सहायक चालू होने पर उत्तर हिन्दी में मिलेंगे।)\n\n" + answer
                meta["language"] = lang
        bot = self._add(cid, "assistant", answer, source=source, used=ctx is not None, emergency=card, meta=meta)
        log.info("chat conversation=%s source=%s triage=%s used_health_data=%s ms=%d", cid, source, tri["level"], ctx is not None,
                 int(1000 * (time.monotonic() - started)))           # never the message text
        return {"conversation_id": cid, "user_message": user_msg, "reply": bot, "emergency": card, "assistant": self.status(),
                "language": lang}

    def _system(self, ctx: dict | None, lang: str = "en") -> list[dict]:
        kb = "\n".join(f"- {t}" for _, t in KNOWLEDGE)
        stable = {"type": "text", "text": SYSTEM_PROMPT.format(knowledge=kb), "cache_control": {"type": "ephemeral"}}
        if ctx is None:
            data = "<cardiotwin_context>\nHealth data sharing is OFF. You have no information about this person. Answer generally only.\n</cardiotwin_context>"
        else:
            data = f"<cardiotwin_context>\n{dumps(ctx)}\n</cardiotwin_context>"
        if lang == "hi":
            data += ("\n<language>Answer in simple Hindi (Devanagari script). Keep medical terms understandable: give the common "
                     "Hindi word and the English term in brackets the first time, for example दिल का दौरा (heart attack), "
                     "कोलेस्ट्रॉल (cholesterol). Numbers stay as digits. Emergency number: 112.</language>")
        return [stable, {"type": "text", "text": data}]

    @staticmethod
    def _history(history: list[dict], text: str, card: dict | None) -> list[dict]:
        msgs = [{"role": r["role"], "content": r["content"]} for r in history if r["content"]]
        while msgs and msgs[0]["role"] != "user":
            msgs.pop(0)
        note = ("\n\n[CardioTwin app note: the emergency card was shown for this message: " + card["title"] + "]") if card else ""
        msgs.append({"role": "user", "content": text + note})
        return msgs


# ---------------------------------------------------------------------------------------------------- offline answers
def _has(t: str, *words: str) -> bool:
    return any(re.search(rf"\b{w}", t) for w in words)


def offline_answer(text: str, ctx: dict | None, role: str) -> str:
    """Rule-based replies from the same context and knowledge base. Used when no AI model is configured or it fails."""
    t = text.lower()
    kb = dict(KNOWLEDGE)
    tail = "\n\n(Offline assistant: general information, not medical advice.)"
    if _has(t, "dose", "dosage", "stop (taking|my)", "skip", "double", "increase", "reduce", "change my (medicine|tablet|dose)", "how much .* take"):
        return ("I can't change, stop or adjust any medicine or dose. Please ask your doctor or pharmacist before changing anything. "
                "You can see your medicine times and mark doses taken in the Medicines tab." + tail)
    if _has(t, "do i have", "diagnos", "am i having", "is it a heart attack", "is this (a )?heart"):
        return ("I can't diagnose. A watch reading or a CardioTwin result is not a diagnosis. If you have chest pain, trouble breathing "
                "or feel faint, call 112 now; otherwise, book a visit with your doctor." + tail)
    if _has(t, "risk factor", "cause(s)? of (cad|heart disease)", "what causes"):
        return kb["risk_factors"] + tail
    if _has(t, "atherosclero", "plaque", "blocked arter", "blockage"):
        return kb["atherosclerosis"] + tail
    if _has(t, "cad\\b", "coronary", "heart disease", "angina"):
        extra = " Note: CardioTwin's own result is a 24-hour deterioration check, not a CAD test or CAD score."
        return kb["cad"] + extra + tail
    if _has(t, "cholesterol", "ldl", "hdl", "lipid", "triglycer"):
        return kb["cholesterol"] + tail
    if _has(t, "blood pressure", "bp\\b", "hypertension"):
        return kb["blood_pressure"] + tail
    if _has(t, "diabet", "sugar", "hba1c", "glucose"):
        return kb["diabetes"] + tail
    if _has(t, "test", "ecg", "ekg", "echo", "tmt", "treadmill", "angiogra", "investigation"):
        return kb["tests"] + tail
    if _has(t, "treatment", "stent", "angioplasty", "bypass", "cabg"):
        return kb["treatment"] + tail
    if _has(t, "risk", "score", "level", "result", "why", "reason", "explain", "shap", "high", "moderate", "low", "status", "check"):
        return _explain(ctx, role) + tail
    if _has(t, "eat", "food", "diet", "meal", "breakfast", "lunch", "dinner", "snack", "nutrition", "veg", "salt", "oil", "khana"):
        s = kb["diet"] + " " + kb["salt"]
        w = (ctx or {}).get("wellness")
        if w:
            s += f" Today's suggestions in your Wellness plan ({w['diet'].lower()}): " + "; ".join(w["todays_meals"]) + "."
        return s + tail
    if _has(t, "exercise", "walk", "yoga", "activity", "gym", "workout", "run", "steps"):
        w = (ctx or {}).get("wellness")
        if w and w["activity_gate"]["state"] != "OK":
            return w["activity_gate"]["reason"] + tail
        return kb["activity"] + " Use the talk test: you should still be able to talk in full sentences. Stop if you get chest pain or feel faint." + tail
    if _has(t, "water", "drink", "hydrat", "fluid", "thirst"):
        w = (ctx or {}).get("wellness")
        return kb["hydration"] + (" " + w["fluid_note"] if w and w.get("fluid_note") else "") + tail
    if _has(t, "sleep", "insomnia", "tired", "nind", "neend"):
        return kb["sleep"] + " Keep regular sleep and wake times, and avoid screens and tea or coffee late in the day." + tail
    if _has(t, "smok", "cigarette", "bidi", "tobacco", "gutka"):
        return kb["smoking"] + tail
    if _has(t, "alcohol", "drink(ing)? beer", "wine", "whisky", "daru"):
        return kb["alcohol"] + tail
    if _has(t, "symptom", "sign", "warning", "attack", "stroke"):
        return kb["warning_signs"] + " " + kb["stroke"] + tail
    if _has(t, "hello", "hi", "namaste", "hey", "good (morning|evening)"):
        return ("Hello! I can explain your CardioTwin heart check, and answer general questions about food, activity, water, sleep "
                "and warning signs. What would you like to know?" + tail)
    return ("I'm the offline assistant, so I can only help with common topics: your heart check result, food and salt, activity, "
            "water, sleep, smoking, and warning signs. For anything about your own treatment, please ask your doctor." + tail)


def _explain(ctx: dict | None, role: str) -> str:
    if ctx is None:
        return ("CardioTwin shows a risk level in words (Low, Moderate or High) for the next 24 hours, made by its risk model from "
                "your watch readings and record. To let me explain your own result, switch on \"Use my health data\" above.")
    hc = ctx.get("heart_check") or {}
    if hc.get("status") != "OK":
        why = hc.get("status_reason") or hc.get("meaning") or "the check could not run"
        return f"Your latest heart check could not give a risk level: {why}. This is not a low result. Make sure the watch is snug and syncing."
    level = hc.get("risk_level") or "not shown"
    s = f"Your latest check shows {level.title()} for the next 24 hours. {hc.get('message_shown_to_patient') or ''}".strip()
    if ctx.get("shows_score_number") and hc.get("score") is not None:
        s += f" Model score {hc['score']:.3f} ({hc.get('score_scale')}); {hc.get('tier_reason') or ''}"
    if hc.get("reasons"):
        parts = []
        for r in hc["reasons"][:3]:
            d = f" ({r['details'][0]})" if r.get("details") else ""
            parts.append(f"{r['factor']} {r['effect']} it{d}")
        s += " The model's main reasons: " + "; ".join(parts) + ". These describe how the model reached the result, not a diagnosis."
    if hc.get("emergency_state") not in (None, "NOT_TRIGGERED"):
        s += " An emergency or re-measure rule is active: follow the instructions on your screen."
    if hc.get("simulated_data"):
        s += " Note: these readings come from the virtual watch (SIMULATED)."
    return s
