package com.cardiotwin.wear

import com.cardiotwin.wear.data.Reading
import com.cardiotwin.wear.net.BackendApi
import com.cardiotwin.wear.net.BackendLink
import com.cardiotwin.wear.net.LinkEvent
import com.cardiotwin.wear.net.LinkState
import com.cardiotwin.wear.net.OfflineMockLink
import com.cardiotwin.wear.net.WifiLink
import com.cardiotwin.wear.sensors.MockReplaySource
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File
import java.time.Instant
import java.util.concurrent.CopyOnWriteArrayList

/**
 * The mock replay and the two links, end to end.
 *  offlineLinkRunsTheFallCheck: no server needed.
 *  wifiLinkAgainstRealBackend: needs the backend running; set CARDIOTWIN_TEST_SERVER=http://127.0.0.1:8000 to run it.
 */
class LinkTest {
    private fun asset(name: String) = listOf("src/main/assets/mock/$name", "app/src/main/assets/mock/$name").map(::File).first { it.exists() }.readText()

    private class Recorder(link: BackendLink, scope: CoroutineScope) {
        val events = CopyOnWriteArrayList<LinkEvent>()
        init { scope.launch { link.events.collect { events += it } } }
        suspend fun <T : LinkEvent> await(what: String, ms: Long = 30_000, pick: (LinkEvent) -> T?): T = withTimeout(ms) {
            while (true) { events.firstNotNullOfOrNull(pick)?.let { return@withTimeout it }; delay(100) }
            @Suppress("UNREACHABLE_CODE") error(what)
        }
    }

    @Test
    fun offlineLinkRunsTheFallCheck() = runBlocking {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
        val link = OfflineMockLink(asset("server_replies.json"))
        val rec = Recorder(link, scope)
        delay(100)
        link.start(scope)
        val src = MockReplaySource(asset("replay_stay_37093652.json"), "offline-demo", 600.0)
        src.start(scope) { link.sendReadings(it) }
        val status = rec.await("status") { it as? LinkEvent.Status }
        assertNotNull(status.watch)
        src.simulateFall()
        val fall = rec.await("fall check") { (it as? LinkEvent.Alert)?.takeIf { a -> a.alert.kind == "FALL" && a.alert.state == "AWAITING_RESPONSE" } }
        link.sendFallResponse(fall.alert.alertId, "need_help")
        val em = rec.await("emergency") { (it as? LinkEvent.Alert)?.takeIf { a -> a.emergency } }
        assertEquals("EMERGENCY_WATCH", em.watch?.type)
        scope.cancel()
    }

    @Test
    fun wifiLinkAgainstRealBackend() = runBlocking {
        val server = System.getenv("CARDIOTWIN_TEST_SERVER")
        assumeTrue("set CARDIOTWIN_TEST_SERVER to run", server != null)
        val patient = System.getenv("CARDIOTWIN_TEST_PATIENT") ?: "stay-30876334"
        val paired = BackendApi().pairWithDemoLogin(server!!, "dr.demo", "demo1234", patient, Reading.SOURCE_SIMULATOR)
        assertEquals(patient, paired.patientId)

        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
        val link = WifiLink(server, paired.deviceToken)
        val rec = Recorder(link, scope)
        delay(100)
        link.start(scope)
        withTimeout(10_000) { while (link.connection.value.state != LinkState.CONNECTED) delay(100) }
        val src = MockReplaySource(asset("replay_stay_37093652.json"), patient, 60.0)
        src.start(scope) { link.sendReadings(it) }

        rec.await("hello") { it as? LinkEvent.Hello }
        val ack = rec.await("ack") { (it as? LinkEvent.Ack)?.takeIf { a -> (a.accepted ?: 0) > 0 } }
        println("backend accepted ${ack.accepted} readings in the first batch")
        val status = rec.await("status", 60_000) { it as? LinkEvent.Status }
        println("first status: ${status.watch?.title} / ${status.watch?.text} tier=${status.tier} simulated=${status.simulated}")
        assertTrue(status.simulated)

        // fall -> I'm OK
        src.simulateFall()
        val fall = rec.await("fall check") { (it as? LinkEvent.Alert)?.takeIf { a -> a.alert.kind == "FALL" && a.alert.state == "AWAITING_RESPONSE" } }
        assertEquals("FALL_CHECK", (fall.watch ?: fall.alert.watch)?.type)
        link.sendFallResponse(fall.alert.alertId, "im_ok")
        val ok = rec.await("resolved") { (it as? LinkEvent.Ack)?.takeIf { a -> a.alertId == fall.alert.alertId } }
        assertEquals("RESOLVED", ok.state)

        // fall -> I need help -> emergency
        delay(500)
        src.simulateFall()
        val fall2 = rec.await("second fall check") {
            (it as? LinkEvent.Alert)?.takeIf { a -> a.alert.kind == "FALL" && a.alert.state == "AWAITING_RESPONSE" && a.alert.alertId != fall.alert.alertId }
        }
        link.sendFallResponse(fall2.alert.alertId, "need_help")
        val em = rec.await("emergency") { (it as? LinkEvent.Alert)?.takeIf { a -> a.emergency && a.alert.alertId == fall2.alert.alertId } }
        println("emergency: ${em.watch?.title} / ${em.watch?.text} / ${em.watch?.actionLabel}")
        assertEquals("EMERGENCY_WATCH", em.watch?.type)

        // a dose reading is accepted (meta.drug)
        link.sendReadings(listOf(Reading(patient, Instant.now(), "dose_taken", 25.0, Reading.SOURCE_SIMULATOR, null,
            mapOf("drug" to "metoprolol", "simulated" to true))))
        delay(1000)
        val errors = rec.events.filterIsInstance<LinkEvent.Error>()
        assertTrue("server errors: $errors", errors.isEmpty())
        src.stop(); link.stop(); scope.cancel()
    }
}
