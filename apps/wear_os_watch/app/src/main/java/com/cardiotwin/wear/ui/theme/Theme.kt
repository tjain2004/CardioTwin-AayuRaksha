package com.cardiotwin.wear.ui.theme

import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.wear.compose.material.Colors
import androidx.wear.compose.material.MaterialTheme

/** Dark palette of the CardioTwin methodology prototype (methodology/cardiotwin_methodology.html). */
object CT {
    val Bg = Color(0xFF0E1519)
    val Surface = Color(0xFF162026)
    val Sunk = Color(0xFF1C2830)
    val Line = Color(0xFF2A3842)
    val Ink = Color(0xFFE4ECF0)
    val Ink2 = Color(0xFFA7B6BF)
    val Ink3 = Color(0xFF7A8A95)
    val Accent = Color(0xFF55BAC4)
    val AccentSoft = Color(0xFF163A40)
    val Low = Color(0xFF5BC08A)
    val Mod = Color(0xFFE2A746)
    val High = Color(0xFFF2864F)
    val Emg = Color(0xFFE0283F)
    val OnEmg = Color(0xFFFFFFFF)

    /** Alert levels as the risk engine names them (tiers LOW/MODERATE/HIGH, severities low..critical). */
    fun level(tierOrSeverity: String?): Color = when (tierOrSeverity?.uppercase()) {
        "LOW" -> Low
        "MODERATE" -> Mod
        "HIGH" -> High
        "CRITICAL", "EMERGENCY" -> Emg
        else -> Ink3
    }
}

@Composable
fun CardioTwinTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colors = Colors(
            primary = CT.Accent, primaryVariant = CT.AccentSoft, secondary = CT.Mod, secondaryVariant = CT.High,
            background = CT.Bg, surface = CT.Surface, error = CT.Emg,
            onPrimary = CT.Bg, onSecondary = CT.Bg, onBackground = CT.Ink, onSurface = CT.Ink, onSurfaceVariant = CT.Ink2, onError = CT.OnEmg,
        ),
        content = content,
    )
}
