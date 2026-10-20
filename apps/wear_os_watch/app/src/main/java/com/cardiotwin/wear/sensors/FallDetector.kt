package com.cardiotwin.wear.sensors

import kotlin.math.sqrt

/**
 * A simple, EXPERIMENTAL fall heuristic on the accelerometer (Wear OS gives third-party apps no fall-detection API):
 *   1. free fall: |a| below [freeFallG] for at least [freeFallMs]
 *   2. impact:    |a| above [impactG] within [impactWindowMs] after the free fall
 *   3. stillness: |a| stays within [stillTolG] of 1 g for [stillMs] after the impact
 * Pure logic, fed with samples, so it is unit-tested on the JVM. Not validated on real falls; the watch always asks
 * "Are you OK?" before anything is escalated, exactly like the backend's fall check.
 */
class FallDetector(
    private val freeFallG: Double = 0.45,
    private val freeFallMs: Long = 80,
    private val impactG: Double = 2.5,
    private val impactWindowMs: Long = 1_000,
    private val stillTolG: Double = 0.35,
    private val stillMs: Long = 2_000,
) {
    private enum class Phase { IDLE, FREE_FALL, AWAIT_IMPACT, AWAIT_STILL }

    private var phase = Phase.IDLE
    private var phaseStart = 0L
    private var peak = 0.0

    /** Feed one sample (m/s²). Returns the impact in g when a fall is confirmed, else null. */
    fun onSample(tMs: Long, x: Double, y: Double, z: Double): Double? {
        val g = sqrt(x * x + y * y + z * z) / 9.80665
        when (phase) {
            Phase.IDLE -> if (g < freeFallG) { phase = Phase.FREE_FALL; phaseStart = tMs }
            Phase.FREE_FALL -> if (g >= freeFallG) {
                val longEnough = tMs - phaseStart >= freeFallMs
                when {
                    !longEnough -> phase = Phase.IDLE
                    g > impactG -> { phase = Phase.AWAIT_STILL; phaseStart = tMs; peak = g }   // landed on this sample
                    else -> { phase = Phase.AWAIT_IMPACT; phaseStart = tMs }
                }
            }
            Phase.AWAIT_IMPACT -> when {
                g > impactG -> { phase = Phase.AWAIT_STILL; phaseStart = tMs; peak = g }
                tMs - phaseStart > impactWindowMs -> phase = Phase.IDLE
            }
            Phase.AWAIT_STILL -> when {
                g > impactG -> { peak = maxOf(peak, g); phaseStart = tMs }          // still bouncing
                tMs - phaseStart < 300 -> Unit                                       // settle after the impact
                kotlin.math.abs(g - 1.0) > stillTolG -> phase = Phase.IDLE           // got up and moving: not a fall
                tMs - phaseStart >= stillMs -> { phase = Phase.IDLE; return peak }
            }
        }
        return null
    }
}
