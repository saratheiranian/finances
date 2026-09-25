"""The database schema.

Three tables: accounts, transactions, and entries. A transaction is a set of
entries that must add up to exactly zero: money is never created or destroyed,
only moved between accounts.

Sign convention: positive amounts are debits and negative amounts are credits.
Spending £5 on groceries from your bank account is:
    expenses:groceries   +500
    assets:hsbc          -500
"""

import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_DB = HERE / "ledger.db"

ACCOUNT_TYPES = ("assets", "liabilities", "equity", "income", "expenses")

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id     INTEGER PRIMARY KEY,
    name   TEXT NOT NULL UNIQUE,   -- hierarchical, e.g. 'expenses:food:groceries'
    type   TEXT NOT NULL CHECK (type IN ('assets', 'liabilities', 'equity', 'income', 'expenses')),
    opened TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transactions (
    id          INTEGER PRIMARY KEY,
    date        TEXT NOT NULL,           -- YYYY-MM-DD, when the money moved
    description TEXT NOT NULL,
    external_id TEXT UNIQUE,             -- e.g. a bank reference; makes posting idempotent
    reverses_id INTEGER UNIQUE REFERENCES transactions(id),  -- UNIQUE: reversible only once
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS entries (
    id             INTEGER PRIMARY KEY,
    transaction_id INTEGER NOT NULL REFERENCES transactions(id),
    account_id     INTEGER NOT NULL REFERENCES accounts(id),
    amount         INTEGER NOT NULL CHECK (amount != 0)  -- pence; + debit, - credit
);
CREATE INDEX IF NOT EXISTS entries_by_account ON entries(account_id);
CREATE INDEX IF NOT EXISTS entries_by_transaction ON entries(transaction_id);

-- Each statement file imported, with the balances it was checked against.
CREATE TABLE IF NOT EXISTS imports (
    id           INTEGER PRIMARY KEY,
    account      TEXT NOT NULL,
    filename     TEXT NOT NULL,
    file_hash    TEXT NOT NULL,          -- sha256 of the file: the same file can't be imported twice
    period_start TEXT NOT NULL,
    period_end   TEXT NOT NULL,
    opening      INTEGER NOT NULL,       -- pence
    closing      INTEGER NOT NULL,
    row_count    INTEGER NOT NULL,
    imported_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (account, file_hash)
);

-- The inbox: imported lines waiting to be reviewed before they touch the ledger.
-- Unlike the ledger, this table is a work area, so rows here can change status.
CREATE TABLE IF NOT EXISTS staged (
    id             INTEGER PRIMARY KEY,
    import_id      INTEGER NOT NULL REFERENCES imports(id),
    date           TEXT NOT NULL,
    description    TEXT NOT NULL,
    amount         INTEGER NOT NULL,     -- pence, as the bank shows it: negative = money out
    external_id    TEXT NOT NULL UNIQUE, -- the same bank line always gets the same id
    status         TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'posted', 'skipped')),
    transaction_id INTEGER REFERENCES transactions(id)
);

-- Monthly spending limits per category (e.g. expenses:food), in pence.
CREATE TABLE IF NOT EXISTS budgets (
    category TEXT PRIMARY KEY,
    monthly  INTEGER NOT NULL CHECK (monthly > 0)
);

-- Saved AI replies, so the same question never costs a second API call.
CREATE TABLE IF NOT EXISTS ai_cache (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL,
    created TEXT NOT NULL DEFAULT (datetime('now'))
);

-- History is permanent: the database itself refuses edits and deletions.
-- Mistakes are corrected by posting a reversal instead.
CREATE TRIGGER IF NOT EXISTS no_editing_transactions BEFORE UPDATE ON transactions
BEGIN SELECT RAISE(ABORT, 'transactions are permanent; post a reversal instead'); END;
CREATE TRIGGER IF NOT EXISTS no_deleting_transactions BEFORE DELETE ON transactions
BEGIN SELECT RAISE(ABORT, 'transactions are permanent; post a reversal instead'); END;
CREATE TRIGGER IF NOT EXISTS no_editing_entries BEFORE UPDATE ON entries
BEGIN SELECT RAISE(ABORT, 'entries are permanent; post a reversal instead'); END;
CREATE TRIGGER IF NOT EXISTS no_deleting_entries BEFORE DELETE ON entries
BEGIN SELECT RAISE(ABORT, 'entries are permanent; post a reversal instead'); END;
"""


def connect(path=DEFAULT_DB):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn
