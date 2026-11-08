"""Safe, built-in task handlers. Remote code execution is intentionally absent."""

from __future__ import annotations

import re
from typing import Any

from .errors import RemoteTaskError

_WORD_RE = re.compile(r"\w+", re.UNICODE)


def _is_prime(number: int) -> bool:
    if number < 2:
        return False
    if number in (2, 3):
        return True
    if number % 2 == 0 or number % 3 == 0:
        return False
    divisor = 5
    while divisor * divisor <= number:
        if number % divisor == 0 or number % (divisor + 2) == 0:
            return False
        divisor += 6
    return True


def execute_task(task_type: str, payload: dict[str, Any], *, max_items: int, media_root: str | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise RemoteTaskError("Task input must be a JSON object")

    if task_type == "math.sum_ints":
        values = payload.get("values")
        if not isinstance(values, list):
            raise RemoteTaskError("math.sum_ints requires input.values to be a list")
        if len(values) > max_items:
            raise RemoteTaskError(f"Too many values; this node allows at most {max_items}")
        total = 0
        for index, value in enumerate(values):
            # bool is a subclass of int in Python, but is not accepted as a number here.
            if isinstance(value, bool) or not isinstance(value, int):
                raise RemoteTaskError(f"values[{index}] must be an integer")
            if value.bit_length() > 4096:
                raise RemoteTaskError(f"values[{index}] is too large")
            total += value
        return {"sum": total, "count": len(values)}

    if task_type == "math.count_primes":
        start = payload.get("start")
        stop = payload.get("stop")
        if isinstance(start, bool) or not isinstance(start, int):
            raise RemoteTaskError("math.count_primes requires integer input.start")
        if isinstance(stop, bool) or not isinstance(stop, int):
            raise RemoteTaskError("math.count_primes requires integer input.stop")
        if start < 0 or stop <= start:
            raise RemoteTaskError("Prime range must satisfy 0 <= start < stop")
        # A fixed upper bound limits CPU work even if a node is misconfigured.
        if stop - start > min(max_items * 2, 200_000):
            raise RemoteTaskError("Prime range is larger than this worker allows")
        count = sum(1 for value in range(start, stop) if _is_prime(value))
        return {"count": count, "start": start, "stop": stop}

    if task_type == "media.video.transcode":
        if media_root is None:
            raise RemoteTaskError("Video processing is not enabled on this worker")
        from .video import transcode_video
        return transcode_video(payload, media_root)

    if task_type == "text.analyze":
        text = payload.get("text")
        if not isinstance(text, str):
            raise RemoteTaskError("text.analyze requires input.text to be a string")
        if len(text) > max_items * 64:
            raise RemoteTaskError("Text exceeds this node's configured character limit")
        return {
            "characters": len(text),
            "words": len(_WORD_RE.findall(text)),
            "lines": 0 if not text else len(text.splitlines()),
        }

    raise RemoteTaskError(f"Unsupported task type: {task_type}")
