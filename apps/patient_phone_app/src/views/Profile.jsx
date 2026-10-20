import { useState } from 'react'
import { usePoll } from '../hooks'
import { Card, DemoTag, Disclaimer, Empty, ErrorLine, Icon, Sheet, SimTag, Toggle } from '../components/ui'
import { ago } from '../format'

// Profile: a short menu. Each row opens its own screen (care team, emergency contacts, notifications, settings),
// so the tab stays uncluttered. The notifications inbox is the shared sheet the bell also opens.
export default function Profile({ ctx, onLogout, focus }) {
  const { api, pid, session, ro, settings, team, setSheet, notes } = ctx
  const s = settings.data
  const [page, setPage] = useState(['contacts', 'settings', 'team'].includes(focus) ? focus : null)
  const name = s?.preferred_name || 'Patient'
  const doctor = team.data?.doctors?.[0]
  const rows = [
    { id: 'team', icon: 'team', label: 'My care team', sub: doctor ? `${doctor.name} · ${doctor.specialty}` : 'Doctors, caregivers and clinic' },
    { id: 'contacts', icon: 'phone', label: 'Emergency contacts', sub: 'Family to call, and 112', danger: true },
    { id: 'notifications', icon: 'bell', label: 'Notifications', sub: 'Reminders, visits and watch alerts', badge: notes.data?.unread },
    { id: 'settings', icon: 'gear', label: 'Settings', sub: 'Name, language and reminders' },
  ]
  const open = (id) => (id === 'notifications' ? setSheet('notifications') : setPage(id))
  return (
    <div className="space-y-4 px-4">
      <section className="card flex items-center gap-3 p-4">
        <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full bg-accent font-display text-2xl font-extrabold text-on-accent">{name[0].toUpperCase()}</div>
        <div className="min-w-0 flex-1">
          <h1 className="font-display text-[21px] font-extrabold leading-tight">{name}</h1>
          <div className="num text-[12.5px] text-muted">{pid}</div>
          <div className="text-[12.5px] text-muted">Signed in as {session.display_name}{session.role === 'caregiver' ? ' (caregiver)' : ''}</div>
        </div>
        {s?.name_is_demo && <span className="tag bg-sunk text-muted" title="Fictional name for the demo patient">demo name</span>}
      </section>

      <nav className="card divide-y divide-line overflow-hidden" aria-label="Profile">
        {rows.map((r) => (
          <button key={r.id} className="flex w-full items-center gap-3 px-4 py-3.5 text-left hover:bg-sunk" onClick={() => open(r.id)}>
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl"
              style={{ background: r.danger ? 'var(--accent)' : 'var(--accent-soft)', color: r.danger ? 'var(--on-accent)' : 'var(--accent)' }}><Icon name={r.icon} size={19} /></span>
            <span className="min-w-0 flex-1"><b className="block text-[15px]">{r.label}</b><span className="block truncate text-[12.5px] text-muted">{r.sub}</span></span>
            {r.badge > 0 && <span className="tag bg-accent text-on-accent">{r.badge} new</span>}
            <Icon name="chevron" size={18} className="text-muted" />
          </button>
        ))}
      </nav>

      <button className="btn min-h-12 w-full text-[15px] font-semibold text-accent" style={{ borderColor: 'var(--accent)' }} onClick={() => { api.logout().catch(() => {}); onLogout() }}>Sign out</button>
      <Disclaimer caregiver={session.role === 'caregiver'} />

      {page === 'team' && <Sheet title="My care team" onClose={() => setPage(null)}><CareTeam team={team.data} onBook={() => { setPage(null); setSheet('book') }} ro={ro} />
        {session.role === 'patient' && !ro && <CaregiverSharing api={api} pid={pid} />}</Sheet>}
      {page === 'contacts' && <Sheet title="Emergency contacts" onClose={() => setPage(null)}><Contacts api={api} pid={pid} ro={ro} onNearby={() => setSheet('nearby')} /></Sheet>}
      {page === 'settings' && <Sheet title="Settings" onClose={() => setPage(null)}><Settings api={api} pid={pid} settings={settings} ro={ro} server={api.base} /></Sheet>}
    </div>
  )
}

