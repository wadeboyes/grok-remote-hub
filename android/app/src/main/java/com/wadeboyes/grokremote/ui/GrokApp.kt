package com.wadeboyes.grokremote.ui

import android.Manifest
import android.annotation.SuppressLint
import android.content.pm.PackageManager
import android.net.Uri
import android.util.Log
import android.view.ViewGroup
import android.webkit.ConsoleMessage
import android.webkit.CookieManager
import android.webkit.WebChromeClient
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.BackHandler
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Snackbar
import androidx.compose.material3.SnackbarDuration
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.SnackbarResult
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.core.content.ContextCompat
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import com.wadeboyes.grokremote.AppUpdater
import com.wadeboyes.grokremote.BuildItem
import com.wadeboyes.grokremote.HubClient
import com.wadeboyes.grokremote.HubException
import com.wadeboyes.grokremote.HubSettings
import com.wadeboyes.grokremote.OpenChat
import com.wadeboyes.grokremote.SettingsStore
import com.wadeboyes.grokremote.TurnWatch
import com.wadeboyes.grokremote.UpdatePhase
import com.wadeboyes.grokremote.UpdateUi
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

private val CardShape = RoundedCornerShape(16.dp)
private val FieldShape = RoundedCornerShape(12.dp)

private data class ChatTarget(
    val url: String,
    val title: String,
    val notice: String,
    val sessionId: String = "",
)

private data class RemovePrompt(
    val item: BuildItem,
    val danger: Boolean,
    val message: String,
)

