package com.cardiotwin.wear

import android.Manifest
import android.content.ActivityNotFoundException
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.view.WindowManager
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.cardiotwin.wear.data.LinkMode
import com.cardiotwin.wear.data.SensorMode
import com.cardiotwin.wear.ui.WearApp
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {
    private val repo by lazy { (application as CardioTwinApp).repository }

    private val permissions = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
        startMonitoring()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        applyAdbExtras(intent)
        setContent {
            WearApp(repo, onOpenPhone = ::openPhone, onModeChanged = ::requestPermissionsThenStart)
        }
        requestPermissionsThenStart()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        if (applyAdbExtras(intent)) repo.restart()
    }

    private fun requestPermissionsThenStart() {
        if (repo.settings.sensorMode == SensorMode.MOCK) {
            window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)    // demo: keep the replay visible
            startMonitoring()
            return
        }
        window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        val wanted = buildList {
            add(if (Build.VERSION.SDK_INT >= 36) "android.permission.health.READ_HEART_RATE" else Manifest.permission.BODY_SENSORS)
            add(Manifest.permission.ACTIVITY_RECOGNITION)
            if (Build.VERSION.SDK_INT >= 33) add(Manifest.permission.POST_NOTIFICATIONS)
        }.filter { ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED }
        if (wanted.isEmpty()) startMonitoring() else permissions.launch(wanted.toTypedArray())
    }

    private fun startMonitoring() {
        if (repo.settings.sensorMode == SensorMode.WATCH) MonitorService.start(this)
        repo.start()
    }

    /** "Call 112" opens the dialler with the number; nothing is ever dialled automatically. */
    private fun openPhone(label: String?) {
        val number = label?.filter { it.isDigit() }.orEmpty()
        val intent = Intent(Intent.ACTION_DIAL, if (number.isNotEmpty()) Uri.parse("tel:$number") else null)
        try {
            startActivity(intent)
        } catch (e: ActivityNotFoundException) {
            Toast.makeText(this, if (number.isNotEmpty()) "Call $number on your phone" else "Check your phone", Toast.LENGTH_LONG).show()
        }
    }

    /**
     * Configure from adb, handy on the emulator where typing is slow, e.g.
     *   adb shell am start -n com.cardiotwin.wear/.MainActivity --es server http://10.0.2.2:8000 --es link wifi --es sensors mock --es code AB12CD34
     */
    private fun applyAdbExtras(i: Intent?): Boolean {
        val x = i?.extras ?: return false
        val s = repo.settings
        var changed = false
        x.getString("server")?.let { s.serverUrl = it; changed = true }
        x.getString("patient")?.let { s.patientId = it; changed = true }
        x.getString("link")?.let { v -> LinkMode.entries.firstOrNull { it.name.equals(v, true) }?.let { s.linkMode = it; changed = true } }
        x.getString("sensors")?.let { v -> SensorMode.entries.firstOrNull { it.name.equals(v, true) }?.let { s.sensorMode = it; changed = true } }
        if (x.containsKey("speed")) { s.mockSpeed = x.getString("speed")?.toDoubleOrNull() ?: s.mockSpeed; changed = true }
        x.getString("code")?.let { code ->
            lifecycleScope.launch {
                val r = repo.pairWithCode(code)
                Toast.makeText(this@MainActivity, r.fold({ it }, { "Pairing failed: ${it.message}" }), Toast.LENGTH_LONG).show()
            }
        }
        if (x.getBoolean("demo_pair", false) || x.getString("demo_pair") == "true") {
            lifecycleScope.launch {
                val r = repo.pairWithDemoLogin()
                Toast.makeText(this@MainActivity, r.fold({ it }, { "Pairing failed: ${it.message}" }), Toast.LENGTH_LONG).show()
            }
        }
        return changed
    }
}
