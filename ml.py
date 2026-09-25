"""A text classifier that learns your categories: multinomial Naive Bayes,
written from scratch.

The merchant lookup in categorise.py is precise but can only suggest shops
you've seen before. This model generalises from the *words* in descriptions
and the size of the amount, so it can make a sensible guess for a new shop.

Features for a line like "TESCO STORES 0112", -£27.84:
    words:           TESCO, STORES
    word pairs:      TESCO_STORES
    character 3-grams of each word, so "TESCOS" still resembles "TESCO"
    direction:       OUT
    amount bucket:   AMT_20_100

Naive Bayes picks the category c that maximises
    log P(c) + sum over features f of log P(f | c)
with add-one (Laplace) smoothing, so unseen features don't zero everything out.
"""

import math
from collections import Counter, defaultdict

from categorise import merchant_key, normalise

AMOUNT_BUCKETS = [(500, "0_5"), (2000, "5_20"), (10000, "20_100"), (50000, "100_500")]
# Words that say where or how you paid, not what you bought.
STOPWORDS = {"LONDON", "GB", "UK", "LTD", "CO", "COM", "WWW", "THE", "CH", "S", "A", "OF", "AND", "PAYMENT"}


def features(description, amount):
    words = [w for w in normalise(description).split() if w not in STOPWORDS] or normalise(description).split()
    feats = list(words)
    feats += [f"{a}_{b}" for a, b in zip(words, words[1:])]
    for w in words:
        padded = f"^{w}$"
        feats += [f"#{padded[i:i + 3]}" for i in range(len(padded) - 2)]
    feats.append("OUT" if amount < 0 else "IN")
    size = abs(amount)
    bucket = next((name for limit, name in AMOUNT_BUCKETS if size < limit), "500_UP")
    feats.append(f"AMT_{bucket}")
    return feats


class NaiveBayes:
    def __init__(self):
        self.class_counts = Counter()                  # examples per category
        self.feature_counts = defaultdict(Counter)     # category -> feature -> count
        self.feature_totals = Counter()                # category -> total features
        self.vocabulary = set()

    @property
    def size(self):
        return sum(self.class_counts.values())

    def learn(self, description, amount, category):
        """Add one example. Training is incremental, so the model can be
        updated line by line as you approve things."""
        feats = features(description, amount)
        self.class_counts[category] += 1
        self.feature_counts[category].update(feats)
        self.feature_totals[category] += len(feats)
        self.vocabulary.update(feats)

    def scores(self, description, amount):
        """Probability of each category, via log-probabilities then softmax."""
        if not self.class_counts:
            return {}
        feats = features(description, amount)
        total = self.size
        v = len(self.vocabulary) or 1
        logs = {}
        for c, n in self.class_counts.items():
            if amount < 0 and c.startswith("income"):
                continue  # money going out is never income
            lp = math.log(n / total)
            counts, denom = self.feature_counts[c], self.feature_totals[c] + v
            lp += sum(math.log((counts[f] + 1) / denom) for f in feats)
            logs[c] = lp
        if not logs:
            return {}
        top = max(logs.values())
        exp = {c: math.exp(lp - top) for c, lp in logs.items()}  # subtract max for numerical stability
        z = sum(exp.values())
        return {c: e / z for c, e in exp.items()}

    def predict(self, description, amount):
        s = self.scores(description, amount)
        if not s:
            return None, 0.0
        best = max(s, key=s.get)
        return best, s[best]

    def has_evidence(self, description, amount, category):
        """Naive Bayes can be confidently wrong when the only thing it recognises
        is the amount size. Require at least one word, or two character 3-grams,
        from the description to have been seen in this category before."""
        seen = self.feature_counts[category]
        feats = [f for f in features(description, amount) if not f.startswith(("AMT_", "OUT", "IN"))]
        words = [f for f in feats if not f.startswith("#")]
        return any(seen[w] for w in words) or sum(1 for g in feats if g.startswith("#") and seen[g]) >= 2

    def explain(self, description, amount, category, top=4):
        """The features that pushed hardest towards this category."""
        v = len(self.vocabulary) or 1
        counts, denom = self.feature_counts[category], self.feature_totals[category] + v
        weights = {f: math.log((counts[f] + 1) / denom) for f in set(features(description, amount)) if counts[f]}
        return [f for f, _ in sorted(weights.items(), key=lambda kv: -kv[1])[:top]]


