package com.wadeboyes.grokremote

import org.json.JSONObject
import java.io.BufferedReader
import java.io.File
import java.io.InputStreamReader
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import java.nio.charset.StandardCharsets

class HubException(message: String) : Exception(message)

data class BuildSummary(
    val what: String,
    val built: String,
    val progress: String,
    val next: String,
    val text: String,
    val source: String,
    val stale: Boolean,
)

data class BuildItem(
    val kind: String,
    val id: String,
    val title: String,
    val cwd: String,
    val updatedAt: String,
    val sessionId: String,
    val worktreePath: String,
    val repoName: String,
    val status: String,
    val isSubagent: Boolean,
    val actionHint: String,
    val summary: BuildSummary,
) {
    val key: String get() = "$kind:$id"
    val buttonLabel: String get() = if (actionHint == "restart") "Restart" else "Resume"
}

data class UpdateManifest(
    val channel: String,
    val versionCode: Long,
    val versionName: String,
    val apk: String,
    val sha256: String,
    val publishedAt: String,
)

data class ResumeResult(
    val action: String,
    val liveSessionId: String,
    val viewSessionId: String,
    val cwd: String,
    val chatPath: String,
    val title: String,
    val command: List<String>,
)

class HubClient {
    fun fetchIndex(baseUrl: String, token: String): List<BuildItem> {
        val body = request("GET", baseUrl, "/api/build/index", token, null, 20000)
        val items = body.optJSONArray("items") ?: return emptyList()
        val out = ArrayList<BuildItem>(items.length())
        for (i in 0 until items.length()) {
            val row = items.optJSONObject(i) ?: continue
            out.add(parseItem(row))
        }
        return out
    }

    fun scheduleRefresh(baseUrl: String, token: String) {
        request("POST", baseUrl, "/api/build/summaries/refresh", token, JSONObject(), 15000)
    }

    fun resume(baseUrl: String, token: String, item: BuildItem): ResumeResult {
        val payload = JSONObject()
            .put("kind", item.kind)
            .put("id", item.id)
            .put("mode", if (item.actionHint == "restart") "restart" else "resume")
        val body = request("POST", baseUrl, "/api/build/resume", token, payload, 90000)
        val commandJson = body.optJSONArray("command")
        val command = ArrayList<String>()
        if (commandJson != null) {
            for (i in 0 until commandJson.length()) {
                command.add(commandJson.optString(i))
            }
        }
        return ResumeResult(
            action = body.optString("action", "resume"),
            liveSessionId = body.optString("liveSessionId"),
            viewSessionId = body.optString("viewSessionId"),
            cwd = body.optString("cwd"),
            chatPath = body.optString("chatPath"),
            title = body.optString("title"),
            command = command,
        )
    }

    fun fetchUpdateManifest(baseUrl: String, token: String): UpdateManifest? {
        val raw = requestRaw("GET", baseUrl, "/app/update.json?channel=stable", token, null, 20000)
        if (raw.code == 404) return null
        if (raw.code == 401) {
            throw HubException("The hub rejected the token. Open Settings and paste the hub token.")
        }
        if (raw.code !in 200..299) {
            throw HubException("Could not reach the hub for updates.")
        }
        val body = JSONObject(raw.text)
        val apk = body.optString("apk")
        val sha = body.optString("sha256").trim().lowercase()
        if (!APK_NAME.matches(apk) || apk.contains("..") || !SHA256.matches(sha)) {
            throw HubException("The hub published an unusable update.")
        }
        val versionCode = body.optLong("versionCode", -1L)
        val versionName = body.optString("versionName").trim()
        if (versionCode < 1L || versionName.isEmpty()) {
            throw HubException("The hub published an unusable update.")
        }
        return UpdateManifest(
            channel = body.optString("channel").ifBlank { "stable" },
            versionCode = versionCode,
            versionName = versionName,
            apk = apk,
            sha256 = sha,
            publishedAt = body.optString("publishedAt"),
        )
    }

    fun downloadApk(baseUrl: String, token: String, apkName: String, dest: File) {
        if (!APK_NAME.matches(apkName) || apkName.contains("..")) {
            throw HubException("The hub published an unusable update.")
        }
        val root = normalizeRoot(baseUrl)
        val connection = (URL(root + "/app/" + apkName + "?channel=stable").openConnection() as HttpURLConnection)
        var partial: File? = null
        try {
            connection.requestMethod = "GET"
            connection.connectTimeout = 8000
            connection.readTimeout = 180000
            connection.instanceFollowRedirects = false
            if (token.isNotBlank()) {
                connection.setRequestProperty("Authorization", "Bearer $token")
            }
            val code = connection.responseCode
            if (code == 401) {
                throw HubException("The hub rejected the token. Open Settings and paste the hub token.")
            }
            if (code !in 200..299) {
                throw HubException("Could not reach the hub for updates.")
            }
            dest.parentFile?.mkdirs()
            val temp = File(dest.parentFile, dest.name + ".partial")
            partial = temp
            connection.inputStream.use { input ->
                temp.outputStream().use { output -> input.copyTo(output) }
            }
            if (temp.length() <= 0L) {
                temp.delete()
                throw HubException("The download was empty.")
            }
            if (dest.exists() && !dest.delete()) {
                temp.delete()
                throw HubException("Could not save the update.")
            }
            if (!temp.renameTo(dest)) {
                temp.delete()
                throw HubException("Could not save the update.")
            }
            partial = null
        } catch (exc: HubException) {
            partial?.delete()
            throw exc
        } catch (exc: Exception) {
            partial?.delete()
            throw HubException("Could not reach the hub for updates.")
        } finally {
            connection.disconnect()
        }
    }

