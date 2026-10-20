import { useEffect, useMemo, useRef, useState } from 'react'
import { usePoll, useLive } from '../hooks'
import { Card, Disclaimer, Empty, ErrorLine, Icon, SimTag } from '../components/ui'
import { Sparkline } from '../components/charts'
import { apptDay, apptTime, greeting, hhmm, localDay, localHm, minutesOf, num, parseTs, RHYTHM, TIER, tierKey } from '../format'
import Appointments, { BookingSheet } from './Appointments'
import Medicines, { dueDoses } from './Medicines'
import Health from './Health'
import Profile, { NotificationsSheet } from './Profile'
import AssistantSheet from './Assistant'
import NearbyHelpSheet from './NearbyHelp'
import { useWellnessReminders } from './Wellness'

// Phone layout for a patient (or caregiver). Never shows a score number, matching the engine's phone_patient view.
// Five tabs (Home, Visits, Medicines, Health, Profile). The fall check and the emergency screen show on every tab.
export default function Patient({ api, session, patientId, onLogout, preview }) {
  const mine = usePoll(() => (patientId ? Promise.resolve(null) : api.patients()), [patientId], 0)
  const pid = patientId || mine.data?.[0]?.patient_id
  if (!pid) {
    return (
      <div className="mx-auto min-h-screen max-w-md p-4">
        {mine.data ? <Empty>No patient linked to this account.</Empty> : <Empty>Loading…</Empty>}
        <button className="btn mt-3 w-full" onClick={onLogout}>Sign out</button>
      </div>
    )
  }
  return <PatientShell key={pid} api={api} pid={pid} session={session} preview={preview} onLogout={onLogout} />
}

const TABS = [
  { id: 'home', label: 'Home', icon: 'home', title: 'Today' },
  { id: 'visits', label: 'Visits', icon: 'calendar', title: 'Appointments', ownHeading: true },
  { id: 'meds', label: 'Medicines', icon: 'pill', title: 'My medicines', ownHeading: true },
  { id: 'health', label: 'Health', icon: 'heart', title: 'My health' },
  { id: 'profile', label: 'Profile', icon: 'user', title: 'Profile and settings' },
]
const TAB_KEY = 'cardiotwin.tab'
const readTab = () => { try { const t = sessionStorage.getItem(TAB_KEY); return TABS.some((x) => x.id === t) ? t : 'home' } catch { return 'home' } }

