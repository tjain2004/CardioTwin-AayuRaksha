package com.cardiotwin.wear.net

import android.util.Log
import com.cardiotwin.wear.data.Reading
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONArray
import org.json.JSONObject
import java.util.ArrayDeque

/**
 * WS {server}/ws/device?token={device token}: sends {"type":"readings","readings":[...]} and
 * {"type":"fall_response","alert_id":..,"answer":..}; receives hello, status, alert, emergency, ack and error.
 * Reconnects with backoff and keeps up to [MAX_QUEUE] readings while offline (the backend ignores duplicates).
 */
class WifiLink(
    private val server: String,
    private val deviceToken: String,
    private val http: OkHttpClient = BackendApi.defaultClient,
) : BackendLink {

    private val _connection = MutableStateFlow(Connection(LinkState.STOPPED))
    override val connection = _connection.asStateFlow()
    private val _events = MutableSharedFlow<LinkEvent>(extraBufferCapacity = 64)
    override val events = _events.asSharedFlow()

    private val queue = ArrayDeque<JSONObject>()
    private val lock = Any()
    @Volatile private var socket: WebSocket? = null
    @Volatile private var running = false
    private var job: Job? = null

    override fun start(scope: CoroutineScope) {
        running = true
        job = scope.launch {
            var backoff = 1_000L
            while (running) {
                _connection.value = Connection(LinkState.CONNECTING, server)
                val closed = kotlinx.coroutines.CompletableDeferred<String>()
                val url = server.trimEnd('/').replaceFirst("http", "ws") + "/ws/device?token=$deviceToken"
                socket = http.newWebSocket(Request.Builder().url(url).build(), object : WebSocketListener() {
                    override fun onOpen(webSocket: WebSocket, response: Response) {
                        backoff = 1_000L
                        _connection.value = Connection(LinkState.CONNECTED, server)
                        flush()
                    }
                    override fun onMessage(webSocket: WebSocket, text: String) {
                        ServerMessages.parse(text)?.let { _events.tryEmit(it) }
                    }
                    override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                        webSocket.close(1000, null)
                        if (code == 4401) {          // token revoked or unknown: the watch has to pair again
                            running = false
                            _connection.value = Connection(LinkState.NOT_PAIRED, reason)
                        }
                        closed.complete("closed $code $reason")
                    }
                    override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                        closed.complete(t.message ?: t.javaClass.simpleName)
                    }
                })
                val why = closed.await()
                socket = null
                if (!running) break
                Log.w(TAG, "WebSocket down: $why; retrying in ${backoff / 1000}s")
                _connection.value = Connection(LinkState.RETRYING, why)
                delay(backoff)
                backoff = (backoff * 2).coerceAtMost(30_000L)
            }
            if (_connection.value.state != LinkState.NOT_PAIRED) _connection.value = Connection(LinkState.STOPPED)
        }
    }

    override fun stop() {
        running = false
        socket?.close(1000, "stopped")
        job?.cancel()
        _connection.value = Connection(LinkState.STOPPED)
    }

    override fun sendReadings(readings: List<Reading>) {
        if (readings.isEmpty()) return
        synchronized(lock) {
            readings.forEach { queue.addLast(it.toJson()) }
            while (queue.size > MAX_QUEUE) queue.removeFirst()
        }
        flush()
    }

    override fun sendFallResponse(alertId: String, answer: String) {
        val msg = JSONObject().put("type", "fall_response").put("alert_id", alertId).put("answer", answer)
        if (socket?.send(msg.toString()) != true) _events.tryEmit(LinkEvent.Error("Not connected: answer not sent"))
    }

    private fun flush() {
        val ws = socket ?: return
        if (_connection.value.state != LinkState.CONNECTED) return
        synchronized(lock) {
            while (queue.isNotEmpty()) {
                val batch = JSONArray()
                while (queue.isNotEmpty() && batch.length() < BATCH) batch.put(queue.removeFirst())
                val msg = JSONObject().put("type", "readings").put("readings", batch)
                if (!ws.send(msg.toString())) {                       // socket closing: put the batch back
                    for (i in batch.length() - 1 downTo 0) queue.addFirst(batch.getJSONObject(i))
                    return
                }
            }
        }
    }

    companion object {
        private const val TAG = "WifiLink"
        private const val BATCH = 500           // the backend's virtual watch sends backfill in batches of 500
        private const val MAX_QUEUE = 20_000
    }
}
