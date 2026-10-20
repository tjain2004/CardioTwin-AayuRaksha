import { useState } from 'react'
import { hhmm, dayTime, parseTs, pct } from '../format'

const TIER_COLOR = { LOW: 'var(--low)', MODERATE: 'var(--mod)', HIGH: 'var(--high)' }

// Risk over time: hourly retrospective trajectory from the engine plus the backend's evaluation history.
export function RiskChart({ points, thresholds, height = 220 }) {
  const [hover, setHover] = useState(null)
  const pts = points.filter((p) => p.score != null && parseTs(p.t)).map((p) => ({ ...p, x: parseTs(p.t).getTime() }))
  if (pts.length < 2) return <div className="flex h-[160px] items-center justify-center rounded-md bg-sunk text-[14px] text-muted">Not enough scored hours yet for a trend.</div>
  const W = 640, H = height, L = 44, R = 12, T = 12, B = 26
  const x0 = Math.min(...pts.map((p) => p.x)), x1 = Math.max(...pts.map((p) => p.x))
  const yMax = Math.max(0.05, ...pts.map((p) => p.score), (thresholds?.high || 0) * 1.4) * 1.1
  const sx = (x) => L + ((x - x0) / Math.max(1, x1 - x0)) * (W - L - R)
  const sy = (y) => T + (1 - y / yMax) * (H - T - B)
  const path = pts.map((p, i) => `${i ? 'L' : 'M'}${sx(p.x).toFixed(1)},${sy(p.score).toFixed(1)}`).join('')
  const ticks = [0, yMax / 2, yMax].map((v) => +v.toFixed(3))
  const xt = [x0, (x0 + x1) / 2, x1]
  const onMove = (e) => {
    const r = e.currentTarget.getBoundingClientRect()
    const x = ((e.clientX - r.left) / r.width) * W
    let best = null
    for (const p of pts) if (!best || Math.abs(sx(p.x) - x) < Math.abs(sx(best.x) - x)) best = p
    setHover(best)
  }
  return (
    <div className="relative">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="Model output over time" onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        {ticks.map((v) => (
          <g key={v}>
            <line x1={L} x2={W - R} y1={sy(v)} y2={sy(v)} stroke="var(--line)" strokeWidth="1" />
            <text x={L - 6} y={sy(v) + 4} textAnchor="end" fontSize="11" fill="var(--muted)" fontFamily="var(--font-mono)">{(v * 100).toFixed(0)}%</text>
          </g>
        ))}
        {thresholds && ['moderate', 'high'].map((k) => thresholds[k] < yMax && (
          <g key={k}>
            <line x1={L} x2={W - R} y1={sy(thresholds[k])} y2={sy(thresholds[k])} stroke={k === 'high' ? 'var(--high)' : 'var(--mod)'} strokeDasharray="4 4" strokeWidth="1.2" />
            <text x={W - R} y={sy(thresholds[k]) - 4} textAnchor="end" fontSize="10.5" fill={k === 'high' ? 'var(--high)' : 'var(--mod)'} fontFamily="var(--font-mono)">{k.toUpperCase()} ≥ {pct(thresholds[k])}</text>
          </g>
        ))}
        {xt.map((x, i) => (
          <text key={i} x={sx(x)} y={H - 8} textAnchor={i === 0 ? 'start' : i === 2 ? 'end' : 'middle'} fontSize="11" fill="var(--muted)" fontFamily="var(--font-mono)">{dayTime(new Date(x).toISOString())}</text>
        ))}
        <path d={path} fill="none" stroke="var(--accent)" strokeWidth="2" strokeLinejoin="round" />
        {pts.map((p, i) => (
          <circle key={i} cx={sx(p.x)} cy={sy(p.score)} r={hover === p ? 5 : 3} fill={TIER_COLOR[p.tier] || 'var(--muted)'} stroke="var(--surface)" strokeWidth="1.5" />
        ))}
        {hover && <line x1={sx(hover.x)} x2={sx(hover.x)} y1={T} y2={H - B} stroke="var(--muted)" strokeWidth="1" />}
      </svg>
      {hover && (
        <div className="pointer-events-none absolute top-1 rounded-md border border-line bg-surface px-2 py-1 text-[12px] shadow-sm"
          style={{ left: `${Math.min(70, (sx(hover.x) / W) * 100)}%` }}>
          <div className="num font-semibold">{pct(hover.score)} · {hover.tier}</div>
          <div className="text-muted">{dayTime(hover.t)} · {hover.kind}</div>
        </div>
      )}
    </div>
  )
}

export function Sparkline({ values, color = 'var(--accent)', height = 36, band }) {
  const v = values.filter((x) => x != null)
  if (v.length < 2) return <div style={{ height }} />
  const W = 160, H = height, pad = 3
  let lo = Math.min(...v), hi = Math.max(...v)
  if (band) { lo = Math.min(lo, band[0]); hi = Math.max(hi, band[1]) }
  if (hi - lo < 1e-6) { hi += 1; lo -= 1 }
  const sx = (i) => (i / (v.length - 1)) * W
  const sy = (y) => pad + (1 - (y - lo) / (hi - lo)) * (H - 2 * pad)
  const d = v.map((y, i) => `${i ? 'L' : 'M'}${sx(i).toFixed(1)},${sy(y).toFixed(1)}`).join('')
  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="w-full" style={{ height }} aria-hidden="true">
      {band && <rect x="0" width={W} y={sy(band[1])} height={Math.max(0, sy(band[0]) - sy(band[1]))} fill="var(--accent-soft)" />}
      <path d={d} fill="none" stroke={color} strokeWidth="1.6" vectorEffect="non-scaling-stroke" />
    </svg>
  )
}

// SHAP group contributions in log-odds; red raises, green lowers (same as the prototype).
export function ShapBars({ steps, max = 10 }) {
  const rows = [...steps].sort((a, b) => Math.abs(b.shap_log_odds) - Math.abs(a.shap_log_odds)).slice(0, max)
  const m = Math.max(0.01, ...rows.map((r) => Math.abs(r.shap_log_odds)))
  return (
    <div className="space-y-1.5">
      {rows.map((r) => {
        const w = (Math.abs(r.shap_log_odds) / m) * 50
        const up = r.shap_log_odds > 0
        return (
          <div key={r.label} className="grid grid-cols-[minmax(0,10rem)_1fr_3.5rem] items-center gap-2 text-[13px]">
            <div className="truncate" title={r.label}>{r.label}</div>
            <div className="relative h-3 rounded-sm bg-sunk">
              <div className="absolute top-0 h-3 rounded-sm" style={{ left: up ? '50%' : `${50 - w}%`, width: `${w}%`, background: up ? 'var(--high)' : 'var(--low)' }} />
              <div className="absolute left-1/2 top-[-2px] h-4 w-px bg-line" />
            </div>
            <div className="num text-right">{up ? '+' : ''}{r.shap_log_odds.toFixed(2)}</div>
          </div>
        )
      })}
    </div>
  )
}

export { hhmm }
