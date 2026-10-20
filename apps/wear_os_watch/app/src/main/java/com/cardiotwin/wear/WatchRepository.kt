package com.cardiotwin.wear

import android.content.Context
import android.util.Log
import com.cardiotwin.wear.data.DueDose
import com.cardiotwin.wear.data.LinkMode
import com.cardiotwin.wear.data.Medication
import com.cardiotwin.wear.data.MedicationSchedule
import com.cardiotwin.wear.data.Reading
import com.cardiotwin.wear.data.SensorMode
import com.cardiotwin.wear.data.Settings
import com.cardiotwin.wear.data.TwinAlert
import com.cardiotwin.wear.data.WatchView
import com.cardiotwin.wear.net.BackendApi
import com.cardiotwin.wear.net.BackendLink
import com.cardiotwin.wear.net.Connection
import com.cardiotwin.wear.net.LinkEvent
import com.cardiotwin.wear.net.LinkState
import com.cardiotwin.wear.net.OfflineMockLink
import com.cardiotwin.wear.net.PhoneRelayLink
import com.cardiotwin.wear.net.WifiLink
import com.cardiotwin.wear.sensors.MockReplaySource
import com.cardiotwin.wear.sensors.SensorSource
import com.cardiotwin.wear.sensors.WatchSensorSource
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.time.Instant
import java.time.LocalDateTime
import java.time.LocalTime
import java.time.format.DateTimeFormatter

data class Vital(val value: Any, val at: Instant, val simulated: Boolean)

/** An open "Fall detected. Are you OK?" check. alertId is null until the backend's FALL alert arrives. */
data class FallCheck(val alertId: String?, val deadlineMillis: Long, val totalSeconds: Int, val answer: String? = null)

data class UiState(
    val vitals: Map<String, Vital> = emptyMap(),
    val watch: WatchView = WatchView.WAITING,
    val tier: String? = null,
    val status: String? = null,
    val evaluatedAt: String? = null,
    val simulated: Boolean = false,
    val alerts: List<TwinAlert> = emptyList(),
    val fallCheck: FallCheck? = null,
    val emergency: WatchView? = null,
    val emergencyHidden: Boolean = false,
    /** True when the emergency was raised on the watch because the server could not be reached. */
    val localEmergency: Boolean = false,
    val dueDose: DueDose? = null,
    val nextDose: DueDose? = null,
    val connection: Connection = Connection(LinkState.STOPPED),
    val sourceDescription: String = "",
    val sensorMode: SensorMode = SensorMode.MOCK,
    val linkMode: LinkMode = LinkMode.OFFLINE,
    val sentCount: Int = 0,
    val log: List<String> = emptyList(),
)

/** One-off UI events: which page to jump to when something arrives. */
enum class UiJump { ALERTS, MEDICATION }

/**
 * Wires a [SensorSource] to a [BackendLink] and turns backend messages into [UiState] for the five screens.
 * Lives as long as the process (owned by [CardioTwinApp]); [MonitorService] keeps the process alive with the screen off.
 */
class WatchRepository(private val context: Context) {
    val settings = Settings(context)
    private val api = BackendApi()
    private val haptics = Haptics(context)
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    private val _state = MutableStateFlow(UiState())
    val state = _state.asStateFlow()
    private val _jumps = MutableSharedFlow<UiJump>(extraBufferCapacity = 4)
    val jumps = _jumps.asSharedFlow()

    private var source: SensorSource? = null
    private var link: BackendLink? = null
    private var runJob: Job? = null
    private var pendingFallAnswer: String? = null

    private val meds: MutableList<Medication> = MedicationSchedule.DEMO.toMutableList()
    private val handledDoses = mutableSetOf<String>()
    private var snoozedUntil: LocalDateTime? = null

    val isRunning: Boolean get() = runJob?.isActive == true

