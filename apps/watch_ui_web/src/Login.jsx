import { useState } from 'react'
import { defaultServer, login } from './api'

export default function Login({ onLogin }) {
  const [server, setServer] = useState(defaultServer())
  const [username, setUsername] = useState('dr.demo')
  const [password, setPassword] = useState('demo1234')
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)

  async function go(e) {
    e.preventDefault()
    setBusy(true); setErr(null)
    try { onLogin(await login(server, username, password)) } catch (ex) {
      setErr(ex.status ? ex.message : `Can't reach the backend at ${server}. Start it with: python -m cardiotwin_backend --host 0.0.0.0 --port 8000`)
    } finally { setBusy(false) }
  }

  return (
    <main className="page" style={{ justifyContent: 'center' }}>
      <form className="panel" style={{ width: '100%', maxWidth: 380 }} onSubmit={go}>
        <div className="row"><img src="/icon.svg" alt="" width="32" height="32" /><div><div className="brand">Cardio<b>Twin</b> watch</div><div className="muted small">Wear OS screens in the browser</div></div></div>
        <label className="small">Backend<input className="input" style={{ fontFamily: 'var(--mono)' }} value={server} onChange={(e) => setServer(e.target.value)} /></label>
        <div className="row" style={{ flexWrap: 'nowrap' }}>
          <label className="small" style={{ flex: 1 }}>User<input className="input" value={username} onChange={(e) => setUsername(e.target.value)} autoCapitalize="none" /></label>
          <label className="small" style={{ flex: 1 }}>Password<input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} /></label>
        </div>
        {err && <div className="err">{err}</div>}
        <button className="btn btn-primary" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
        <p className="muted small" style={{ margin: 0 }}>
          Demo accounts, password <code>demo1234</code>: <code>dr.demo</code> (any patient), <code>p37093652</code> (one patient).
          Research prototype, not a medical device. Everything from the virtual watch is labelled SIMULATED.
        </p>
      </form>
    </main>
  )
}
