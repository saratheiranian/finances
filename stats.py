"""Spending summaries, all calculated from ledger entries."""

from datetime import date


def months_with_data(conn):
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT substr(date, 1, 7) AS m FROM transactions ORDER BY m DESC")]


def previous_month(month):
    y, m = map(int, month.split("-"))
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


def _group_total(conn, kind, month):
    """Total of all expenses or income in a month, in natural sign."""
    total = conn.execute(
        "SELECT COALESCE(SUM(e.amount), 0) FROM entries e JOIN accounts a ON a.id = e.account_id "
        "JOIN transactions t ON t.id = e.transaction_id WHERE a.type = ? AND substr(t.date, 1, 7) = ?",
        (kind, month)).fetchone()[0]
    return -total if kind == "income" else total


def by_category(conn, month):
    """Spending per top-level category (expenses:food, expenses:rent...) this month
    and last month, biggest first."""
    def totals(m):
        rows = conn.execute(
            "SELECT a.name, SUM(e.amount) AS total FROM entries e JOIN accounts a ON a.id = e.account_id "
            "JOIN transactions t ON t.id = e.transaction_id "
            "WHERE a.type = 'expenses' AND substr(t.date, 1, 7) = ? GROUP BY a.name", (m,)).fetchall()
        out = {}
        for r in rows:
            parts = r["name"].split(":")
            key = ":".join(parts[:2])
            out[key] = out.get(key, 0) + r["total"]
        return out

    now, before = totals(month), totals(previous_month(month))
    rows = [{"category": c, "label": c.split(":", 1)[-1], "amount": now.get(c, 0), "previous": before.get(c, 0)}
            for c in set(now) | set(before)]
    for r in rows:
        r["change"] = r["amount"] - r["previous"]
    return sorted(rows, key=lambda r: -r["amount"])


def month_summary(conn, month):
    income = _group_total(conn, "income", month)
    spending = _group_total(conn, "expenses", month)
    kept = income - spending
    return {"month": month, "income": income, "spending": spending, "kept": kept,
            "kept_share": kept / income if income > 0 else None,
            "previous_spending": _group_total(conn, "expenses", previous_month(month))}


def monthly_trend(conn, count=6, until=None):
    months = sorted(months_with_data(conn))
    if until:
        months = [m for m in months if m <= until]
    return [{"month": m, "income": _group_total(conn, "income", m), "spending": _group_total(conn, "expenses", m)}
            for m in months[-count:]]


def month_label(month):
    y, m = map(int, month.split("-"))
    return date(y, m, 1).strftime("%B %Y")