    // ------------------------------------------------------------------ lifecycle
    @Synchronized
    fun start() {
        if (isRunning) return
        val s = settings
        val patient = s.pairedPatientId.takeIf { s.linkMode == LinkMode.WIFI } ?: s.patientId
        val src: SensorSource = when (s.sensorMode) {
            SensorMode.MOCK -> MockReplaySource(context, patient, s.mockSpeed)
            SensorMode.WATCH -> WatchSensorSource(context, patient)
        }
        val lnk: BackendLink? = when (s.linkMode) {
            LinkMode.OFFLINE -> OfflineMockLink(context)
            LinkMode.PHONE -> PhoneRelayLink(context)
            LinkMode.WIFI -> pairingProblem()?.let { problem ->
                _state.update { it.copy(connection = Connection(LinkState.NOT_PAIRED, problem)) }
                null
            } ?: WifiLink(s.serverUrl, s.deviceToken!!)
        }
        source = src
        link = lnk
        _state.update { it.copy(sensorMode = s.sensorMode, linkMode = s.linkMode, watch = WatchView.WAITING, tier = null,
            status = null, fallCheck = null, emergency = null, alerts = emptyList(), vitals = emptyMap(), sentCount = 0) }
        log("Started: ${s.sensorMode.label} -> ${s.linkMode.label}, patient $patient")

        runJob = scope.launch {
            lnk?.let { l ->
                launch { l.connection.collect { c -> _state.update { it.copy(connection = c) } } }
                launch { l.events.collect { onEvent(it) } }
                l.start(this)
            }
            launch { src.description.collect { d -> _state.update { it.copy(sourceDescription = d) } } }
            launch { medicationTicker() }
            launch { fallCountdownWatcher() }
            src.start(this) { readings -> onReadings(readings) }
        }
    }

    @Synchronized
    fun stop() {
        source?.stop()
        link?.stop()
        runJob?.cancel()
        runJob = null
        source = null
        link = null
        _state.update { it.copy(connection = Connection(LinkState.STOPPED)) }
    }

    fun restart() {
        stop()
        start()
    }

    /** Null when the Wi-Fi link can run, else what is wrong with the pairing. */
    fun pairingProblem(): String? {
        val s = settings
        return when {
            s.deviceToken == null -> "Not paired. Open Setup and pair."
            s.pairedServer != s.serverUrl -> "Paired with another server. Pair again."
            s.pairedSource != s.readingSource -> "Paired for ${s.pairedSource} readings; ${s.sensorMode.label} sends ${s.readingSource}. Pair again."
            else -> null
        }
    }

    // ------------------------------------------------------------------ pairing (Setup screen)
    suspend fun pairWithCode(code: String): Result<String> = pair { api.pair(settings.serverUrl, code, settings.readingSource, deviceName()) }

    suspend fun pairWithDemoLogin(): Result<String> = pair {
        api.pairWithDemoLogin(settings.serverUrl, BuildConfig.DEMO_USER, BuildConfig.DEMO_PASSWORD, settings.patientId, settings.readingSource)
    }

    suspend fun testServer(): Result<String> = withContext(Dispatchers.IO) {
        runCatching { api.health(settings.serverUrl).let { "Server OK, model loaded: ${it.opt("model_loaded") ?: it.opt("ok") ?: "?"}" } }
    }

    private suspend fun pair(call: () -> BackendApi.Paired): Result<String> = withContext(Dispatchers.IO) {
        runCatching {
            val p = call()
            settings.deviceToken = p.deviceToken
            settings.pairedPatientId = p.patientId
            settings.pairedSource = p.source
            settings.pairedServer = settings.serverUrl
            log("Paired as ${p.source} for ${p.patientId}")
            "Paired with ${p.patientId}"
        }.onSuccess { restart() }
    }

    private fun deviceName() = if (settings.sensorMode == SensorMode.MOCK) "Wear OS watch (SIMULATED replay)" else "Wear OS watch"

    // ------------------------------------------------------------------ demo controls
    fun simulateFall() = source?.simulateFall()
    fun simulateAf() {
        source?.simulateAf()
        log("Simulated AF episode started")
    }

    // ------------------------------------------------------------------ readings out
    private fun onReadings(readings: List<Reading>) {
        val ok = readings.filter { Reading.isValid(it.type, it.value) }
        if (ok.isEmpty()) return
        _state.update { st ->
            val v = st.vitals.toMutableMap()
            ok.forEach { r -> if (r.type != "fall") v[r.type] = Vital(r.value, r.timestamp, r.isSimulated) }
            st.copy(vitals = v, sentCount = st.sentCount + ok.size)
        }
        if (ok.any { it.type == "fall" && (it.value as Number).toInt() == 1 }) startLocalFallCheck()
        link?.sendReadings(ok)
    }

    // ------------------------------------------------------------------ fall check
    private fun startLocalFallCheck(seconds: Int = 30) {
        if (_state.value.fallCheck != null) return
        haptics.play("long repeating")
        _state.update { it.copy(fallCheck = FallCheck(null, System.currentTimeMillis() + seconds * 1000L, seconds)) }
        log("Fall detected on the watch")
    }