@Composable
fun GrokApp(
    store: SettingsStore,
    client: HubClient,
    openChat: StateFlow<OpenChat?>,
) {
    val scope = rememberCoroutineScope()
    val context = LocalContext.current
    val updater = remember { AppUpdater(context.applicationContext, client) }
    var updateUi by remember { mutableStateOf(updater.state.value) }
    var settings by remember { mutableStateOf(store.read()) }
    var items by remember { mutableStateOf<List<BuildItem>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var query by rememberSaveable { mutableStateOf("") }
    var filter by rememberSaveable { mutableStateOf("all") }
    var selectedKey by rememberSaveable { mutableStateOf<String?>(null) }
    var showSettings by rememberSaveable { mutableStateOf(false) }
    var chat by remember { mutableStateOf<ChatTarget?>(null) }
    var busyKey by remember { mutableStateOf<String?>(null) }
    var removePrompt by remember { mutableStateOf<RemovePrompt?>(null) }
    val snackbarHostState = remember { SnackbarHostState() }
    val notifyPermission = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        store.setNotificationAsked()
        store.setNotificationsEnabled(granted)
        settings = store.read()
        if (granted) {
            TurnWatch.start(context.applicationContext)
        } else {
            TurnWatch.stop(context.applicationContext)
        }
    }

    fun notificationsAllowed(): Boolean {
        return ContextCompat.checkSelfPermission(
            context,
            Manifest.permission.POST_NOTIFICATIONS,
        ) == PackageManager.PERMISSION_GRANTED
    }

    fun applyNotifications(enabled: Boolean) {
        if (!enabled) {
            store.setNotificationsEnabled(false)
            settings = store.read()
            TurnWatch.stop(context.applicationContext)
            return
        }
        if (notificationsAllowed()) {
            store.setNotificationsEnabled(true)
            settings = store.read()
            TurnWatch.start(context.applicationContext)
        } else {
            notifyPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
    }

    LaunchedEffect(Unit) {
        if (!store.notificationsEnabled()) return@LaunchedEffect
        if (notificationsAllowed()) {
            TurnWatch.start(context.applicationContext)
        } else if (!store.notificationAsked()) {
            store.setNotificationAsked()
            notifyPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
    }

    LaunchedEffect(openChat) {
        openChat.collect { open ->
            if (open == null || open.sessionId.isBlank()) return@collect
            val current = store.read()
            val path = open.chatPath.ifBlank { "/?open=${open.sessionId}&embed=chat" }
            chat = ChatTarget(
                url = client.chatUrl(current.hubUrl, path, current.token),
                title = open.title.ifBlank { "Grok session" },
                notice = "",
                sessionId = open.sessionId,
            )
            showSettings = false
        }
    }

    LaunchedEffect(chat?.sessionId) {
        store.setOpenSession(chat?.sessionId)
    }

    fun load(scheduleRefresh: Boolean) {
        scope.launch {
            loading = items.isEmpty()
            error = null
            val current = settings
            val result = withContext(Dispatchers.IO) {
                runCatching {
                    if (scheduleRefresh) {
                        runCatching { client.scheduleRefresh(current.hubUrl, current.token) }
                    }
                    client.fetchIndex(current.hubUrl, current.token)
                }
            }
            result.onSuccess { loaded ->
                items = loaded
                loading = false
                if (selectedKey == null || loaded.none { it.key == selectedKey }) {
                    selectedKey = loaded.firstOrNull()?.key
                }
            }.onFailure { exc ->
                loading = false
                error = exc.message ?: "Could not reach the hub."
            }
        }
    }

    LaunchedEffect(updater) {
        updater.state.collect { updateUi = it }
    }

    LaunchedEffect(settings.hubUrl, settings.token) {
        load(scheduleRefresh = false)
        while (true) {
            updater.checkAndInstall(settings.hubUrl, settings.token)
            delay(AppUpdater.CHECK_EVERY_MS)
        }
    }

    LaunchedEffect(updateUi.confirmSerial) {
        val intent = updateUi.confirmIntent ?: return@LaunchedEffect
        runCatching { context.startActivity(intent) }
    }

    fun resume(item: BuildItem) {
        if (busyKey != null) return
        busyKey = item.key
        selectedKey = item.key
        scope.launch {
            val current = settings
            val result = withContext(Dispatchers.IO) {
                runCatching { client.resume(current.hubUrl, current.token, item) }
            }
            busyKey = null
            result.onSuccess { resumed ->
                val notice = if (resumed.action == "restart") {
                    "Started a fresh session in this folder."
                } else {
                    ""
                }
                chat = ChatTarget(
                    url = client.chatUrl(current.hubUrl, resumed.chatPath, current.token),
                    title = resumed.title.ifBlank { item.title },
                    notice = notice,
                    sessionId = resumed.liveSessionId.ifBlank {
                        resumed.viewSessionId.ifBlank { item.sessionId }
                    },
                )
                showSettings = false
            }.onFailure { exc ->
                error = exc.message ?: "Could not resume that session."
            }
        }
    }

    fun restore(item: BuildItem) {
        scope.launch {
            val current = settings
            val result = withContext(Dispatchers.IO) {
                runCatching { client.restore(current.hubUrl, current.token, item) }
            }
            result.onSuccess {
                items = items.map { row ->
                    if (row.key == item.key) row.copy(archived = false) else row
                }
            }.onFailure { exc ->
                error = exc.message ?: "Could not restore that item."
            }
        }
    }

    fun archive(item: BuildItem) {
        if (busyKey != null) return
        busyKey = item.key
        scope.launch {
            val current = settings
            val result = withContext(Dispatchers.IO) {
                runCatching { client.archive(current.hubUrl, current.token, item) }
            }
            busyKey = null
            result.onSuccess {
                val next = items.map { row ->
                    if (row.key == item.key) row.copy(archived = true) else row
                }
                items = next
                if (selectedKey == item.key && filter != "archived") {
                    val stillVisible = filterItems(next, query, filter)
                    if (stillVisible.none { it.key == selectedKey }) {
                        selectedKey = stillVisible.firstOrNull()?.key
                    }
                }
                val outcome = snackbarHostState.showSnackbar(
                    message = "Archived ${item.title.ifBlank { "item" }}",
                    actionLabel = "Undo",
                    duration = SnackbarDuration.Short,
                )
                if (outcome == SnackbarResult.ActionPerformed) {
                    restore(item)
                }
            }.onFailure { exc ->
                error = exc.message ?: "Could not archive that item."
            }
        }
    }

    fun askRemove(item: BuildItem) {
        if (item.kind != "worktree" || busyKey != null) return
        removePrompt = RemovePrompt(
            item = item,
            danger = false,
            message = removeConfirmText(item),
        )
    }

    fun confirmRemove() {
        val prompt = removePrompt ?: return
        removePrompt = null
        busyKey = prompt.item.key
        scope.launch {
            val current = settings
            val result = withContext(Dispatchers.IO) {
                runCatching {
                    client.removeWorktree(current.hubUrl, current.token, prompt.item, prompt.danger)
                }
            }
            busyKey = null
            result.onSuccess { archivedSession ->
                val next = items.mapNotNull { row ->
                    when {
                        row.key == prompt.item.key -> null
                        archivedSession.isNotBlank() &&
                            row.kind == "session" &&
                            row.sessionId == archivedSession -> row.copy(archived = true)
                        else -> row
                    }
                }
                items = next
                if (selectedKey == prompt.item.key) {
                    selectedKey = filterItems(next, query, filter).firstOrNull()?.key
                }
                val folder = folderLabel(prompt.item)
                snackbarHostState.showSnackbar(
                    message = "Removed $folder.",
                    duration = SnackbarDuration.Short,
                )
            }.onFailure { exc ->
                val code = (exc as? HubException)?.code.orEmpty()
                if (!prompt.danger && code in setOf("dirty", "unpushed", "dirty_unpushed")) {
                    removePrompt = RemovePrompt(
                        item = prompt.item,
                        danger = true,
                        message = removeAnywayText(exc.message ?: ""),
                    )
                } else {
                    if (code == "missing") {
                        items = items.filterNot { it.key == prompt.item.key }
                    }
                    error = exc.message ?: "Could not remove that worktree."
                }
            }
        }
    }

    Scaffold(
        containerColor = Bg,
        contentWindowInsets = WindowInsets.safeDrawing,
        snackbarHost = {
            SnackbarHost(snackbarHostState) { data ->
                Snackbar(
                    snackbarData = data,
                    containerColor = Surface2,
                    contentColor = TextMain,
                    actionColor = Accent,
                )
            }
        },
    ) { padding ->
        BoxWithConstraints(
            Modifier
                .padding(padding)
                .fillMaxSize()
                .background(Bg),
        ) {
            val twoPane = maxWidth >= TwoPaneMin
            val visible = remember(items, query, filter) { filterItems(items, query, filter) }
            val selected = visible.firstOrNull { it.key == selectedKey }
                ?: items.firstOrNull { it.key == selectedKey }?.takeIf { candidate ->
                    filterItems(listOf(candidate), "", filter).isNotEmpty()
                }

            if (!twoPane && chat != null) {
                BackHandler { chat = null }
                ChatPane(
                    target = chat!!,
                    onBack = { chat = null },
                    showBack = true,
                )
            } else if (!twoPane && showSettings) {
                BackHandler { showSettings = false }
                SettingsPane(
                    settings = settings,
                    updateUi = updateUi,
                    onBack = { showSettings = false },
                    onCheck = {
                        scope.launch { updater.checkAndInstall(settings.hubUrl, settings.token) }
                    },
                    onNotifications = { enabled -> applyNotifications(enabled) },
                    onSave = { next ->
                        store.write(next)
                        settings = store.read()
                        showSettings = false
                        chat = null
                        if (next.notificationsEnabled && next.token.isNotBlank() && notificationsAllowed()) {
                            TurnWatch.start(context.applicationContext)
                        }
                    },
                )
            } else if (twoPane) {
                Row(Modifier.fillMaxSize()) {
                    Column(
                        Modifier
                            .weight(0.38f)
                            .fillMaxHeight()
                            .background(Surface),
                    ) {
                        ListPane(
                            items = visible,
                            loading = loading,
                            error = error,
                            query = query,
                            filter = filter,
                            selectedKey = selectedKey,
                            busyKey = busyKey,
                            onQuery = { query = it },
                            onFilter = { filter = it },
                            onSelect = { selectedKey = it.key },
                            onResume = { resume(it) },
                            onArchive = { archive(it) },
                            onRestore = { restore(it) },
                            onRemove = { askRemove(it) },
                            onRefresh = { load(true) },
                            onSettings = { showSettings = true },
                            twoPane = true,
                            updateBanner = if (updateUi.announce) updateUi.message else null,
                        )
                    }
                    Box(
                        Modifier
                            .width(1.dp)
                            .fillMaxHeight()
                            .background(Border),
                    )
                    Column(
                        Modifier
                            .weight(0.62f)
                            .fillMaxHeight(),
                    ) {
                        when {
                            showSettings -> SettingsPane(
                                settings = settings,
                                updateUi = updateUi,
                                onBack = { showSettings = false },
                                onCheck = {
                                    scope.launch { updater.checkAndInstall(settings.hubUrl, settings.token) }
                                },
                                onNotifications = { enabled -> applyNotifications(enabled) },
                                onSave = { next ->
                                    store.write(next)
                                    settings = store.read()
                                    showSettings = false
                                    chat = null
                                    if (
                                        next.notificationsEnabled &&
                                        next.token.isNotBlank() &&
                                        notificationsAllowed()
                                    ) {
                                        TurnWatch.start(context.applicationContext)
                                    }
                                },
                            )
                            chat != null -> {
                                BackHandler { chat = null }
                                ChatPane(
                                    target = chat!!,
                                    onBack = { chat = null },
                                    showBack = true,
                                )
                            }
                            selected != null -> SummaryPane(
                                item = selected,
                                busy = busyKey == selected.key,
                                onResume = { resume(selected) },
                                onArchive = { archive(selected) },
                                onRestore = { restore(selected) },
                                onRemove = { askRemove(selected) },
                            )
                            else -> EmptyDetail()
                        }
                    }
                }
            } else {
                ListPane(
                    items = visible,
                    loading = loading,
                    error = error,
                    query = query,
                    filter = filter,
                    selectedKey = null,
                    busyKey = busyKey,
                    onQuery = { query = it },
                    onFilter = { filter = it },
                    onSelect = { selectedKey = it.key },
                    onResume = { resume(it) },
                    onArchive = { archive(it) },
                    onRestore = { restore(it) },
                    onRemove = { askRemove(it) },
                    onRefresh = { load(true) },
                    onSettings = { showSettings = true },
                    twoPane = false,
                    updateBanner = if (updateUi.announce) updateUi.message else null,
                )
            }
        }
        val prompt = removePrompt
        if (prompt != null) {
            BackHandler { removePrompt = null }
            AlertDialog(
                onDismissRequest = { removePrompt = null },
                containerColor = Surface,
                titleContentColor = TextMain,
                textContentColor = TextMain,
                title = {
                    Text(if (prompt.danger) "Remove anyway" else "Remove worktree")
                },
                text = { Text(prompt.message) },
                confirmButton = {
                    Button(
                        onClick = { confirmRemove() },
                        modifier = Modifier.heightIn(min = MinTouch),
                        colors = ButtonDefaults.buttonColors(
                            containerColor = Danger,
                            contentColor = TextMain,
                        ),
                    ) {
                        Text(if (prompt.danger) "Remove anyway" else "Remove")
                    }
                },
                dismissButton = {
                    TextButton(
                        onClick = { removePrompt = null },
                        modifier = Modifier.heightIn(min = MinTouch),
                    ) { Text("Cancel") }
                },
            )
        }
    }
}

private fun filterItems(items: List<BuildItem>, query: String, filter: String): List<BuildItem> {
    val q = query.trim().lowercase()
    return items.filter { item ->
        val kindOk = when (filter) {
            "archived" -> item.archived
            "session" -> item.kind == "session" && !item.archived
            "worktree" -> item.kind == "worktree" && !item.archived
            else -> !item.archived
        }
        if (!kindOk) return@filter false
        if (q.isEmpty()) return@filter true
        val hay = listOf(item.title, item.cwd, item.repoName, item.summary.text, item.sessionId)
            .joinToString(" ")
            .lowercase()
        hay.contains(q)
    }
}

@Composable
private fun ListPane(
    items: List<BuildItem>,
    loading: Boolean,
    error: String?,
    query: String,
    filter: String,
    selectedKey: String?,
    busyKey: String?,
    onQuery: (String) -> Unit,
    onFilter: (String) -> Unit,
    onSelect: (BuildItem) -> Unit,
    onResume: (BuildItem) -> Unit,
    onArchive: (BuildItem) -> Unit,
    onRestore: (BuildItem) -> Unit,
    onRemove: (BuildItem) -> Unit,
    onRefresh: () -> Unit,
    onSettings: () -> Unit,
    twoPane: Boolean,
    updateBanner: String?,
) {
    Column(Modifier.fillMaxSize()) {
        Row(
            Modifier
                .fillMaxWidth()
                .padding(start = 16.dp, end = 8.dp, top = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(Modifier.weight(1f)) {
                Text("Grok Build", style = MaterialTheme.typography.headlineSmall)
                Text(
                    if (twoPane) "Sessions and worktrees" else "All sessions and worktrees",
                    style = MaterialTheme.typography.bodySmall,
                )
            }
            TextButton(
                onClick = onRefresh,
                modifier = Modifier.heightIn(min = MinTouch),
            ) { Text("Refresh") }
            TextButton(
                onClick = onSettings,
                modifier = Modifier.heightIn(min = MinTouch),
            ) { Text("Settings") }
        }
        SearchField(query, onQuery, Modifier.padding(horizontal = 16.dp, vertical = 8.dp))
        FilterRow(filter, onFilter, Modifier.padding(horizontal = 16.dp))
        if (!updateBanner.isNullOrBlank()) {
            Text(
                updateBanner,
                color = TextMain,
                style = MaterialTheme.typography.bodyMedium,
                modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp),
            )
        }
        if (!error.isNullOrBlank()) {
            Text(
                error,
                color = Danger,
                style = MaterialTheme.typography.bodyMedium,
                modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp),
            )
            Button(
                onClick = onRefresh,
                modifier = Modifier
                    .padding(horizontal = 16.dp)
                    .fillMaxWidth()
                    .heightIn(min = MinTouch),
                colors = ButtonDefaults.buttonColors(containerColor = Accent, contentColor = AccentText),
            ) { Text("Try again") }
        }
        when {
            loading && items.isEmpty() -> Box(
                Modifier.fillMaxSize(),
                contentAlignment = Alignment.Center,
            ) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    CircularProgressIndicator(color = Accent)
                    Spacer(Modifier.height(12.dp))
                    Text("Loading sessions", style = MaterialTheme.typography.bodyMedium)
                }
            }
            items.isEmpty() -> Box(
                Modifier
                    .fillMaxSize()
                    .padding(24.dp),
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    if (filter == "archived") {
                        "Nothing is archived."
                    } else {
                        "No sessions or worktrees match. Start work on the PC, then tap Refresh."
                    },
                    style = MaterialTheme.typography.bodyLarge,
                )
            }
            else -> LazyColumn(
                contentPadding = PaddingValues(16.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
                modifier = Modifier.fillMaxSize(),
            ) {
                items(items, key = { it.key }) { item ->
                    SessionCard(
                        item = item,
                        selected = item.key == selectedKey,
                        busy = item.key == busyKey,
                        onSelect = { onSelect(item) },
                        onResume = { onResume(item) },
                        onArchive = { onArchive(item) },
                        onRestore = { onRestore(item) },
                        onRemove = { onRemove(item) },
                    )
                }
            }
        }
    }
}

