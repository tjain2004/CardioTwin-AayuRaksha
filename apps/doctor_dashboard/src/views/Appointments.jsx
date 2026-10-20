import { useState } from 'react'
import { usePoll } from '../hooks'
import { Card, Empty, ErrorLine, SimTag } from '../components/ui'
import { apptDay, apptTime, dayTime } from '../format'

// Appointments and what the patient keeps in the phone app (medicine list, check-ins). Bookings are DEMO data stored in
// CardioTwin: the patient books in the phone app, the doctor sees them here; nothing goes to a real calendar or clinic.
const DEMO = <span className="tag" style={{ color: 'var(--accent)', background: 'var(--accent-soft)' }} title="Demo booking stored in CardioTwin only">Demo</span>
const STATUS = { BOOKED: ['Booked', 'var(--low)', 'var(--low-soft)'], COMPLETED: ['Done', 'var(--muted)', 'var(--sunk)'], CANCELLED: ['Cancelled', 'var(--high)', 'var(--high-soft)'], NO_SHOW: ['No-show', 'var(--high)', 'var(--high-soft)'] }
const SYMPTOM = { chest_pain: 'chest pain', pain_spreads_arm_jaw_back: 'pain spreading to arm/jaw/back', breathless: 'breathless', cold_sweat: 'cold sweat', nausea: 'nausea', dizzy_or_fainted: 'dizzy/fainted' }

function StatusTag({ s }) {
  const [l, fg, bg] = STATUS[s] || STATUS.BOOKED
  return <span className="tag" style={{ color: fg, background: bg }}>{l}</span>
}

// Sidebar list: the signed-in doctor's upcoming clinic across all patients.
export function ClinicList({ api, onSelect, selected }) {
  const today = new Date().toISOString().slice(0, 10)
  const list = usePoll(() => api.clinicAppointments(today), [], 15000)
  const rows = (list.data || []).filter((a) => a.status === 'BOOKED')
  return (
    <div className="space-y-1.5">
      <div className="eyebrow flex items-center gap-2">Upcoming appointments ({rows.length}) {DEMO}</div>
      <ErrorLine error={list.error} />
      {!rows.length && list.data && <div className="text-[12.5px] text-muted">None booked.</div>}
      {rows.slice(0, 8).map((a) => (
        <button key={a.appointment_id} onClick={() => onSelect(a.patient_id)} className="card block w-full p-2.5 text-left text-[12.5px]"
          style={a.patient_id === selected ? { borderColor: 'var(--accent)' } : undefined}>
          <div className="flex justify-between gap-2"><b className="num">{apptDay(a.start_at)} {apptTime(a.start_at)}</b><span className="text-muted">{a.mode_label}</span></div>
          <div className="num text-muted">{a.patient_id}</div>
          <div className="truncate">{a.reason}</div>
        </button>
      ))}
    </div>
  )
}

