package org.rhexora.android

import kotlin.math.sqrt

/** Bounded segmented sieve used by the Android worker; no unbounded trial division. */
internal object AndroidWorkerEngine {
    private const val MAX_RANGE = 200_000L
    private const val MAX_STOP = 1_000_000_000_000L

    fun countPrimes(start: Long, stop: Long): Long {
        require(start >= 0 && stop > start && stop - start <= MAX_RANGE && stop <= MAX_STOP) {
            "نطاق المهمة غير مسموح"
        }

        val root = sqrt((stop - 1).toDouble()).toInt()
        val baseComposite = BooleanArray(root + 1)
        val basePrimes = ArrayList<Int>()
        if (root >= 2) {
            for (candidate in 2..root) {
                if (!baseComposite[candidate]) {
                    basePrimes.add(candidate)
                    val square = candidate.toLong() * candidate
                    if (square <= root.toLong()) {
                        var multiple = square.toInt()
                        while (multiple <= root) {
                            baseComposite[multiple] = true
                            multiple += candidate
                        }
                    }
                }
            }
        }

        val composite = BooleanArray((stop - start).toInt())
        for (prime in basePrimes) {
            val p = prime.toLong()
            val firstAtOrAfterStart = ((start + p - 1L) / p) * p
            var multiple = maxOf(p * p, firstAtOrAfterStart)
            while (multiple < stop) {
                composite[(multiple - start).toInt()] = true
                multiple += p
            }
        }

        var count = 0L
        for (offset in composite.indices) {
            val value = start + offset
            if (value >= 2L && !composite[offset]) count++
        }
        return count
    }
}
