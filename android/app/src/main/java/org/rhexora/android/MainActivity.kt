package org.rhexora.android

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.lightColorScheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.unit.LayoutDirection
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream

private val RhexoraLightColors = lightColorScheme(
    primary = Color(0xFF5A43B5),
    onPrimary = Color.White,
    primaryContainer = Color(0xFFE9DFFF),
    onPrimaryContainer = Color(0xFF21105E),
    secondary = Color(0xFF286A64),
    onSecondary = Color.White,
    secondaryContainer = Color(0xFFB9EEE7),
    onSecondaryContainer = Color(0xFF00201D),
    background = Color(0xFFF7F6FC),
    surface = Color(0xFFFFFFFF),
    surfaceVariant = Color(0xFFEAE7F2),
    onSurface = Color(0xFF1C1B20),
    onSurfaceVariant = Color(0xFF494650),
    outline = Color(0xFF797580),
    error = Color(0xFFBA1A1A),
)

private val RhexoraDarkColors = darkColorScheme(
    primary = Color(0xFFD0BCFF),
    onPrimary = Color(0xFF321A78),
    primaryContainer = Color(0xFF49318E),
    onPrimaryContainer = Color(0xFFE9DDFF),
    secondary = Color(0xFF9CD4CC),
    onSecondary = Color(0xFF003731),
    secondaryContainer = Color(0xFF1B514B),
    onSecondaryContainer = Color(0xFFB9EEE7),
    background = Color(0xFF111016),
    surface = Color(0xFF1B1A20),
    surfaceVariant = Color(0xFF302E36),
    onSurface = Color(0xFFE6E1EA),
    onSurfaceVariant = Color(0xFFCAC4D0),
    outline = Color(0xFF938E99),
    error = Color(0xFFFFB4AB),
)

private enum class AppTab(val label: String, val glyph: String) {
    HOME("الرئيسية", "⌂"),
    TASKS("المهام", "▤"),
    SETTINGS("الإعدادات", "⚙"),
}

private data class TaskChoice(val type: String, val title: String, val description: String) {
    companion object {
        val all = listOf(
            TaskChoice("math.count_primes", "الأعداد الأولية", "عدّ الأعداد الأولية ضمن نطاق محدود"),
            TaskChoice("math.sum_ints", "جمع الأعداد", "اجمع قائمة من الأعداد الصحيحة"),
            TaskChoice("text.analyze", "تحليل النص", "احسب الحروف والكلمات والأسطر"),
        )
    }
}

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { RhexoraScreen() }
    }
}

