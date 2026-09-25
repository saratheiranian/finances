import pytest

import query
from helpers import ACCOUNTS, build

BASE = {"metric": "total", "flow": "spending", "start": "2026-09-01", "end": "2026-11-30"}


def plan(**kw):
    return query.validate({**BASE, **kw}, ACCOUNTS)


def test_total_by_category_and_merchant(books):
    build(books)
    r = query.run(books, plan(category="expenses:food:eating-out", start="2026-10-01", end="2026-10-31"))
    assert r["headline"] == 545 + 1650 + 2110 + 395 * 2
    r = query.run(books, plan(metric="count", merchant="uber"))
    assert r["headline"] == 2
    r = query.run(books, plan(metric="largest", category="expenses:food:groceries"))
    assert r["headline"] == 14860


def test_grouping(books):
    build(books)
    r = query.run(books, plan(category="expenses:subscriptions", group_by="month"))
    assert [(k, v) for k, v, _ in r["groups"]] == [("2026-09", 4297), ("2026-10", 4297), ("2026-11", 4397)]


def test_income(books):
    build(books)
    r = query.run(books, plan(flow="income"))
    assert r["headline"] == 125000 * 3


@pytest.mark.parametrize("bad, message", [
    ({"metric": "delete"}, "metric"),
    ({"category": "expenses:holidays"}, "no category"),
    ({"category": "income:salary"}, "isn't a spending"),
    ({"merchant": "x'; DROP TABLE entries; --"}, "characters"),
    ({"start": "last tuesday"}, "valid start"),
    ({"start": "2026-12-01", "end": "2026-01-01"}, "after the end"),
])
def test_validation_rejects_bad_plans(bad, message):
    with pytest.raises(query.QueryError, match=message):
        plan(**bad)
