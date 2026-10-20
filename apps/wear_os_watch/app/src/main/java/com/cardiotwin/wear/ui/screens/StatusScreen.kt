package com.cardiotwin.wear.ui.screens

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.wear.compose.material.Text
import com.cardiotwin.wear.UiState
import com.cardiotwin.wear.ui.Eyebrow
import com.cardiotwin.wear.ui.RingFrame
import com.cardiotwin.wear.ui.SimulatedBadge
import com.cardiotwin.wear.ui.theme.CT

/**
 * Screen 2: the digital twin's status, exactly as the backend's risk engine words it for the watch (views.watch):
 * "No alert / Keep wearing", "Check phone / Readings changed", "High risk / See phone now", "Re-measure / Sit still 2 min",
 * "No update / Check watch fit". The patient never sees a score number, only the tier colour and words.
 */
@Composable
fun StatusScreen(state: UiState) {
    val w = state.watch
    val color = CT.level(w.tier)
    RingFrame(color) {
        Column(horizontalAlignment = Alignment.CenterHorizontally, modifier = Modifier.padding(horizontal = 24.dp)) {
            Eyebrow("Next 24 hours", color = CT.Ink3)
            Spacer(Modifier.height(4.dp))
            Text(w.iconGlyph ?: "•", color = if (w.tier == null) CT.Ink2 else color, fontSize = 26.sp)
            Text(w.title, color = CT.Ink, fontSize = 20.sp, fontWeight = FontWeight.Bold, textAlign = TextAlign.Center)
            Text(w.text, color = CT.Ink2, fontSize = 13.sp, textAlign = TextAlign.Center)
            Spacer(Modifier.height(6.dp))
            val sync = w.lastSyncMinutes?.let { "Last sync ${Math.round(it)} min ago" } ?: state.status?.let { "Status $it" } ?: "Waiting for the server"
            Text(sync, color = CT.Ink3, fontSize = 10.sp, textAlign = TextAlign.Center)
            if (state.simulated) { Spacer(Modifier.height(4.dp)); SimulatedBadge() }
        }
    }
}
