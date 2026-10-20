// Small helpers shared by the watch screens.

// Colours for the risk engine's tiers and alert severities, as in the Wear OS app's CT.level().
export function levelColor(tierOrSeverity) {
  switch (String(tierOrSeverity || '').toUpperCase()) {
    case 'LOW': return 'var(--low)'
    case 'MODERATE': return 'var(--mod)'
    case 'HIGH': return 'var(--high)'
    case 'CRITICAL': case 'EMERGENCY': return 'var(--emg)'
    default: return 'var(--ink3)'
  }
}

export const fmt = (v, d = 0) => (v == null || Number.isNaN(Number(v)) ? '--' : Number(v).toFixed(d))

// The backend mixes "2026-10-19T07:11:00", "2026-10-19 07:11:00" and "...Z". The simulated clock is UTC.
export function parseTs(s) {
  if (!s) return null
  let t = String(s).replace(' ', 'T')
  if (!/[zZ]|[+-]\d\d:?\d\d$/.test(t)) t += 'Z'
  const d = new Date(t)
  return Number.isNaN(d.getTime()) ? null : d
}
export function hhmm(s) {
  const d = parseTs(s)
  return d ? d.toISOString().slice(11, 16) : '--:--'
}
export const localHm = (d) => `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`

// Demo medication list, the same as the Wear OS app's MedicationSchedule.DEMO. The backend has no schedule yet.
export const MEDS = [
  { generic: 'metoprolol', label: 'Metoprolol 25 mg', mg: 25, times: ['08:00', '20:00'] },
  { generic: 'aspirin', label: 'Aspirin 75 mg', mg: 75, times: ['09:00'] },
]

// The next dose at or after `now` (local time), looking up to two days ahead.
export function nextDose(now = new Date()) {
  const out = []
  for (const m of MEDS) {
    for (const d of [0, 1]) {
      for (const t of m.times) {
        const [h, mi] = t.split(':').map(Number)
        const at = new Date(now.getFullYear(), now.getMonth(), now.getDate() + d, h, mi)
        if (at >= now) out.push({ med: m, at })
      }
    }
  }
  return out.sort((a, b) => a.at - b.at)[0] || null
}
