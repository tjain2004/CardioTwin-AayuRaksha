import { useEffect, useRef, useState } from 'react'
import { usePoll } from '../hooks'
import { ErrorLine, Icon, Sheet, Toggle } from '../components/ui'
import { parseTs } from '../format'

// "Ask CardioTwin": the AI health assistant. Emergency words are checked here on the phone as well as on the server,
// so the Call 112 card appears at once, even with no network and before any AI reply. The input is one text box that
// a future voice module can fill (speech-to-text in front of the same send()).
const URGENT = [
  /chest\s*(pain|pressure|tight)|crushing|pain (in|spreading to) (my )?(left )?(arm|jaw)/i,
  /can'?t breathe|cannot breathe|short(ness)? of breath|breathless|gasping/i,
  /faint(ed|ing)?|passed out|collapsed?|unconscious/i,
  /face (is )?droop|slurred|can'?t (move|lift) (my )?(arm|leg)|stroke/i,
  /kill myself|end my life|suicid|self.?harm/i,
  /seene (mein|me) dard|saans (nahi|phool)|behosh|सीने में दर्द|साँस|सांस|बेहोश/i,
]
const NEGATED = /\b(no|not|don'?t|never|without)\b[^.]{0,20}(chest|breath|faint|pain)/i
const INFO = /\b(what|which|how|signs? of|symptoms? of|kya hai)\b/i

export function localTriage(text) {
  if (!text || NEGATED.test(text)) return null
  if (!URGENT.some((r) => r.test(text))) return null
  return INFO.test(text) && !/\b(i|me|my|mujhe|mere)\b/i.test(text) ? 'possible' : 'urgent'
}

const UI = {
  en: { title: 'Ask CardioTwin', placeholder: 'Ask about your heart, food, activity…', send: 'Send', thinking: 'Thinking…', share: 'Use my health data',
    shareSub: 'Lets the assistant read your latest heart check, readings and record', newChat: 'New chat', history: 'Earlier chats',
    never: 'The app never calls 112 for you: it opens your dialler and you press Call.', call: 'Call 112', sendCheckin: 'Send as check-in', nearby: 'Nearby hospitals', offline: 'OFFLINE', ai: 'AI', rules: 'Safety rules',
    suggestions: ['Explain my heart check', 'What is CAD?', 'What should I eat today?', 'How much should I walk?'] },
  hi: { title: 'CardioTwin से पूछें', placeholder: 'दिल, खाना, व्यायाम के बारे में पूछें…', send: 'भेजें', thinking: 'सोच रहा है…', share: 'मेरा स्वास्थ्य डेटा इस्तेमाल करें',
    shareSub: 'सहायक आपकी हाल की हार्ट जाँच, रीडिंग और रिकॉर्ड पढ़ सकेगा', newChat: 'नई बातचीत', history: 'पिछली बातचीत',
    never: 'ऐप खुद 112 पर कॉल नहीं करता: यह आपका डायलर खोलता है और कॉल आप दबाते हैं।', call: '112 पर कॉल करें', sendCheckin: 'चेक-इन के रूप में भेजें', nearby: 'पास के अस्पताल', offline: 'ऑफ़लाइन', ai: 'AI', rules: 'सुरक्षा नियम',
    suggestions: ['मेरी हार्ट जाँच समझाइए', 'CAD क्या है?', 'आज मुझे क्या खाना चाहिए?', 'मुझे कितना चलना चाहिए?'] },
}

export default function AssistantSheet({ ctx, onClose, onNearby }) {
  const { api, pid, session, ro, settings, refreshAll } = ctx
  const [lang, setLang] = useState(settings.data?.language === 'hi' ? 'hi' : 'en')
  const t = UI[lang]
  const status = usePoll(() => api.assistantStatus(), [], 0)
  const consent = usePoll(() => api.chatConsent(pid), [pid], 0)
  const convs = usePoll(() => api.chatConversations(pid), [pid], 0)
  const [cid, setCid] = useState(null)
  const [msgs, setMsgs] = useState([])
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const [showHistory, setShowHistory] = useState(false)
  const end = useRef(null)
  const canShare = !ro && ['patient', 'caregiver'].includes(session.role)
  useEffect(() => { end.current?.scrollIntoView({ block: 'end' }) }, [msgs, busy])

  const open = async (id) => {
    setErr(null); setShowHistory(false)
    try { const c = await api.chatConversation(id); setCid(id); setMsgs(c.messages) } catch (e) { setErr(e) }
  }
  const send = async (q) => {
    const m = (q ?? text).trim()
    if (!m || busy) return
    setText(''); setErr(null)
    const local = localTriage(m)
    const now = new Date().toISOString()
    setMsgs((x) => [...x, { id: `local-${now}`, role: 'user', content: m, created_at: now, local_emergency: local }])
    setBusy(true)
    try {
      const r = await api.chatSend(pid, m, cid, lang)
      setCid(r.conversation_id)
      setMsgs((x) => [...x.slice(0, -1), { ...r.user_message, local_emergency: local }, r.reply])
      convs.refresh()
    } catch (e) { setErr(e) } finally { setBusy(false) }
  }
  const st = status.data
  return (
    <Sheet title={t.title} onClose={onClose} right={
      <div className="flex items-center gap-1">
        <button className="btn min-h-8 px-2.5 text-[12.5px]" onClick={() => setLang(lang === 'en' ? 'hi' : 'en')} aria-label="Switch language">{lang === 'en' ? 'हिन्दी' : 'English'}</button>
        <button className="btn min-h-8 px-2.5 text-[12.5px]" onClick={() => setShowHistory(!showHistory)}>{t.history}</button>
      </div>}>
      <div className="flex flex-wrap items-center gap-2 text-[12px] text-muted">
        {st && <span className="tag" style={st.mode === 'ai' ? { background: 'var(--accent-soft)', color: 'var(--accent)' } : { background: 'var(--sunk)', color: 'var(--muted)' }}>{st.mode === 'ai' ? t.ai : t.offline}</span>}
        <span className="min-w-0 flex-1">{st?.disclaimer}</span>
      </div>
      {st?.note && <p className="text-[12px] text-muted">{st.note}</p>}
      {canShare && consent.data && (
        <div className="card px-3">
          <Toggle checked={consent.data.share_health_data} label={t.share} sub={t.shareSub}
            onChange={async (v) => { try { await api.setChatConsent(pid, v); consent.refresh() } catch (e) { setErr(e) } }} />
        </div>
      )}
      {showHistory && (
        <div className="card divide-y divide-line">
          <button className="w-full px-3 py-2.5 text-left text-[14px] font-semibold text-accent" onClick={() => { setCid(null); setMsgs([]); setShowHistory(false) }}>+ {t.newChat}</button>
          {(convs.data || []).map((c) => (
            <div key={c.conversation_id} className="flex items-center gap-2 px-3 py-2 text-[13.5px]">
              <button className="min-w-0 flex-1 truncate text-left" onClick={() => open(c.conversation_id)}>{c.title || 'Chat'} <span className="text-[12px] text-muted">· {parseTs(c.updated_at)?.toLocaleDateString()}</span></button>
              <button className="text-muted" aria-label="Delete chat" onClick={async () => { await api.chatDelete(c.conversation_id).catch(setErr); if (c.conversation_id === cid) { setCid(null); setMsgs([]) } convs.refresh() }}><Icon name="trash" size={17} /></button>
            </div>
          ))}
        </div>
      )}

      <div className="space-y-2.5 pb-28" aria-live="polite">
        {!msgs.length && (
          <div className="space-y-2">
            <p className="text-[14px] text-muted">{lang === 'hi' ? 'नमस्ते! मैं आपकी हार्ट जाँच समझा सकता हूँ और खाने, व्यायाम, पानी और नींद पर सामान्य जानकारी दे सकता हूँ।' : 'Hello! I can explain your heart check and answer general questions about food, activity, water, sleep and warning signs.'}</p>
            <div className="flex flex-wrap gap-1.5">{t.suggestions.map((s) => <button key={s} className="btn min-h-8 text-[13px]" disabled={ro} onClick={() => send(s)}>{s}</button>)}</div>
          </div>
        )}
        {msgs.map((m) => <Bubble key={m.id} m={m} t={t} ctx={ctx} onNearby={onNearby} refreshAll={refreshAll} />)}
        {busy && <div className="w-fit rounded-2xl bg-sunk px-3 py-2 text-[14px] text-muted">{t.thinking}</div>}
        <ErrorLine error={err} />
        <div ref={end} />
      </div>

      <form className="fixed inset-x-0 bottom-0 z-50 border-t border-line bg-surface p-2" style={{ paddingBottom: 'calc(env(safe-area-inset-bottom) + 8px)' }}
        onSubmit={(e) => { e.preventDefault(); send() }}>
        <div className="mx-auto flex max-w-md items-end gap-2">
          <textarea className="input min-h-11 flex-1 resize-none py-2.5" rows={1} maxLength={st?.max_message_chars || 2000} placeholder={t.placeholder} value={text} disabled={ro}
            onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }} aria-label={t.placeholder} />
          <button className="btn btn-primary min-h-11" disabled={ro || busy || !text.trim()}>{t.send}</button>
        </div>
      </form>
    </Sheet>
  )
}