    fun answerFall(answer: String) {
        val fc = _state.value.fallCheck ?: return
        haptics.cancel()
        val id = fc.alertId
        val connected = _state.value.connection.state == LinkState.CONNECTED
        when {
            id != null -> link?.sendFallResponse(id, answer)
            connected -> pendingFallAnswer = answer           // sent as soon as the backend's FALL alert arrives
            answer == "need_help" -> localEmergency("You asked for help, but the watch cannot reach the CardioTwin server.")
        }
        log("Fall answer: $answer")
        if (answer == "im_ok") _state.update { it.copy(fallCheck = null) }
        else _state.update { it.copy(fallCheck = fc.copy(answer = answer)) }
    }

    private suspend fun fallCountdownWatcher() {
        while (scope.isActive) {
            delay(500)
            val fc = _state.value.fallCheck ?: continue
            if (fc.answer == null && System.currentTimeMillis() > fc.deadlineMillis + 5_000 &&
                (fc.alertId == null && _state.value.connection.state != LinkState.CONNECTED)) {
                localEmergency("No answer after a fall, and the watch cannot reach the CardioTwin server.")
            }
        }
    }

    private fun localEmergency(reason: String) {
        haptics.play("long repeating")
        _state.update {
            it.copy(fallCheck = null, emergencyHidden = false, localEmergency = true, emergency = WatchView(WatchView.EMERGENCY, "EMERGENCY", "Use your phone to call for help",
                "⚠", null, "long repeating", null, null, "Open phone", "open_phone", null, false, null))
        }
        log("Local emergency: $reason")
    }

    fun hideEmergency() = _state.update { if (it.localEmergency) it.copy(emergency = null, localEmergency = false) else it.copy(emergencyHidden = true) }

    // ------------------------------------------------------------------ backend messages in
    private fun onEvent(e: LinkEvent) {
        when (e) {
            is LinkEvent.Hello -> log("Connected for ${e.patientId}${if (e.simulated) " (SIMULATED)" else ""}")
            is LinkEvent.Status -> onStatus(e)
            is LinkEvent.Alert -> onAlert(e)
            is LinkEvent.Ack -> if (e.alertId != null) {
                if (e.alertId == _state.value.fallCheck?.alertId && e.state in setOf("RESOLVED", "ESCALATED")) {
                    _state.update { it.copy(fallCheck = null) }
                }
                log("Alert ${e.alertId.take(8)}: ${e.state}")
            }
            is LinkEvent.Error -> log("Server: ${e.detail}")
        }
    }

    private fun onStatus(e: LinkEvent.Status) {
        val w = e.watch ?: return
        val prev = _state.value
        _state.update { st ->
            var s = st.copy(tier = e.tier, status = e.status, evaluatedAt = e.evaluatedAt, simulated = e.simulated)
            when (w.type) {
                WatchView.EMERGENCY -> s = s.copy(emergency = w, fallCheck = null, localEmergency = false,
                    emergencyHidden = st.emergencyHidden && st.emergency != null)
                WatchView.FALL_CHECK -> {
                    s = s.copy(fallCheck = (st.fallCheck ?: FallCheck(null, System.currentTimeMillis() + (w.countdownSeconds ?: 30) * 1000L,
                        w.countdownSeconds ?: 30)).copy(alertId = w.alertId ?: st.fallCheck?.alertId))
                }
                else -> {
                    // the backend keeps EMERGENCY_WATCH on every status while its emergency is open, so a normal face
                    // means it is over; an emergency raised on the watch itself (server unreachable) stays until hidden
                    s = s.copy(watch = w, emergency = st.emergency.takeIf { st.localEmergency })
                }
            }
            s
        }
        if (w.type == WatchView.FACE && w.haptic != null && (prev.watch.title != w.title)) haptics.play(w.haptic)
        flushPendingFallAnswer()
    }