@Composable
private fun SessionCard(
    item: BuildItem,
    selected: Boolean,
    busy: Boolean,
    onSelect: () -> Unit,
    onResume: () -> Unit,
    onArchive: () -> Unit,
    onRestore: () -> Unit,
    onRemove: () -> Unit,
) {
    val borderColor = if (selected) Accent else Border
    Column(
        Modifier
            .fillMaxWidth()
            .clip(CardShape)
            .background(Surface2)
            .border(1.dp, borderColor, CardShape)
            .clickable(onClick = onSelect)
            .padding(16.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                kindLabel(item),
                color = Accent,
                style = MaterialTheme.typography.bodySmall,
                fontWeight = FontWeight.SemiBold,
            )
            Spacer(Modifier.weight(1f))
            Text(shortWhen(item.updatedAt), style = MaterialTheme.typography.bodySmall)
        }
        Spacer(Modifier.height(6.dp))
        Text(
            item.title,
            style = MaterialTheme.typography.titleMedium,
            maxLines = 2,
            overflow = TextOverflow.Ellipsis,
        )
        val place = leaf(item.cwd.ifBlank { item.worktreePath })
        if (place.isNotBlank()) {
            Text(place, style = MaterialTheme.typography.bodySmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        Spacer(Modifier.height(8.dp))
        Text(
            item.summary.text.ifBlank { "Summary is not ready yet." },
            style = MaterialTheme.typography.bodyMedium,
            maxLines = 6,
            overflow = TextOverflow.Ellipsis,
        )
        if (item.summary.stale) {
            Spacer(Modifier.height(6.dp))
            Text("Updating this summary on the PC.", style = MaterialTheme.typography.bodySmall)
        }
        Spacer(Modifier.height(12.dp))
        ItemActions(item, busy, onResume, onArchive, onRestore, onRemove)
    }
}

@Composable
private fun ItemActions(
    item: BuildItem,
    busy: Boolean,
    onResume: () -> Unit,
    onArchive: () -> Unit,
    onRestore: () -> Unit,
    onRemove: () -> Unit,
) {
    if (item.archived) {
        Button(
            onClick = onRestore,
            enabled = !busy,
            modifier = Modifier
                .fillMaxWidth()
                .heightIn(min = MinTouch),
            colors = ButtonDefaults.buttonColors(containerColor = Accent, contentColor = AccentText),
        ) { Text(if (busy) "Working..." else "Restore") }
    } else {
        Button(
            onClick = onResume,
            enabled = !busy,
            modifier = Modifier
                .fillMaxWidth()
                .heightIn(min = MinTouch),
            colors = ButtonDefaults.buttonColors(containerColor = Accent, contentColor = AccentText),
        ) {
            Text(if (busy) "Working..." else item.buttonLabel)
        }
    }
    val secondary = item.kind == "worktree" || !item.archived
    if (!secondary) return
    Spacer(Modifier.height(8.dp))
    if (item.archived) {
        if (item.kind == "worktree") {
            TextButton(
                onClick = onRemove,
                enabled = !busy,
                modifier = Modifier
                    .fillMaxWidth()
                    .heightIn(min = MinTouch),
            ) { Text("Remove", color = Danger) }
        }
    } else if (item.kind == "worktree") {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            TextButton(
                onClick = onArchive,
                enabled = !busy,
                modifier = Modifier
                    .weight(1f)
                    .heightIn(min = MinTouch),
            ) { Text("Archive") }
            TextButton(
                onClick = onRemove,
                enabled = !busy,
                modifier = Modifier
                    .weight(1f)
                    .heightIn(min = MinTouch),
            ) { Text("Remove", color = Danger) }
        }
    } else {
        TextButton(
            onClick = onArchive,
            enabled = !busy,
            modifier = Modifier
                .fillMaxWidth()
                .heightIn(min = MinTouch),
        ) { Text("Archive") }
    }
}

