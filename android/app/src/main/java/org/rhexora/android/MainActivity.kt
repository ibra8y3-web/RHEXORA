package org.rhexora.android

import android.os.Bundle
import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import android.content.Context
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { RhexoraScreen() }
    }
}

@androidx.compose.runtime.Composable
private fun RhexoraScreen() {
    var inviteText by remember { mutableStateOf("") }
    var startText by remember { mutableStateOf("1") }
    var stopText by remember { mutableStateOf("100000") }
    var status by remember { mutableStateOf("ألصق ملف دعوة العقدة الخاصة بك للبدء.") }
    var busy by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    val context = LocalContext.current
    val clipboard = LocalClipboardManager.current
    var workerInvite by remember {
        mutableStateOf(context.getSharedPreferences("rhexora_worker", Context.MODE_PRIVATE).getString("worker_invite", "") ?: "")
    }
    var workerStatus by remember {
        mutableStateOf(if (context.getSharedPreferences("rhexora_worker", Context.MODE_PRIVATE).getBoolean("worker_running", false)) "العامل نشط" else "العامل متوقف")
    }
    var relayHost by remember {
        mutableStateOf(context.getSharedPreferences("rhexora_worker", Context.MODE_PRIVATE).getString("relay_host", "") ?: "")
    }
    var relayPort by remember {
        mutableStateOf(context.getSharedPreferences("rhexora_worker", Context.MODE_PRIVATE).getString("relay_port", "47825") ?: "47825")
    }

    val startWorkerNow: () -> Unit = {
        val normalizedPort = relayPort.toIntOrNull()
        if (relayHost.isNotBlank() && (normalizedPort == null || normalizedPort !in 1..65535)) {
            workerStatus = "منفذ Relay يجب أن يكون بين 1 و65535"
        } else {
            context.getSharedPreferences("rhexora_worker", Context.MODE_PRIVATE).edit()
                .putString("relay_host", relayHost.trim())
                .putString("relay_port", relayPort)
                .commit()
            runCatching { RhexoraWorkerService.start(context) }
                .onSuccess { workerStatus = "جارٍ تشغيل العامل وإنشاء دعوة خاصة..." }
                .onFailure { workerStatus = "تعذر بدء العامل: ${it.message}" }
            scope.launch {
                repeat(20) {
                    delay(500)
                    val prefs = context.getSharedPreferences("rhexora_worker", Context.MODE_PRIVATE)
                    val invite = prefs.getString("worker_invite", null)
                    if (!invite.isNullOrBlank() && prefs.getBoolean("worker_running", false)) {
                        workerInvite = invite
                        workerStatus = "العامل نشط — يمكن مشاركة الدعوة مع جهاز موثوق"
                        return@launch
                    }
                }
                if (workerInvite.isBlank()) workerStatus = "لم تبدأ الخدمة بعد. تحقق من المنفذ 47821 وإعدادات الهاتف."
            }
        }
    }
    val notificationPermission = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        if (granted) startWorkerNow()
        else workerStatus = "اسمح بإشعارات RHEXORA لإظهار حالة خدمة المشاركة باستمرار."
    }

    MaterialTheme {
        Surface(modifier = Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
            Column(
                modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(20.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                Text("RHEXORA", style = MaterialTheme.typography.headlineLarge, fontWeight = FontWeight.Bold)
                Text("شبكة حوسبة لامركزية مفتوحة المصدر", style = MaterialTheme.typography.titleMedium)
                Text("عميل وعامل Android — إصدار ألفا", style = MaterialTheme.typography.bodySmall)
                Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text("ربط عقدة موثوقة", fontWeight = FontWeight.SemiBold)
                        Text("استورد محتوى ملف الدعوة الخاص الذي أنشأته بأمر rhexora invite. يحتوي الملف على رمز دخول سري.")
                        OutlinedTextField(
                            value = inviteText,
                            onValueChange = { inviteText = it },
                            modifier = Modifier.fillMaxWidth(),
                            minLines = 4,
                            label = { Text("محتوى ملف الدعوة JSON") },
                        )
                    }
                }
                Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text("المشاركة كعامل Android", fontWeight = FontWeight.SemiBold)
                        Text("يمكن لهاتفك استقبال مهام حساب الأعداد الأولية أثناء تشغيل خدمة ظاهرة بإشعار دائم. لا تعمل الخدمة تلقائيًا؛ أنت من يبدأها ويوقفها.")
                        OutlinedTextField(
                            value = relayHost,
                            onValueChange = { relayHost = it },
                            modifier = Modifier.fillMaxWidth(),
                            label = { Text("عنوان Relay عام (اختياري)") },
                            placeholder = { Text("relay.example.com") },
                        )
                        OutlinedTextField(
                            value = relayPort,
                            onValueChange = { relayPort = it.filter(Char::isDigit).take(5) },
                            modifier = Modifier.fillMaxWidth(),
                            label = { Text("منفذ Relay") },
                        )
                        Text("عند ضبط Relay سيحافظ الهاتف على اتصال صادر إلى الخادم، ما يسمح للعميل بالوصول إليه خلف NAT/CGNAT. يجب أن يضيف مشغّل Relay رقم العقدة ورمز relay_token الواردين في الدعوة إلى ملف الاعتمادات لديه. تغيير إعدادات Relay يتطلب إعادة تشغيل العامل.", style = MaterialTheme.typography.bodySmall)
                        Text(workerStatus, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            Button(onClick = {
                                if (Build.VERSION.SDK_INT >= 33 &&
                                    context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
                                ) {
                                    notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
                                } else {
                                    startWorkerNow()
                                }
                            }, modifier = Modifier.weight(1f)) { Text("تشغيل العامل") }
                            Button(onClick = {
                                RhexoraWorkerService.stop(context)
                                workerStatus = "تم طلب إيقاف العامل"
                            }, modifier = Modifier.weight(1f)) { Text("إيقاف العامل") }
                        }
                        if (workerInvite.isNotBlank()) {
                            OutlinedTextField(
                                value = workerInvite,
                                onValueChange = {},
                                readOnly = true,
                                modifier = Modifier.fillMaxWidth(),
                                minLines = 4,
                                label = { Text("دعوة العامل الخاصة") },
                            )
                            Button(onClick = { clipboard.setText(AnnotatedString(workerInvite)); workerStatus = "تم نسخ الدعوة — تعامل معها ككلمة مرور سرية" }) {
                                Text("نسخ دعوة العامل")
                            }
                        }
                    }
                }
                Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text("مهمة حساب الأعداد الأولية", fontWeight = FontWeight.SemiBold)
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            OutlinedTextField(value = startText, onValueChange = { startText = it }, modifier = Modifier.weight(1f), label = { Text("من") })
                            OutlinedTextField(value = stopText, onValueChange = { stopText = it }, modifier = Modifier.weight(1f), label = { Text("إلى (غير شامل)") })
                        }
                        Button(
                            onClick = {
                                scope.launch {
                                    busy = true
                                    status = "جارٍ الاتصال بالعقدة والتحقق من شهادة TLS..."
                                    val result = withContext(Dispatchers.IO) {
                                        runCatching {
                                            val invite = PeerInvite.parse(inviteText)
                                            RhexoraProtocol.countPrimes(invite, startText.toLong(), stopText.toLong())
                                        }
                                    }
                                    status = result.fold(
                                        onSuccess = { "اكتملت المهمة بنجاح:\n${it.toString(2)}" },
                                        onFailure = { "فشلت المهمة: ${it.message ?: "خطأ غير معروف"}" },
                                    )
                                    busy = false
                                }
                            },
                            enabled = !busy,
                            modifier = Modifier.fillMaxWidth(),
                        ) { Text(if (busy) "جارٍ التنفيذ..." else "اتصال وتنفيذ المهمة") }
                    }
                }
                Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(14.dp)) {
                        Text("الحالة والنتيجة", fontWeight = FontWeight.SemiBold)
                        Spacer(Modifier.height(6.dp))
                        Text(status)
                    }
                }
                Text(
                    "تنبيه: عامل Android يدعم حاليًا math.count_primes فقط ويعمل أثناء تشغيل خدمة أمامية بدأتها يدويًا. الدعوة تحتوي رمز دخول سريًا وبصمة شهادة TLS؛ لا تنشرها علنًا. يلزم إبقاء العامل نشطًا واتصال الإنترنت أو الشبكة المحلية متاحًا؛ عند استخدام Relay يجب أن يكون خادم Relay متاحًا.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}