    fun chatUrl(baseUrl: String, chatPath: String, token: String): String {
        val root = baseUrl.trim().trimEnd('/')
        val path = if (chatPath.startsWith("/")) chatPath else "/$chatPath"
        if (token.isBlank()) return root + path
        val join = if (path.contains("?")) "&" else "?"
        val encoded = URLEncoder.encode(token, StandardCharsets.UTF_8.name())
        return root + path + join + "token=" + encoded
    }

    private fun parseItem(row: JSONObject): BuildItem {
        val summary = row.optJSONObject("summary") ?: JSONObject()
        return BuildItem(
            kind = row.optString("kind", "session"),
            id = row.optString("id"),
            title = row.optString("title").ifBlank { "Untitled session" },
            cwd = row.optString("cwd"),
            updatedAt = row.optString("updatedAt"),
            sessionId = row.optString("sessionId"),
            worktreePath = row.optString("worktreePath"),
            repoName = row.optString("repoName"),
            status = row.optString("status"),
            isSubagent = row.optBoolean("isSubagent"),
            actionHint = row.optString("actionHint", "resume"),
            summary = BuildSummary(
                what = summary.optString("what"),
                built = summary.optString("built"),
                progress = summary.optString("progress"),
                next = summary.optString("next"),
                text = summary.optString("text"),
                source = summary.optString("source"),
                stale = summary.optBoolean("stale"),
            ),
        )
    }

    private fun request(
        method: String,
        baseUrl: String,
        path: String,
        token: String,
        json: JSONObject?,
        readTimeoutMs: Int,
    ): JSONObject {
        val raw = requestRaw(method, baseUrl, path, token, json, readTimeoutMs)
        if (raw.code == 401) {
            throw HubException("The hub rejected the token. Open Settings and paste the hub token.")
        }
        if (raw.code !in 200..299) {
            val err = runCatching { JSONObject(raw.text).optString("error") }.getOrDefault("")
            val detail = err.ifBlank { "Hub returned ${raw.code}." }
            throw HubException(detail)
        }
        if (raw.text.isBlank()) return JSONObject()
        return JSONObject(raw.text)
    }

    private fun requestRaw(
        method: String,
        baseUrl: String,
        path: String,
        token: String,
        json: JSONObject?,
        readTimeoutMs: Int,
    ): RawResponse {
        val root = normalizeRoot(baseUrl)
        val connection = (URL(root + path).openConnection() as HttpURLConnection)
        try {
            connection.requestMethod = method
            connection.connectTimeout = 8000
            connection.readTimeout = readTimeoutMs
            connection.setRequestProperty("Accept", "application/json")
            if (token.isNotBlank()) {
                connection.setRequestProperty("Authorization", "Bearer $token")
            }
            if (json != null) {
                val bytes = json.toString().toByteArray(StandardCharsets.UTF_8)
                connection.doOutput = true
                connection.setRequestProperty("Content-Type", "application/json; charset=utf-8")
                connection.outputStream.use { it.write(bytes) }
            }
            val code = connection.responseCode
            val stream = if (code in 200..299) connection.inputStream else connection.errorStream
            val text = stream?.use { input ->
                BufferedReader(InputStreamReader(input, StandardCharsets.UTF_8)).readText()
            }.orEmpty()
            return RawResponse(code, text)
        } catch (exc: HubException) {
            throw exc
        } catch (exc: Exception) {
            throw HubException(exc.message ?: "Could not reach the hub.")
        } finally {
            connection.disconnect()
        }
    }

    private fun normalizeRoot(baseUrl: String): String {
        val root = baseUrl.trim().trimEnd('/')
        if (!root.startsWith("http://") && !root.startsWith("https://")) {
            throw HubException("Hub URL must start with http:// or https://.")
        }
        return root
    }

    private data class RawResponse(val code: Int, val text: String)

    companion object {
        private val APK_NAME = Regex("^GrokRemote-[0-9A-Za-z._+-]+\\.apk$")
        private val SHA256 = Regex("^[0-9a-f]{64}$")
    }
}
