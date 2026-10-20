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
    <section className={`card p-5 ${className}`}>
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
