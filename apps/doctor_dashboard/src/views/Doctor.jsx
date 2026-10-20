import { Fragment, useMemo, useState } from 'react'
import { usePoll, useLive } from '../hooks'
import { patientAppUrl, watchAppUrl } from '../api'
import { Card, Empty, ErrorLine, SimTag, Stat, TierBadge } from '../components/ui'
import { RiskChart, ShapBars, Sparkline } from '../components/charts'
import WatchFace from '../components/WatchFace'
import { ClinicList, PatientAppointments } from './Appointments'
import { AssistantDrawer, HeartOverviewCard } from './AddOns'
import { dayTime, hhmm, num, pct, RHYTHM, SIGNALS } from '../format'

export default function Doctor({ api, session, onLogout }) {
  const patients = usePoll(() => api.patients(), [], 5000)
  const health = usePoll(() => api.health(), [], 30000)
  const [selected, setSelected] = useState(null)
  const [filter, setFilter] = useState('')
  const list = patients.data || []
  const pid = selected || list[0]?.patient_id
  const shown = list.filter((p) => !filter || (p.display_name + p.patient_id).toLowerCase().includes(filter.toLowerCase()))

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-20 border-b border-line bg-surface/95 backdrop-blur">
        <div className="mx-auto flex max-w-[1500px] flex-wrap items-center gap-3 px-5 py-3">
          <span className="flex h-9 w-9 items-center justify-center rounded-full bg-accent-soft"><img src="/icon.svg" alt="" className="h-6 w-6" /></span>
          <div className="font-display text-[19px] font-extrabold">Cardio<span className="text-accent">Twin</span></div>
          <span className="hidden rounded-full bg-sunk px-2.5 py-0.5 text-[12px] font-semibold text-muted sm:inline">Doctor dashboard</span>
          <div className="ml-auto flex items-center gap-3 text-[13px]">
            <span className="hidden text-muted md:inline">
              {health.data ? <>Model <span className="num">{health.data.model?.name} {health.data.model?.version}</span>{health.data.model_loaded ? '' : ' (not loaded)'}</> : health.error ? <b className="rounded-full bg-emg-soft px-2 text-emg">Backend unreachable</b> : '…'}
            </span>
            <span className="flex items-center gap-2 font-semibold">
              <span className="flex h-8 w-8 items-center justify-center rounded-full bg-accent text-[13px] font-bold text-on-accent">{(session.display_name || 'D').replace(/^Dr\.?\s*/, '')[0]}</span>
              {session.display_name}
            </span>
            <button className="btn" onClick={onLogout}>Sign out</button>
          </div>
        </div>
        <div className="px-4 pb-1.5 text-center text-[11.5px] text-muted">
          Research prototype, not a medical device. Model trained on MIMIC-IV demo ICU data; provisional thresholds and draft emergency rules, not clinically reviewed.
        </div>
      </header>

      <div className="mx-auto grid max-w-[1500px] gap-6 px-5 py-5 lg:grid-cols-[290px_minmax(0,1fr)]">
        <aside className="space-y-3 lg:sticky lg:top-[96px] lg:max-h-[calc(100vh-112px)] lg:overflow-y-auto lg:p-1">
          <div className="flex items-baseline justify-between"><h2 className="font-display text-[20px] font-extrabold">Patients</h2><span className="text-[13px] text-muted">{list.length}</span></div>
          <input className="input w-full rounded-full px-4" placeholder="Search patients" value={filter} onChange={(e) => setFilter(e.target.value)} />
          <ErrorLine error={patients.error} />
          <div className="flex gap-2 overflow-x-auto pb-1 lg:block lg:space-y-2 lg:overflow-visible">
            {shown.map((p) => {
              const ev = p.latest_evaluation
              const active = p.patient_id === pid
              return (
                <button key={p.patient_id} onClick={() => setSelected(p.patient_id)}
                  className="card block w-56 shrink-0 rounded-2xl p-3 text-left transition-shadow lg:w-full"
                  style={active ? { borderColor: 'var(--accent)', borderWidth: 2, boxShadow: '0 8px 24px rgba(183,28,43,.14)' } : undefined}>
                  <div className="flex items-center justify-between gap-2">
                    <div className="num text-[14px] font-semibold" style={active ? { color: 'var(--accent)' } : undefined}>{p.patient_id}</div>
                    {ev ? <TierBadge tier={ev.tier} emergency={p.critical_open ? 'TRIGGERED' : ev.emergency_state} status={ev.status} /> : <span className="tag bg-sunk text-muted">No data</span>}
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-2 text-[12px] text-muted">
                    <SimTag show={!!ev?.simulated || p.clock_mode === 'simulated'} />
                    {ev?.score != null && <span className="num">score {pct(ev.score)}</span>}
                    {p.open_alerts > 0 && <span className="font-semibold text-high">{p.open_alerts} open alert{p.open_alerts > 1 ? 's' : ''}</span>}
                    {p.devices?.length > 0 && <span>{p.devices.length} device{p.devices.length > 1 ? 's' : ''}</span>}
                  </div>
                </button>
              )
            })}
          </div>
          <div className="hidden pt-3 lg:block"><ClinicList api={api} selected={pid} onSelect={setSelected} /></div>
        </aside>
        <main className="min-w-0">{pid ? <PatientPanel key={pid} api={api} pid={pid} patient={list.find((p) => p.patient_id === pid)} onChange={patients.refresh} /> : <Empty>No patients yet.</Empty>}</main>
      </div>
    </div>
  )
}

