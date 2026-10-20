package com.cardiotwin.wear.net

import com.cardiotwin.wear.data.Reading
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow

enum class LinkState { STOPPED, CONNECTING, CONNECTED, RETRYING, NOT_PAIRED }

data class Connection(val state: LinkState, val detail: String = "")

/**
 * The route between the watch and the backend. Three implementations:
 *  [WifiLink]        WebSocket straight to the FastAPI backend (the demo default)
 *  [PhoneRelayLink]  Wear Data Layer (Bluetooth) to a phone app that relays to the backend (stub, see its notes)
 *  [OfflineMockLink] no backend: replays saved backend replies so the screens can be shown anywhere
 */
interface BackendLink {
    val connection: StateFlow<Connection>
    val events: SharedFlow<LinkEvent>
    fun start(scope: CoroutineScope)
    fun stop()
    fun sendReadings(readings: List<Reading>)
    /** answer is "im_ok" or "need_help" (backend safety.fall_response). */
    fun sendFallResponse(alertId: String, answer: String)
}
