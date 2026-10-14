package com.wadeboyes.grokremote

import android.content.Context

data class HubSettings(
    val hubUrl: String,
    val token: String,
)

class SettingsStore(context: Context) {
    private val prefs = context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    fun read(): HubSettings {
        val url = prefs.getString(KEY_URL, null)?.trim().orEmpty().ifEmpty { DEFAULT_HUB_URL }
        val token = prefs.getString(KEY_TOKEN, null).orEmpty()
        return HubSettings(hubUrl = url, token = token)
    }

    fun write(settings: HubSettings) {
        val url = settings.hubUrl.trim().trimEnd('/')
        prefs.edit()
            .putString(KEY_URL, url.ifEmpty { DEFAULT_HUB_URL })
            .putString(KEY_TOKEN, settings.token.trim())
            .apply()
    }

    companion object {
        const val DEFAULT_HUB_URL = "http://100.113.227.9:8787"
        private const val PREFS = "grok_remote_settings"
        private const val KEY_URL = "hub_url"
        private const val KEY_TOKEN = "hub_token"
    }
}
