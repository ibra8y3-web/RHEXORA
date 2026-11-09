package org.rhexora.android

import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedInputStream
import java.io.BufferedOutputStream
import java.io.DataInputStream
import java.io.DataOutputStream
import java.net.InetSocketAddress
import java.net.Socket
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec
import java.security.MessageDigest
import java.security.cert.X509Certificate
import java.util.UUID
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSocket
import javax.net.ssl.TrustManager
import javax.net.ssl.X509TrustManager

private const val PROTOCOL_VERSION = 1
private const val MAX_FRAME_BYTES = 8 *  1024 * 1024

/** A private invite is a bearer credential; never log or share its token. */
data class PeerInvite(
    val name: String,
    val host: String,
    val port: Int,
    val fingerprint: String,
    val token: String,
    val nodeId: String,
    val relayHost: String = "",
    val relayPort: Int = 0,
    val relayToken: String = "",
) {
    companion object {
        fun parse(raw: String): PeerInvite {
            require(raw.toByteArray(Charsets.UTF_8).size <= 32 * 1024) { "ملف الدعوة كبير جدًا" }
            val obj = JSONObject(raw)
            require(obj.optInt("invite_version") == 1) { "إصدار ملف الدعوة غير مدعوم" }
            val invite = PeerInvite(
                name = obj.optString("name", "peer"),
                host = obj.getString("host"),
                port = obj.getInt("port"),
                fingerprint = obj.getString("tls_fingerprint").replace(":", "").lowercase(),
                token = obj.getString("token"),
                nodeId = obj.optString("node_id", ""),
                relayHost = obj.optString("relay_host", ""),
                relayPort = obj.optInt("relay_port", 0),
                relayToken = obj.optString("relay_token", ""),
            )
            require(invite.host.isNotBlank() && invite.host.length <= 253) { "عنوان العقدة غير صالح" }
            require(invite.port in 1..65535) { "منفذ الاتصال غير صالح" }
            require(invite.fingerprint.matches(Regex("[0-9a-f]{64}"))) { "بصمة TLS غير صالحة" }
            require(invite.token.length >= 24) { "رمز الدعوة غير صالح" }
            require(invite.relayHost.isBlank() || invite.relayHost.length <= 253) { "عنوان Relay غير صالح" }
            require((invite.relayHost.isBlank() && invite.relayPort == 0 && invite.relayToken.isBlank()) ||
                (invite.relayHost.isNotBlank() && invite.relayPort in 1..65535 && invite.relayToken.length >= 24 && invite.nodeId.isNotBlank())) {
                "بيانات Relay غير مكتملة أو غير صالحة"
            }
            return invite
        }
    }
}

object RhexoraProtocol {
    fun countPrimes(invite: PeerInvite, start: Long, stop: Long): JSONObject {
        require(start >= 0 && stop > start && stop - start <= 200_000 && stop <= 1_000_000_000_000L) {
            "النطاق يجب ألا يتجاوز 200000 رقم أو 10^12 كحد أعلى"
        }
        val task = JSONObject()
            .put("type", "math.count_primes")
            .put("input", JSONObject().put("start", start).put("stop", stop))
        val response = executeTask(invite, "math.count_primes", task.getJSONObject("input"))
        val result = response.getJSONObject("result")
        return JSONObject()
            .put("worker", invite.name)
            .put("node_id", response.optString("node_id"))
            .put("request_id", response.optString("request_id"))
            .put("prime_count", result.getLong("count"))
            .put("start", result.getLong("start"))
            .put("stop_exclusive", result.getLong("stop"))
            .put("task_hash", response.optString("task_hash"))
    }