@Composable
private fun SummaryPane(
    item: BuildItem,
    busy: Boolean,
    onResume: () -> Unit,
    onArchive: () -> Unit,
    onRestore: () -> Unit,
    onRemove: () -> Unit,
) {
    Column(
        Modifier
            .fillMaxSize()
            .padding(20.dp),
    ) {
        Text(kindLabel(item), color = Accent, style = MaterialTheme.typography.bodySmall, fontWeight = FontWeight.SemiBold)
        Spacer(Modifier.height(6.dp))
        Text(item.title, style = MaterialTheme.typography.headlineSmall)
        Text(item.cwd, style = MaterialTheme.typography.bodySmall)
        val branch = branchLabel(item.gitRef)
        if (item.kind == "worktree" && branch.isNotBlank()) {
            Text("Branch $branch", style = MaterialTheme.typography.bodySmall)
        }
        Spacer(Modifier.height(16.dp))
        SummaryBlock("What it is", item.summary.what)
        SummaryBlock("What was being built", item.summary.built)
        SummaryBlock("Where it got to", item.summary.progress)
        SummaryBlock("What is next", item.summary.next)
        if (item.summary.stale) {
            Text("Updating this summary on the PC.", style = MaterialTheme.typography.bodySmall)
            Spacer(Modifier.height(8.dp))
        }
        Spacer(Modifier.weight(1f))
        ItemActions(item, busy, onResume, onArchive, onRestore, onRemove)
    }
}

