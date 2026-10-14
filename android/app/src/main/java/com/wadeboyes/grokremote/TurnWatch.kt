package com.wadeboyes.grokremote

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.IBinder
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

data class OpenChat(
    val sessionId: String,
    val chatPath: String,
    val title: String,
    val stamp: Long,
)

/** Skip the session already open on this device. Other sessions still alert. */
fun shouldAlert(openSessionId: String, eventSessionId: String): Boolean {
    val eventId = eventSessionId.trim()
    if (eventId.isEmpty()) return false
    return eventId != openSessionId.trim()
}

fun notifyGroupKey(sessionId: String): String = "session:" + sessionId.trim()

object TurnAlerts {
    const val CHANNEL_ALERTS = "grok_turns"
    const val CHANNEL_WATCH = "grok_watch"
    const val WATCH_ID = 42
    const val EXTRA_SESSION = "open_session_id"
    const val EXTRA_PATH = "open_chat_path"
    const val EXTRA_TITLE = "open_title"

    fun ensureChannels(context: Context) {
        val manager = context.getSystemService(NotificationManager::class.java) ?: return
        val alerts = NotificationChannel(
            CHANNEL_ALERTS,
            "Session alerts",
            NotificationManager.IMPORTANCE_DEFAULT,
        )
        alerts.description = "When a Grok session finishes or needs your answer"
        val watch = NotificationChannel(
            CHANNEL_WATCH,
            "Watching",
            NotificationManager.IMPORTANCE_MIN,
        )
        watch.description = "Quiet notice while Grok Remote listens for session alerts"
        watch.setShowBadge(false)
        watch.setSound(null, null)
        manager.createNotificationChannel(alerts)
        manager.createNotificationChannel(watch)
    }

    fun watchNotification(context: Context): Notification {
        ensureChannels(context)
        return NotificationCompat.Builder(context, CHANNEL_WATCH)
            .setSmallIcon(R.drawable.ic_stat_grok)
            .setContentTitle("Watching sessions")
            .setContentText("Alerts when a session finishes or needs you.")
            .setOngoing(true)
            .setSilent(true)
            .setOnlyAlertOnce(true)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .setContentIntent(appIntent(context, "", "", ""))
            .build()
    }

    fun showAll(context: Context, events: List<NotifyEvent>, openSessionId: String) {
        ensureChannels(context)
        for (event in events) {
            if (!shouldAlert(openSessionId, event.sessionId)) continue
            show(context, event)
        }
    }

    fun show(context: Context, event: NotifyEvent) {
        val sessionId = event.sessionId.trim()
        if (sessionId.isEmpty()) return
        val title = event.title.ifBlank { "Grok session" }
        val line = event.line.ifBlank { "Finished" }
        val group = notifyGroupKey(sessionId)
        val pending = appIntent(context, sessionId, event.chatPath, title)
        val childId = alertId(sessionId)
        val summaryId = childId + 1_000_000
        val child = NotificationCompat.Builder(context, CHANNEL_ALERTS)
            .setSmallIcon(R.drawable.ic_stat_grok)
            .setContentTitle(title)
            .setContentText(line)
            .setStyle(NotificationCompat.BigTextStyle().bigText(line))
            .setGroup(group)
            .setAutoCancel(true)
            .setContentIntent(pending)
            .build()
        val summary = NotificationCompat.Builder(context, CHANNEL_ALERTS)
            .setSmallIcon(R.drawable.ic_stat_grok)
            .setContentTitle(title)
            .setContentText(line)
            .setStyle(NotificationCompat.BigTextStyle().bigText(line))
            .setGroup(group)
            .setGroupSummary(true)
            .setAutoCancel(true)
            .setContentIntent(pending)
            .build()
        val manager = context.getSystemService(NotificationManager::class.java) ?: return
        manager.notify(childId, child)
        manager.notify(summaryId, summary)
    }

