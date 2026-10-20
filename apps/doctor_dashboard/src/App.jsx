import { useMemo, useState } from 'react'
import { loadSession, makeApi, patientAppUrl, saveSession } from './api'
import Login from './views/Login'
import Doctor from './views/Doctor'

// Doctor dashboard (laptop). Patients and caregivers use the separate patient phone app (../patient_phone_app).
export default function App() {
  const [session, setSession] = useState(loadSession)
  const logout = () => { saveSession(null); setSession(null) }
  const api = useMemo(() => (session ? makeApi(session, logout) : null), [session])
  if (!session) return <Login onLogin={(s) => { saveSession(s); setSession(s) }} />
  if (session.role !== 'clinician') {
    return (
      <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center gap-3 px-4">
        <div className="card space-y-3 p-5 text-[14px]">
          <p>This is the doctor dashboard. Patients and caregivers sign in on the patient phone app instead.</p>
          <a className="btn btn-primary w-full" href={patientAppUrl()}>Open the patient phone app</a>
          <button className="btn w-full" onClick={logout}>Sign out</button>
        </div>
      </main>
    )
  }
  return <Doctor api={api} session={session} onLogout={() => { api.logout().catch(() => {}); logout() }} />
}
