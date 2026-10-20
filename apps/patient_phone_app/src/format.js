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
function wall(s) {
  const [y, m, d] = String(s).slice(0, 10).split('-').map(Number)
  return new Date(Date.UTC(y, m - 1, d))
}
export const apptDay = (s) => { const d = wall(s); return `${WD[d.getUTCDay()]} ${d.getUTCDate()} ${MO[d.getUTCMonth()]}` }
export const apptTime = (s) => String(s).slice(11, 16)
export const apptParts = (s) => { const d = wall(s); return { wd: WD[d.getUTCDay()], day: d.getUTCDate(), mon: MO[d.getUTCMonth()] } }

// The phone's own local date and time ("2026-10-19", "08:05"), used for medicine reminders and date pickers.
export function localDay(d = new Date()) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
export const localHm = (d = new Date()) => `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
export const minutesOf = (hm) => { const [h, m] = hm.split(':').map(Number); return h * 60 + m }

export function greeting(d = new Date()) {
  const h = d.getHours()
  return h < 5 ? 'Good night' : h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening'
}

export function ago(s) {
  const d = parseTs(s)
  if (!d) return ''
  const m = Math.round((Date.now() - d.getTime()) / 60000)
  if (m < 1) return 'just now'
  if (m < 60) return `${m} min ago`
  if (m < 60 * 24) return `${Math.round(m / 60)} h ago`
  const days = Math.round(m / 1440)
  return days === 1 ? 'yesterday' : `${days} days ago`
}

export const SYMPTOM_LABELS = {
  chest_pain: 'Chest pain or pressure',
  pain_spreads_arm_jaw_back: 'Pain spreading to arm, jaw or back',
  breathless: 'Short of breath',
  cold_sweat: 'Cold sweat',
  nausea: 'Feeling sick',
  dizzy_or_fainted: 'Dizzy or fainted',
}
