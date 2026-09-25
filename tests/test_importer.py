import pytest

import importer
import ledger
from test_statements import SAMPLE

ACCOUNT = "assets:hsbc:current"


def do_import(conn, text=SAMPLE, opening=85000, closing=147154, **kw):
    return importer.import_statement(conn, text, "sample.csv", ACCOUNT, opening, closing, **kw)


def test_import_stages_everything_and_posts_nothing(books):
    r = do_import(books)
    assert (r["lines"], r["staged"], r["duplicates"]) == (20, 20, 0)
    assert len(importer.inbox(books)) == 20
    assert books.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0


def test_same_file_twice_is_refused(books):
    do_import(books)
    with pytest.raises(importer.ImportError_, match="already imported"):
        do_import(books)


def test_statement_that_doesnt_add_up_imports_nothing(books):
    with pytest.raises(Exception, match="doesn't add up"):
        do_import(books, closing=147000)
    assert books.execute("SELECT COUNT(*) FROM imports").fetchone()[0] == 0


def test_overlapping_statements_dont_duplicate(books):
    first = "01/10/2026,NETFLIX,-5.99\n02/10/2026,COSTA COFFEE,-3.95\n"
    overlap = "02/10/2026,COSTA COFFEE,-3.95\n03/10/2026,TESCO STORES 2345,-10.00\n"
    do_import(books, first, opening=10000, closing=9006)
    r = do_import(books, overlap, opening=9006, closing=7611, allow_gap=True)
    assert (r["staged"], r["duplicates"]) == (1, 1)


def test_gap_between_statements_is_caught(books):
    do_import(books)  # closes at 1471.54
    nxt = "01/10/2026,NETFLIX,-5.99\n"
    with pytest.raises(importer.ImportError_, match="Gap between statements"):
        do_import(books, nxt, opening=100000, closing=99401)
    ok = do_import(books, nxt, opening=147154, closing=146555)
    assert ok["staged"] == 1


def test_approve_posts_with_the_right_signs(books):
    do_import(books)
    rows = {r["description"]: r["id"] for r in importer.inbox(books)}
    importer.approve(books, rows["KINGS COLLEGE LONDON SALARY"], "income:salary")
    importer.approve(books, rows["SAINSBURYS S/MKTS"], "expenses:food:groceries")
    assert ledger.natural_balance(books, "income:salary") == 125000
    assert ledger.natural_balance(books, "expenses:food:groceries") == 2347
    assert ledger.natural_balance(books, ACCOUNT) == 125000 - 2347
    assert len(importer.inbox(books)) == 18
    with pytest.raises(importer.ImportError_, match="already posted"):
        importer.approve(books, rows["SAINSBURYS S/MKTS"], "expenses:food:groceries")


def test_approving_everything_matches_the_bank(books):
    ledger.post(books, "2026-08-31", "Opening balance", [(ACCOUNT, 85000), ("equity:opening", -85000)])
    do_import(books)
    for r in importer.inbox(books):
        category = "income:salary" if r["amount"] > 0 else "expenses:food:groceries"
        importer.approve(books, r["id"], category)
    assert ledger.natural_balance(books, ACCOUNT) == 147154  # the ledger agrees with HSBC to the penny
    assert ledger.check_integrity(books) == []


def test_skip(books):
    do_import(books)
    first = importer.inbox(books)[0]["id"]
    importer.skip(books, first)
    assert len(importer.inbox(books)) == 19


def test_money_out_cannot_be_income_but_refunds_can_reduce_expenses(books):
    do_import(books)
    rows = {r["description"]: r["id"] for r in importer.inbox(books)}
    with pytest.raises(importer.ImportError_, match="can't be income"):
        importer.approve(books, rows["SAINSBURYS S/MKTS"], "income:salary")
    importer.approve(books, rows["AMAZON.CO.UK, REFUND"], "expenses:food:groceries")
    assert ledger.natural_balance(books, "expenses:food:groceries") == -1599
