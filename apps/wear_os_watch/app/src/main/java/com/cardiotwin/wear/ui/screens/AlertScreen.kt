package com.cardiotwin.wear.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.wear.compose.foundation.lazy.ScalingLazyColumn
import androidx.wear.compose.foundation.lazy.items
import androidx.wear.compose.foundation.lazy.rememberScalingLazyListState
import androidx.wear.compose.material.Button
import androidx.wear.compose.material.ButtonDefaults
import androidx.wear.compose.material.Card
import androidx.wear.compose.material.CardDefaults
import androidx.wear.compose.material.CompactChip
import androidx.wear.compose.material.ChipDefaults
import androidx.wear.compose.material.Text
import androidx.compose.ui.graphics.painter.ColorPainter
import com.cardiotwin.wear.UiState
import com.cardiotwin.wear.data.WatchView
import com.cardiotwin.wear.ui.Eyebrow
import com.cardiotwin.wear.ui.SimulatedBadge
import com.cardiotwin.wear.ui.theme.CT

/** Screen 3: twin alerts from the backend (possible AF, long QTc, dose over limit, risk alerts, fall results). */
@Composable
fun AlertScreen(state: UiState, onSeen: (String) -> Unit) {
    val list = rememberScalingLazyListState()
    ScalingLazyColumn(state = list, modifier = Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally) {
        item { Text("Twin alerts", color = CT.Ink, fontWeight = FontWeight.SemiBold, fontSize = 14.sp) }
        if (state.alerts.isEmpty()) {
            item {
                Column(horizontalAlignment = Alignment.CenterHorizontally, modifier = Modifier.padding(top = 12.dp)) {
                    Text("✓", color = CT.Low, fontSize = 24.sp)
                    Text("No open alerts", color = CT.Ink2, fontSize = 13.sp)
                }
            }
        }
        items(state.alerts, key = { it.alertId }) { a ->
            val color = CT.level(a.severity)
            Card(
                onClick = { onSeen(a.alertId) },
                backgroundPainter = ColorPainter(CT.Surface),
                modifier = Modifier.fillMaxWidth(),
            ) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Box(Modifier.width(4.dp).height(28.dp).background(color))
                    Spacer(Modifier.width(6.dp))
                    Column {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Eyebrow(a.severity, color = color)
                            if (a.simulated) { Spacer(Modifier.width(4.dp)); SimulatedBadge() }
                        }
                        Text(a.watch?.title?.takeIf { it.isNotEmpty() } ?: a.title, color = CT.Ink, fontWeight = FontWeight.SemiBold, fontSize = 14.sp)
                        Text(a.watch?.text?.takeIf { it.isNotEmpty() } ?: a.message, color = CT.Ink2, fontSize = 12.sp,
                            maxLines = 3, overflow = TextOverflow.Ellipsis)
                    }
                }
            }
        }
        if (state.alerts.isNotEmpty()) item { Text("Tap an alert to hide it. Details are on the phone.", color = CT.Ink3, fontSize = 10.sp, textAlign = TextAlign.Center) }
    }
}

/** Full-screen emergency (backend EMERGENCY_WATCH): an unanswered fall, "I need help", or an engine emergency rule. */
@Composable
fun EmergencyScreen(view: WatchView, simulated: Boolean, onAction: () -> Unit, onHide: () -> Unit) {
    Box(Modifier.fillMaxSize().background(CT.Emg), contentAlignment = Alignment.Center) {
        Column(horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Center,
            modifier = Modifier.padding(horizontal = 20.dp)) {
            if (simulated) SimulatedBadge()
            Text(view.iconGlyph ?: "⚠", color = CT.OnEmg, fontSize = 28.sp)
            Text(view.title.ifEmpty { "EMERGENCY" }, color = CT.OnEmg, fontSize = 20.sp, fontWeight = FontWeight.Black)
            Text(view.text, color = CT.OnEmg, fontSize = 13.sp, textAlign = TextAlign.Center)
            Spacer(Modifier.height(8.dp))
            Button(onClick = onAction, colors = ButtonDefaults.buttonColors(backgroundColor = CT.OnEmg, contentColor = CT.Emg),
                modifier = Modifier.fillMaxWidth(0.8f).height(44.dp)) {
                Text(view.actionLabel ?: "Open phone", fontWeight = FontWeight.Bold)
            }
            Spacer(Modifier.height(4.dp))
            CompactChip(onClick = onHide, label = { Text("Hide", color = CT.OnEmg) },
                colors = ChipDefaults.chipColors(backgroundColor = CT.Emg.copy(alpha = 0.6f)))
            Text("Nothing is dispatched automatically.", color = CT.OnEmg.copy(alpha = 0.8f), fontSize = 9.sp, textAlign = TextAlign.Center)
        }
    }
}
