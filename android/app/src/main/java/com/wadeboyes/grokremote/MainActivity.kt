package com.wadeboyes.grokremote

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.toArgb
import com.wadeboyes.grokremote.ui.GrokApp
import com.wadeboyes.grokremote.ui.GrokTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val bar = Color(0xFF0A0C10).toArgb()
        enableEdgeToEdge(
            statusBarStyle = SystemBarStyle.dark(bar),
            navigationBarStyle = SystemBarStyle.dark(bar),
        )
        val store = SettingsStore(this)
        val client = HubClient()
        setContent {
            GrokTheme {
                GrokApp(store = store, client = client)
            }
        }
    }
}
