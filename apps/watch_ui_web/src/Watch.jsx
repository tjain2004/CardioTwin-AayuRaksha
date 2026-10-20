import { useEffect, useRef, useState } from 'react'
import { usePoll, useLive } from './hooks'
import { nextDose, parseTs } from './format'
import { AlertsScreen, EmergencyScreen, FallScreen, MedicationScreen, OverviewScreen, Sim, StatusScreen } from './screens'

const PAGES = ['Overview', 'Status', 'Twin alerts', 'Medication', 'Fall check']
const FALL_PAGE = 4

// One patient's watch, live from the backend: the same status, alerts and fall checks the Wear OS app receives,
// plus the virtual watch's demo controls (start, Trigger AF, Trigger fall) beside it.
export default function Watch({ api, session, onLogout }) {
  const q = new URLSearchParams(location.search)
  const patients = usePoll(() => api.patients(), [], 15000)
  const [picked, setPicked] = useState(q.get('patient'))
  const list = patients.data || []
  const pid = session.role === 'patient' ? session.patient_id
    : picked || (list.find((p) => p.patient_id === 'stay-37093652') || list[0])?.patient_id
  const choose = (id) => {
    setPicked(id)
    const u = new URL(location.href)
    u.searchParams.set('patient', id)
    history.replaceState(null, '', u)
  }
  return (
    <div className="page">
      <header className="topbar">
        <img src="/icon.svg" alt="" />
        <span className="brand">Cardio<b>Twin</b> watch</span>
        <span className="muted small">Wear OS screens in the browser</span>
        <span style={{ flex: 1 }} />
        <span className="muted small">{session.display_name || session.role}</span>
        <button className="btn" onClick={onLogout}>Sign out</button>
      </header>
      {pid ? <PatientWatch key={pid} api={api} pid={pid} patients={list} canPick={session.role !== 'patient'} onPick={choose} />
        : <div className="panel">{patients.error ? patients.error.message : 'Loading patients…'}</div>}
    </div>
  )
}

