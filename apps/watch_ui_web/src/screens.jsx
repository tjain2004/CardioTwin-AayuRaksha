import { useEffect, useState } from 'react'
import { fmt, hhmm, levelColor, localHm } from './format'

// The Wear OS app's screens (wear_os_watch/app/src/main/java/com/cardiotwin/wear/ui/screens), drawn in HTML.
// Every word and colour comes from the backend's payloads (views.watch, alerts), as on the watch.

export function Sim({ show = true }) {
  return show ? <span className="sim">SIMULATED</span> : null
}

function Eyebrow({ children, color }) {
  return <div className="eyebrow" style={color ? { color } : undefined}>{children}</div>
}

/** Screen 1: the latest readings and where they are going. */
export function OverviewScreen({ vitals, watch, simulated, live, sim, onOpenStatus }) {
  const hr = vitals.hr
  const color = levelColor(watch?.tier)
  return (
    <div className="face">
      <div className="row" style={{ gap: '1.5cqw', justifyContent: 'center' }}>
        <span style={{ fontWeight: 600, fontSize: '3.8cqw' }}>CardioTwin</span><Sim show={simulated} />
      </div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: '1cqw' }}>
        <span style={{ color: 'var(--emg)', fontSize: '6cqw' }}>♥</span>
        <span className="big">{fmt(hr?.value_num)}</span>
        <span className="t-body">bpm</span>
      </div>
      <div className="t-tiny">Heart rate · {hr ? `sim ${hhmm(hr.ts)}` : 'no reading yet'}</div>
      <div className="metrics">
        <Metric label="SpO2" value={fmt(vitals.spo2?.value_num)} unit="%" />
        <Metric label="Breathing" value={fmt(vitals.resp?.value_num)} unit="/min" />
        <Metric label="HRV" value={fmt(vitals.hrv_rmssd?.value_num)} unit="ms" />
      </div>
      <button className="chip" style={{ marginTop: '1.5cqw' }} onClick={onOpenStatus}>
        <span className="g" style={{ color }}>{watch?.icon_glyph || '•'}</span>
        <span style={{ minWidth: 0 }}>
          <div className="l" style={{ color }}>{watch?.title || 'Status'}</div>
          <div className="s">{watch?.text || 'Waiting for the server'}</div>
        </span>
      </button>
      <div className="t-tiny" style={{ color: live ? 'var(--low)' : 'var(--mod)', fontSize: '3cqw' }}>● {live ? 'Live from backend' : 'Connecting…'}</div>
      <div className="t-tiny">{sim?.running ? `Virtual watch · ${sim.readings_sent} readings sent` : 'Virtual watch stopped'}</div>
    </div>
  )
}

function Metric({ label, value, unit }) {
  return (
    <div className="metric">
      <Eyebrow>{label}</Eyebrow>
      <div><b>{value}</b><small>{unit}</small></div>
    </div>
  )
}

/** Screen 2: the twin's status exactly as the risk engine words it for the watch. Never a score number. */
export function StatusScreen({ watch, simulated }) {
  const w = watch || { title: 'Connecting', text: 'Waiting for first update', icon_glyph: '⟳' }
  const color = w.type === 'EMERGENCY_WATCH' ? 'var(--emg)' : w.type === 'FALL_CHECK' ? 'var(--high)' : levelColor(w.tier)
  return (
    <>
      <div className="ring" style={{ borderColor: color }} />
      <div className="face" style={{ padding: '16cqw' }}>
        <Eyebrow>Next 24 hours</Eyebrow>
        <div style={{ fontSize: '8cqw', color: w.tier || w.type !== 'WATCH_FACE' ? color : 'var(--ink2)' }}>{w.icon_glyph || '•'}</div>
        <div className="t-title" style={{ fontSize: '6.4cqw' }}>{w.title}</div>
        <div className="t-body">{w.text}</div>
        <div className="t-tiny" style={{ marginTop: '1.5cqw' }}>
          {w.last_sync_minutes != null ? `Last sync ${Math.round(w.last_sync_minutes)} min ago` : 'Waiting for the server'}
        </div>
        <Sim show={simulated} />
      </div>
    </>
  )
}