function PatientShell({ api, pid, session, preview, onLogout }) {
  const [tab, setTabState] = useState(readTab)
  const [sheet, setSheet] = useState(null)          // 'notifications' | 'book' | 'assistant' | 'nearby' | 'pharmacy'
  const [focus, setFocus] = useState(null)
  const setTab = (t, focus = null) => {
    setTabState(t); setFocus(focus)
    try { sessionStorage.setItem(TAB_KEY, t) } catch { /* private mode */ }
    window.scrollTo({ top: 0 })
  }

  const status = usePoll(() => api.status(pid), [pid], 4000)
  const features = usePoll(() => api.features(pid), [pid], 6000)
  const readings = usePoll(() => api.readings(pid, 'hr,spo2,resp,rhythm,steps,dose_taken', 600), [pid], 8000)
  const alerts = usePoll(() => api.alerts(pid, true), [pid], 4000)
  const ehr = usePoll(() => api.ehr(pid), [pid], 0)
  const settings = usePoll(() => api.appSettings(pid), [pid], 0)
  const notes = usePoll(() => api.notifications(pid), [pid], 15000)
  const appts = usePoll(() => api.appointments(pid), [pid], 20000)
  const meds = usePoll(() => api.medications(pid, localDay()), [pid], 30000)
  const team = usePoll(() => api.careTeam(pid), [pid], 0)
  // a caregiver sees the emergency screen unless the patient turned that off for them (Profile, My care team)
  const sharing = usePoll(() => (session.role === 'caregiver' ? api.caregiverConsent(pid) : Promise.resolve(null)), [pid], 60000)
  const carerNoEmergency = sharing.data?.[0]?.emergency === false
  useLive(api.wsUrl(pid), (m) => {
    if (m.type === 'status') status.refresh()
    if (m.type === 'alert' || m.type === 'emergency' || m.type === 'ambulance') { alerts.refresh(); status.refresh(); notes.refresh() }
  })
  useMedReminders(meds.data, settings.data)
  useWellnessReminders(api, pid, !preview && settings.data?.med_reminders !== false)

  const st = status.data
  const pv = st?.views?.phone_patient
  const open = alerts.data || []
  const fallCheck = open.find((a) => a.kind === 'FALL' && a.state === 'AWAITING_RESPONSE')
  const emergencyAlert = open.find((a) => a.severity === 'critical')
  const modal = pv?.emergency_modal || emergencyAlert?.payload?.emergency
  const simulated = !!st?.simulated
  const refreshAll = () => { alerts.refresh(); status.refresh(); readings.refresh(); notes.refresh() }
  const ro = !!preview
  const unread = notes.data?.unread || 0
  const s = settings.data
  const carer = session.role === 'caregiver'
  const name = carer ? (session.display_name || 'there').replace(/\s*\(demo\)$/, '') : s?.preferred_name || 'there'
  const ctx = { api, pid, session, ro, st, pv, features, readings, alerts, ehr, settings, notes, appts, meds, team, refreshAll, setTab, setSheet }

  return (
    <div className="mx-auto min-h-screen max-w-md pb-28">
      <header className="sticky top-0 z-20 flex items-center gap-3 bg-bg/90 px-4 pb-2 pt-3 backdrop-blur">
        {tab === 'home' ? (
          <>
            <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-accent-soft font-display text-[18px] font-extrabold text-accent">{(name[0] || '?').toUpperCase()}</span>
            <div className="min-w-0">
              <div className="text-[12.5px] text-muted">{greeting()}</div>
              <div className="flex items-center gap-1.5 truncate font-display text-[17px] font-bold leading-tight">
                {name}
                {s?.name_is_demo && !carer && <span className="tag bg-sunk text-muted" title="Fictional name for the demo patient. Change it under Profile, Settings.">demo name</span>}
              </div>
            </div>
          </>
        ) : (
          <>
            <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-surface shadow-sm"><img src="/icon.svg" alt="" className="h-7 w-7" /></span>
            {TABS.find((t) => t.id === tab)?.ownHeading
              ? <div className="font-display text-[19px] font-extrabold leading-tight">Cardio<span className="text-accent">Twin</span></div>
              : <div className="font-display text-[19px] font-bold leading-tight">{TABS.find((t) => t.id === tab)?.title}</div>}
          </>
        )}
        {preview && <span className="tag bg-accent-soft text-accent">doctor preview</span>}
        <button className="relative ml-auto flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-surface shadow-sm hover:text-accent" onClick={() => setSheet('notifications')}
          aria-label={`Notifications${unread ? `, ${unread} unread` : ''}`}>
          <Icon name="bell" size={21} />
          {unread > 0 && <span className="num absolute -right-0.5 -top-0.5 flex h-[18px] min-w-[18px] items-center justify-center rounded-full bg-accent px-1 text-[10.5px] font-bold text-on-accent">{unread > 9 ? '9+' : unread}</span>}
        </button>
      </header>

      <div className="space-y-3 px-4 pt-3 empty:hidden">
        {status.error && status.error.status !== 404 && tab === 'home' && <ErrorLine error={status.error} />}
        {modal && !carerNoEmergency && <EmergencyModal api={api} modal={modal} alert={emergencyAlert} onDone={refreshAll} simulated={simulated} onNearby={() => setSheet('nearby')} hidden={sheet === 'nearby' || sheet === 'pharmacy'} />}
      </div>
      {fallCheck && <FallCheck api={api} alert={fallCheck} onDone={refreshAll} />}

      <main className="pt-2">
        {tab === 'home' && <Home ctx={ctx} simulated={simulated} statusError={status.error} />}
        {tab === 'visits' && <Appointments ctx={ctx} />}
        {tab === 'meds' && <Medicines ctx={ctx} />}
        {tab === 'health' && <Health key={focus || 'health'} ctx={ctx} focus={focus} />}
        {tab === 'profile' && <Profile key={focus || 'menu'} ctx={ctx} onLogout={onLogout} focus={focus} />}
      </main>

      <nav className="fixed inset-x-0 bottom-0 z-30 px-3 pb-3" style={{ paddingBottom: 'calc(12px + env(safe-area-inset-bottom))' }} aria-label="Main">
        <div className="mx-auto grid max-w-md grid-cols-5 rounded-full p-1.5 shadow-[0_10px_30px_rgba(90,15,20,.28)]" style={{ background: 'var(--accent-strong)' }}>
          {TABS.map((t) => {
            const on = t.id === tab
            return (
              <button key={t.id} onClick={() => setTab(t.id)} aria-current={on ? 'page' : undefined}
                className="flex min-h-[54px] flex-col items-center justify-center gap-0.5 rounded-full text-[10.5px] transition-colors"
                style={on ? { background: 'var(--accent)', color: '#fff', fontWeight: 700 } : { color: 'rgba(255,255,255,.72)', fontWeight: 500 }}>
                <Icon name={t.icon} size={20} strokeWidth={on ? 2.2 : 1.8} />
                {t.label}
              </button>
            )
          })}
        </div>
      </nav>

      {sheet === 'notifications' && <NotificationsSheet ctx={ctx} onClose={() => setSheet(null)} />}
      {!sheet && (
        <button onClick={() => setSheet('assistant')} aria-label="Ask CardioTwin, the health assistant"
          className="fixed bottom-[92px] right-4 z-30 flex h-12 items-center gap-2 rounded-full bg-surface px-4 font-semibold text-accent shadow-[0_8px_24px_rgba(90,15,20,.18)] sm:right-[calc(50%-14rem+1rem)]"
          style={{ marginBottom: 'env(safe-area-inset-bottom)' }}>
          <Icon name="chat" size={20} /> Ask
        </button>
      )}
      {sheet === 'assistant' && <AssistantSheet ctx={ctx} onClose={() => setSheet(null)} onNearby={() => setSheet('nearby')} />}
      {(sheet === 'nearby' || sheet === 'pharmacy') && <NearbyHelpSheet ctx={ctx} alertId={emergencyAlert?.alert_id} initialMode={sheet === 'pharmacy' ? 'pharmacy' : 'hospital'} onClose={() => setSheet(null)} />}
      {sheet === 'book' && <BookingSheet ctx={ctx} onClose={() => setSheet(null)} onBooked={() => { appts.refresh(); notes.refresh(); setTab('visits') }} />}
    </div>
  )
}

