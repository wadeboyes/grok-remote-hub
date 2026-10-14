package com.wadeboyes.grokremote

import android.content.Context

data class HubSettings(
    val hubUrl: String,
    val token: String,
    val notificationsEnabled: Boolean = true,
)

class SettingsStore(context: Context) {
    private val prefs = context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    fun read(): HubSettings {
        val url = prefs.getString(KEY_URL, null)?.trim().orEmpty().ifEmpty { DEFAULT_HUB_URL }
        val token = prefs.getString(KEY_TOKEN, null).orEmpty()
        return HubSettings(
            hubUrl = url,
            token = token,
            notificationsEnabled = notificationsEnabled(),
        )
    }

    fun write(settings: HubSettings) {
        val url = settings.hubUrl.trim().trimEnd('/')
        prefs.edit()
            .putString(KEY_URL, url.ifEmpty { DEFAULT_HUB_URL })
            .putString(KEY_TOKEN, settings.token.trim())
            .putBoolean(KEY_NOTIFY, settings.notificationsEnabled)
            .apply()
    }

    fun notificationsEnabled(): Boolean = prefs.getBoolean(KEY_NOTIFY, true)

    fun setNotificationsEnabled(enabled: Boolean) {
        prefs.edit().putBoolean(KEY_NOTIFY, enabled).apply()
    }

    fun notificationAsked(): Boolean = prefs.getBoolean(KEY_ASKED, false)

    fun setNotificationAsked() {
        prefs.edit().putBoolean(KEY_ASKED, true).apply()
    }

    fun openSessionId(): String = prefs.getString(KEY_OPEN, null)?.trim().orEmpty()

    fun setOpenSession(sessionId: String?) {
        prefs.edit().putString(KEY_OPEN, sessionId?.trim().orEmpty()).apply()
    }

    fun hasNotifyCursor(): Boolean = prefs.getBoolean(KEY_CURSOR_SET, false)

    fun notifyCursor(): Long = prefs.getLong(KEY_CURSOR, 0L)

    fun setNotifyCursor(value: Long) {
        prefs.edit()
            .putLong(KEY_CURSOR, value)
            .putBoolean(KEY_CURSOR_SET, true)
            .commit()
    }

    companion object {
        const val DEFAULT_HUB_URL = "http://100.113.227.9:8787"
        private const val PREFS = "grok_remote_settings"
        private const val KEY_URL = "hub_url"
        private const val KEY_TOKEN = "hub_token"
        private const val KEY_NOTIFY = "notify_enabled"
        private const val KEY_ASKED = "notify_asked"
        private const val KEY_OPEN = "open_session_id"
        private const val KEY_CURSOR = "notify_cursor"
        private const val KEY_CURSOR_SET = "notify_cursor_set"
    }
}
