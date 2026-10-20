package com.cardiotwin.wear.net

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

/** The few HTTP calls the watch makes. Everything else goes over the device WebSocket. */
class BackendApi(private val http: OkHttpClient = defaultClient) {

    data class Paired(val deviceId: String, val deviceToken: String, val patientId: String, val source: String)

    /** POST /api/devices/pair with the 8-character code shown on the phone app / QR (works once, 10 minutes). */
    fun pair(server: String, code: String, source: String, name: String): Paired {
        val body = JSONObject().put("code", code.trim().uppercase()).put("kind", "watch").put("source", source).put("name", name)
        val o = post(server, "/api/devices/pair", body, null)
        return Paired(o.getString("device_id"), o.getString("device_token"), o.getString("patient_id"), o.getString("source"))
    }

    /**
     * DEMO ONLY: what the backend's virtual watch does (simulator/watch_sim.py pair_with_server): sign in as a demo user,
     * ask for a pairing code for the patient, then pair. Lets the emulator pair without typing a code.
     */
    fun pairWithDemoLogin(server: String, user: String, password: String, patientId: String, source: String): Paired {
        val token = post(server, "/api/auth/login", JSONObject().put("username", user).put("password", password), null)
            .getString("access_token")
        val code = post(server, "/api/patients/$patientId/pairing", JSONObject(), token).getString("pairing_code")
        return pair(server, code, source, if (source == "simulator") "Wear OS watch (SIMULATED replay)" else "Wear OS watch")
    }

    /** GET /api/health: used by Setup to test the server address. */
    fun health(server: String): JSONObject {
        val req = Request.Builder().url(server.trimEnd('/') + "/api/health").get().build()
        http.newCall(req).execute().use { r ->
            val text = r.body?.string().orEmpty()
            if (!r.isSuccessful) throw IOException("HTTP ${r.code}: ${text.take(200)}")
            return JSONObject(text)
        }
    }

    private fun post(server: String, path: String, body: JSONObject, bearer: String?): JSONObject {
        val req = Request.Builder().url(server.trimEnd('/') + path)
            .post(body.toString().toRequestBody(JSON))
            .apply { bearer?.let { header("Authorization", "Bearer $it") } }
            .build()
        http.newCall(req).execute().use { r ->
            val text = r.body?.string().orEmpty()
            if (!r.isSuccessful) {
                val detail = runCatching { JSONObject(text).opt("detail")?.toString() }.getOrNull() ?: text.take(200)
                throw IOException("HTTP ${r.code}: $detail")
            }
            return JSONObject(text)
        }
    }

    companion object {
        private val JSON = "application/json".toMediaType()
        val defaultClient: OkHttpClient = OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            .pingInterval(20, TimeUnit.SECONDS)
            .build()
    }
}