/** Screen 3: open twin alerts (possible AF, long QTc, dose over limit, risk alerts, fall results). */
export function AlertsScreen({ alerts, onSeen }) {
  return (
    <div className="face scroll">
      <div style={{ fontWeight: 600, fontSize: '3.8cqw', marginBottom: '1cqw' }}>Twin alerts</div>
      {alerts.length === 0 && (
        <div style={{ marginTop: '6cqw' }}>
          <div style={{ color: 'var(--low)', fontSize: '7cqw' }}>✓</div>
          <div className="t-body">No open alerts</div>
        </div>
      )}
      {alerts.map((a) => {
        const color = levelColor(a.severity)
        const w = a.payload?.emergency?.watch || a.payload?.watch
        return (
          <button key={a.alert_id} className="card" onClick={() => onSeen(a.alert_id)}>
            <span className="bar" style={{ background: color }} />
            <span style={{ minWidth: 0 }}>
              <span className="row" style={{ gap: '1.4cqw' }}><Eyebrow color={color}>{a.severity}</Eyebrow><Sim show={a.simulated} /></span>
              <div className="ttl">{w?.title || a.title}</div>
              <div className="msg">{w?.text || a.message}</div>
            </span>
          </button>
        )
      })}
      {alerts.length > 0 && <div className="t-tiny" style={{ marginTop: '1cqw' }}>Tap an alert to hide it. Details are on the phone.</div>}
    </div>
  )
}

/** Screen 4: medication reminder. "Taken" sends a dose_taken reading; the watch never suggests a dose. */
export function MedicationScreen({ due, next, onTaken, onSnooze, onSkip, onDemo, busy }) {
  if (due) {
    return (
      <div className="face">
        <Eyebrow color="var(--accent)">Time for your medicine</Eyebrow>
        <div style={{ fontSize: '7cqw' }}>💊</div>
        <div className="t-title">{due.med.label}</div>
        <div className="t-body" style={{ fontSize: '3.2cqw' }}>Due {localHm(due.at)}</div>
        <button className="wbtn" disabled={busy} style={{ background: 'var(--low)', color: 'var(--bg)', width: '62%', marginTop: '1cqw' }} onClick={onTaken}>Taken</button>
        <div className="row" style={{ gap: '1.5cqw', justifyContent: 'center' }}>
          <button className="chip secondary" style={{ width: 'auto' }} onClick={onSnooze}>Snooze 10 min</button>
          <button className="chip secondary" style={{ width: 'auto' }} onClick={onSkip}>Skip</button>
        </div>
        <div className="t-tiny">Reminders only. Ask your doctor before changing a dose.</div>
      </div>
    )
  }
  return (
    <div className="face">
      <div style={{ fontWeight: 600, fontSize: '3.8cqw' }}>Medicines</div>
      <Eyebrow>Next dose</Eyebrow>
      <div className="big" style={{ color: 'var(--accent)', fontSize: '10cqw' }}>{next ? localHm(next.at) : '--:--'}</div>
      <div className="t-body">{next?.med.label || 'Nothing scheduled'}</div>
      <button className="chip secondary" style={{ marginTop: '2cqw' }} onClick={onDemo}>Demo: remind now</button>
      <div className="t-tiny">Reminders only. Ask your doctor before changing a dose.</div>
    </div>
  )
}

/** Ticks every 250 ms while `on`, for the fall countdown. */
function useNow(on) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    if (!on) return
    const t = setInterval(() => setNow(Date.now()), 250)
    return () => clearInterval(t)
  }, [on])
  return now
}

/**
 * Screen 5: "Fall detected. Are you OK?" with the backend's countdown. "I'm OK" closes it; "Need help", or no answer
 * before the ring runs out, makes the backend raise the emergency. Without a fall it offers the demo trigger.
 */
