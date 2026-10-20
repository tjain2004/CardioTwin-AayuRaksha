import { useEffect, useRef, useState } from 'react'
import { usePoll } from '../hooks'
import { Empty, ErrorLine, Icon, Sheet } from '../components/ui'

// Nearby help: hospitals and medical stores around the phone's location, with route and drive time, plus 112 / 108 and the ambulance
// provider. No live ambulance provider is connected, so the ambulance part is a DEMO simulation the person starts on
// purpose; every DEMO vehicle is labelled as not real. 112 is always one tap away, whatever fails.
// Opened from the emergency screen (it is noted on that alert), Emergency contacts and Home.
// Medical stores come from Google Places only (no key: a Google Maps search, never made-up shops). CardioTwin does not
// say which medicine to take for a symptom; that tab shows the medicine list already on the record and a clear
// "ask your doctor, or call 112" note instead.
const STAGE_LABEL = { REQUESTED: 'Requested', SEARCHING: 'Searching', ASSIGNED: 'Assigned', EN_ROUTE: 'En route', ARRIVING: 'Arriving', ARRIVED: 'Arrived', COMPLETED: 'Completed' }
const km = (m) => (m == null ? '–' : m < 1000 ? `${m} m` : `${(m / 1000).toFixed(1)} km`)
const mins = (s) => (s == null ? '–' : `${Math.max(1, Math.round(s / 60))} min`)

