<p align="center">
  <img src="assets/images/hero.png" alt="CardioTwin: the problem it solves and how" width="100%">
</p>

<p align="center">
  <b>Team AyuRaksha</b> · Happiest Health Hackathon 2026 · Digital Twin Challenge<br>
  <sub>Research prototype · not a medical device · built on open, de-identified MIMIC-IV demo data</sub>
</p>

<p align="center">
  <a href="assets/architecture/cardiotwin_system_architecture.pdf">📐 System Architecture (PDF)</a> &nbsp;·&nbsp;
  <a href="assets/presentation/CardioTwin_AyuRaksha_Presentation.pdf">📊 Presentation (PDF)</a> &nbsp;·&nbsp;
  <a href="#-demo-video">🎬 Demo Video</a> &nbsp;·&nbsp;
  <a href="#-team-ayuraksha">👥 Team</a>
</p>

---

**Status labels used in this README:** 🟢 **Implemented** (in the code and shown in the prototype) · 🟡 **Proposed** (designed or coded, not yet verified) · 🔵 **Future Scope** (planned)

## 📌 Contents

1. [Problem](#-1-the-problem) · 2. [Solution](#-2-our-solution) · 3. [Three dashboards](#-3-three-dashboards-one-twin) · 4. [Features](#-4-key-features) · 5. [What makes it unique](#-5-what-makes-cardiotwin-unique) · 6. [Watch vision](#-6-our-unique-vision-a-watch-connected-digital-twin) · 7. [Use case](#-7-healthcare-use-case) · 8. [Architecture & AI/ML](#-8-architecture-and-aiml) · 9. [Tech stack](#-9-technical-stack) · 10. [Submission files](#-10-submission-files) · 11. [Run it](#-11-run-it-locally) · 12. [Limitations](#-12-responsible-ai-and-limitations) · 13. [Checklist](#-13-unstop-checklist) · 14. [License](#-14-license)

---

## 🩺 1. The problem

> **Problem statement:** To address the lack of early cardiac risk awareness, explainable predictions, personalized health guidance, and accessible emergency support.
>
> **Theme:** MedTech / BioTech / HealthTech · **Category:** Software

Cardiac deterioration usually shows up in the vital signs hours before a crisis, but those signals are hard to act on:

- **Scattered data.** The health record (age, diagnoses, labs, medicines) and the live vitals (heart rate, SpO₂, breathing) sit in separate systems.
- **Generic thresholds.** Early-warning scores such as NEWS2 apply the same cut-offs to everyone. In our data NEWS2 scored an AUPRC of just 0.074.
- **Black-box AI.** A risk number without reasons is hard for a doctor to trust.
- **Stale data.** A silent sensor can look like a healthy patient.
- **No "what-if" and little access.** Doctors cannot test scenarios, and patients have limited access to alerts once they leave the hospital.

CardioTwin brings **history and live physiology together** in one place. It **looks ahead**, and it **explains** the result to doctors and to patients.

## 💡 2. Our solution

**CardioTwin is a cardiac digital twin.** It builds a per-patient virtual model from two data streams, forecasts acute deterioration 24 hours ahead and explains why. It shows the result to the doctor, the patient and a watch screen.

| | What it does in the prototype | |
|---|---|---|
| 🔗 **Fuse** | Joins the static EHR (demographics, diagnoses, labs, ECG, medicines) with dynamic vitals (heart rate, SpO₂, breathing rate, BP, rhythm) | 🟢 |
| 🔮 **Predict** | Estimates the risk of vasopressor start, lactate ≥ 4 or in-hospital death within the next 24 hours, re-scored every hour | 🟢 |
| 🧾 **Explain** | Gives the top SHAP reasons and a doctor-ready SAR report (Situation, Assessment, Recommendation) | 🟢 |
| 🧪 **Simulate** | The what-if simulator shows how risk moves if a vital sign or risk factor changes | 🟢 |
| 🛡️ **Protect** | Freshness gates, emergency rules that run before the model, and a 112 prompt only for critical patients (the user presses Call) | 🟢 |
| 📲 **Deliver** | One backend serves the doctor dashboard, the patient app and the watch screens | 🟢 |

**How data flows:** PhysioNet data → clean and merge → 211 engineered features → train and calibrate → saved model. Live readings (replayed by a *virtual watch*) → validate → safety rules → risk engine → SHAP and SAR → dashboards over WebSocket.

## 🖥️ 3. Three dashboards, one twin

<p align="center">
  <img src="assets/images/three_views.png" alt="Doctor dashboard, patient view and watch readings" width="100%">
</p>

<p align="center"><sub>Real screenshots of the running prototype. The data is a replayed MIMIC-IV ICU test patient, labelled <b>SIMULATED</b> on screen.</sub></p>

<details>
<summary><b>See each screen full size</b></summary>

**🩺 Doctor dashboard:** live risk, model spread, data coverage, watch face and alerts
<img src="assets/screenshots/doctor_dashboard.png" alt="Doctor dashboard" width="100%">

**📱 Patient view:** home, medicines and nearby help
<p>
  <img src="assets/screenshots/phone_home.png" alt="Patient home" width="31%">
  <img src="assets/screenshots/phone_medicines.png" alt="Patient medicines" width="31%">
  <img src="assets/screenshots/phone_nearby_help.png" alt="Patient nearby help" width="31%">
</p>

**⌚ Watch readings:** watch screens in the browser, driven by the virtual watch (not a physical watch)
<img src="assets/screenshots/watch_browser_simulated.png" alt="Watch screens in the browser" width="85%">

</details>

## ✨ 4. Key features

| Feature | Status |
|---|:---:|
| **Data pipeline:** four PhysioNet sources become linked, cleaned CSVs (notebooks 01–02) | 🟢 |
| **Two AI models:** a full model (EHR + vitals, LightGBM) and a watch-only model (logistic regression) | 🟢 |
| **Risk tiers:** LOW (within baseline) · MODERATE (≥ 3.62%) · HIGH (≥ 7.59%), with hysteresis | 🟢 |
| **Explainable AI:** SHAP reasons, waterfalls and SAR doctor reports | 🟢 |
| **Risk engine:** a 10-step pipeline. 48 tests pass, and it gives the same tier as the trained model for 99.7% of test hours | 🟢 |
| **What-if simulator:** 21 levers, sensitivity curves and counterfactuals, labelled HYPOTHETICAL | 🟢 |
| **Safety layer:** 30 s fall check, confirmed-AF review, QTc ≥ 500 ms review, dose check, NEWS2 red bands | 🟢 |
| **Doctor dashboard · patient app · watch screens** (React) | 🟢 |
| **Virtual watch:** replays 19 held-out ICU stays, labelled SIMULATED | 🟢 |
| **Ask CardioTwin chatbot:** heart and CAD education, with emergency phrases in English, Hindi and Hinglish | 🟢 offline mode · 🟡 AI mode |
| **Heart-health (CAD) view and wellness coach:** risk factors, Indian meal ideas, activity goals | 🟢 |
| **Nearby hospitals and pharmacies** (Google Places) | 🟡 |
| **Ambulance tracking:** a labelled demo only, so nobody is dispatched | 🟢 demo · 🔵 real dispatch |
| **Wear OS watch app** (Kotlin, source code only, never built) | 🟡 |

## 🏆 5. What makes CardioTwin unique

- **A living twin, not a one-off score.** Each patient's state updates as readings arrive, and you can ask it "what if".
- **A watch-ready AI model.** A second model uses only watch-like signals. It flags the same 3 of 4 events as the full model.
- **Explanation first.** Every score comes with reasons, a SAR report and plain words for the patient.
- **Safety by design.** It returns `INSUFFICIENT_DATA` instead of a guess, emergency rules run before the model, and nothing is dispatched automatically.
- **Honest labels.** Simulated data, demo ambulances and offline answers are all marked on screen.
- **One backend, three views.** The doctor, the patient and the watch all read the same twin.

## ⌚ 6. Our unique vision: a watch-connected digital twin

> 🔵 **Future Scope.** Today CardioTwin does **not** connect to a physical smartwatch, receive live wearable data, monitor continuously or send real medical alerts.

The goal is a twin that stays current **between clinic visits**. A compatible smartwatch would send the signals it really supports, the backend would keep the person's twin up to date, and the doctor and the patient would see the same explained picture.

<p align="center">
  <img src="assets/images/future_vision.png" alt="Proposed Future Architecture — Not Yet Implemented" width="100%">
</p>

<p align="center"><sub>Conceptual diagram, not a screenshot: <b>Proposed Future Architecture — Not Yet Implemented</b>.</sub></p>

| Building block | Status |
|---|:---:|
| One reading format for every source, so a real watch would use the same endpoint | 🟢 |
| Virtual watch replaying ICU vitals (HRV, steps, sleep and QTc are synthetic) | 🟢 |
| Watch screens in the browser | 🟢 |
| Watch-only model, trained on ICU monitor signals and not yet validated on wrist data | 🟢 |
| Wear OS app with Health Services, fall heuristic and QR pairing: code exists, never built or run | 🟡 |
| Real watch sync, live monitoring, validated notifications | 🔵 |

**Envisioned steps (Future Scope):** 1) collect supported signals from a compatible wearable → 2) sync securely over Wi-Fi or through the phone → 3) update the twin with validated data → 4) analyse with a model validated on wearable data → 5) show trends on the dashboard and watch → 6) send notifications only after clinical validation.