function Bubble({ m, t, ctx, onNearby, refreshAll }) {
  const mine = m.role === 'user'
  const card = m.emergency
  const time = parseTs(m.created_at)?.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  return (
    <div className={`flex flex-col ${mine ? 'items-end' : 'items-start'}`}>
      {mine && m.local_emergency === 'urgent' && !card && <EmergencyCard card={{ title: 'These symptoms can be an emergency', message: 'Call 112 now. Stop any activity and sit or lie down.', level: 'urgent', call_action: { uri: 'tel:112' } }} t={t} ctx={ctx} onNearby={onNearby} refreshAll={refreshAll} />}
      {!mine && card && <EmergencyCard card={card} t={t} ctx={ctx} onNearby={onNearby} refreshAll={refreshAll} />}
      {!(m.source === 'safety_rules' && card) && <div className={`max-w-[88%] whitespace-pre-wrap rounded-2xl px-3 py-2 text-[14.5px] leading-snug ${mine ? 'bg-accent text-on-accent' : 'border border-line bg-surface'}`}>{m.content}</div>}
      <div className="mt-0.5 px-1 text-[11px] text-muted">
        {time}
        {!mine && m.source && <> · {m.source === 'ai' ? t.ai : m.source === 'safety_rules' ? t.rules : t.offline}</>}
        {!mine && m.used_health_data && <> · used your data</>}
      </div>
    </div>
  )
}

