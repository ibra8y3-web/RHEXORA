package org.rhexora.android

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class RhexoraCanonicalTest {
    @Test fun canonicalJsonSortsKeysRecursively() {
        val value = JSONObject("""{"z":1,"a":{"y":2,"b":3}}""")
        assertEquals("{\"a\":{\"b\":3,\"y\":2},\"z\":1}", RhexoraProtocol.canonical(value))
    }

    @Test fun relayHmacMatchesPythonProtocolVector() {
        val proof = RhexoraProtocol.relayProof(
            "0123456789abcdef0123456789abcdef",
            "connect",
            "node-1",
            "0123456789abcdef",
        )
        assertEquals("ca099b7f2729a006ca6e3010be46313b6aa870e0316a55eefb5383e1d232bcfa", proof)
    }

    @Test fun inviteRejectsPartiallyConfiguredRelay() {
        val invite = JSONObject()
            .put("invite_version", 1)
            .put("name", "worker")
            .put("host", "worker.example.com")
            .put("port", 47821)
            .put("tls_fingerprint", "a".repeat(64))
            .put("token", "x".repeat(32))
            .put("node_id", "node-1")
            .put("relay_host", "relay.example.com")
            .put("relay_port", 47825)
        assertThrows(IllegalArgumentException::class.java) {
            PeerInvite.parse(invite.toString())
        }
    }

    @Test fun inviteParsesCompleteRelayConfiguration() {
        val invite = JSONObject()
            .put("invite_version", 1)
            .put("name", "worker")
            .put("host", "worker.example.com")
            .put("port", 47821)
            .put("tls_fingerprint", "a".repeat(64))
            .put("token", "x".repeat(32))
            .put("node_id", "node-1")
            .put("relay_host", "relay.example.com")
            .put("relay_port", 47825)
            .put("relay_token", "r".repeat(32))
        val parsed = PeerInvite.parse(invite.toString())
        assertEquals("relay.example.com", parsed.relayHost)
        assertEquals(47825, parsed.relayPort)
        assertEquals("r".repeat(32), parsed.relayToken)
    }
}