// In-app medicine reminders: while the app is open, a dose that falls due (and is not marked taken) raises a phone
// notification when the browser allows it. The Home screen shows the same due doses as a banner either way.
function useMedReminders(meds, settings) {
  const fired = useRef(new Set())
  useEffect(() => {
    if (!meds || settings?.med_reminders === false) return
    const tick = () => {
      if (typeof Notification === 'undefined' || Notification.permission !== 'granted') return
      const now = minutesOf(localHm()), day = localDay()
      for (const m of meds) {
        if (!m.reminders) continue
        for (const t of m.times) {
          const key = `${m.med_id}|${day}|${t}`
          const due = now >= minutesOf(t) && now - minutesOf(t) <= 30
          if (due && !m.taken?.[t] && !fired.current.has(key)) {
            fired.current.add(key)
            try { new Notification(`Time for ${m.name}`, { body: `${m.strength || ''} · ${t}. Open CardioTwin to mark it taken.`.trim(), icon: '/icon-192.png', tag: key }) } catch { /* not allowed here */ }
          }
        }
      }
    }
    tick()
    const t = setInterval(tick, 30000)
    return () => clearInterval(t)
  }, [meds, settings?.med_reminders])
}

function Home({ ctx, simulated, statusError }) {
  const { api, session, ro, pv, st, features, readings, alerts, settings, appts, meds, team, refreshAll, setTab, setSheet } = ctx
  const s = settings.data
  const patientName = s?.preferred_name
  const carer = session.role === 'caregiver'
  const next = (appts.data || []).filter((a) => a.upcoming).sort((a, b) => a.start_at.localeCompare(b.start_at))[0]
  const due = s?.med_reminders === false ? [] : dueDoses(meds.data)
  const doctor = team.data?.doctors?.[0]
  const critical = pv?.emergency_level === 'critical' || st?.emergency_state === 'TRIGGERED' || (alerts.data || []).some((x) => x.severity === 'critical')
  const today = new Date().toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' })
  const markTaken = async (m, t) => { try { await api.medTaken(m.med_id, localDay(), t); meds.refresh() } catch { /* shown on the Medicines tab */ } }

  return (
    <div className="space-y-4 px-4">
      <div>
        <h1 className="font-display text-[28px] font-extrabold leading-tight">Your heart, <span className="text-accent">today</span></h1>
        <div className="mt-0.5 text-[13px] text-muted">{today}{carer && patientName && <> · looking after {patientName}</>}</div>
      </div>

      {simulated && (
        <div className="flex items-center gap-2 text-[12px] text-muted">
          <SimTag /> <span>Virtual watch replaying an ICU record, not real watch data.</span>
        </div>
      )}
      {statusError?.status === 404 && <Empty>Waiting for your watch. Readings appear here a few seconds after it starts sending.</Empty>}

      {pv && <RiskCard pv={pv} st={st} onContact={() => setTab('profile', 'team')} />}

      {due.length > 0 && (
        <section className="card p-4">
          <div className="mb-2 flex items-center gap-2"><Icon name="clock" size={18} className="text-accent" /><b className="text-[15px]">Medicine reminder</b></div>
          <div className="space-y-1.5">
            {due.slice(0, 3).map(({ med, time, late }) => (
              <div key={med.med_id + time} className="flex items-center gap-3 rounded-2xl bg-sunk px-3 py-2 text-[14px]">
                <span className="num w-12 font-semibold">{time}</span>
                <span className="min-w-0 flex-1"><b>{med.name}</b> <span className="text-muted">{med.strength}</span>{late && <span className="block text-[12px] text-high">Overdue</span>}</span>
                <button className="btn btn-primary min-h-9" disabled={ro} onClick={() => markTaken(med, time)}>Taken</button>
              </div>
            ))}
          </div>
        </section>
      )}

      <section className="brand-bar rounded-[var(--radius)] p-4 shadow-[0_10px_30px_rgba(122,14,24,.25)]">
        <div className="mb-3 flex items-center justify-between">
          <span className="text-[13px] font-semibold opacity-90">Next appointment</span>
          <span className="tag" style={{ background: 'rgba(255,255,255,.18)', color: '#fff' }} title="Demo data stored in CardioTwin only. Nothing is sent to a real clinic.">Demo</span>
        </div>
        {next ? (
          <button className="flex w-full items-center gap-3 text-left" onClick={() => setTab('visits')}>
            <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl bg-white text-accent"><Icon name="calendar" size={22} /></span>
            <div className="min-w-0 flex-1">
              <div className="font-display text-[18px] font-bold leading-tight">{apptDay(next.start_at)}, {apptTime(next.start_at)}</div>
              <div className="truncate text-[13px] opacity-90">{next.doctor_name} · {next.mode_label}</div>
            </div>
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-white text-accent"><Icon name="chevron" size={18} /></span>
          </button>
        ) : (
          <div className="flex items-center gap-3">
            <p className="min-w-0 flex-1 text-[14px] opacity-90">No upcoming appointment{doctor ? ` with ${doctor.name}` : ''}.</p>
            <button className="btn border-white bg-white text-accent hover:bg-white" disabled={ro} onClick={() => setSheet('book')}>Book</button>
          </div>
        )}
      </section>

      <div className="grid grid-cols-4 gap-2">
        {[
          { label: 'Book visit', icon: 'calendar', on: () => setSheet('book'), dis: ro },
          { label: 'How I feel', icon: 'note', on: () => setTab('health', 'checkin') },
          { label: 'Nearby help', icon: 'clinic', on: () => setSheet('nearby') },
          // The red Emergency shortcut is only for a patient classified critical; everyone else gets "Contact doctor".
          critical
            ? { label: 'Emergency', icon: 'sos', on: () => setTab('profile', 'contacts'), danger: true }
            : { label: 'Contact doctor', icon: 'phone', on: () => setTab('profile', 'team') },
        ].map((q) => (
          <button key={q.label} onClick={q.on} disabled={q.dis} className="flex flex-col items-center gap-1.5 py-1 text-[12px] font-semibold disabled:opacity-50">
            <span className="flex h-14 w-14 items-center justify-center rounded-full shadow-[var(--shadow)]" style={{ background: q.danger ? 'var(--accent)' : 'var(--surface)', color: q.danger ? 'var(--on-accent)' : 'var(--accent)' }}><Icon name={q.icon} size={22} /></span>
            {q.label}
          </button>
        ))}
      </div>

      <VitalsTiles features={features.data} readings={readings.data} badges={pv?.freshness_badges} />
      <AlertList api={api} alerts={(alerts.data || []).filter((a) => a.severity !== 'critical' && !(a.kind === 'FALL' && a.state === 'AWAITING_RESPONSE'))} onChange={refreshAll} />

      <Disclaimer caregiver={carer} />
    </div>
  )
}

