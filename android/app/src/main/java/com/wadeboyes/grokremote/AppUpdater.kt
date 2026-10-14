package com.wadeboyes.grokremote

import android.content.Intent
import android.content.pm.PackageInfo
import android.content.pm.PackageInstaller
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.provider.Settings
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.withContext
import java.io.File
import java.security.MessageDigest

enum class UpdatePhase {
    Idle,
    Checking,
    UpToDate,
    NonePublished,
    Downloading,
    Verifying,
    Installing,
    NeedsConfirm,
    Failed,
}

data class UpdateUi(
    val currentName: String,
    val currentCode: Long,
    val availableName: String,
    val availableCode: Long,
    val phase: UpdatePhase,
    val message: String,
    val announce: Boolean,
    val confirmIntent: Intent?,
    val confirmSerial: Int,
)

/**
 * Checks the hub for a newer sideload build, checks the file, then installs it.
 * Silent install works only after this app is the installer of record.
 */
class AppUpdater(
    private val context: android.content.Context,
    private val client: HubClient,
) {
    private val gate = Mutex()
    private val _state: MutableStateFlow<UpdateUi>

    val state: StateFlow<UpdateUi>

    init {
        val installed = installedVersion()
        _state = MutableStateFlow(
            UpdateUi(
                currentName = installed.name,
                currentCode = installed.code,
                availableName = "",
                availableCode = 0L,
                phase = UpdatePhase.Idle,
                message = "",
                announce = false,
                confirmIntent = null,
                confirmSerial = 0,
            )
        )
        state = _state
        active = this
    }

    fun installedVersion(): InstalledVersion {
        val info = context.packageManager.getPackageInfo(context.packageName, 0)
        return InstalledVersion(
            name = info.versionName?.ifBlank { "unknown" } ?: "unknown",
            code = info.longVersionCode,
        )
    }

    suspend fun checkAndInstall(baseUrl: String, token: String) {
        if (!gate.tryLock()) return
        try {
            runCheck(baseUrl, token)
        } catch (exc: CancellationException) {
            throw exc
        } catch (exc: Exception) {
            _state.update {
                it.copy(
                    phase = UpdatePhase.Failed,
                    message = "Could not reach the hub for updates.",
                    announce = false,
                    confirmIntent = null,
                )
            }
        } finally {
            gate.unlock()
        }
    }

    fun onInstallStatus(status: Int, rawMessage: String, confirm: Intent?) {
        _state.update { current ->
            when (status) {
                PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                    val launch = confirm?.apply {
                        addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    }
                    current.copy(
                        phase = UpdatePhase.NeedsConfirm,
                        message = "Android needs one confirm tap to install this update.",
                        announce = true,
                        confirmIntent = launch,
                        confirmSerial = current.confirmSerial + 1,
                    )
                }
                PackageInstaller.STATUS_SUCCESS -> current.copy(
                    phase = UpdatePhase.Installing,
                    message = "Update installed.",
                    announce = true,
                    confirmIntent = null,
                )
                else -> current.copy(
                    phase = UpdatePhase.Failed,
                    message = rawMessage.ifBlank { "The update could not be installed." },
                    announce = true,
                    confirmIntent = null,
                )
            }
        }
    }

    private suspend fun runCheck(baseUrl: String, token: String) {
        val installed = installedVersion()
        _state.update {
            it.copy(
                currentName = installed.name,
                currentCode = installed.code,
                phase = UpdatePhase.Checking,
                message = "Checking for updates.",
                announce = false,
                confirmIntent = null,
            )
        }
        val manifest = try {
            withContext(Dispatchers.IO) { client.fetchUpdateManifest(baseUrl, token) }
        } catch (exc: HubException) {
            val text = if (exc.message?.contains("token", ignoreCase = true) == true) {
                exc.message ?: "Could not reach the hub for updates."
            } else {
                "Could not reach the hub for updates."
            }
            _state.update {
                it.copy(phase = UpdatePhase.Failed, message = text, announce = false)
            }
            return
        }
        if (manifest == null) {
            _state.update {
                it.copy(
                    availableName = "",
                    availableCode = 0L,
                    phase = UpdatePhase.NonePublished,
                    message = "No update is published on the hub yet.",
                    announce = false,
                )
            }
            return
        }
        _state.update {
            it.copy(
                availableName = manifest.versionName,
                availableCode = manifest.versionCode,
            )
        }
        if (manifest.versionCode <= installed.code) {
            _state.update {
                it.copy(
                    phase = UpdatePhase.UpToDate,
                    message = "You are on the latest build.",
                    announce = false,
                )
            }
            return
        }
        if (!context.packageManager.canRequestPackageInstalls()) {
            _state.update {
                it.copy(
                    phase = UpdatePhase.Failed,
                    message = "Allow this app to install unknown apps, then tap Check for updates again.",
                    announce = true,
                )
            }
            withContext(Dispatchers.Main) {
                val intent = Intent(
                    Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                    Uri.parse("package:${context.packageName}"),
                ).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                runCatching { context.startActivity(intent) }
            }
            return
        }
        _state.update {
            it.copy(
                phase = UpdatePhase.Downloading,
                message = "Update ${manifest.versionName} is downloading.",
                announce = true,
            )
        }
        val dest = File(context.cacheDir, "updates/${manifest.apk}")
        dest.parentFile?.mkdirs()
        try {
            withContext(Dispatchers.IO) {
                if (!dest.isFile || sha256(dest) != manifest.sha256) {
                    client.downloadApk(baseUrl, token, manifest.apk, dest)
                }
            }
        } catch (exc: HubException) {
            dest.delete()
            _state.update {
                it.copy(
                    phase = UpdatePhase.Failed,
                    message = exc.message ?: "Could not reach the hub for updates.",
                    announce = true,
                )
            }
            return
        }
        _state.update {
            it.copy(phase = UpdatePhase.Verifying, message = "Checking the download.", announce = true)
        }
        val hash = withContext(Dispatchers.IO) { sha256(dest) }
        if (!hash.equals(manifest.sha256, ignoreCase = true)) {
            dest.delete()
            _state.update {
                it.copy(
                    phase = UpdatePhase.Failed,
                    message = "The download did not match the published checksum.",
                    announce = true,
                )
            }
            return
        }
        val verdict = withContext(Dispatchers.IO) { inspectApk(dest, manifest.versionCode) }
        if (verdict != null) {
            dest.delete()
            _state.update {
                it.copy(phase = UpdatePhase.Failed, message = verdict, announce = true)
            }
            return
        }
        _state.update {
            it.copy(phase = UpdatePhase.Installing, message = "Installing.", announce = true)
        }
        try {
            install(dest)
        } catch (exc: Exception) {
            _state.update {
                it.copy(
                    phase = UpdatePhase.Failed,
                    message = "The update could not be installed.",
                    announce = true,
                )
            }
        }
    }

    private fun inspectApk(apk: File, expectedCode: Long): String? {
        val flags = PackageManager.GET_SIGNING_CERTIFICATES
        val archive = context.packageManager.getPackageArchiveInfo(apk.absolutePath, flags)
            ?: return "Could not read the update. It was not installed."
        if (archive.packageName != context.packageName) {
            return "The update is for a different app. It was not installed."
        }
        if (archive.longVersionCode != expectedCode) {
            return "The download did not match the published version."
        }
        val installed = context.packageManager.getPackageInfo(context.packageName, flags)
        val archiveCerts = signerDigests(archive)
        val installedCerts = signerDigests(installed)
        if (archiveCerts.isEmpty() || installedCerts.isEmpty() || archiveCerts.intersect(installedCerts).isEmpty()) {
            return "The update is signed by a different key. It was not installed."
        }
        return null
    }

    private fun signerDigests(info: PackageInfo): Set<String> {
        val signing = info.signingInfo ?: return emptySet()
        val signers = (if (signing.hasMultipleSigners()) {
            signing.apkContentsSigners
        } else {
            signing.signingCertificateHistory
        }) ?: emptyArray()
        return signers.map { sha256(it.toByteArray()) }.toSet()
    }

    private fun install(apk: File) {
        val installer = context.packageManager.packageInstaller
        val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL).apply {
            setAppPackageName(context.packageName)
            setSize(apk.length())
            setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
            if (Build.VERSION.SDK_INT >= 34) {
                setRequestUpdateOwnership(true)
            }
        }
        val sessionId = installer.createSession(params)
        installer.openSession(sessionId).use { session ->
            session.openWrite("base.apk", 0, apk.length()).use { output ->
                apk.inputStream().use { input -> input.copyTo(output) }
                session.fsync(output)
            }
            val callback = Intent(context, UpdateInstallReceiver::class.java).apply {
                action = UpdateInstallReceiver.ACTION
            }
            val pending = android.app.PendingIntent.getBroadcast(
                context,
                sessionId,
                callback,
                android.app.PendingIntent.FLAG_UPDATE_CURRENT or android.app.PendingIntent.FLAG_MUTABLE,
            )
            session.commit(pending.intentSender)
        }
    }

    private fun sha256(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buffer = ByteArray(1024 * 1024)
            while (true) {
                val count = input.read(buffer)
                if (count < 0) break
                digest.update(buffer, 0, count)
            }
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }

    private fun sha256(bytes: ByteArray): String {
        val digest = MessageDigest.getInstance("SHA-256").digest(bytes)
        return digest.joinToString("") { "%02x".format(it) }
    }

    data class InstalledVersion(val name: String, val code: Long)

    companion object {
        const val CHECK_EVERY_MS = 3L * 60L * 60L * 1000L

        @Volatile
        private var active: AppUpdater? = null

        fun dispatchInstallStatus(status: Int, rawMessage: String, confirm: Intent?) {
            active?.onInstallStatus(status, rawMessage, confirm)
        }
    }
}
