import { useCallback, useEffect, useRef, useState } from 'react'

// Runs `fn` now, every `ms`, and whenever `refresh()` is called. Keeps the last good value on errors.
export function usePoll(fn, deps, ms = 5000) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const fnRef = useRef(fn)
  fnRef.current = fn
  const seq = useRef(0)
  const refresh = useCallback(async () => {
    const my = ++seq.current
    try {
      const d = await fnRef.current()
      if (my === seq.current) { setData(d); setError(null) }
    } catch (e) {
      if (my === seq.current) setError(e)
    }
  }, [])
  useEffect(() => {
    setData(null)
    refresh()
    if (!ms) return
    const t = setInterval(refresh, ms)
    return () => clearInterval(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  return { data, error, refresh }
}

// Subscribes to /ws/live/{patient}; calls onMessage for each message and reconnects after drops.
export function useLive(url, onMessage) {
  const [connected, setConnected] = useState(false)
  const cb = useRef(onMessage)
  cb.current = onMessage
  useEffect(() => {
    if (!url) return
    let ws, closed = false, timer, ping
    const open = () => {
      ws = new WebSocket(url)
      ws.onopen = () => {
        setConnected(true)
        ping = setInterval(() => ws.readyState === 1 && ws.send(JSON.stringify({ type: 'ping' })), 25000)
      }
      ws.onmessage = (ev) => {
        try { cb.current(JSON.parse(ev.data)) } catch { /* ignore malformed */ }
      }
      ws.onclose = (ev) => {
        setConnected(false)
        clearInterval(ping)
        if (!closed && ev.code !== 4401 && ev.code !== 4403) timer = setTimeout(open, 3000)
      }
    }
    open()
    return () => { closed = true; clearTimeout(timer); clearInterval(ping); ws && ws.close() }
  }, [url])
  return connected
}