@Composable
private fun SummaryBlock(label: String, body: String) {
    Text(label, style = MaterialTheme.typography.bodySmall, fontWeight = FontWeight.SemiBold)
    Spacer(Modifier.height(4.dp))
    Text(
        body.ifBlank { "Not written down yet." },
        style = MaterialTheme.typography.bodyLarge,
    )
    Spacer(Modifier.height(14.dp))
}

@Composable
private fun EmptyDetail() {
    Box(Modifier.fillMaxSize().padding(24.dp), contentAlignment = Alignment.Center) {
        Text(
            "Pick a session or worktree. The summary stays on this side. Resume opens the chat here.",
            style = MaterialTheme.typography.bodyLarge,
        )
    }
}

private const val WEB_LOG = "GrokRemoteWeb"

@SuppressLint("SetJavaScriptEnabled")
private fun prepareChatWebView(webView: WebView) {
    webView.layoutParams = ViewGroup.LayoutParams(
        ViewGroup.LayoutParams.MATCH_PARENT,
        ViewGroup.LayoutParams.MATCH_PARENT,
    )
    webView.setBackgroundColor(0xFF0A0C10.toInt())
    val settings = webView.settings
    settings.javaScriptEnabled = true
    settings.domStorageEnabled = true
    settings.useWideViewPort = true
    // Overview zoom shrinks the chat to a speck and leaves the pane black.
    settings.loadWithOverviewMode = false
    // A cached 401 for app.js would leave the page on Connecting forever.
    settings.cacheMode = WebSettings.LOAD_NO_CACHE
    val cookies = CookieManager.getInstance()
    cookies.setAcceptCookie(true)
    cookies.setAcceptThirdPartyCookies(webView, true)
    webView.clearCache(true)
}

