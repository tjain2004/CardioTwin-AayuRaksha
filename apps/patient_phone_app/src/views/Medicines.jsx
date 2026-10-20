import { useState } from 'react'
import { usePoll } from '../hooks'
import { Card, DemoTag, Empty, ErrorLine, Icon, Toggle } from '../components/ui'
import { localDay, localHm, minutesOf, parseTs } from '../format'

// Medicines: today's doses with reminders, the last 7 days, the medicine list (add, change times, remove) and the
// original dose log against the doctor's daily limits. CardioTwin never suggests a dose.
export function dueDoses(meds, windowMin = 60) {
  const now = minutesOf(localHm())
  const out = []
  for (const m of meds || []) {
    for (const t of m.times || []) {
      const d = now - minutesOf(t)
      if (!m.taken?.[t] && d >= -windowMin && d <= 120) out.push({ med: m, time: t, late: d > 30 })
    }
  }
  return out.sort((a, b) => minutesOf(a.time) - minutesOf(b.time))
}

export default function Medicines({ ctx }) {
  const { api, pid, ro, meds, ehr, readings, settings, setSheet } = ctx
  const [adding, setAdding] = useState(false)
  const [editing, setEditing] = useState(null)
  const day = localDay()
  const adh = usePoll(() => api.adherence(pid, day), [pid, meds.data], 0)
  const list = meds.data || []
  const doses = list.flatMap((m) => m.times.map((t) => ({ m, t }))).sort((a, b) => minutesOf(a.t) - minutesOf(b.t))
  const now = minutesOf(localHm())
  const taken = doses.filter(({ m, t }) => m.taken?.[t]).length
  const [err, setErr] = useState(null)
  const toggle = async (m, t, on) => { setErr(null); try { await api.medTaken(m.med_id, day, t, on); meds.refresh() } catch (e) { setErr(e) } }
  const remindersOff = settings.data && !settings.data.med_reminders

  return (
    <div className="space-y-3 px-4">
      <div className="flex items-end gap-3">
        <div className="min-w-0 flex-1">
          <h1 className="font-display text-[26px] font-extrabold leading-tight">My <span className="text-accent">medicines</span></h1>
          <div className="text-[13px] text-muted">{doses.length ? `${taken} of ${doses.length} doses taken today` : 'No reminders set'}</div>
        </div>
        <button className="btn shrink-0" onClick={() => setSheet('pharmacy')}><Icon name="clinic" size={16} /> Medical stores</button>
      </div>
      {doses.length > 0 && (
        <div className="h-2 overflow-hidden rounded-full bg-sunk"><div className="h-full rounded-full" style={{ width: `${(taken / doses.length) * 100}%`, background: 'var(--low)' }} /></div>
      )}
      {remindersOff && <p className="rounded-lg bg-sunk px-3 py-2 text-[12.5px] text-muted">Medicine reminders are off. Turn them on under Profile, Settings.</p>}

      <Card title="Today" right={<span className="text-[12px] text-muted">{new Date().toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' })}</span>}>
        {doses.length ? (
          <ol className="space-y-1.5">
            {doses.map(({ m, t }) => {
              const done = m.taken?.[t]
              const diff = now - minutesOf(t)
              const state = done ? 'taken' : diff > 120 ? 'missed' : diff >= -30 ? 'due' : 'later'
              const color = { taken: 'var(--low)', missed: 'var(--high)', due: 'var(--accent)', later: 'var(--muted)' }[state]
              return (
                <li key={m.med_id + t} className="flex items-center gap-3 rounded-xl border px-3 py-2" style={{ borderColor: state === 'due' ? 'var(--accent)' : 'var(--line)' }}>
                  <span className="num w-11 text-[15px] font-semibold">{t}</span>
                  <span className="min-w-0 flex-1 text-[14px]">
                    <b>{m.name}</b> <span className="text-muted">{m.strength}</span>
                    <span className="block text-[12px]" style={{ color }}>
                      {state === 'taken' ? `Taken at ${parseTs(done)?.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}` : state === 'missed' ? 'Not marked as taken' : state === 'due' ? 'Due now' : 'Later today'}
                    </span>
                  </span>
                  {done
                    ? <button className="btn" disabled={ro} onClick={() => toggle(m, t, false)} aria-label={`Undo ${m.name} ${t}`}><Icon name="check" size={18} style={{ color: 'var(--low)' }} /> Undo</button>
                    : <button className={`btn ${state === 'due' ? 'btn-primary' : ''}`} disabled={ro} onClick={() => toggle(m, t, true)}>Taken</button>}
                </li>
              )
            })}
          </ol>
        ) : <Empty>Add your medicines below with the times you take them, and CardioTwin will remind you.</Empty>}
        <ErrorLine error={err} />
      </Card>

      {adh.data && doses.length > 0 && <Adherence days={adh.data.days} />}

      <Card title="My medicines" right={!ro && <button className="btn" onClick={() => { setAdding(true); setEditing(null) }}><Icon name="plus" size={16} /> Add</button>}>
        {list.some((m) => m.is_demo) && <p className="mb-2 flex items-center gap-2 text-[12px] text-muted"><DemoTag title="Fictional demo medicine list, not from the medical record" /> Demo list for this sample patient, not from a real prescription.</p>}
        {adding && <MedForm api={api} pid={pid} onDone={() => { setAdding(false); meds.refresh() }} onCancel={() => setAdding(false)} />}
        <div className="space-y-2">
          {list.map((m) => editing === m.med_id
            ? <MedForm key={m.med_id} api={api} pid={pid} med={m} onDone={() => { setEditing(null); meds.refresh() }} onCancel={() => setEditing(null)} />
            : (
              <article key={m.med_id} className="rounded-xl bg-sunk p-3">
                <div className="flex items-start gap-3">
                  <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-surface text-accent"><Icon name="pill" size={18} /></span>
                  <div className="min-w-0 flex-1">
                    <div className="text-[15px]"><b>{m.name}</b> <span className="text-muted">{m.strength}</span></div>
                    {m.instructions && <div className="text-[13px] text-muted">{m.instructions}</div>}
                    <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                      {m.times.map((t) => <span key={t} className="num rounded-full bg-surface px-2 py-0.5 text-[12px]">{t}</span>)}
                      {!m.reminders && <span className="text-[12px] text-muted">reminders off</span>}
                      <span className="text-[11.5px] text-muted">· added by {m.source === 'doctor' ? 'your doctor' : m.source === 'demo' ? 'demo data' : 'you'}</span>
                    </div>
                  </div>
                  {!ro && <button className="flex h-9 w-9 items-center justify-center rounded-full hover:bg-surface" aria-label={`Edit ${m.name}`} onClick={() => { setEditing(m.med_id); setAdding(false) }}><Icon name="edit" size={18} /></button>}
                </div>
              </article>
            ))}
          {!list.length && !adding && <Empty>No medicines yet.</Empty>}
        </div>
        <p className="mt-2 text-[11.5px] text-muted">Take medicines exactly as your doctor prescribed. Do not stop or change a heart medicine without talking to your doctor.</p>
      </Card>

      <Medications api={api} pid={pid} ehr={ehr.data} readings={readings.data} nowTs={ctx.features.data?.now} onLogged={readings.refresh} disabled={ro} />
    </div>
  )
}

function Adherence({ days }) {
  return (
    <Card title="Last 7 days">
      <div className="flex items-end justify-between gap-1.5">
        {days.map((d) => {
          const pctv = d.due ? d.taken / d.due : 0
          const wd = new Date(`${d.day}T12:00:00`).toLocaleDateString(undefined, { weekday: 'narrow' })
          return (
            <div key={d.day} className="flex flex-1 flex-col items-center gap-1" title={`${d.day}: ${d.taken} of ${d.due}`}>
              <div className="flex h-16 w-full items-end overflow-hidden rounded-md bg-sunk">
                <div className="w-full rounded-md" style={{ height: `${Math.max(pctv * 100, d.due ? 4 : 0)}%`, background: pctv >= 0.99 ? 'var(--low)' : pctv >= 0.5 ? 'var(--mod)' : 'var(--high)' }} />
              </div>
              <span className="text-[11px] text-muted">{wd}</span>
            </div>
          )
        })}
      </div>
      <p className="mt-2 text-[12px] text-muted">Share of reminder doses you marked as taken each day.</p>
    </Card>
  )
}

function MedForm({ api, pid, med, onDone, onCancel }) {
  const [name, setName] = useState(med?.name || '')
  const [strength, setStrength] = useState(med?.strength || '')
  const [instructions, setInstructions] = useState(med?.instructions || '')
  const [times, setTimes] = useState(med?.times?.length ? med.times : ['08:00'])
  const [reminders, setReminders] = useState(med ? med.reminders : true)
  const [err, setErr] = useState(null)
  const save = async () => {
    setErr(null)
    const body = { name, strength, instructions, times: times.filter(Boolean), reminders }
    try { med ? await api.updateMedication(med.med_id, body) : await api.addMedication(pid, body); onDone() } catch (e) { setErr(e) }
  }
  const remove = async () => { try { await api.removeMedication(med.med_id); onDone() } catch (e) { setErr(e) } }
  return (
    <div className="mb-2 space-y-2 rounded-xl border p-3" style={{ borderColor: 'var(--accent)' }}>
      <div className="grid grid-cols-[1fr_7rem] gap-2">
        <input className="input" placeholder="Medicine name" value={name} onChange={(e) => setName(e.target.value)} />
        <input className="input" placeholder="Strength" value={strength} onChange={(e) => setStrength(e.target.value)} />
      </div>
      <input className="input w-full" placeholder="How to take it (from your prescription)" value={instructions} onChange={(e) => setInstructions(e.target.value)} />
      <div>
        <div className="mb-1 text-[13px] text-muted">Reminder times</div>
        <div className="flex flex-wrap items-center gap-2">
          {times.map((t, i) => (
            <span key={i} className="flex items-center gap-1">
              <input type="time" className="input num w-[7.2rem]" value={t} onChange={(e) => setTimes(times.map((x, j) => (j === i ? e.target.value : x)))} />
              {times.length > 1 && <button className="h-8 w-8 rounded-full text-muted hover:bg-sunk" aria-label="Remove time" onClick={() => setTimes(times.filter((_, j) => j !== i))}>×</button>}
            </span>
          ))}
          {times.length < 6 && <button className="btn" onClick={() => setTimes([...times, '20:00'])}><Icon name="plus" size={14} /> time</button>}
        </div>
      </div>
      <Toggle label="Remind me" checked={reminders} onChange={setReminders} />
      <ErrorLine error={err} />
      <div className="flex gap-2">
        {med && <button className="btn" style={{ color: 'var(--high)' }} onClick={remove}><Icon name="trash" size={16} /> Remove</button>}
        <button className="btn ml-auto" onClick={onCancel}>Cancel</button>
        <button className="btn btn-primary" disabled={!name.trim()} onClick={save}>Save</button>
      </div>
    </div>
  )
}

// The original dose log: log mg taken against the daily limit the doctor set; going over raises a tell-the-doctor alert.
function Medications({ api, pid, ehr, readings, nowTs, onLogged, disabled }) {
  const limits = ehr?.dose_limits || {}
  const drugs = Object.keys(limits)
  const [drug, setDrug] = useState('')
  const [mg, setMg] = useState('')
  const [msg, setMsg] = useState(null)
  const doses = (readings || []).filter((r) => r.type === 'dose_taken')
  const day = (nowTs || new Date().toISOString()).slice(0, 10)
  const today = (d) => doses.filter((r) => r.meta?.drug === d && r.ts.slice(0, 10) === day).reduce((s, r) => s + (r.value_num || 0), 0)
  const log = async () => {
    const d = (drug || drugs[0] || '').trim().toLowerCase()
    if (!d || !mg) return
    // Simulated patients run on the replay clock, so stamp the dose at the backend's "now" for them.
    const ts = nowTs ? parseTs(nowTs).toISOString() : new Date().toISOString()
    try {
      await api.sendReadings([{ patient_id: pid, timestamp: ts, type: 'dose_taken', value: Number(mg), source: 'phone', quality: 1, meta: { drug: d } }])
      setMsg(`Logged ${mg} mg ${d}.`); setMg(''); onLogged()
    } catch (e) { setMsg(e.message) }
  }
  return (
    <Card title="Dose log" eyebrow="Daily limits from your doctor" right={<span className="text-[12px] text-muted">{ehr?.profile?.n_active_drugs != null ? `${ehr.profile.n_active_drugs} active on record` : ''}</span>}>
      {drugs.length ? (
        <div className="space-y-1.5">
          {drugs.map((d) => {
            const t = today(d)
            const over = t > limits[d]
            return (
              <div key={d} className="flex items-center justify-between rounded-xl bg-sunk px-3 py-2 text-[14px]">
                <span className="font-semibold capitalize">{d}</span>
                <span className="num" style={{ color: over ? 'var(--high)' : undefined }}>{t} mg today{over ? ' · over your limit, your doctor is told' : ''}</span>
              </div>
            )
          })}
        </div>
      ) : <Empty>Your doctor hasn't added medicines with a daily limit yet. You can still log a dose.</Empty>}
      <div className="mt-3 flex gap-2">
        {drugs.length ? (
          <select className="input min-w-0 flex-1" value={drug || drugs[0]} onChange={(e) => setDrug(e.target.value)}>{drugs.map((d) => <option key={d}>{d}</option>)}</select>
        ) : <input className="input min-w-0 flex-1" placeholder="Medicine" value={drug} onChange={(e) => setDrug(e.target.value)} />}
        <input className="input w-20" type="number" inputMode="decimal" placeholder="mg" value={mg} onChange={(e) => setMg(e.target.value)} />
        <button className="btn btn-primary" disabled={disabled} onClick={log}>Log dose</button>
      </div>
      {msg && <div className="mt-2 text-[13px] text-muted">{msg}</div>}
      <p className="mt-2 text-[11.5px] text-muted">CardioTwin never suggests a dose. Take medicines as your doctor prescribed.</p>
    </Card>
  )
}

