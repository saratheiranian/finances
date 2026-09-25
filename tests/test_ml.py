import importer
import ml
from helpers import build, category_for


def test_features():
    f = ml.features("TESCO STORES 0112", -2784)
    assert "TESCO" in f and "TESCO_STORES" in f and "#^TE" in f
    assert "OUT" in f and "AMT_20_100" in f


def test_naive_bayes_learns_and_generalises():
    m = ml.NaiveBayes()
    for desc, amt, cat in [("TESCO STORES 1", -2000, "groceries"), ("SAINSBURYS S/MKTS", -2500, "groceries"),
                           ("PRET A MANGER", -600, "eating-out"), ("COSTA COFFEE", -395, "eating-out"),
                           ("TFL TRAVEL CH", -560, "transport")]:
        m.learn(desc, amt, cat)
    assert m.predict("TESCO EXPRESS 44", -1500)[0] == "groceries"   # unseen store, shared word
    scores = m.scores("COSTA COFFEE", -395)
    assert abs(sum(scores.values()) - 1) < 1e-9                        # a proper probability distribution


def test_never_predicts_income_for_money_out():
    m = ml.NaiveBayes()
    m.learn("KINGS COLLEGE SALARY", 125000, "income:salary")
    m.learn("RENT", -65000, "expenses:rent")
    assert m.predict("KINGS COLLEGE SALARY", -125000)[0] == "expenses:rent"


def test_model_suggests_for_unseen_shops(books):
    build(books, months=2)                   # learn from September and October
    importer.import_statement(books, (__import__("helpers").SAMPLES / "hsbc-sample-2026-11.csv").read_text(),
                              "nov", "assets:hsbc:current", 168473, 191354)
    pending = importer.inbox(books)
    suggestions = ml.suggest_for_inbox(books, pending)
    by_desc = {r["description"]: suggestions.get(r["id"]) for r in pending}
    assert by_desc["NETFLIX"]["source"] == "history"
    assert by_desc["TESCO STORES 2345"]["category"] == "expenses:food:groceries"
    # LIDL and GAILS share no words with anything seen before, so the model
    # abstains rather than guessing from the amount alone.
    assert by_desc["LIDL GB LONDON"] is None and by_desc["GAILS BAKERY"] is None


def test_model_abstains_without_evidence_but_generalises_with_it(books):
    build(books, months=2)
    model = ml.train(books)
    assert model.has_evidence("TESCO EXPRESS", -1500, "expenses:food:groceries")
    assert model.predict("TESCO EXPRESS", -1500)[0] == "expenses:food:groceries"
    assert not model.has_evidence("LIDL GB LONDON", -1742, "expenses:shopping")


def test_evaluation_replays_history_without_peeking(books):
    build(books)
    e = ml.evaluate(books)
    assert e["examples"] == 63
    combined = e["methods"]["combined"]
    assert combined["coverage"] >= e["methods"]["lookup"]["coverage"]
    assert combined["accuracy"] > 0.8
