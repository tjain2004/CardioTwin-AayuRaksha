package com.cardiotwin.wear.ui

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.wear.compose.material.Text
import com.cardiotwin.wear.ui.theme.CT
import java.time.Duration
import java.time.Instant

/** The amber SIMULATED tag used across the prototype for anything that is not real watch data. */
@Composable
fun SimulatedBadge(modifier: Modifier = Modifier) {
    Text(
        "SIMULATED", modifier = modifier.background(CT.Mod, RoundedCornerShape(4.dp)).padding(horizontal = 5.dp, vertical = 1.dp),
        color = CT.Bg, fontSize = 9.sp, fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Bold,
    )
}

/** Small uppercase monospace label, as in the prototype ("NEXT 24 HOURS"). */
@Composable
fun Eyebrow(text: String, color: Color = CT.Ink3, modifier: Modifier = Modifier) {
    Text(text.uppercase(), modifier = modifier, color = color, fontSize = 10.sp, fontFamily = FontFamily.Monospace, letterSpacing = 1.sp)
}

/** A coloured ring around the round screen, like the prototype's watch face. */
@Composable
fun RingFrame(color: Color, modifier: Modifier = Modifier, width: Float = 14f, content: @Composable BoxScope.() -> Unit) {
    Box(modifier.fillMaxSize().background(CT.Bg), contentAlignment = Alignment.Center) {
        Canvas(Modifier.fillMaxSize().padding(4.dp)) {
            drawCircle(color = color, radius = size.minDimension / 2 - width / 2, style = Stroke(width = width))
        }
        content()
    }
}

fun ago(t: Instant?, now: Instant = Instant.now()): String {
    if (t == null) return "never"
    val s = Duration.between(t, now).seconds
    return when {
        s < 0 -> "sim time"           // mock replay runs ahead of the real clock
        s < 60 -> "just now"
        s < 3600 -> "${s / 60} min ago"
        else -> "${s / 3600} h ago"
    }
}

fun fmt(v: Any?, digits: Int = 0): String = when (v) {
    null -> "--"
    is Number -> if (digits == 0) Math.round(v.toDouble()).toString() else String.format("%.${digits}f", v.toDouble())
    else -> v.toString()
}
