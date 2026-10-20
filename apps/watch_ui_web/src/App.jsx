import { useMemo, useState } from 'react'
import { loadSession, makeApi, saveSession } from './api'
import Login from './Login'
import Watch from './Watch'

// The Wear OS watch screens in a browser, for demos without a watch or Android Studio. Opens a patient with
// ?patient=<id> (the doctor dashboard's "Open watch view" links here); a patient account opens its own patient.
export default function App() {
  const [session, setSession] = useState(loadSession)
  const logout = () => { saveSession(null); setSession(null) }
  const api = useMemo(() => (session ? makeApi(session, logout) : null), [session])
  if (!session) return <Login onLogin={(s) => { saveSession(s); setSession(s) }} />
  return <Watch api={api} session={session} onLogout={logout} />
}
