package com.cardiotwin.wear.sensors

import com.cardiotwin.wear.data.Reading
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.StateFlow

/** Something that produces readings: the real watch sensors or the mock replay. */
interface SensorSource {
    /** A one-line description shown on the Status screen, e.g. what is measured and what is not. */
    val description: StateFlow<String>
    fun start(scope: CoroutineScope, emit: (List<Reading>) -> Unit)
    fun stop()
    /** Demo controls. Readings they produce are always marked simulated. */
    fun simulateFall()
    fun simulateAf()
}
