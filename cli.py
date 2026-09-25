"""Command line for the ledger.

    python3 cli.py open assets:hsbc:current
    python3 cli.py open expenses:food:groceries
    python3 cli.py post 2026-09-25 "Tesco" expenses:food:groceries=12.50 assets:hsbc:current=-12.50
    python3 cli.py spend 2026-09-25 "Tesco" 12.50 expenses:food:groceries --from assets:hsbc:current
    python3 cli.py journal
    python3 cli.py balances
    python3 cli.py reverse 3 --reason "charged twice"
    python3 cli.py check

    python3 cli.py import samples/hsbc-sample-2026-09.csv --account assets:hsbc:current \
                          --opening 850.00 --closing 1471.54
    python3 cli.py inbox
    python3 cli.py approve 4 expenses:food:groceries
    python3 cli.py skip 7
    python3 cli.py imports
"""

import argparse
import sys
from datetime import date
from pathlib import Path

import importer
import ledger
import statements
from db import connect
from money import fmt, to_pence


def parse_entry(text):
    """'expenses:food=12.50' -> ('expenses:food', 1250)"""
    if "=" not in text:
        raise ledger.LedgerError(f"'{text}' should look like account=amount, e.g. expenses:food=12.50")
    account, amount = text.rsplit("=", 1)
    try:
        return account.strip(), to_pence(amount)
    except ValueError as e:
        raise ledger.LedgerError(str(e))


def print_journal(txns):
    if not txns:
        print("No transactions yet.")
    for t in txns:
        flag = f"  [reversed by #{t['reversed_by']}]" if t["reversed_by"] else ""
        print(f"#{t['id']:<4} {t['date']}  {t['description']}{flag}")
        for e in t["entries"]:
            print(f"        {e['name']:36} {fmt(e['amount']):>12}")


def print_balances(rows):
    if not rows:
        print("No accounts yet. Open one with: open assets:hsbc:current")
    for r in rows:
        depth = r["name"].count(":")
        if depth == 0:
            print()
        label = "  " * depth + r["name"].split(":")[-1]
        print(f"{label:36} {fmt(r['balance']):>12}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Double-entry ledger")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("open", help="open a new account")
    p.add_argument("name")

    p = sub.add_parser("post", help="post a transaction from account=amount entries")
    p.add_argument("date")
    p.add_argument("description")
    p.add_argument("entries", nargs="+")
    p.add_argument("--ref", help="external reference, e.g. from a bank statement")

    p = sub.add_parser("spend", help="shortcut: pay for something from one account")
    p.add_argument("date")
    p.add_argument("description")
    p.add_argument("amount")
    p.add_argument("category")
    p.add_argument("--from", dest="source", required=True)

    p = sub.add_parser("reverse", help="cancel a transaction by posting its opposite")
    p.add_argument("id", type=int)
    p.add_argument("--reason")

    p = sub.add_parser("journal", help="recent transactions")
    p.add_argument("--limit", type=int, default=20)

    p = sub.add_parser("balances", help="every account's balance")
    p.add_argument("--as-of")

    p = sub.add_parser("import", help="import a bank statement CSV into the inbox")
    p.add_argument("file")
    p.add_argument("--account", required=True, help="the bank account it belongs to")
    p.add_argument("--opening", required=True, help="balance before the first transaction")
    p.add_argument("--closing", required=True, help="balance after the last transaction")
    p.add_argument("--allow-gap", action="store_true", help="import even if it doesn't follow on from the last statement")

    p = sub.add_parser("approve", help="post an inbox item to the ledger")
    p.add_argument("id", type=int)
    p.add_argument("category", help="e.g. expenses:food:groceries or income:salary")

    p = sub.add_parser("skip", help="leave an inbox item out of the ledger")
    p.add_argument("id", type=int)

    sub.add_parser("inbox", help="imported lines waiting for review")
    sub.add_parser("imports", help="statements imported so far")
    sub.add_parser("accounts")
    sub.add_parser("check", help="audit the ledger")

    args = parser.parse_args(argv)
    conn = connect()
    try:
        if args.command == "open":
            print(f"Opened {ledger.open_account(conn, args.name)}.")
        elif args.command == "post":
            entries = [parse_entry(e) for e in args.entries]
            tid, created = ledger.post(conn, args.date, args.description, entries, args.ref)
            print(f"Posted #{tid}." if created else f"Already posted as #{tid}; nothing changed.")
        elif args.command == "spend":
            pence = to_pence(args.amount)
            tid, _ = ledger.post(conn, args.date, args.description,
                                 [(args.category, pence), (args.source, -pence)])
            print(f"Posted #{tid}.")
        elif args.command == "reverse":
            tid = ledger.reverse(conn, args.id, date.today(), args.reason)
            print(f"Posted reversal #{tid}.")
        elif args.command == "journal":
            print_journal(ledger.journal(conn, args.limit))
        elif args.command == "balances":
            print_balances(ledger.balances(conn, args.as_of))
        elif args.command == "import":
            path = Path(args.file).expanduser()
            if not path.exists():
                raise ledger.LedgerError(f"Can't find {path}.")
            r = importer.import_statement(conn, path.read_text(encoding="utf-8-sig"), path.name, args.account,
                                          to_pence(args.opening), to_pence(args.closing), args.allow_gap)
            print(f"Checked {r['lines']} lines from {r['start']} to {r['end']}: "
                  f"{fmt(r['money_in'])} in, {fmt(r['money_out'])} out. Balances match.")
            print(f"Added {r['staged']} to your inbox" +
                  (f", skipped {r['duplicates']} already imported." if r["duplicates"] else "."))
            print("Review them with: python3 cli.py inbox")
        elif args.command == "inbox":
            rows = importer.inbox(conn)
            if not rows:
                print("Inbox is empty.")
            for r in rows:
                print(f"[{r['id']:>3}] {r['date']}  {r['description'][:34]:34} {fmt(r['amount']):>11}")
            if rows:
                print(f"\n{len(rows)} waiting. Approve with: approve ID CATEGORY, or skip ID")
        elif args.command == "approve":
            tid = importer.approve(conn, args.id, args.category)
            print(f"Posted as transaction #{tid}.")
        elif args.command == "skip":
            importer.skip(conn, args.id)
            print(f"Skipped #{args.id}.")
        elif args.command == "imports":
            for i in importer.list_imports(conn):
                print(f"{i['period_start']} to {i['period_end']}  {i['account']:22} {i['filename']:28} "
                      f"{fmt(i['opening'])} -> {fmt(i['closing'])}  ({i['pending'] or 0} pending)")
        elif args.command == "accounts":
            for a in ledger.list_accounts(conn):
                print(a["name"])
        elif args.command == "check":
            problems = ledger.check_integrity(conn)
            print("\n".join(problems) if problems else "Ledger is balanced. Everything adds up.")
            return 1 if problems else 0
    except (ledger.LedgerError, statements.StatementError, importer.ImportError_, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