function PatientWatch({ api, pid, patients, canPick, onPick }) {
  const status = usePoll(() => api.status(pid), [pid], 4000)
  const alerts = usePoll(() => api.alerts(pid, true), [pid], 3000)
  const readings = usePoll(() => api.readings(pid, 'hr,spo2,resp,hrv_rmssd', 40), [pid], 4000)
  const features = usePoll(() => api.features(pid), [pid], 10000)
  const sessions = usePoll(() => api.simSessions(), [pid], 3000)
  const live = useLive(api.wsUrl(pid), (m) => {
    if (m.type === 'status') { status.refresh(); readings.refresh() }
    if (m.type === 'alert' || m.type === 'emergency') { alerts.refresh(); status.refresh() }
    if (m.type === 'simulator') sessions.refresh()
  })

  const [page, setPage] = useState(0)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const [toast, setToast] = useState(null)
  const [hiddenAlerts, setHiddenAlerts] = useState(() => new Set())
  const [hiddenEmg, setHiddenEmg] = useState(() => new Set())
  const [answered, setAnswered] = useState({})          // fall alert id -> im_ok | need_help
  const [due, setDue] = useState(null)
  const [snoozeUntil, setSnoozeUntil] = useState(0)
  const [handled, setHandled] = useState(() => new Set())  // doses already taken / skipped / snoozed, by due time

  const st = status.data
  const watch = st?.views?.watch
  const open = alerts.data || []
  const sim = (sessions.data || []).find((s) => s.patient_id === pid && s.running)
  const patient = patients.find((p) => p.patient_id === pid)
  const simulated = !!(st?.simulated || sim || patient?.clock_mode === 'simulated')
  const vitals = {}
  for (const r of readings.data || []) vitals[r.type] = r          // ascending: the last one wins
  const fallCheck = open.find((a) => a.kind === 'FALL' && a.state === 'AWAITING_RESPONSE')
  // Hide covers both the emergency alert and the status's EMERGENCY_WATCH face that comes with it.
  const anyCritical = open.some((a) => a.severity === 'critical')
  const emgAlert = open.find((a) => a.severity === 'critical' && !hiddenEmg.has(a.alert_id))
  const emgView = emgAlert ? (emgAlert.payload?.emergency?.watch || emgAlert.payload?.watch || { title: 'EMERGENCY', text: emgAlert.title })
    : !anyCritical && watch?.type === 'EMERGENCY_WATCH' && !hiddenEmg.has('status') ? watch : null
  const next = nextDose()

  // A new fall check takes over the screen, as on the watch.
  const lastFall = useRef(null)
  useEffect(() => {
    if (fallCheck && fallCheck.alert_id !== lastFall.current) setPage(FALL_PAGE)
    lastFall.current = fallCheck?.alert_id || null
  }, [fallCheck])

  // A dose comes due at its (local) time, like the watch's reminder.
  useEffect(() => {
    const t = setInterval(() => {
      const now = Date.now()
      if (due || now < snoozeUntil) return
      const n = nextDose(new Date(now - 60000))
      if (n && n.at.getTime() <= now && !handled.has(n.at.getTime())) { setDue(n); setPage(3) }
    }, 5000)
    return () => clearInterval(t)
  }, [due, snoozeUntil, handled])

  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(null), 3500)
    return () => clearTimeout(t)
  }, [toast])

  // Swipe or arrow keys between pages, like the Wear OS pager.
  const go = (d) => setPage((p) => Math.min(PAGES.length - 1, Math.max(0, p + d)))
  useEffect(() => {
    const k = (e) => {
      if (e.target.closest('input, select, textarea')) return
      if (e.key === 'ArrowRight') go(1)
      if (e.key === 'ArrowLeft') go(-1)
    }
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  }, [])
  const down = useRef(null)
  const onPointerDown = (e) => { down.current = e.clientX }
  const onPointerUp = (e) => {
    if (down.current == null) return
    const dx = e.clientX - down.current
    down.current = null
    if (Math.abs(dx) > 45) go(dx < 0 ? 1 : -1)
  }

  async function run(fn, after) {
    setBusy(true); setErr(null)
    try { await fn(); after?.() } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }
  const refreshAll = () => { sessions.refresh(); status.refresh(); alerts.refresh(); readings.refresh() }
  const trigger = (event) => run(() => api.simTrigger(sim.session_id, event), refreshAll)
  const answerFall = (answer) => {
    setAnswered((a) => ({ ...a, [fallCheck.alert_id]: answer }))
    run(() => api.fallResponse(fallCheck.alert_id, answer), () => { alerts.refresh(); status.refresh(); if (answer === 'im_ok') setPage(0) })
  }
  const finishDose = (what) => {
    setHandled((h) => new Set(h).add(due.at.getTime()))
    if (what === 'snooze') setSnoozeUntil(Date.now() + 10 * 60000)
    setDue(null)
  }
  const taken = () => {
    // A simulated patient runs on the replay clock, so stamp the dose at the backend's "now" for them.
    const ts = simulated && features.data?.now ? parseTs(features.data.now).toISOString() : new Date().toISOString()
    const r = { patient_id: pid, timestamp: ts, type: 'dose_taken', value: due.med.mg, source: 'simulator', quality: 1,
      meta: { drug: due.med.generic, via: 'watch_ui_web' } }
    run(() => api.sendReadings([r]), () => { setToast(`Sent: ${due.med.label} taken`); finishDose('taken'); alerts.refresh() })
  }

  let screen
  if (emgView) {
    screen = (
      <EmergencyScreen view={emgView} simulated={simulated}
        onAction={() => setToast('On a real watch this opens the phone. Nothing is dialled automatically.')}
        onHide={() => setHiddenEmg((s) => new Set(s).add(emgAlert?.alert_id || 'status'))} />
    )
  } else if (page === 0) {
    screen = <OverviewScreen vitals={vitals} watch={watch} simulated={simulated} live={live} sim={sim} onOpenStatus={() => setPage(1)} />
  } else if (page === 1) {
    screen = <StatusScreen watch={watch} simulated={simulated} />
  } else if (page === 2) {
    screen = <AlertsScreen alerts={open.filter((a) => !hiddenAlerts.has(a.alert_id))} onSeen={(id) => setHiddenAlerts((s) => new Set(s).add(id))} />
  } else if (page === 3) {
    screen = (
      <MedicationScreen due={due} next={next} busy={busy} onTaken={taken} onSnooze={() => finishDose('snooze')} onSkip={() => finishDose('skip')}
        onDemo={() => setDue(next ? { ...next, at: new Date() } : null)} />
    )
  } else {
    screen = (
      <FallScreen check={fallCheck} answered={fallCheck && answered[fallCheck.alert_id]} busy={busy}
        onOk={() => answerFall('im_ok')} onHelp={() => answerFall('need_help')} canSimulate={!!sim} onSimulate={() => trigger('fall')} />
    )
  }

  return (
    <>
      {simulated && (
        <div className="simbar">
          <Sim />
          <span>
            No real watch: the <b>virtual watch</b> is replaying a recorded ICU test patient
            {sim?.stay_id ? ` (MIMIC-IV demo stay ${sim.stay_id})` : ''} through the same reading format a real watch sends.
          </span>
        </div>
      )}
      <div className="layout">
        <section className="stage">
          <div className="watch">
            <div className="screen" data-page={emgView ? 'emergency' : PAGES[page]} onPointerDown={onPointerDown} onPointerUp={onPointerUp}>
              {screen}
              {toast && <div className="toast">{toast}</div>}
            </div>
            <button className="crown" aria-label="Crown: back to Overview" title="Crown: back to Overview" onClick={() => setPage(0)} />
          </div>
          <div className="navrow">
            <button className="arrow" aria-label="Previous screen" disabled={page === 0} onClick={() => go(-1)}>‹</button>
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6 }}>
              <div className="dots">
                {PAGES.map((p, i) => <button key={p} className={`dot${i === page ? ' on' : ''}`} aria-label={p} title={p} onClick={() => setPage(i)} />)}
              </div>
              <div className="pagename">{emgView ? 'Emergency' : PAGES[page]}</div>
            </div>
            <button className="arrow" aria-label="Next screen" disabled={page === PAGES.length - 1} onClick={() => go(1)}>›</button>
          </div>
          <div className="muted small" style={{ textAlign: 'center' }}>Swipe or drag on the watch, use the arrows or ← → keys, or click the crown for Overview.</div>
        </section>

        <aside className="panel">
          <h2>Virtual watch</h2>
          {canPick && (
            <label className="small">Patient
              <select className="input" value={pid} onChange={(e) => onPick(e.target.value)}>
                {patients.map((p) => <option key={p.patient_id} value={p.patient_id}>{p.patient_id}</option>)}
              </select>
            </label>
          )}
          <div className="kv">
            {sim ? <>running · sim {sim.sim_time?.slice(11, 16)} UTC<br />{sim.readings_sent} readings sent{sim.in_af ? ' · in AF' : ''}</> : 'not running'}
            <br /><span style={{ color: live ? 'var(--low)' : 'var(--mod)' }}>● {live ? 'live link to backend' : 'connecting to backend…'}</span>
          </div>
          <div className="row">
            {sim ? <button className="btn" disabled={busy} onClick={() => run(() => api.simStop(sim.session_id), refreshAll)}>Stop watch</button>
              : <button className="btn btn-primary" disabled={busy} onClick={() => run(() => api.simStart(pid), refreshAll)}>Start virtual watch</button>}
          </div>
          <div className="row">
            {sim?.in_af ? <button className="btn" disabled={busy} onClick={() => trigger('af_stop')}>Stop AF</button>
              : <button className="btn" disabled={busy || !sim} onClick={() => trigger('af')}>Trigger AF</button>}
            <button className="btn" disabled={busy || !sim} onClick={() => trigger('fall')}>Trigger fall</button>
          </div>
          {err && <div className="err">{err}</div>}
          {status.error?.status === 404 && <div className="muted small">No status yet. Start the virtual watch; the first update arrives within a few seconds.</div>}
          <details className="muted small" style={{ lineHeight: 1.5 }}>
            <summary>Where the data comes from</summary>
            There is no real watch here. The virtual watch in the backend replays a real ICU
            test patient's recorded vitals, five minutes at a time, and sends them as the same readings a Wear OS watch would
            send. The backend scores them and sends back the watch screen, alerts and fall checks shown here. A real watch would
            simply replace the virtual one as the source of readings.
          </details>
          <p className="muted small" style={{ margin: 0 }}>Research prototype, not a medical device. Nothing here contacts emergency services.</p>
        </aside>
      </div>
    </>
  )
}
