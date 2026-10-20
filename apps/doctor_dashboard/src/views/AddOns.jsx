import { useEffect, useRef, useState } from 'react'
import { usePoll } from '../hooks'
import { Card, Empty, ErrorLine, SimTag } from '../components/ui'

// Doctor-side views of the add-on modules: the CAD risk-factor overview (heart_health.py) and the "Ask CardioTwin"
// assistant in clinician mode (chatbot.py). Clinicians see the model score in the assistant's context; the assistant
// never computes or changes it. The patient decides whether the assistant may use their data.
const enc = encodeURIComponent
const FACTOR = { PRESENT: ['Present', 'var(--high)'], NOT_PRESENT: ['Not present', 'var(--low)'], UNKNOWN: ['Not recorded', 'var(--muted)'] }

export function HeartOverviewCard({ api, pid }) {
  const ov = usePoll(() => api.get(`/api/patients/${enc(pid)}/heart/overview`), [pid], 60000)
  const d = ov.data
  if (ov.error) return <ErrorLine error={ov.error} />
  if (!d) return null
  const rows = [...d.risk_factors.modifiable, ...d.risk_factors.non_modifiable, ...d.risk_factors.other]
  return (
    <Card eyebrow="CAD risk-factor profile (record and logs, not a model output)" title="Heart health overview" right={<SimTag show={d.simulated} />}>
      <div className="grid gap-x-6 gap-y-1 text-[13px] md:grid-cols-2">
        {rows.map((f) => (
          <div key={f.key} className="flex items-baseline gap-2 border-b border-line py-1">
            <span className="w-36 shrink-0 font-semibold">{f.label}</span>
            <span className="min-w-0 flex-1 text-muted">{f.detail}</span>
            <span className="shrink-0 text-[12px]" style={{ color: FACTOR[f.status][1] }}>{FACTOR[f.status][0]}</span>
          </div>
        ))}
      </div>
      {d.ascvd && <p className="mt-2 text-[13px]"><b>ASCVD 10-year (from record):</b> {d.ascvd.value_pct}% ({d.ascvd.band})</p>}
      <p className="mt-1 text-[12px] text-muted">24-hour deterioration check trend: {d.status.trend?.text}. {d.disclaimer}</p>
    </Card>
  )
}

export function AssistantDrawer({ api, pid }) {
  const [open, setOpen] = useState(false)
  const [cid, setCid] = useState(null)
  const [msgs, setMsgs] = useState([])
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const status = usePoll(() => api.get('/api/assistant/status'), [], 0)
  const consent = usePoll(() => api.get(`/api/patients/${enc(pid)}/assistant/consent`), [pid, open], 0)
  const end = useRef(null)
  useEffect(() => { end.current?.scrollIntoView({ block: 'end' }) }, [msgs, busy])
  useEffect(() => { setCid(null); setMsgs([]) }, [pid])
  const send = async () => {
    const m = text.trim()
    if (!m || busy) return
    setText(''); setErr(null); setBusy(true)
    try {
      const r = await api.post(`/api/patients/${enc(pid)}/assistant/messages`, { message: m, conversation_id: cid })
      setCid(r.conversation_id); setMsgs((x) => [...x, r.user_message, r.reply])
    } catch (e) { setErr(e) } finally { setBusy(false) }
  }
  if (!open) return <button className="btn btn-primary fixed bottom-5 right-5 z-30 min-h-12 shadow-lg" onClick={() => setOpen(true)}>Ask CardioTwin</button>
  return (
    <aside className="fixed inset-y-0 right-0 z-40 flex w-full max-w-md flex-col border-l border-line bg-surface shadow-xl" aria-label="Ask CardioTwin">
      <header className="flex items-center gap-2 border-b border-line px-4 py-3">
        <div className="min-w-0 flex-1"><b className="font-display text-[17px]">Ask CardioTwin</b><div className="num text-[12px] text-muted">{pid} · {status.data?.mode === 'ai' ? 'AI' : 'OFFLINE'}</div></div>
        <button className="btn" onClick={() => { setCid(null); setMsgs([]) }}>New</button>
        <button className="btn" onClick={() => setOpen(false)}>Close</button>
      </header>
      <div className="flex-1 space-y-2 overflow-y-auto px-4 py-3 text-[14px]">
        <p className="text-[12px] text-muted">{status.data?.disclaimer}</p>
        {consent.data && !consent.data.share_health_data && <p className="rounded-md bg-sunk px-3 py-2 text-[12.5px]">The patient has not allowed the assistant to use their data, so it answers generally only.</p>}
        {!msgs.length && <Empty>Ask for a plain-language summary of the latest result, the SHAP reasons, or what data is missing or stale.</Empty>}
        {msgs.map((m) => (
          <div key={m.id} className={m.role === 'user' ? 'ml-auto w-fit max-w-[90%] rounded-xl bg-accent px-3 py-2 text-on-accent' : 'w-fit max-w-[95%] whitespace-pre-wrap rounded-xl border border-line px-3 py-2'}>
            {m.content}
            <div className="mt-0.5 text-[11px] opacity-70">{new Date(m.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}{m.source ? ` · ${m.source}` : ''}</div>
          </div>
        ))}
        {busy && <div className="text-muted">Thinking…</div>}
        <ErrorLine error={err} />
        <div ref={end} />
      </div>
      <form className="flex gap-2 border-t border-line p-3" onSubmit={(e) => { e.preventDefault(); send() }}>
        <input className="input flex-1" maxLength={2000} placeholder="Ask about this patient's result…" value={text} onChange={(e) => setText(e.target.value)} />
        <button className="btn btn-primary" disabled={busy || !text.trim()}>Send</button>
      </form>
    </aside>
  )
}
