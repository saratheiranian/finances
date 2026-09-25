import ledger
import stats

ACC = "assets:hsbc:current"


def test_month_summary_and_categories(books):
    ledger.post(books, "2026-08-10", "Tesco", [("expenses:food:groceries", 4000), (ACC, -4000)])
    ledger.post(books, "2026-09-01", "Salary", [(ACC, 100000), ("income:salary", -100000)])
    ledger.post(books, "2026-09-05", "Tesco", [("expenses:food:groceries", 3000), (ACC, -3000)])
    ledger.post(books, "2026-09-06", "Pret", [("expenses:food:eating-out", 1000), (ACC, -1000)])
    ledger.post(books, "2026-09-07", "Bus", [("expenses:transport", 500), (ACC, -500)])

    s = stats.month_summary(books, "2026-09")
    assert (s["income"], s["spending"], s["kept"]) == (100000, 4500, 95500)
    assert s["previous_spending"] == 4000
    assert round(s["kept_share"], 3) == 0.955

    cats = {c["category"]: c for c in stats.by_category(books, "2026-09")}
    assert cats["expenses:food"]["amount"] == 4000          # groceries + eating out roll up
    assert cats["expenses:food"]["change"] == 0             # same as August
    assert cats["expenses:transport"]["previous"] == 0


def test_previous_month_wraps_the_year():
    assert stats.previous_month("2026-01") == "2025-12"
    assert stats.previous_month("2026-10") == "2026-09"


def test_trend(books):
    ledger.post(books, "2026-08-10", "Tesco", [("expenses:food:groceries", 4000), (ACC, -4000)])
    ledger.post(books, "2026-09-10", "Tesco", [("expenses:food:groceries", 1000), (ACC, -1000)])
    assert [t["spending"] for t in stats.monthly_trend(books)] == [4000, 1000]
