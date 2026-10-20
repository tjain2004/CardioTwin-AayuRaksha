package com.cardiotwin.wear.ui.screens

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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.wear.compose.foundation.lazy.ScalingLazyColumn
import androidx.wear.compose.foundation.lazy.rememberScalingLazyListState
import androidx.wear.compose.material.Button
import androidx.wear.compose.material.ButtonDefaults
import androidx.wear.compose.material.Chip
import androidx.wear.compose.material.ChipDefaults
import androidx.wear.compose.material.CompactChip
import androidx.wear.compose.material.Text
import com.cardiotwin.wear.UiState
import com.cardiotwin.wear.ui.Eyebrow
import com.cardiotwin.wear.ui.theme.CT
import java.time.format.DateTimeFormatter

private val HM = DateTimeFormatter.ofPattern("HH:mm")

/**
 * Screen 4: medication reminder. "Taken" sends a dose_taken reading (mg, meta.drug) that the backend checks against
 * the clinic's daily limit. The watch only reminds; it never suggests or changes a dose.
 */
@Composable
fun MedicationScreen(state: UiState, onTaken: () -> Unit, onSnooze: () -> Unit, onSkip: () -> Unit, onDemo: () -> Unit) {
    val list = rememberScalingLazyListState()
    val due = state.dueDose
    ScalingLazyColumn(state = list, modifier = Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally) {
        if (due != null) {
            item { Eyebrow("Time for your medicine", color = CT.Accent) }
            item {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text("💊", fontSize = 26.sp)
                    Text(due.med.label, color = CT.Ink, fontSize = 18.sp, fontWeight = FontWeight.Bold, textAlign = TextAlign.Center)
                    Text("Due ${due.dueAt.format(HM)}", color = CT.Ink2, fontSize = 12.sp)
                }
            }
            item {
                Button(onClick = onTaken, colors = ButtonDefaults.buttonColors(backgroundColor = CT.Low, contentColor = CT.Bg),
                    modifier = Modifier.fillMaxWidth(0.75f).height(44.dp)) { Text("Taken", fontWeight = FontWeight.Bold) }
            }
            item {
                Row {
                    CompactChip(onClick = onSnooze, label = { Text("Snooze 10 min") }, colors = ChipDefaults.secondaryChipColors())
                    Spacer(Modifier.width(4.dp))
                    CompactChip(onClick = onSkip, label = { Text("Skip") }, colors = ChipDefaults.secondaryChipColors())
                }
            }
        } else {
            item { Text("Medicines", color = CT.Ink, fontWeight = FontWeight.SemiBold, fontSize = 14.sp) }
            item {
                val n = state.nextDose
                Column(horizontalAlignment = Alignment.CenterHorizontally, modifier = Modifier.padding(vertical = 6.dp)) {
                    Eyebrow("Next dose")
                    Text(n?.dueAt?.format(HM) ?: "--:--", color = CT.Accent, fontSize = 30.sp, fontWeight = FontWeight.Bold)
                    Text(n?.med?.label ?: "Nothing scheduled", color = CT.Ink2, fontSize = 13.sp)
                }
            }
            item {
                Chip(onClick = onDemo, label = { Text("Demo: remind in 1 min") }, colors = ChipDefaults.secondaryChipColors(),
                    modifier = Modifier.fillMaxWidth())
            }
        }
        item { Text("Reminders only. Ask your doctor before changing a dose.", color = CT.Ink3, fontSize = 9.sp, textAlign = TextAlign.Center) }
    }
}