**A watch cannot do everything.** Labs, lactate, a diagnostic 12-lead ECG and medication records still come from clinical systems, which is why the EHR stays half of the twin. Which signals are available depends on the device. SpO₂ and ECG usually need vendor SDKs (for example the Samsung Health Sensor SDK), so device support is a requirement for the future work.

**Before this is real:** build and test the Wear OS app on an emulator and then on real devices. Retrain and validate on consented wearable data, validate fall detection, and get clinical review of all rules. Add production security that complies with the DPDP Act.

## 🏥 7. Healthcare use case

A clinician is following a cardiac patient at risk of acute deterioration. CardioTwin warns up to 24 hours ahead, explains why and helps decide what to check next.

**Demo flow** 🟢 *(simulated data)*
1. The doctor signs in and picks a held-out test patient.
2. **Start virtual watch** replays that patient's recorded vitals as watch readings.
3. Risk, tier, SHAP reasons and the SAR report update live.
4. **Trigger AF** or **Trigger fall** shows the safety layer (AF review alert, 30-second "Are you OK?" check).
5. The patient app and the watch screens update. Only a critical patient sees the 112 prompt.
6. The what-if panel shows which changes would lower the risk most.

## 🧠 8. Architecture and AI/ML

### 📐 System architecture

<p align="center">
  <img src="assets/architecture/cardiotwin_system_architecture.png" alt="CardioTwin system architecture" width="100%">
