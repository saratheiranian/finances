"""Suggest a category for an inbox line by learning from what you chose before.

Bank descriptions vary slightly between purchases ("TESCO STORES 2345",
"TESCO STORES 0112"), so descriptions are normalised first: uppercase, with
digits and punctuation removed. Then the category you've used most often
for that merchant wins.
"""

import re
from collections import Counter, defaultdict


def normalise(description):
    text = re.sub(r"[^A-Z ]", " ", description.upper())
    return " ".join(text.split())


def merchant_key(description):
    """The first two words usually identify the merchant: 'TESCO STORES'."""
    return " ".join(normalise(description).split()[:2])


def learn(conn):
    """Map each merchant to the categories you've filed it under, from posted inbox lines."""
    history = defaultdict(Counter)
    rows = conn.execute(
        "SELECT s.description, a.name AS category FROM staged s "
        "JOIN imports i ON i.id = s.import_id "
        "JOIN entries e ON e.transaction_id = s.transaction_id "
        "JOIN accounts a ON a.id = e.account_id "
        "WHERE s.status = 'posted' AND a.name != i.account"
    ).fetchall()
    for r in rows:
        history[merchant_key(r["description"])][r["category"]] += 1
    return history


def suggest(history, description, amount):
    """Return (category, confidence) or (None, 0). Money out is never suggested as income."""
    counts = history.get(merchant_key(description))
    if not counts:
        return None, 0.0
    allowed = {c: n for c, n in counts.items() if not (amount < 0 and c.startswith("income"))}
    if not allowed:
        return None, 0.0
    category, n = max(allowed.items(), key=lambda kv: (kv[1], kv[0]))
    return category, n / sum(counts.values())
