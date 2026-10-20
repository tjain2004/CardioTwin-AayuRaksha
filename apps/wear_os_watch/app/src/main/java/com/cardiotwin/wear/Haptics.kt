package com.cardiotwin.wear

import android.content.Context
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager

/** The haptic patterns the risk engine names: "double" (review) and "long repeating" (fall check, emergency). */
class Haptics(context: Context) {
    private val vibrator: Vibrator = context.getSystemService(VibratorManager::class.java).defaultVibrator

    fun play(kind: String?) {
        when {
            kind == null -> Unit
            kind.startsWith("long") -> vibrator.vibrate(VibrationEffect.createWaveform(longArrayOf(0, 700, 300, 700, 300, 700, 300, 700), -1))
            kind == "double" -> vibrator.vibrate(VibrationEffect.createWaveform(longArrayOf(0, 120, 120, 120), -1))
            else -> vibrator.vibrate(VibrationEffect.createOneShot(150, VibrationEffect.DEFAULT_AMPLITUDE))
        }
    }

    fun tick() = vibrator.vibrate(VibrationEffect.createPredefined(VibrationEffect.EFFECT_TICK))
    fun cancel() = vibrator.cancel()
}
