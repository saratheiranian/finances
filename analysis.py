"""Pattern-finding over your bank lines: recurring payments, unusual amounts,
possible duplicate charges, budgets, and a cash-flow forecast.

Everything is measured "as of" your latest statement rather than today's date,
so the analysis stays meaningful even if you haven't imported recently.
"""

import calendar
from collections import defaultdict
from datetime import date, timedelta
from statistics import median

from categorise import merchant_key


# ---------- shared ----------

def reference_date(conn):
    row = conn.execute("SELECT MAX(date) FROM staged WHERE status != 'skipped'").fetchone()[0]
    row = row or conn.execute("SELECT MAX(date) FROM transactions").fetchone()[0]
    return date.fromisoformat(row) if row else date.today()


def bank_lines(conn, include_pending=False):
    """Every imported bank line (not skipped), oldest first."""
    statuses = "('posted', 'pending')" if include_pending else "('posted')"
    rows = conn.execute(
        f"SELECT s.*, i.account FROM staged s JOIN imports i ON i.id = s.import_id "
        f"WHERE s.status IN {statuses} ORDER BY s.date, s.id").fetchall()
    return [{**dict(r), "day": date.fromisoformat(r["date"]), "merchant": merchant_key(r["description"])}
            for r in rows]


def robust_outlier(value, history, threshold=3.5):
    """Is `value` far from the typical values in `history`?

    Uses the median and the median absolute deviation (MAD) instead of the mean
    and standard deviation, because one huge purchase would drag a mean up and
    hide itself. The 0.6745 factor makes the score comparable to a z-score.
    """
    if len(history) < 3:
        return False, 0.0
    m = median(history)
    mad = median(abs(x - m) for x in history)
    if mad == 0:
        # All past values identical: flag only a clearly different amount.
        return abs(value - m) > max(0.5 * abs(m), 500), 0.0
    score = 0.6745 * (value - m) / mad
    return abs(score) > threshold, score


# ---------- recurring payments ----------

PERIODS = [("weekly", 7, 2), ("monthly", 30.4, 5), ("yearly", 365, 10)]  # name, days, tolerance


def detect_recurring(conn, as_of=None):
    """Find merchants that charge (or pay) on a regular schedule.

    For each merchant, the gaps between payments are compared with weekly,
    monthly and yearly periods. If every gap fits one period within its
    tolerance, it's recurring. The next date is predicted from the median gap,
    and changes in amount are reported as price changes.
    """
    as_of = as_of or reference_date(conn)
    groups = defaultdict(list)
    for line in bank_lines(conn, include_pending=True):
        groups[(line["merchant"], line["amount"] < 0)].append(line)

    found = []
    for (merchant, outgoing), lines in groups.items():
        # One payment per day at most: two coffees on one day aren't a schedule.
        by_day = {}
        for l in lines:
            by_day.setdefault(l["day"], l)
        lines = sorted(by_day.values(), key=lambda l: l["day"])
        if len(lines) < 2:
            continue
        gaps = [(b["day"] - a["day"]).days for a, b in zip(lines, lines[1:])]
        for name, days, tolerance in PERIODS:
            if all(abs(g - days) <= tolerance for g in gaps):
                break
        else:
            continue
        typical_gap = median(gaps)
        last = lines[-1]
        next_due = last["day"] + timedelta(days=round(typical_gap))
        amounts = [l["amount"] for l in lines]
        item = {
            "merchant": merchant, "description": last["description"], "period": name,
            "occurrences": len(lines), "amount": last["amount"], "outgoing": outgoing,
            "last": last["day"], "next_due": next_due,
            "overdue": as_of > next_due + timedelta(days=tolerance),
            "confidence": "high" if len(lines) >= 3 else "likely",
            "price_change": None,
        }
        if len(amounts) >= 2 and amounts[-1] != amounts[-2]:
            item["price_change"] = {"from": amounts[-2], "to": amounts[-1]}
        yearly_factor = {"weekly": 52, "monthly": 12, "yearly": 1}[name]
        item["yearly_cost"] = abs(last["amount"]) * yearly_factor
        found.append(item)
    return sorted(found, key=lambda i: (not i["outgoing"], i["amount"]))


# ---------- warnings for the inbox ----------

def inbox_warnings(conn, pending_rows):
    """Things worth a second look before approving: possible duplicate
    charges, unusually large amounts for that shop, and first-time shops."""
    history = bank_lines(conn, include_pending=True)
    by_merchant = defaultdict(list)
    for l in history:
        by_merchant[l["merchant"]].append(l)

    warnings = {}
    for r in pending_rows:
        notes = []
        day, merchant = date.fromisoformat(r["date"]), merchant_key(r["description"])
        others = [l for l in by_merchant[merchant] if l["id"] != r["id"]]
        near = [l for l in others if l["amount"] == r["amount"] and abs((l["day"] - day).days) <= 2]
        if near:
            notes.append(("duplicate", "Same shop and amount within 2 days. A double charge, or two real purchases?"))
        earlier = [l["amount"] for l in others if l["day"] < day and (l["amount"] < 0) == (r["amount"] < 0)]
        unusual, _ = robust_outlier(abs(r["amount"]), [abs(a) for a in earlier])
        if unusual:
            notes.append(("unusual", f"Much larger than usual here (typically {abs(median(earlier)) / 100:.2f})."))
        if not others:
            notes.append(("new", "First time at this shop."))
        warnings[r["id"]] = notes
    return warnings


