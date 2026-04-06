"""scraper/utils.py のユニットテスト"""

from datetime import date

from scraper.utils import date_range, parse_float, parse_int, parse_time_sec


def test_parse_time_sec_with_minutes():
    assert parse_time_sec("1:23.4") == 83.4


def test_parse_time_sec_without_minutes():
    assert parse_time_sec("23.4") == 23.4


def test_parse_time_sec_invalid():
    assert parse_time_sec("---") is None
    assert parse_time_sec("") is None


def test_parse_int_normal():
    assert parse_int("12") == 12
    assert parse_int("1,234") == 1234


def test_parse_int_invalid():
    assert parse_int("---") is None
    assert parse_int("") is None


def test_parse_float_normal():
    assert parse_float("12.5") == 12.5


def test_parse_float_invalid():
    assert parse_float("---") is None


def test_date_range():
    result = list(date_range(date(2026, 1, 1), date(2026, 1, 3)))
    assert result == [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)]


def test_date_range_single():
    result = list(date_range(date(2026, 1, 1), date(2026, 1, 1)))
    assert result == [date(2026, 1, 1)]