export default function NearbyHelpSheet({ ctx, alertId, onClose, initialMode = 'hospital' }) {
  const { api, pid, ro, meds } = ctx
  const [mode, setMode] = useState(initialMode)          // 'hospital' | 'pharmacy'
  const [stores, setStores] = useState(null)
  const cfg = usePoll(() => api.mapsConfig(), [], 0)
  const [loc, setLoc] = useState(null)
  const [locErr, setLocErr] = useState(null)
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  const [routes, setRoutes] = useState({})
  const [amb, setAmb] = useState(null)
  const [confirming, setConfirming] = useState(false)

  const locate = () => {
    setLocErr(null)
    if (!navigator.geolocation) { setLocErr('This browser cannot share your location.'); return }
    navigator.geolocation.getCurrentPosition(
      (p) => setLoc({ lat: p.coords.latitude, lng: p.coords.longitude, accuracy: Math.round(p.coords.accuracy) }),
      (e) => setLocErr(e.code === 1 ? 'Location permission was not given. You can still call 112 or search in Google Maps.' :
        e.code === 3 ? 'Finding your location took too long. Try again outdoors or near a window.' : 'Your location is not available right now (GPS or network).'),
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 60000 })
  }
  useEffect(() => {
    if (!loc) return
    setErr(null)
    api.nearbyHospitals(pid, loc.lat, loc.lng, 8000, alertId).then(setData).catch(setErr)
  }, [loc, pid, alertId, api])
  useEffect(() => {
    if (!loc || mode !== 'pharmacy' || stores) return
    api.nearbyPharmacies(pid, loc.lat, loc.lng, 3000).then(setStores).catch(setErr)
  }, [loc, mode, stores, pid, api])
  useEffect(() => { api.activeAmbulance(pid).then((r) => r.request && setAmb(r.request)).catch(() => {}) }, [pid, api])
  useEffect(() => {
    if (!amb || ['COMPLETED', 'CANCELLED'].includes(amb.status)) return
    const t = setInterval(() => api.ambulance(amb.request_id).then(setAmb).catch(() => {}), 2000)
    return () => clearInterval(t)
  }, [amb?.request_id, amb?.status, api])

  const getRoute = async (h) => {
    try { const r = await api.route(pid, loc, h); setRoutes((x) => ({ ...x, [h.place_id]: r })) } catch (e) { setErr(e) }
  }
  const startDemo = async () => {
    setConfirming(false)
    try { setAmb(await api.requestAmbulance(pid, loc.lat, loc.lng, alertId, true)) } catch (e) { setErr(e) }
  }
  const numbers = data?.emergency_numbers || cfg.data?.emergency_numbers || [{ number: '112', label: 'Emergency (112)', uri: 'tel:112' }, { number: '108', label: 'Ambulance (108)', uri: 'tel:108' }]
  const search = data?.maps_search_url || 'https://www.google.com/maps/search/?api=1&query=hospital'
  const demo = data?.ambulances?.label === 'DEMO' || cfg.data?.ambulance?.simulated
  const pharmacy = mode === 'pharmacy'
  const storeSearch = stores?.maps_search_url || cfg.data?.pharmacy_search_url || 'https://www.google.com/maps/search/?api=1&query=pharmacy'

  return (
    <Sheet title="Nearby help" onClose={onClose}>
      <div className="grid grid-cols-2 gap-2">
        {numbers.slice(0, 2).map((n, i) => (
          <a key={n.number} href={n.uri || `tel:${n.number}`} className={`btn min-h-14 flex-col gap-0 py-1 ${i === 0 ? 'btn-emg' : ''}`}>
            <span className="flex items-center gap-1.5 text-[17px] font-bold"><Icon name="phone" size={18} /> Call {n.number}</span>
            <span className="text-[11px] opacity-85">{n.label}</span>
          </a>
        ))}
      </div>
      <p className="text-[12px] text-muted">These open your phone's dialler; you press Call. CardioTwin never calls or sends an ambulance by itself.</p>

      <div role="tablist" aria-label="What to find" className="chips">
        <button role="tab" aria-selected={!pharmacy} className="chip" onClick={() => setMode('hospital')}>Hospitals</button>
        <button role="tab" aria-selected={pharmacy} className="chip" onClick={() => setMode('pharmacy')}>Medical stores</button>
      </div>

      {!loc && (
        <section className="card space-y-2 p-4">
          <b className="block text-[15px]">Find {pharmacy ? 'medical stores' : 'hospitals'} near you</b>
          <p className="text-[13.5px] text-muted">CardioTwin uses your location only for this search. It is not saved with your record, and a DEMO request forgets it when cancelled.</p>
          <button className="btn btn-primary min-h-11 w-full" onClick={locate}>Share my location</button>
          {locErr && <div className="rounded-md bg-emg-soft px-3 py-2 text-[13px] text-emg">{locErr}</div>}
          <a className="btn min-h-11 w-full" href={pharmacy ? storeSearch : search} target="_blank" rel="noreferrer">Search {pharmacy ? 'medical stores' : 'hospitals'} in Google Maps</a>
        </section>
      )}

      {loc && <MapView browserKey={cfg.data?.maps?.browser_key} loc={loc} hospitals={pharmacy ? stores?.pharmacies || [] : data?.hospitals || []}
        mark={pharmacy ? 'M' : 'H'} vehicle={pharmacy ? null : amb?.vehicle} />}
      <ErrorLine error={err} />

      {pharmacy && loc && stores && <PlaceList title="Medical stores" res={stores} list={stores.pharmacies} search={storeSearch} routes={routes} getRoute={getRoute} />}
      {pharmacy && <MyMedicines meds={meds?.data} note={stores?.medicine_note || cfg.data?.medicine_note} />}

      {!pharmacy && loc && data && (
        <section className="card p-4">
          <div className="mb-2 flex items-center justify-between"><b className="text-[15px]">Hospitals</b><span className="text-[11.5px] text-muted">{data.source || ''}</span></div>
          {!data.available && <Empty>{data.note} <a className="text-accent underline" href={search} target="_blank" rel="noreferrer">Open Google Maps</a></Empty>}
          {data.available && !data.hospitals.length && <Empty>{data.note}</Empty>}
          <div className="space-y-2">
            {data.hospitals.slice(0, 8).map((h) => {
              const r = routes[h.place_id]
              return (
                <div key={h.place_id} className="rounded-2xl bg-sunk p-3 text-[13.5px]">
                  <div className="flex items-start gap-2">
                    <div className="min-w-0 flex-1"><b className="block text-[14.5px]">{h.name}</b><span className="text-muted">{h.address}</span></div>
                    <span className="num shrink-0 font-semibold">{km(h.distance_m)}</span>
                  </div>
                  {h.open_now != null && <div className="text-[12px]" style={{ color: h.open_now ? 'var(--low)' : 'var(--high)' }}>{h.open_now ? 'Open now' : 'May be closed now'}</div>}
                  {r && (r.available ? <div className="text-[12.5px]">By road {km(r.distance_m)}, about {mins(r.duration_s)} by car (Google Routes)</div>
                    : <div className="text-[12.5px] text-muted">Drive time unavailable; {km(r.straight_line_m)} in a straight line.</div>)}
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {h.phone && <a className="btn min-h-9" href={`tel:${h.phone.replace(/\s/g, '')}`}><Icon name="phone" size={16} /> Call</a>}
                    {!r && <button className="btn min-h-9" onClick={() => getRoute(h)}>Drive time</button>}
                    <a className="btn min-h-9" target="_blank" rel="noreferrer" href={`https://www.google.com/maps/dir/?api=1&destination=${h.lat},${h.lng}&destination_place_id=${h.place_id}`}>Directions</a>
                  </div>
                </div>
              )
            })}
          </div>
        </section>
      )}

      {!pharmacy && <section className="card p-4">
        <div className="mb-1 flex items-center gap-2"><b className="text-[15px]">Ambulance</b>{demo && <span className="tag" style={{ background: 'var(--mod-soft)', color: 'var(--mod)' }}>DEMO</span>}</div>
        {!demo && <p className="text-[13.5px] text-muted">{data?.ambulances?.note || 'No ambulance provider is connected. Call 108 or 112 for an ambulance.'}</p>}
        {demo && !amb && (
          <>
            <p className="text-[13.5px]">No live ambulance service is connected to CardioTwin. You can run a <b>DEMO simulation</b> to see how tracking would look. <b>No ambulance will come.</b> For a real ambulance call 108 or 112.</p>
            {!confirming ? <button className="btn mt-2 min-h-11 w-full" disabled={ro || !loc} onClick={() => setConfirming(true)}>{loc ? 'Start DEMO simulation' : 'Share your location first'}</button> : (
              <div className="mt-2 rounded-xl bg-mod-soft p-3 text-[13.5px]">
                <p>I understand this is a demonstration only and no ambulance is being sent.</p>
                <div className="mt-2 grid grid-cols-2 gap-2"><button className="btn" onClick={() => setConfirming(false)}>Cancel</button><button className="btn btn-primary" onClick={startDemo}>Start DEMO</button></div>
              </div>
            )}
          </>
        )}
        {amb && <AmbulanceStatus amb={amb} onCancel={async () => { try { setAmb(await api.cancelAmbulance(amb.request_id)) } catch (e) { setErr(e) } }} />}
      </section>}
    </Sheet>
  )
}

