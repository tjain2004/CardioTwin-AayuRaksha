import { useEffect, useRef, useState } from 'react'
import { usePoll } from '../hooks'
import { Card, Empty, ErrorLine, Icon, Sheet, SimTag, Toggle } from '../components/ui'
import { localDay, localHm, minutesOf } from '../format'

// Wellness: today's Indian meal ideas (veg or non-veg), an activity coach matched to ability and to today's heart
// check, water and sleep, daily and weekly goals. Wellness information only, kept apart from the heart check; the
// plan follows any limits a clinician set on the record (fluid limit, no exercise) before the person's own choices.
const SLOTS = [['breakfast', 'Breakfast'], ['lunch', 'Lunch'], ['snack', 'Snack'], ['dinner', 'Dinner']]
const DIETS = [['veg', 'Vegetarian'], ['eggetarian', 'Veg + eggs'], ['non_veg', 'Non-veg']]
const REGIONS = [['any', 'Any'], ['north', 'North'], ['south', 'South'], ['east', 'East'], ['west', 'West']]
const LEVELS = [['rest', 'Mostly resting'], ['light', 'Light'], ['moderate', 'Moderate']]
const LIMITS = [['joint_pain', 'Knee or joint pain'], ['walking_aid', 'Uses a walking aid'], ['breathless_on_exertion', 'Breathless when active'], ['recent_hospital_stay', 'Recent hospital stay']]
const QUICK = { water_glasses: 1, active_minutes: 10, fruit_veg_servings: 1 }
const GATE = { OK: null, GENTLE_ONLY: { color: 'var(--mod)', bg: 'var(--mod-soft)', label: 'Gentle only' }, PAUSED: { color: 'var(--emg)', bg: 'var(--emg-soft)', label: 'Paused' } }

export default function Wellness({ ctx }) {
  const { api, pid, ro } = ctx
  const day = localDay()
  const plan = usePoll(() => api.wellness(pid, day), [pid, day], 0)
  const prog = usePoll(() => api.wellnessProgress(pid, day), [pid, day], 30000)
  const [prefsOpen, setPrefsOpen] = useState(false)
  const [err, setErr] = useState(null)
  const p = plan.data
  const log = async (metric, value, mode) => {
    setErr(null)
    try { await api.wellnessLog(pid, day, metric, value, mode); prog.refresh() } catch (e) { setErr(e) }
  }
  if (plan.error) return <ErrorLine error={plan.error} />
  if (!p) return <Empty>Loading…</Empty>
  const gate = GATE[p.activity.gate.state]
  return (
    <div className="space-y-3">
      <Card eyebrow="Today" title="Your goals" right={<button className="btn min-h-9 text-[13px]" disabled={ro} onClick={() => setPrefsOpen(true)}><Icon name="gear" size={16} /> Preferences</button>}>
        <ErrorLine error={err} />
        {prog.data ? (
          <div className="space-y-2.5">
            {prog.data.today.map((i) => (
              <div key={i.metric}>
                <div className="flex items-center gap-2 text-[13.5px]">
                  <b className="min-w-0 flex-1">{i.label}</b>
                  <span className="num">{i.value}{i.goal ? ` / ${i.goal}` : ''} {i.unit}</span>
                  {i.source && i.source !== 'you' && <SimTag show={prog.data.simulated_steps && i.metric === 'steps'} />}
                  {QUICK[i.metric] && <button className="btn min-h-8 px-2.5" disabled={ro} onClick={() => log(i.metric, QUICK[i.metric], 'add')} aria-label={`Add ${QUICK[i.metric]} ${i.unit}`}>+{QUICK[i.metric]}</button>}
                  {i.metric === 'sleep_hours' && <SleepInput disabled={ro} value={i.value} onSet={(v) => log('sleep_hours', v, 'set')} />}
                </div>
                {i.goal ? <div className="mt-1 h-2 rounded-full bg-sunk"><div className="h-2 rounded-full" style={{ width: `${i.pct}%`, background: i.pct >= 100 ? 'var(--low)' : 'var(--accent)' }} /></div>
                  : <div className="text-[11.5px] text-muted">No goal today</div>}
              </div>
            ))}
          </div>
        ) : <Empty>Loading…</Empty>}
      </Card>

      {prog.data && <WeekCard prog={prog.data} />}

      <Card eyebrow={DIETS.find(([k]) => k === p.prefs.diet)?.[1]} title="Meals for today">
        <div className="space-y-2">
          {SLOTS.map(([slot, label]) => p.meals.filter((m) => m.slot === slot).map((m) => (
            <div key={slot + m.name} className="rounded-xl border border-line p-3 text-[13.5px]">
              <div className="flex items-center gap-2"><span className="eyebrow">{label}</span><span className="tag ml-auto" style={{ background: m.diet === 'non_veg' ? 'var(--high-soft)' : 'var(--low-soft)', color: m.diet === 'non_veg' ? 'var(--high)' : 'var(--low)' }}>{m.diet_label}</span></div>
              <b className="mt-0.5 block text-[15px]">{m.name}</b>
              <span className="text-muted">{m.description}</span>
              <p className="mt-1 text-[12.5px]"><b>Why:</b> {m.why}</p>
              {m.notes?.map((n, i) => <p key={i} className="text-[12px]" style={{ color: 'var(--mod)' }}>{n}</p>)}
            </div>
          )))}
          <div className="rounded-xl bg-sunk p-3 text-[13.5px]"><b>Hydration:</b> {p.goals.water_glasses} glasses through the day. {p.fluid_note || 'More in hot weather.'}</div>
          {p.tips?.[0] && <div className="rounded-xl bg-sunk p-3 text-[13.5px]"><b>Nutrition tip:</b> {p.tips[0]}</div>}
          <p className="text-[12px] text-muted">Go easy on: {p.limit_foods.slice(0, 6).join(', ')}.</p>
        </div>
      </Card>

      <Card eyebrow={p.activity.level_label} title="Activity coach" right={gate && <span className="tag" style={{ color: gate.color, background: gate.bg }}>{gate.label}</span>}>
        {p.activity.gate.reason && <p className="mb-2 rounded-lg px-3 py-2 text-[13.5px]" style={{ background: gate?.bg }}>{p.activity.gate.reason}</p>}
        {p.activity.suggestions.length > 0 && (
          <ul className="space-y-1.5">{p.activity.suggestions.map((e) => <li key={e.name} className="rounded-xl bg-sunk px-3 py-2 text-[13.5px]"><b>{e.name}</b><span className="block text-muted">{e.how}</span></li>)}</ul>
        )}
        {p.activity.heart_rate_zone && p.activity.gate.state === 'OK' && (
          <p className="mt-2 text-[13px]">Comfortable heart rate while active: <b className="num">{p.activity.heart_rate_zone.low} to {p.activity.heart_rate_zone.high} bpm</b> <SimTag show={p.activity.heart_rate_zone.simulated} /><span className="block text-[12px] text-muted">{p.activity.heart_rate_zone.note}</span></p>
        )}
        {p.activity.gate.state !== 'PAUSED' && <p className="mt-2 text-[12.5px] text-muted">{p.activity.talk_test}</p>}
        <SedentaryNudge prog={prog.data} paused={p.activity.gate.state === 'PAUSED'} />
      </Card>

      <p className="px-1 text-[11.5px] text-muted">{p.disclaimer} Based on: {[...p.based_on.conditions, ...p.based_on.medicines].join(', ') || 'your preferences'}{p.based_on.clinician_restrictions ? '; your doctor’s limits' : ''}.</p>
      {prefsOpen && <PrefsSheet api={api} pid={pid} prefs={p.prefs} onClose={() => setPrefsOpen(false)} onSaved={() => { setPrefsOpen(false); plan.refresh(); prog.refresh() }} />}
    </div>
  )
}

