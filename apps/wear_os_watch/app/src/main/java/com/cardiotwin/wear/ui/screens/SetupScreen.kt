package com.cardiotwin.wear.ui.screens

import android.app.RemoteInput
import android.content.Intent
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.sp
import androidx.wear.compose.foundation.lazy.ScalingLazyColumn
import androidx.wear.compose.foundation.lazy.rememberScalingLazyListState
import androidx.wear.compose.material.Chip
import androidx.wear.compose.material.ChipDefaults
import androidx.wear.compose.material.Text
import androidx.wear.input.RemoteInputIntentHelper
import com.cardiotwin.wear.UiState
import com.cardiotwin.wear.WatchRepository
import com.cardiotwin.wear.data.LinkMode
import com.cardiotwin.wear.data.SensorMode
import com.cardiotwin.wear.ui.Eyebrow
import com.cardiotwin.wear.ui.theme.CT
import kotlinx.coroutines.launch

/** Setup: modes, server, pairing, and the demo buttons (simulate a fall or an AF episode). */
@Composable
fun SetupScreen(repo: WatchRepository, state: UiState, onModeChanged: () -> Unit) {
    val s = repo.settings
    val scope = rememberCoroutineScope()
    var message by remember { mutableStateOf("") }
    var refresh by remember { mutableIntStateOf(0) }       // re-read settings after edits
    var editing by remember { mutableStateOf("") }

    val input = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) { r ->
        val text = r.data?.let { RemoteInput.getResultsFromIntent(it)?.getCharSequence(KEY)?.toString() }?.trim()
        if (!text.isNullOrEmpty()) when (editing) {
            "server" -> { s.serverUrl = text; repo.restart() }
            "patient" -> s.patientId = text
            "code" -> scope.launch { message = repo.pairWithCode(text).fold({ it }, { "Pairing failed: ${it.message}" }) }
        }
        refresh++
    }
    fun ask(what: String, label: String) {
        editing = what
        val intent: Intent = RemoteInputIntentHelper.createActionRemoteInputIntent()
        RemoteInputIntentHelper.putRemoteInputsExtra(intent, listOf(RemoteInput.Builder(KEY).setLabel(label).build()))
        input.launch(intent)
    }

    val list = rememberScalingLazyListState()
    key(refresh) {       // settings live in SharedPreferences: redraw after each change
    ScalingLazyColumn(state = list, modifier = Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally) {
        item { Text("Setup", color = CT.Ink, fontWeight = FontWeight.SemiBold, fontSize = 14.sp) }
        item {
            SetupChip("Readings from", s.sensorMode.label) {
                s.sensorMode = SensorMode.entries[(s.sensorMode.ordinal + 1) % SensorMode.entries.size]
                onModeChanged(); repo.restart(); refresh++
            }
        }
        item {
            SetupChip("Send to", s.linkMode.label) {
                s.linkMode = LinkMode.entries[(s.linkMode.ordinal + 1) % LinkMode.entries.size]
                repo.restart(); refresh++
            }
        }
        if (s.linkMode == LinkMode.WIFI) {
            item { SetupChip("Server", s.serverUrl) { ask("server", "Server, e.g. http://192.168.1.20:8000") } }
            item {
                val paired = s.deviceToken?.let { "${s.pairedPatientId} (${s.pairedSource})" } ?: "not paired"
                SetupChip("Paired", repo.pairingProblem() ?: paired, onClick = null)
            }
            item { SetupChip("Pair with code", "8 characters from the app") { ask("code", "Pairing code") } }
            item {
                SetupChip("Pair with demo login", "${s.patientId} as dr.demo") {
                    message = "Pairing…"
                    scope.launch { message = repo.pairWithDemoLogin().fold({ it }, { "Pairing failed: ${it.message}" }) }
                }
            }
            item { SetupChip("Demo patient", s.patientId) { ask("patient", "Patient id, e.g. stay-37093652") } }
            item {
                SetupChip("Test server", "GET /api/health") {
                    message = "Testing…"
                    scope.launch { message = repo.testServer().fold({ it }, { "Cannot reach server: ${it.message}" }) }
                }
            }
            item { SetupChip("Unpair", "Forget the device token") { s.clearPairing(); repo.restart(); refresh++ } }
        }
        if (message.isNotEmpty()) item { Text(message, color = CT.Accent, fontSize = 11.sp, textAlign = TextAlign.Center) }
        item { Eyebrow("Demo", modifier = Modifier) }
        item { SetupChip("Simulate fall", "Starts the fall check") { repo.simulateFall() } }
        item { SetupChip("Simulate AF", "60 min of rapid AF readings") { repo.simulateAf() } }
        item { SetupChip("Restart", state.connection.state.name.lowercase()) { repo.restart() } }
        item { Text(state.sourceDescription, color = CT.Ink3, fontSize = 10.sp, textAlign = TextAlign.Center) }
        item { Eyebrow("Log") }
        state.log.take(12).forEach { line -> item { Text(line, color = CT.Ink2, fontSize = 9.sp) } }
    }
    }
}

@Composable
private fun SetupChip(label: String, value: String, onClick: (() -> Unit)?) {
    Chip(
        onClick = { onClick?.invoke() },
        enabled = onClick != null,
        label = { Text(label) },
        secondaryLabel = { Text(value, maxLines = 2, color = CT.Ink2) },
        colors = ChipDefaults.chipColors(backgroundColor = CT.Surface, contentColor = CT.Ink),
        modifier = Modifier.fillMaxWidth(),
    )
}

private const val KEY = "value"