private class ChatChromeClient : WebChromeClient() {
    override fun onConsoleMessage(message: ConsoleMessage?): Boolean {
        if (message == null) return true
        val line = redactWeb(
            "${message.messageLevel().name} ${message.sourceId()}:${message.lineNumber()} ${message.message()}"
        )
        Log.i(WEB_LOG, line)
        return true
    }
}

private fun redactWeb(text: String): String {
    return text.replace(Regex("token=[^&\\s\"]+"), "token=redacted")
}

private fun loadChatUrl(webView: WebView, pageUrl: String) {
    // The page URL already has ?token=. Also store that same secret as a cookie
    // so stylesheet, script, and websocket requests are authorized. Android
    // WebView does not copy the document query string onto those requests.
    primeHubCookie(pageUrl)
    val token = pageToken(pageUrl)
    if (token.isEmpty()) {
        webView.loadUrl(pageUrl)
    } else {
        webView.loadUrl(pageUrl, mapOf("Authorization" to "Bearer $token"))
    }
}

private fun pageToken(pageUrl: String): String {
    return Uri.parse(pageUrl).getQueryParameter("token").orEmpty()
}

private fun primeHubCookie(pageUrl: String) {
    val token = pageToken(pageUrl)
    if (token.isEmpty() || !cookieSafeToken(token)) return
    val uri = Uri.parse(pageUrl)
    val scheme = uri.scheme ?: return
    val authority = uri.authority ?: return
    val manager = CookieManager.getInstance()
    manager.setAcceptCookie(true)
    manager.setCookie("$scheme://$authority/", "hub_token=$token; Path=/")
    manager.flush()
}

private fun cookieSafeToken(token: String): Boolean {
    if (token.isEmpty()) return false
    return token.all { ch ->
        val code = ch.code
        code in 33..126 && ch != ';' && ch != ',' && ch != '"' && ch != '\\'
    }
}

