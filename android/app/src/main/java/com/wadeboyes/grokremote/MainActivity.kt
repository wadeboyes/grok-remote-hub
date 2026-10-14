package com.wadeboyes.grokremote

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.toArgb
import com.wadeboyes.grokremote.ui.GrokApp
import com.wadeboyes.grokremote.ui.GrokTheme
import kotlinx.coroutines.flow.MutableStateFlow

class MainActivity : ComponentActivity() {
    val openChat = MutableStateFlow<OpenChat?>(null)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val bar = Color(0xFF0A0C10).toArgb()
        enableEdgeToEdge(
            statusBarStyle = SystemBarStyle.dark(bar),
            navigationBarStyle = SystemBarStyle.dark(bar),
        )
        openChat.value = readOpenChat(intent)
        val store = SettingsStore(this)
        val client = HubClient()
        setContent {
            GrokTheme {
                GrokApp(store = store, client = client, openChat = openChat)
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        val open = readOpenChat(intent)
        if (open != null) {
            openChat.value = open
        }
    }

    private fun readOpenChat(intent: Intent?): OpenChat? {
        val id = intent?.getStringExtra(TurnAlerts.EXTRA_SESSION)?.trim().orEmpty()
        if (id.isEmpty()) return null
        return OpenChat(
            sessionId = id,
            chatPath = intent?.getStringExtra(TurnAlerts.EXTRA_PATH).orEmpty(),
            title = intent?.getStringExtra(TurnAlerts.EXTRA_TITLE).orEmpty(),
            stamp = System.nanoTime(),
        )
    }
}