@Composable
private fun RhexoraScreen() {
    val context = LocalContext.current
    val preferences = remember(context) { context.getSharedPreferences("rhexora_worker", Context.MODE_PRIVATE) }
    var selectedTab by remember { mutableStateOf(AppTab.HOME) }
    var themeMode by remember { mutableStateOf(preferences.getString("theme_mode", "system") ?: "system") }
    val useDarkTheme = when (themeMode) {
        "dark" -> true
        "light" -> false
        else -> androidx.compose.foundation.isSystemInDarkTheme()
    }
    var inviteText by remember { mutableStateOf("") }
    var inviteFeedback by remember { mutableStateOf("") }
    var revealInvite by remember { mutableStateOf(false) }
    var revealWorkerInvite by remember { mutableStateOf(false) }
    var workerInvite by remember { mutableStateOf(preferences.getString("worker_invite", "") ?: "") }
    var workerRunning by remember { mutableStateOf(preferences.getBoolean("worker_running", false) && RhexoraWorkerService.activeInProcess) }
    var workerStatus by remember {
        mutableStateOf(preferences.getString("worker_message", null) ?: if (workerRunning) "العامل نشط" else "العامل متوقف")
    }
    var relayHost by remember { mutableStateOf(preferences.getString("relay_host", "") ?: "") }
    var relayPort by remember { mutableStateOf(preferences.getString("relay_port", "47825") ?: "47825") }
    var settingsMessage by remember { mutableStateOf("") }
    var restartRequired by remember { mutableStateOf(false) }
    var selectedTask by remember { mutableStateOf(TaskChoice.all.first()) }
    var primeStart by remember { mutableStateOf("1") }
    var primeStop by remember { mutableStateOf("100000") }
    var sumInput by remember { mutableStateOf("12\n34\n56") }
    var textInput by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var taskMessage by remember { mutableStateOf("أدخل دعوة موثوقة ثم اختر مهمة متاحة.") }
    var taskSucceeded by remember { mutableStateOf<Boolean?>(null) }
    var taskResult by remember { mutableStateOf("") }
    val scope = rememberCoroutineScope()
    val clipboard = LocalClipboardManager.current

    val parsedInvite = remember(inviteText) {
        if (inviteText.isBlank()) null else runCatching { PeerInvite.parse(inviteText) }.getOrNull()
    }

    LaunchedEffect(context) {
        while (true) {
            val prefs = context.getSharedPreferences("rhexora_worker", Context.MODE_PRIVATE)
            val persistedRunning = prefs.getBoolean("worker_running", false)
            workerRunning = persistedRunning && RhexoraWorkerService.activeInProcess
            if (persistedRunning && !workerRunning) {
                prefs.edit().putBoolean("worker_running", false).putString("worker_message", "العامل متوقف").commit()
            }
            workerInvite = prefs.getString("worker_invite", "") ?: ""
            val savedMessage = prefs.getString("worker_message", null)
            if (!savedMessage.isNullOrBlank()) workerStatus = savedMessage
            else if (workerRunning) workerStatus = "العامل نشط — المنفذ المحلي 47821"
            else if (!workerStatus.contains("تعذر") && !workerStatus.contains("جارٍ")) workerStatus = "العامل متوقف"
            delay(700)
        }
    }

    val saveRelaySettings: () -> Unit = {
        val port = relayPort.toIntOrNull()
        when {
            relayHost.length > 253 -> settingsMessage = "عنوان Relay أطول من الحد المسموح."
            relayHost.isNotBlank() && (port == null || port !in 1..65535) -> settingsMessage = "أدخل منفذ Relay بين 1 و65535."
            else -> {
                val oldHost = preferences.getString("relay_host", "") ?: ""
                val oldPort = preferences.getString("relay_port", "47825") ?: "47825"
                val saved = preferences.edit().putString("relay_host", relayHost.trim()).putString("relay_port", relayPort).commit()
                if (!saved) settingsMessage = "تعذر حفظ الإعدادات على هذا الجهاز."
                else {
                    restartRequired = workerRunning && (oldHost != relayHost.trim() || oldPort != relayPort)
                    settingsMessage = if (restartRequired) "حُفظت الإعدادات. أعد تشغيل العامل لتطبيقها." else "تم حفظ الإعدادات."
                }
            }
        }
    }

    val startWorkerNow: () -> Unit = {
        val normalizedPort = relayPort.toIntOrNull()
        if (relayHost.isNotBlank() && (normalizedPort == null || normalizedPort !in 1..65535)) {
            workerStatus = "منفذ Relay يجب أن يكون بين 1 و65535"
        } else {
            preferences.edit().putString("relay_host", relayHost.trim()).putString("relay_port", relayPort)
                .putString("worker_message", "جارٍ تشغيل العامل وإنشاء دعوة خاصة...").commit()
            workerStatus = "جارٍ تشغيل العامل وإنشاء دعوة خاصة..."
            restartRequired = false
            runCatching { RhexoraWorkerService.start(context) }
                .onFailure {
                    workerStatus = "تعذر بدء العامل: ${it.message ?: "خطأ غير معروف"}"
                    preferences.edit().putString("worker_message", workerStatus).commit()
                }
        }
    }

    val notificationPermission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) startWorkerNow()
        else workerStatus = "اسمح بإشعارات RHEXORA لعرض حالة العامل أثناء تشغيله."
    }

    val beginWorker: () -> Unit = {
        if (Build.VERSION.SDK_INT >= 33 &&
            context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        else startWorkerNow()
    }

    val inviteFilePicker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) {
            runCatching { readBoundedDocument(context, uri, 32 * 1024) }
                .onSuccess { content ->
                    inviteText = content
                    val invite = runCatching { PeerInvite.parse(content) }.getOrNull()
                    inviteFeedback = if (invite == null) "الملف لا يحتوي على دعوة صالحة؛ راجع JSON وبيانات العقدة." else
                        "دعوة صالحة: ${invite.name} — ${if (invite.relayHost.isBlank()) "اتصال مباشر" else "مسار Relay"}. تعامل معها كسرّ."
                }
                .onFailure { inviteFeedback = "تعذر قراءة الملف: ${it.message ?: "خطأ في الملف"}" }
        }
    }

    MaterialTheme(colorScheme = if (useDarkTheme) RhexoraDarkColors else RhexoraLightColors) {
        androidx.compose.runtime.CompositionLocalProvider(LocalLayoutDirection provides LayoutDirection.Rtl) {
            Scaffold(
                containerColor = MaterialTheme.colorScheme.background,
                bottomBar = {
                    NavigationBar(containerColor = MaterialTheme.colorScheme.surface) {
                        AppTab.entries.forEach { tab ->
                            NavigationBarItem(
                                selected = selectedTab == tab,
                                onClick = { selectedTab = tab },
                                icon = { Text(tab.glyph, fontSize = 20.sp, fontWeight = FontWeight.SemiBold) },
                                label = { Text(tab.label) },
                            )
                        }
                    }
                },
            ) { innerPadding ->
                Column(
                    modifier = Modifier.fillMaxSize().padding(innerPadding),
                ) {
                    AppHeader()
                    when (selectedTab) {
                        AppTab.HOME -> HomeScreen(
                            workerRunning = workerRunning,
                            workerStatus = workerStatus,
                            workerInvite = workerInvite,
                            revealWorkerInvite = revealWorkerInvite,
                            onRevealInvite = { revealWorkerInvite = !revealWorkerInvite },
                            onStart = beginWorker,
                            onStop = {
                                RhexoraWorkerService.stop(context)
                                workerStatus = "جارٍ إيقاف العامل..."
                                preferences.edit().putString("worker_message", workerStatus).commit()
                            },
                            onCopyInvite = {
                                clipboard.setText(AnnotatedString(workerInvite))
                                workerStatus = "نُسخت الدعوة. هي سرّ دخول؛ شاركها مع جهاز موثوق فقط."
                            },
                            onOpenTasks = { selectedTab = AppTab.TASKS },
                        )
                        AppTab.TASKS -> TasksScreen(
                            inviteText = inviteText,
                            inviteFeedback = inviteFeedback,
                            parsedInvite = parsedInvite,
                            revealInvite = revealInvite,
                            onInviteTextChange = { inviteText = it; inviteFeedback = "" },
                            onRevealInvite = { revealInvite = !revealInvite },
                            onChooseInviteFile = { inviteFilePicker.launch(arrayOf("application/json", "text/plain", "*/*")) },
                            onPasteInvite = {
                                val pasted = clipboard.getText()?.text.orEmpty()
                                inviteText = pasted
                                val invite = runCatching { PeerInvite.parse(pasted) }.getOrNull()
                                inviteFeedback = if (invite == null) "لم يتم العثور على دعوة صالحة في الحافظة." else
                                    "دعوة صالحة: ${invite.name} — ${if (invite.relayHost.isBlank()) "اتصال مباشر" else "مسار Relay"}."
                            },
                            selectedTask = selectedTask,
                            onSelectTask = { selectedTask = it; taskMessage = "أدخل البيانات ثم ابدأ المهمة."; taskSucceeded = null; taskResult = "" },
                            primeStart = primeStart,
                            primeStop = primeStop,
                            onPrimeStartChange = { primeStart = normalizeDigits(it).filter(Char::isDigit).take(13) },
                            onPrimeStopChange = { primeStop = normalizeDigits(it).filter(Char::isDigit).take(13) },
                            sumInput = sumInput,
                            onSumInputChange = { sumInput = it.take(100_000) },
                            textInput = textInput,
                            onTextInputChange = { textInput = it.take(100_000) },
                            busy = busy,
                            taskMessage = taskMessage,
                            taskSucceeded = taskSucceeded,
                            taskResult = taskResult,
                            onCopyResult = {
                                clipboard.setText(AnnotatedString(taskResult))
                                taskMessage = "نُسخت ملخصات المهمة إلى الحافظة."
                            },
                            onExecute = {
                                val invite = parsedInvite
                                if (invite == null) {
                                    taskMessage = "أدخل دعوة صالحة أولًا."
                                    taskSucceeded = false
                                } else {
                                    val taskType = selectedTask.type
                                    val input = runCatching { buildTaskInput(taskType, primeStart, primeStop, sumInput, textInput) }
                                    if (input.isFailure) {
                                        taskMessage = input.exceptionOrNull()?.message ?: "مدخلات المهمة غير صالحة."
                                        taskSucceeded = false
                                    } else {
                                        busy = true
                                        taskMessage = "جارٍ الاتصال والتحقق من شهادة TLS وإرسال المهمة..."
                                        taskSucceeded = null
                                        taskResult = ""
                                        scope.launch {
                                            val result = withContext(Dispatchers.IO) {
                                                runCatching { RhexoraProtocol.executeTask(invite, taskType, input.getOrThrow()) }
                                            }
                                            result.onSuccess { response ->
                                                taskMessage = "اكتملت المهمة وتحققت بصمة النتيجة."
                                                taskSucceeded = true
                                                taskResult = formatTaskResult(selectedTask, response)
                                            }.onFailure { error ->
                                                taskMessage = "تعذر إكمال المهمة: ${error.message ?: "خطأ اتصال غير معروف"}"
                                                taskSucceeded = false
                                            }
                                            busy = false
                                        }
                                    }
                                }
                            },
                        )
                        AppTab.SETTINGS -> SettingsScreen(
                            relayHost = relayHost,
                            relayPort = relayPort,
                            onRelayHostChange = { relayHost = it; settingsMessage = "" },
                            onRelayPortChange = { relayPort = normalizeDigits(it).filter(Char::isDigit).take(5); settingsMessage = "" },
                            onSaveRelay = saveRelaySettings,
                            settingsMessage = settingsMessage,
                            workerRunning = workerRunning,
                            restartRequired = restartRequired,
                            onRestartWorker = {
                                RhexoraWorkerService.stop(context)
                                workerStatus = "جارٍ إعادة تشغيل العامل بالإعدادات الجديدة..."
                                preferences.edit().putString("worker_message", workerStatus).commit()
                                restartRequired = false
                                scope.launch {
                                    var attempts = 0
                                    while (preferences.getBoolean("worker_running", false) && attempts < 40) {
                                        delay(250)
                                        attempts++
                                    }
                                    beginWorker()
                                }
                            },
                            themeMode = themeMode,
                            onThemeModeChange = {
                                themeMode = it
                                preferences.edit().putString("theme_mode", it).apply()
                            },
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun AppHeader() {
    Row(
        modifier = Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 14.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        Column {
            Text("RHEXORA", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.ExtraBold, color = MaterialTheme.colorScheme.primary)
            Text("حوسبة خاصة بين الأجهزة", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Surface(color = MaterialTheme.colorScheme.secondaryContainer, shape = MaterialTheme.shapes.extraLarge) {
            Text("ألفا 0.4", modifier = Modifier.padding(horizontal = 12.dp, vertical = 7.dp), style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSecondaryContainer)
        }
    }
}

@Composable
private fun HomeScreen(
    workerRunning: Boolean,
    workerStatus: String,
    workerInvite: String,
    revealWorkerInvite: Boolean,
    onRevealInvite: () -> Unit,
    onStart: () -> Unit,
    onStop: () -> Unit,
    onCopyInvite: () -> Unit,
    onOpenTasks: () -> Unit,
) {
    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 18.dp, vertical = 8.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.primaryContainer)) {
            Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text("شبكتك. أجهزتك. مهام محدودة بوضوح.", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold, color = MaterialTheme.colorScheme.onPrimaryContainer)
                Text("عميل وعامل Android تجريبي. استخدم دعوة موثوقة، وشغّل العامل بنفسك، وتابع حالته من هنا.", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onPrimaryContainer)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                    InfoPill("TLS مثبت")
                    InfoPill("3 مهام")
                    InfoPill("بدون تنفيذ كود حر")
                }
                Button(onClick = onOpenTasks, modifier = Modifier.fillMaxWidth()) { Text("ابدأ مهمة") }
            }
        }

        SectionCard("عامل هذا الجهاز", "خدمة ظاهرة تبدأها وتوقفها أنت") {
            val starting = workerStatus.startsWith("جارٍ")
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                StatusDot(active = workerRunning)
                Text(when { workerRunning -> "نشط"; starting -> "جارٍ البدء"; else -> "متوقف" }, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
            }
            Text(workerStatus, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Text("منفذ العامل المحلي: 47821 · يقبل المهام الثلاث المبيّنة في التطبيق فقط.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                Button(onClick = onStart, enabled = !workerRunning && !starting, modifier = Modifier.weight(1f)) { Text(if (starting) "جارٍ البدء..." else "تشغيل العامل") }
                OutlinedButton(onClick = onStop, enabled = workerRunning, modifier = Modifier.weight(1f)) { Text("إيقاف العامل") }
            }
        }

        if (workerRunning && workerInvite.isNotBlank()) {
            SectionCard("دعوة العامل", "مفتاح دخول خاص — لا تنشره علنًا") {
                OutlinedTextField(
                    value = workerInvite,
                    onValueChange = {},
                    readOnly = true,
                    modifier = Modifier.fillMaxWidth(),
                    minLines = 3,
                    maxLines = 5,
                    label = { Text("ملف الدعوة") },
                    visualTransformation = if (revealWorkerInvite) VisualTransformation.None else PasswordVisualTransformation(),
                )
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    TextButton(onClick = onRevealInvite) { Text(if (revealWorkerInvite) "إخفاء" else "إظهار") }
                    OutlinedButton(onClick = onCopyInvite) { Text("نسخ الدعوة السرية") }
                }
            }
        }

        SectionCard("ما يعمل على Android الآن", "هذه مهام فعلية، وليست عناصر واجهة شكلية") {
            TaskChoice.all.forEachIndexed { index, task ->
                TaskSummaryRow(task.title, task.description)
                if (index != TaskChoice.all.lastIndex) HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.2f))
            }
            Text("معالجة الفيديو وGPU متاحة في مسار Python فقط، وليست مدعومة كمهام Android.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun TasksScreen(
    inviteText: String,
    inviteFeedback: String,
    parsedInvite: PeerInvite?,
    revealInvite: Boolean,
    onInviteTextChange: (String) -> Unit,
    onRevealInvite: () -> Unit,
    onChooseInviteFile: () -> Unit,
    onPasteInvite: () -> Unit,
    selectedTask: TaskChoice,
    onSelectTask: (TaskChoice) -> Unit,
    primeStart: String,
    primeStop: String,
    onPrimeStartChange: (String) -> Unit,
    onPrimeStopChange: (String) -> Unit,
    sumInput: String,
    onSumInputChange: (String) -> Unit,
    textInput: String,
    onTextInputChange: (String) -> Unit,
    busy: Boolean,
    taskMessage: String,
    taskSucceeded: Boolean?,
    taskResult: String,
    onCopyResult: () -> Unit,
    onExecute: () -> Unit,
) {
    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 18.dp, vertical = 8.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        Text("تنفيذ مهمة", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
        Text("اختر عقدة تثق بها. يثبت التطبيق شهادة TLS ويتحقق من هوية العامل وبصمات الطلب والنتيجة.", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)

        SectionCard("1 · دعوة العقدة", "يمكنك لصق JSON أو اختيار ملف دعوة خاص") {
            OutlinedTextField(
                value = inviteText,
                onValueChange = onInviteTextChange,
                enabled = !busy,
                modifier = Modifier.fillMaxWidth(),
                minLines = 3,
                maxLines = 6,
                label = { Text("دعوة العقدة بصيغة JSON") },
                visualTransformation = if (revealInvite) VisualTransformation.None else PasswordVisualTransformation(),
                supportingText = { Text("الدعوة سرّ دخول؛ لا ترسلها إلى جهة غير موثوقة.") },
            )
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(onClick = onChooseInviteFile, enabled = !busy, modifier = Modifier.weight(1f)) { Text("اختيار ملف JSON") }
                OutlinedButton(onClick = onPasteInvite, enabled = !busy, modifier = Modifier.weight(1f)) { Text("لصق من الحافظة") }
            }
            TextButton(onClick = onRevealInvite) { Text(if (revealInvite) "إخفاء رموز الدعوة" else "إظهار رموز الدعوة") }
            if (inviteFeedback.isNotBlank()) Text(inviteFeedback, color = if (parsedInvite != null) MaterialTheme.colorScheme.secondary else MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall)
            if (parsedInvite != null) {
                InfoPill(if (parsedInvite.relayHost.isBlank()) "مسار مباشر" else "اتصال عبر Relay")
                Text("العقدة: ${parsedInvite.name} · المنفذ: ${parsedInvite.port}", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }

        SectionCard("2 · اختر مهمة", "المتاح على Android حاليًا") {
            TaskChoice.all.forEach { task ->
                FilterChip(
                    selected = selectedTask.type == task.type,
                    onClick = { onSelectTask(task) },
                    enabled = !busy,
                    label = { Text(task.title) },
                    modifier = Modifier.fillMaxWidth(),
                )
            }
            when (selectedTask.type) {
                "math.count_primes" -> {
                    OutlinedTextField(value = primeStart, onValueChange = onPrimeStartChange, enabled = !busy, modifier = Modifier.fillMaxWidth(), label = { Text("بداية النطاق") }, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number))
                    OutlinedTextField(value = primeStop, onValueChange = onPrimeStopChange, enabled = !busy, modifier = Modifier.fillMaxWidth(), label = { Text("نهاية النطاق (غير شاملة)") }, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number))
                    Text("الحد الأقصى 200,000 عدد، والنهاية لا تتجاوز 10¹².", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                "math.sum_ints" -> {
                    OutlinedTextField(value = sumInput, onValueChange = onSumInputChange, enabled = !busy, modifier = Modifier.fillMaxWidth(), minLines = 4, label = { Text("أعداد صحيحة — رقم في كل سطر أو افصل بفاصلة") }, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal))
                    Text("حتى 100,000 قيمة صحيحة؛ تقبل الواجهة أعدادًا ضمن مدى Long.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                else -> {
                    OutlinedTextField(value = textInput, onValueChange = onTextInputChange, enabled = !busy, modifier = Modifier.fillMaxWidth(), minLines = 5, maxLines = 10, label = { Text("النص المراد تحليله") })
                    Text("الحساب محليًا على العامل: عدد المحارف والكلمات والأسطر. الحد الأقصى 100,000 محرف في الواجهة.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            Button(onClick = onExecute, enabled = !busy && parsedInvite != null, modifier = Modifier.fillMaxWidth()) {
                if (busy) CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp, color = MaterialTheme.colorScheme.onPrimary)
                else Text("اتصال آمن وتنفيذ")
            }
        }

        SectionCard("النتيجة", "لا تعرض الدعوة أو رمز الدخول هنا") {
            Text(taskMessage, color = when (taskSucceeded) {
                true -> MaterialTheme.colorScheme.secondary
                false -> MaterialTheme.colorScheme.error
                null -> MaterialTheme.colorScheme.onSurfaceVariant
            })
            if (taskResult.isNotBlank()) {
                HorizontalDivider()
                Text(taskResult, style = MaterialTheme.typography.bodyMedium)
                TextButton(onClick = onCopyResult) { Text("نسخ ملخص النتيجة") }
            }
        }
    }
}

@Composable
private fun SettingsScreen(
    relayHost: String,
    relayPort: String,
    onRelayHostChange: (String) -> Unit,
    onRelayPortChange: (String) -> Unit,
    onSaveRelay: () -> Unit,
    settingsMessage: String,
    workerRunning: Boolean,
    restartRequired: Boolean,
    onRestartWorker: () -> Unit,
    themeMode: String,
    onThemeModeChange: (String) -> Unit,
) {
    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 18.dp, vertical = 8.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        Text("الإعدادات", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
        SectionCard("المظهر", "احفظ اختيارك على هذا الجهاز") {
            listOf("system" to "حسب النظام", "light" to "فاتح", "dark" to "داكن").forEach { (value, title) ->
                FilterChip(selected = themeMode == value, onClick = { onThemeModeChange(value) }, label = { Text(title) }, modifier = Modifier.fillMaxWidth())
            }
        }
        SectionCard("اتصال Relay", "إعداد اختياري لعامل الهاتف خلف NAT أو CGNAT") {
            Text("المنفذ 47825 هو منفذ خادم Relay البعيد، وليس منفذ العامل. عامل هذا الهاتف يستمع محليًا على 47821.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            OutlinedTextField(value = relayHost, onValueChange = onRelayHostChange, modifier = Modifier.fillMaxWidth(), label = { Text("اسم مضيف Relay — اتركه فارغًا للاتصال المباشر") }, placeholder = { Text("relay.example.com") }, singleLine = true)
            OutlinedTextField(value = relayPort, onValueChange = onRelayPortChange, modifier = Modifier.fillMaxWidth(), label = { Text("منفذ خادم Relay") }, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number), singleLine = true)
            Text("يحتاج المشغّل إلى إضافة node_id ورمز relay_token الموجودين في الدعوة إلى قائمة اعتماداته. حفظ الإعداد وحده لا يثبت أن الخادم متاح.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Button(onClick = onSaveRelay, modifier = Modifier.fillMaxWidth()) { Text("حفظ إعداد Relay") }
            if (settingsMessage.isNotBlank()) Text(settingsMessage, color = if (settingsMessage.startsWith("تعذر") || settingsMessage.startsWith("أدخل") || settingsMessage.startsWith("عنوان")) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.secondary)
            if (restartRequired && workerRunning) {
                Text("العامل يعمل بالإعداد السابق. أعد تشغيله لتطبيق تغيير Relay.", color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall)
                OutlinedButton(onClick = onRestartWorker, modifier = Modifier.fillMaxWidth()) { Text("إعادة تشغيل العامل الآن") }
            }
        }
        SectionCard("حدود التطبيق", "ما هو متاح فعلًا في هذا الإصدار التجريبي") {
            SettingLine("المهام على Android", "عدّ الأوليات · جمع الأعداد · تحليل النص")
            SettingLine("عامل Android", "تشغيل يدوي بخدمة أمامية مرئية")
            SettingLine("التخزين", "دعوة وهوية خاصة داخل مساحة التطبيق")
            SettingLine("الفيديو وGPU", "غير مدعومين على Android حاليًا؛ متاحان عبر Python فقط")
            SettingLine("الإصدار", "0.4.0-alpha · غير جاهز للإنتاج")
        }
        SectionCard("الأمان والخصوصية", "") {
            Text("دعوة العقدة والعامل تحتوي رموز دخول؛ تعامل معها ككلمة مرور. يتحقق التطبيق من بصمة شهادة TLS ولا يقبل كودًا حرًا. أوقف العامل عندما لا تحتاج إليه. مسح بيانات التطبيق قد يؤدي إلى فقدان هوية العامل المحفوظة.", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun SectionCard(title: String, subtitle: String? = null, content: @Composable ColumnScope.() -> Unit) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
    ) {
        Column(Modifier.fillMaxWidth().padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp), content = {
            Text(title, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
            if (!subtitle.isNullOrBlank()) Text(subtitle, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            content()
        })
    }
}

@Composable
private fun InfoPill(text: String) {
    Surface(color = MaterialTheme.colorScheme.secondaryContainer, shape = MaterialTheme.shapes.extraLarge) {
        Text(text, modifier = Modifier.padding(horizontal = 10.dp, vertical = 6.dp), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSecondaryContainer)
    }
}

@Composable
private fun StatusDot(active: Boolean) {
    Surface(color = if (active) MaterialTheme.colorScheme.secondary else MaterialTheme.colorScheme.outline, shape = MaterialTheme.shapes.extraLarge, modifier = Modifier.size(12.dp)) {}
}

@Composable
private fun TaskSummaryRow(title: String, description: String) {
    Column(verticalArrangement = Arrangement.spacedBy(3.dp)) {
        Text(title, fontWeight = FontWeight.SemiBold)
        Text(description, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun SettingLine(title: String, value: String) {
    Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
        Text(title, style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(value, style = MaterialTheme.typography.bodyMedium)
    }
}

private fun readBoundedDocument(context: Context, uri: android.net.Uri, maxBytes: Int): String {
    val input = requireNotNull(context.contentResolver.openInputStream(uri)) { "تعذر فتح الملف" }
    input.use { stream ->
        val output = ByteArrayOutputStream()
        val buffer = ByteArray(4096)
        while (true) {
            val count = stream.read(buffer)
            if (count < 0) break
            require(output.size() + count <= maxBytes) { "ملف الدعوة يتجاوز 32 كيلوبايت" }
            output.write(buffer, 0, count)
        }
        return output.toByteArray().toString(Charsets.UTF_8)
    }
}

private fun buildTaskInput(type: String, primeStart: String, primeStop: String, sumInput: String, textInput: String): JSONObject {
    return when (type) {
        "math.count_primes" -> {
            val start = normalizeDigits(primeStart).toLongOrNull() ?: throw IllegalArgumentException("أدخل بداية نطاق صحيحة.")
            val stop = normalizeDigits(primeStop).toLongOrNull() ?: throw IllegalArgumentException("أدخل نهاية نطاق صحيحة.")
            require(start >= 0 && stop > start && stop - start <= 200_000 && stop <= 1_000_000_000_000L) {
                "النطاق يجب أن يكون موجبًا، لا يتجاوز 200,000 عدد، ونهايته لا تتجاوز 10¹²."
            }
            JSONObject().put("start", start).put("stop", stop)
        }
        "math.sum_ints" -> {
            val tokens = normalizeDigits(sumInput.replace('،', ',')).split(Regex("[,\\s]+" )).filter { it.isNotBlank() }
            require(tokens.isNotEmpty()) { "أدخل عددًا صحيحًا واحدًا على الأقل." }
            require(tokens.size <= 100_000) { "الحد الأقصى 100,000 قيمة." }
            val values = JSONArray()
            var total = 0L
            tokens.forEachIndexed { index, token ->
                val value = token.toLongOrNull() ?: throw IllegalArgumentException("القيمة رقم ${index + 1} ليست عددًا صحيحًا ضمن مدى Long.")
                total = try {
                    Math.addExact(total, value)
                } catch (_: ArithmeticException) {
                    throw IllegalArgumentException("مجموع القيم يجب أن يبقى ضمن مدى 64-بت.")
                }
                values.put(value)
            }
            JSONObject().put("values", values)
        }
        "text.analyze" -> {
            require(textInput.codePointCount(0, textInput.length) <= 100_000) { "الحد الأقصى 100,000 محرف في واجهة Android." }
            JSONObject().put("text", textInput)
        }
        else -> throw IllegalArgumentException("نوع المهمة غير مدعوم في هذا الإصدار.")
    }
}

private fun formatTaskResult(task: TaskChoice, response: JSONObject): String {
    val result = response.getJSONObject("result")
    val summary = when (task.type) {
        "math.count_primes" -> "الأعداد الأولية: ${result.getLong("count")}\nالنطاق: ${result.getLong("start")} إلى أقل من ${result.getLong("stop")}"
        "math.sum_ints" -> "المجموع: ${result.get("sum")}\nعدد القيم: ${result.getInt("count")}"
        else -> "المحارف: ${result.getInt("characters")}\nالكلمات: ${result.getInt("words")}\nالأسطر: ${result.getInt("lines")}"
    }
    return "$summary\n\nالعامل: ${response.optString("worker")}\nالمسار: ${if (response.optString("transport") == "relay") "Relay" else "مباشر"}\nمعرّف الطلب: ${response.optString("request_id")}\nبصمة النتيجة: ${response.optString("result_hash")}"
}

private fun normalizeDigits(value: String): String = buildString(value.length) {
    value.forEach { character ->
        val digit = when (character) {
            in '\u0660'..'\u0669' -> character.code - '\u0660'.code
            in '\u06F0'..'\u06F9' -> character.code - '\u06F0'.code
            else -> -1
        }
        append(if (digit >= 0) ('0'.code + digit).toChar() else character)
    }
}
