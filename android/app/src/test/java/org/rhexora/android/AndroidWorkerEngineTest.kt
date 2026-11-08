package org.rhexora.android

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
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
}
