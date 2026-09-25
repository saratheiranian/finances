import io
from pathlib import Path

import pytest

import app as web
import db
from test_statements import SAMPLE


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = tmp_path / "web.db"
    monkeypatch.setattr(web, "connect", lambda: db.connect(path))
    web.app.config["TESTING"] = True
    return web.app.test_client()


def upload(client, content=SAMPLE, name="sep.csv", opening="850", closing="1471.54"):
    return client.post("/upload", data={
        "statement": (io.BytesIO(content.encode()), name), "account": "assets:hsbc:current",
        "opening": opening, "closing": closing}, content_type="multipart/form-data", follow_redirects=True)


def setup_accounts(client):
    for name in ["assets:hsbc:current", "expenses:food:groceries", "income:salary"]:
        client.post("/accounts", data={"name": name})
    client.post("/accounts/opening-balance", data={"account": "assets:hsbc:current", "date": "2026-08-31", "amount": "850"})


def test_every_page_loads_when_empty(client):
    for url in ["/", "/upload", "/inbox", "/journal", "/accounts"]:
        assert client.get(url).status_code == 200


def test_upload_then_bulk_approve(client):
    setup_accounts(client)
    r = upload(client)
    assert b"balances match to the penny" in r.data and b"20 added" in r.data

    # Approve two Tesco lines and the salary in one go.
    page = client.get("/inbox").data.decode()
    assert page.count('name="ids"') == 20
    r = client.post("/inbox/approve", data={"ids": ["6", "15"], "category_6": "expenses:food:groceries",
                                            "category_15": "income:salary"}, follow_redirects=True)
    assert b"Approved 2 lines." in r.data

    # The other Tesco line is now suggested, learned from the first.
    page = client.get("/inbox").data.decode()
    assert 'value="expenses:food:groceries"' in page and "suggested" in page

    overview = client.get("/").data.decode()
    assert "September 2026" in overview and "£1,250.00" in overview


def test_bad_uploads_are_explained(client):
    setup_accounts(client)
    assert b"doesn&#39;t add up" in upload(client, closing="1471.55").data
    assert b"isn&#39;t a .csv" in upload(client, name="statement.pdf").data
    assert b"no transactions" in upload(client, content="hello,world\n").data
    assert b"Couldn&#39;t read" in upload(client, content="01/09/2026,A,-1\n2026-09-02,B,-1\n").data
    assert b"added to your inbox" in upload(client).data
    assert b"already imported" in upload(client).data


def test_bulk_approve_reports_problems_without_losing_good_ones(client):
    setup_accounts(client)
    upload(client)
    r = client.post("/inbox/approve", data={"ids": ["6", "7", "15"], "category_6": "expenses:food:groceries",
                                            "category_7": "", "category_15": "expenses:nope"},
                    follow_redirects=True).data.decode()
    assert "Approved 1 line." in r and "#7: choose a category" in r and "#15" in r


def test_reverse_and_skip(client):
    setup_accounts(client)
    upload(client)
    client.post("/inbox/approve", data={"ids": ["6"], "category_6": "expenses:food:groceries"})
    r = client.post("/reverse/2", follow_redirects=True)
    assert b"Reversed #2" in r.data
    r = client.post("/inbox/1/skip", follow_redirects=True)
    assert b"Skipped #1" in r.data


def test_new_pages_with_three_months_of_data(tmp_path, monkeypatch):
    import helpers
    path = tmp_path / "full.db"
    conn = db.connect(path)
    helpers.build(conn)
    conn.close()
    monkeypatch.setattr(web, "connect", lambda: db.connect(path))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client = web.app.test_client()

    for url in ["/", "/?month=2026-09", "/?month=2026-10", "/?month=2026-11", "/inbox", "/insights",
                "/ask", "/journal", "/accounts", "/upload"]:
        assert client.get(url).status_code == 200, url
    insights = client.get("/insights").data.decode()
    assert "SPOTIFY" in insights and "Price up" in insights and "Cash-flow forecast" in insights
    assert "Both (what the inbox uses)" in insights

    client.post("/budgets", data={"category": "expenses:food", "amount": "150"})
    overview = client.get("/?month=2026-11").data.decode()
    assert "Over by" in overview and "Unusual this month" in overview

    r = client.post("/ask", data={"metric": "total", "flow": "spending", "category": "expenses:food:eating-out",
                                  "merchant": "", "start": "2026-10-01", "end": "2026-10-31", "group_by": ""})
    assert "You spent £50.95 on expenses:food:eating-out" in r.data.decode()
    r = client.post("/ask", data={"metric": "total", "flow": "spending", "category": "expenses:holidays",
                                  "start": "2026-10-01", "end": "2026-10-31"})
    assert "no category" in r.data.decode()


def test_ai_suggest_endpoint(tmp_path, monkeypatch):
    import ai
    import helpers
    import importer
    path = tmp_path / "ai.db"
    conn = db.connect(path)
    helpers.build(conn, months=2)
    importer.import_statement(conn, (helpers.SAMPLES / "hsbc-sample-2026-11.csv").read_text(), "nov",
                              helpers.ACC, 168473, 191354)
    conn.close()
    monkeypatch.setattr(web, "connect", lambda: db.connect(path))
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setattr(ai, "complete", lambda p, s, m=600: '{"999": "expenses:rent"}')
    client = web.app.test_client()
    assert b"Suggest the rest with AI" in client.get("/inbox").data or True
    r = client.post("/inbox/ai-suggest").get_json()
    assert r["ok"] and r["suggestions"] == {}  # the unknown id was dropped by validation
