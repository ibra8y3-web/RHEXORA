package org.rhexora.android

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.ContentValues
import android.content.Context
import android.content.pm.ServiceInfo
import android.content.Intent
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import android.os.Build
import android.os.IBinder
import android.util.Log
import org.bouncycastle.asn1.x500.X500Name
import org.bouncycastle.cert.jcajce.JcaX509CertificateConverter
import org.bouncycastle.cert.jcajce.JcaX509v3CertificateBuilder
import org.bouncycastle.operator.jcajce.JcaContentSignerBuilder
import org.json.JSONObject
import java.io.BufferedInputStream
import java.io.BufferedOutputStream
import java.io.DataInputStream
import java.io.DataOutputStream
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.math.BigInteger
import java.net.Inet4Address
import java.net.InetAddress
import java.net.NetworkInterface
import java.net.ServerSocket
import java.net.Socket
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.MessageDigest
import java.security.SecureRandom
import java.security.cert.X509Certificate
import java.util.Date
import java.util.UUID
import java.util.concurrent.SynchronousQueue
import java.util.concurrent.ExecutorService
import java.util.concurrent.ThreadPoolExecutor
import java.util.concurrent.TimeUnit
import javax.net.ssl.KeyManagerFactory
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLServerSocket
import javax.net.ssl.SSLSocket

private const val WORKER_CHANNEL = "rhexora_worker"
private const val WORKER_NOTIFICATION_ID = 47821
private const val WORKER_PORT = 47821
private const val WORKER_MAX_FRAME = 8 * 1024 * 1024
private const val WORKER_ACTION_START = "org.rhexora.android.worker.START"

/** The worker identity stays inside the app sandbox; only the invite is shown to the owner. */
private data class AndroidWorkerIdentity(
    val nodeId: String,
    val token: String,
    val fingerprint: String,
    val relayToken: String,
    val sslContext: SSLContext,
) {
    fun invite(host: String, port: Int, relayHost: String, relayPort: Int): String {
        val invite = JSONObject()
        .put("invite_version", 1)
        .put("name", "RHEXORA Android Worker")
        .put("host", host)
        .put("port", port)
        .put("tls_fingerprint", fingerprint)
        .put("token", token)
        .put("node_id", nodeId)
        .put("max_items", 100_000)
        if (relayHost.isNotBlank()) {
            invite.put("relay_host", relayHost)
                .put("relay_port", relayPort)
                .put("relay_token", relayToken)
        }
        return invite.toString(2)
    }
}

private object AndroidWorkerIdentityStore {
    private const val PREFS = "rhexora_worker_identity"
    private const val PASSWORD_KEY = "keystore_password"
    private const val NODE_ID_KEY = "node_id"
    private const val TOKEN_KEY = "auth_token"
    private const val RELAY_TOKEN_KEY = "relay_token"
    private const val KEY_ALIAS = "rhexora-worker"
    private const val KEYSTORE_FILE = "rhexora-worker-identity.p12"