@SuppressLint("SetJavaScriptEnabled")
@Composable
private fun ChatPane(target: ChatTarget, onBack: () -> Unit, showBack: Boolean) {
    Column(Modifier.fillMaxSize()) {
        Row(
            Modifier
                .fillMaxWidth()
                .padding(horizontal = 8.dp, vertical = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            if (showBack) {
                TextButton(onClick = onBack, modifier = Modifier.heightIn(min = MinTouch)) {
                    Text("Back")
                }
            }
            Column(Modifier.weight(1f).padding(end = 12.dp)) {
                Text(
                    target.title.ifBlank { "Chat" },
                    style = MaterialTheme.typography.titleMedium,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                if (target.notice.isNotBlank()) {
                    Text(target.notice, style = MaterialTheme.typography.bodySmall)
                }
            }
        }
        Box(
            Modifier
                .fillMaxWidth()
                .weight(1f),
        ) {
            AndroidView(
                modifier = Modifier.fillMaxSize(),
                factory = { context ->
                    WebView(context).apply {
                        prepareChatWebView(this)
                        webViewClient = WebViewClient()
                        webChromeClient = ChatChromeClient()
                        tag = target.url
                        loadChatUrl(this, target.url)
                    }
                },
                update = { web ->
                    web.layoutParams = ViewGroup.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT,
                        ViewGroup.LayoutParams.MATCH_PARENT,
                    )
                    if (web.tag != target.url) {
                        web.tag = target.url
                        loadChatUrl(web, target.url)
                    }
                },
                onRelease = { web ->
                    runCatching {
                        web.stopLoading()
                        web.destroy()
                    }
                },
            )
        }
    }
}

@Composable
private fun SettingsPane(
    settings: HubSettings,
    updateUi: UpdateUi,
    onBack: () -> Unit,
    onCheck: () -> Unit,
    onNotifications: (Boolean) -> Unit,
    onSave: (HubSettings) -> Unit,
) {
    var url by rememberSaveable(settings.hubUrl) { mutableStateOf(settings.hubUrl) }
    var token by rememberSaveable(settings.token) { mutableStateOf(settings.token) }
    var localError by remember { mutableStateOf<String?>(null) }
    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            TextButton(onClick = onBack, modifier = Modifier.heightIn(min = MinTouch)) {
                Text("Back")
            }
            Text("Settings", style = MaterialTheme.typography.headlineSmall)
        }
        Spacer(Modifier.height(8.dp))
        Text(
            "Hub URL and token. The phone and the tablet use the same Tailscale address.",
            style = MaterialTheme.typography.bodyMedium,
        )
        Spacer(Modifier.height(16.dp))
        Text("Hub URL", style = MaterialTheme.typography.bodySmall)
        Spacer(Modifier.height(6.dp))
        FieldBox(url, { url = it }, "http://100.113.227.9:8787")
        Spacer(Modifier.height(14.dp))
        Text("Hub token", style = MaterialTheme.typography.bodySmall)
        Spacer(Modifier.height(6.dp))
        FieldBox(token, { token = it }, "Paste hub_token")
        if (!localError.isNullOrBlank()) {
            Spacer(Modifier.height(10.dp))
            Text(localError!!, color = Danger, style = MaterialTheme.typography.bodyMedium)
        }
        Spacer(Modifier.height(16.dp))
        Button(
            onClick = {
                val cleaned = url.trim()
                if (!cleaned.startsWith("http://") && !cleaned.startsWith("https://")) {
                    localError = "Hub URL must start with http:// or https://."
                } else {
                    localError = null
                    onSave(
                        HubSettings(
                            hubUrl = cleaned,
                            token = token.trim(),
                            notificationsEnabled = settings.notificationsEnabled,
                        ),
                    )
                }
            },
            modifier = Modifier
                .fillMaxWidth()
                .heightIn(min = MinTouch),
            colors = ButtonDefaults.buttonColors(containerColor = Accent, contentColor = AccentText),
        ) { Text("Save") }
        Spacer(Modifier.height(18.dp))
        Row(
            Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(Modifier.weight(1f)) {
                Text("Session alerts", style = MaterialTheme.typography.bodyLarge)
                Text(
                    "Notify when any session finishes, stops with an error, or needs your answer. The open chat stays quiet.",
                    style = MaterialTheme.typography.bodySmall,
                )
            }
            Spacer(Modifier.width(12.dp))
            Switch(
                checked = settings.notificationsEnabled,
                onCheckedChange = onNotifications,
                modifier = Modifier.heightIn(min = MinTouch),
            )
        }
        Spacer(Modifier.height(18.dp))
        Text("Current version", style = MaterialTheme.typography.bodySmall)
        Text(
            "${updateUi.currentName} (${updateUi.currentCode})",
            style = MaterialTheme.typography.bodyLarge,
        )
        Spacer(Modifier.height(10.dp))
        Text("Available", style = MaterialTheme.typography.bodySmall)
        Text(
            if (updateUi.availableCode > 0L) {
                "${updateUi.availableName} (${updateUi.availableCode})"
            } else {
                "None yet"
            },
            style = MaterialTheme.typography.bodyLarge,
        )
        Spacer(Modifier.height(12.dp))
        val updateBusy = updateUi.phase == UpdatePhase.Checking ||
            updateUi.phase == UpdatePhase.Downloading ||
            updateUi.phase == UpdatePhase.Verifying ||
            updateUi.phase == UpdatePhase.Installing
        Button(
            onClick = onCheck,
            enabled = !updateBusy,
            modifier = Modifier
                .fillMaxWidth()
                .heightIn(min = MinTouch),
            colors = ButtonDefaults.buttonColors(containerColor = Accent, contentColor = AccentText),
        ) { Text("Check for updates") }
        if (updateUi.message.isNotBlank()) {
            Spacer(Modifier.height(10.dp))
            Text(updateUi.message, style = MaterialTheme.typography.bodyMedium)
        }
        Spacer(Modifier.height(14.dp))
        Text(
            "Made for the Galaxy S24 Ultra and the Galaxy Tab S10. Rotate the tablet for the two pane layout.",
            style = MaterialTheme.typography.bodySmall,
        )
    }
}