function PatientPanel({ api, pid, patient, onChange }) {
  const status = usePoll(() => api.status(pid), [pid], 4000)
  const history = usePoll(() => api.history(pid), [pid], 10000)
  const features = usePoll(() => api.features(pid), [pid], 6000)
  const readings = usePoll(() => api.readings(pid, 'hr,spo2,resp,rhythm,steps,hrv_rmssd', 900), [pid], 8000)
  const alerts = usePoll(() => api.alerts(pid), [pid], 4000)
  const ehr = usePoll(() => api.ehr(pid), [pid], 0)
  const sessions = usePoll(() => api.simSessions(), [pid], 5000)
  const [toast, setToast] = useState(null)
  const live = useLive(api.wsUrl(pid), (m) => {
    if (m.type === 'status') status.refresh()
    if (m.type === 'alert' || m.type === 'emergency') {
      alerts.refresh(); status.refresh(); onChange()
      if (m.type === 'emergency') setToast(m.alert)
    }
  })

  const st = status.data
  const doc = st?.views?.phone_doctor
  const res = st?.result
  const sim = (sessions.data || []).find((s) => s.patient_id === pid && s.running)
  const openAlerts = (alerts.data || []).filter((a) => a.state !== 'RESOLVED')
  const emergencyAlert = openAlerts.find((a) => a.severity === 'critical')

  const trend = useMemo(() => {
    const pts = []
    for (const t of doc?.trajectory || []) if (t.scored) pts.push({ t: t.hour, score: t.score, tier: t.tier, kind: 'hourly (retrospective)' })
    const last = pts.length ? pts[pts.length - 1].t : null
    for (const h of history.data || []) if (h.score != null && (!last || h.evaluated_at > last.replace(' ', 'T'))) pts.push({ t: h.evaluated_at, score: h.score, tier: h.tier, kind: 'live evaluation' })
    return pts
  }, [doc, history.data])

  return (
    <div className="space-y-5">
      {toast && (
        <div role="alert" className="flex flex-wrap items-center gap-3 rounded-xl px-4 py-3" style={{ background: 'var(--emg)', color: 'var(--on-emg)' }}>
          <span className="text-xl" aria-hidden="true">⚠</span>
          <div className="min-w-0 flex-1">
            <div className="font-display font-bold">Emergency: {toast.title}</div>
            <div className="text-[13px] opacity-90">{toast.message || toast.payload?.emergency?.reason} {toast.simulated && '(SIMULATED)'}</div>
          </div>
          <button className="btn" onClick={() => setToast(null)}>Dismiss</button>
        </div>
      )}

      <SimulatorBar api={api} pid={pid} patient={patient} sim={sim} refresh={() => { sessions.refresh(); status.refresh(); alerts.refresh() }} />

      {!st && !status.error && <Empty>Loading…</Empty>}
      {status.error && status.error.status === 404 && <Empty>No evaluation yet for {pid}. Start the virtual watch above, or pair a watch, and the first score appears within a few seconds.</Empty>}
      {status.error && status.error.status !== 404 && <ErrorLine error={status.error} />}

      {st && doc && (
        <>
          <div className="grid gap-4 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
            <ScoreCard st={st} doc={doc} res={res} live={live} />
            <Card eyebrow="On the watch now" title="Watch face" right={<SimTag show={st.simulated} />}>
              <div className="flex flex-wrap items-center justify-around gap-4">
                <a href={`${watchAppUrl()}/?patient=${encodeURIComponent(pid)}`} target="_blank" rel="noreferrer" title="Open the watch screens full size" className="rounded-full">
                  <WatchFace watch={st.views.watch} simulated={st.simulated} />
                </a>
                <div className="max-w-[16rem] space-y-2 text-[13px]">
                  <div className="text-muted">The Wear OS app will render this same payload. Patient phone screen: <b className="text-ink">{st.views.phone_patient?.screen}</b>.</div>
                  <a className="btn" href={`${watchAppUrl()}/?patient=${encodeURIComponent(pid)}`} target="_blank" rel="noreferrer">Open watch view</a>
                  <a className="btn" href={`${patientAppUrl()}/?view=patient&patient=${encodeURIComponent(pid)}`} target="_blank" rel="noreferrer">Open patient phone view</a>
                </div>
              </div>
            </Card>
          </div>

          <EmergencyCard em={doc.emergency} alert={emergencyAlert} />

          <Card eyebrow="Risk trend" title="Model output over time" right={<div className="text-right text-[12px] text-muted">{doc.header?.score_scale}<br />{doc.trajectory_summary?.hours_scored} hours scored</div>}>
            <RiskChart points={trend} thresholds={res?.risk?.thresholds} />
            <div className="mt-2 text-[12px] text-muted">{doc.trajectory_summary?.note} Dots coloured by tier.</div>
          </Card>

          <Vitals features={features.data} readings={readings.data} freshness={doc.data_quality?.freshness_by_signal} simulated={st.simulated} />
          <HeartOverviewCard api={api} pid={pid} />

          <div className="grid gap-4 xl:grid-cols-2">
            <Card eyebrow="Why this score" title="SHAP contributions (log-odds)">
              {doc.explanation_status !== 'AVAILABLE' ? <Empty>Explanation {doc.explanation_status?.toLowerCase()}.</Empty> : (
                <>
                  <p className="mb-3 text-[13px] text-muted">Starts from the average patient ({num(doc.waterfall?.base, 2)}) and ends at {num(doc.waterfall?.final, 2)}. Red pushes risk up, green pulls it down.</p>
                  <ShapBars steps={doc.waterfall?.steps || []} />
                  <p className="mt-2 text-[12px] text-muted">{doc.waterfall?.note}</p>
                </>
              )}
            </Card>
            <Card eyebrow="Top reasons" title="What drives it">
              <div className="space-y-2">
                {(doc.top_reasons || []).map((r) => (
                  <div key={r.rank} className="rounded-lg border border-line p-3">
                    <div className="flex items-baseline justify-between gap-2">
                      <div className="font-semibold">{r.reason}</div>
                      <div className="num text-[13px]" style={{ color: r.direction === 'raises' ? 'var(--high)' : 'var(--low)' }}>{r.effect_words} · odds ×{num(r.odds_multiplier, 2)}</div>
                    </div>
                    <ul className="mt-1 space-y-0.5 text-[13px] text-muted">
                      {r.inputs.map((i) => <li key={i.feature}>{i.text}{i.vs_reference && i.vs_reference !== 'in range' ? <span className="text-high"> ({i.vs_reference})</span> : ''}{i.was_missing ? ' (filled with training median)' : ''}</li>)}
                    </ul>
                  </div>
                ))}
              </div>
              <Baseline b={doc.baseline} />
            </Card>
          </div>

          <div className="grid gap-4 xl:grid-cols-2">
            <WhatIf api={api} pid={pid} />
            <Alerts api={api} alerts={alerts} onChange={() => { alerts.refresh(); onChange() }} />
          </div>

          <SarReport st={st} doc={doc} res={res} pid={pid} ehr={ehr.data} />
          <Ehr api={api} pid={pid} ehr={ehr} />
        </>
      )}
      <PatientAppointments api={api} pid={pid} />
      <AssistantDrawer api={api} pid={pid} />
    </div>
  )
}

