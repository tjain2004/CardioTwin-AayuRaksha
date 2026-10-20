import { useEffect, useMemo, useState } from 'react'
import { usePoll } from '../hooks'
import { Card, DemoTag, Empty, ErrorLine, Icon, Sheet } from '../components/ui'
import { apptDay, apptParts, apptTime, localDay } from '../format'

// Appointments with the patient's doctor. DEMO: a booking is stored in CardioTwin and shows on the doctor dashboard;
// nothing is sent to a real clinic or calendar, and there is no payment.
const DEMO_LINE = 'Demo booking. It is saved in CardioTwin and your doctor sees it on the dashboard, but no real appointment is made and nothing is sent to a clinic.'
const STATUS = {
  BOOKED: { label: 'Booked', fg: 'var(--low)', bg: 'var(--low-soft)' },
  COMPLETED: { label: 'Done', fg: 'var(--muted)', bg: 'var(--sunk)' },
  CANCELLED: { label: 'Cancelled', fg: 'var(--high)', bg: 'var(--high-soft)' },
  NO_SHOW: { label: 'Missed', fg: 'var(--high)', bg: 'var(--high-soft)' },
}
const MODES = [
  { id: 'in_person', label: 'At the clinic', icon: 'clinic' },
  { id: 'video', label: 'Video call', icon: 'video' },
  { id: 'phone', label: 'Phone call', icon: 'phone' },
]
const REASONS = ['Routine heart check-up', 'Follow-up after hospital stay', 'New or worse symptoms', 'Question about my medicines', 'Discuss test results']

export function DateBlock({ s, muted }) {
  const p = apptParts(s)
  return (
    <div className="flex h-14 w-14 shrink-0 flex-col items-center justify-center rounded-xl" style={{ background: muted ? 'var(--sunk)' : 'var(--accent-soft)', color: muted ? 'var(--muted)' : 'var(--accent)' }}>
      <span className="text-[10.5px] font-semibold uppercase">{p.mon}</span>
      <span className="font-display text-[20px] font-extrabold leading-none">{p.day}</span>
      <span className="text-[10px]">{p.wd}</span>
    </div>
  )
}

export default function Appointments({ ctx }) {
  const { api, ro, appts, team, notes, setSheet } = ctx
  const [moving, setMoving] = useState(null)
  const list = appts.data || []
  const upcoming = list.filter((a) => a.upcoming).sort((a, b) => a.start_at.localeCompare(b.start_at))
  const past = list.filter((a) => !a.upcoming)
  const doctor = team.data?.doctors?.[0]
  const changed = () => { appts.refresh(); notes.refresh() }

  return (
    <div className="space-y-3 px-4">
      <div className="flex items-end justify-between gap-3">
        <div>
          <h1 className="font-display text-[26px] font-extrabold leading-tight">My <span className="text-accent">appointments</span></h1>
          <div className="text-[13px] text-muted">{doctor ? `${doctor.name} · ${doctor.clinic}` : 'Your care team'}</div>
        </div>
        <DemoTag />
      </div>
      <button className="btn btn-primary min-h-12 w-full text-[15px]" disabled={ro} onClick={() => setSheet('book')}><Icon name="plus" size={18} /> Book an appointment</button>
      <p className="rounded-lg bg-sunk px-3 py-2 text-[12px] text-muted">{DEMO_LINE}</p>
      <ErrorLine error={appts.error} />

      <section>
        <div className="eyebrow mb-2 px-1">Upcoming ({upcoming.length})</div>
        {upcoming.length ? (
          <div className="space-y-2">
            {upcoming.map((a) => <ApptCard key={a.appointment_id} a={a} api={api} ro={ro} onChange={changed} onMove={() => setMoving(a)} />)}
          </div>
        ) : <Empty>Nothing booked. Book a visit, video call or phone call with your doctor.</Empty>}
      </section>

      <section>
        <div className="eyebrow mb-2 px-1">Past and cancelled ({past.length})</div>
        {past.length ? (
          <div className="space-y-2">{past.map((a) => <ApptCard key={a.appointment_id} a={a} api={api} ro onChange={changed} />)}</div>
        ) : <Empty>No past appointments yet.</Empty>}
      </section>

      {team.data?.clinic && <p className="px-1 pb-2 text-[11.5px] text-muted">{team.data.clinic.name}: {team.data.clinic.hours}. Times are clinic time (UTC{team.data.clinic.utc_offset}). {team.data.clinic.note}</p>}
      {moving && <BookingSheet ctx={ctx} moving={moving} onClose={() => setMoving(null)} onBooked={() => { setMoving(null); changed() }} />}
    </div>
  )
}

