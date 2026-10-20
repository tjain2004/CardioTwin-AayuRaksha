import { useState } from 'react'
import { usePoll } from '../hooks'
import { Card, Empty, ErrorLine, Icon, SimTag } from '../components/ui'

// Heart health: the CAD-focused overview, "Explain my heart health" and the weekly reports. All of it is computed by
// fixed rules from data CardioTwin already holds (no AI). CardioTwin's model is a 24-hour deterioration check, not a
// CAD test, and every screen says so. Values that are not on record are shown as missing, never estimated.
const TREND = {
  IMPROVING: { label: 'Improving', color: 'var(--low)', icon: '↗' },
  STABLE: { label: 'Stable', color: 'var(--ink)', icon: '→' },
  WORSENING: { label: 'Worsening', color: 'var(--high)', icon: '↘' },
  STALE: { label: 'Out of date', color: 'var(--mod)', icon: '⏱' },
  INSUFFICIENT_DATA: { label: 'Not enough data', color: 'var(--muted)', icon: '·' },
  SINGLE_VALUE: { label: 'One value', color: 'var(--muted)', icon: '·' },
  TODAY_ONLY: { label: 'Today', color: 'var(--muted)', icon: '·' },
  LOGGED: { label: 'Logged', color: 'var(--muted)', icon: '·' },
  MISSING: { label: 'Missing', color: 'var(--muted)', icon: '–' },
}
const FACTOR = {
  PRESENT: { label: 'Needs attention', color: 'var(--high)', bg: 'var(--high-soft)' },
  NOT_PRESENT: { label: 'On track', color: 'var(--low)', bg: 'var(--low-soft)' },
  UNKNOWN: { label: 'Not recorded', color: 'var(--muted)', bg: 'var(--sunk)' },
}
const LEVEL = { LOW: 'Low', MODERATE: 'Moderate', HIGH: 'High' }

export default function HeartHealth({ ctx }) {
  const { api, pid } = ctx
  const ov = usePoll(() => api.heartOverview(pid), [pid], 60000)
  const [view, setView] = useState('overview')
  const d = ov.data
  if (ov.error) return <ErrorLine error={ov.error} />
  if (!d) return <Empty>Loading…</Empty>
  return (
    <div className="space-y-3">
      <div className="flex gap-1.5">
        {[['overview', 'Overview'], ['explain', 'Explain my heart health'], ['reports', 'Weekly reports']].map(([id, label]) => (
          <button key={id} className="btn min-h-9 px-3 text-[13px]" onClick={() => setView(id)}
            style={view === id ? { background: 'var(--accent-soft)', borderColor: 'var(--accent)', color: 'var(--accent)', fontWeight: 600 } : undefined}>{label}</button>
        ))}
      </div>
      {view === 'overview' && <Overview d={d} />}
      {view === 'explain' && <Explain ctx={ctx} />}
      {view === 'reports' && <Reports ctx={ctx} />}
    </div>
  )
}