    @Synchronized
    fun load(context: Context): AndroidWorkerIdentity {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        var password = prefs.getString(PASSWORD_KEY, null)
        var nodeId = prefs.getString(NODE_ID_KEY, null)
        var token = prefs.getString(TOKEN_KEY, null)
        var relayToken = prefs.getString(RELAY_TOKEN_KEY, null)
        val file = File(context.filesDir, KEYSTORE_FILE)
        if (password == null || nodeId == null || token == null || relayToken == null || !file.isFile) {
            password = randomToken(48)
            nodeId = UUID.randomUUID().toString()
            token = randomToken(48)
            relayToken = randomToken(48)
            createKeyStore(file, password)
            check(prefs.edit()
                .putString(PASSWORD_KEY, password)
                .putString(NODE_ID_KEY, nodeId)
                .putString(TOKEN_KEY, token)
                .putString(RELAY_TOKEN_KEY, relayToken)
                .commit()) { "تعذر حفظ هوية عامل Android" }
        }

        val finalPassword = requireNotNull(password)
        val finalNodeId = requireNotNull(nodeId)
        val finalToken = requireNotNull(token)
        val finalRelayToken = requireNotNull(relayToken)
        val keyStore = KeyStore.getInstance("PKCS12")
        FileInputStream(file).use { keyStore.load(it, finalPassword.toCharArray()) }
        val certificate = keyStore.getCertificate(KEY_ALIAS) as X509Certificate
        val keyManagerFactory = KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm())
        keyManagerFactory.init(keyStore, finalPassword.toCharArray())
        val ssl = SSLContext.getInstance("TLS")
        ssl.init(keyManagerFactory.keyManagers, null, SecureRandom())
        val fingerprint = sha256(certificate.encoded)
        return AndroidWorkerIdentity(finalNodeId, finalToken, fingerprint, finalRelayToken, ssl)
    }

    private fun createKeyStore(file: File, password: String) {
        val generator = KeyPairGenerator.getInstance("RSA")
        generator.initialize(2048, SecureRandom())
        val pair = generator.generateKeyPair()
        val now = System.currentTimeMillis()
        val subject = X500Name("CN=RHEXORA Android Worker")
        val builder = JcaX509v3CertificateBuilder(
            subject,
            BigInteger(160, SecureRandom()).abs().max(BigInteger.ONE),
            Date(now - 60_000),
            Date(now + 3650L * 24 * 60 * 60 * 1000),
            subject,
            pair.public,
        )
        val signer = JcaContentSignerBuilder("SHA256withRSA").build(pair.private)
        val certificate = JcaX509CertificateConverter().getCertificate(builder.build(signer))
        certificate.checkValidity(Date())
        certificate.verify(pair.public)
        val store = KeyStore.getInstance("PKCS12")
        store.load(null, password.toCharArray())
        store.setKeyEntry(KEY_ALIAS, pair.private, password.toCharArray(), arrayOf(certificate))
        FileOutputStream(file).use { store.store(it, password.toCharArray()) }
        file.setReadable(false, false)
        file.setReadable(true, true)
        file.setWritable(false, false)
        file.setWritable(true, true)
    }

    private fun randomToken(byteCount: Int): String {
        val bytes = ByteArray(byteCount)
        SecureRandom().nextBytes(bytes)
        return android.util.Base64.encodeToString(bytes, android.util.Base64.URL_SAFE or android.util.Base64.NO_WRAP or android.util.Base64.NO_PADDING)
    }

    private fun sha256(bytes: ByteArray): String = MessageDigest.getInstance("SHA-256")
        .digest(bytes).joinToString("") { "%02x".format(it) }
}

/** Small persistent idempotency ledger for the Android worker. */
private class AndroidWorkerLedger(context: Context) : SQLiteOpenHelper(context, "rhexora-worker-ledger.db", null, 1) {
    override fun onCreate(db: SQLiteDatabase) {
        db.execSQL("CREATE TABLE requests (request_id TEXT PRIMARY KEY, task_hash TEXT NOT NULL, state TEXT NOT NULL, response TEXT, created_ms INTEGER NOT NULL)")
    }

    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) = Unit

    @Synchronized
    fun recoverInterrupted() {
        // The previous Android service process is no longer executing its in-flight
        // requests when a fresh service start reaches this method. Mark those rows
        // reclaimable so a requester retry is not stuck forever in the "running" state.
        writableDatabase.update(
            "requests",
            ContentValues().apply { put("state", "interrupted") },
            "state = ?",
            arrayOf("running"),
        )
    }

    @Synchronized
    fun claim(requestId: String, taskHash: String): Pair<String, String?> {
        val db = writableDatabase
        db.beginTransaction()
        try {
            db.rawQuery("SELECT task_hash, state, response FROM requests WHERE request_id = ?", arrayOf(requestId)).use { cursor ->
                if (cursor.moveToFirst()) {
                    val oldHash = cursor.getString(0)
                    val state = cursor.getString(1)
                    val response = if (cursor.isNull(2)) null else cursor.getString(2)
                    db.setTransactionSuccessful()
                    return when {
                        oldHash != taskHash -> "conflict" to null
                        state == "completed" && response != null -> "completed" to response
                        state == "interrupted" -> {
                            val retry = ContentValues().apply {
                                put("state", "running")
                                putNull("response")
                                put("created_ms", System.currentTimeMillis())
                            }
                            db.update("requests", retry, "request_id = ?", arrayOf(requestId))
                            "claimed" to null
                        }
                        else -> "running" to null
                    }
                }
            }
            val values = ContentValues().apply {
                put("request_id", requestId)
                put("task_hash", taskHash)
                put("state", "running")
                put("created_ms", System.currentTimeMillis())
            }
            db.insertOrThrow("requests", null, values)
            db.delete("requests", "created_ms < ?", arrayOf((System.currentTimeMillis() - 14L * 24 * 60 * 60 * 1000).toString()))
            db.setTransactionSuccessful()
            return "claimed" to null
        } finally {
            db.endTransaction()
        }
    }

    @Synchronized
    fun complete(requestId: String, response: JSONObject) {
        val values = ContentValues().apply {
            put("state", "completed")
            put("response", response.toString())
        }
        writableDatabase.update("requests", values, "request_id = ?", arrayOf(requestId))
    }
}