// Medical stores from Google Places, nearest first, with call, drive time and directions.
function PlaceList({ title, res, list, search, routes, getRoute }) {
  return (
    <section className="card p-4">
      <div className="mb-2 flex items-center justify-between"><b className="text-[15px]">{title}</b><span className="text-[11.5px] text-muted">{res.source || ''}</span></div>
      {!res.available && <Empty>{res.note} <a className="text-accent underline" href={search} target="_blank" rel="noreferrer">Open Google Maps</a></Empty>}
      {res.available && !list.length && <Empty>{res.note}</Empty>}
      <div className="space-y-2">
        {list.slice(0, 8).map((h) => {
          const r = routes[h.place_id]
          return (
            <div key={h.place_id} className="rounded-2xl bg-sunk p-3 text-[13.5px]">
              <div className="flex items-start gap-2">
                <div className="min-w-0 flex-1"><b className="block text-[14.5px]">{h.name}</b><span className="text-muted">{h.address}</span></div>
                <span className="num shrink-0 font-semibold">{km(h.distance_m)}</span>
              </div>
              {h.open_now != null && <div className="text-[12px]" style={{ color: h.open_now ? 'var(--low)' : 'var(--high)' }}>{h.open_now ? 'Open now' : 'May be closed now'}</div>}
              {r && (r.available ? <div className="text-[12.5px]">By road {km(r.distance_m)}, about {mins(r.duration_s)} by car (Google Routes)</div>
                : <div className="text-[12.5px] text-muted">Drive time unavailable; {km(r.straight_line_m)} in a straight line.</div>)}
              <div className="mt-2 flex flex-wrap gap-1.5">
                {h.phone && <a className="btn min-h-9" href={`tel:${h.phone.replace(/\s/g, '')}`}><Icon name="phone" size={16} /> Call</a>}
                {!r && <button className="btn min-h-9" onClick={() => getRoute(h)}>Drive time</button>}
                <a className="btn min-h-9" target="_blank" rel="noreferrer" href={`https://www.google.com/maps/dir/?api=1&destination=${h.lat},${h.lng}&destination_place_id=${h.place_id}`}>Directions</a>
              </div>
            </div>
          )
        })}
      </div>
    </section>
  )
}

// The medicines already on this person's list, so they can take it to the store. No advice on what to take.
function MyMedicines({ meds, note }) {
  const by = { doctor: 'your doctor', demo: 'demo data', patient: 'you' }
  return (
    <section className="card p-4">
      <b className="block text-[15px]">Your medicine list</b>
      <p className="mt-0.5 text-[12.5px] text-muted">Show this at the medical store. It lists what is already on your record.</p>
      {meds?.length ? (
        <ul className="mt-2 space-y-1.5">
          {meds.map((m) => (
            <li key={m.med_id} className="flex items-center gap-2.5 rounded-2xl bg-sunk px-3 py-2 text-[14px]">
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-surface text-accent"><Icon name="pill" size={16} /></span>
              <span className="min-w-0 flex-1"><b>{m.name}</b> <span className="text-muted">{m.strength}</span>
                <span className="block text-[11.5px] text-muted">{m.instructions || m.times?.join(', ')}{m.source ? ` · added by ${by[m.source] || 'you'}` : ''}</span></span>
            </li>
          ))}
        </ul>
      ) : <div className="mt-2"><Empty>No medicines on your list yet.</Empty></div>}
      <div className="mt-3 rounded-2xl px-3 py-2.5 text-[13px] font-medium" style={{ background: 'var(--accent-soft)', color: 'var(--accent-strong)' }}>
        {note || 'CardioTwin does not tell you which medicine to take for symptoms. Take only what your doctor prescribed. If you feel unwell, call your doctor; for chest pain, fainting or trouble breathing call 112.'}
      </div>
    </section>
  )
}