function CareTeam({ team, onBook, ro }) {
  if (!team) return null
  return (
    <Card eyebrow="Doctors and clinic" right={<DemoTag title="Demo care team and clinic" />}>
      <div className="space-y-2">
        {team.doctors.map((d) => (
          <div key={d.user_id} className="flex items-center gap-3 rounded-xl bg-sunk p-3">
            <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-surface font-display text-lg font-extrabold text-accent">{d.name.replace(/^Dr\.?\s*/, '')[0]}</span>
            <div className="min-w-0 flex-1">
              <b className="block">{d.name}</b>
              <span className="text-[13px] text-muted">{d.specialty}</span>
            </div>
            <button className="btn btn-primary" disabled={ro} onClick={onBook}>Book</button>
          </div>
        ))}
        {team.caregivers.map((c) => (
          <div key={c.user_id} className="flex items-center gap-3 rounded-xl border border-line p-3">
            <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-sunk text-muted"><Icon name="team" size={20} /></span>
            <div><b className="block">{c.name}</b><span className="text-[13px] text-muted">{c.role}, can see your readings</span></div>
          </div>
        ))}
        {!team.doctors.length && <Empty>No doctor linked yet.</Empty>}
      </div>
      <div className="mt-3 flex items-start gap-2 text-[13px] text-muted">
        <Icon name="clinic" size={18} />
        <div><b className="text-ink">{team.clinic.name}</b><br />{team.clinic.hours}<br /><span className="text-[12px]">{team.clinic.note}</span></div>
      </div>
    </Card>
  )
}