/** Explicitly user-started, visible Android foreground worker. It handles a strict task allow-list. */
class RhexoraWorkerService : Service() {
    @Volatile private var running = false
    @Volatile private var listener: SSLServerSocket? = null
    @Volatile private var relayBridge: ServerSocket? = null
    @Volatile private var relayConnection: Socket? = null
    private val activeSockets = java.util.concurrent.ConcurrentHashMap.newKeySet<Socket>()
    private lateinit var pool: ExecutorService
    private lateinit var ledger: AndroidWorkerLedger
    private lateinit var identity: AndroidWorkerIdentity

    override fun onCreate() {
        super.onCreate()
        // The service accept loop and the optional Relay bridge/connect loops are blocking.
        // A SynchronousQueue starts a worker thread for each accepted session instead of
        // queueing it behind those permanent loops; the hard cap rejects excess sessions.
        pool = ThreadPoolExecutor(
            4, 16, 30, TimeUnit.SECONDS, SynchronousQueue(),
            ThreadPoolExecutor.AbortPolicy(),
        )
        ledger = AndroidWorkerLedger(this)
        createNotificationChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val ongoing = notification("جارٍ تشغيل عامل RHEXORA")
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(WORKER_NOTIFICATION_ID, ongoing, ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE)
        } else {
            startForeground(WORKER_NOTIFICATION_ID, ongoing)
        }
        if (!running) {
            runCatching { ledger.recoverInterrupted() }
                .onFailure { Log.e("RHEXORA", "Could not recover interrupted worker tasks", it) }
            running = true
            pool.execute { serve() }
        }
        return START_NOT_STICKY
    }

    private fun serve() {
        try {
            identity = AndroidWorkerIdentityStore.load(this)
            val server = identity.sslContext.serverSocketFactory.createServerSocket(
                WORKER_PORT, 32, InetAddress.getByName("0.0.0.0")
            ) as SSLServerSocket
            server.enabledProtocols = server.supportedProtocols.filter { it == "TLSv1.3" || it == "TLSv1.2" }.toTypedArray()
            server.soTimeout = 1000
            listener = server
            val host = findLanIpv4() ?: "127.0.0.1"
            val prefs = getSharedPreferences("rhexora_worker", MODE_PRIVATE)
            val relayHost = prefs.getString("relay_host", "")?.trim().orEmpty()
            val relayPort = prefs.getString("relay_port", "47825")?.toIntOrNull() ?: 47825
            if (relayHost.isNotBlank()) {
                require(relayHost.length <= 253 && relayPort in 1..65535) { "بيانات Relay غير صالحة" }
                startRelayConnector(relayHost, relayPort)
            }
            val invite = identity.invite(host, server.localPort, relayHost, relayPort)
            activeInProcess = true
            prefs.edit()
                .putString("worker_invite", invite)
                .putBoolean("worker_running", true)
                .putString("worker_message", "العامل نشط على $host:${server.localPort}")
                .commit()
            sendBroadcast(Intent(ACTION_WORKER_READY).setPackage(packageName))
            updateNotification("عامل RHEXORA نشط على $host:${server.localPort}")
            while (running) {
                try {
                    val socket = server.accept() as SSLSocket
                    activeSockets.add(socket)
                    try {
                        pool.execute {
                            try { handle(socket) }
                            finally { activeSockets.remove(socket) }
                        }
                    } catch (_: java.util.concurrent.RejectedExecutionException) {
                        activeSockets.remove(socket)
                        runCatching { socket.close() }
                    }
                } catch (_: java.net.SocketTimeoutException) {
                    // Allows stop requests to be observed without holding a wake lock.
                }
            }
        } catch (error: Exception) {
            Log.e("RHEXORA", "Android worker stopped: ${error.message}")
            getSharedPreferences("rhexora_worker", MODE_PRIVATE).edit()
                .putBoolean("worker_running", false).commit()
            val failureMessage = "تعذر تشغيل العامل: ${error.message ?: "خطأ اتصال"}"
            getSharedPreferences("rhexora_worker", MODE_PRIVATE).edit()
                .putString("worker_message", failureMessage).commit()
            updateNotification(failureMessage)
            stopSelf()
        } finally {
            running = false
            activeInProcess = false
            runCatching { listener?.close() }
            listener = null
        }
    }

    private fun handle(socket: SSLSocket) {
        socket.use { tls ->
            try {
                tls.soTimeout = 30_000
                tls.startHandshake()
                processTlsFrames(tls)
            } catch (error: Exception) {
                Log.w("RHEXORA", "Rejected worker connection: ${error.message}")
            }
        }
    }

    private fun processTlsFrames(tls: SSLSocket) {
        try {
                val input = DataInputStream(BufferedInputStream(tls.inputStream))
                val output = DataOutputStream(BufferedOutputStream(tls.outputStream))
                val frameSize = input.readInt()
                if (frameSize !in 2..WORKER_MAX_FRAME) return
                val frame = ByteArray(frameSize)
                input.readFully(frame)
                val request = JSONObject(frame.toString(Charsets.UTF_8))
                val response = processRequest(request)
                val bytes = RhexoraProtocol.canonical(response).toByteArray(Charsets.UTF_8)
                if (bytes.size !in 2..WORKER_MAX_FRAME) return
                output.writeInt(bytes.size)
                output.write(bytes)
                output.flush()
        } catch (error: Exception) {
            Log.w("RHEXORA", "Rejected worker frame: ${error.message}")
        }
    }

    /** Outbound, independently authenticated Relay connector for phones behind NAT/CGNAT. */
    private fun startRelayConnector(host: String, port: Int) {
        val bridge = ServerSocket(0, 16, InetAddress.getByName("127.0.0.1"))
        bridge.soTimeout = 1000
        relayBridge = bridge
        pool.execute {
            while (running) {
                try {
                    val raw = bridge.accept()
                    activeSockets.add(raw)
                    try {
                        pool.execute {
                            try {
                                val tls = identity.sslContext.socketFactory.createSocket(
                                    raw, "rhexora-android.local", bridge.localPort, true
                                ) as SSLSocket
                                tls.use { secure ->
                                    secure.useClientMode = false
                                    secure.soTimeout = 30_000
                                    secure.startHandshake()
                                    processTlsFrames(secure)
                                }
                            } catch (error: Exception) {
                                Log.w("RHEXORA", "Relayed TLS session rejected: ${error.message}")
                                runCatching { raw.close() }
                            } finally {
                                activeSockets.remove(raw)
                                runCatching { raw.close() }
                            }
                        }
                    } catch (_: java.util.concurrent.RejectedExecutionException) {
                        activeSockets.remove(raw)
                        runCatching { raw.close() }
                    }
                } catch (_: java.net.SocketTimeoutException) {
                    // Poll running state without a wake lock.
                } catch (error: Exception) {
                    if (running) Log.w("RHEXORA", "Local Relay bridge stopped: ${error.message}")
                    break
                }
            }
        }
        pool.execute { relayConnectLoop(host, port, bridge.localPort) }
    }

    private fun relayConnectLoop(host: String, port: Int, localPort: Int) {
        while (running) {
            var remote: Socket? = null
            try {
                remote = Socket()
                remote.connect(java.net.InetSocketAddress(host, port), 15_000)
                relayConnection = remote
                remote.soTimeout = 0
                val input = remote.getInputStream()
                remote.soTimeout = 15_000
                val greeting = readControlLine(input)
                val challenge = readControlLine(input)
                require(greeting == "RHEXORA-RELAY/1" && challenge.startsWith("CHALLENGE ")) { "مصافحة Relay غير متوافقة" }
                val nonce = challenge.removePrefix("CHALLENGE ")
                require(nonce.matches(Regex("[0-9a-f]{32,128}"))) { "تحدي Relay غير صالح" }
                val proof = AndroidRelayAuth.makeProof(identity.relayToken, "worker", identity.nodeId, nonce)
                val auth = JSONObject()
                    .put("action", "worker")
                    .put("node_id", identity.nodeId)
                    .put("timestamp", System.currentTimeMillis() / 1000L)
                    .put("proof", proof)
                    .toString() + "\n"
                remote.getOutputStream().write(auth.toByteArray(Charsets.UTF_8))
                remote.getOutputStream().flush()
                remote.soTimeout = 35_000
                val session = readControlLine(input)
                if (session != "SESSION") throw java.io.IOException("Relay session was not paired: $session")
                remote.soTimeout = 0
                val local = Socket("127.0.0.1", localPort)
                relayConnection = remote
                bridgeStreams(remote, local)
            } catch (error: Exception) {
                if (running) Log.w("RHEXORA", "Relay tunnel unavailable; reconnecting: ${error.message}")
            } finally {
                relayConnection = null
                runCatching { remote?.close() }
            }
            if (running) try { Thread.sleep(2000) } catch (_: InterruptedException) { return }
        }
    }

    private fun bridgeStreams(remote: Socket, local: Socket) {
        // Two dedicated pumps are required: the caller itself is one of the bounded
        // service threads, so submitting both pumps to the same saturated pool can deadlock.
        val down = Thread({ copyStream(remote.getInputStream(), local.getOutputStream()) }, "rhexora-relay-down")
        val up = Thread({ copyStream(local.getInputStream(), remote.getOutputStream()) }, "rhexora-relay-up")
        down.start()
        up.start()
        try {
            down.join()
        } finally {
            runCatching { remote.close() }
            runCatching { local.close() }
            runCatching { up.join(2_000) }
        }
    }

    private fun copyStream(input: java.io.InputStream, output: java.io.OutputStream) {
        val buffer = ByteArray(64 * 1024)
        try {
            while (running) {
                val count = input.read(buffer)
                if (count < 0) break
                output.write(buffer, 0, count)
                output.flush()
            }
        } catch (_: Exception) {
            // Either endpoint closing the opaque stream is a normal end-of-session event.
        }
    }

    private fun readControlLine(input: java.io.InputStream, limit: Int = 4096): String {
        val bytes = java.io.ByteArrayOutputStream()
        while (bytes.size() < limit) {
            val value = input.read()
            if (value < 0) throw java.io.EOFException("انقطع اتصال Relay أثناء المصافحة")
            if (value == 10) return bytes.toString(Charsets.US_ASCII.name()).trimEnd('\r')
            bytes.write(value)
        }
        throw java.io.IOException("سطر تحكم Relay تجاوز الحد المسموح")
    }

    private fun processRequest(request: JSONObject): JSONObject {
        val requestId = request.optString("request_id", "")
        val base = JSONObject()
            .put("kind", "result")
            .put("protocol_version", 1)
            .put("request_id", requestId)
            .put("node_id", identity.nodeId)
        if (request.optString("kind") != "execute" || request.optInt("protocol_version") != 1 || requestId.length !in 1..80) {
            return base.put("ok", false).put("error", "طلب غير متوافق مع البروتوكول")
        }
        val providedToken = request.optString("token", "")
        if (!MessageDigest.isEqual(providedToken.toByteArray(), identity.token.toByteArray())) {
            return base.put("ok", false).put("error", "فشل التحقق من رمز الدخول")
        }
        val task = request.optJSONObject("task")
            ?: return base.put("ok", false).put("error", "بيانات المهمة غير صالحة")
        val taskType = task.optString("type")
        val input = task.optJSONObject("input")
            ?: return base.put("ok", false).put("error", "مدخلات المهمة غير صالحة")
        try {
            AndroidTaskExecutor.validate(taskType, input)
        } catch (error: IllegalArgumentException) {
            return base.put("ok", false).put("error", error.message ?: "مدخلات المهمة غير صالحة")
        }
        val taskHash = RhexoraProtocol.sha256(RhexoraProtocol.canonical(task).toByteArray(Charsets.UTF_8))
        val claim = ledger.claim(requestId, taskHash)
        when (claim.first) {
            "conflict" -> return base.put("ok", false).put("error", "معرّف الطلب مستخدم لمهمة أخرى")
            "running" -> return base.put("ok", false).put("retryable", true).put("error", "المهمة ما زالت قيد التنفيذ")
            "completed" -> return JSONObject(claim.second ?: "{}")
            "claimed" -> Unit
            else -> return base.put("ok", false).put("error", "تعذر حجز المهمة")
        }
        val result = AndroidTaskExecutor.execute(taskType, input)
        val response = base
            .put("ok", true)
            .put("task_hash", taskHash)
            .put("result", result)
            .put("result_hash", RhexoraProtocol.sha256(RhexoraProtocol.canonical(result).toByteArray(Charsets.UTF_8)))
        ledger.complete(requestId, response)
        return response
    }

    private fun findLanIpv4(): String? {
        return try {
            NetworkInterface.getNetworkInterfaces().toList()
                .sortedBy { if (it.name.startsWith("wlan", ignoreCase = true) || it.name.startsWith("eth", ignoreCase = true)) 0 else 1 }
                .asSequence()
                .filter { it.isUp && !it.isLoopback }
                .flatMap { it.inetAddresses.toList().asSequence() }
                .filterIsInstance<Inet4Address>()
                .firstOrNull { !it.isLoopbackAddress && !it.isLinkLocalAddress }
                ?.hostAddress
        } catch (_: Exception) {
            null
        }
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val manager = getSystemService(NotificationManager::class.java)
            manager.createNotificationChannel(NotificationChannel(
                WORKER_CHANNEL, "RHEXORA worker", NotificationManager.IMPORTANCE_LOW
            ).apply { description = "حالة عامل الحوسبة الذي شغله المستخدم" })
        }
    }

    private fun notification(message: String): Notification {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            Notification.Builder(this, WORKER_CHANNEL)
                .setSmallIcon(android.R.drawable.stat_sys_upload_done)
                .setContentTitle("RHEXORA")
                .setContentText(message)
                .setOngoing(true)
                .build()
        } else {
            @Suppress("DEPRECATION")
            Notification.Builder(this)
                .setSmallIcon(android.R.drawable.stat_sys_upload_done)
                .setContentTitle("RHEXORA")
                .setContentText(message)
                .setOngoing(true)
                .build()
        }
    }

    private fun updateNotification(message: String) {
        val manager = getSystemService(NotificationManager::class.java)
        manager.notify(WORKER_NOTIFICATION_ID, notification(message))
    }

    override fun onDestroy() {
        running = false
        activeInProcess = false
        runCatching { listener?.close() }
        runCatching { relayBridge?.close() }
        runCatching { relayConnection?.close() }
        activeSockets.toList().forEach { socket -> runCatching { socket.close() } }
        activeSockets.clear()
        val preferences = getSharedPreferences("rhexora_worker", MODE_PRIVATE)
        val previousMessage = preferences.getString("worker_message", "").orEmpty()
        val editor = preferences.edit().putBoolean("worker_running", false)
        if (!previousMessage.startsWith("تعذر تشغيل العامل:")) editor.putString("worker_message", "العامل متوقف")
        editor.commit()
        pool.shutdownNow()
        runCatching { pool.awaitTermination(2, TimeUnit.SECONDS) }
        runCatching { ledger.close() }
        stopForeground(STOP_FOREGROUND_REMOVE)
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    companion object {
        @Volatile
        var activeInProcess: Boolean = false
            private set

        const val ACTION_READY = "org.rhexora.android.worker.READY"
        private const val ACTION_WORKER_READY = ACTION_READY

        fun start(context: Context) {
            val intent = Intent(context, RhexoraWorkerService::class.java).setAction(WORKER_ACTION_START)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) context.startForegroundService(intent)
            else context.startService(intent)
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, RhexoraWorkerService::class.java))
        }
    }
}
