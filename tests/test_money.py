import pytest

from money import fmt, to_pence


@pytest.mark.parametrize("text, pence", [
    ("12.34", 1234), ("-5", -500), ("£1,200.50", 120050), ("0.1", 10), (" 7.00 ", 700),
])
def test_to_pence(text, pence):
    assert to_pence(text) == pence


def test_floats_would_have_gone_wrong():
    assert 0.1 + 0.2 != 0.3                                  # the problem...
    assert to_pence("0.1") + to_pence("0.2") == to_pence("0.3")  # ...and the fix


@pytest.mark.parametrize("bad", ["abc", "1.234", ""])
def test_to_pence_rejects_bad_input(bad):
    with pytest.raises(ValueError):
        to_pence(bad)


def test_fmt():
    assert fmt(1234) == "£12.34"
    assert fmt(-500) == "-£5.00"
    assert fmt(123456789) == "£1,234,567.89"
