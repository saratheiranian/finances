import sqlite3
from datetime import date

import pytest

import ledger
from ledger import LedgerError

D = date(2026, 9, 25)


def spend(conn, pence, category="expenses:food:groceries", ref=None, desc="Tesco"):
    return ledger.post(conn, D, desc, [(category, pence), ("assets:hsbc:current", -pence)], ref)


# ---------- accounts ----------

def test_account_type_comes_from_its_name(conn):
    ledger.open_account(conn, "Expenses:Food")  # normalised to lowercase
    assert ledger.get_account(conn, "expenses:food")["type"] == "expenses"


@pytest.mark.parametrize("bad", ["food", "spending:food", "expenses::food", "expenses:food groceries", ""])
def test_bad_account_names_are_rejected(conn, bad):
    with pytest.raises(LedgerError):
        ledger.open_account(conn, bad)


# ---------- rule 1: balanced ----------

def test_balanced_transaction_is_posted(books):
    tid, created = spend(books, 1250)
    assert created
    assert ledger.natural_balance(books, "expenses:food:groceries") == 1250
    assert ledger.natural_balance(books, "assets:hsbc:current") == -1250


def test_unbalanced_transaction_is_refused_and_nothing_is_saved(books):
    with pytest.raises(LedgerError, match="add up to zero"):
        ledger.post(books, D, "Oops", [("expenses:food:groceries", 1250), ("assets:hsbc:current", -1200)])
    assert books.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0


def test_split_transactions_work(books):
    ledger.post(books, D, "Big shop", [("expenses:food:groceries", 3000),
                                       ("expenses:transport", 500),
                                       ("assets:hsbc:current", -3500)])
    assert ledger.natural_balance(books, "expenses") == 3500


@pytest.mark.parametrize("entries, message", [
    ([("expenses:food:groceries", 12.5), ("assets:hsbc:current", -12.5)], "integers"),
    ([("expenses:food:groceries", 0), ("assets:hsbc:current", 0)], "zero"),
    ([("expenses:food:groceries", 100)], "at least two"),
    ([("expenses:nope", 100), ("assets:hsbc:current", -100)], "No account"),
])
def test_invalid_entries(books, entries, message):
    with pytest.raises(LedgerError, match=message):
        ledger.post(books, D, "Bad", entries)


# ---------- rule 2: idempotent ----------

def test_posting_the_same_reference_twice_is_harmless(books):
    first, created1 = spend(books, 1250, ref="HSBC-2026-09-25-001")
    again, created2 = spend(books, 1250, ref="HSBC-2026-09-25-001")
    assert (created1, created2) == (True, False)
    assert first == again
    assert ledger.natural_balance(books, "assets:hsbc:current") == -1250  # not -2500


def test_same_reference_with_different_amounts_is_an_error(books):
    spend(books, 1250, ref="HSBC-1")
    with pytest.raises(LedgerError, match="different amounts"):
        spend(books, 9999, ref="HSBC-1")


# ---------- rule 3: permanent ----------

def test_database_refuses_edits_and_deletes(books):
    tid, _ = spend(books, 1250)
    for sql in ["UPDATE transactions SET description = 'x'", "DELETE FROM transactions",
                "UPDATE entries SET amount = 1", "DELETE FROM entries"]:
        with pytest.raises(sqlite3.IntegrityError, match="permanent"):
            books.execute(sql)


def test_reversal_cancels_without_erasing_history(books):
    tid, _ = spend(books, 1250)
    rid = ledger.reverse(books, tid, D, "charged twice")
    assert ledger.natural_balance(books, "assets:hsbc:current") == 0
    assert books.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 2
    assert "charged twice" in ledger.get_transaction(books, rid)["description"]
    assert ledger.journal(books)[1]["reversed_by"] == rid


def test_cannot_reverse_twice_or_reverse_a_reversal(books):
    tid, _ = spend(books, 1250)
    rid = ledger.reverse(books, tid, D)
    with pytest.raises(LedgerError, match="already been reversed"):
        ledger.reverse(books, tid, D)
    with pytest.raises(LedgerError, match="itself a reversal"):
        ledger.reverse(books, rid, D)


# ---------- rule 4: derived balances ----------

def test_balances_roll_up_the_hierarchy(books):
    spend(books, 1000, "expenses:food:groceries")
    spend(books, 2000, "expenses:food:eating-out")
    spend(books, 300, "expenses:transport")
    assert ledger.natural_balance(books, "expenses:food") == 3000
    assert ledger.natural_balance(books, "expenses") == 3300


def test_prefix_match_respects_boundaries(books):
    ledger.open_account(books, "assets:hsbc2")
    ledger.post(books, D, "Other bank", [("assets:hsbc2", 500), ("equity:opening", -500)])
    assert ledger.natural_balance(books, "assets:hsbc") == 0  # hsbc2 isn't under hsbc


def test_income_shows_as_positive(books):
    ledger.post(books, D, "Salary", [("assets:hsbc:current", 200000), ("income:salary", -200000)])
    assert ledger.natural_balance(books, "income:salary") == 200000
    assert ledger.balance(books, "income:salary") == -200000  # raw credit balance


def test_balance_as_of_a_date(books):
    ledger.post(books, "2026-09-01", "Salary", [("assets:hsbc:current", 100000), ("income:salary", -100000)])
    ledger.post(books, "2026-09-20", "Rent", [("expenses:transport", 50000), ("assets:hsbc:current", -50000)])
    assert ledger.natural_balance(books, "assets:hsbc:current", "2026-09-10") == 100000
    assert ledger.natural_balance(books, "assets:hsbc:current") == 50000


def test_integrity_check(books):
    spend(books, 1250)
    ledger.reverse(books, 1, D)
    assert ledger.check_integrity(books) == []


def test_balances_include_parent_levels(books):
    spend(books, 1000, "expenses:food:groceries")
    spend(books, 300, "expenses:transport")
    rows = {r["name"]: r["balance"] for r in ledger.balances(books)}
    assert rows["expenses"] == 1300
    assert rows["expenses:food"] == 1000
    assert rows["assets"] == rows["assets:hsbc"] == -1300
