import { useState } from 'react'
import { defaultServer, login } from '../api'

const QUICK = [
  { u: 'dr.demo', label: 'Doctor', hint: 'all patients' },
]

export default function Login({ onLogin }) {
  const [server, setServer] = useState(defaultServer())
  const [username, setUsername] = useState('dr.demo')
  const [password, setPassword] = useState('demo1234')
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)

  async function go(u = username) {
    setBusy(true); setErr(null)
    try { onLogin(await login(server, u, password)) } catch (e) {
      setErr(e.status ? e.message : `Can't reach the backend at ${server}. Start it with: python -m cardiotwin_backend --host 0.0.0.0 --port 8000`)
    } finally { setBusy(false) }
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center px-4 py-10">
      <div className="mb-6 flex items-center gap-3">
        <img src="/icon.svg" alt="" className="h-10 w-10" />
        <div>
          <h1 className="font-display text-2xl font-extrabold">CardioTwin</h1>
          <div className="text-[13px] text-muted">Doctor dashboard</div>
        </div>
      </div>
      <form className="card space-y-3 p-5" onSubmit={(e) => { e.preventDefault(); go() }}>
        <label className="block text-[13px]">Backend
          <input className="input mt-1 w-full num" value={server} onChange={(e) => setServer(e.target.value)} />
        </label>
        <div className="grid grid-cols-2 gap-3">
          <label className="block text-[13px]">User
            <input className="input mt-1 w-full" value={username} onChange={(e) => setUsername(e.target.value)} autoCapitalize="none" />
          </label>
          <label className="block text-[13px]">Password
            <input className="input mt-1 w-full" type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
          </label>
        </div>
        {err && <div className="rounded-md bg-emg-soft px-3 py-2 text-[13px] text-emg">{err}</div>}
        <button className="btn btn-primary w-full" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
        <div className="eyebrow pt-2">Demo accounts (password demo1234)</div>
        <div className="space-y-2">
          {QUICK.map((q) => (
            <button key={q.u} type="button" className="btn w-full justify-between" disabled={busy} onClick={() => { setUsername(q.u); go(q.u) }}>
              <span>{q.label}</span><span className="text-[12px] text-muted">{q.hint}</span>
            </button>
          ))}
        </div>
      </form>
      <p className="mt-4 text-[12px] leading-relaxed text-muted">
        Research prototype trained on MIMIC-IV demo ICU data. Not a medical device. Data from the virtual watch is labelled SIMULATED.
        CardioTwin never contacts emergency services by itself.
      </p>
    </main>
  )
}
