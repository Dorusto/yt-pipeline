import threading
import time

import pytest

from common.pool import ordered_map


def test_results_come_back_in_input_order():
    def fn(n):
        time.sleep(0.05 * (5 - n))
        return n

    assert list(ordered_map(fn, [1, 2, 3, 4], workers=4)) == [1, 2, 3, 4]


def test_workers_one_is_sequential():
    calls = []

    def fn(n):
        calls.append(n)
        return n

    assert list(ordered_map(fn, [1, 2, 3], workers=1)) == [1, 2, 3]
    assert calls == [1, 2, 3]


def test_pool_is_bounded():
    lock = threading.Lock()
    active = 0
    peak = 0

    def fn(n):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.02)
        with lock:
            active -= 1
        return n

    assert list(ordered_map(fn, list(range(8)), workers=3)) == list(range(8))
    assert peak <= 3


def test_workers_below_one_is_an_error():
    with pytest.raises(ValueError):
        list(ordered_map(lambda n: n, [1], workers=0))


def test_empty_items():
    assert list(ordered_map(lambda n: n, [], workers=4)) == []


def test_exception_propagates():
    def fn(n):
        if n == 2:
            raise RuntimeError("boom")
        return n

    with pytest.raises(RuntimeError):
        list(ordered_map(fn, [1, 2, 3], workers=4))