const SCREEN_TEXT = { LOW: 'Low', MODERATE: 'Moderate', HIGH: 'High', EMERGENCY: 'Emergency' }

function RiskCard({ pv, st, onContact }) {
  const k = tierKey(pv.tier, st.emergency_state, st.status)
  const t = TIER[k]
  const title = SCREEN_TEXT[pv.screen] || (pv.screen ? pv.screen.replaceAll('_', ' ').toLowerCase().replace(/^./, (c) => c.toUpperCase()) : t.label)
  return (
    <section className="card relative overflow-hidden p-4 pl-5">
      <span className="absolute inset-y-0 left-0 w-1.5" style={{ background: t.fg }} aria-hidden="true" />
      <div className="flex items-center justify-between">
        <span className="text-[13px] font-semibold text-muted">Next 24 hours</span>
        <span className="text-[11.5px] text-muted">synced {hhmm(pv.last_sync)}</span>
      </div>
      <div className="mt-1 flex items-center gap-2">
        <span className="h-3 w-3 rounded-full" style={{ background: t.fg }} aria-hidden="true" />
        <span className="font-display text-[30px] font-extrabold leading-none" style={{ color: t.fg }}>{title}</span>
      </div>
      <p className="mt-2 text-[14.5px] leading-snug">{pv.message}</p>
      {pv.review_banner && <div className="mt-3 rounded-2xl bg-sunk px-3 py-2 text-[13.5px]">{typeof pv.review_banner === 'string' ? pv.review_banner : pv.review_banner.text || pv.review_banner.title}</div>}
      {pv.care_action && pv.emergency_level !== 'critical' && (
        <div className="mt-3 flex items-center gap-3">
          <p className="min-w-0 flex-1 text-[13px] text-muted">{pv.care_action.text}</p>
          <button className="btn shrink-0 font-semibold text-accent" style={{ borderColor: 'var(--accent)' }} onClick={onContact}>{pv.care_action.label}</button>
        </div>
      )}
      <div className="mt-2 text-[11.5px] text-muted">Checked {hhmm(pv.evaluated_at)}</div>
    </section>
  )
}