function Overview({ d }) {
  const s = d.status
  const tr = TREND[s.trend?.trend] || TREND.MISSING
  return (
    <>
      <Card eyebrow="Current status" title={s.state === 'OK' ? `${LEVEL[s.level] || 'Not shown'} on the 24-hour check` : s.state === 'NO_EVALUATION_YET' ? 'No heart check yet' : 'Check could not run'}
        right={d.simulated && <SimTag />}>
        {s.text && <p className="text-[14px]">{s.text}</p>}
        <p className="mt-1 text-[13px]"><span style={{ color: tr.color }}>{tr.icon} {tr.label}</span> <span className="text-muted">· {s.trend?.text}</span></p>
        <p className="mt-2 rounded-lg bg-sunk px-3 py-2 text-[12.5px] text-muted">{s.check_meaning}</p>
      </Card>

      <Card title="Measurements" eyebrow="Latest and trend">
        <div className="divide-y divide-line">
          {Object.entries(d.measurements).map(([k, m]) => {
            const t = TREND[m.trend] || TREND.MISSING
            return (
              <div key={k} className="flex items-center gap-3 py-2 text-[13.5px]">
                <div className="min-w-0 flex-1">
                  <div className="font-semibold">{m.label}</div>
                  <div className="text-[11.5px] text-muted">{m.source || (m.status === 'NOT_LOGGED' ? 'Not logged yet' : 'Not on your record')}{m.text ? ` · ${m.text}` : ''}</div>
                </div>
                <div className="shrink-0 text-right">
                  <div className="num font-semibold">{m.value ?? m.latest ?? '–'}{(m.value ?? m.latest) != null && m.unit ? ` ${m.unit}` : ''}</div>
                  <div className="text-[11px]" style={{ color: t.color }}>{t.icon} {t.label}</div>
                </div>
              </div>
            )
          })}
        </div>
      </Card>

      <Card title="Risk factors for heart disease" eyebrow="From your record and logs">
        <FactorList title="You can change" items={d.risk_factors.modifiable} />
        <FactorList title="You cannot change" items={d.risk_factors.non_modifiable} />
        {d.risk_factors.other.length > 0 && <FactorList title="Other conditions on record" items={d.risk_factors.other} />}
        {d.ascvd && <p className="mt-3 rounded-lg bg-sunk px-3 py-2 text-[13px]"><b>{d.ascvd.label}:</b> {d.ascvd.text}</p>}
      </Card>
      <p className="px-1 text-[11.5px] text-muted">{d.disclaimer} Labels: {Object.values(d.data_labels).join(' · ')}.</p>
    </>
  )
}

