import { useState } from 'react'
import { usePoll } from '../hooks'
import { Card, Empty, ErrorLine, Icon, SimTag } from '../components/ui'
import { ago, parseTs, SYMPTOM_LABELS } from '../format'
import HeartHealth from './HeartHealth'
import Wellness from './Wellness'
import Learn from './Learn'

// Health: sections behind a switch: the how-do-you-feel check-in and its history, heart health (CAD overview, "Explain
// my heart health", weekly reports), wellness (meals, activity, water, sleep, goals), the plain-language health profile
// from the record, lifestyle tips picked from the conditions on record, and the CAD Education Center. No risk score
// number is shown here.
const SECTIONS = [{ id: 'checkin', label: 'Check-in' }, { id: 'heart', label: 'Heart health' }, { id: 'wellness', label: 'Wellness' },
  { id: 'record', label: 'My record' }, { id: 'tips', label: 'Tips' }, { id: 'learn', label: 'Learn' }]

export default function Health({ ctx, focus }) {
  const { api, pid, ro, refreshAll } = ctx
  const [section, setSection] = useState(SECTIONS.some((x) => x.id === focus) ? focus : 'checkin')
  const profile = usePoll(() => api.healthProfile(pid), [pid], 0)
  const checkins = usePoll(() => api.checkins(pid, 20), [pid], 20000)
  const hp = profile.data

  return (
    <div className="space-y-4 px-4">
      <div role="tablist" aria-label="Health sections" className="chips -mx-4 px-4">
        {SECTIONS.map((x) => (
          <button key={x.id} role="tab" aria-selected={x.id === section} onClick={() => setSection(x.id)} className="chip">{x.label}</button>
        ))}
      </div>

      {section === 'checkin' && (
        <>
          <Symptoms api={api} pid={pid} disabled={ro} onDone={() => { refreshAll(); checkins.refresh() }} />
          <CheckinHistory items={checkins.data} error={checkins.error} />
        </>
      )}

      {section === 'record' && <Card title="Health profile" right={hp?.is_demo && <span className="tag bg-sunk text-muted" title={hp.source}>sample record</span>}>
        <ErrorLine error={profile.error} />
        {hp && (
          <>
            <div className="grid grid-cols-3 gap-2">
              {hp.measures.map((m) => (
                <div key={m.label} className="rounded-xl bg-sunk px-2.5 py-2">
                  <div className="text-[11.5px] text-muted">{m.label}</div>
                  <div className="num text-[15px] font-semibold">{m.value}</div>
                </div>
              ))}
            </div>
            <div className="mb-1.5 mt-4 text-[14px] font-semibold">Conditions on record</div>
            {hp.conditions.length ? (
              <ul className="space-y-1.5">
                {hp.conditions.map((c) => (
                  <li key={c.code} className="flex items-center gap-2 rounded-xl border border-line px-3 py-2 text-[14px]">
                    <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: 'var(--accent)' }} />
                    <span className="min-w-0 flex-1">{c.name}</span>
                    <span className="text-[12px] text-muted">{c.note}</span>
                  </li>
                ))}
              </ul>
            ) : <Empty>No long-term conditions on your record.</Empty>}
            {hp.labs.length > 0 && (
              <>
                <div className="mb-1.5 mt-4 text-[14px] font-semibold">Latest tests</div>
                <div className="divide-y divide-line rounded-xl border border-line text-[13.5px]">
                  {hp.labs.map((l) => (
                    <div key={l.code} className="flex justify-between gap-3 px-3 py-2"><span>{l.name}</span><span className="num shrink-0 font-semibold">{l.value} {l.unit}</span></div>
                  ))}
                </div>
              </>
            )}
            {hp.medicines_on_record != null && <p className="mt-3 text-[13px] text-muted">{Math.round(hp.medicines_on_record)} medicines on your hospital record.</p>}
            <p className="mt-2 text-[11.5px] text-muted">{hp.note}{hp.is_demo ? ' This sample patient comes from the MIMIC-IV demo dataset.' : ''}</p>
          </>
        )}
      </Card>}

      {section === 'tips' && <Tips conditions={hp?.conditions || []} />}
      {section === 'heart' && <HeartHealth ctx={ctx} />}
      {section === 'wellness' && <Wellness ctx={ctx} />}
      {section === 'learn' && <Learn />}
    </div>
  )
}

const SYMPTOMS = Object.entries(SYMPTOM_LABELS)