    fun executeTask(invite: PeerInvite, taskType: String, input: JSONObject): JSONObject {
        require(taskType in setOf("math.count_primes", "math.sum_ints", "text.analyze")) {
            "نوع المهمة غير متاح في تطبيق Android"
        }
        require(taskType != "math.count_primes" || run {
            val start = input.opt("start")?.toString()?.toLongOrNull()
            val stop = input.opt("stop")?.toString()?.toLongOrNull()
            start != null && stop != null && start >= 0 && stop > start && stop - start <= 200_000 && stop <= 1_000_000_000_000L
        }) { "النطاق يجب ألا يتجاوز 200000 رقم أو 10^12 كحد أعلى" }
        val task = JSONObject().put("type", taskType).put("input", input)
        val requestId = UUID.randomUUID().toString()
        val request = JSONObject()
            .put("kind", "execute")
            .put("protocol_version", PROTOCOL_VERSION)
            .put("request_id", requestId)
            .put("token", invite.token)
            .put("task", task)

        val socket = connectPinnedSocket(invite)
        try {
            socket.soTimeout = 30_000
            val output = DataOutputStream(BufferedOutputStream(socket.outputStream))
            val input = DataInputStream(BufferedInputStream(socket.inputStream))
            val bytes = request.toString().toByteArray(Charsets.UTF_8)
            require(bytes.size in 1..MAX_FRAME_BYTES) { "حجم الطلب غير مسموح" }
            output.writeInt(bytes.size)
            output.write(bytes)
            output.flush()

            val frameSize = input.readInt()
            require(frameSize in 1..MAX_FRAME_BYTES) { "حجم استجابة العقدة غير مسموح" }
            val responseBytes = ByteArray(frameSize)
            input.readFully(responseBytes)
            val response = JSONObject(responseBytes.toString(Charsets.UTF_8))
            require(response.optString("kind") == "result" && response.optInt("protocol_version") == PROTOCOL_VERSION) {
                "استجابة غير متوافقة مع البروتوكول"
            }
            require(response.optString("request_id") == requestId) { "معرّف الطلب في الاستجابة غير مطابق" }
            if (invite.nodeId.isNotBlank()) {
                require(response.optString("node_id") == invite.nodeId) { "هوية العقدة لا تطابق ملف الدعوة" }
            }
            require(response.optBoolean("ok")) { response.optString("error", "العقدة رفضت المهمة") }
            val expectedTaskHash = sha256(canonical(task).toByteArray(Charsets.UTF_8))
            require(response.optString("task_hash") == expectedTaskHash) { "بصمة المهمة غير مطابقة" }
            val result = response.getJSONObject("result")
            val resultHash = sha256(canonical(result).toByteArray(Charsets.UTF_8))
            require(response.optString("result_hash") == resultHash) { "بصمة النتيجة غير مطابقة" }
            return JSONObject()
                .put("worker", invite.name)
                .put("node_id", response.optString("node_id"))
                .put("request_id", requestId)
                .put("task_type", taskType)
                .put("transport", if (invite.relayHost.isBlank()) "direct" else "relay")
                .put("task_hash", expectedTaskHash)
                .put("result_hash", resultHash)
                .put("result", result)
        } finally {
            runCatching { socket.close() }
        }
    }

    private fun connectPinnedSocket(invite: PeerInvite): SSLSocket {
        val trustManager = object : X509TrustManager {
            override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
            override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) {
                throw UnsupportedOperationException("Client certificates are not used")
            }
            override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {
                require(chain.isNotEmpty()) { "الخادم لم يقدم شهادة TLS" }
                val actual = sha256(chain[0].encoded)
                require(MessageDigest.isEqual(actual.toByteArray(), invite.fingerprint.toByteArray())) {
                    "بصمة شهادة TLS لا تطابق ملف الدعوة"
                }
            }
        }
        val context = SSLContext.getInstance("TLS")
        context.init(null, arrayOf<TrustManager>(trustManager), null)

        if (invite.relayHost.isBlank()) {
            val direct = context.socketFactory.createSocket() as SSLSocket
            try {
                direct.connect(InetSocketAddress(invite.host, invite.port), 10_000)
                direct.soTimeout = 30_000
                direct.startHandshake()
                return direct
            } catch (error: Exception) {
                runCatching { direct.close() }
                throw error
            }
        }