@Composable
private fun FieldBox(value: String, onValue: (String) -> Unit, hint: String) {
    Box(
        Modifier
            .fillMaxWidth()
            .heightIn(min = MinTouch)
            .clip(FieldShape)
            .background(Bg)
            .border(1.dp, Border, FieldShape)
            .padding(horizontal = 14.dp, vertical = 12.dp),
        contentAlignment = Alignment.CenterStart,
    ) {
        if (value.isEmpty()) {
            Text(hint, style = MaterialTheme.typography.bodyMedium, color = Muted)
        }
        BasicTextField(
            value = value,
            onValueChange = onValue,
            textStyle = MaterialTheme.typography.bodyLarge.copy(color = TextMain),
            cursorBrush = SolidColor(Accent),
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
    }
}

@Composable
private fun SearchField(value: String, onValue: (String) -> Unit, modifier: Modifier = Modifier) {
    Box(
        modifier
            .fillMaxWidth()
            .heightIn(min = MinTouch)
            .clip(FieldShape)
            .background(Surface2)
            .border(1.dp, Border, FieldShape)
            .padding(horizontal = 14.dp),
        contentAlignment = Alignment.CenterStart,
    ) {
        if (value.isEmpty()) {
            Text("Search sessions", color = Muted, style = MaterialTheme.typography.bodyMedium)
        }
        BasicTextField(
            value = value,
            onValueChange = onValue,
            textStyle = MaterialTheme.typography.bodyLarge.copy(color = TextMain),
            cursorBrush = SolidColor(Accent),
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
    }
}

@Composable
private fun FilterRow(filter: String, onFilter: (String) -> Unit, modifier: Modifier = Modifier) {
    Row(
        modifier
            .fillMaxWidth()
            .horizontalScroll(rememberScrollState()),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        val chip = Modifier.widthIn(min = 96.dp)
        FilterChip("All", filter == "all", { onFilter("all") }, chip)
        FilterChip("Sessions", filter == "session", { onFilter("session") }, chip)
        FilterChip("Worktrees", filter == "worktree", { onFilter("worktree") }, chip)
        FilterChip("Archived", filter == "archived", { onFilter("archived") }, chip)
    }
}

@Composable
private fun FilterChip(label: String, active: Boolean, onClick: () -> Unit, modifier: Modifier = Modifier) {
    val bg = if (active) Accent else Surface2
    val fg = if (active) AccentText else TextMain
    Box(
        modifier
            .heightIn(min = MinTouch)
            .clip(FieldShape)
            .background(bg)
            .clickable(onClick = onClick)
            .padding(horizontal = 12.dp),
        contentAlignment = Alignment.Center,
    ) {
        Text(label, color = fg, style = MaterialTheme.typography.labelLarge, maxLines = 1)
    }
}

private fun kindLabel(item: BuildItem): String {
    return when {
        item.kind == "worktree" && item.isSubagent -> "Worktree, subagent"
        item.kind == "worktree" -> "Worktree"
        item.isSubagent -> "Subagent"
        else -> "Session"
    }
}

private fun branchLabel(gitRef: String): String {
    val raw = gitRef.trim()
    return if (raw.startsWith("refs/heads/")) raw.removePrefix("refs/heads/") else raw
}

private fun folderLabel(item: BuildItem): String {
    return leaf(item.worktreePath.ifBlank { item.cwd }).ifBlank { item.title.ifBlank { "worktree" } }
}

private fun removeConfirmText(item: BuildItem): String {
    val folder = folderLabel(item)
    val branch = branchLabel(item.gitRef)
    val named = if (branch.isNotBlank()) {
        "Remove the worktree folder $folder on branch $branch?"
    } else {
        "Remove the worktree folder $folder?"
    }
    return "$named This can't be undone. The branch is kept."
}

private fun removeAnywayText(reason: String): String {
    val clean = reason.trim().ifBlank {
        "This worktree has uncommitted changes or unpushed commits."
    }
    return "$clean Remove anyway deletes the folder. This can't be undone. The branch is kept."
}

private fun leaf(path: String): String {
    val trimmed = path.trim().trimEnd('\\', '/')
    if (trimmed.isEmpty()) return ""
    val slash = maxOf(trimmed.lastIndexOf('\\'), trimmed.lastIndexOf('/'))
    return if (slash >= 0 && slash < trimmed.lastIndex) trimmed.substring(slash + 1) else trimmed
}

private fun shortWhen(iso: String): String {
    if (iso.length < 10) return ""
    val months = arrayOf(
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    )
    val year = iso.substring(0, 4)
    val month = iso.substring(5, 7).toIntOrNull() ?: return iso.take(10)
    val day = iso.substring(8, 10).toIntOrNull() ?: return iso.take(10)
    if (month !in 1..12) return iso.take(10)
    return "$day ${months[month - 1]} $year"
}