function Symptoms({ api, pid, onDone, disabled }) {
  const [sel, setSel] = useState({})
  const [note, setNote] = useState('')
  const [res, setRes] = useState(null)
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)
  const send = async () => {
    setErr(null); setBusy(true)
    try { setRes(await api.symptoms(pid, Object.fromEntries(SYMPTOMS.map(([k]) => [k, !!sel[k]])), note.trim() || undefined)); setSel({}); setNote(''); onDone() } catch (e) { setErr(e) } finally { setBusy(false) }
  }
  const none = !Object.values(sel).some(Boolean)
  return (
    <Card title="How do you feel?" eyebrow="Check-in">
      <div className="grid grid-cols-1 gap-1.5">
        {SYMPTOMS.map(([k, label]) => (
          <label key={k} className="flex min-h-11 items-center gap-3 rounded-xl border border-line px-3 text-[14px]" style={sel[k] ? { borderColor: 'var(--accent)', background: 'var(--accent-soft)' } : undefined}>
            <input type="checkbox" checked={!!sel[k]} onChange={(e) => setSel({ ...sel, [k]: e.target.checked })} className="h-4 w-4" /> {label}
          </label>
        ))}
      </div>
      <textarea className="input mt-2 min-h-16 w-full py-2" maxLength={500} placeholder="Anything else? (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
      <button className="btn btn-primary mt-2 min-h-11 w-full" disabled={disabled || busy} onClick={send}>{none ? 'I feel fine, send check-in' : 'Send check-in'}</button>
      <ErrorLine error={err} />
      {res && <div className="mt-2 text-[13px] text-muted">Sent. {res.checkin?.outcome || 'Your care team can see it.'}</div>}
    </Card>
  )
}

function CheckinHistory({ items, error }) {
  const [all, setAll] = useState(false)
  if (error) return <ErrorLine error={error} />
  if (!items) return null
  const shown = all ? items : items.slice(0, 4)
  return (
    <Card title="Check-in history" right={items.length > 4 && <button className="text-[13px] text-accent" onClick={() => setAll(!all)}>{all ? 'Show less' : `Show all ${items.length}`}</button>}>
      {items.length ? (
        <ol className="space-y-2">
          {shown.map((c) => {
            const urgent = c.emergency_state && c.emergency_state !== 'NOT_TRIGGERED'
            return (
              <li key={c.id} className="flex gap-3">
                <span className="mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: urgent ? 'var(--emg)' : c.symptoms.length ? 'var(--mod)' : 'var(--low)' }} />
                <div className="min-w-0 flex-1 border-b border-line pb-2 text-[13.5px]">
                  <div className="flex items-center gap-2">
                    <b>{c.symptoms.length ? c.symptoms.map((s) => SYMPTOM_LABELS[s] || s).join(', ') : 'Feeling fine'}</b>
                    {c.simulated && <SimTag title="Checked against readings from the virtual watch" />}
                  </div>
                  {c.note && <div className="text-muted">"{c.note}"</div>}
                  <div className="text-[12px] text-muted">{parseTs(c.created_at)?.toLocaleString([], { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })} · {ago(c.created_at)} · {c.outcome}</div>
                </div>
              </li>
            )
          })}
        </ol>
      ) : <Empty>No check-ins yet. A quick check-in once a day helps your care team.</Empty>}
    </Card>
  )
}

const TIPS = {
  general: [
    { icon: 'heart', title: 'Know the warning signs', text: 'Chest pain or pressure, pain spreading to the arm, jaw or back, sudden breathlessness or fainting: call 112 straight away. Do not wait for an appointment.' },
    { icon: 'leaf', title: 'Less salt', text: 'Aim for under one teaspoon (5 g) of salt a day. Watch pickles, papad, namkeen and packaged foods.' },
    { icon: 'clock', title: 'Move a little every day', text: 'Gentle walking most days helps your heart. Ask your doctor how much activity is right for you, especially soon after a hospital stay.' },
    { icon: 'pill', title: 'Same time, every day', text: 'Take your medicines at the same times each day. The reminders in Medicines can help.' },
  ],
  hist_af: { icon: 'heart', title: 'Living with an irregular heartbeat', text: 'Keep wearing the watch so changes in rhythm are noticed. If you take a blood thinner, never skip or double a dose without asking your doctor.' },
  smoker: { icon: 'leaf', title: 'Stopping smoking', text: 'Stopping is the single biggest thing you can do for your heart. Ask your doctor about support to quit. The national quitline is 1800-11-2356.' },
  bmi_30: { icon: 'leaf', title: 'A healthy weight', text: 'Small, steady changes add up: smaller portions, more vegetables, fewer fried snacks and sugary drinks.' },
  hba1c: { icon: 'note', title: 'Blood sugar', text: 'Keeping blood sugar in range protects your heart and kidneys. Keep up with your sugar checks and diabetes medicines.' },
  egfr: { icon: 'pill', title: 'Kidneys and medicines', text: 'Some common painkillers can strain the kidneys. Check with your doctor before taking any new medicine, even one from the chemist.' },
  ix_bleeding: { icon: 'alert', title: 'Bleeding', text: 'Tell your doctor about any unusual bleeding or bruising, black stools, or blood in urine.' },
}

function Tips({ conditions }) {
  const personal = conditions.map((c) => TIPS[c.code]).filter(Boolean)
  const tips = [...personal, ...TIPS.general]
  return (
    <Card title="Tips for a healthy heart" eyebrow={personal.length ? 'Picked for you' : 'Lifestyle'}>
      <div className="space-y-2">
        {tips.map((t) => (
          <div key={t.title} className="flex gap-3 rounded-xl bg-sunk p-3">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-surface text-accent"><Icon name={t.icon} size={18} /></span>
            <div className="text-[13.5px]"><b className="block text-[14px]">{t.title}</b>{t.text}</div>
          </div>
        ))}
      </div>
      <p className="mt-2 text-[11.5px] text-muted">General information, not medical advice. Follow your own doctor's advice first.</p>
    </Card>
  )
}