function FactorList({ title, items }) {
  return (
    <div className="mb-3">
      <div className="mb-1 text-[13px] font-semibold text-muted">{title}</div>
      <div className="space-y-1.5">
        {items.map((f) => {
          const c = FACTOR[f.status]
          return (
            <div key={f.key} className="flex items-center gap-2 rounded-xl border border-line px-3 py-2 text-[13.5px]">
              <span className="min-w-0 flex-1"><b>{f.label}</b><span className="block text-[12px] text-muted">{f.detail}</span></span>
              <span className="tag" style={{ color: c.color, background: c.bg }}>{c.label}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function Explain({ ctx }) {
  const { api, pid } = ctx
  const s = usePoll(() => api.heartSummary(pid), [pid], 0)
  if (s.error) return <ErrorLine error={s.error} />
  if (!s.data) return <Empty>Loading…</Empty>
  const x = s.data.sections
  const List = ({ items, empty }) => items.length ? <ul className="list-disc space-y-1 pl-5 text-[14px]">{items.map((i, n) => <li key={n}>{i}</li>)}</ul> : <p className="text-[13.5px] text-muted">{empty}</p>
  return (
    <>
      <Card eyebrow="Current risk" title="Explain my heart health" right={s.data.simulated && <SimTag />}>
        <p className="text-[14.5px]">{x.current_risk}</p>
        <p className="mt-2 text-[12.5px] text-muted">{s.data.check_meaning}</p>
      </Card>
      <Card title="Main factors" eyebrow="What the model weighed most">
        {x.main_factors.length ? (
          <ul className="space-y-1.5 text-[14px]">
            {x.main_factors.map((f, n) => (
              <li key={n} className="rounded-xl bg-sunk px-3 py-2"><b>{f.factor}</b> <span className="text-muted">{f.effect} the level</span>{f.detail && <span className="block text-[12.5px] text-muted">{f.detail}</span>}
                {f.modifiable != null && <span className="text-[11.5px]" style={{ color: f.modifiable ? 'var(--low)' : 'var(--muted)' }}>{f.modifiable ? 'Something you can work on' : 'Not something you can change'}</span>}</li>
            ))}
          </ul>
        ) : <p className="text-[13.5px] text-muted">No explanation is available for the latest check.</p>}
      </Card>
      <Card title="Positive trends"><List items={x.positive_trends} empty="Nothing to show yet. Keep logging in Wellness and wearing the watch." /></Card>
      <Card title="Areas to watch"><List items={x.areas_to_watch} empty="Nothing flagged right now." /></Card>
      <Card title="Lifestyle focus"><List items={x.lifestyle_focus} empty="" /></Card>
      <Card title="Next step"><p className="text-[14.5px] font-semibold">{x.next_step}</p>
        {x.missing_information.length > 0 && <p className="mt-2 text-[12.5px] text-muted">Not on your record: {x.missing_information.join(', ')}.</p>}</Card>
      <p className="px-1 text-[11.5px] text-muted">{s.data.generated_by}. {s.data.disclaimer}</p>
    </>
  )
}

function Reports({ ctx }) {
  const { api, pid, ro } = ctx
  const list = usePoll(() => api.heartReports(pid), [pid], 0)
  const [open, setOpen] = useState(null)
  const [err, setErr] = useState(null)
  const show = async (w) => { setErr(null); try { setOpen(await api.heartReport(pid, w)) } catch (e) { setErr(e) } }
  const now = async () => { setErr(null); try { setOpen(await api.heartReportNow(pid)); list.refresh() } catch (e) { setErr(e) } }
  if (list.error) return <ErrorLine error={list.error} />
  return (
    <>
      <Card title="Weekly cardiac health reports" eyebrow="Made automatically each week" right={<button className="btn min-h-9 text-[13px]" disabled={ro} onClick={now}>This week so far</button>}>
        <ErrorLine error={err} />
        {(list.data || []).length ? (
          <div className="divide-y divide-line">
            {list.data.map((r) => (
              <button key={r.week_start} className="flex w-full items-center gap-3 py-2.5 text-left text-[14px]" onClick={() => show(r.week_start)}>
                <Icon name="note" size={18} className="text-accent" />
                <span className="min-w-0 flex-1"><b>Week of {new Date(r.week_start).toLocaleDateString([], { day: 'numeric', month: 'short' })}</b><span className="block text-[12.5px] text-muted">{r.headline}</span></span>
                <Icon name="chevron" size={16} className="text-muted" />
              </button>
            ))}
          </div>
        ) : <Empty>No reports yet.</Empty>}
      </Card>
      {open && <Report r={open} />}
    </>
  )
}

function Report({ r }) {
  const t = TREND[r.heart_rate.trend] || TREND.MISSING
  return (
    <Card eyebrow={`${r.week_start} to ${r.week_end}`} title={r.headline} right={r.simulated && <SimTag title={r.watch_window.clock} />}>
      <div className="grid grid-cols-3 gap-2 text-center text-[12px]">
        {['LOW', 'MODERATE', 'HIGH'].map((k) => <div key={k} className="rounded-xl bg-sunk p-2"><div className="num text-[18px] font-semibold">{r.risk_trend.checks_by_level[k]}</div>{LEVEL[k]} checks</div>)}
      </div>
      <p className="mt-2 text-[13.5px]">Heart rate over the week: <span style={{ color: t.color }}>{t.icon} {t.label}</span></p>
      {r.sleep.average_h != null && <p className="text-[13.5px]">Sleep: {r.sleep.average_h} h on average ({r.sleep.nights_logged} nights logged)</p>}
      {r.activity.logged_active_minutes != null && <p className="text-[13.5px]">Active minutes logged: {r.activity.logged_active_minutes}</p>}
      {r.positive_changes.length > 0 && <><div className="mt-3 text-[13px] font-semibold" style={{ color: 'var(--low)' }}>Positive changes</div><ul className="list-disc pl-5 text-[13.5px]">{r.positive_changes.map((x, i) => <li key={i}>{x}</li>)}</ul></>}
      {r.needs_attention.length > 0 && <><div className="mt-3 text-[13px] font-semibold" style={{ color: 'var(--high)' }}>Needs attention</div><ul className="list-disc pl-5 text-[13.5px]">{r.needs_attention.map((x, i) => <li key={i}>{x}</li>)}</ul></>}
      <div className="mt-3 text-[13px] font-semibold">Suggestions</div>
      <ul className="list-disc pl-5 text-[13.5px]">{r.suggestions.map((x, i) => <li key={i}>{x}</li>)}</ul>
      <p className="mt-2 text-[11.5px] text-muted">{r.disclaimer} Watch data: {r.watch_window.clock}.</p>
    </Card>
  )
}
