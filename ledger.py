"""The ledger's rules. Every write goes through here, and every rule is
checked before anything is saved:

1. Balanced:    a transaction's entries must sum to exactly zero.
2. Idempotent:  posting with an external_id that's already been posted does
                nothing and returns the original, so re-imports are safe.
3. Permanent:   nothing is edited or deleted; reverse() cancels a transaction
                by posting its mirror image.
4. Derived:     balances are never stored, only calculated from entries,
                so they can't drift out of sync.
"""

import re
from datetime import date

from db import ACCOUNT_TYPES

ACCOUNT_NAME = re.compile(r"^[a-z0-9_-]+(:[a-z0-9_-]+)*$")


class LedgerError(ValueError):
    pass


# ---------- accounts ----------

def open_account(conn, name, today=None):
    """Create an account such as 'expenses:food:groceries'. Its type comes
    from the first part of the name, so the hierarchy always makes sense."""
    name = name.strip().lower()
    if not ACCOUNT_NAME.match(name):
        raise LedgerError(f"'{name}' isn't a valid name. Use lowercase parts joined by colons, "
                          "e.g. expenses:food:groceries.")
    kind = name.split(":")[0]
    if kind not in ACCOUNT_TYPES:
        raise LedgerError(f"Account names must start with one of: {', '.join(ACCOUNT_TYPES)}.")
    if get_account(conn, name):
        raise LedgerError(f"'{name}' already exists.")
    with conn:
        conn.execute("INSERT INTO accounts (name, type, opened) VALUES (?, ?, ?)",
                     (name, kind, (today or date.today()).isoformat()))
    return name


def get_account(conn, name):
    return conn.execute("SELECT * FROM accounts WHERE name = ?", (name,)).fetchone()


def list_accounts(conn):
    return conn.execute("SELECT * FROM accounts ORDER BY name").fetchall()


# ---------- posting ----------

def post(conn, when, description, entries, external_id=None):
    """Record a transaction. `entries` is a list of (account_name, pence) pairs.

    Returns (transaction_id, created). created is False when this external_id
    was already posted with the same contents, which makes imports repeatable.
    """
    description = description.strip()
    if not description:
        raise LedgerError("A transaction needs a description.")
    if isinstance(when, str):
        try:
            when = date.fromisoformat(when)
        except ValueError:
            raise LedgerError(f"'{when}' isn't a date. Use YYYY-MM-DD.")
    if len(entries) < 2:
        raise LedgerError("A transaction moves money between at least two accounts.")

    resolved = []
    for account_name, amount in entries:
        if not isinstance(amount, int) or isinstance(amount, bool):
            raise LedgerError("Amounts must be whole pence (integers), never floats.")
        if amount == 0:
            raise LedgerError("Entries can't be zero.")
        account = get_account(conn, account_name)
        if account is None:
            raise LedgerError(f"No account called '{account_name}'. Open it first.")
        resolved.append((account["id"], amount))

    total = sum(amount for _, amount in resolved)
    if total != 0:
        raise LedgerError(f"Entries must add up to zero, but these add up to {total} pence.")

    if external_id is not None:
        existing = conn.execute(
            "SELECT id FROM transactions WHERE external_id = ?", (external_id,)).fetchone()
        if existing:
            if _signature(conn, existing["id"]) != sorted(resolved):
                raise LedgerError(f"'{external_id}' was already posted with different amounts. "
                                  "Refusing to guess which one is right.")
            return existing["id"], False

    with conn:  # the transaction and all its entries are saved together, or not at all
        tid = conn.execute(
            "INSERT INTO transactions (date, description, external_id) VALUES (?, ?, ?)",
            (when.isoformat(), description, external_id),
        ).lastrowid
        conn.executemany("INSERT INTO entries (transaction_id, account_id, amount) VALUES (?, ?, ?)",
                         [(tid, aid, amount) for aid, amount in resolved])
    return tid, True


def _signature(conn, transaction_id):
    rows = conn.execute("SELECT account_id, amount FROM entries WHERE transaction_id = ?",
                        (transaction_id,)).fetchall()
    return sorted((r["account_id"], r["amount"]) for r in rows)


