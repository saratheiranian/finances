import pytest

import ai
import query
from helpers import ACCOUNTS


@pytest.fixture
def fake(monkeypatch):
    calls = []

    def complete(prompt, system, max_tokens=600):
        calls.append(prompt)
        return complete.reply

    complete.reply = ""
    complete.calls = calls
    monkeypatch.setattr(ai, "complete", complete)
    return complete


LINES = [{"id": 1, "description": "LIDL GB LONDON", "amount": -1742},
         {"id": 2, "description": "GAILS BAKERY", "amount": -460},
         {"id": 3, "description": "MYSTERY LTD", "amount": -999}]


def test_categories_are_validated(conn, fake):
    fake.reply = '''```json
    {"1": "expenses:food:groceries", "2": "expenses:food:cakes", "3": "income:salary", "99": "expenses:rent"}
    ```'''
    result = ai.suggest_categories(conn, LINES, ACCOUNTS)
    # invented category (2), income for money out (3) and an id we never sent (99) are all dropped
    assert result == {1: "expenses:food:groceries"}


def test_replies_are_cached(conn, fake):
    fake.reply = '{"1": "expenses:food:groceries"}'
    ai.suggest_categories(conn, LINES, ACCOUNTS)
    ai.suggest_categories(conn, LINES, ACCOUNTS)
    assert len(fake.calls) == 1


def test_question_plan_still_goes_through_validation(conn, fake):
    from datetime import date
    fake.reply = '{"metric": "total", "flow": "spending", "category": "expenses:food", "start": "2026-10-01", "end": "2026-10-31"}'
    raw = ai.plan_question(conn, "food in October?", date(2026, 11, 30), ACCOUNTS, ["2026-10"])
    assert query.validate(raw, ACCOUNTS)["category"] == "expenses:food"
    fake.reply = '{"metric": "DROP TABLE", "start": "2026-10-01", "end": "2026-10-31"}'
    raw = ai.plan_question(conn, "something sneaky", date(2026, 11, 30), ACCOUNTS, ["2026-10"])
    with pytest.raises(query.QueryError):
        query.validate(raw, ACCOUNTS)


def test_no_key_means_off(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert not ai.enabled()
    with pytest.raises(ai.AIError):
        ai.complete("hi", "sys")
