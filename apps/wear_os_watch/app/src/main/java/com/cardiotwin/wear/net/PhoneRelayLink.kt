package com.cardiotwin.wear.net

import android.content.Context
import android.util.Log
import com.cardiotwin.wear.data.Reading
import com.google.android.gms.wearable.MessageClient
import com.google.android.gms.wearable.MessageEvent
import com.google.android.gms.wearable.Wearable
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.tasks.await
import org.json.JSONArray
import org.json.JSONObject

/**
 * STUB: the Bluetooth route through the paired phone, using the Wear Data Layer MessageClient.
 *
 * Watch side (here): sends the same JSON the WebSocket carries to every connected phone node on
 *   /cardiotwin/readings       {"type":"readings","readings":[...]}
 *   /cardiotwin/fall_response  {"type":"fall_response","alert_id":..,"answer":..}
 * and treats anything arriving on /cardiotwin/server as a backend message (same shapes as WS /ws/device).
 *
 * Phone side (NOT BUILT YET, part of the phone app): a WearableListenerService that forwards those messages to the
 * backend over its own /ws/device connection (the phone pairs as kind "watch" on the watch's behalf), and sends every
 * backend message back on /cardiotwin/server. Both apps need the same applicationId and signing key for the Data Layer
 * to connect them. Until the phone app exists this link reports "no phone" and readings are dropped.
 */
class PhoneRelayLink(context: Context) : BackendLink {
    private val messages: MessageClient = Wearable.getMessageClient(context)
    private val nodes = Wearable.getNodeClient(context)
    private val _connection = MutableStateFlow(Connection(LinkState.STOPPED))
    override val connection = _connection.asStateFlow()
    private val _events = MutableSharedFlow<LinkEvent>(extraBufferCapacity = 64)
    override val events = _events.asSharedFlow()
    private var scope: CoroutineScope? = null

    private val listener = MessageClient.OnMessageReceivedListener { ev: MessageEvent ->
        if (ev.path == PATH_SERVER) ServerMessages.parse(String(ev.data))?.let { _events.tryEmit(it) }
    }

    override fun start(scope: CoroutineScope) {
        this.scope = scope
        messages.addListener(listener)
        scope.launch {
            _connection.value = Connection(LinkState.CONNECTING, "looking for phone")
            val found = runCatching { nodes.connectedNodes.await() }.getOrDefault(emptyList())
            _connection.value = if (found.isEmpty()) Connection(LinkState.RETRYING, "No phone connected (phone app not built yet)")
            else Connection(LinkState.CONNECTED, "phone: ${found.first().displayName} (relay stub)")
        }
    }

    override fun stop() {
        messages.removeListener(listener)
        _connection.value = Connection(LinkState.STOPPED)
    }

    override fun sendReadings(readings: List<Reading>) {
        val msg = JSONObject().put("type", "readings").put("readings", JSONArray(readings.map { it.toJson() }))
        send(PATH_READINGS, msg)
    }

    override fun sendFallResponse(alertId: String, answer: String) {
        send(PATH_FALL, JSONObject().put("type", "fall_response").put("alert_id", alertId).put("answer", answer))
    }

    private fun send(path: String, msg: JSONObject) {
        scope?.launch {
            val found = runCatching { nodes.connectedNodes.await() }.getOrDefault(emptyList())
            if (found.isEmpty()) {
                Log.w(TAG, "No phone node: dropped $path")
                return@launch
            }
            found.forEach { n -> runCatching { messages.sendMessage(n.id, path, msg.toString().toByteArray()).await() } }
        }
    }

    companion object {
        private const val TAG = "PhoneRelayLink"
        const val PATH_READINGS = "/cardiotwin/readings"
        const val PATH_FALL = "/cardiotwin/fall_response"
        const val PATH_SERVER = "/cardiotwin/server"
    }
}