function SleepInput({ value, onSet, disabled }) {
  const [v, setV] = useState('')
  return (
    <form className="flex gap-1" onSubmit={(e) => { e.preventDefault(); const n = parseFloat(v); if (n >= 0 && n <= 16) { onSet(n); setV('') } }}>
      <input className="input h-8 min-h-8 w-14 px-2" inputMode="decimal" placeholder={value ? String(value) : 'h'} value={v} disabled={disabled} onChange={(e) => setV(e.target.value)} aria-label="Hours slept last night" />
      <button className="btn min-h-8 px-2.5" disabled={disabled || !v}>Set</button>
    </form>
  )
}

function WeekCard({ prog }) {
  const metrics = prog.today.filter((i) => i.goal)
  return (
    <Card eyebrow="Last 7 days" title="Weekly goals">
      <div className="space-y-1.5 text-[13px]">
        {metrics.map((m) => {
          const met = prog.week.filter((d) => d.met[m.metric]).length
          return (
            <div key={m.metric} className="flex items-center gap-2">
              <span className="w-28 shrink-0">{m.label}</span>
              <span className="flex flex-1 gap-1">{prog.week.map((d) => <span key={d.day} title={d.day} className="h-4 flex-1 rounded" style={{ background: d.met[m.metric] ? 'var(--low)' : 'var(--line)' }} />)}</span>
              <span className="num w-10 text-right">{met}/7</span>
            </div>
          )
        })}
      </div>
      {prog.insights.length > 0 && <ul className="mt-3 list-disc space-y-1 pl-5 text-[13.5px]">{prog.insights.map((x, i) => <li key={i}>{x}</li>)}</ul>}
    </Card>
  )
}

// Sedentary awareness: if the watch counted almost no steps in the last hour during the day, a gentle prompt.
function SedentaryNudge({ prog, paused }) {
  if (!prog || paused) return null
  const h = new Date().getHours()
  const steps = prog.today.find((i) => i.metric === 'steps')
  if (h < 9 || h > 20 || !steps || steps.value > 0.6 * (steps.goal || 0)) return null
  return <p className="mt-2 rounded-lg bg-accent-soft px-3 py-2 text-[13px]">You have been fairly still today. If you feel well, a slow 5-minute walk around the house helps.</p>
}

