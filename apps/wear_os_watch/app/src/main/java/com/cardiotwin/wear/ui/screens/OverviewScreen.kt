package com.cardiotwin.wear.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.wear.compose.foundation.lazy.ScalingLazyColumn
import androidx.wear.compose.foundation.lazy.rememberScalingLazyListState
import androidx.wear.compose.material.Chip
import androidx.wear.compose.material.ChipDefaults
import androidx.wear.compose.material.Text
import com.cardiotwin.wear.UiState
import com.cardiotwin.wear.net.LinkState
import com.cardiotwin.wear.ui.Eyebrow
import com.cardiotwin.wear.ui.SimulatedBadge
import com.cardiotwin.wear.ui.ago
import com.cardiotwin.wear.ui.fmt
import com.cardiotwin.wear.ui.theme.CT

/** Screen 1: the latest readings this watch sent, and where they are going. */
@Composable
fun OverviewScreen(state: UiState, onOpenStatus: () -> Unit, onOpenSetup: () -> Unit) {
    val list = rememberScalingLazyListState()
    val hr = state.vitals["hr"]
    val simulated = state.vitals.values.any { it.simulated } || state.simulated
    ScalingLazyColumn(state = list, modifier = Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally) {
        item {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("CardioTwin", color = CT.Ink, fontWeight = FontWeight.SemiBold, fontSize = 14.sp)
                if (simulated) { Spacer(Modifier.width(6.dp)); SimulatedBadge() }
            }
        }
        item {
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Row(verticalAlignment = Alignment.Bottom) {
                    Text("♥ ", color = CT.Emg, fontSize = 22.sp)
                    Text(fmt(hr?.value), color = CT.Ink, fontSize = 40.sp, fontWeight = FontWeight.Bold)
                    Text(" bpm", color = CT.Ink2, fontSize = 12.sp, modifier = Modifier.padding(bottom = 8.dp))
                }
                Text("Heart rate · ${ago(hr?.at)}", color = CT.Ink3, fontSize = 11.sp)
            }
        }
        item {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceEvenly) {
                Metric("SpO2", fmt(state.vitals["spo2"]?.value), "%")
                Metric("Breathing", fmt(state.vitals["resp"]?.value), "/min")
                Metric("HRV", fmt(state.vitals["hrv_rmssd"]?.value), "ms")
            }
        }
        item {
            val tierColor = CT.level(state.watch.tier)
            Chip(
                onClick = onOpenStatus,
                label = { Text(state.watch.title.ifEmpty { "Status" }, color = tierColor, fontWeight = FontWeight.SemiBold) },
                secondaryLabel = { Text(state.watch.text, color = CT.Ink2, maxLines = 1) },
                icon = { Text(state.watch.iconGlyph ?: "•", color = tierColor, fontSize = 18.sp) },
                colors = ChipDefaults.chipColors(backgroundColor = CT.Surface),
                modifier = Modifier.fillMaxWidth(),
            )
        }
        item {
            val c = state.connection
            val dot = when (c.state) {
                LinkState.CONNECTED -> CT.Low
                LinkState.CONNECTING, LinkState.RETRYING -> CT.Mod
                LinkState.NOT_PAIRED -> CT.High
                LinkState.STOPPED -> CT.Ink3
            }
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Text("● ${state.linkMode.label}", color = dot, fontSize = 11.sp)
                Text("${state.sensorMode.label} · ${state.sentCount} readings sent", color = CT.Ink3, fontSize = 10.sp, textAlign = TextAlign.Center)
            }
        }
        item {
            Chip(onClick = onOpenSetup, label = { Text("Setup and demo") }, colors = ChipDefaults.secondaryChipColors(),
                modifier = Modifier.fillMaxWidth())
        }
        item { Spacer(Modifier.height(8.dp)) }
    }
}

@Composable
private fun Metric(label: String, value: String, unit: String, color: Color = CT.Ink) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Eyebrow(label)
        Row(verticalAlignment = Alignment.Bottom) {
            Text(value, color = color, fontSize = 18.sp, fontWeight = FontWeight.SemiBold)
            Text(unit, color = CT.Ink3, fontSize = 9.sp, modifier = Modifier.padding(bottom = 3.dp))
        }
    }
}
