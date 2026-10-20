package com.cardiotwin.wear.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.wear.compose.material.Button
import androidx.wear.compose.material.ButtonDefaults
import androidx.wear.compose.material.CircularProgressIndicator
import androidx.wear.compose.material.Text
import com.cardiotwin.wear.FallCheck
import com.cardiotwin.wear.ui.theme.CT
import kotlinx.coroutines.delay

/**
 * Screen 5: "Fall detected. Are you OK?" with the backend's countdown (30 s by default). "I'm OK" closes it;
 * "I need help", or no answer before the ring runs out, makes the backend raise the emergency screen.
 */
@Composable
fun FallScreen(check: FallCheck, onOk: () -> Unit, onHelp: () -> Unit) {
    var now by remember { mutableLongStateOf(System.currentTimeMillis()) }
    LaunchedEffect(check) {
        while (true) { now = System.currentTimeMillis(); delay(200) }
    }
    val leftMs = (check.deadlineMillis - now).coerceAtLeast(0)
    val progress = leftMs / (check.totalSeconds * 1000f)
    Box(Modifier.fillMaxSize().background(CT.Bg), contentAlignment = Alignment.Center) {
        CircularProgressIndicator(
            progress = progress, modifier = Modifier.fillMaxSize().padding(3.dp),
            startAngle = 270f, endAngle = 270f + 360f * 0.999f,
            indicatorColor = if (leftMs > 10_000) CT.High else CT.Emg, trackColor = CT.Sunk, strokeWidth = 7.dp,
        )
        Column(horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Center,
            modifier = Modifier.padding(horizontal = 22.dp)) {
            if (check.answer == "need_help") {
                Text("⚠", color = CT.Emg, fontSize = 26.sp)
                Text("Help requested", color = CT.Ink, fontSize = 18.sp, fontWeight = FontWeight.Bold)
                Text("Alerting your care team…", color = CT.Ink2, fontSize = 12.sp, textAlign = TextAlign.Center)
                return@Column
            }
            Text("Fall detected", color = CT.Ink, fontSize = 17.sp, fontWeight = FontWeight.Bold)
            Text("Are you OK?", color = CT.Ink2, fontSize = 13.sp)
            Text(if (leftMs > 0) "${(leftMs + 999) / 1000}" else "…", color = if (leftMs > 10_000) CT.High else CT.Emg,
                fontSize = 30.sp, fontWeight = FontWeight.Black)
            Row(horizontalArrangement = Arrangement.Center, verticalAlignment = Alignment.CenterVertically) {
                Button(onClick = onOk, colors = ButtonDefaults.buttonColors(backgroundColor = CT.Low, contentColor = CT.Bg),
                    modifier = Modifier.size(58.dp)) {
                    Text("I'm\nOK", fontWeight = FontWeight.Bold, fontSize = 13.sp, textAlign = TextAlign.Center, lineHeight = 14.sp)
                }
                Spacer(Modifier.width(10.dp))
                Button(onClick = onHelp, colors = ButtonDefaults.buttonColors(backgroundColor = CT.Emg, contentColor = CT.OnEmg),
                    modifier = Modifier.size(58.dp)) {
                    Text("Need\nhelp", fontWeight = FontWeight.Bold, fontSize = 12.sp, textAlign = TextAlign.Center, lineHeight = 13.sp)
                }
            }
            Spacer(Modifier.height(4.dp))
            Text(if (check.alertId == null) "Contacting server…" else "No answer = emergency alert", color = CT.Ink3, fontSize = 9.sp)
        }
    }
}