function SimulatorBar({ api, pid, patient, sim, refresh }) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const run = async (fn) => { setBusy(true); setErr(null); try { await fn(); refresh() } catch (e) { setErr(e) } finally { setBusy(false) } }
  const ev = patient?.latest_evaluation
  const name = patient?.display_name || pid
  return (
    <section className="flex flex-wrap items-center gap-4 rounded-[var(--radius)] border-2 bg-surface p-5" style={{ borderColor: 'var(--accent)', boxShadow: '0 10px 30px rgba(183,28,43,.10)' }}
      aria-label={`Patient ${name}`}>
      <span className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full font-display text-[20px] font-extrabold" style={{ background: 'var(--accent-soft)', color: 'var(--accent)' }}>
        {(name.match(/[A-Za-z]/)?.[0] || 'P').toUpperCase()}
      </span>
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="font-display text-[22px] font-extrabold leading-tight">{name}</h1>
          {ev && <TierBadge tier={ev.tier} emergency={patient?.critical_open ? 'TRIGGERED' : ev.emergency_state} status={ev.status} />}
        </div>
        <div className="mt-0.5 text-[13px] text-muted"><span className="num">{pid}</span> · {patient?.devices?.map((d) => `${d.kind || 'device'} (${d.source})`).join(', ') || 'No device paired'}</div>
      </div>
      <div className="ml-auto flex flex-wrap items-center gap-2">
        {sim ? (
          <>
            <SimTag />
            <span className="num text-[12px] text-muted">virtual watch · sim {hhmm(sim.sim_time)} · {sim.readings_sent} readings{sim.in_af ? ' · in AF' : ''}</span>
            <button className="btn" disabled={busy} onClick={() => run(() => api.simTrigger(sim.session_id, sim.in_af ? 'af_stop' : 'af'))}>{sim.in_af ? 'Stop AF' : 'Trigger AF'}</button>
            <button className="btn" disabled={busy} onClick={() => run(() => api.simTrigger(sim.session_id, 'fall'))}>Trigger fall</button>
            <button className="btn btn-primary" disabled={busy} onClick={() => run(() => api.simStop(sim.session_id))}>Stop watch</button>
          </>
        ) : patient?.replay_stay_id ? (
          <button className="btn btn-primary" disabled={busy} onClick={() => run(() => api.simStart(pid))}>Start virtual watch</button>
        ) : null}
      </div>
      {err && <div className="w-full"><ErrorLine error={err} /></div>}
    </section>
  )
}

function ScoreCard({ st, doc, res, live }) {
  const h = doc.header || {}
  const u = res?.risk?.uncertainty
  const ts = doc.trajectory_summary || {}
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2">
        <span className="eyebrow">Next {h.horizon} · evaluated {dayTime(st.evaluated_at)}</span>
        <SimTag show={st.simulated} />
        <span className="tag" style={{ background: 'var(--sunk)', color: live ? 'var(--low)' : 'var(--muted)' }}>{live ? '● live' : '○ polling'}</span>
      </div>
      <div className="mt-2 flex flex-wrap items-baseline gap-3">
        <div className="num font-display text-5xl font-bold">{st.score != null ? pct(st.score) : '–'}</div>
        <TierBadge tier={st.tier} emergency={st.emergency_state} status={st.status} />
        {h.status !== 'OK' && <span className="text-[13px] text-muted">{h.reason}</span>}
      </div>
      <p className="mt-2 text-[14px] text-muted">Chance of {h.target} within {h.horizon} ({h.score_scale}{h.provisional_policy ? ', provisional cut-offs' : ''}).</p>
      <div className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-3">
        <Stat label={`Model spread (${u?.n_refits ?? '–'} refits)`} value={u ? `${pct(u.p05)} – ${pct(u.p95)}` : '–'} sub={u?.tier_could_change ? 'tier could change' : null} />
        <Stat label="Peak in last 24 h" value={pct(ts.peak_24h)} />
        <Stat label="Hours at HIGH, 24 h" value={ts.hours_at_high_24h ?? '–'} />
        <Stat label="Data coverage" value={doc.data_quality?.coverage_pct != null ? `${num(doc.data_quality.coverage_pct, 1)}%` : '–'} sub={doc.data_quality?.status} />
        <Stat label="× average risk" value={res?.risk?.times_average_risk != null ? `${num(res.risk.times_average_risk, 2)}×` : '–'} />
        <Stat label="Alert decision" value={<span className="text-[13px]">{doc.alert?.decision || '–'}</span>} />
      </div>
      {(doc.data_quality?.warnings?.length > 0 || doc.data_quality?.flags_for_remeasurement?.length > 0) && (
        <ul className="mt-3 space-y-1 rounded-md bg-mod-soft px-3 py-2 text-[13px]">
          {doc.data_quality.warnings.map((w, i) => <li key={i}>{typeof w === 'string' ? w : w.message || JSON.stringify(w)}</li>)}
        </ul>
      )}
    </Card>
  )
}