def reverse(conn, transaction_id, when=None, reason=None):
    """Cancel a transaction by posting one with every amount flipped."""
    original = get_transaction(conn, transaction_id)
    if original is None:
        raise LedgerError(f"No transaction #{transaction_id}.")
    if original["reverses_id"] is not None:
        raise LedgerError("That transaction is itself a reversal. Post the correct entries instead.")
    if conn.execute("SELECT 1 FROM transactions WHERE reverses_id = ?", (transaction_id,)).fetchone():
        raise LedgerError(f"Transaction #{transaction_id} has already been reversed.")

    when = when or date.today()
    description = f"Reversal of #{transaction_id}: {original['description']}"
    if reason:
        description += f" ({reason})"
    with conn:
        tid = conn.execute(
            "INSERT INTO transactions (date, description, reverses_id) VALUES (?, ?, ?)",
            (when.isoformat() if isinstance(when, date) else when, description, transaction_id),
        ).lastrowid
        conn.execute(
            "INSERT INTO entries (transaction_id, account_id, amount) "
            "SELECT ?, account_id, -amount FROM entries WHERE transaction_id = ?",
            (tid, transaction_id),
        )
    return tid


# ---------- reading ----------

def get_transaction(conn, transaction_id):
    return conn.execute("SELECT * FROM transactions WHERE id = ?", (transaction_id,)).fetchone()


def journal(conn, limit=50):
    """Recent transactions, each with its entries."""
    txns = conn.execute(
        "SELECT t.*, r.id AS reversed_by FROM transactions t "
        "LEFT JOIN transactions r ON r.reverses_id = t.id "
        "ORDER BY t.date DESC, t.id DESC LIMIT ?", (limit,)).fetchall()
    result = []
    for t in txns:
        entries = conn.execute(
            "SELECT a.name, e.amount FROM entries e JOIN accounts a ON a.id = e.account_id "
            "WHERE e.transaction_id = ? ORDER BY e.amount DESC", (t["id"],)).fetchall()
        result.append({**dict(t), "entries": [dict(e) for e in entries]})
    return result


def balance(conn, account_name, as_of=None):
    """Raw balance (debits positive) of an account and everything beneath it.
    balance('expenses') includes expenses:food, expenses:food:groceries, etc."""
    as_of = (as_of or date.max).isoformat() if not isinstance(as_of, str) else as_of
    row = conn.execute(
        "SELECT COALESCE(SUM(e.amount), 0) FROM entries e "
        "JOIN accounts a ON a.id = e.account_id "
        "JOIN transactions t ON t.id = e.transaction_id "
        # 'expenses' matches itself and 'expenses:...', but not 'expensesfoo'
        "WHERE (a.name = ? OR a.name LIKE ? ESCAPE '\\') AND t.date <= ?",
        (account_name, _escape_like(account_name) + ":%", as_of),
    ).fetchone()
    return row[0]


def _escape_like(text):
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def natural_balance(conn, account_name, as_of=None):
    """Balance in the sign a person expects. Income, liabilities and equity
    are credit accounts, so their raw balance is negative; flip it."""
    raw = balance(conn, account_name, as_of)
    kind = account_name.split(":")[0]
    return -raw if kind in ("liabilities", "equity", "income") else raw


def balances(conn, as_of=None):
    """Every account and every level above it (expenses, expenses:food, ...),
    each with the rolled-up total of everything beneath it."""
    names = set()
    for a in list_accounts(conn):
        parts = a["name"].split(":")
        names.update(":".join(parts[:i]) for i in range(1, len(parts) + 1))
    return [{"name": n, "type": n.split(":")[0], "balance": natural_balance(conn, n, as_of)}
            for n in sorted(names)]


def check_integrity(conn):
    """Audit the whole ledger. Returns a list of problems; empty means healthy."""
    problems = []
    total = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM entries").fetchone()[0]
    if total != 0:
        problems.append(f"The ledger as a whole is off by {total} pence.")
    for row in conn.execute(
            "SELECT transaction_id, SUM(amount) AS s FROM entries GROUP BY transaction_id HAVING s != 0"):
        problems.append(f"Transaction #{row['transaction_id']} is unbalanced by {row['s']} pence.")
    for row in conn.execute(
            "SELECT t.id FROM transactions t LEFT JOIN entries e ON e.transaction_id = t.id "
            "GROUP BY t.id HAVING COUNT(e.id) < 2"):
        problems.append(f"Transaction #{row['id']} has fewer than two entries.")
    return problems
