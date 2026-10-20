export const TIER = {
  LOW: { label: 'Low', fg: 'var(--low)', bg: 'var(--low-soft)' },
  MODERATE: { label: 'Moderate', fg: 'var(--mod)', bg: 'var(--mod-soft)' },
  HIGH: { label: 'High', fg: 'var(--high)', bg: 'var(--high-soft)' },
  EMERGENCY: { label: 'Emergency', fg: 'var(--emg)', bg: 'var(--emg-soft)' },
  NONE: { label: 'No score', fg: 'var(--muted)', bg: 'var(--sunk)' },
}

// Only a TRIGGERED emergency rule is critical and labelled Emergency. REVIEW_REQUIRED (re-measure, contact the doctor)
// and NOT_ASSESSABLE keep the patient's own risk level.
export function tierKey(tier, emergencyState, status) {
  if (emergencyState === 'TRIGGERED') return 'EMERGENCY'
  if (status && status !== 'OK') return 'NONE'
  return TIER[tier] ? tier : 'NONE'
}

export const pct = (x, d = 1) => (x == null || Number.isNaN(x) ? '–' : `${(x * 100).toFixed(d)}%`)
export const num = (x, d = 0) => (x == null || Number.isNaN(Number(x)) ? '–' : Number(x).toFixed(d))

// The backend mixes "2026-10-19T07:11:00", "2026-10-19 07:11:00" and "...Z". The simulated clock is UTC, so show UTC wall time.
export function parseTs(s) {
  if (!s) return null
  let t = String(s).replace(' ', 'T')
  if (!/[zZ]|[+-]\d\d:?\d\d$/.test(t)) t += 'Z'
  const d = new Date(t)
  return Number.isNaN(d.getTime()) ? null : d
}
export function hhmm(s) {
  const d = parseTs(s)
  return d ? d.toISOString().slice(11, 16) : '–'
}
export function dayTime(s) {
  const d = parseTs(s)
  return d ? `${d.toISOString().slice(5, 10)} ${d.toISOString().slice(11, 16)}` : '–'
}

export const SIGNALS = {
  hr: { label: 'Heart rate', short: 'HR', unit: 'bpm', d: 0 },
  spo2: { label: 'Oxygen saturation', short: 'SpO2', unit: '%', d: 0 },
  resp: { label: 'Breathing rate', short: 'Breathing', unit: '/min', d: 0 },
  rr: { label: 'Breathing rate', short: 'Breathing', unit: '/min', d: 0 },
  hrv_rmssd: { label: 'HRV (RMSSD)', short: 'HRV', unit: 'ms', d: 0 },
  ecg_qtc: { label: 'QTc', short: 'QTc', unit: 'ms', d: 0 },
}

export const RHYTHM = { sinus: 'Regular (sinus)', af_flutter: 'Irregular (possible AF)', tachy: 'Fast', brady: 'Slow', paced: 'Paced', other: 'Other', unknown: 'Unknown' }

// Appointment times are clinic wall-clock strings ("2026-10-23T11:00"); format them as written, without time-zone shifts.
const WD = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
const MO = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
export function apptDay(s) {
  const [y, m, d] = String(s).slice(0, 10).split('-').map(Number)
  const x = new Date(Date.UTC(y, m - 1, d))
  return `${WD[x.getUTCDay()]} ${d} ${MO[m - 1]}`
}
export const apptTime = (s) => String(s).slice(11, 16)
