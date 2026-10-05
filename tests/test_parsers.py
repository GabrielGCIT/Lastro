"""Parsers de form (helpers) — robustos contra input vazio ou adulterado."""
from app.helpers import parse_int


def test_parse_int():
    assert parse_int('42') == 42
    assert parse_int(' 7 ') == 7
    assert parse_int('') is None
    assert parse_int(None) is None
    assert parse_int('abc') is None
    assert parse_int('1.5') is None
