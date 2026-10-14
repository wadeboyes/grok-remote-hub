package com.wadeboyes.grokremote.ui

import android.annotation.SuppressLint
import android.webkit.WebChromeClient
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
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
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
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
import com.wadeboyes.grokremote.HubSettings
import com.wadeboyes.grokremote.SettingsStore
import com.wadeboyes.grokremote.UpdatePhase
import com.wadeboyes.grokremote.UpdateUi
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

private val CardShape = RoundedCornerShape(16.dp)
private val FieldShape = RoundedCornerShape(12.dp)

private data class ChatTarget(
    val url: String,
    val title: String,
    val notice: String,
)

@Composable
fun GrokApp(store: SettingsStore, client: HubClient) {
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
                )
                showSettings = false
            }.onFailure { exc ->
                error = exc.message ?: "Could not resume that session."
            }
        }
    }

    Scaffold(
        containerColor = Bg,
        contentWindowInsets = WindowInsets.safeDrawing,
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
                ?: items.firstOrNull { it.key == selectedKey }

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
                    onSave = { next ->
                        store.write(next)
                        settings = store.read()
                        showSettings = false
                        chat = null
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
                                onSave = { next ->
                                    store.write(next)
                                    settings = store.read()
                                    showSettings = false
                                    chat = null
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
                    onRefresh = { load(true) },
                    onSettings = { showSettings = true },
                    twoPane = false,
                    updateBanner = if (updateUi.announce) updateUi.message else null,
                )
            }
        }
    }
}

private fun filterItems(items: List<BuildItem>, query: String, filter: String): List<BuildItem> {
    val q = query.trim().lowercase()
    return items.filter { item ->
        val kindOk = when (filter) {
            "session" -> item.kind == "session"
            "worktree" -> item.kind == "worktree"
            else -> true
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
                    "No sessions or worktrees match. Start work on the PC, then tap Refresh.",
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
}

@Composable
private fun SummaryPane(item: BuildItem, busy: Boolean, onResume: () -> Unit) {
    Column(
        Modifier
            .fillMaxSize()
            .padding(20.dp),
    ) {
        Text(kindLabel(item), color = Accent, style = MaterialTheme.typography.bodySmall, fontWeight = FontWeight.SemiBold)
        Spacer(Modifier.height(6.dp))
        Text(item.title, style = MaterialTheme.typography.headlineSmall)
        Text(item.cwd, style = MaterialTheme.typography.bodySmall)
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
        AndroidView(
            modifier = Modifier
                .fillMaxWidth()
                .weight(1f),
            factory = { context ->
                WebView(context).apply {
                    settings.javaScriptEnabled = true
                    settings.domStorageEnabled = true
                    settings.useWideViewPort = true
                    settings.loadWithOverviewMode = true
                    webViewClient = WebViewClient()
                    webChromeClient = WebChromeClient()
                    tag = target.url
                    loadUrl(target.url)
                }
            },
            update = { web ->
                if (web.tag != target.url) {
                    web.tag = target.url
                    web.loadUrl(target.url)
                }
            },
        )
    }
}

@Composable
private fun SettingsPane(
    settings: HubSettings,
    updateUi: UpdateUi,
    onBack: () -> Unit,
    onCheck: () -> Unit,
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
                    onSave(HubSettings(hubUrl = cleaned, token = token.trim()))
                }
            },
            modifier = Modifier
                .fillMaxWidth()
                .heightIn(min = MinTouch),
            colors = ButtonDefaults.buttonColors(containerColor = Accent, contentColor = AccentText),
        ) { Text("Save") }
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
    Row(modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        FilterChip("All", filter == "all", { onFilter("all") }, Modifier.weight(1f))
        FilterChip("Sessions", filter == "session", { onFilter("session") }, Modifier.weight(1f))
        FilterChip("Worktrees", filter == "worktree", { onFilter("worktree") }, Modifier.weight(1f))
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
            .clickable(onClick = onClick),
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
