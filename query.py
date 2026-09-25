"""Answering questions about your money from a small, validated query plan.

The AI never writes SQL and never does arithmetic. It only fills in a plan
like the one below; this module checks every field against a whitelist and
computes the answer itself, so the numbers are always exact.

    {"metric": "total", "flow": "spending", "category": "expenses:food",
     "merchant": null, "start": "2026-10-01", "end": "2026-10-31", "group_by": null}
"""

import re
from collections import defaultdict
from datetime import date

from categorise import merchant_key
from money import fmt

METRICS = ("total", "count", "average", "largest", "list")
FLOWS = ("spending", "income")
GROUPS = (None, "month", "category", "merchant")


class QueryError(ValueError):
    pass


def validate(plan, account_names):
    """Return a clean copy of the plan, or raise QueryError explaining what's wrong."""
    if not isinstance(plan, dict):
        raise QueryError("The plan must be an object.")
    clean = {
        "metric": plan.get("metric") or "total",
        "flow": plan.get("flow") or "spending",
        "category": plan.get("category") or None,
        "merchant": plan.get("merchant") or None,
        "group_by": plan.get("group_by") or None,
    }
    if clean["metric"] not in METRICS:
        raise QueryError(f"Unknown metric '{clean['metric']}'.")
    if clean["flow"] not in FLOWS:
        raise QueryError(f"Unknown flow '{clean['flow']}'.")
    if clean["group_by"] not in GROUPS:
        raise QueryError(f"Can't group by '{clean['group_by']}'.")
    if clean["category"]:
        cat = clean["category"].strip().lower()
        if not any(n == cat or n.startswith(cat + ":") for n in account_names):
            raise QueryError(f"There's no category called '{cat}'.")
        expected = "expenses" if clean["flow"] == "spending" else "income"
        if cat.split(":")[0] != expected:
            raise QueryError(f"'{cat}' isn't a {clean['flow']} category.")
        clean["category"] = cat
    if clean["merchant"]:
        m = str(clean["merchant"]).strip()
        if not re.fullmatch(r"[A-Za-z0-9 &'.*-]{1,40}", m):
            raise QueryError("The shop name has characters I can't search for.")
        clean["merchant"] = m
    for key in ("start", "end"):
        try:
            clean[key] = date.fromisoformat(str(plan.get(key)))
        except ValueError:
            raise QueryError(f"'{plan.get(key)}' isn't a valid {key} date.")
    if clean["start"] > clean["end"]:
        raise QueryError("The start date is after the end date.")
    return clean


def run(conn, plan):
    """Execute a validated plan. Returns rows, a headline number, and a sentence."""
    kind = "expenses" if plan["flow"] == "spending" else "income"
    sql = ("SELECT t.id, t.date, t.description, a.name AS category, e.amount FROM entries e "
           "JOIN accounts a ON a.id = e.account_id JOIN transactions t ON t.id = e.transaction_id "
           "WHERE a.type = ? AND t.date BETWEEN ? AND ?")
    params = [kind, plan["start"].isoformat(), plan["end"].isoformat()]
    if plan["category"]:
        sql += " AND (a.name = ? OR a.name LIKE ?)"
        params += [plan["category"], plan["category"] + ":%"]
    if plan["merchant"]:
        sql += " AND UPPER(t.description) LIKE ?"
        params.append(f"%{plan['merchant'].upper()}%")
    rows = []
    for r in conn.execute(sql + " ORDER BY t.date", params):
        value = r["amount"] if kind == "expenses" else -r["amount"]
        rows.append({**dict(r), "value": value})

    def summarise(items):
        values = [i["value"] for i in items]
        if plan["metric"] == "count":
            return len(values)
        if plan["metric"] == "average":
            return round(sum(values) / len(values)) if values else 0
        if plan["metric"] == "largest":
            return max(values, default=0)
        return sum(values)

    groups = None
    if plan["group_by"]:
        buckets = defaultdict(list)
        for r in rows:
            key = {"month": r["date"][:7], "category": ":".join(r["category"].split(":")[:2]),
                   "merchant": merchant_key(r["description"])}[plan["group_by"]]
            buckets[key].append(r)
        groups = sorted(((k, summarise(v), len(v)) for k, v in buckets.items()),
                        key=lambda g: (g[0] if plan["group_by"] == "month" else -g[1]))

    headline = summarise(rows)
    return {"rows": rows, "headline": headline, "groups": groups, "sentence": describe(plan, headline, len(rows))}


def describe(plan, headline, n):
    what = plan["category"] or ("all spending" if plan["flow"] == "spending" else "all income")
    if plan["merchant"]:
        what += f" at {plan['merchant'].upper()}"
    period = f"from {plan['start']:%d %b %Y} to {plan['end']:%d %b %Y}"
    verb = "spent" if plan["flow"] == "spending" else "received"
    if plan["metric"] == "count":
        return f"{headline} transaction{'' if headline == 1 else 's'} in {what}, {period}."
    if plan["metric"] == "average":
        return f"On average you {verb} {fmt(headline)} per transaction in {what}, {period} ({n} transactions)."
    if plan["metric"] == "largest":
        return f"The largest single amount in {what} {period} was {fmt(headline)}."
    if plan["metric"] == "list":
        return f"{n} transaction{'' if n == 1 else 's'} in {what}, {period}, totalling {fmt(sum_or_zero(headline))}."
    return f"You {verb} {fmt(headline)} on {what}, {period} ({n} transaction{'' if n == 1 else 's'})."


def sum_or_zero(x):
    return x or 0