function ApptCard({ a, api, ro, onChange, onMove }) {
  const [confirm, setConfirm] = useState(false)
  const [why, setWhy] = useState('')
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)
  const st = STATUS[a.status] || STATUS.BOOKED
  const cancel = async () => {
    setBusy(true); setErr(null)
    try { await api.cancelAppointment(a.appointment_id, why || undefined); setConfirm(false); onChange() } catch (e) { setErr(e) } finally { setBusy(false) }
  }
  const mode = MODES.find((m) => m.id === a.mode)
  return (
    <article className="card p-3">
      <div className="flex gap-3">
        <DateBlock s={a.start_at} muted={!a.upcoming} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <b className="num text-[16px]">{apptTime(a.start_at)}</b>
            <span className="tag" style={{ color: st.fg, background: st.bg }}>{st.label}</span>
          </div>
          <div className="text-[14px] font-semibold">{a.doctor_name}</div>
          <div className="flex items-center gap-1 text-[13px] text-muted"><Icon name={mode?.icon || 'clinic'} size={15} /> {a.mode_label}</div>
          <div className="mt-1 text-[13.5px]">{a.reason}</div>
          {a.note && <div className="mt-1.5 rounded-lg bg-sunk px-2.5 py-1.5 text-[12.5px]"><b>Doctor's note:</b> {a.note}</div>}
          {a.history?.some((h) => h.event === 'RESCHEDULED') && a.upcoming && <div className="mt-1 text-[12px] text-muted">Moved from {apptDay(a.history.filter((h) => h.event === 'RESCHEDULED').at(-1).from)} {apptTime(a.history.filter((h) => h.event === 'RESCHEDULED').at(-1).from)}</div>}
        </div>
      </div>
      {a.upcoming && !ro && !confirm && (
        <div className="mt-3 grid grid-cols-2 gap-2">
          <button className="btn" onClick={onMove}>Reschedule</button>
          <button className="btn" style={{ color: 'var(--high)' }} onClick={() => setConfirm(true)}>Cancel</button>
        </div>
      )}
      {confirm && (
        <div className="mt-3 space-y-2 rounded-xl p-3" style={{ background: 'var(--high-soft)' }}>
          <div className="text-[14px] font-semibold">Cancel this appointment?</div>
          <input className="input w-full" placeholder="Reason (optional)" value={why} onChange={(e) => setWhy(e.target.value)} />
          <div className="grid grid-cols-2 gap-2">
            <button className="btn" onClick={() => setConfirm(false)}>Keep it</button>
            <button className="btn btn-emg" disabled={busy} onClick={cancel}>Yes, cancel</button>
          </div>
        </div>
      )}
      <ErrorLine error={err} />
    </article>
  )
}

// Next 21 days the clinic is open (Monday to Saturday), from the phone's own date.
function openDays(n = 21) {
  const out = []
  const d = new Date()
  for (let i = 0; out.length < n && i < 40; i++) {
    const x = new Date(d.getFullYear(), d.getMonth(), d.getDate() + i)
    if (x.getDay() !== 0) out.push(localDay(x))
  }
  return out
}