export function PatientAppointments({ api, pid }) {
  const appts = usePoll(() => api.appointments(pid), [pid], 10000)
  const meds = usePoll(() => api.medications(pid), [pid], 30000)
  const checkins = usePoll(() => api.checkins(pid, 8), [pid], 15000)
  const [err, setErr] = useState(null)
  const [note, setNote] = useState({})
  const list = appts.data || []
  const upcoming = list.filter((a) => a.upcoming).sort((a, b) => a.start_at.localeCompare(b.start_at))
  const past = list.filter((a) => !a.upcoming)
  const act = async (fn) => { setErr(null); try { await fn(); appts.refresh() } catch (e) { setErr(e) } }

  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <Card eyebrow="Appointments" title={`${upcoming.length} upcoming`} right={DEMO}>
        <p className="mb-3 text-[12px] text-muted">Booked by the patient (or caregiver) in the phone app. Demo data: stored in CardioTwin only, not sent to a real calendar or clinic. Clinic time.</p>
        <ErrorLine error={err} />
        <div className="space-y-2">
          {upcoming.map((a) => (
            <div key={a.appointment_id} className="rounded-lg border border-line p-3 text-[13px]">
              <div className="flex flex-wrap items-center gap-2">
                <b className="num text-[14px]">{apptDay(a.start_at)}, {apptTime(a.start_at)}</b><StatusTag s={a.status} /><span className="text-muted">{a.mode_label} · {a.minutes} min</span>
              </div>
              <div className="mt-1">{a.reason}</div>
              <div className="text-[12px] text-muted">Booked by {a.booked_by_name || '–'} · {dayTime(a.created_at)} UTC{a.history?.some((h) => h.event === 'RESCHEDULED') ? ' · moved by patient' : ''}</div>
              <div className="mt-2 flex flex-wrap gap-2">
                <input className="input min-w-0 flex-1" placeholder="Visit note for the patient (optional)" value={note[a.appointment_id] || ''} onChange={(e) => setNote({ ...note, [a.appointment_id]: e.target.value })} />
                <button className="btn" onClick={() => act(() => api.completeAppointment(a.appointment_id, { note: note[a.appointment_id] || undefined }))}>Mark done</button>
                <button className="btn" onClick={() => act(() => api.completeAppointment(a.appointment_id, { no_show: true }))}>No-show</button>
                <button className="btn" style={{ color: 'var(--high)' }} onClick={() => act(() => api.cancelAppointment(a.appointment_id, 'Cancelled by the clinic'))}>Cancel</button>
              </div>
            </div>
          ))}
          {!upcoming.length && <Empty>No upcoming appointments.</Empty>}
        </div>
        {past.length > 0 && (
          <details className="mt-3 text-[13px]">
            <summary className="cursor-pointer text-muted">Past and cancelled ({past.length})</summary>
            <div className="mt-2 space-y-1">
              {past.map((a) => (
                <div key={a.appointment_id} className="flex flex-wrap items-center gap-2 border-b border-line py-1">
                  <span className="num">{apptDay(a.start_at)} {apptTime(a.start_at)}</span><StatusTag s={a.status} /><span className="min-w-0 flex-1 truncate text-muted">{a.reason}{a.note ? ` · ${a.note}` : ''}</span>
                </div>
              ))}
            </div>
          </details>
        )}
      </Card>

      <Card eyebrow="From the patient app" title="Medicines and check-ins">
        <div className="mb-1 text-[13px] font-semibold">Medicine list and reminder times</div>
        {(meds.data || []).map((m) => (
          <div key={m.med_id} className="flex justify-between gap-2 border-b border-line py-1 text-[13px]">
            <span><b>{m.name}</b> {m.strength} <span className="text-muted">({m.source === 'demo' ? 'demo list' : `added by ${m.source}`})</span></span>
            <span className="num text-muted">{m.times.join(', ') || '–'}</span>
          </div>
        ))}
        {meds.data && !meds.data.length && <div className="text-[13px] text-muted">None entered.</div>}
        <div className="mb-1 mt-4 text-[13px] font-semibold">Recent symptom check-ins</div>
        {(checkins.data || []).map((c) => (
          <div key={c.id} className="flex flex-wrap items-center gap-2 border-b border-line py-1 text-[13px]">
            <span className="num text-muted">{dayTime(c.created_at)}</span>
            <span className="min-w-0 flex-1">{c.symptoms.length ? c.symptoms.map((s) => SYMPTOM[s] || s).join(', ') : 'no symptoms'}{c.note ? ` · "${c.note}"` : ''}</span>
            {c.emergency_state && c.emergency_state !== 'NOT_TRIGGERED' && <span className="tag" style={{ background: 'var(--emg)', color: 'var(--on-emg)' }}>emergency</span>}
            <SimTag show={c.simulated} />
          </div>
        ))}
        {checkins.data && !checkins.data.length && <div className="text-[13px] text-muted">No check-ins yet.</div>}
      </Card>
    </div>
  )
}
