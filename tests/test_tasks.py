import pytest

from rhexora.errors import RemoteTaskError
from rhexora.tasks import execute_task


def test_sum_ints_returns_real_sum_and_count():
    assert execute_task("math.sum_ints", {"values": [3, -2, 9]}, max_items=10) == {
        "sum": 10,
        "count": 3,
    }


def test_sum_rejects_bool_and_non_integer_values():
    with pytest.raises(RemoteTaskError):
        execute_task("math.sum_ints", {"values": [1, True]}, max_items=10)


def test_sum_enforces_item_limit():
    with pytest.raises(RemoteTaskError, match="Too many"):
        execute_task("math.sum_ints", {"values": [1, 2, 3]}, max_items=2)


def test_text_analyze_counts_text():
    assert execute_task("text.analyze", {"text": "hello world\nRHEXORA"}, max_items=10) == {
        "characters": 19,
        "words": 3,
        "lines": 2,
    }


def test_unknown_task_is_rejected():
    with pytest.raises(RemoteTaskError, match="Unsupported"):
        execute_task("os.shell", {"command": "whoami"}, max_items=10)


def test_count_primes_in_half_open_range():
    assert execute_task("math.count_primes", {"start": 0, "stop": 100}, max_items=100) == {
        "count": 25,
        "start": 0,
        "stop": 100,
    }


def test_count_primes_rejects_excessive_range():
    with pytest.raises(RemoteTaskError, match="larger"):
        execute_task("math.count_primes", {"start": 0, "stop": 1_000_000}, max_items=10)
