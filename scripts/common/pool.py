# SPDX-License-Identifier: AGPL-3.0-or-later
"""A bounded worker pool that preserves the input order (AUDIT V030 F4)."""

from concurrent.futures import ThreadPoolExecutor


def ordered_map(fn, items, workers=4):
    """Yield ``fn(item)`` for every item, running at most ``workers`` at a time.

    Results come back in the input order, so a caller that writes them as it
    receives them produces the same output as a sequential run. ``workers``
    below 1 is a usage error.
    """
    items = list(items)
    if workers < 1:
        raise ValueError("workers must be at least 1")
    if workers == 1 or len(items) <= 1:
        for item in items:
            yield fn(item)
        return
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(fn, item) for item in items]
        for future in futures:
            yield future.result()
