"""Web interface for the ledger. Run with:  python3 app.py  then open http://127.0.0.1:5002

    /           Overview: balances, this month's spending, trends
    /upload     Upload a bank statement CSV
    /inbox      Review imported lines, with suggested categories, and approve in bulk
    /insights   Recurring payments, cash-flow forecast, unusual spending, model accuracy
    /ask        Questions in plain English, answered from your data
    /journal    Every transaction, with reversals
    /accounts   Open accounts and record opening balances
"""

from datetime import date

from flask import Flask, flash, g, jsonify, redirect, render_template, request, url_for

import ai
import analysis
import importer
import ledger
import ml
import query
import stats
import statements
from db import connect
from money import fmt, to_pence

ai.load_dotenv()
app = Flask(__name__)
app.secret_key = "dev"  # needed for flash messages; fine for a local-only app
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024  # refuse uploads over 2 MB

KNOWN_ERRORS = (ledger.LedgerError, statements.StatementError, importer.ImportError_, query.QueryError, ValueError)

app.jinja_env.filters["money"] = fmt
app.jinja_env.filters["month_label"] = stats.month_label


def get_db():
    if "db" not in g:
        g.db = connect()
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


@app.context_processor
def inbox_count():
    if request.endpoint in (None, "static"):
        return {}
    return {"inbox_count": len(importer.inbox(get_db())), "ai_enabled": ai.enabled()}


def done(message, to, error=False):
    flash(message, "error" if error else "ok")
    return redirect(to)


# ---------- overview ----------

@app.get("/")
def overview():
    db = get_db()
    months = stats.months_with_data(db)
    month = request.args.get("month") or (months[0] if months else date.today().strftime("%Y-%m"))
    return render_template(
        "overview.html",
        month=month, months=months,
        summary=stats.month_summary(db, month),
        categories=stats.by_category(db, month),
        trend=stats.monthly_trend(db, 6, until=month),
        balances=[b for b in ledger.balances(db) if b["type"] in ("assets", "liabilities")],
        has_accounts=bool(ledger.list_accounts(db)),
        budgets=analysis.budget_progress(db, month),
        unusual=analysis.unusual_spending(db, month),
        expense_categories=sorted({":".join(a["name"].split(":")[:2]) for a in ledger.list_accounts(db)
                                   if a["type"] == "expenses" and ":" in a["name"]}),
    )


@app.post("/budgets")
def budgets():
    try:
        analysis.set_budget(get_db(), request.form["category"], to_pence(request.form["amount"] or "0"))
    except KNOWN_ERRORS as e:
        return done(str(e), request.referrer or url_for("overview"), error=True)
    return done("Budget saved.", request.referrer or url_for("overview"))


# ---------- upload ----------

def bank_accounts(db):
    return [a for a in ledger.list_accounts(db) if a["type"] in ("assets", "liabilities")]


@app.get("/upload")
def upload():
    return render_template("upload.html", accounts=bank_accounts(get_db()), imports=importer.list_imports(get_db()))


@app.post("/upload")
def upload_post():
    db, f = get_db(), request.form
    file = request.files.get("statement")
    if not file or not file.filename:
        return done("Choose a CSV file to upload.", url_for("upload"), error=True)
    if not file.filename.lower().endswith(".csv"):
        return done("That isn't a .csv file. Download your statement from HSBC as CSV.", url_for("upload"), error=True)
    try:
        # Read the file in memory; it's never saved to disk.
        text = file.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        return done("Couldn't read that file as text. Is it definitely a CSV?", url_for("upload"), error=True)
    try:
        r = importer.import_statement(db, text, file.filename, f["account"], to_pence(f["opening"]),
                                      to_pence(f["closing"]), allow_gap=bool(f.get("allow_gap")))
    except KNOWN_ERRORS as e:
        return done(str(e), url_for("upload"), error=True)
    extra = f" {r['duplicates']} were already imported and skipped." if r["duplicates"] else ""
    return done(f"Statement checked: {r['lines']} lines, balances match to the penny. "
                f"{r['staged']} added to your inbox.{extra}", url_for("inbox"))


# ---------- inbox ----------

@app.get("/inbox")
def inbox():
    db = get_db()
    pending = importer.inbox(db)
    suggestions = ml.suggest_for_inbox(db, pending)
    warnings = analysis.inbox_warnings(db, pending)
    rows = [{**dict(r), "suggestion": suggestions.get(r["id"]), "warnings": warnings.get(r["id"], [])}
            for r in pending]
    categories = [a["name"] for a in ledger.list_accounts(db)]
    return render_template("inbox.html", rows=rows, categories=categories,
                           unsuggested=sum(1 for r in rows if not r["suggestion"]))


def category_choices(db):
    return [a["name"] for a in ledger.list_accounts(db) if a["type"] != "equity"]


@app.post("/inbox/ai-suggest")
def inbox_ai_suggest():
    """Ask the AI to categorise the lines nothing else could. Returns JSON for the page to fill in."""
    db = get_db()
    pending = importer.inbox(db)
    known = ml.suggest_for_inbox(db, pending)
    lines = [{"id": r["id"], "description": r["description"], "amount": r["amount"]}
             for r in pending if r["id"] not in known]
    bank = {r["account"] for r in pending}
    try:
        result = ai.suggest_categories(db, lines, [c for c in category_choices(db) if c not in bank])
    except ai.AIError as e:
        return jsonify(ok=False, error=str(e)), 502
    return jsonify(ok=True, suggestions={str(k): v for k, v in result.items()}, asked=len(lines))