function VitalsTiles({ features, readings, badges }) {
  const by = useMemo(() => {
    const m = {}
    for (const r of readings || []) (m[r.type] ||= []).push(r)
    return m
  }, [readings])
  const s = features?.signals || {}
  const rhythm = (by.rhythm || []).at(-1)
  const steps = (by.steps || []).slice(-12).reduce((a, r) => a + (r.value_num || 0), 0)
  const tiles = [
    { k: 'hr', label: 'Heart rate', unit: 'bpm', badge: 'hr' },
    { k: 'spo2', label: 'Oxygen', unit: '%', badge: 'spo2' },
    { k: 'resp', label: 'Breathing', unit: '/min', badge: 'rr' },
  ]
  return (
    <Card title="Your readings">
      <div className="grid grid-cols-3 gap-2">
        {tiles.map((t) => {
          const b = badges?.[t.badge]
          return (
            <div key={t.k} className="rounded-2xl bg-sunk p-2.5">
              <div className="text-[12px] text-muted">{t.label}</div>
              <div className="num text-[22px] font-semibold leading-tight">{num(s[t.k]?.last?.value, 0)}<span className="ml-0.5 text-[11px] font-normal text-muted">{t.unit}</span></div>
              <Sparkline values={(by[t.k] || []).slice(-36).map((r) => r.value_num)} height={24} />
              <div className="text-[11px]" style={{ color: b?.status === 'FRESH' ? 'var(--low)' : 'var(--high)' }}>{b ? `${b.status.toLowerCase()} · ${Math.round(b.minutes_old)} min` : '–'}</div>
            </div>
          )
        })}
      </div>
      <div className="mt-2 grid grid-cols-2 gap-2 text-[13px]">
        <div className="rounded-2xl bg-sunk px-3 py-2"><div className="text-[12px] text-muted">Rhythm</div><div className="font-semibold">{rhythm ? RHYTHM[rhythm.value_text] || rhythm.value_text : '–'}</div></div>
        <div className="rounded-2xl bg-sunk px-3 py-2"><div className="text-[12px] text-muted">Steps, last hour</div><div className="num font-semibold">{by.steps?.length ? steps : '–'}</div></div>
      </div>
    </Card>
  )
}