function Contacts({ api, pid, ro, onNearby }) {
  const contacts = usePoll(() => api.contacts(pid), [pid], 0)
  const [form, setForm] = useState(null)          // null | {} for new | contact for edit
  const [err, setErr] = useState(null)
  const list = contacts.data || []
  const save = async () => {
    setErr(null)
    const body = { name: form.name || '', phone: form.phone || '', relation: form.relation || '', is_primary: !!form.is_primary }
    try { form.contact_id ? await api.updateContact(form.contact_id, body) : await api.addContact(pid, body); setForm(null); contacts.refresh() } catch (e) { setErr(e) }
  }
  const remove = async () => { try { await api.removeContact(form.contact_id); setForm(null); contacts.refresh() } catch (e) { setErr(e) } }
  return (
    <Card eyebrow="Call in one tap" right={!ro && !form && list.length < 5 && <button className="btn" onClick={() => setForm({})}><Icon name="plus" size={16} /> Add</button>}>
      <a href="tel:112" className="flex items-center gap-3 rounded-xl p-3" style={{ background: 'var(--emg-soft)' }}>
        <span className="flex h-10 w-10 items-center justify-center rounded-full" style={{ background: 'var(--emg)', color: 'var(--on-emg)' }}><Icon name="phone" size={18} /></span>
        <span className="min-w-0 flex-1"><b className="block">Emergency services</b><span className="text-[13px] text-muted">Ambulance, police, fire</span></span>
        <b className="num text-[18px]" style={{ color: 'var(--emg)' }}>112</b>
      </a>
      {onNearby && <button className="btn mt-2 min-h-11 w-full" onClick={onNearby}><Icon name="clinic" size={17} /> Nearby hospitals and ambulance</button>}
      <div className="mt-2 space-y-2">
        {list.map((c) => (
          <div key={c.contact_id} className="flex items-center gap-3 rounded-xl border border-line p-3">
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-sunk font-semibold">{c.name[0]?.toUpperCase()}</span>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-1.5"><b>{c.name}</b>{c.is_primary && <span className="tag" style={{ background: 'var(--accent-soft)', color: 'var(--accent)' }}>first to call</span>}</div>
              <div className="text-[13px] text-muted">{c.relation}{c.relation && ' · '}<span className="num whitespace-nowrap">{c.phone}</span></div>
            </div>
            {!ro && <button className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full hover:bg-sunk" aria-label={`Edit ${c.name}`} onClick={() => setForm(c)}><Icon name="edit" size={17} /></button>}
            <a className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full" style={{ background: 'var(--low-soft)', color: 'var(--low)' }} href={`tel:${c.phone.replace(/[^\d+]/g, '')}`} aria-label={`Call ${c.name}`}><Icon name="phone" size={18} /></a>
          </div>
        ))}
        {!list.length && !form && <Empty>Add a family member or friend so you can reach them in one tap.</Empty>}
      </div>
      {form && (
        <div className="mt-2 space-y-2 rounded-xl border p-3" style={{ borderColor: 'var(--accent)' }}>
          <div className="grid grid-cols-2 gap-2">
            <input className="input" placeholder="Name" value={form.name || ''} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            <input className="input" placeholder="Relation (e.g. Son)" value={form.relation || ''} onChange={(e) => setForm({ ...form, relation: e.target.value })} />
          </div>
          <input className="input num w-full" type="tel" inputMode="tel" placeholder="Phone number" value={form.phone || ''} onChange={(e) => setForm({ ...form, phone: e.target.value })} />
          <Toggle label="Call this person first" checked={!!form.is_primary} onChange={(v) => setForm({ ...form, is_primary: v })} />
          <ErrorLine error={err} />
          <div className="flex gap-2">
            {form.contact_id && <button className="btn" style={{ color: 'var(--high)' }} onClick={remove}><Icon name="trash" size={16} /> Remove</button>}
            <button className="btn ml-auto" onClick={() => { setForm(null); setErr(null) }}>Cancel</button>
            <button className="btn btn-primary" onClick={save}>Save</button>
          </div>
        </div>
      )}
      <p className="mt-2 text-[11.5px] text-muted">Tapping the phone button opens your phone's dialler; you press Call yourself. CardioTwin never calls or messages anyone by itself.</p>
    </Card>
  )
}

function Settings({ api, pid, settings, ro, server }) {
  const s = settings.data
  const [name, setName] = useState(null)
  const [err, setErr] = useState(null)
  const [saved, setSaved] = useState(false)
  const [perm, setPerm] = useState(typeof Notification === 'undefined' ? 'unsupported' : Notification.permission)
  if (!s) return null
  const put = async (body) => {
    setErr(null); setSaved(false)
    try { await api.putAppSettings(pid, body); settings.refresh(); setSaved(true); setTimeout(() => setSaved(false), 1500) } catch (e) { setErr(e) }
  }
  const askPermission = async () => {
    try { setPerm(await Notification.requestPermission()) } catch { setPerm('unsupported') }
  }
  return (
    <Card eyebrow="Your preferences" right={saved && <span className="text-[12px]" style={{ color: 'var(--low)' }}>Saved</span>}>
      <label className="block text-[13px] text-muted">What should we call you?
        <div className="mt-1 flex gap-2">
          <input className="input min-w-0 flex-1" value={name ?? s.preferred_name ?? ''} disabled={ro} onChange={(e) => setName(e.target.value)} placeholder="Your first name" />
          <button className="btn" disabled={ro || name == null || name === s.preferred_name} onClick={() => { put({ preferred_name: name }); setName(null) }}>Save</button>
        </div>
      </label>
      <label className="mt-3 block text-[13px] text-muted">Language
        <select className="input mt-1 w-full" value={s.language} disabled={ro} onChange={(e) => put({ language: e.target.value })}>
          {s.languages.map((l) => <option key={l.code} value={l.code}>{l.label}</option>)}
        </select>
        <span className="mt-1 block text-[11.5px]">Saved as your preference. The app text is in English for now.</span>
      </label>
      <div className="mt-2 divide-y divide-line">
        <Toggle label="Medicine reminders" sub="Show doses that are due on the Home screen" checked={s.med_reminders} disabled={ro} onChange={(v) => put({ med_reminders: v })} />
        <Toggle label="Appointment updates" sub="Booked, moved and cancelled visits in Notifications" checked={s.appointment_updates} disabled={ro} onChange={(v) => put({ appointment_updates: v })} />
        <div className="flex min-h-12 items-center gap-3 py-1.5 text-[14px]">
          <span className="min-w-0 flex-1">Phone notifications<span className="block text-[12px] text-muted">
            {perm === 'granted' ? 'On. Reminders pop up while CardioTwin is open.' : perm === 'denied' ? 'Blocked in the browser settings.' : perm === 'unsupported' ? 'Not available in this browser. Needs https or localhost.' : 'Let CardioTwin pop up medicine reminders.'}
          </span></span>
          {perm === 'default' && <button className="btn" onClick={askPermission}>Allow</button>}
        </div>
      </div>
      <ErrorLine error={err} />
      <dl className="mt-3 grid grid-cols-[6rem_1fr] gap-y-1 border-t border-line pt-3 text-[12px] text-muted">
        <dt>Backend</dt><dd className="num truncate">{server}</dd>
        <dt>App</dt><dd>CardioTwin patient app 1.1 (research prototype)</dd>
      </dl>
    </Card>
  )
}

const KIND_ICON = { appointment: 'calendar', medication: 'pill', alert: 'alert', welcome: 'heart', ambulance: 'clinic', heart_info: 'heart', heart_attention: 'heart', heart_high: 'alert' }
// Levels for the heart-health notices. "Emergency" is only ever an alert from the existing emergency system.
const LEVEL = { heart_info: ['Information', 'var(--accent)'], heart_attention: ['Attention', 'var(--mod)'], heart_high: ['High priority', 'var(--high)'] }
const levelOf = (n) => (n.kind === 'alert' && n.severity === 'critical' ? ['Emergency', 'var(--emg)'] : LEVEL[n.kind])

export function NotificationsSheet({ ctx, onClose }) {
  const { api, pid, notes, ro, settings } = ctx
  const items = (notes.data?.items || []).filter((n) => n.kind !== 'appointment' || settings.data?.appointment_updates !== false)
  const markAll = async () => { try { await api.markRead(pid); notes.refresh() } catch { /* stays unread */ } }
  return (
    <Sheet title="Notifications" onClose={() => { if (!ro && notes.data?.unread) markAll(); onClose() }}
      right={!ro && notes.data?.unread > 0 && <button className="btn" onClick={markAll}>Mark all read</button>}>
      <ErrorLine error={notes.error} />
      {items.length ? (
        <div className="card divide-y divide-line">
          {items.map((n) => (
            <div key={n.notification_id} className="flex gap-3 p-3" style={!n.read ? { background: 'var(--accent-soft)' } : undefined}>
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-surface"
                style={{ color: levelOf(n)?.[1] || (n.kind === 'alert' ? 'var(--high)' : 'var(--accent)') }}>
                <Icon name={KIND_ICON[n.kind] || 'bell'} size={18} />
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5 text-[14px]"><b className="min-w-0">{n.title}</b>{n.simulated && <SimTag />}{n.kind === 'appointment' && <DemoTag />}
                  {levelOf(n) && <span className="tag ml-auto" style={{ color: levelOf(n)[1], background: 'var(--sunk)' }}>{levelOf(n)[0]}</span>}</div>
                {n.body && <div className="text-[13px] text-muted">{n.body}</div>}
                <div className="text-[11.5px] text-muted">{ago(n.created_at)}{n.kind === 'alert' && n.state ? ` · ${n.state.toLowerCase().replace('_', ' ')}` : ''}</div>
              </div>
              {!n.read && <span className="mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: 'var(--accent)' }} aria-label="unread" />}
            </div>
          ))}
        </div>
      ) : <Empty>No notifications yet.</Empty>}
      <p className="text-[11.5px] text-muted">Watch alerts show as read once you tap "Got it" on the Home screen.</p>
    </Sheet>
  )
}

// What each caregiver may see beyond the existing screens. Emergency alerts stay on by default (as before); health
// alerts and weekly reports are off until the patient turns them on, and can be turned off again at any time.
function CaregiverSharing({ api, pid }) {
  const list = usePoll(() => api.caregiverConsent(pid), [pid], 0)
  const [err, setErr] = useState(null)
  const set = async (cid, k, v) => { setErr(null); try { await api.setCaregiverConsent(pid, cid, { [k]: v }); list.refresh() } catch (e) { setErr(e) } }
  if (!list.data?.length) return null
  return (
    <Card title="What caregivers can see" eyebrow="Your choice">
      <ErrorLine error={err} />
      {list.data.map((c) => (
        <div key={c.caregiver_id} className="mb-2">
          <b className="text-[14px]">{c.name}</b>
          <Toggle checked={c.emergency} label="Emergency alerts" sub="The emergency screen and Call 112 prompt" onChange={(v) => set(c.caregiver_id, 'emergency', v)} />
          <Toggle checked={c.health_alerts} label="Heart health and alerts" sub="Heart health overview and attention notices" onChange={(v) => set(c.caregiver_id, 'health_alerts', v)} />
          <Toggle checked={c.weekly_reports} label="Weekly reports" sub="Your weekly cardiac health summary" onChange={(v) => set(c.caregiver_id, 'weekly_reports', v)} />
        </div>
      ))}
      <p className="text-[12px] text-muted">Caregivers linked to you can already see your readings and medicines, as before.</p>
    </Card>
  )
}
