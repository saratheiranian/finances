from datetime import date
from pathlib import Path

import pytest

from statements import StatementError, check_balances, external_ids, parse_hsbc_csv

SAMPLE = (Path(__file__).resolve().parent.parent / "samples" / "hsbc-sample-2026-09.csv").read_text()


def test_parses_the_sample():
    lines = parse_hsbc_csv(SAMPLE)
    assert len(lines) == 20
    salary = next(l for l in lines if "SALARY" in l.description)
    assert salary.amount == 125000            # "1,250.00" in quotes
    assert salary.date == date(2026, 9, 25)
    refund = next(l for l in lines if "REFUND" in l.description)
    assert refund.description == "AMAZON.CO.UK, REFUND"  # comma inside quotes survives


def test_header_row_and_blank_lines_are_ignored():
    text = "Date,Description,Amount\n\n01/09/2026,NETFLIX,-5.99\n"
    assert [l.amount for l in parse_hsbc_csv(text)] == [-599]


def test_every_bad_line_is_reported():
    text = "01/09/2026,OK,-1.00\n2026-09-02,BAD DATE,-1.00\n03/09/2026,BAD AMOUNT,lots\n04/09/2026,TOO,MANY,COLUMNS\n"
    with pytest.raises(StatementError) as e:
        parse_hsbc_csv(text)
    message = str(e.value)
    assert "line 2" in message and "line 3" in message and "line 4" in message


def test_balances_must_add_up():
    lines = parse_hsbc_csv(SAMPLE)
    summary = check_balances(lines, 85000, 147154)
    assert summary["start"] == date(2026, 9, 1) and summary["end"] == date(2026, 9, 30)
    with pytest.raises(StatementError, match="doesn't add up"):
        check_balances(lines, 85000, 147155)  # off by 1p


def test_ids_are_stable_and_keep_identical_purchases_apart():
    lines = parse_hsbc_csv(SAMPLE)
    ids = [i for _, i in external_ids("assets:hsbc:current", lines)]
    assert len(set(ids)) == len(ids)  # the two identical Prets get different ids
    reversed_file = "\n".join(reversed(SAMPLE.strip().splitlines()))
    assert sorted(ids) == sorted(i for _, i in external_ids("assets:hsbc:current", parse_hsbc_csv(reversed_file)))