function FallCheck({ api, alert, onDone }) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const secs = alert.payload?.response_window_seconds || 30
  const started = parseTs(alert.created_at)?.getTime() || Date.now()
  const [left, setLeft] = useState(secs)
  useEffect(() => {
    const t = setInterval(() => setLeft(Math.max(0, Math.round(secs - (Date.now() - started) / 1000))), 500)
    return () => clearInterval(t)
  }, [secs, started])
  const answer = async (a) => { setBusy(true); setErr(null); try { await api.fallResponse(alert.alert_id, a); onDone() } catch (e) { setErr(e) } finally { setBusy(false) } }
  return (
    <div className="fixed inset-0 z-[45] flex items-end justify-center bg-black/40 p-3 sm:items-center">
    <section role="alertdialog" aria-modal="true" aria-label="Fall detected" className="w-full max-w-md rounded-3xl border-2 bg-surface p-5 shadow-xl" style={{ borderColor: 'var(--high)' }}>
      <div className="font-display text-2xl font-extrabold" style={{ color: 'var(--high)' }}>Fall detected. Are you OK?</div>
      <p className="mt-1 text-[14px]">{alert.payload?.phone?.text}</p>
      <div className="num mt-1 text-[13px] text-muted">{left > 0 ? `${left} s to answer` : 'Waiting for the emergency check…'}</div>
      <div className="mt-3 grid grid-cols-2 gap-2">
        <button className="btn min-h-12 text-[16px]" disabled={busy} onClick={() => answer('im_ok')}>I'm OK</button>
        <button className="btn btn-emg min-h-12 text-[16px]" disabled={busy} onClick={() => answer('need_help')}>I need help</button>
      </div>
      <ErrorLine error={err} />
    </section>
    </div>
  )
}