def unusual_spending(conn, month):
    """Expense entries this month that are outliers within their own category."""
    rows = conn.execute(
        "SELECT t.id, t.date, t.description, a.name AS category, e.amount FROM entries e "
        "JOIN accounts a ON a.id = e.account_id JOIN transactions t ON t.id = e.transaction_id "
        "WHERE a.type = 'expenses' AND e.amount > 0 ORDER BY t.date").fetchall()
    by_cat = defaultdict(list)
    for r in rows:
        by_cat[r["category"]].append(r)
    out = []
    for category, items in by_cat.items():
        for r in items:
            if not r["date"].startswith(month):
                continue
            others = [x["amount"] for x in items if x["id"] != r["id"]]
            flagged, score = robust_outlier(r["amount"], others)
            if flagged and score > 0:
                out.append({**dict(r), "typical": round(median(others))})  # median can be x.5; money is whole pence
    return sorted(out, key=lambda r: -r["amount"])


# ---------- budgets ----------

def set_budget(conn, category, pence):
    if not category.startswith("expenses"):
        raise ValueError("Budgets are for expenses: categories.")
    with conn:
        if pence <= 0:
            conn.execute("DELETE FROM budgets WHERE category = ?", (category,))
        else:
            conn.execute("INSERT OR REPLACE INTO budgets VALUES (?, ?)", (category, pence))


def budget_progress(conn, month, as_of=None):
    """Spending against each budget, and where you'll end up at this pace."""
    import ledger
    as_of = as_of or reference_date(conn)
    y, m = map(int, month.split("-"))
    days_in_month = calendar.monthrange(y, m)[1]
    in_progress = as_of.year == y and as_of.month == m
    elapsed = as_of.day if in_progress else days_in_month
    start = date(y, m, 1)
    out = []
    for b in conn.execute("SELECT * FROM budgets ORDER BY category"):
        end = date(y, m, days_in_month).isoformat()
        spent = (ledger.natural_balance(conn, b["category"], end)
                 - ledger.natural_balance(conn, b["category"], (start - timedelta(days=1)).isoformat()))
        projected = round(spent * days_in_month / elapsed) if in_progress and elapsed else spent
        out.append({"category": b["category"], "budget": b["monthly"], "spent": spent,
                    "share": spent / b["monthly"], "projected": projected,
                    "in_progress": in_progress, "over": spent > b["monthly"],
                    "heading_over": in_progress and projected > b["monthly"] >= spent})
    return out


# ---------- cash-flow forecast ----------

def forecast(conn, account, days=60, as_of=None):
    """Project an account's balance forward, day by day.

    balance tomorrow = balance today
                     + any recurring payment or income predicted for that day
                     - typical day-to-day spending (the median-based daily rate
                       of everything that isn't recurring, over the last 60 days)

    It's a simple, explainable model rather than a black box: every number on
    the chart can be traced to a recurring item or the daily rate.
    """
    import ledger
    as_of = as_of or reference_date(conn)
    balance = ledger.natural_balance(conn, account, as_of.isoformat())
    recurring = [r for r in detect_recurring(conn, as_of)]
    recurring_merchants = {r["merchant"] for r in recurring}

    window_start = as_of - timedelta(days=59)
    lines = [l for l in bank_lines(conn, include_pending=True)
             if l["account"] == account and window_start <= l["day"] <= as_of]
    covered = (as_of - max(window_start, min((l["day"] for l in lines), default=as_of))).days + 1
    discretionary = [l for l in lines if l["amount"] < 0 and l["merchant"] not in recurring_merchants
                     and "TRANSFER" not in l["description"].upper()]
    daily_rate = -sum(l["amount"] for l in discretionary) / max(covered, 1)

    # Expand recurring items into dated events over the horizon.
    events = defaultdict(list)
    horizon = as_of + timedelta(days=days)
    for r in recurring:
        step = {"weekly": 7, "monthly": None, "yearly": 365}[r["period"]]
        due = r["next_due"]
        while due <= horizon:
            if due > as_of:
                events[due].append(r)
            due = _add_month(due) if step is None else due + timedelta(days=step)

    points, low = [], None
    running = balance
    for i in range(1, days + 1):
        day = as_of + timedelta(days=i)
        running += sum(r["amount"] for r in events.get(day, [])) - daily_rate
        point = {"date": day, "balance": round(running), "events": events.get(day, [])}
        points.append(point)
        if low is None or point["balance"] < low["balance"]:
            low = point
    return {"account": account, "as_of": as_of, "start": balance, "daily_rate": round(daily_rate),
            "points": points, "low": low, "end": points[-1]["balance"] if points else balance,
            "recurring_count": len(recurring)}


def _add_month(d):
    y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def chart_path(points, width=640, height=180, pad=10):
    """SVG polyline coordinates for the forecast, plus the y of the zero line."""
    if not points:
        return "", None
    values = [p["balance"] for p in points]
    lo, hi = min(values + [0]), max(values)
    span = (hi - lo) or 1
    xs = lambda i: pad + i * (width - 2 * pad) / max(len(points) - 1, 1)
    ys = lambda v: pad + (hi - v) * (height - 2 * pad) / span
    coords = " ".join(f"{xs(i):.1f},{ys(v):.1f}" for i, v in enumerate(values))
    return coords, ys(0)
