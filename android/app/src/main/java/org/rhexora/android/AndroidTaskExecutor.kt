package org.rhexora.android

import org.json.JSONObject

/** Bounded built-in tasks only; Android workers never execute caller-supplied code. */
internal object AndroidTaskExecutor {
    private const val MAX_ITEMS = 100_000
    private const val MAX_PRIME_RANGE = 200_000L
    private const val MAX_TEXT_CODE_POINTS = MAX_ITEMS * 64

    fun validate(taskType: String, input: JSONObject) {
        when (taskType) {
            "math.count_primes" -> primeBounds(input)
            "math.sum_ints" -> checkedSum(integerValues(input))
            "text.analyze" -> textValue(input)
            else -> throw IllegalArgumentException("هذه المهمة غير مدعومة على عامل Android")
        }
    }

    fun execute(taskType: String, input: JSONObject): JSONObject {
        validate(taskType, input)
        return when (taskType) {
            "math.count_primes" -> {
                val (start, stop) = primeBounds(input)
                JSONObject().put("count", AndroidWorkerEngine.countPrimes(start, stop))
                    .put("start", start).put("stop", stop)
            }
            "math.sum_ints" -> {
                val values = integerValues(input)
                val total = checkedSum(values)
                JSONObject().put("sum", total).put("count", values.size)
            }
            "text.analyze" -> analyzeText(textValue(input))
            else -> throw IllegalArgumentException("هذه المهمة غير مدعومة على عامل Android")
        }
    }

    private fun primeBounds(input: JSONObject): Pair<Long, Long> {
        val start = exactLong(input.opt("start"))
            ?: throw IllegalArgumentException("بداية النطاق يجب أن تكون عددًا صحيحًا")
        val stop = exactLong(input.opt("stop"))
            ?: throw IllegalArgumentException("نهاية النطاق يجب أن تكون عددًا صحيحًا")
        require(start >= 0 && stop > start && stop - start <= MAX_PRIME_RANGE && stop <= 1_000_000_000_000L) {
            "نطاق الحساب خارج الحدود المسموحة"
        }
        return start to stop
    }

    private fun integerValues(input: JSONObject): List<Long> {
        val values = input.optJSONArray("values")
            ?: throw IllegalArgumentException("مهمة الجمع تحتاج قائمة أعداد صحيحة")
        require(values.length() <= MAX_ITEMS) { "عدد القيم يتجاوز الحد المسموح ($MAX_ITEMS)" }
        val integers = ArrayList<Long>(values.length())
        for (index in 0 until values.length()) {
            val value = values.get(index)
            val integer = when (value) {
                is Byte, is Short, is Int, is Long -> value.toString().toLong()
                else -> throw IllegalArgumentException("القيمة رقم ${index + 1} ليست عددًا صحيحًا")
            }
            integers.add(integer)
        }
        return integers
    }

    private fun checkedSum(values: List<Long>): Long {
        var total = 0L
        try {
            values.forEach { total = Math.addExact(total, it) }
        } catch (_: ArithmeticException) {
            throw IllegalArgumentException("مجموع القيم يتجاوز مدى 64-بت")
        }
        return total
    }

    private fun exactLong(value: Any?): Long? = when (value) {
        is Byte, is Short, is Int, is Long -> value.toString().toLongOrNull()
        else -> null
    }

    private fun textValue(input: JSONObject): String {
        val text = input.opt("text") as? String
            ?: throw IllegalArgumentException("مهمة تحليل النص تحتاج قيمة نصية")
        val codePoints = text.codePointCount(0, text.length)
        require(codePoints <= MAX_TEXT_CODE_POINTS) { "النص يتجاوز الحد المسموح ($MAX_TEXT_CODE_POINTS حرفًا)" }
        return text
    }

    private fun analyzeText(text: String): JSONObject {
        val codePoints = text.codePointCount(0, text.length)
        val words = WORD_PATTERN.findAll(text).count()
        return JSONObject()
            .put("characters", codePoints)
            .put("words", words)
            .put("lines", pythonCompatibleLineCount(text))
    }

    /** Match Python str.splitlines() for the Unicode line boundaries it recognizes. */
    private fun pythonCompatibleLineCount(text: String): Int {
        if (text.isEmpty()) return 0
        var breaks = 0
        var index = 0
        while (index < text.length) {
            when (text[index]) {
                '\r' -> {
                    breaks++
                    if (index + 1 < text.length && text[index + 1] == '\n') index++
                }
                '\n', '\u000b', '\u000c', '\u001c', '\u001d', '\u001e', '\u0085', '\u2028', '\u2029' -> breaks++
            }
            index++
        }
        val endsWithLineBreak = text.last() in LINE_BREAKS ||
            (text.length >= 2 && text[text.length - 2] == '\r' && text.last() == '\n')
        return breaks + if (endsWithLineBreak) 0 else 1
    }

    private val LINE_BREAKS = setOf('\r', '\n', '\u000b', '\u000c', '\u001c', '\u001d', '\u001e', '\u0085', '\u2028', '\u2029')
    private val WORD_PATTERN = Regex("[\\p{L}\\p{N}_]+")
}