// Full-screen emergency screen. "Call 112" asks for confirmation, then only opens the phone dialler (tel:112);
// the person presses Call themselves. Nothing is dialled or dispatched automatically.
function EmergencyModal({ api, modal, alert, onDone, simulated, onNearby, hidden }) {
  const [confirming, setConfirming] = useState(false)
  const [minimised, setMinimised] = useState(false)
  const [err, setErr] = useState(null)
  const callRef = useRef(null)
  const call = (modal.actions || []).find((a) => a.id === 'call_emergency') || { number: '112', uri: 'tel:112', label: 'Call 112' }
  useEffect(() => { if (!minimised) callRef.current?.focus() }, [minimised, confirming])
  useEffect(() => {
    if (minimised) return
    try { navigator.vibrate?.([600, 300, 600, 300, 600]) } catch { /* not supported */ }
  }, [minimised])
  const record = async (action, body) => {
    if (!alert) return
    try { await api.alertAction(alert.alert_id, action, body) } catch (e) { setErr(e) }
  }

  if (minimised || hidden) {
    if (hidden) return null
    return (
      <button className="flex w-full items-center gap-3 rounded-2xl p-4 text-left" style={{ background: 'var(--emg)', color: 'var(--on-emg)' }} onClick={() => setMinimised(false)}>
        <span className="text-2xl" aria-hidden="true">⚠</span>
        <span className="min-w-0 flex-1"><b className="block font-display">Emergency still open</b><span className="text-[13px] opacity-90">{modal.reason}</span></span>
        <span className="text-[13px] underline">Open</span>
      </button>
    )
  }
  return (
    <div role="alertdialog" aria-modal="true" aria-live="assertive" aria-label={modal.accessibility?.aria_label || `${modal.title}. ${modal.reason}`}
      className="fixed inset-0 z-50 overflow-y-auto" style={{ background: 'var(--emg)', color: 'var(--on-emg)' }}>
      <div className="mx-auto flex min-h-full max-w-md flex-col px-5 pb-8 pt-10">
        <div className="text-5xl" aria-hidden="true">{modal.icon_glyph || '⚠'}</div>
        <h1 className="mt-2 font-display text-3xl font-extrabold">{modal.title}</h1>
        {simulated && <div className="mt-1"><span className="tag" style={{ background: 'rgba(255,255,255,.2)', color: '#fff' }}>Simulated</span></div>}
        <p className="mt-3 text-[18px] font-semibold leading-snug">{modal.headline}</p>
        <p className="mt-2 text-[15px] opacity-95">{modal.reason}</p>
        <ul className="mt-4 list-disc space-y-1.5 pl-5 text-[15px]">{(modal.instructions || []).map((i, n) => <li key={n}>{i}</li>)}</ul>

        <div className="mt-auto space-y-3 pt-8">
          {!confirming ? (
            <button ref={callRef} className="flex min-h-14 w-full items-center justify-center rounded-full bg-white text-[18px] font-bold" style={{ color: 'var(--emg)' }} onClick={() => setConfirming(true)}>
              {call.label}
            </button>
          ) : (
            <div className="rounded-2xl bg-white p-4 text-ink">
              <p className="text-[14px]">{call.confirm_text || `This opens your phone's dialler with ${call.number} filled in. You press Call yourself.`}</p>
              <div className="mt-3 grid grid-cols-2 gap-2">
                <button className="btn" onClick={() => setConfirming(false)}>Cancel</button>
                <a ref={callRef} href={call.uri || `tel:${call.number}`} className="btn btn-emg" onClick={() => record('user_action', { user_action: 'opened_dialer_112' })}>Open dialler</a>
              </div>
              <p className="mt-2 text-[11.5px] text-muted">Dispatch status: {call.dispatch_status || 'NOT_DISPATCHED'}. Opening the dialler is not an ambulance dispatch.</p>
            </div>
          )}
          <div className="grid grid-cols-2 gap-2">
            <button className="min-h-12 rounded-full border border-white/70 text-[15px] font-semibold" onClick={async () => { await record('acknowledge'); setMinimised(true); onDone() }}>I have seen this</button>
            <button className="min-h-12 rounded-full border border-white/70 text-[15px] font-semibold" onClick={() => record('user_action', { user_action: 'contact_care_team' })}>Message my care team</button>
          </div>
          {onNearby && <button className="min-h-12 w-full rounded-full border border-white/70 text-[15px] font-semibold" onClick={onNearby}>Nearby hospitals, medical stores and ambulance</button>}
          {err && <div className="rounded-md bg-white/20 px-3 py-2 text-[13px]">{err.message}</div>}
          {modal.persistent_until && <p className="text-[12px] opacity-85">Stays on screen until {modal.persistent_until}.</p>}
        </div>
      </div>
    </div>
  )
}

function AlertList({ api, alerts, onChange }) {
  if (!alerts.length) return null
  return (
    <Card title="For your attention">
      <div className="space-y-2">
        {alerts.map((a) => (
          <div key={a.alert_id} className="rounded-2xl bg-sunk p-3">
            <div className="flex items-center gap-2"><b>{a.payload?.watch?.title || a.title}</b>{a.simulated && <SimTag />}</div>
            {a.message && <p className="mt-1 text-[13px] text-muted">{a.message}</p>}
            {a.payload?.not_a_diagnosis && <p className="mt-1 text-[12px] text-muted">{a.payload.not_a_diagnosis}</p>}
            {['NEW', 'DISPLAYED'].includes(a.state) && <button className="btn mt-2" onClick={() => api.alertAction(a.alert_id, 'acknowledge').then(onChange).catch(() => {})}>Got it</button>}
          </div>
        ))}
      </div>
    </Card>
  )
}