MIN_EXAMPLES = 10       # below this the model stays quiet
MIN_CONFIDENCE = 0.5    # and it only speaks up when it's fairly sure


def training_rows(conn):
    """Approved bank lines and the category chosen for each, oldest first."""
    return conn.execute(
        "SELECT s.description, s.amount, s.date, a.name AS category FROM staged s "
        "JOIN imports i ON i.id = s.import_id "
        "JOIN entries e ON e.transaction_id = s.transaction_id "
        "JOIN accounts a ON a.id = e.account_id "
        "WHERE s.status = 'posted' AND a.name != i.account ORDER BY s.date, s.id"
    ).fetchall()


def train(conn):
    model = NaiveBayes()
    for r in training_rows(conn):
        model.learn(r["description"], r["amount"], r["category"])
    return model


def evaluate(conn):
    """Replay your history in date order. Before learning each line, ask each
    method to predict it, using only what came earlier. This measures how
    suggestions would really have performed, with no peeking at the future.

    Compares: merchant lookup alone, the model alone, and the combination the
    app actually uses (lookup first, model for shops it hasn't seen).
    """
    rows = training_rows(conn)
    model = NaiveBayes()
    lookup = defaultdict(Counter)
    tally = {k: {"right": 0, "wrong": 0} for k in ("lookup", "model", "combined")}

    def record(method, guess, truth):
        if guess is not None:
            tally[method]["right" if guess == truth else "wrong"] += 1

    for r in rows:
        desc, amount, truth = r["description"], r["amount"], r["category"]
        seen = lookup.get(merchant_key(desc))
        look = seen.most_common(1)[0][0] if seen else None
        guess, conf = model.predict(desc, amount) if model.size >= MIN_EXAMPLES else (None, 0)
        if not (guess and conf >= MIN_CONFIDENCE and model.has_evidence(desc, amount, guess)):
            guess = None
        record("lookup", look, truth)
        record("model", guess, truth)
        record("combined", look or guess, truth)
        model.learn(desc, amount, truth)
        lookup[merchant_key(desc)][truth] += 1

    n = len(rows)
    for t in tally.values():
        made = t["right"] + t["wrong"]
        t["coverage"] = made / n if n else 0.0          # how often it offered a suggestion
        t["accuracy"] = t["right"] / made if made else None  # how often that suggestion was right
    return {"examples": n, "methods": tally}


def suggest_for_inbox(conn, rows):
    """Suggestions for pending lines: merchant history first (it's rarely
    wrong), then the model for shops you haven't filed before.
    Returns {id: {"category", "confidence", "source", "because"}}."""
    import categorise
    history = categorise.learn(conn)
    model = train(conn)
    out = {}
    for r in rows:
        category, confidence = categorise.suggest(history, r["description"], r["amount"])
        if category:
            out[r["id"]] = {"category": category, "confidence": confidence, "source": "history",
                            "because": "You filed this shop here before."}
            continue
        if model.size >= MIN_EXAMPLES:
            category, confidence = model.predict(r["description"], r["amount"])
            if category and confidence >= MIN_CONFIDENCE and model.has_evidence(r["description"], r["amount"], category):
                clues = [f.lstrip("#") for f in model.explain(r["description"], r["amount"], category)
                         if not f.startswith("#")][:3]
                out[r["id"]] = {"category": category, "confidence": confidence, "source": "model",
                                "because": "Similar to past lines" + (f" ({', '.join(clues)})" if clues else "") + "."}
    return out
