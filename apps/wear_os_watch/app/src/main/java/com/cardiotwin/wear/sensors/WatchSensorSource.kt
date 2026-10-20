package com.cardiotwin.wear.sensors

import android.content.Context
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.os.SystemClock
import android.util.Log
import androidx.health.services.client.HealthServices
import androidx.health.services.client.MeasureCallback
import androidx.health.services.client.data.Availability
import androidx.health.services.client.data.DataPointContainer
import androidx.health.services.client.data.DataType
import androidx.health.services.client.data.DeltaDataType
import androidx.health.services.client.data.HeartRateAccuracy
import androidx.health.services.client.data.SampleDataPoint
import com.cardiotwin.wear.data.Reading
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import java.time.Instant

/**
 * REAL WATCH SENSORS.
 *  hr        Health Services MeasureClient (about 1 sample per second while running), averaged and sent every minute
 *            with quality = mean sensor accuracy; samples with no skin contact or unreliable accuracy are dropped.
 *  steps     the Android step counter, sent as steps since the previous minute.
 *  fall      accelerometer heuristic ([FallDetector]); the watch then asks "Are you OK?".
 * NOT measured here, because Wear OS gives third-party apps no API for them: SpO2, breathing rate, HRV, rhythm/ECG
 * (QTc) and sleep stage. On Samsung watches the Samsung Health Sensor SDK can add SpO2, ECG and IBI (for HRV);
 * otherwise they come from the ESP32 sensor or the phone. The risk model still runs on heart rate alone, with the
 * freshness gate reporting the missing signals.
 */
class WatchSensorSource(private val context: Context, private val patientId: String) : SensorSource {
    private val measure = HealthServices.getClient(context).measureClient
    private val sensors = context.getSystemService(SensorManager::class.java)
    private val fall = FallDetector()

    private val _description = MutableStateFlow("Watch sensors: heart rate, steps, fall check")
    override val description = _description.asStateFlow()

    private val hrSamples = mutableListOf<Pair<Double, Double>>()      // bpm, quality
    private var lastStepTotal: Float? = null
    private var stepDelta = 0f
    private var emit: ((List<Reading>) -> Unit)? = null
    private var job: Job? = null

    private val hrCallback = object : MeasureCallback {
        override fun onAvailabilityChanged(dataType: DeltaDataType<*, *>, availability: Availability) {
            Log.i(TAG, "heart rate availability: $availability")
        }

        override fun onDataReceived(data: DataPointContainer) {
            data.getData(DataType.HEART_RATE_BPM).forEach { p: SampleDataPoint<Double> ->
                val q = when ((p.accuracy as? HeartRateAccuracy)?.sensorStatus) {
                    HeartRateAccuracy.SensorStatus.ACCURACY_HIGH -> 0.95
                    HeartRateAccuracy.SensorStatus.ACCURACY_MEDIUM -> 0.8
                    HeartRateAccuracy.SensorStatus.ACCURACY_LOW -> 0.5
                    HeartRateAccuracy.SensorStatus.NO_CONTACT, HeartRateAccuracy.SensorStatus.UNRELIABLE -> null
                    else -> 0.6
                }
                if (q != null && Reading.isValid("hr", p.value)) synchronized(hrSamples) { hrSamples += p.value to q }
            }
        }
    }

    private val sensorListener = object : SensorEventListener {
        override fun onSensorChanged(e: SensorEvent) {
            when (e.sensor.type) {
                Sensor.TYPE_STEP_COUNTER -> {
                    val total = e.values[0]
                    lastStepTotal?.let { stepDelta += (total - it).coerceAtLeast(0f) }
                    lastStepTotal = total
                }
                Sensor.TYPE_ACCELEROMETER -> {
                    val impact = fall.onSample(SystemClock.elapsedRealtime(), e.values[0].toDouble(), e.values[1].toDouble(), e.values[2].toDouble())
                    if (impact != null) {
                        emit?.invoke(listOf(Reading(patientId, Instant.now(), "fall", 1.0, Reading.SOURCE_WATCH, null,
                            mapOf("impact_g" to Math.round(impact * 10) / 10.0, "detector" to "accelerometer heuristic v1"))))
                    }
                }
            }
        }

        override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) = Unit
    }

    override fun start(scope: CoroutineScope, emit: (List<Reading>) -> Unit) {
        this.emit = emit
        val missing = mutableListOf<String>()
        try {
            measure.registerMeasureCallback(DataType.HEART_RATE_BPM, hrCallback)
        } catch (e: Exception) {
            Log.w(TAG, "heart rate unavailable", e)
            missing += "heart rate (${e.message})"
        }
        sensors.getDefaultSensor(Sensor.TYPE_STEP_COUNTER)?.let { sensors.registerListener(sensorListener, it, SensorManager.SENSOR_DELAY_NORMAL) }
            ?: missing.add("step counter")
        sensors.getDefaultSensor(Sensor.TYPE_ACCELEROMETER)?.let { sensors.registerListener(sensorListener, it, SensorManager.SENSOR_DELAY_GAME) }
            ?: missing.add("accelerometer (no fall check)")
        if (missing.isNotEmpty()) _description.value = "Watch sensors. Not available: ${missing.joinToString()}"

        job = scope.launch {
            while (isActive) {
                delay(60_000)
                val now = Instant.now()
                val out = mutableListOf<Reading>()
                synchronized(hrSamples) {
                    if (hrSamples.isNotEmpty()) {
                        val bpm = hrSamples.map { it.first }.average()
                        val q = hrSamples.map { it.second }.average()
                        out += Reading(patientId, now, "hr", Math.round(bpm * 10) / 10.0, Reading.SOURCE_WATCH, Math.round(q * 100) / 100.0,
                            mapOf("samples" to hrSamples.size))
                        hrSamples.clear()
                    }
                }
                if (lastStepTotal != null) {
                    out += Reading(patientId, now, "steps", stepDelta.toDouble(), Reading.SOURCE_WATCH)
                    stepDelta = 0f
                }
                if (out.isNotEmpty()) emit(out)
            }
        }
    }

    override fun stop() {
        job?.cancel()
        runCatching { measure.unregisterMeasureCallbackAsync(DataType.HEART_RATE_BPM, hrCallback) }
        sensors.unregisterListener(sensorListener)
    }

    /** Test button on a real watch: a fall reading marked simulated (the backend still runs the full fall check). */
    override fun simulateFall() {
        emit?.invoke(listOf(Reading(patientId, Instant.now(), "fall", 1.0, Reading.SOURCE_WATCH, null,
            mapOf("simulated" to true, "label" to Reading.SIMULATED_LABEL, "impact_g" to 3.4))))
    }

    /** Test button: a real watch has no rhythm API here, so send the two AF rhythm labels the backend needs for its alert. */
    override fun simulateAf() {
        val now = Instant.now()
        emit?.invoke(listOf(now.minusSeconds(60), now).map {
            Reading(patientId, it, "rhythm", "af_flutter", Reading.SOURCE_WATCH, null,
                mapOf("simulated" to true, "label" to Reading.SIMULATED_LABEL, "episode" to "simulated AF"))
        })
    }

    companion object { private const val TAG = "WatchSensors" }
}