function EmergencyCard({ card, t, ctx, onNearby, refreshAll }) {
  const { api, pid, ro } = ctx
  const [sent, setSent] = useState(null)
  const urgent = card.level === 'urgent'
  const checkin = async () => {
    try { const r = await api.symptoms(pid, card.checkin_symptoms); setSent(r.checkin?.outcome || 'Sent to your care team.'); refreshAll() } catch (e) { setSent(e.message) }
  }
  return (
    <section role="alert" className="mb-1.5 w-full rounded-2xl p-3" style={{ background: urgent ? 'var(--emg)' : 'var(--emg-soft)', color: urgent ? 'var(--on-emg)' : 'var(--ink)' }}>
      <b className="block font-display text-[16px]">{card.title}</b>
      <p className="mt-1 text-[14px]">{card.message}</p>
      <div className="mt-2 grid grid-cols-2 gap-2">
        <a href={card.call_action?.uri || 'tel:112'} className="btn min-h-11 font-bold" style={{ background: '#fff', color: 'var(--emg)', borderColor: '#fff' }}>{t.call}</a>
        {(card.other_numbers || []).slice(0, 1).map((n) => <a key={n.number} href={n.uri} className="btn min-h-11" style={{ background: 'transparent', color: 'inherit', borderColor: 'currentColor' }}>{n.label}</a>)}
        {card.checkin_symptoms && !ro && <button className="btn min-h-11" style={{ background: 'transparent', color: 'inherit', borderColor: 'currentColor' }} disabled={!!sent} onClick={checkin}>{t.sendCheckin}</button>}
        {onNearby && <button className="btn min-h-11" style={{ background: 'transparent', color: 'inherit', borderColor: 'currentColor' }} onClick={onNearby}>{t.nearby}</button>}
      </div>
      {sent && <p className="mt-1.5 text-[12.5px]">{sent}</p>}
      {card.note && <p className="mt-1.5 text-[11.5px] opacity-85">{card.note}</p>}
      <p className="mt-1 text-[11.5px] opacity-85">{t.never}</p>
    </section>
  )
}