function AmbulanceStatus({ amb, onCancel }) {
  const stages = amb.stages || Object.keys(STAGE_LABEL)
  const at = stages.indexOf(amb.status)
  return (
    <div className="space-y-2">
      {amb.simulated && <div className="rounded-lg px-3 py-2 text-[13px] font-semibold" style={{ background: 'var(--mod-soft)', color: 'var(--mod)' }}>{amb.note}</div>}
      <div className="font-display text-[18px] font-bold">{amb.status_text}</div>
      {amb.status === 'CANCELLED' ? null : (
        <>
          <ol className="grid grid-cols-7 gap-1" aria-label={`Step ${at + 1} of ${stages.length}: ${STAGE_LABEL[amb.status] || amb.status}`}>
            {stages.map((s, i) => <li key={s} className="h-1.5 rounded-full" title={STAGE_LABEL[s] || s} style={{ background: i <= at ? 'var(--accent)' : 'var(--line)' }} />)}
          </ol>
          <p className="text-[11.5px] text-muted">{stages.map((s, i) => <span key={s} style={i === at ? { color: 'var(--accent)', fontWeight: 700 } : undefined}>{i ? ' → ' : ''}{STAGE_LABEL[s] || s}</span>)}</p>
        </>
      )}
      {amb.vehicle && <div className="text-[13.5px]">{amb.vehicle.label} · {km(amb.distance_m)} away{amb.eta_seconds != null && <> · ETA {mins(amb.eta_seconds)} (simulated)</>}</div>}
      {!['COMPLETED', 'CANCELLED', 'ARRIVED'].includes(amb.status) && <button className="btn min-h-10 w-full" onClick={onCancel}>Cancel DEMO request</button>}
    </div>
  )
}

// Google Maps JavaScript API with the browser key (restrict it to your site in Google Cloud). Without a key, or if
// the script cannot load, the list above still works.
let mapsPromise = null
function loadMaps(key) {
  if (window.google?.maps?.Map) return Promise.resolve(window.google.maps)
  if (!mapsPromise) {
    mapsPromise = new Promise((resolve, reject) => {
      window.__ctMapsReady = () => resolve(window.google.maps)
      const s = document.createElement('script')
      s.src = `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(key)}&callback=__ctMapsReady&loading=async`
      s.async = true
      s.onerror = () => { mapsPromise = null; reject(new Error('Google Maps could not load (network or key).')) }
      document.head.appendChild(s)
    })
  }
  return mapsPromise
}

function MapView({ browserKey, loc, hospitals, vehicle, mark = 'H' }) {
  const el = useRef(null)
  const map = useRef(null)
  const marks = useRef([])
  const [err, setErr] = useState(null)
  useEffect(() => {
    if (!browserKey) return
    let gone = false
    loadMaps(browserKey).then((g) => {
      if (gone || !el.current) return
      map.current = map.current || new g.Map(el.current, { center: loc, zoom: 13, disableDefaultUI: true, zoomControl: true })
      marks.current.forEach((m) => m.setMap(null))
      marks.current = [new g.Marker({ position: loc, map: map.current, title: 'You', label: 'You' })]
      for (const h of hospitals.slice(0, 15)) marks.current.push(new g.Marker({ position: { lat: h.lat, lng: h.lng }, map: map.current, title: h.name, label: mark }))
      if (vehicle) marks.current.push(new g.Marker({ position: { lat: vehicle.lat, lng: vehicle.lng }, map: map.current, title: vehicle.label, label: 'DEMO' }))
    }).catch((e) => setErr(e.message))
    return () => { gone = true }
  }, [browserKey, loc, hospitals, vehicle, mark])
  if (!browserKey) return <p className="text-[12px] text-muted">Map view is off (no Google Maps browser key on the server). The list below still works.</p>
  return (
    <>
      <div ref={el} className="h-64 w-full overflow-hidden rounded-2xl border border-line bg-sunk" role="img" aria-label="Map of nearby places" />
      {vehicle && <p className="text-[11.5px] font-semibold" style={{ color: 'var(--mod)' }}>The DEMO marker is a simulation, not a real ambulance.</p>}
      {err && <p className="text-[12px] text-muted">{err} The list below still works.</p>}
    </>
  )
}