export function FallScreen({ check, answered, onOk, onHelp, onSimulate, canSimulate, busy }) {
  const now = useNow(!!check)
  if (!check) {
    return (
      <div className="face">
        <div style={{ fontWeight: 600, fontSize: '3.8cqw' }}>Fall check</div>
        <div style={{ color: 'var(--low)', fontSize: '8cqw' }}>✓</div>
        <div className="t-body">No fall detected</div>
        <button className="chip secondary" style={{ marginTop: '2cqw' }} disabled={!canSimulate || busy} onClick={onSimulate}>Simulate fall</button>
        <div className="t-tiny">{canSimulate ? 'Sends a fall from the virtual watch.' : 'Start the virtual watch first.'}</div>
      </div>
    )
  }
  const total = (check.payload?.response_window_seconds || 30) * 1000
  const deadline = Date.parse(check.deadline_at) || now + total
  const left = Math.max(0, deadline - now)
  const color = left > 10000 ? 'var(--high)' : 'var(--emg)'
  const R = 47
  const C = 2 * Math.PI * R
  return (
    <>
      <svg className="countdown" viewBox="0 0 100 100" aria-hidden="true">
        <circle cx="50" cy="50" r={R} fill="none" stroke="var(--sunk)" strokeWidth="3.6" />
        <circle cx="50" cy="50" r={R} fill="none" stroke={color} strokeWidth="3.6" strokeLinecap="round"
          strokeDasharray={C} strokeDashoffset={C * (1 - left / total)} />
      </svg>
      <div className="face" style={{ padding: '15cqw' }} role="alertdialog" aria-label="Fall detected">
        {answered === 'need_help' ? (
          <>
            <div style={{ color: 'var(--emg)', fontSize: '7cqw' }}>⚠</div>
            <div className="t-title">Help requested</div>
            <div className="t-body">Alerting your care team…</div>
          </>
        ) : (
          <>
            <Sim show={check.simulated} />
            <div className="t-title" style={{ fontSize: '5.6cqw' }}>Fall detected</div>
            <div className="t-body">Are you OK?</div>
            <div className="big" style={{ color, fontSize: '9cqw' }}>{left > 0 ? Math.ceil(left / 1000) : '…'}</div>
            <div className="row" style={{ gap: '3cqw', justifyContent: 'center' }}>
              <button className="round" disabled={busy} style={{ background: 'var(--low)', color: 'var(--bg)' }} onClick={onOk}>I'm<br />OK</button>
              <button className="round" disabled={busy} style={{ background: 'var(--emg)', color: 'var(--on-emg)' }} onClick={onHelp}>Need<br />help</button>
            </div>
            <div className="t-tiny">No answer = emergency alert</div>
          </>
        )}
      </div>
    </>
  )
}

/** Full-screen emergency (backend EMERGENCY_WATCH): an unanswered fall, "I need help", or an engine emergency rule. */
export function EmergencyScreen({ view, simulated, onAction, onHide }) {
  return (
    <div className="face emergency" role="alertdialog" aria-label="Emergency">
      <Sim show={simulated} />
      <div style={{ fontSize: '8cqw' }}>{view.icon_glyph || '⚠'}</div>
      <div className="t-title" style={{ fontSize: '7cqw', fontWeight: 800 }}>{view.title || 'EMERGENCY'}</div>
      <div className="t-body">{view.text}</div>
      <button className="wbtn" style={{ background: 'var(--on-emg)', color: 'var(--emg)', width: '72%', marginTop: '1.5cqw' }} onClick={onAction}>
        {view.action?.label || 'Open phone'}
      </button>
      <button className="chip secondary" style={{ width: 'auto', background: 'rgba(0,0,0,.25)', color: 'var(--on-emg)' }} onClick={onHide}>Hide</button>
      <div className="t-tiny">Nothing is dispatched automatically.</div>
    </div>
  )
}
