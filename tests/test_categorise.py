import categorise
import importer
from test_importer import do_import


def test_normalise_and_merchant_key():
    assert categorise.normalise("TESCO STORES 2345") == "TESCO STORES"
    assert categorise.merchant_key("AMAZON.CO.UK, REFUND") == "AMAZON CO"
    assert categorise.merchant_key("Tesco Stores 0112") == categorise.merchant_key("TESCO STORES 2345")


def test_learns_from_approved_lines(books):
    do_import(books)
    rows = {r["description"]: r for r in importer.inbox(books)}
    tesco = [r for r in importer.inbox(books) if r["description"].startswith("TESCO")]
    importer.approve(books, tesco[0]["id"], "expenses:food:groceries")
    history = categorise.learn(books)
    assert categorise.suggest(history, tesco[1]["description"], tesco[1]["amount"]) == ("expenses:food:groceries", 1.0)
    assert categorise.suggest(history, "SOMEWHERE NEW", -100) == (None, 0.0)


def test_most_common_choice_wins():
    history = {"PRET A": {"expenses:food:eating-out": 3, "expenses:food:groceries": 1}}
    assert categorise.suggest(history, "PRET A MANGER", -680) == ("expenses:food:eating-out", 0.75)


def test_never_suggests_income_for_money_out():
    history = {"KINGS COLLEGE": {"income:salary": 2}}
    assert categorise.suggest(history, "KINGS COLLEGE", -500) == (None, 0.0)
    assert categorise.suggest(history, "KINGS COLLEGE", 500) == ("income:salary", 1.0)