function PrefsSheet({ api, pid, prefs, onClose, onSaved }) {
  const [f, setF] = useState(prefs)
  const [err, setErr] = useState(null)
  const save = async () => {
    setErr(null)
    try { await api.putWellnessPrefs(pid, { diet: f.diet, region: f.region, activity_level: f.activity_level, limitations: f.limitations, goals: f.goals, reminders: f.reminders, avoid_foods: f.avoid_foods }); onSaved() } catch (e) { setErr(e) }
  }
  const Pick = ({ label, opts, k }) => (
    <div><div className="mb-1 text-[13px] font-semibold">{label}</div>
      <div className="flex flex-wrap gap-1.5">{opts.map(([v, l]) => <button key={v} type="button" className="btn min-h-9 text-[13px]" onClick={() => setF({ ...f, [k]: v })} style={f[k] === v ? { background: 'var(--accent)', color: 'var(--on-accent)', borderColor: 'var(--accent)' } : undefined}>{l}</button>)}</div></div>
  )
  return (
    <Sheet title="Wellness preferences" onClose={onClose} right={<button className="btn btn-primary" onClick={save}>Save</button>}>
      <ErrorLine error={err} />
      <Card>
        <div className="space-y-3">
          <Pick label="Food" opts={DIETS} k="diet" />
          <Pick label="Cuisine" opts={REGIONS} k="region" />
          <Pick label="Activity level" opts={LEVELS} k="activity_level" />
          <div><div className="mb-1 text-[13px] font-semibold">Anything that limits activity?</div>
            {LIMITS.map(([v, l]) => <label key={v} className="flex min-h-10 items-center gap-2 text-[14px]"><input type="checkbox" checked={f.limitations.includes(v)} onChange={(e) => setF({ ...f, limitations: e.target.checked ? [...f.limitations, v] : f.limitations.filter((x) => x !== v) })} /> {l}</label>)}</div>
          <label className="block text-[13px] font-semibold">Foods to avoid (comma separated)
            <input className="input mt-1 w-full" value={f.avoid_foods.join(', ')} onChange={(e) => setF({ ...f, avoid_foods: e.target.value.split(',').map((x) => x.trim()).filter(Boolean).slice(0, 10) })} placeholder="e.g. peanuts, mushroom" /></label>
        </div>
      </Card>
      <Card title="Daily goals">
        {[['steps', 'Steps', 500], ['water_glasses', 'Water (glasses)', 1], ['sleep_hours', 'Sleep (hours)', 0.5], ['active_minutes', 'Active minutes', 5], ['fruit_veg_servings', 'Fruit and vegetables', 1]].map(([k, l, step]) => (
          <label key={k} className="flex min-h-11 items-center gap-2 text-[14px]"><span className="flex-1">{l}</span>
            <input type="number" className="input w-24" step={step} value={f.goals[k]} onChange={(e) => setF({ ...f, goals: { ...f.goals, [k]: Number(e.target.value) } })} /></label>
        ))}
        <p className="text-[12px] text-muted">A limit your doctor set (for example on fluids) always applies, whatever you choose here.</p>
      </Card>
      <Card title="Reminders">
        <Toggle checked={f.reminders.hydration} label="Water reminders" sub="Every 2 hours while you are awake" onChange={(v) => setF({ ...f, reminders: { ...f.reminders, hydration: v } })} />
        <Toggle checked={f.reminders.sleep} label="Bedtime reminder" sub="30 minutes before bed" onChange={(v) => setF({ ...f, reminders: { ...f.reminders, sleep: v } })} />
        <div className="grid grid-cols-2 gap-2 text-[13px]">
          <label>Wake up<input type="time" className="input mt-1 w-full" value={f.reminders.wake_time} onChange={(e) => setF({ ...f, reminders: { ...f.reminders, wake_time: e.target.value } })} /></label>
          <label>Bedtime<input type="time" className="input mt-1 w-full" value={f.reminders.bed_time} onChange={(e) => setF({ ...f, reminders: { ...f.reminders, bed_time: e.target.value } })} /></label>
        </div>
      </Card>
    </Sheet>
  )
}

// Water and bedtime reminders while the app is open, the same way medicine reminders work.
export function useWellnessReminders(api, pid, enabled) {
  const fired = useRef(new Set())
  const list = usePoll(() => (enabled ? api.wellnessReminders(pid) : Promise.resolve([])), [pid, enabled], 0)
  useEffect(() => {
    if (!list.data?.length) return
    const tick = () => {
      if (typeof Notification === 'undefined' || Notification.permission !== 'granted') return
      const now = minutesOf(localHm()), day = localDay()
      for (const r of list.data) {
        const key = `${day}|${r.kind}|${r.time}`
        if (now >= minutesOf(r.time) && now - minutesOf(r.time) <= 20 && !fired.current.has(key)) {
          fired.current.add(key)
          try { new Notification(r.kind === 'sleep' ? 'Bedtime soon' : 'Water reminder', { body: r.text, icon: '/icon-192.png', tag: key }) } catch { /* not allowed here */ }
        }
      }
    }
    tick()
    const t = setInterval(tick, 60000)
    return () => clearInterval(t)
  }, [list.data])
}
