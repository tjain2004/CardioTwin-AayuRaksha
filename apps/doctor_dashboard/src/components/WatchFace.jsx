// Preview of what the Wear OS watch face shows for this patient (views.watch from the risk engine / safety layer).
const RING = { LOW: 'var(--low)', MODERATE: 'var(--mod)', HIGH: 'var(--high)' }

export default function WatchFace({ watch, simulated }) {
  if (!watch) return null
  const emg = watch.type === 'EMERGENCY_WATCH'
  const fall = watch.type === 'FALL_CHECK'
  const ring = emg ? 'var(--emg)' : fall ? 'var(--high)' : RING[watch.tier] || 'var(--muted)'
  return (
    <div className="flex flex-col items-center gap-2">
      <div className="relative flex h-40 w-40 items-center justify-center rounded-full bg-[#0f0a0b] p-2 shadow-inner">
        <div className="flex h-full w-full flex-col items-center justify-center rounded-full border-[5px] px-4 text-center text-white" style={{ borderColor: ring, background: emg ? 'var(--emg)' : '#140f10' }}>
          <div className="text-xl" aria-hidden="true">{emg ? '⚠' : watch.icon_glyph || (fall ? '!' : '♥')}</div>
          <div className="font-display text-[15px] font-bold leading-tight">{watch.title}</div>
          <div className="text-[11px] leading-tight text-white/80">{watch.text}</div>
          {fall && <div className="mt-1 flex gap-1 text-[10px]"><span className="rounded-full bg-white/20 px-2">I'm OK</span><span className="rounded-full bg-white/20 px-2">Help</span></div>}
        </div>
      </div>
      <div className="text-center text-[12px] text-muted">
        {simulated && <span className="font-semibold text-sim">SIMULATED · </span>}
        Haptic: {watch.haptic || watch.haptics || 'none'}
        {watch.last_sync_minutes != null && <> · last sync {Math.round(watch.last_sync_minutes)} min ago</>}
      </div>
    </div>
  )
}