@app.post("/inbox/approve")
def inbox_approve():
    db = get_db()
    ids = request.form.getlist("ids")
    if not ids:
        return done("Tick at least one line to approve.", url_for("inbox"), error=True)
    approved, problems = 0, []
    for staged_id in ids:
        category = request.form.get(f"category_{staged_id}", "").strip()
        if not category:
            problems.append(f"#{staged_id}: choose a category")
            continue
        try:
            importer.approve(db, int(staged_id), category)
            approved += 1
        except KNOWN_ERRORS as e:
            problems.append(f"#{staged_id}: {e}")
    message = f"Approved {approved} line{'' if approved == 1 else 's'}."
    if problems:
        message += " Not approved: " + "; ".join(problems)
    return done(message, url_for("inbox"), error=bool(problems))


@app.post("/inbox/<int:staged_id>/skip")
def inbox_skip(staged_id):
    try:
        importer.skip(get_db(), staged_id)
    except KNOWN_ERRORS as e:
        return done(str(e), url_for("inbox"), error=True)
    return done(f"Skipped #{staged_id}.", url_for("inbox"))


# ---------- insights ----------

@app.get("/insights")
def insights():
    db = get_db()
    banks = [a["name"] for a in bank_accounts(db) if a["type"] == "assets"]
    account = request.args.get("account") or (banks[0] if banks else None)
    fc = analysis.forecast(db, account) if account else None
    path, zero_y = analysis.chart_path(fc["points"]) if fc else ("", None)
    recurring = analysis.detect_recurring(db)
    return render_template(
        "insights.html", recurring=recurring, forecast=fc, chart=path, zero_y=zero_y,
        banks=banks, account=account, as_of=analysis.reference_date(db),
        evaluation=ml.evaluate(db), min_examples=ml.MIN_EXAMPLES,
        yearly_subscriptions=sum(r["yearly_cost"] for r in recurring if r["outgoing"]),
    )


# ---------- ask ----------

@app.route("/ask", methods=["GET", "POST"])
def ask():
    db = get_db()
    names = [a["name"] for a in ledger.list_accounts(db)]
    months = sorted(stats.months_with_data(db))
    context = {"categories": [n for n in names if n.split(":")[0] in ("expenses", "income")], "months": months}
    result, plan, question, error = None, None, "", None
    if request.method == "POST":
        question = request.form.get("question", "").strip()
        try:
            if question:
                raw = ai.plan_question(db, question, analysis.reference_date(db), names, months)
            else:  # the manual form: same plan, filled in by hand
                raw = {k: request.form.get(k) or None for k in
                       ("metric", "flow", "category", "merchant", "start", "end", "group_by")}
            plan = query.validate(raw, names)
            result = query.run(db, plan)
        except (ai.AIError, query.QueryError) as e:
            error = str(e)
    default_start = f"{months[0]}-01" if months else ""
    default_end = analysis.reference_date(db).isoformat()
    plan_json = {**plan, "start": plan["start"].isoformat(), "end": plan["end"].isoformat()} if plan else None
    return render_template("ask.html", result=result, plan=plan, plan_json=plan_json, question=question, error=error,
                           default_start=default_start, default_end=default_end, **context,
                           metrics=query.METRICS, groups=query.GROUPS)


# ---------- journal ----------

@app.get("/journal")
def journal():
    return render_template("journal.html", txns=ledger.journal(get_db(), limit=200))


@app.post("/reverse/<int:transaction_id>")
def reverse(transaction_id):
    try:
        rid = ledger.reverse(get_db(), transaction_id, date.today(), request.form.get("reason") or None)
    except KNOWN_ERRORS as e:
        return done(str(e), url_for("journal"), error=True)
    return done(f"Reversed #{transaction_id} with #{rid}.", url_for("journal"))


# ---------- accounts ----------

@app.get("/accounts")
def accounts():
    db = get_db()
    return render_template("accounts.html", balances=ledger.balances(db), bank_accounts=bank_accounts(db))


@app.post("/accounts")
def accounts_open():
    try:
        name = ledger.open_account(get_db(), request.form["name"])
    except KNOWN_ERRORS as e:
        return done(str(e), url_for("accounts"), error=True)
    return done(f"Opened {name}.", url_for("accounts"))


@app.post("/accounts/opening-balance")
def opening_balance():
    """Record what an account held before your first statement, balanced against equity."""
    db, f = get_db(), request.form
    try:
        if not ledger.get_account(db, "equity:opening"):
            ledger.open_account(db, "equity:opening")
        pence = to_pence(f["amount"])
        ledger.post(db, f["date"], f"Opening balance: {f['account']}",
                    [(f["account"], pence), ("equity:opening", -pence)])
    except KNOWN_ERRORS as e:
        return done(str(e), url_for("accounts"), error=True)
    return done(f"Recorded an opening balance of {fmt(pence)} for {f['account']}.", url_for("accounts"))


if __name__ == "__main__":
    # Port 5002, so it can run alongside the review tracker on 5001.
    app.run(port=5002, debug=True)
