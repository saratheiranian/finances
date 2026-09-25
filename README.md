# Ledger (working name)

A double-entry personal finance system. Upload bank statements, review each
transaction with machine-learned category suggestions, and see where the money
went: budgets, recurring payments, a cash-flow forecast, unusual spending, and
questions answered in plain English.

Flask and SQLite, with a command line as well as the web interface.
88 automated tests.

## The accounting core

1. **Balanced.** Every transaction's entries sum to exactly zero.
2. **Idempotent.** Re-posting an external reference changes nothing, so
   re-importing a statement is safe.
3. **Permanent.** Database triggers refuse edits and deletions. Mistakes are
   corrected with a reversal.
4. **Derived.** Balances are calculated from entries, never stored.

Money is stored as integer pence, never floats.

## Importing statements

    parse -> check balances -> check continuity -> inbox -> approve -> post

Every line is validated, with all problems reported by line number. The
statement must add up to the penny, and each statement must open where the
previous one closed. Lines wait in an inbox until approved. Each bank line gets
an id derived from its content, so overlapping downloads never double-count,
while identical purchases on the same day stay separate.

## Machine learning and analysis

**Category suggestions** come from two sources:

- *Merchant history*: normalised descriptions ("TESCO STORES 2345" and
  "TESCO STORES 0112" are the same shop), with the most common past choice.
- *A Naive Bayes classifier written from scratch* (`ml.py`), for shops not
  seen before. Features are words, word pairs, character 3-grams, direction
  and an amount bucket, with Laplace smoothing, log-probabilities and a
  numerically stable softmax. Training is incremental.

Naive Bayes is prone to confident mistakes when little evidence is shared, so
the model abstains unless the description itself shares a word or several
character 3-grams with the category, and location words like "LONDON" are
ignored. This was added after it confidently filed Lidl as shopping because of
the word "LONDON".

**Evaluation** replays the history in date order, predicting each line using
only earlier lines (no look-ahead), and reports coverage (how often a method
suggests) and accuracy (how often that suggestion is right) for history alone,
the model alone, and the combination.

**Recurring payments** are detected when every gap between a payee's payments
fits a weekly, monthly or yearly cycle. The next date comes from the median
gap. Price changes and late payments are flagged.

**Unusual amounts** use a robust z-score (median and median absolute
deviation) rather than mean and standard deviation, so a single large purchase
can't inflate the baseline and hide itself. The inbox also flags possible
duplicate charges and first-time shops before anything is approved.

**Cash-flow forecast** projects the balance 60 days ahead: recurring items on
their predicted dates, minus the daily rate of non-recurring spending. It's
deliberately simple so every number can be explained.

**Budgets** show spending against monthly limits, projecting the current
month to its end at the current pace.

## AI features (optional)

With a Gemini or Claude key in `.env`:

- **Suggest the rest**: an LLM categorises lines that neither history nor the
  model could, choosing only from your existing accounts.
- **Ask**: plain-English questions like "average Deliveroo order in October".

The design treats LLM output as untrusted input:

- For questions, the model only produces a small JSON query plan. `query.py`
  validates every field against a whitelist and computes the answer itself, so
  the model never writes SQL, never sees balances and never does arithmetic.
  The plan is shown under each answer, and the same plan can be built by hand.
- For categories, any invented category, income suggested for money going
  out, or id that wasn't sent is discarded.
- Replies are cached, and temperature is 0.

## Files

| File | Job |
| --- | --- |
| `money.py` | Text like "£12.50" to and from integer pence |
| `db.py` | Schema, including the triggers that make history permanent |
| `ledger.py` | Accounts, posting, reversals, balances, integrity audit |
| `statements.py` | Parsing and checking HSBC CSV statements |
| `importer.py` | Import pipeline, inbox, approve and skip |
| `categorise.py` | Merchant normalisation and history-based suggestions |
| `ml.py` | Naive Bayes classifier, evidence check, time-ordered evaluation |
| `analysis.py` | Recurring payments, anomalies, duplicates, budgets, forecast |
| `query.py` | Validated query plans and exact answers |
| `ai.py` | LLM client, prompts, validation, caching |
| `stats.py` | Monthly summaries and category breakdowns |
| `app.py`, `templates/`, `static/` | Flask web interface |
| `cli.py` | Command line |
| `samples/` | Three months of made-up HSBC statements |

## Running

    python3 -m pip install -r requirements.txt
    python3 -m pytest
    python3 app.py                 # http://127.0.0.1:5002

Sample statements (opening and closing balances):

| File | Opening | Closing |
| --- | --- | --- |
| `hsbc-sample-2026-09.csv` | 850.00 | 1471.54 |
| `hsbc-sample-2026-10.csv` | 1471.54 | 1684.73 |
| `hsbc-sample-2026-11.csv` | 1684.73 | 1913.54 |

To enable AI features, copy `.env.example` to `.env` and add a key.
Uploaded statements are read in memory and never saved to disk.
