from datetime import date

import analysis
import importer
from helpers import SAMPLES, build


def test_robust_outlier():
    assert analysis.robust_outlier(14860, [3122, 1250, 3460, 2930])[0]
    assert not analysis.robust_outlier(2800, [3122, 1250, 3460, 2930])[0]
    assert not analysis.robust_outlier(99999, [100, 200])[0]  # too little history to judge


def test_recurring_payments(books):
    build(books)
    found = {r["merchant"]: r for r in analysis.detect_recurring(books)}
    for merchant in ("NETFLIX", "SPOTIFY", "GYM GROUP", "RENT SO", "THREE MOBILE", "KINGS COLLEGE"):
        assert found[merchant]["period"] == "monthly", merchant
    assert "TFL TRAVEL" not in found and "TESCO STORES" not in found  # irregular
    assert found["SPOTIFY"]["price_change"] == {"from": -1199, "to": -1299}
    assert found["NETFLIX"]["next_due"] == date(2026, 12, 1)
    assert found["NETFLIX"]["confidence"] == "high"
    assert not found["KINGS COLLEGE"]["outgoing"]


def test_inbox_warnings(books):
    build(books, months=2)
    importer.import_statement(books, (SAMPLES / "hsbc-sample-2026-11.csv").read_text(),
                              "nov", "assets:hsbc:current", 168473, 191354)
    pending = importer.inbox(books)
    warnings = analysis.inbox_warnings(books, pending)
    kinds = {r["description"] + r["date"]: {k for k, _ in warnings[r["id"]]} for r in pending}
    assert "duplicate" in kinds["DELIVEROO2026-11-21"] and "duplicate" in kinds["DELIVEROO2026-11-23"]
    assert "unusual" in kinds["TESCO STORES 23452026-11-28"]
    assert "new" in kinds["LIDL GB LONDON2026-11-26"]
    assert not kinds["NETFLIX2026-11-01"]


def test_unusual_spending(books):
    build(books)
    flagged = [u["description"] for u in analysis.unusual_spending(books, "2026-11")]
    assert "TESCO STORES 2345" in flagged


def test_budgets(books):
    build(books)
    analysis.set_budget(books, "expenses:food", 15000)
    b = analysis.budget_progress(books, "2026-11")[0]
    assert b["spent"] == 14860 + 610 + 1742 + 2430 * 2 + 460 + 2215 + 395 + 2930
    assert b["over"] and not b["in_progress"] is False  # November is the latest month, so it's in progress
    analysis.set_budget(books, "expenses:food", 0)
    assert analysis.budget_progress(books, "2026-11") == []


def test_forecast(books):
    build(books)
    f = analysis.forecast(books, "assets:hsbc:current", days=60)
    assert f["start"] == 191354 and len(f["points"]) == 60
    assert f["daily_rate"] > 0
    paydays = [p for p in f["points"] if any(e["merchant"] == "KINGS COLLEGE" for e in p["events"])]
    assert [p["date"] for p in paydays] == [date(2026, 12, 25), date(2027, 1, 25)]
    coords, zero = analysis.chart_path(f["points"])
    assert coords.count(",") == 60


def test_unusual_spending_typical_is_whole_pence(books):
    build(books)
    for u in analysis.unusual_spending(books, "2026-11"):
        assert isinstance(u["typical"], int)
