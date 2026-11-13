import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from extract import line_is_consistent, parse_number  # noqa: E402


def test_parse_number_handles_thousands_separator():
    assert parse_number("1,207.50") == 1207.5


def test_parse_number_rejects_garbage_instead_of_guessing():
    assert parse_number("8.4S") is None
    assert parse_number("") is None


def test_line_check_passes_to_the_cent():
    assert line_is_consistent(22, 8.45, 185.90)


def test_line_check_fails_on_misread_digit():
    assert not line_is_consistent(22, 8.15, 185.90)


def test_line_check_fails_when_a_value_is_missing():
    assert not line_is_consistent(22, None, 185.90)
