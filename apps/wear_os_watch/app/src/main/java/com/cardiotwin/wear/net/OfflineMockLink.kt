package com.cardiotwin.wear.net

import android.content.Context
import com.cardiotwin.wear.data.Reading
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import org.json.JSONObject
import java.time.Duration
import java.time.Instant
import java.util.UUID

/**
 * No backend: plays backend replies that were RECORDED from the real CardioTwin backend (assets/mock/server_replies.json,
 * made by wear_os_watch/tools/record_mock_replies.py while the same mock replay was streamed to it). Status messages are played
 * back in the order they were received, other alerts at the point of the replay where they were raised; the fall check, the possible-AF alert and the fall emergency are re-issued
 * when this watch sends a fall or AF readings, with the backend's own rules mirrored:
 *   fall      -> FALL_CHECK with the recorded countdown; "I'm OK" resolves it; "I need help" or no answer -> emergency
 *   rhythm    -> 2 or more af_flutter readings within 30 minutes -> possible-AF alert (once until restarted)
 * Every message is marked SIMULATED. Nothing is scored on the watch: the tiers shown are the backend's recorded ones.
 */
class OfflineMockLink(repliesJson: String) : BackendLink {
    constructor(context: Context) : this(context.assets.open(ASSET).bufferedReader().use { it.readText() })

    private val replies = JSONObject(repliesJson)
    private val statuses = replies.getJSONArray("statuses").let { a -> (0 until a.length()).map { a.getJSONObject(it) } }
    private val alerts = replies.optJSONArray("alerts")?.let { a -> (0 until a.length()).map { a.getJSONObject(it) } }.orEmpty()
    private var alertsPlayed = 0

    private val _connection = MutableStateFlow(Connection(LinkState.STOPPED))
    override val connection = _connection.asStateFlow()
    private val _events = MutableSharedFlow<LinkEvent>(extraBufferCapacity = 64)
    override val events = _events.asSharedFlow()

    private var scope: CoroutineScope? = null
    private var batches = 0
    private var lastStatus = -1
    private val afTimes = ArrayDeque<Instant>()
    private var afAlerted = false
    private var fallAlertId: String? = null
    private var fallTimer: Job? = null

    override fun start(scope: CoroutineScope) {
        this.scope = scope
        _connection.value = Connection(LinkState.CONNECTED, "offline demo (recorded backend replies)")
        emit(JSONObject().put("type", "hello").put("patient_id", "offline-demo").put("simulated", true))
    }

    override fun stop() {
        fallTimer?.cancel()
        _connection.value = Connection(LinkState.STOPPED)
    }

    override fun sendReadings(readings: List<Reading>) {
        batches++
        emit(JSONObject().put("type", "ack").put("accepted", readings.size).put("duplicates", 0))
        // latest recorded status for this point in the stream
        val idx = statuses.indexOfLast { it.getInt("after_batch") <= batches }
        if (idx >= 0 && idx != lastStatus && fallAlertId == null) {
            lastStatus = idx
            emit(statuses[idx].getJSONObject("message"))
        }
        // alerts the backend raised at this point of the recorded replay (risk alerts, possible AF in the real data, ...)
        while (alertsPlayed < alerts.size && alerts[alertsPlayed].getInt("after_batch") <= batches) {
            val m = withId(alerts[alertsPlayed].getJSONObject("message"), UUID.randomUUID().toString())
            if (m.optJSONObject("alert")?.optString("kind") == "RHYTHM_AF") afAlerted = true
            emit(m)
            alertsPlayed++
        }
        for (r in readings) {
            if (r.type == "fall" && (r.value as? Number)?.toInt() == 1) onFall()
            if (r.type == "rhythm" && r.value == "af_flutter") onAf(r.timestamp)
        }
    }

    override fun sendFallResponse(alertId: String, answer: String) {
        if (alertId != fallAlertId) {
            emit(JSONObject().put("type", "error").put("detail", "This fall check is no longer waiting for an answer."))
            return
        }
        fallTimer?.cancel()
        if (answer == "im_ok") {
            fallAlertId = null
            emit(JSONObject().put("type", "ack").put("alert_id", alertId).put("state", "RESOLVED"))
            val resolved = withId(replies.getJSONObject("fall_alert"), alertId)
            resolved.getJSONObject("alert").put("state", "RESOLVED")
            resolved.remove("watch")
            emit(resolved)
            if (lastStatus >= 0) emit(statuses[lastStatus].getJSONObject("message"))
        } else {
            escalate(alertId)
            emit(JSONObject().put("type", "ack").put("alert_id", alertId).put("state", "ESCALATED"))
        }
    }

    private fun onFall() {
        if (fallAlertId != null) return
        val id = UUID.randomUUID().toString()
        fallAlertId = id
        val alert = withId(replies.getJSONObject("fall_alert"), id)
        emit(alert)
        val check = alert.optJSONObject("watch") ?: return
        emit(JSONObject().put("type", "status").put("simulated", true).put("label", Reading.SIMULATED_LABEL)
            .put("status", "OK").put("display_state", "FALL_CHECK")
            .put("views", JSONObject().put("watch", JSONObject(check.toString()).put("alert_id", id))))
        val secs = check.optInt("countdown_seconds", 30)
        fallTimer = scope?.launch {
            delay(secs * 1000L)
            if (fallAlertId == id) escalate(id)
        }
    }

    private fun escalate(id: String) {
        val em = withId(replies.getJSONObject("fall_emergency"), id)
        emit(em)
        val watch = em.optJSONObject("modal")?.optJSONObject("watch")
        if (watch != null) {
            emit(JSONObject().put("type", "status").put("simulated", true).put("label", Reading.SIMULATED_LABEL)
                .put("status", "OK").put("display_state", "EMERGENCY_ALERT").put("emergency_state", "TRIGGERED")
                .put("views", JSONObject().put("watch", watch)))
        }
    }

    private fun onAf(t: Instant) {
        afTimes.addLast(t)
        while (afTimes.isNotEmpty() && Duration.between(afTimes.first(), t) > Duration.ofMinutes(30)) afTimes.removeFirst()
        if (!afAlerted && afTimes.size >= 2) {
            afAlerted = true
            emit(withId(replies.getJSONObject("af_alert"), UUID.randomUUID().toString()))
        }
    }

    private fun withId(template: JSONObject, id: String): JSONObject {
        val m = JSONObject(template.toString())
        m.optJSONObject("alert")?.put("alert_id", id)?.put("created_at", Reading.iso(Instant.now()))
        m.put("simulated", true)
        return m
    }

    private fun emit(m: JSONObject) {
        ServerMessages.parse(m)?.let { _events.tryEmit(it) }
    }

    companion object {
        const val ASSET = "mock/server_replies.json"
    }
}