// Booking flow in four short steps: how and with whom, day and time, reason, confirm. With `moving` it only picks a new time.
export function BookingSheet({ ctx, onClose, onBooked, moving }) {
  const { api, pid, team } = ctx
  const doctors = team.data?.doctors || []
  const [step, setStep] = useState(moving ? 1 : 0)
  const [doctorId, setDoctorId] = useState(moving?.clinician_id || null)
  const [mode, setMode] = useState(moving?.mode || 'in_person')
  const days = useMemo(() => openDays(), [])
  const [day, setDay] = useState(null)
  const [time, setTime] = useState(null)
  const [reason, setReason] = useState('')
  const [details, setDetails] = useState('')
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(null)
  const doc = doctors.find((d) => d.user_id === doctorId) || doctors[0]
  useEffect(() => { if (!doctorId && doctors[0]) setDoctorId(doctors[0].user_id) }, [doctors, doctorId])

  const slots = usePoll(() => (day ? api.slots(pid, day) : Promise.resolve(null)), [day], 0)
  useEffect(() => {      // jump to the first day that still has a free slot
    if (day) return
    let off = false
    ;(async () => {
      for (const d of days.slice(0, 7)) {
        try { const r = await api.slots(pid, d); if (r.slots.some((x) => x.available)) { if (!off) setDay(d); return } } catch { break }
      }
      if (!off) setDay(days[0])
    })()
    return () => { off = true }
  }, [api, pid, days, day])

  const fullReason = [reason, details.trim()].filter(Boolean).join(': ')
  const submit = async () => {
    setBusy(true); setErr(null)
    try {
      const a = moving
        ? await api.rescheduleAppointment(moving.appointment_id, `${day}T${time}`)
        : await api.book(pid, { start_at: `${day}T${time}`, reason: fullReason, mode, clinician_id: doc?.user_id })
      setDone(a)
    } catch (e) { setErr(e); if (e.status === 400) slots.refresh() } finally { setBusy(false) }
  }

  if (done) {
    return (
      <Sheet title={moving ? 'Appointment moved' : 'Appointment booked'} onClose={() => { onBooked(done); onClose() }}>
        <div className="card p-5 text-center">
          <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-full" style={{ background: 'var(--low-soft)', color: 'var(--low)' }}><Icon name="check" size={30} strokeWidth={2.4} /></div>
          <h2 className="mt-3 font-display text-[22px] font-extrabold">{moving ? 'Moved' : 'You are booked'}</h2>
          <p className="mt-1 text-[15px]">{apptDay(done.start_at)} at <b className="num">{apptTime(done.start_at)}</b></p>
          <p className="text-[14px] text-muted">{done.doctor_name} · {done.mode_label}</p>
          <div className="mt-3"><DemoTag /></div>
          <p className="mt-2 text-[12.5px] text-muted">{DEMO_LINE}</p>
        </div>
        <button className="btn btn-primary min-h-12 w-full" onClick={() => { onBooked(done); onClose() }}>See my appointments</button>
      </Sheet>
    )
  }

  const morning = (slots.data?.slots || []).filter((s) => s.time < '13:00')
  const afternoon = (slots.data?.slots || []).filter((s) => s.time >= '13:00')
  const steps = moving ? ['Time', 'Confirm'] : ['Doctor', 'Time', 'Reason', 'Confirm']
  const idx = moving ? step - 1 : step
  const canNext = [!!doc, !!(day && time), !!reason, true][step]

  return (
    <Sheet title={moving ? 'Reschedule' : 'Book an appointment'} onClose={onClose} right={<DemoTag />}>
      <ol className="flex gap-1.5" aria-label="Steps">
        {steps.map((s, i) => (
          <li key={s} className="flex-1">
            <div className="h-1.5 rounded-full" style={{ background: i <= idx ? 'var(--accent)' : 'var(--line)' }} />
            <div className="mt-1 text-[11.5px]" style={{ color: i === idx ? 'var(--ink)' : 'var(--muted)', fontWeight: i === idx ? 600 : 400 }}>{s}</div>
          </li>
        ))}
      </ol>

      {step === 0 && (
        <>
          <Card title="Who would you like to see?">
            {doctors.length ? doctors.map((d) => (
              <label key={d.user_id} className="flex items-center gap-3 rounded-xl border p-3" style={{ borderColor: d.user_id === doc?.user_id ? 'var(--accent)' : 'var(--line)', background: d.user_id === doc?.user_id ? 'var(--accent-soft)' : undefined }}>
                <input type="radio" name="doc" checked={d.user_id === doc?.user_id} onChange={() => setDoctorId(d.user_id)} />
                <span className="flex h-10 w-10 items-center justify-center rounded-full bg-surface font-display font-extrabold text-accent">{d.name.replace(/^Dr\.?\s*/, '')[0]}</span>
                <span><b className="block">{d.name}</b><span className="text-[13px] text-muted">{d.specialty} · {d.clinic}</span></span>
              </label>
            )) : <Empty>No doctor is linked to your account yet.</Empty>}
          </Card>
          <Card title="How would you like to meet?">
            <div className="grid grid-cols-3 gap-2">
              {MODES.map((m) => (
                <button key={m.id} onClick={() => setMode(m.id)} className="flex flex-col items-center gap-1 rounded-xl border px-1 py-3 text-[13px] font-medium"
                  style={{ borderColor: mode === m.id ? 'var(--accent)' : 'var(--line)', background: mode === m.id ? 'var(--accent-soft)' : undefined, color: mode === m.id ? 'var(--accent)' : undefined }}>
                  <Icon name={m.icon} /> {m.label}
                </button>
              ))}
            </div>
          </Card>
        </>
      )}

      {step === 1 && (
        <>
          {moving && <div className="card p-3 text-[13.5px]">Now: <b>{apptDay(moving.start_at)}, {apptTime(moving.start_at)}</b> with {moving.doctor_name}. Pick a new time.</div>}
          <Card title="Pick a day">
            <div className="-mx-1 flex gap-2 overflow-x-auto px-1 pb-1">
              {days.map((d) => {
                const p = apptParts(d), on = d === day
                return (
                  <button key={d} onClick={() => { setDay(d); setTime(null) }} className="flex w-14 shrink-0 flex-col items-center rounded-xl border py-2"
                    style={{ borderColor: on ? 'var(--accent)' : 'var(--line)', background: on ? 'var(--accent)' : 'var(--surface)', color: on ? 'var(--surface)' : undefined }}>
                    <span className="text-[11px]">{p.wd}</span><span className="font-display text-[19px] font-extrabold leading-tight">{p.day}</span><span className="text-[10.5px]">{p.mon}</span>
                  </button>
                )
              })}
            </div>
            <label className="mt-2 flex items-center gap-2 text-[13px] text-muted">Other date
              <input type="date" className="input" min={days[0]} value={day || ''} onChange={(e) => { setDay(e.target.value); setTime(null) }} />
            </label>
          </Card>
          <Card title={day ? `Times on ${apptDay(day)}` : 'Times'} right={<span className="text-[11.5px] text-muted">clinic time</span>}>
            {!slots.data && !slots.error && <Empty>Loading free times…</Empty>}
            <ErrorLine error={slots.error} />
            {slots.data?.closed && <Empty>{slots.data.closed_reason}</Empty>}
            {slots.data && !slots.data.closed && !slots.data.slots.some((s) => s.available) && <Empty>No free times that day. Try another day.</Empty>}
            {[['Morning', morning], ['Afternoon', afternoon]].map(([label, list]) => list.length > 0 && (
              <div key={label} className="mb-2">
                <div className="mb-1.5 text-[12.5px] text-muted">{label}</div>
                <div className="grid grid-cols-4 gap-1.5">
                  {list.map((s) => (
                    <button key={s.time} disabled={!s.available} onClick={() => setTime(s.time)} title={s.yours ? 'You already have this slot' : s.available ? '' : 'Not available'}
                      className="num min-h-10 rounded-lg border text-[14px] disabled:cursor-not-allowed"
                      style={time === s.time ? { background: 'var(--accent)', borderColor: 'var(--accent)', color: 'var(--surface)' }
                        : s.available ? { borderColor: 'var(--line)' } : { borderColor: 'transparent', background: 'var(--sunk)', color: 'var(--muted)', textDecoration: 'line-through' }}>
                      {s.time}
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </Card>
        </>
      )}

      {step === 2 && (
        <Card title="What is the visit for?">
          <div className="flex flex-wrap gap-2">
            {REASONS.map((r) => (
              <button key={r} onClick={() => setReason(r)} className="rounded-full border px-3 py-2 text-[13.5px]"
                style={{ borderColor: reason === r ? 'var(--accent)' : 'var(--line)', background: reason === r ? 'var(--accent-soft)' : undefined, color: reason === r ? 'var(--accent)' : undefined }}>
                {r}
              </button>
            ))}
          </div>
          <label className="mt-3 block text-[13px] text-muted">Anything your doctor should know? (optional)
            <textarea className="input mt-1 min-h-20 w-full py-2" maxLength={300} value={details} onChange={(e) => setDetails(e.target.value)} placeholder="For example: breathless when climbing stairs for a week" />
          </label>
          <p className="mt-2 text-[12px] text-muted">If you have chest pain now, don't wait for an appointment. Call 112.</p>
        </Card>
      )}

      {((!moving && step === 3) || (moving && step === 2)) && (
        <Card title="Check and confirm">
          <dl className="space-y-2 text-[14px]">
            <Row k="Doctor" v={moving ? moving.doctor_name : doc?.name} />
            <Row k="When" v={`${apptDay(day)}, ${time} (clinic time)`} />
            <Row k="How" v={MODES.find((m) => m.id === mode)?.label} />
            {!moving && <Row k="Reason" v={fullReason} />}
            <Row k="Where" v={doc?.clinic} />
          </dl>
          <p className="mt-3 rounded-lg bg-sunk px-3 py-2 text-[12px] text-muted">{DEMO_LINE} No payment is taken.</p>
        </Card>
      )}

      <ErrorLine error={err} />
      <div className="sticky bottom-0 grid grid-cols-2 gap-2 bg-bg py-3">
        <button className="btn min-h-12" onClick={() => (step === (moving ? 1 : 0) ? onClose() : setStep(step - 1))}>{step === (moving ? 1 : 0) ? 'Close' : 'Back'}</button>
        {(moving ? step === 2 : step === 3)
          ? <button className="btn btn-primary min-h-12" disabled={busy} onClick={submit}>{busy ? 'Saving…' : moving ? 'Confirm new time' : 'Confirm booking'}</button>
          : <button className="btn btn-primary min-h-12" disabled={!(moving && step === 1 ? day && time : canNext)} onClick={() => setStep(step + 1)}>Next</button>}
      </div>
    </Sheet>
  )
}

function Row({ k, v }) {
  return <div className="flex gap-3"><dt className="w-16 shrink-0 text-muted">{k}</dt><dd className="min-w-0 flex-1 font-medium">{v || '–'}</dd></div>
}
