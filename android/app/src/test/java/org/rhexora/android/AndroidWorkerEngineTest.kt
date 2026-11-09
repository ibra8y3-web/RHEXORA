package org.rhexora.android

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.json.JSONObject
import org.junit.Test

class AndroidWorkerEngineTest {
    @Test fun countsPrimesForHalfOpenRange() {
        assertEquals(25L, AndroidWorkerEngine.countPrimes(0, 100))
        assertEquals(168L, AndroidWorkerEngine.countPrimes(1, 1000))
        // Exercises segmented marking at a high range without trial-dividing each value.
        assertEquals(7_213L, AndroidWorkerEngine.countPrimes(999_999_800_000L, 1_000_000_000_000L))
    }

    @Test fun androidWorkerRelayProofMatchesPythonProtocol() {
        assertEquals(
            "a39ff78ce44253e3c486db480cce4c349cac9e25bc24b965023253a3bb791966",
            AndroidRelayAuth.makeProof(
                "0123456789abcdef0123456789abcdef", "worker", "node-1", "0123456789abcdef0123456789abcdef0123456789abcdef",
            ),
        )
    }

    @Test fun rejectsInvalidOrExcessiveRanges() {
        assertThrows(IllegalArgumentException::class.java) { AndroidWorkerEngine.countPrimes(-1, 10) }
        assertThrows(IllegalArgumentException::class.java) { AndroidWorkerEngine.countPrimes(10, 10) }
        assertThrows(IllegalArgumentException::class.java) { AndroidWorkerEngine.countPrimes(0, 200_001) }
        assertThrows(IllegalArgumentException::class.java) { AndroidWorkerEngine.countPrimes(0, 1_000_000_000_001L) }
    }

    @Test fun androidTaskExecutorSumsIntegersExactly() {
        val task = JSONObject().put("values", org.json.JSONArray().put(5).put(-2).put(7))
        val result = AndroidTaskExecutor.execute("math.sum_ints", task)
        assertEquals("10", result.get("sum").toString())
        assertEquals(3, result.getInt("count"))
    }

    @Test fun androidTaskExecutorAnalyzesArabicTextAndPythonLineBreaks() {
        val result = AndroidTaskExecutor.execute("text.analyze", JSONObject().put("text", "أهلًا بالعالم\nRHEXORA\n"))
        assertEquals(4, result.getInt("words"))
        assertEquals(2, result.getInt("lines"))
        assertEquals("أهلًا بالعالم\nRHEXORA\n".codePointCount(0, "أهلًا بالعالم\nRHEXORA\n".length), result.getInt("characters"))
    }

    @Test fun androidTaskExecutorAcceptsEmptyText() {
        val result = AndroidTaskExecutor.execute("text.analyze", JSONObject().put("text", ""))
        assertEquals(0, result.getInt("characters"))
        assertEquals(0, result.getInt("words"))
        assertEquals(0, result.getInt("lines"))
    }

    @Test fun androidTaskExecutorRejectsUnsupportedTypesAndNonIntegerValues() {
        assertThrows(IllegalArgumentException::class.java) {
            AndroidTaskExecutor.validate("media.video.transcode", JSONObject())
        }
        assertThrows(IllegalArgumentException::class.java) {
            AndroidTaskExecutor.validate("math.sum_ints", JSONObject().put("values", org.json.JSONArray().put(1.5)))
        }
        assertThrows(IllegalArgumentException::class.java) {
            AndroidTaskExecutor.validate(
                "math.sum_ints",
                JSONObject().put("values", org.json.JSONArray().put(Long.MAX_VALUE).put(1)),
            )
        }
    }
}
