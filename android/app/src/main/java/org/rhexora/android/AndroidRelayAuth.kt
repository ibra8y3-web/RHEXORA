package org.rhexora.android

import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

internal object AndroidRelayAuth {
    fun makeProof(token: String, action: String, nodeId: String, nonce: String): String {
        require(token.length >= 24 && action == "worker" && nodeId.isNotBlank())
        require(nonce.matches(Regex("[0-9a-f]{32,128}")))
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(token.toByteArray(Charsets.UTF_8), "HmacSHA256"))
        return mac.doFinal("$action|$nodeId|$nonce".toByteArray(Charsets.UTF_8))
            .joinToString("") { "%02x".format(it) }
    }
}
