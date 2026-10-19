"""Tests for mementic.text helpers."""

from __future__ import annotations

import pytest

from mementic.text import format_timestamp


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2024-01-01T00:00:00Z", "2024-01-01 00:00"),
        ("2024-01-01T23:59:59.123456+02:00", "2024-01-01 23:59"),
    ],
)
def test_format_timestamp_renders_iso_values(value: str, expected: str) -> None:
    assert format_timestamp(value) == expected


def test_format_timestamp_returns_unparseable_value_unchanged() -> None:
    assert format_timestamp("not-a-timestamp") == "not-a-timestamp"
