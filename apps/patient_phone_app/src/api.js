// Thin client for the CardioTwin FastAPI backend (backend/README.md).
const KEY = 'cardiotwin.session'

export function defaultServer() {
  const env = import.meta.env.VITE_API_URL
  if (env) return env.replace(/\/$/, '')
  // Same machine name as the page, port 8000: works on the laptop and on a phone on the same Wi-Fi.
  return `${location.protocol}//${location.hostname || '127.0.0.1'}:8000`
}

export function loadSession() {
  try { return JSON.parse(localStorage.getItem(KEY)) } catch { return null }
}
export function saveSession(s) {
  try { s ? localStorage.setItem(KEY, JSON.stringify(s)) : localStorage.removeItem(KEY) } catch { /* private mode */ }
}

export class ApiError extends Error {
  constructor(status, detail) { super(detail || `HTTP ${status}`); this.status = status }
}

export function makeApi(session, onUnauthorized) {
  const base = session.server
  async function req(method, path, body) {
    const res = await fetch(base + path, {
      method,
      headers: { 'content-type': 'application/json', ...(session.token ? { authorization: `Bearer ${session.token}` } : {}) },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    if (res.status === 401 && onUnauthorized) onUnauthorized()
    const text = await res.text()
    let data = null
    try { data = text ? JSON.parse(text) : null } catch { data = text }
    if (!res.ok) {
      const d = data && data.detail
      throw new ApiError(res.status, typeof d === 'string' ? d : d ? JSON.stringify(d) : text)
    }
    return data
  }
  const pid = encodeURIComponent
  return {
    base,
    get: (p) => req('GET', p),
    post: (p, b) => req('POST', p, b ?? {}),
    put: (p, b) => req('PUT', p, b),
    del: (p) => req('DELETE', p),
    health: () => req('GET', '/api/health'),
    me: () => req('GET', '/api/auth/me'),
    logout: () => req('POST', '/api/auth/logout'),
    patients: () => req('GET', '/api/patients'),
    status: (id) => req('GET', `/api/patients/${pid(id)}/status`),
    history: (id, limit = 300) => req('GET', `/api/patients/${pid(id)}/history?limit=${limit}`),
    features: (id) => req('GET', `/api/patients/${pid(id)}/features`),
    readings: (id, types, limit = 500) => req('GET', `/api/patients/${pid(id)}/readings?types=${types}&limit=${limit}`),
    ehr: (id) => req('GET', `/api/patients/${pid(id)}/ehr`),
    putEhr: (id, body) => req('PUT', `/api/patients/${pid(id)}/ehr`, body),
    alerts: (id, open = false) => req('GET', `/api/patients/${pid(id)}/alerts${open ? '?open=true' : ''}`),
    alertAction: (aid, action, body = {}) => req('POST', `/api/alerts/${pid(aid)}/${action}`, body),
    fallResponse: (aid, answer) => req('POST', `/api/alerts/${pid(aid)}/fall-response`, { answer }),
    symptoms: (id, symptoms, note) => req('POST', `/api/patients/${pid(id)}/symptoms`, { symptoms, note }),
    whatif: (id, changes) => req('POST', `/api/patients/${pid(id)}/whatif`, { changes }),
    sendReadings: (readings) => req('POST', '/api/readings', { readings }),
    simSessions: () => req('GET', '/api/simulator/sessions'),
    simStart: (patient_id) => req('POST', '/api/simulator/sessions', { patient_id }),
    simTrigger: (sid, event) => req('POST', `/api/simulator/sessions/${pid(sid)}/trigger`, { event }),
    simStop: (sid) => req('DELETE', `/api/simulator/sessions/${pid(sid)}`),
    // patient app extras (DEMO appointments, medicines, check-ins, contacts, notifications, settings)
    careTeam: (id) => req('GET', `/api/patients/${pid(id)}/care-team`),
    healthProfile: (id) => req('GET', `/api/patients/${pid(id)}/health-profile`),
    appSettings: (id) => req('GET', `/api/patients/${pid(id)}/app-settings`),
    putAppSettings: (id, body) => req('PUT', `/api/patients/${pid(id)}/app-settings`, body),
    appointments: (id) => req('GET', `/api/patients/${pid(id)}/appointments`),
    slots: (id, date) => req('GET', `/api/patients/${pid(id)}/appointments/slots?date=${date}`),
    book: (id, body) => req('POST', `/api/patients/${pid(id)}/appointments`, body),
    clinicAppointments: (since) => req('GET', `/api/appointments${since ? `?since=${encodeURIComponent(since)}` : ''}`),
    cancelAppointment: (aid, reason) => req('POST', `/api/appointments/${pid(aid)}/cancel`, { reason }),
    rescheduleAppointment: (aid, start_at) => req('POST', `/api/appointments/${pid(aid)}/reschedule`, { start_at }),
    completeAppointment: (aid, body = {}) => req('POST', `/api/appointments/${pid(aid)}/complete`, body),
    medications: (id, day) => req('GET', `/api/patients/${pid(id)}/medications${day ? `?day=${day}` : ''}`),
    addMedication: (id, body) => req('POST', `/api/patients/${pid(id)}/medications`, body),
    updateMedication: (mid, body) => req('PUT', `/api/medications/${pid(mid)}`, body),
    removeMedication: (mid) => req('DELETE', `/api/medications/${pid(mid)}`),
    medTaken: (mid, day, slot, taken = true) => req('POST', `/api/medications/${pid(mid)}/taken`, { day, slot, taken }),
    adherence: (id, today, days = 7) => req('GET', `/api/patients/${pid(id)}/medications/adherence?days=${days}&today=${today}`),
    checkins: (id, limit = 50) => req('GET', `/api/patients/${pid(id)}/symptoms?limit=${limit}`),
    contacts: (id) => req('GET', `/api/patients/${pid(id)}/contacts`),
    addContact: (id, body) => req('POST', `/api/patients/${pid(id)}/contacts`, body),
    updateContact: (cid, body) => req('PUT', `/api/contacts/${pid(cid)}`, body),
    removeContact: (cid) => req('DELETE', `/api/contacts/${pid(cid)}`),
    notifications: (id) => req('GET', `/api/patients/${pid(id)}/notifications`),
    markRead: (id, ids) => req('POST', `/api/patients/${pid(id)}/notifications/read`, { ids }),
    // add-ons: assistant, nearby help (Google Maps), DEMO ambulance, wellness, heart health, caregiver sharing
    assistantStatus: () => req('GET', '/api/assistant/status'),
    chatConsent: (id) => req('GET', `/api/patients/${pid(id)}/assistant/consent`),
    setChatConsent: (id, share_health_data) => req('PUT', `/api/patients/${pid(id)}/assistant/consent`, { share_health_data }),
    chatSend: (id, message, conversation_id, language) => req('POST', `/api/patients/${pid(id)}/assistant/messages`, { message, conversation_id, language }),
    chatConversations: (id) => req('GET', `/api/patients/${pid(id)}/assistant/conversations`),
    chatConversation: (cid) => req('GET', `/api/assistant/conversations/${pid(cid)}`),
    chatDelete: (cid) => req('DELETE', `/api/assistant/conversations/${pid(cid)}`),
    mapsConfig: () => req('GET', '/api/maps/config'),
    nearbyHospitals: (id, lat, lng, radius_m, alert_id) => req('POST', `/api/patients/${pid(id)}/nearby-hospitals`, { lat, lng, radius_m, alert_id }),
    nearbyPharmacies: (id, lat, lng, radius_m) => req('POST', `/api/patients/${pid(id)}/nearby-pharmacies`, { lat, lng, radius_m }),
    route: (id, o, d) => req('POST', `/api/patients/${pid(id)}/route`, { origin_lat: o.lat, origin_lng: o.lng, dest_lat: d.lat, dest_lng: d.lng }),
    requestAmbulance: (id, lat, lng, alert_id, confirm_demo) => req('POST', `/api/patients/${pid(id)}/ambulance`, { lat, lng, alert_id, confirm_demo }),
    activeAmbulance: (id) => req('GET', `/api/patients/${pid(id)}/ambulance/active`),
    ambulance: (rid) => req('GET', `/api/ambulance/${pid(rid)}`),
    cancelAmbulance: (rid) => req('POST', `/api/ambulance/${pid(rid)}/cancel`),
    wellness: (id, day) => req('GET', `/api/patients/${pid(id)}/wellness${day ? `?day=${day}` : ''}`),
    wellnessProgress: (id, day) => req('GET', `/api/patients/${pid(id)}/wellness/progress${day ? `?day=${day}` : ''}`),
    wellnessPrefs: (id) => req('GET', `/api/patients/${pid(id)}/wellness/preferences`),
    putWellnessPrefs: (id, body) => req('PUT', `/api/patients/${pid(id)}/wellness/preferences`, body),
    wellnessLog: (id, day, metric, value, mode = 'set') => req('POST', `/api/patients/${pid(id)}/wellness/log`, { day, metric, value, mode }),
    wellnessReminders: (id) => req('GET', `/api/patients/${pid(id)}/wellness/reminders`),
    heartOverview: (id) => req('GET', `/api/patients/${pid(id)}/heart/overview`),
    heartSummary: (id) => req('GET', `/api/patients/${pid(id)}/heart/summary`),
    heartReports: (id) => req('GET', `/api/patients/${pid(id)}/heart/reports`),
    heartReport: (id, week) => req('GET', `/api/patients/${pid(id)}/heart/reports/${pid(week)}`),
    heartReportNow: (id) => req('POST', `/api/patients/${pid(id)}/heart/reports`),
    caregiverConsent: (id) => req('GET', `/api/patients/${pid(id)}/caregiver-consent`),
    setCaregiverConsent: (id, cid, body) => req('PUT', `/api/patients/${pid(id)}/caregiver-consent/${pid(cid)}`, body),
    wsUrl: (id) => `${base.replace(/^http/, 'ws')}/ws/live/${pid(id)}?token=${encodeURIComponent(session.token)}`,
  }
}

export async function login(server, username, password) {
  const res = await fetch(server.replace(/\/$/, '') + '/api/auth/login', {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ username, password }),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new ApiError(res.status, data.detail || 'Sign-in failed')
  return { server: server.replace(/\/$/, ''), token: data.access_token, role: data.role, patient_id: data.patient_id, display_name: data.display_name, expires_at: data.expires_at }
}
