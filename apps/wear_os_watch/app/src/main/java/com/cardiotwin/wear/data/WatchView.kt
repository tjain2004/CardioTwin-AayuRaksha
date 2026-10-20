package com.cardiotwin.wear.data

import org.json.JSONObject

/**
 * The watch screen the backend sends in every `status` message (views.watch), produced by the risk engine's
 * views.watch() or by the backend's safety layer. Types seen:
 *  WATCH_FACE      normal face: title/text/icon/tier, e.g. "No alert" / "Keep wearing"
 *  WATCH_NOTICE    a review alert, e.g. "Irregular rhythm" / "Record ECG, see phone"
 *  FALL_CHECK      "Fall detected" / "Are you OK?" with countdown_seconds and the alert_id to answer
 *  EMERGENCY_WATCH full-screen "EMERGENCY" / "Check your phone now" with an action (open the phone dialler)
 */
data class WatchView(
    val type: String,
    val title: String,
    val text: String,
    val iconGlyph: String?,
    val tier: String?,
    val haptic: String?,
    val alertId: String?,
    val countdownSeconds: Int?,
    val actionLabel: String?,
    val actionType: String?,
    val lastSyncMinutes: Double?,
    val stale: Boolean,
    val evaluatedAt: String?,
) {
    companion object {
        const val FACE = "WATCH_FACE"
        const val NOTICE = "WATCH_NOTICE"
        const val FALL_CHECK = "FALL_CHECK"
        const val EMERGENCY = "EMERGENCY_WATCH"

        val WAITING = WatchView(FACE, "Connecting", "Waiting for first update", "⟳", null, null, null, null,
            null, null, null, false, null)

        fun fromJson(o: JSONObject?): WatchView? {
            if (o == null) return null
            val action = o.optJSONObject("action")
            return WatchView(
                type = o.optString("type", FACE),
                title = o.optString("title", ""),
                text = o.optString("text", ""),
                iconGlyph = o.optStringOrNull("icon_glyph"),
                tier = o.optStringOrNull("tier"),
                haptic = o.optStringOrNull("haptic") ?: o.optStringOrNull("haptics"),
                alertId = o.optStringOrNull("alert_id"),
                countdownSeconds = if (o.has("countdown_seconds")) o.optInt("countdown_seconds") else null,
                actionLabel = action?.optStringOrNull("label"),
                actionType = action?.optStringOrNull("type"),
                lastSyncMinutes = if (o.isNull("last_sync_minutes")) null else o.optDouble("last_sync_minutes"),
                stale = o.optBoolean("stale", false),
                evaluatedAt = o.optStringOrNull("evaluated_at"),
            )
        }
    }
}

/** One alert as the backend pushes it (`alert` / `emergency` messages). */
data class TwinAlert(
    val alertId: String,
    val kind: String,          // FALL, RHYTHM_AF, QTC_LONG, DOSE, EMERGENCY, RISK ...
    val severity: String,      // low | moderate | high | critical
    val state: String,         // OPEN, AWAITING_RESPONSE, ACKNOWLEDGED, RESOLVED ...
    val title: String,
    val message: String,
    val watch: WatchView?,
    val simulated: Boolean,
    val receivedAtMillis: Long = System.currentTimeMillis(),
) {
    /** Same open states as the backend's alerts.OPEN_STATES. */
    val isOpen: Boolean get() = state in setOf("NEW", "DISPLAYED", "AWAITING_RESPONSE", "ACKNOWLEDGED", "ESCALATED")
    val isEmergency: Boolean get() = severity == "critical" && isOpen

    companion object {
        fun fromJson(o: JSONObject, simulated: Boolean): TwinAlert? {
            val id = o.optStringOrNull("alert_id") ?: return null
            return TwinAlert(
                alertId = id,
                kind = o.optString("kind", ""),
                severity = o.optString("severity", "moderate"),
                state = o.optString("state", "OPEN"),
                title = o.optString("title", ""),
                message = o.optString("message", ""),
                // an escalated fall or engine emergency carries the full-screen watch view inside payload.emergency
                watch = WatchView.fromJson(o.optJSONObject("payload")?.optJSONObject("emergency")?.optJSONObject("watch"))
                    ?: WatchView.fromJson(o.optJSONObject("payload")?.optJSONObject("watch")),
                simulated = o.optBoolean("simulated", simulated),
            )
        }
    }
}

fun JSONObject.optStringOrNull(key: String): String? = if (!has(key) || isNull(key)) null else optString(key)