        val tunnel = Socket()
        try {
            tunnel.connect(InetSocketAddress(invite.relayHost, invite.relayPort), 10_000)
            tunnel.soTimeout = 35_000
            val input = tunnel.getInputStream()
            val greeting = readAsciiLine(input)
            val challenge = readAsciiLine(input)
            require(greeting == "RHEXORA-RELAY/1" && challenge.startsWith("CHALLENGE ")) {
                "مصافحة Relay غير متوافقة"
            }
            val nonce = challenge.removePrefix("CHALLENGE ")
            require(nonce.matches(Regex("[0-9a-f]{32,128}"))) { "تحدي Relay غير صالح" }
            val proof = relayProof(invite.relayToken, "connect", invite.nodeId, nonce)
            val auth = JSONObject()
                .put("action", "connect")
                .put("node_id", invite.nodeId)
                .put("timestamp", System.currentTimeMillis() / 1000L)
                .put("proof", proof)
                .toString() + "\n"
            tunnel.getOutputStream().write(auth.toByteArray(Charsets.UTF_8))
            tunnel.getOutputStream().flush()
            val status = readAsciiLine(input)
            require(status == "READY") {
                when (status) {
                    "NO_WORKER" -> "لا توجد عقدة متصلة بالـ Relay حاليًا"
                    else -> "رفض Relay الاتصال: $status"
                }
            }

            val tls = context.socketFactory.createSocket(tunnel, invite.host, invite.port, true) as SSLSocket
            tls.soTimeout = 30_000
            try {
                tls.startHandshake()
                return tls
            } catch (error: Exception) {
                runCatching { tls.close() }
                throw error
            }
        } catch (error: Exception) {
            runCatching { tunnel.close() }
            throw error
        }
    }

    /** Read one bounded ASCII control line without buffering bytes from the following TLS stream. */
    private fun readAsciiLine(input: java.io.InputStream, limit: Int = 4096): String {
        val bytes = java.io.ByteArrayOutputStream()
        while (bytes.size() < limit) {
            val value = input.read()
            if (value < 0) throw java.io.EOFException("انقطع اتصال Relay أثناء المصافحة")
            if (value == 10) return bytes.toString(Charsets.US_ASCII.name()).trimEnd('\r')
            bytes.write(value)
        }
        throw java.io.IOException("سطر تحكم Relay أطول من الحد المسموح")
    }

    internal fun relayProof(token: String, action: String, nodeId: String, nonce: String): String {
        require(token.length >= 24 && action in setOf("worker", "connect") && nodeId.isNotBlank())
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(token.toByteArray(Charsets.UTF_8), "HmacSHA256"))
        val message = "$action|$nodeId|$nonce".toByteArray(Charsets.UTF_8)
        return mac.doFinal(message).joinToString("") { "%02x".format(it) }
    }

    internal fun sha256(value: ByteArray): String = MessageDigest.getInstance("SHA-256")
        .digest(value).joinToString("") { "%02x".format(it) }

    /** Python's canonical_json sorts every object key recursively before hashing. */
    internal fun canonical(value: Any?): String = when (value) {
        JSONObject.NULL, null -> "null"
        is JSONObject -> {
            val keys = value.keys().asSequence().toList().sorted()
            keys.joinToString(separator = ",", prefix = "{", postfix = "}") { key ->
                JSONObject.quote(key) + ":" + canonical(value.get(key))
            }
        }
        is JSONArray -> (0 until value.length()).joinToString(separator = ",", prefix = "[", postfix = "]") { canonical(value.get(it)) }
        is String -> JSONObject.quote(value)
        is Number, is Boolean -> value.toString()
        else -> error("Unsupported JSON value: ${value::class.java.simpleName}")
    }
}
