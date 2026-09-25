"""Importing statements into the inbox, and approving them into the ledger.

    parse -> check balances -> check continuity -> stage -> (review) -> post
"""

from datetime import date

import ledger
import statements
from money import fmt


class ImportError_(ValueError):  # trailing underscore: ImportError is a Python built-in
    pass


def import_statement(conn, text, filename, account, opening, closing, allow_gap=False):
    acct = ledger.get_account(conn, account)
    if acct is None:
        raise ImportError_(f"No account called '{account}'. Open it first.")
    if acct["type"] not in ("assets", "liabilities"):
        raise ImportError_("Statements belong to a bank account (assets:...) or a card (liabilities:...).")

    lines = statements.parse_hsbc_csv(text)
    summary = statements.check_balances(lines, opening, closing)

    digest = statements.file_hash(text)
    already = conn.execute("SELECT imported_at FROM imports WHERE account = ? AND file_hash = ?",
                           (account, digest)).fetchone()
    if already:
        raise ImportError_(f"This exact file was already imported on {already['imported_at'][:10]}.")

    # Continuity: this statement should open where the last one closed.
    previous = conn.execute(
        "SELECT * FROM imports WHERE account = ? ORDER BY period_end DESC, id DESC LIMIT 1", (account,)
    ).fetchone()
    if previous and previous["closing"] != opening and not allow_gap:
        raise ImportError_(
            f"Gap between statements: the last one (ending {previous['period_end']}) closed at "
            f"{fmt(previous['closing'])}, but this one opens at {fmt(opening)}. Something may be "
            "missing in between. If you're sure, re-run with --allow-gap."
        )

    staged = duplicates = 0
    with conn:
        import_id = conn.execute(
            "INSERT INTO imports (account, filename, file_hash, period_start, period_end, opening, closing, row_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (account, filename, digest, summary["start"].isoformat(), summary["end"].isoformat(),
             opening, closing, len(lines)),
        ).lastrowid
        for line, ext_id in statements.external_ids(account, lines):
            cur = conn.execute(
                "INSERT OR IGNORE INTO staged (import_id, date, description, amount, external_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (import_id, line.date.isoformat(), line.description, line.amount, ext_id),
            )
            if cur.rowcount:
                staged += 1
            else:
                duplicates += 1  # seen in an earlier, overlapping statement
    return {**summary, "import_id": import_id, "lines": len(lines), "staged": staged, "duplicates": duplicates}


def inbox(conn):
    return conn.execute(
        "SELECT s.*, i.account FROM staged s JOIN imports i ON i.id = s.import_id "
        "WHERE s.status = 'pending' ORDER BY s.date, s.id"
    ).fetchall()


def _pending(conn, staged_id):
    row = conn.execute(
        "SELECT s.*, i.account FROM staged s JOIN imports i ON i.id = s.import_id WHERE s.id = ?",
        (staged_id,)).fetchone()
    if row is None:
        raise ImportError_(f"No inbox item #{staged_id}.")
    if row["status"] != "pending":
        raise ImportError_(f"Inbox item #{staged_id} is already {row['status']}.")
    return row


def approve(conn, staged_id, category):
    """Post an inbox line to the ledger against a category account.
    Money out of the bank (negative) becomes a debit to the category;
    money in (positive) becomes a credit from it."""
    row = _pending(conn, staged_id)
    if category == row["account"]:
        raise ImportError_("The category can't be the bank account itself.")
    # Money leaving the bank can't be income. (Money arriving *can* go to an
    # expense account: that's a refund, and it correctly lowers the spending.)
    if row["amount"] < 0 and category.split(":")[0] == "income":
        raise ImportError_(f"#{staged_id} is money going out ({fmt(row['amount'])}), "
                           "so it can't be income. Did you mean an expenses: account?")
    tid, _ = ledger.post(
        conn, row["date"], row["description"],
        [(row["account"], row["amount"]), (category, -row["amount"])],
        external_id=row["external_id"],
    )
    with conn:
        conn.execute("UPDATE staged SET status = 'posted', transaction_id = ? WHERE id = ?", (tid, staged_id))
    return tid


def skip(conn, staged_id):
    """Leave a line out of the ledger, e.g. when it's already been recorded another way."""
    _pending(conn, staged_id)
    with conn:
        conn.execute("UPDATE staged SET status = 'skipped' WHERE id = ?", (staged_id,))


def list_imports(conn):
    return conn.execute(
        "SELECT i.*, SUM(s.status = 'pending') AS pending FROM imports i "
        "LEFT JOIN staged s ON s.import_id = i.id GROUP BY i.id ORDER BY i.period_end"
    ).fetchall()
