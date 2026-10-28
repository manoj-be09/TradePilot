from packages.database.clock import monotonic_utc_now


def test_monotonic_utc_now_never_repeats_or_goes_back():
    values = [monotonic_utc_now() for _ in range(5000)]
    assert all(later > earlier for earlier, later in zip(values, values[1:], strict=False))
    assert all(value.tzinfo is not None for value in values)
