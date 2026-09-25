"""Build a realistic ledger from the three sample statements."""
from pathlib import Path

import importer
import ledger

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
ACC = "assets:hsbc:current"
STATEMENTS = [("hsbc-sample-2026-09.csv", 85000, 147154),
              ("hsbc-sample-2026-10.csv", 147154, 168473),
              ("hsbc-sample-2026-11.csv", 168473, 191354)]

# Order matters: the first match wins, and "NETFLIX" contains "TFL".
RULES = [("NETFLIX", "expenses:subscriptions"), ("SALARY", "income:salary"), ("RENT", "expenses:rent"), ("TESCO", "expenses:food:groceries"),
         ("SAINSBURY", "expenses:food:groceries"), ("LIDL", "expenses:food:groceries"),
         ("PRET", "expenses:food:eating-out"), ("COSTA", "expenses:food:eating-out"),
         ("DELIVEROO", "expenses:food:eating-out"), ("NANDOS", "expenses:food:eating-out"),
         ("GAILS", "expenses:food:eating-out"), ("TFL", "expenses:transport"), ("UBER", "expenses:transport"),
         ("NETFLIX", "expenses:subscriptions"), ("SPOTIFY", "expenses:subscriptions"),
         ("GYM", "expenses:subscriptions"), ("THREE", "expenses:bills"), ("SAVINGS", "assets:hsbc:savings"),
         ("AMAZON", "expenses:shopping"), ("ASOS", "expenses:shopping"), ("WATERSTONES", "expenses:shopping")]

ACCOUNTS = ["assets:hsbc:current", "assets:hsbc:savings", "equity:opening", "income:salary",
            "expenses:food:groceries", "expenses:food:eating-out", "expenses:transport", "expenses:rent",
            "expenses:bills", "expenses:subscriptions", "expenses:shopping"]


def category_for(description):
    return next(cat for key, cat in RULES if key in description.upper())


def build(conn, months=3, approve=True):
    for name in ACCOUNTS:
        if not ledger.get_account(conn, name):
            ledger.open_account(conn, name)
    ledger.post(conn, "2026-08-31", "Opening balance", [(ACC, 85000), ("equity:opening", -85000)])
    for filename, opening, closing in STATEMENTS[:months]:
        importer.import_statement(conn, (SAMPLES / filename).read_text(), filename, ACC, opening, closing)
        if approve:
            for r in importer.inbox(conn):
                importer.approve(conn, r["id"], category_for(r["description"]))
    return conn