function EmergencyCard({ em, alert }) {
  if (!em) return null
  const on = em.state === 'TRIGGERED'
  const review = em.state === 'REVIEW_REQUIRED'
  if (!on && !alert) return review ? <ReviewCard em={em} /> : null
  return (
    <section className="card p-4" style={{ borderColor: 'var(--emg)', background: 'var(--emg-soft)' }} role="alert">
      <div className="flex flex-wrap items-center gap-2">
        <span aria-hidden="true">⚠</span>
        <h2 className="font-display text-[17px] font-bold text-emg">
          {alert ? `Open emergency alert: ${alert.title}` : 'Critical: emergency rule met'} {(alert?.rule_id || em.rule_id) && `(${alert?.rule_id || em.rule_id})`}
        </h2>
        {alert?.simulated && <SimTag />}
      </div>
      <p className="mt-1 text-[14px]">{alert ? alert.message || alert.payload?.emergency?.reason : em.reason}</p>
      {alert && !on && <p className="mt-1 text-[13px] text-muted">The latest readings no longer meet an emergency rule, but the alert stays open until a clinician resolves it.</p>}
      {on && em.evidence?.length > 0 && <ul className="mt-1 list-disc pl-5 text-[13px]">{em.evidence.map((e, i) => <li key={i}>{typeof e === 'string' ? e : `${e.signal}: ${e.value} ${e.unit || ''} at ${e.timestamp} (${e.band})`}</li>)}</ul>}
      {on && em.guidance?.length > 0 && <ul className="mt-1 list-disc pl-5 text-[13px]">{em.guidance.map((g, i) => <li key={i}>{g}</li>)}</ul>}
      <p className="mt-2 text-[12px] text-muted">Runs before the model and independently of it. {em.review_status}. Classified critical, so the patient's phone shows the emergency screen with a Call 112 button that only opens the dialler (never auto-dials); dispatch status: NOT_DISPATCHED.</p>
    </section>
  )
}

// Urgent review (one red-band signal, an unconfirmed reading or an amber symptom score): not critical, so no 112 prompt.
function ReviewCard({ em }) {
  return (
    <section className="card p-4" style={{ borderColor: 'var(--high)', background: 'var(--high-soft)' }} role="status">
      <div className="flex flex-wrap items-center gap-2">
        <span aria-hidden="true">!</span>
        <h2 className="font-display text-[17px] font-bold" style={{ color: 'var(--high)' }}>Urgent review: re-measure {em.rule_id && `(${em.rule_id})`}</h2>
      </div>
      <p className="mt-1 text-[14px]">{em.reason}</p>
      {em.guidance?.length > 0 && <ul className="mt-1 list-disc pl-5 text-[13px]">{em.guidance.map((g, i) => <li key={i}>{g}</li>)}</ul>}
      <p className="mt-2 text-[12px] text-muted">Not classified critical. The patient is asked to re-measure and contact their doctor; the phone shows no Call 112 prompt. It becomes an emergency only if an emergency rule is met.</p>
    </section>
  )
}

const VITALS = ['hr', 'spo2', 'resp', 'hrv_rmssd']

function Vitals({ features, readings, freshness, simulated }) {
  const by = useMemo(() => {
    const m = {}
    for (const r of readings || []) (m[r.type] ||= []).push(r)
    return m
  }, [readings])
  const sig = features?.signals || {}
  const lastRhythm = (by.rhythm || []).at(-1)
  const steps = (by.steps || []).reduce((s, r) => s + (r.value_num || 0), 0)
  return (
    <Card eyebrow="Vitals" title="Watch readings against the personal baseline" right={<SimTag show={simulated} />}>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {VITALS.map((k) => {
          const f = sig[k] || (k === 'resp' ? sig.rr : null)
          const meta = SIGNALS[k]
          const series = (by[k] || []).map((r) => r.value_num)
          const b = f?.baseline
          const fr = freshness?.[k === 'resp' ? 'rr' : k]
          return (
            <div key={k} className="rounded-lg border border-line p-3">
              <div className="flex items-baseline justify-between">
                <div className="text-[13px] text-muted">{meta.label}</div>
                {fr && <span className="tag" style={{ background: 'var(--sunk)', color: fr.status === 'FRESH' ? 'var(--low)' : 'var(--high)' }}>{fr.status?.toLowerCase()}</span>}
              </div>
              <div className="num mt-1 text-2xl font-semibold">{num(f?.last?.value, meta.d)} <span className="text-[13px] font-normal text-muted">{meta.unit}</span></div>
              <Sparkline values={series.slice(-72)} band={b?.median != null && b?.mad != null ? [b.median - 2 * b.mad, b.median + 2 * b.mad] : null} />
              <div className="mt-1 grid grid-cols-3 gap-1 text-[11.5px] text-muted">
                <div>1 h <span className="num text-ink">{num(f?.['1h']?.mean, 0)}</span></div>
                <div>6 h <span className="num text-ink">{num(f?.['6h']?.mean, 0)}</span></div>
                <div>24 h <span className="num text-ink">{num(f?.['24h']?.mean, 0)}</span></div>
              </div>
              <div className="mt-1 text-[11.5px] text-muted">
                Baseline {b?.median != null ? <span className="num text-ink">{num(b.median, 0)}</span> : '–'} ({b?.status?.toLowerCase() || 'unavailable'})
                {b?.robust_z_1h != null && <> · z <span className="num" style={{ color: Math.abs(b.robust_z_1h) >= 3 ? 'var(--high)' : 'var(--ink)' }}>{num(b.robust_z_1h, 1)}</span></>}
              </div>
            </div>
          )
        })}
      </div>
      <div className="mt-3 flex flex-wrap gap-4 text-[13px] text-muted">
        <span>Rhythm: <b className="text-ink">{lastRhythm ? RHYTHM[lastRhythm.value_text] || lastRhythm.value_text : '–'}</b>{lastRhythm && ` at ${hhmm(lastRhythm.ts)}`}</span>
        <span>Steps in window: <b className="num text-ink">{steps || '–'}</b></span>
        {features?.now && <span>Feature clock: <span className="num">{dayTime(features.now)}</span></span>}
        <span>Shaded band: baseline ± 2 MAD</span>
      </div>
    </Card>
  )
}

