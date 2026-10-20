import { TIER, tierKey } from '../format'

export function TierBadge({ tier, emergency, status, children }) {
  const k = tierKey(tier, emergency, status)
  const t = TIER[k]
  return (
    <span className="tag" style={{ color: k === 'EMERGENCY' ? 'var(--on-emg)' : t.fg, background: k === 'EMERGENCY' ? 'var(--emg)' : t.bg }}>
      {children || t.label}
    </span>
  )
}

export function SimTag({ show = true, title }) {
  if (!show) return null
  return (
    <span className="tag" title={title || 'Data from the virtual watch, replaying a MIMIC-IV demo ICU stay. Not real watch data.'}
      style={{ color: 'var(--surface)', background: 'var(--sim)' }}>
      Simulated
    </span>
  )
}

export function Card({ title, eyebrow, right, children, className = '' }) {
  return (
    <section className={`card p-4 ${className}`}>
      {(title || eyebrow || right) && (
        <header className="mb-3 flex items-start justify-between gap-3">
          <div>
            {eyebrow && <div className="eyebrow mb-1">{eyebrow}</div>}
            {title && <h2 className="font-display text-[17px] font-bold leading-tight">{title}</h2>}
          </div>
          {right}
        </header>
      )}
      {children}
    </section>
  )
}

export function Stat({ label, value, sub }) {
  return (
    <div>
      <div className="text-[13px] text-muted">{label}</div>
      <div className="num text-[16px] font-semibold">{value}</div>
      {sub && <div className="text-[12px] text-muted">{sub}</div>}
    </div>
  )
}

export function ErrorLine({ error }) {
  if (!error) return null
  return <div className="rounded-md bg-emg-soft px-3 py-2 text-[13px] text-emg">{String(error.message || error)}</div>
}

export function Empty({ children }) {
  return <div className="rounded-md bg-sunk px-3 py-3 text-[14px] text-muted">{children}</div>
}

export function DemoTag({ title }) {
  return (
    <span className="tag" title={title || 'Demo data stored in CardioTwin only. Nothing is sent to a real clinic.'}
      style={{ color: 'var(--accent)', background: 'var(--accent-soft)' }}>
      Demo
    </span>
  )
}

export function Toggle({ checked, onChange, label, sub, disabled }) {
  return (
    <label className="flex min-h-12 items-center gap-3 py-1.5 text-[14px]">
      <span className="min-w-0 flex-1">{label}{sub && <span className="block text-[12px] text-muted">{sub}</span>}</span>
      <button type="button" role="switch" aria-checked={!!checked} disabled={disabled} onClick={() => onChange(!checked)}
        className="relative h-7 w-12 shrink-0 rounded-full transition-colors disabled:opacity-50"
        style={{ background: checked ? 'var(--accent)' : 'var(--line)' }}>
        <span className="absolute top-0.5 h-6 w-6 rounded-full bg-white shadow transition-all" style={{ left: checked ? 22 : 2 }} />
      </button>
    </label>
  )
}

// Full-screen panel that slides over the app (notifications, booking flow).
export function Sheet({ title, onClose, children, right }) {
  return (
    <div role="dialog" aria-modal="true" aria-label={title} className="fixed inset-0 z-40 overflow-y-auto bg-bg">
      <div className="mx-auto min-h-full max-w-md pb-10">
        <header className="sticky top-0 z-10 flex items-center gap-2 border-b border-line bg-surface/95 px-2 py-2 backdrop-blur">
          <button className="flex h-10 w-10 items-center justify-center rounded-full hover:bg-sunk" onClick={onClose} aria-label="Back"><Icon name="back" /></button>
          <h1 className="font-display text-[18px] font-bold">{title}</h1>
          <div className="ml-auto">{right}</div>
        </header>
        <div className="space-y-3 px-4 pt-3">{children}</div>
      </div>
    </div>
  )
}

const ICONS = {
  home: 'M3 11.5 12 4l9 7.5V20a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z',
  calendar: 'M7 3v3M17 3v3M4 8h16M5 5h14a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1zM8 12h2M14 12h2M8 16h2',
  pill: 'M10.5 20.5a5 5 0 0 1-7-7l6-6a5 5 0 0 1 7 7zM7.5 10.5l6 6',
  heart: 'M12 20s-7.5-4.4-7.5-10A4.3 4.3 0 0 1 12 7.4 4.3 4.3 0 0 1 19.5 10C19.5 15.6 12 20 12 20z',
  user: 'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 21a8 8 0 0 1 16 0',
  bell: 'M6 16V11a6 6 0 1 1 12 0v5l2 2H4zM10 20a2 2 0 0 0 4 0',
  back: 'M15 5l-7 7 7 7',
  chevron: 'M9 5l7 7-7 7',
  phone: 'M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a1 1 0 0 1-1 1A16 16 0 0 1 4 5a1 1 0 0 1 1-1z',
  video: 'M3 7h12v10H3zM15 10l6-3v10l-6-3',
  clinic: 'M4 21V8l8-5 8 5v13zM10 21v-5h4v5M12 8v5M9.5 10.5h5',
  plus: 'M12 5v14M5 12h14',
  check: 'M5 12.5l4.5 4.5L19 7.5',
  clock: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7v5l3 2',
  alert: 'M12 3 2 20h20zM12 9v5M12 17.5v.5',
  sos: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7v6M12 16.5v.5',
  team: 'M9 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM2.5 20a6.5 6.5 0 0 1 13 0M16 4.5a3.5 3.5 0 0 1 0 6.5M18 14a6.5 6.5 0 0 1 3.5 6',
  leaf: 'M5 19C5 10 11 5 20 4c-1 9-6 15-15 15zM5 19l7-7',
  gear: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z',
  note: 'M6 3h9l4 4v14H6zM14 3v5h5M9 13h7M9 17h5',
  star: 'M12 3.5l2.6 5.3 5.9.9-4.2 4.1 1 5.8-5.3-2.8-5.3 2.8 1-5.8L3.5 9.7l5.9-.9z',
  trash: 'M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3',
  edit: 'M4 20h4L19 9l-4-4L4 16zM14 6l4 4',
  chat: 'M4 5h16v11H9l-5 4zM8 9.5h8M8 12.5h5',
}

export function Icon({ name, size = 22, className = '', strokeWidth = 1.8, style }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor" strokeWidth={strokeWidth}
      strokeLinecap="round" strokeLinejoin="round" className={className} style={style} aria-hidden="true">
      <path d={ICONS[name] || ''} />
    </svg>
  )
}

export function Disclaimer({ caregiver }) {
  return (
    <p className="px-1 pb-2 text-[11.5px] leading-relaxed text-muted">
      CardioTwin is a research prototype, not a medical device. It does not diagnose and never calls emergency services by itself. If you feel unwell, contact your care team or call 112.
      {caregiver && ' You are viewing as a caregiver.'}
    </p>
  )
}
