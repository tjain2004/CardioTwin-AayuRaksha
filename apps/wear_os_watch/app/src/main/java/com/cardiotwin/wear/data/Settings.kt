package com.cardiotwin.wear.data

import android.content.Context
import com.cardiotwin.wear.BuildConfig

/** Where readings come from. */
enum class SensorMode(val label: String) {
    MOCK("Mock replay"),          // plays a saved test-patient stream from assets (SIMULATED), works on the emulator
    WATCH("Watch sensors"),       // Health Services heart rate, step counter, accelerometer fall check
}

/** Where readings go and alerts come from. */
enum class LinkMode(val label: String) {
    OFFLINE("Offline demo"),      // no backend: plays saved backend replies from assets
    WIFI("Wi-Fi to backend"),     // default for the demo: WebSocket straight to the FastAPI backend
    PHONE("Via phone (stub)"),    // Wear Data Layer over Bluetooth to a phone app that relays to the backend
}

class Settings(context: Context) {
    private val p = context.getSharedPreferences("cardiotwin", Context.MODE_PRIVATE)

    var serverUrl: String
        get() = p.getString("server_url", BuildConfig.DEFAULT_SERVER_URL)!!
        set(v) = p.edit().putString("server_url", v.trim().trimEnd('/')).apply()
    var patientId: String
        get() = p.getString("patient_id", BuildConfig.DEFAULT_PATIENT_ID)!!
        set(v) = p.edit().putString("patient_id", v.trim()).apply()
    var sensorMode: SensorMode
        get() = SensorMode.valueOf(p.getString("sensor_mode", SensorMode.MOCK.name)!!)
        set(v) = p.edit().putString("sensor_mode", v.name).apply()
    var linkMode: LinkMode
        get() = LinkMode.valueOf(p.getString("link_mode", LinkMode.OFFLINE.name)!!)
        set(v) = p.edit().putString("link_mode", v.name).apply()

    /** Device token from POST /api/devices/pair. Bound to one patient and one source (watch or simulator). */
    var deviceToken: String?
        get() = p.getString("device_token", null)
        set(v) = p.edit().putString("device_token", v).apply()
    var pairedPatientId: String?
        get() = p.getString("paired_patient", null)
        set(v) = p.edit().putString("paired_patient", v).apply()
    var pairedSource: String?
        get() = p.getString("paired_source", null)
        set(v) = p.edit().putString("paired_source", v).apply()
    var pairedServer: String?
        get() = p.getString("paired_server", null)
        set(v) = p.edit().putString("paired_server", v).apply()

    /** Mock replay speed: simulated minutes per real second. The backend's virtual watch default is 6 (1 h per 10 s). */
    var mockSpeed: Double
        get() = p.getFloat("mock_speed", 6f).toDouble()
        set(v) = p.edit().putFloat("mock_speed", v.toFloat()).apply()

    fun clearPairing() {
        p.edit().remove("device_token").remove("paired_patient").remove("paired_source").remove("paired_server").apply()
    }

    /** The source the backend expects from this device: mock replays are labelled SIMULATED. */
    val readingSource: String get() = if (sensorMode == SensorMode.MOCK) Reading.SOURCE_SIMULATOR else Reading.SOURCE_WATCH
}