function Baseline({ b }) {
  if (!b?.deviations?.length) return null
  return (
    <div className="mt-4">
      <div className="mb-2 font-semibold">Against this patient's own baseline <span className="text-[12px] font-normal text-muted">({b.status?.toLowerCase()})</span></div>
      <div className="grid grid-cols-3 gap-3">
        {b.deviations.map((d) => (
          <div key={d.signal} className="text-[13px]">
            <div className="text-muted">{SIGNALS[d.signal]?.short || d.signal}: {num(d.latest_value, 0)} {d.unit}</div>
            <div className="num font-semibold">{d.deviation > 0 ? '+' : ''}{num(d.deviation, 0)} vs {num(d.baseline_median, 0)}</div>
            <div className="text-[12px] text-muted">robust z {num(d.robust_z, 2)}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

const WHATIF_FIELDS = [
  { k: 'hr', label: 'Heart rate', unit: 'bpm', min: 40, max: 160, step: 1, def: 75 },
  { k: 'spo2', label: 'SpO2', unit: '%', min: 80, max: 100, step: 1, def: 97 },
  { k: 'rr', label: 'Breathing rate', unit: '/min', min: 8, max: 35, step: 1, def: 16 },
  { k: 'bmi', label: 'BMI', unit: '', min: 15, max: 45, step: 0.5, def: 25 },
  { k: 'smoker', label: 'Smoker (0/1)', unit: '', min: 0, max: 1, step: 1, def: 0 },
]

function WhatIf({ api, pid }) {
  const [on, setOn] = useState({ hr: true })
  const [vals, setVals] = useState(Object.fromEntries(WHATIF_FIELDS.map((f) => [f.k, f.def])))
  const [res, setRes] = useState(null)
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)
  const run = async () => {
    const changes = Object.fromEntries(WHATIF_FIELDS.filter((f) => on[f.k]).map((f) => [f.k, Number(vals[f.k])]))
    if (!Object.keys(changes).length) return
    setBusy(true); setErr(null)
    try { setRes(await api.whatif(pid, changes)) } catch (e) { setErr(e) } finally { setBusy(false) }
  }
  return (
    <Card eyebrow="What if" title={<>Hypothetical model simulation <span className="tag ml-1 align-middle" style={{ background: 'var(--accent-soft)', color: 'var(--accent)' }}>Hypothetical</span></>}>
      <div className="space-y-2">
        {WHATIF_FIELDS.map((f) => (
          <label key={f.k} className="grid grid-cols-[1.25rem_7.5rem_1fr_4rem] items-center gap-2 text-[13px]">
            <input type="checkbox" checked={!!on[f.k]} onChange={(e) => setOn({ ...on, [f.k]: e.target.checked })} />
            <span>{f.label}</span>
            <input type="range" min={f.min} max={f.max} step={f.step} value={vals[f.k]} disabled={!on[f.k]} onChange={(e) => setVals({ ...vals, [f.k]: e.target.value })} className="accent-[var(--accent)]" />
            <span className="num text-right">{vals[f.k]}{f.unit && ` ${f.unit}`}</span>
          </label>
        ))}
      </div>
      <button className="btn btn-primary mt-3" disabled={busy} onClick={run}>{busy ? 'Simulating…' : 'Run what-if'}</button>
      <ErrorLine error={err} />
      {res && (
        <div className="mt-3 space-y-2">
          {res.status !== 'OK' ? <Empty>{res.reason || res.status}{res.rejected && ` ${JSON.stringify(res.rejected)}`}</Empty> : (
            <>
              <div className="flex flex-wrap items-center gap-3">
                <span className="num text-xl font-semibold">{pct(res.original?.score)} → {pct(res.simulated?.score)}</span>
                <TierBadge tier={res.original?.tier} /> <span>→</span> <TierBadge tier={res.simulated?.tier} emergency={res.simulated?.emergency_state} />
              </div>
              <div className="text-[12px] text-muted">Applied to the last {res.applied_to_last_hours} hours of readings.</div>
              <table className="w-full text-[12.5px]">
                <thead><tr className="text-left text-muted"><th className="font-normal">Model input</th><th className="text-right font-normal">SHAP before</th><th className="text-right font-normal">after</th></tr></thead>
                <tbody>{(res.changed_model_inputs || []).slice(0, 6).map((c) => (
                  <tr key={c.feature} className="border-t border-line"><td className="num py-0.5">{c.feature}</td><td className="num text-right">{num(c.shap_original, 3)}</td><td className="num text-right">{num(c.shap_simulated, 3)}</td></tr>
                ))}</tbody>
              </table>
            </>
          )}
          <p className="text-[12px] text-muted">{res.label}</p>
        </div>
      )}
    </Card>
  )
}

function Alerts({ api, alerts, onChange }) {
  const [showAll, setShowAll] = useState(false)
  const [err, setErr] = useState(null)
  const list = (alerts.data || []).filter((a) => showAll || a.state !== 'RESOLVED')
  const act = async (a, action, body) => {
    setErr(null)
    try { await api.alertAction(a.alert_id, action, body); onChange() } catch (e) { setErr(e) }
  }
  const sevColor = { critical: 'var(--emg)', high: 'var(--high)', moderate: 'var(--mod)', low: 'var(--low)' }
  return (
    <Card eyebrow="Alerts" title={`${list.length} ${showAll ? 'total' : 'open'}`} right={<label className="text-[13px]"><input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} /> show resolved</label>}>
      <ErrorLine error={err} />
      {list.length === 0 && <Empty>No open alerts.</Empty>}
      <div className="space-y-2">
        {list.map((a) => (
          <div key={a.alert_id} className="rounded-lg border border-line p-3" style={{ borderLeft: `4px solid ${sevColor[a.severity] || 'var(--line)'}` }}>
            <div className="flex flex-wrap items-center gap-2">
              <div className="font-semibold">{a.title}</div>
              <span className="tag" style={{ background: 'var(--sunk)', color: sevColor[a.severity] }}>{a.severity}</span>
              <span className="tag bg-sunk text-muted">{a.state.replaceAll('_', ' ')}</span>
              {a.simulated && <SimTag />}
              <span className="num ml-auto text-[12px] text-muted">{a.rule_id || a.kind} · {dayTime(a.created_at)}</span>
            </div>
            {a.message && <p className="mt-1 text-[13px] text-muted">{a.message}</p>}
            {a.payload?.not_a_diagnosis && <p className="mt-1 text-[12px] text-muted">{a.payload.not_a_diagnosis}</p>}
            {a.state !== 'RESOLVED' && (
              <div className="mt-2 flex flex-wrap gap-2">
                {['NEW', 'DISPLAYED', 'ESCALATED', 'AWAITING_RESPONSE'].includes(a.state) && <button className="btn" onClick={() => act(a, 'acknowledge')}>Acknowledge</button>}
                <button className="btn" onClick={() => act(a, 'review', { review_status: 'reviewed', note: 'Reviewed on dashboard' })}>Mark reviewed</button>
                <button className="btn" onClick={() => act(a, 'escalate', { to: 'care team' })}>Escalate</button>
                <button className="btn" onClick={() => { const note = prompt('Resolution note (required)'); if (note) act(a, 'resolve', { note }) }}>Resolve…</button>
              </div>
            )}
          </div>
        ))}
      </div>
    </Card>
  )
}

// SAR = Situation, Assessment, Recommendation. The engine also returns each section as separate sentences
// (result.sar.sentences); they are laid out as facts, factor rows and numbered steps instead of one paragraph each.
const EFFECT = /\s*This (?:\w+ )?(raises|lowers) the model output \(odds x([\d.]+)\)\.\s*/
const SIG_LABEL = { hr: 'Heart rate', spo2: 'SpO2', rr: 'Breathing', resp: 'Breathing', rhythm: 'Rhythm', hrv_rmssd: 'HRV' }

const cap = (x) => x.charAt(0).toUpperCase() + x.slice(1)

function sarParts(sar) {
  const sen = sar.sentences || {}
  const text = (k) => (sen[k] || []).map((x) => x.text)
  const split = (p) => (p ? p.match(/[^.]+(?:\.(?!\d)|$)+/g)?.map((x) => x.trim()).filter(Boolean) || [p] : [])
  const assess = text('assessment').length ? text('assessment') : split(sar.assessment)
  const drivers = [], baseline = [], other = []
  for (const t of assess) {
    const m = t.match(EFFECT)
    if (m) {
      const body = t.replace(EFFECT, ' ').trim()
      const i = body.indexOf(':')
      const caveat = body.slice(m.index).trim()
      drivers.push({ name: i > 0 ? body.slice(0, i) : 'Factor', detail: cap((i > 0 ? body.slice(i + 1) : body).replace(caveat, '').trim()), dir: m[1], odds: m[2], caveat: caveat !== body ? caveat : '' })
    } else if (/personal baseline|this patient's baseline/.test(t)) {
      const [head, ...rest] = t.split(/(?<=\.)\s+(?=[A-Z])/)
      baseline.push({ head, rest: rest.join(' ') })
    } else other.push(t)
  }
  const rec = text('recommendation').length ? text('recommendation') : split(sar.recommendation)
  const disclaimer = rec.filter((t) => /never replaces|suggests no treatment/i.test(t))
  return { situation: text('situation'), drivers, baseline, other, steps: rec.filter((t) => !disclaimer.includes(t)), disclaimer: disclaimer.join(' ') }
}

function SarSection({ letter, title, children }) {
  return (
    <section className="grid gap-3 border-t border-line pt-4 md:grid-cols-[180px_minmax(0,1fr)]">
      <div className="flex items-center gap-2.5 md:items-start">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent font-display text-lg font-extrabold text-on-accent">{letter}</span>
        <h3 className="font-display text-[16px] font-bold md:pt-1.5">{title}</h3>
      </div>
      <div className="min-w-0">{children}</div>
    </section>
  )
}

function SarReport({ st, doc, res, pid, ehr }) {
  const sar = { ...(doc.sar || {}), sentences: res?.sar?.sentences }
  const parts = sarParts(sar)
  const risk = res?.risk || {}
  const th = risk.thresholds || {}
  const u = risk.uncertainty
  const fresh = doc.data_quality?.freshness_by_signal || {}
  const imputed = (doc.technical_details_collapsed?.imputed || res?.data_quality?.imputed_fields || []).map((x) => x.feature || x)
  const h = doc.header || {}
  const tier = <TierBadge tier={st.tier} emergency={st.emergency_state} status={st.status} />

  const print = () => {
    const w = window.open('', '_blank')
    if (!w) return
    const esc = (x) => String(x ?? '').replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]))
    const li = (xs) => xs.map((x) => `<li>${esc(x)}</li>`).join('')
    w.document.write(`<!doctype html><title>SAR ${esc(pid)}</title><style>
      body{font:14px/1.55 "Segoe UI",system-ui,sans-serif;color:#2B1416;max-width:780px;margin:28px auto;padding:0 18px}
      header{border-bottom:3px solid #D32F2F;padding-bottom:10px;margin-bottom:14px}h1{font-size:20px;margin:0;color:#A51C24}
      .meta{color:#7D5F62;font-size:12px}.sim{background:#A86400;color:#fff;padding:1px 6px;border-radius:4px;font-size:11px;font-weight:700}
      .sum{background:#FDE7E7;border-left:4px solid #D32F2F;padding:8px 12px;border-radius:6px;font-weight:600}
      h2{font-size:15px;margin:18px 0 6px;color:#A51C24}table{border-collapse:collapse;width:100%}td{padding:4px 8px;border-bottom:1px solid #F2D9DB;vertical-align:top}
      td:first-child{color:#7D5F62;width:30%}.up{color:#D9480F;font-weight:600}.down{color:#2E7D4F;font-weight:600}.small{color:#7D5F62;font-size:12px}</style>
      <header><h1>CardioTwin SAR report · ${esc(pid)} ${st.simulated ? '<span class="sim">SIMULATED</span>' : ''}</h1>
      <div class="meta">Evaluated ${esc(st.evaluated_at)} · model ${esc(h.model_version)} · policy ${esc(h.policy_version)} · audit ${esc(st.audit_id)}</div></header>
      <p class="sum">${esc(sar.one_line)}</p>
      <h2>Situation</h2><ul>${li(parts.situation.length ? parts.situation : [sar.situation])}</ul>
      <h2>Assessment</h2>
      ${parts.drivers.length ? `<table>${parts.drivers.map((d) => `<tr><td>${esc(d.name)}</td><td>${esc(d.detail)} <span class="${d.dir === 'raises' ? 'up' : 'down'}">${d.dir} risk, odds ×${esc(d.odds)}</span>${d.caveat ? `<div class="small">${esc(d.caveat)}</div>` : ''}</td></tr>`).join('')}</table>` : ''}
      <ul>${li(parts.baseline.map((b) => b.head))}${li(parts.other)}</ul>
      <h2>Recommendation</h2><ol>${li(parts.steps)}</ol><p class="small">${esc(parts.disclaimer)}</p>
      <h2>Limitations</h2><ul class="small">${li(sar.limitations || [])}</ul>
      <p class="small">Research prototype, not a medical device. ${esc(ehr?.source || '')}</p>`)
    w.document.close()
    w.focus()
    w.print()
  }

  return (
    <section className="card overflow-hidden">
      <header className="brand-bar flex flex-wrap items-center gap-3 px-5 py-3.5">
        <div className="min-w-0 flex-1">
          <div className="font-mono text-[11px] uppercase tracking-[.12em] opacity-85">SAR report · {pid}</div>
          <h2 className="font-display text-[19px] font-bold leading-tight">Situation, Assessment, Recommendation</h2>
        </div>
        {st.simulated && <span className="tag bg-white text-sim">Simulated</span>}
        <button className="btn border-white bg-white text-accent hover:bg-white" onClick={print}>Print / save PDF</button>
      </header>

      <div className="space-y-4 p-5">
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <div className="rounded-xl bg-accent-soft p-3">
            <div className="text-[12px] text-muted">Model output, next {h.horizon}</div>
            <div className="mt-0.5 flex items-center gap-2"><span className="num font-display text-[26px] font-bold">{pct(st.score)}</span>{tier}</div>
          </div>
          <div className="rounded-xl bg-sunk p-3">
            <div className="text-[12px] text-muted">Model spread ({u?.n_refits ?? '–'} refits)</div>
            <div className="num mt-1 text-[17px] font-semibold">{u ? `${pct(u.p05)} – ${pct(u.p95)}` : '–'}</div>
            {u?.tier_could_change && <div className="text-[12px] text-high">tier could change</div>}
          </div>
          <div className="rounded-xl bg-sunk p-3">
            <div className="text-[12px] text-muted">Cut-offs{h.provisional_policy ? ' (provisional)' : ''}</div>
            <div className="num mt-1 text-[14px]">Moderate ≥ {pct(th.moderate)}<br />High ≥ {pct(th.high)}</div>
          </div>
          <div className="rounded-xl bg-sunk p-3">
            <div className="text-[12px] text-muted">Data</div>
            <div className="num mt-1 text-[17px] font-semibold">{doc.data_quality?.coverage_pct != null ? `${num(doc.data_quality.coverage_pct, 0)}% coverage` : '–'}</div>
            <div className="text-[12px] text-muted">{doc.data_quality?.status?.toLowerCase()}</div>
          </div>
        </div>

        <p className="rounded-lg border-l-4 border-accent bg-surface px-3 py-2 text-[14.5px] font-semibold">{sar.one_line}</p>

        <SarSection letter="S" title="Situation">
          <dl className="grid grid-cols-[minmax(0,9rem)_minmax(0,1fr)] gap-x-4 gap-y-2 text-[14px]">
            <dt className="text-muted">Predicting</dt><dd>{h.target} within {h.horizon}</dd>
            <dt className="text-muted">Evaluated</dt><dd className="num">{dayTime(st.evaluated_at)}</dd>
            <dt className="text-muted">Latest readings</dt>
            <dd className="flex flex-wrap gap-1.5">
              {Object.entries(fresh).map(([k, f]) => (
                <span key={k} className="rounded-full border px-2.5 py-0.5 text-[12.5px]" style={{ borderColor: f.status === 'FRESH' ? 'var(--low)' : 'var(--high)', color: f.status === 'FRESH' ? 'var(--low)' : 'var(--high)' }}>
                  {SIG_LABEL[k] || k} · {Math.round(f.age_minutes)} min · {f.status.toLowerCase()}
                </span>
              ))}
              {!Object.keys(fresh).length && <span className="text-muted">none</span>}
            </dd>
            <dt className="text-muted">Filled-in inputs</dt>
            <dd className="flex flex-wrap gap-1.5">
              {imputed.length ? imputed.map((f) => <span key={f} className="num rounded bg-sunk px-1.5 py-0.5 text-[12px]">{f}</span>) : <span className="text-muted">none</span>}
              {imputed.length > 0 && <span className="w-full text-[12px] text-muted">Not available, filled with training medians.</span>}
            </dd>
          </dl>
        </SarSection>

        <SarSection letter="A" title="Assessment">
          {parts.drivers.length > 0 && (
            <>
              <div className="eyebrow mb-1.5">What moves the score</div>
              <div className="divide-y divide-line rounded-xl border border-line">
                {parts.drivers.map((d, i) => (
                  <div key={i} className="grid gap-1 px-3 py-2.5 text-[13.5px] sm:grid-cols-[minmax(0,10rem)_minmax(0,1fr)_auto] sm:gap-3">
                    <b>{d.name}</b>
                    <span className="text-muted">{d.detail}{d.caveat && <span className="mt-0.5 block text-[12px] text-mod">{d.caveat}</span>}</span>
                    <span className="num h-fit whitespace-nowrap rounded-full px-2 py-0.5 text-[12px] font-semibold"
                      style={{ background: d.dir === 'raises' ? 'var(--high-soft)' : 'var(--low-soft)', color: d.dir === 'raises' ? 'var(--high)' : 'var(--low)' }}>
                      {d.dir === 'raises' ? '▲ raises' : '▼ lowers'} ×{d.odds}
                    </span>
                  </div>
                ))}
              </div>
            </>
          )}
          {parts.baseline.length > 0 && (
            <>
              <div className="eyebrow mb-1.5 mt-4">Compared with this patient's own baseline</div>
              <ul className="space-y-1.5 text-[13.5px]">
                {parts.baseline.map((b, i) => <li key={i} className="rounded-lg bg-sunk px-3 py-2">{b.head}{b.rest && <span className="block text-[12px] text-muted">{b.rest}</span>}</li>)}
              </ul>
            </>
          )}
          {parts.other.length > 0 && (
            <>
              <div className="eyebrow mb-1.5 mt-4">Trend and uncertainty</div>
              <ul className="list-disc space-y-1 pl-5 text-[13.5px]">{parts.other.map((t, i) => <li key={i}>{t}</li>)}</ul>
            </>
          )}
        </SarSection>

        <SarSection letter="R" title="Recommendation">
          <ol className="space-y-2">
            {parts.steps.map((t, i) => (
              <li key={i} className="flex gap-3 text-[14px]">
                <span className="num flex h-6 w-6 shrink-0 items-center justify-center rounded-full border-2 border-accent text-[12px] font-bold text-accent">{i + 1}</span>
                <span className="pt-0.5">{t}</span>
              </li>
            ))}
          </ol>
          {parts.disclaimer && <p className="mt-3 text-[12.5px] italic text-muted">{parts.disclaimer}</p>}
        </SarSection>

        {sar.limitations?.length > 0 && (
          <details className="rounded-xl bg-sunk px-4 py-2.5 text-[13px] text-muted">
            <summary className="cursor-pointer font-semibold text-accent">Limitations ({sar.limitations.length})</summary>
            <ul className="mt-1.5 list-disc space-y-1 pl-5">{sar.limitations.map((l, i) => <li key={i}>{l}</li>)}</ul>
          </details>
        )}
        <div className="num text-[11.5px] text-muted">audit {st.audit_id} · model {h.model_version} · policy {h.policy_version} · {res?.model?.calibration_status}</div>
      </div>
    </section>
  )
}