    private fun onAlert(e: LinkEvent.Alert) {
        val a = e.alert
        val isNew = _state.value.alerts.none { it.alertId == a.alertId && it.state == a.state }
        _state.update { st ->
            val list = (listOf(a) + st.alerts.filter { it.alertId != a.alertId }).filter { it.isOpen }.take(20)
            var s = st.copy(alerts = list)
            if (a.kind == "FALL" && a.state == "AWAITING_RESPONSE") {
                val secs = (e.watch ?: a.watch)?.countdownSeconds ?: 30
                s = s.copy(fallCheck = (st.fallCheck ?: FallCheck(null, System.currentTimeMillis() + secs * 1000L, secs)).copy(alertId = a.alertId))
            }
            if (a.kind == "FALL" && a.state == "RESOLVED" && st.fallCheck?.alertId == a.alertId) s = s.copy(fallCheck = null)
            if (e.emergency || a.isEmergency) {
                s = s.copy(fallCheck = null, emergencyHidden = false, localEmergency = false,
                    emergency = e.watch ?: a.watch ?: WatchView(WatchView.EMERGENCY, "EMERGENCY", "Check your phone now", "⚠", null,
                        "long repeating", a.alertId, null, "Open phone", "open_phone", null, false, null))
            }
            s
        }
        if (isNew) {
            when {
                e.emergency || a.isEmergency -> haptics.play("long repeating")
                a.kind == "FALL" && a.state == "AWAITING_RESPONSE" -> if (_state.value.fallCheck?.alertId == a.alertId) Unit
                a.isOpen -> { haptics.play(a.watch?.haptic ?: "double"); _jumps.tryEmit(UiJump.ALERTS) }
            }
            log("${a.title} (${a.state})")
        }
        flushPendingFallAnswer()
    }

    private fun flushPendingFallAnswer() {
        val ans = pendingFallAnswer ?: return
        val id = _state.value.fallCheck?.alertId ?: return
        pendingFallAnswer = null
        link?.sendFallResponse(id, ans)
    }

    /** The backend has no watch-side acknowledge over the device socket yet, so "Seen" only hides it here. */
    fun dismissAlert(id: String) = _state.update { st -> st.copy(alerts = st.alerts.filter { it.alertId != id }) }

    // ------------------------------------------------------------------ medication reminders
    private suspend fun medicationTicker() {
        while (scope.isActive) {
            val now = LocalDateTime.now()
            val due = meds.flatMap { m -> m.times.map { DueDose(m, LocalDateTime.of(now.toLocalDate(), it)) } }
                .filter { !it.dueAt.isAfter(now) && it.dueAt.isAfter(now.minusHours(2)) && key(it) !in handledDoses }
                .minByOrNull { it.dueAt }
            val ringing = due?.takeIf { snoozedUntil?.let { s -> now.isAfter(s) } ?: true }
            val wasRinging = _state.value.dueDose
            _state.update { it.copy(dueDose = ringing, nextDose = MedicationSchedule.next(meds, now)) }
            if (ringing != null && wasRinging == null) {
                haptics.play("double")
                _jumps.tryEmit(UiJump.MEDICATION)
            }
            delay(10_000)
        }
    }

    fun doseTaken() {
        val d = _state.value.dueDose ?: return
        handledDoses += key(d)
        snoozedUntil = null
        val patient = settings.pairedPatientId.takeIf { settings.linkMode == LinkMode.WIFI } ?: settings.patientId
        val meta = mutableMapOf<String, Any?>("drug" to d.med.generic, "scheduled_for" to d.dueAt.toString())
        if (settings.sensorMode == SensorMode.MOCK) meta += mapOf("simulated" to true, "label" to Reading.SIMULATED_LABEL)
        onReadings(listOf(Reading(patient, Instant.now(), "dose_taken", d.med.mg, settings.readingSource, null, meta)))
        _state.update { it.copy(dueDose = null) }
        log("Dose taken: ${d.med.label}")
    }

    fun doseSnooze(minutes: Long = 10) {
        snoozedUntil = LocalDateTime.now().plusMinutes(minutes)
        _state.update { it.copy(dueDose = null) }
    }

    fun doseSkip() {
        _state.value.dueDose?.let { handledDoses += key(it); log("Dose skipped: ${it.med.label}") }
        _state.update { it.copy(dueDose = null) }
    }

    /** Demo: a reminder for the first medicine one minute from now. */
    fun demoReminderInOneMinute() {
        val t = LocalTime.now().plusMinutes(1).withSecond(0).withNano(0)
        val m = meds.first()
        meds[0] = m.copy(times = (m.times + t).distinct().sorted())
        snoozedUntil = null
        log("Demo reminder set for $t")
    }

    private fun key(d: DueDose) = "${d.med.generic}@${d.dueAt}"

    private fun log(line: String) {
        Log.i("CardioTwin", line)
        val t = LocalTime.now().format(DateTimeFormatter.ofPattern("HH:mm:ss"))
        _state.update { it.copy(log = (listOf("$t $line") + it.log).take(40)) }
    }
}