    private fun appIntent(
        context: Context,
        sessionId: String,
        chatPath: String,
        title: String,
    ): PendingIntent {
        val intent = Intent(context, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or
                Intent.FLAG_ACTIVITY_SINGLE_TOP or
                Intent.FLAG_ACTIVITY_CLEAR_TOP
            if (sessionId.isNotBlank()) {
                putExtra(EXTRA_SESSION, sessionId)
                putExtra(EXTRA_PATH, chatPath)
                putExtra(EXTRA_TITLE, title)
            }
        }
        val request = if (sessionId.isBlank()) WATCH_ID else alertId(sessionId)
        return PendingIntent.getActivity(
            context,
            request,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
    }

    private fun alertId(sessionId: String): Int {
        val hash = sessionId.hashCode() and 0x7fffffff
        return 10_000 + (hash % 1_000_000)
    }
}

object TurnWatch {
    const val WORK_NAME = "grok-turn-watch"

    fun start(context: Context) {
        val app = context.applicationContext
        val store = SettingsStore(app)
        if (!store.notificationsEnabled()) return
        if (store.read().token.isBlank()) return
        TurnAlerts.ensureChannels(app)
        val intent = Intent(app, TurnWatchService::class.java)
        androidx.core.content.ContextCompat.startForegroundService(app, intent)
        schedule(app)
    }

    fun stop(context: Context) {
        val app = context.applicationContext
        app.stopService(Intent(app, TurnWatchService::class.java))
        runCatching { WorkManager.getInstance(app).cancelUniqueWork(WORK_NAME) }
    }

    fun schedule(context: Context) {
        val constraints = Constraints.Builder()
            .setRequiredNetworkType(NetworkType.CONNECTED)
            .build()
        val request = PeriodicWorkRequestBuilder<TurnWatchWorker>(15, TimeUnit.MINUTES)
            .setConstraints(constraints)
            .build()
        WorkManager.getInstance(context.applicationContext).enqueueUniquePeriodicWork(
            WORK_NAME,
            ExistingPeriodicWorkPolicy.UPDATE,
            request,
        )
    }

    fun pollOnce(store: SettingsStore, client: HubClient): List<NotifyEvent> {
        val settings = store.read()
        if (!store.notificationsEnabled() || settings.token.isBlank()) return emptyList()
        val primed = store.hasNotifyCursor()
        val since = if (primed) store.notifyCursor() else -1L
        val wait = if (primed) 20 else 0
        val poll = client.pollNotify(settings.hubUrl, settings.token, since, wait)
        if (!primed) {
            store.setNotifyCursor(poll.cursor)
            return emptyList()
        }
        val known = store.notifyCursor()
        if (poll.cursor < known) {
            store.setNotifyCursor(poll.cursor)
            return emptyList()
        }
        store.setNotifyCursor(poll.cursor)
        return poll.events
    }
}

class TurnWatchService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var job: Job? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        ServiceCompat.startForeground(
            this,
            TurnAlerts.WATCH_ID,
            TurnAlerts.watchNotification(this),
            ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC,
        )
        val store = SettingsStore(this)
        if (!store.notificationsEnabled() || store.read().token.isBlank()) {
            running.set(false)
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf()
            return START_NOT_STICKY
        }
        running.set(true)
        if (job?.isActive != true) {
            job = scope.launch { loop() }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        running.set(false)
        job?.cancel()
        scope.coroutineContext[Job]?.cancel()
        super.onDestroy()
    }

    private suspend fun loop() {
        val store = SettingsStore(this)
        val client = HubClient()
        var pauseMs = 5_000L
        while (running.get()) {
            if (!store.notificationsEnabled() || store.read().token.isBlank()) {
                stopSelf()
                return
            }
            try {
                val events = TurnWatch.pollOnce(store, client)
                TurnAlerts.showAll(this, events, store.openSessionId())
                pauseMs = 5_000L
            } catch (_: Exception) {
                delay(pauseMs)
                pauseMs = (pauseMs * 2).coerceAtMost(30_000L)
            }
        }
    }

    companion object {
        val running = AtomicBoolean(false)
    }
}

class TurnWatchWorker(
    appContext: Context,
    params: WorkerParameters,
) : CoroutineWorker(appContext, params) {
    override suspend fun doWork(): Result {
        val store = SettingsStore(applicationContext)
        if (!store.notificationsEnabled()) return Result.success()
        if (TurnWatchService.running.get()) return Result.success()
        return try {
            val events = TurnWatch.pollOnce(store, HubClient())
            TurnAlerts.showAll(applicationContext, events, store.openSessionId())
            Result.success()
        } catch (_: Exception) {
            Result.success()
        }
    }
}
