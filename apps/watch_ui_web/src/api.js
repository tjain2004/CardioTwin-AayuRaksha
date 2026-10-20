// Thin client for the CardioTwin FastAPI backend (backend/README.md). Same as the patient phone app's api.js.
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
    symptoms: (id, symptoms) => req('POST', `/api/patients/${pid(id)}/symptoms`, { symptoms }),
    whatif: (id, changes) => req('POST', `/api/patients/${pid(id)}/whatif`, { changes }),
    sendReadings: (readings) => req('POST', '/api/readings', { readings }),
    simSessions: () => req('GET', '/api/simulator/sessions'),
    simStart: (patient_id) => req('POST', '/api/simulator/sessions', { patient_id }),
    simTrigger: (sid, event) => req('POST', `/api/simulator/sessions/${pid(sid)}/trigger`, { event }),
    simStop: (sid) => req('DELETE', `/api/simulator/sessions/${pid(sid)}`),
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
