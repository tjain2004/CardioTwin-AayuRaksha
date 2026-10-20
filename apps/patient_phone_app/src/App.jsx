import { useMemo, useState } from 'react'
import { loadSession, makeApi, saveSession } from './api'
import Login from './views/Login'
import Patient from './views/Patient'

// Patient phone app. Patients and caregivers see their own patient; a clinician can open one patient's phone layout
// read-only with ?view=patient&patient=<id> (the doctor dashboard's "Open patient phone view" button links here).
export default function App() {
  const [session, setSession] = useState(loadSession)
  const logout = () => { saveSession(null); setSession(null) }
  const api = useMemo(() => (session ? makeApi(session, logout) : null), [session])
  if (!session) return <Login onLogin={(s) => { saveSession(s); setSession(s) }} />
  const q = new URLSearchParams(location.search)
  const preview = session.role === 'clinician' ? q.get('patient') : null
  if (session.role === 'clinician' && !preview) {
    return (
      <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center gap-3 px-4">
        <div className="card space-y-3 p-5 text-[14px]">
          <p>This is the patient phone app. Doctors use the doctor dashboard; its "Open patient phone view" button opens a patient here.</p>
          <button className="btn w-full" onClick={logout}>Sign out</button>
        </div>
      </main>
    )
  }
  return <Patient api={api} session={session} patientId={preview || session.patient_id} onLogout={logout} preview={!!preview} />
}