function Ehr({ api, pid, ehr }) {
  const [drug, setDrug] = useState('')
  const [limit, setLimit] = useState('')
  const [err, setErr] = useState(null)
  const e = ehr.data
  if (!e) return null
  const prof = e.profile || {}
  const show = [['Age', prof.age_at_admit], ['Sex', prof.gender_male === 1 ? 'male' : prof.gender_male === 0 ? 'female' : null], ['BMI', prof.bmi], ['Weight', prof.weight_lbs && `${prof.weight_lbs} lb`], ['Smoker', prof.smoker === 1 ? 'yes' : prof.smoker === 0 ? 'no' : null], ['AF on record', prof.hist_af === 1 ? 'yes' : prof.hist_af === 0 ? 'no' : null], ['Active drugs', prof.n_active_drugs], ['HAS-BLED (partial)', prof.has_bled_partial], ['ASCVD 10y', prof.ascvd_10y_risk != null && pct(prof.ascvd_10y_risk)]]
  const addLimit = async () => {
    if (!drug || !limit) return
    setErr(null)
    try { await api.putEhr(pid, { dose_limits: { ...(e.dose_limits || {}), [drug.trim().toLowerCase()]: Number(limit) } }); setDrug(''); setLimit(''); ehr.refresh() } catch (x) { setErr(x) }
  }
  return (
    <Card eyebrow="EHR" title="Profile, labs and dose limits" right={<span className="text-[12px] text-muted">{e.source}</span>}>
      <div className="grid gap-4 lg:grid-cols-3">
        <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-[13px]">
          {show.filter(([, v]) => v != null && v !== false).map(([k, v]) => <Fragment key={k}><dt className="text-muted">{k}</dt><dd className="num">{typeof v === 'number' ? num(v, v % 1 ? 1 : 0) : v}</dd></Fragment>)}
        </dl>
        <div className="text-[13px]">
          <div className="mb-1 font-semibold">Labs</div>
          {(e.clinical || []).map((c) => <div key={c.name} className="flex justify-between border-b border-line py-0.5"><span>{c.name}</span><span className="num">{c.value} {c.unit}</span></div>)}
        </div>
        <div className="text-[13px]">
          <div className="mb-1 font-semibold">Daily dose limits</div>
          {Object.entries(e.dose_limits || {}).map(([d, l]) => <div key={d} className="flex justify-between border-b border-line py-0.5"><span>{d}</span><span className="num">{l} mg/day</span></div>)}
          {!Object.keys(e.dose_limits || {}).length && <div className="text-muted">None set. A limit lets the backend raise a tell-the-doctor alert when the patient logs more.</div>}
          <div className="mt-2 flex gap-2">
            <input className="input min-w-0 flex-1" placeholder="drug" value={drug} onChange={(x) => setDrug(x.target.value)} />
            <input className="input w-20" placeholder="mg" type="number" value={limit} onChange={(x) => setLimit(x.target.value)} />
            <button className="btn" onClick={addLimit}>Add</button>
          </div>
          <ErrorLine error={err} />
        </div>
      </div>
    </Card>
  )
}
