package com.cardiotwin.wear.data

import org.json.JSONObject
import java.time.Instant
import java.time.temporal.ChronoUnit

/**
 * One reading in the backend's common format (backend/cardiotwin_backend/readings.py):
 * {patient_id, timestamp, type, value, source} plus optional quality and meta.
 * `value` is a Double for numeric types and a String for `rhythm` and `sleep_stage`.
 */
data class Reading(
    val patientId: String,
    val timestamp: Instant,
    val type: String,
    val value: Any,
    val source: String,
    val quality: Double? = null,
    val meta: Map<String, Any?>? = null,
) {
    fun toJson(): JSONObject = JSONObject().apply {
        put("patient_id", patientId)
        put("timestamp", iso(timestamp))
        put("type", type)
        put("value", value)
        put("source", source)
        quality?.let { put("quality", it) }
        meta?.let { m -> put("meta", JSONObject().apply { m.forEach { (k, v) -> put(k, v ?: JSONObject.NULL) } }) }
    }

    val isSimulated: Boolean get() = source == SOURCE_SIMULATOR || meta?.get("simulated") == true

    companion object {
        const val SOURCE_WATCH = "watch"
        const val SOURCE_SIMULATOR = "simulator"
        const val SIMULATED_LABEL = "SIMULATED"

        /** Types the backend accepts, with the same hard limits it applies at the door. */
        val LIMITS = mapOf(
            "hr" to 20.0..250.0, "hrv_rmssd" to 1.0..500.0, "spo2" to 50.0..100.0, "resp" to 4.0..60.0,
            "steps" to 0.0..100000.0, "ecg_qtc" to 200.0..800.0, "fall" to 0.0..1.0, "dose_taken" to 0.0..100000.0,
        )
        val RHYTHMS = setOf("sinus", "af_flutter", "tachy", "brady", "paced", "other", "unknown")
        val SLEEP_STAGES = setOf("awake", "light", "deep", "rem")

        /** Same shape as the backend's iso(): milliseconds and a Z. */
        fun iso(t: Instant): String = t.truncatedTo(ChronoUnit.MILLIS).toString().let {
            if (it.contains('.')) it else it.removeSuffix("Z") + ".000Z"
        }

        /** True when the backend would accept this type and value. Lets the watch drop bad sensor values early. */
        fun isValid(type: String, value: Any): Boolean = when (type) {
            "rhythm" -> value is String && value in RHYTHMS
            "sleep_stage" -> value is String && value in SLEEP_STAGES
            else -> (value as? Number)?.toDouble()?.let { v -> LIMITS[type]?.contains(v) } ?: false
        }
    }
}