</p>

<p align="center"><sub><a href="assets/architecture/cardiotwin_system_architecture.pdf">Download PDF</a> · The Wear OS box is the designed watch app (🟡 code only). The demo uses the reading simulator. The "Planned next" items are 🔵.</sub></p>

### 📂 Data (open and de-identified)

| Dataset | Used for |
|---|---|
| [MIMIC-IV Clinical Demo 2.2](https://physionet.org/content/mimic-iv-demo/2.2/) | EHR, labs, medicines, ICU vitals, outcomes (100 patients) |
| [MIMIC-IV-ECG Demo](https://physionet.org/content/mimic-iv-ecg-demo/0.1/) | ECG features such as QTc |
| [MIMIC-IV Waveform DB 0.1.0](https://physionet.org/content/mimic4wdb/0.1.0/) | Monitor numerics as a stand-in for wearable streams |
| [I-CARE 2.1](https://physionet.org/content/i-care/2.1/) | Cardiac-arrest cohort metadata (exploratory) |

Raw and patient-level data are **not** included in this repository (PhysioNet licence). `src/build_structured_csv.py` rebuilds them.

### 🔬 ML pipeline (notebooks 01–08)

**Structure → Merge and clean → Feature engineering (211 features) → EDA → Train (grouped 5-fold CV, Optuna) → SHAP and SAR → Risk engine → What-if simulator**

Models compared: NEWS2, logistic regression, random forest, XGBoost and LightGBM. The selected models are calibrated, given operating points, and saved with a SHA-256 manifest.

### 📈 Results on held-out patients

<sub>59 training patients, 15 held-out test patients (4 with an event). 95% bootstrap CIs. Source: `models/model_metadata.json`.</sub>

| Model | AUROC | AUPRC | Events flagged (HIGH) | Median lead time |
|---|:---:|:---:|:---:|:---:|
| NEWS2 baseline (CV) | 0.69 | 0.074 | – | – |
| **Full** · LightGBM, 40 features | **0.80** <sub>[0.70–0.92]</sub> | 0.19 | 3 / 4 | 23 h |
| **Watch** · logistic regression, 46 features | 0.73 <sub>[0.52–0.94]</sub> | 0.19 | 3 / 4 | 23 h |

With only 4 test events, these results show the method works end to end. They do **not** prove clinical performance.

<details>
<summary><b>See model, SHAP and what-if charts</b></summary>

<p>
  <img src="charts/model_charts/08_test_pr_roc_calibration.png" alt="Test PR, ROC and calibration" width="49%">
  <img src="charts/shap_charts/02_beeswarm_watch.png" alt="SHAP beeswarm, watch model" width="49%">
  <img src="charts/risk_engine/02_risk_trajectories.png" alt="Risk trajectories" width="49%">
  <img src="charts/what_if/07_tornado.png" alt="What-if tornado" width="49%">
</p>

</details>

## 🛠️ 9. Technical stack

| Layer | Tools |
|---|---|
| **Data & ML** | Python 3.13 · pandas · NumPy · scikit-learn · LightGBM · XGBoost · Optuna · SHAP · Jupyter |
| **Backend** | FastAPI · WebSockets · SQLite · Pydantic · pytest |
| **Front ends** | React · Vite · PWA (doctor dashboard, patient app, watch screens) |
| **External APIs** (optional keys) | Claude API (chatbot) · Google Places and Routes (maps) |
| **Watch app** 🟡 | Kotlin · Jetpack Compose for Wear OS · Health Services |

## 📦 10. Submission files

### 📐 System Architecture (SA)
➡️ [`assets/architecture/cardiotwin_system_architecture.pdf`](assets/architecture/cardiotwin_system_architecture.pdf)

### 📊 Presentation (PPT)
➡️ PDF: [`assets/presentation/CardioTwin_AyuRaksha_Presentation.pdf`](assets/presentation/CardioTwin_AyuRaksha_Presentation.pdf) · PowerPoint: [`.pptx`](assets/presentation/CardioTwin_AyuRaksha_Presentation.pptx)

<sub>The deck was made at the idea stage, so it lists the backend and apps as "planned". They have since been built, except the Wear OS app.</sub>

### 🎬 Demo Video
➡️ **YouTube (unlisted, 15–20 min):** `ADD_YOUTUBE_LINK_HERE`

### 👥 Team AyuRaksha

<table>
  <tr>
    <td align="center" width="25%">👑<br><b>ADD_NAME</b><br><sub>Team Lead · ML & Data</sub></td>
    <td align="center" width="25%">🧠<br><b>ADD_NAME</b><br><sub>AI/ML & Explainability</sub></td>
    <td align="center" width="25%">🖥️<br><b>ADD_NAME</b><br><sub>Backend & Apps</sub></td>
    <td align="center" width="25%">🎨<br><b>ADD_NAME</b><br><sub>UI/UX & Presentation</sub></td>
  </tr>
</table>

**🏫 College / Incubator:** `ADD_COLLEGE_OR_INCUBATOR_NAME`

## ▶️ 11. Run it locally

<details>
<summary><b>Show setup steps</b> (Windows PowerShell · Python 3.12/3.13 · Node.js LTS)</summary>

```powershell
# 1. Backend  → http://127.0.0.1:8000
cd apps\backend
py -3.13 -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m cardiotwin_backend --host 0.0.0.0

# 2. Doctor dashboard  → http://localhost:5173  (dr.demo / demo1234)
cd apps\doctor_dashboard; npm ci; npm run dev

# 3. Patient app  → http://localhost:5174  (p37093652 / demo1234)
cd apps\patient_phone_app; npm ci; npm run dev

# 4. Watch screens  → http://localhost:5175/?patient=stay-37093652  (dr.demo / demo1234)
cd apps\watch_ui_web; npm ci; npm run dev
```

In the doctor dashboard pick `stay-37093652`, press **Start virtual watch**, then **Trigger AF** or **Trigger fall**.

- **Replay data** is derived from MIMIC-IV and not pushed. After downloading the PhysioNet demo data, run `src/build_structured_csv.py`, notebooks 02–07 and `apps/backend/tools/build_replay_data.py`.
- **Optional keys** (server-side): `ANTHROPIC_API_KEY`, `CARDIOTWIN_GOOGLE_MAPS_SERVER_KEY`, `CARDIOTWIN_GOOGLE_MAPS_BROWSER_KEY`.
- **Tests:** `python -m pytest tests -q` inside `apps/backend`, `risk_engine` and `what_if_simulator`.

</details>

<details>
<summary><b>Repository structure</b></summary>

```
apps/               backend · doctor_dashboard · patient_phone_app · watch_ui_web · wear_os_watch
notebooks/          01–08: data → cleaning → features → EDA → training → SHAP → risk engine → what-if
src/                data building and preprocessing scripts
risk_engine/        inference module used by the backend
what_if_simulator/  what-if engine and tests
models/             trained models, calibration, schema, metadata
charts/             EDA, model, SHAP, risk-engine and what-if charts
assets/             README images, screenshots, architecture PDF, presentation
```

</details>

## ⚖️ 12. Responsible AI and limitations

- **Not a medical device.** This is a research prototype without clinical validation. Do not use it for diagnosis or treatment.
- **Small ICU cohort.** It was trained on 59 patients and tested on 15, so results won't transfer directly to outpatients or wearables.
- **It predicts deterioration, not CAD.** The CAD view is a risk-factor profile, not a diagnosis.
- **No real wearable data.** The "watch" readings are replayed ICU data, and some signals are synthetic.
- **Draft safety rules.** AF, QTc, dose and fall thresholds need clinical review.
- **Nothing is dispatched.** The app never contacts emergency services, and only the user can place a call.
- **The chatbot doesn't diagnose.** It explains existing results and never invents scores or changes medicines.
- **Privacy.** Only open, de-identified data is used, keys stay on the server and decisions are audit-logged. Production use would need encryption, consent management and DPDP Act compliance.

## ✅ 13. Unstop checklist

| Mandatory README item | Where | |
|---|---|:---:|
| Team details | [Team](#-team-ayuraksha) | ⬜ names |
| College / Incubator information | [Team](#-team-ayuraksha) | ⬜ |
| Project Title | CardioTwin | ✅ |
| Problem Statement | [§1](#-1-the-problem) | ✅ |
| Healthcare Use Case | [§7](#-7-healthcare-use-case) | ✅ |
| Technical Stack | [§9](#-9-technical-stack) | ✅ |
| AI/ML Model or Framework Details | [§8](#-8-architecture-and-aiml) | ✅ |
| 15–20 min demo video (unlisted YouTube) | [Demo Video](#-demo-video) | ⬜ link |
| Open-source License Details | [§14](#-14-license) | ✅ |
| Architecture Diagram in PDF/PPT | [SA PDF](assets/architecture/cardiotwin_system_architecture.pdf) | ✅ |
| Presentation in PDF/PPT (details and outcomes) | [PPT PDF](assets/presentation/CardioTwin_AyuRaksha_Presentation.pdf) | ✅ |
| All files and links publicly accessible | Public repo · unlisted video | ⬜ check |

<sub>The prototype also meets the challenge brief: static + dynamic data fusion ✅ · adverse-event prediction ✅ · doctor dashboard for the virtual patient ✅ · open, anonymized data only ✅</sub>

## 📜 14. License

The code is released under the **[MIT License](LICENSE)**. The datasets keep their own PhysioNet licences and must be downloaded from [PhysioNet](https://physionet.org/).

**Acknowledgements:** Happiest Health and Unstop for the challenge; PhysioNet and MIT-LCP for MIMIC-IV; the I-CARE consortium.

<p align="center"><sub>❤️ CardioTwin · Team AyuRaksha · research prototype, not medical advice</sub></p>
