package com.cardiotwin.wear

import com.cardiotwin.wear.data.MedicationSchedule
import com.cardiotwin.wear.data.Reading
import com.cardiotwin.wear.data.WatchView
import com.cardiotwin.wear.net.LinkEvent
import com.cardiotwin.wear.net.ServerMessages
import com.cardiotwin.wear.sensors.FallDetector
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.time.Instant
import java.time.LocalDateTime

/** JVM tests for the parts that do not need a watch: reading format, backend message parsing, fall heuristic, meds. */
class PureLogicTest {

    @Test
    fun readingMatchesBackendFormat() {
        val r = Reading("stay-37093652", Instant.parse("2026-10-19T10:05:00Z"), "hr", 92.0, "watch", 0.95, mapOf("samples" to 60))
        val j = r.toJson()
        assertEquals("stay-37093652", j.getString("patient_id"))
        assertEquals("2026-10-19T10:05:00.000Z", j.getString("timestamp"))     // same shape as backend readings.iso()
        assertEquals("hr", j.getString("type"))
        assertEquals(92.0, j.getDouble("value"), 0.0)
        assertEquals("watch", j.getString("source"))
        assertEquals(60, j.getJSONObject("meta").getInt("samples"))
        assertEquals(setOf("patient_id", "timestamp", "type", "value", "source", "quality", "meta"), j.keySet())
    }

    @Test
    fun validationMirrorsBackendLimits() {
        assertTrue(Reading.isValid("hr", 92.0))
        assertFalse(Reading.isValid("hr", 300.0))
        assertFalse(Reading.isValid("spo2", 40.0))
        assertTrue(Reading.isValid("rhythm", "af_flutter"))
        assertFalse(Reading.isValid("rhythm", "afib"))
        assertTrue(Reading.isValid("sleep_stage", "rem"))
        assertFalse(Reading.isValid("unknown_type", 1.0))
    }

    @Test
    fun parsesStatusWithWatchFace() {
        val e = ServerMessages.parse("""{"type":"status","patient_id":"stay-1","simulated":true,"label":"SIMULATED","status":"OK",
            "tier":"HIGH","display_state":"RISK","views":{"watch":{"type":"WATCH_FACE","title":"High risk","text":"See phone now",
            "icon_glyph":"▲","tier":"HIGH","haptic":"double","last_sync_minutes":1.0,"stale":false}}}""") as LinkEvent.Status
        assertEquals("HIGH", e.tier)
        assertTrue(e.simulated)
        assertEquals(WatchView.FACE, e.watch!!.type)
        assertEquals("High risk", e.watch!!.title)
        assertEquals("double", e.watch!!.haptic)
    }

    @Test
    fun parsesFallCheckAndEmergency() {
        val fall = ServerMessages.parse("""{"type":"alert","simulated":true,"alert":{"alert_id":"a1","kind":"FALL","severity":"high",
            "state":"AWAITING_RESPONSE","title":"Fall detected","message":"m","payload":{"watch":{"type":"FALL_CHECK","title":"Fall detected",
            "text":"Are you OK?","countdown_seconds":30,"actions":[{"id":"im_ok","label":"I'm OK"}]}}},
            "watch":{"type":"FALL_CHECK","title":"Fall detected","text":"Are you OK?","countdown_seconds":30}}""") as LinkEvent.Alert
        assertEquals("a1", fall.alert.alertId)
        assertEquals(30, fall.watch!!.countdownSeconds)
        assertFalse(fall.emergency)
        assertTrue(fall.alert.isOpen)

        val em = ServerMessages.parse("""{"type":"emergency","alert":{"alert_id":"a1","kind":"FALL","severity":"critical","state":"ESCALATED",
            "title":"Emergency warning: fall","message":"No answer","payload":{"emergency":{"watch":{"type":"EMERGENCY_WATCH","title":"EMERGENCY",
            "text":"Check your phone now","action":{"label":"Call 112","type":"open_phone_dialer"}}}}},
            "modal":{"watch":{"type":"EMERGENCY_WATCH","title":"EMERGENCY","text":"Check your phone now",
            "action":{"label":"Call 112","type":"open_phone_dialer"}}}}""") as LinkEvent.Alert
        assertTrue(em.emergency)
        assertTrue(em.alert.isEmergency)
        assertEquals("Call 112", em.watch!!.actionLabel)
        assertEquals(WatchView.EMERGENCY, em.alert.watch!!.type)     // taken from payload.emergency.watch
    }

    @Test
    fun recordedBackendRepliesParse() {
        val f = listOf("src/main/assets/mock/server_replies.json", "app/src/main/assets/mock/server_replies.json").map(::File).first { it.exists() }
        val j = JSONObject(f.readText())
        val statuses = j.getJSONArray("statuses")
        assertTrue(statuses.length() > 0)
        for (i in 0 until statuses.length()) {
            val s = ServerMessages.parse(statuses.getJSONObject(i).getJSONObject("message")) as LinkEvent.Status
            assertNotNull(s.watch)
        }
        val fall = ServerMessages.parse(j.getJSONObject("fall_alert")) as LinkEvent.Alert
        assertEquals("FALL", fall.alert.kind)
        assertNotNull((fall.watch ?: fall.alert.watch)?.countdownSeconds)
        val em = ServerMessages.parse(j.getJSONObject("fall_emergency")) as LinkEvent.Alert
        assertTrue(em.emergency)
        assertEquals(WatchView.EMERGENCY, em.watch!!.type)
    }

    @Test
    fun fallDetectorFiresOnFreeFallImpactAndStillness() {
        val d = FallDetector()
        var t = 0L
        var hit: Double? = null
        fun feed(g: Double, ms: Long) { val end = t + ms; while (t < end) { d.onSample(t, 0.0, 0.0, g * 9.80665)?.let { hit = it }; t += 20 } }
        feed(1.0, 1000)     // standing
        feed(0.1, 300)      // free fall
        feed(3.5, 40)       // impact
        feed(1.0, 2600)     // lying still
        assertNotNull(hit)
        assertEquals(3.5, hit!!, 0.01)
    }

    @Test
    fun fallDetectorIgnoresArmSwingAndGettingUp() {
        val d = FallDetector()
        var t = 0L
        var hit: Double? = null
        fun feed(g: Double, ms: Long) { val end = t + ms; while (t < end) { d.onSample(t, 0.0, 0.0, g * 9.80665)?.let { hit = it }; t += 20 } }
        repeat(20) { feed(1.8, 100); feed(0.6, 100) }    // brisk walking
        feed(0.1, 300); feed(3.0, 40); feed(1.0, 500)
        feed(1.9, 400); feed(0.5, 400)                    // moved again straight away
        feed(1.0, 3000)
        assertNull(hit)
    }

    @Test
    fun nextDoseRollsToTomorrow() {
        val n = MedicationSchedule.next(MedicationSchedule.DEMO, LocalDateTime.of(2026, 10, 19, 21, 0))!!
        assertEquals("metoprolol", n.med.generic)
        assertEquals(LocalDateTime.of(2026, 10, 20, 8, 0), n.dueAt)
    }
}
