package com.cardiotwin.wear.net

import com.cardiotwin.wear.data.TwinAlert
import com.cardiotwin.wear.data.WatchView
import com.cardiotwin.wear.data.optStringOrNull
import org.json.JSONObject

/** What the backend sends on WS /ws/device (see backend/cardiotwin_backend/app.py ws_device and service._publish_alert). */
sealed interface LinkEvent {
    data class Hello(val patientId: String, val deviceId: String?, val simulated: Boolean) : LinkEvent

    /** `status` after each risk-engine evaluation. A device only gets views.watch (app.shape_for). */
    data class Status(
        val watch: WatchView?,
        val status: String?,          // OK | INSUFFICIENT_DATA | ...
        val tier: String?,            // LOW | MODERATE | HIGH | null
        val displayState: String?,
        val emergencyState: String?,
        val evaluatedAt: String?,
        val simulated: Boolean,
    ) : LinkEvent

    /** `alert` or `emergency`. A pending fall check also carries the FALL_CHECK watch view. */
    data class Alert(val alert: TwinAlert, val emergency: Boolean, val watch: WatchView?) : LinkEvent
    data class Ack(val alertId: String?, val state: String?, val accepted: Int?) : LinkEvent
    data class Error(val detail: String) : LinkEvent
}

object ServerMessages {
    fun parse(text: String): LinkEvent? = try { parse(JSONObject(text)) } catch (e: Exception) { LinkEvent.Error("Bad message: ${e.message}") }

    fun parse(m: JSONObject): LinkEvent? {
        val simulated = m.optBoolean("simulated", false)
        return when (m.optString("type")) {
            "hello" -> LinkEvent.Hello(m.optString("patient_id"), m.optStringOrNull("device_id"), simulated)
            "status" -> LinkEvent.Status(
                watch = WatchView.fromJson(m.optJSONObject("views")?.optJSONObject("watch")),
                status = m.optStringOrNull("status"),
                tier = m.optStringOrNull("tier"),
                displayState = m.optStringOrNull("display_state"),
                emergencyState = m.optStringOrNull("emergency_state"),
                evaluatedAt = m.optStringOrNull("evaluated_at"),
                simulated = simulated,
            )
            "alert", "emergency" -> {
                val a = m.optJSONObject("alert")?.let { TwinAlert.fromJson(it, simulated) } ?: return null
                val watch = WatchView.fromJson(m.optJSONObject("modal")?.optJSONObject("watch"))
                    ?: WatchView.fromJson(m.optJSONObject("watch"))
                LinkEvent.Alert(a, m.optString("type") == "emergency", watch)
            }
            "ack" -> LinkEvent.Ack(m.optStringOrNull("alert_id"), m.optStringOrNull("state"),
                if (m.has("accepted")) m.optInt("accepted") else null)
            "error" -> LinkEvent.Error(m.optString("detail"))
            else -> null       // pong and anything new
        }
    }
}
