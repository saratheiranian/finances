"""Optional AI features, via an LLM (Gemini or Claude).

  suggest_categories   categorises inbox lines that the lookup and the model
                       couldn't, choosing only from your existing accounts
  plan_question        turns a question in plain English into a query plan,
                       which query.py validates and runs itself

Two safety principles run through both:
  1. The model's output is untrusted. Every value is checked against a
     whitelist before it's used, and anything unexpected is dropped.
  2. The model never touches the numbers. It never sees your balances, never
     writes SQL and never calculates totals; the app does all of that.

With no API key, everything else in the app works as normal. Replies are
cached in the database, so repeating a request costs nothing.
"""

import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent


class AIError(Exception):
    pass


def load_dotenv(path=HERE / ".env"):
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def provider():
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "claude"
    return None


def enabled():
    return provider() is not None


def _post_json(url, headers, body):
    request = urllib.request.Request(url, data=json.dumps(body).encode(),
                                     headers={"content-type": "application/json", **headers})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as e:
        raise AIError(f"The AI service returned an error ({e.code}): {e.read().decode(errors='replace')[:300]}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise AIError(f"Couldn't reach the AI service: {e}") from e


def complete(prompt, system, max_tokens=600):
    which = provider()
    if which == "gemini":
        model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
        data = _post_json(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            {"x-goog-api-key": os.environ["GEMINI_API_KEY"]},
            {"system_instruction": {"parts": [{"text": system}]},
             "contents": [{"parts": [{"text": prompt}]}],
             "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0}})
        try:
            return "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"]).strip()
        except (KeyError, IndexError) as e:
            raise AIError("The AI service sent back an empty reply.") from e
    if which == "claude":
        model = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
        data = _post_json(
            "https://api.anthropic.com/v1/messages",
            {"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"},
            {"model": model, "max_tokens": max_tokens, "system": system, "temperature": 0,
             "messages": [{"role": "user", "content": prompt}]})
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()
    raise AIError("No AI key set. Add GEMINI_API_KEY or ANTHROPIC_API_KEY to a .env file.")


def cached(conn, key_parts, make):
    key = hashlib.sha256(json.dumps(key_parts, sort_keys=True, default=str).encode()).hexdigest()
    row = conn.execute("SELECT value FROM ai_cache WHERE key = ?", (key,)).fetchone()
    if row:
        return row["value"]
    value = make()
    with conn:
        conn.execute("INSERT OR REPLACE INTO ai_cache (key, value) VALUES (?, ?)", (key, value))
    return value


def extract_json(text):
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise AIError("The AI reply wasn't in the expected format.")
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError as e:
        raise AIError("The AI reply wasn't valid JSON.") from e


def suggest_categories(conn, lines, categories):
    """lines: [{'id', 'description', 'amount'}]. Returns {id: category} for the
    lines the model placed confidently, using only names from `categories`."""
    if not lines:
        return {}
    system = ("You categorise UK bank transactions into a person's own ledger accounts. "
              "Reply with one JSON object only.")
    listing = "\n".join(f'{l["id"]}: "{l["description"]}" {"money out" if l["amount"] < 0 else "money in"}'
                        for l in lines)
    prompt = (f"Allowed categories:\n{json.dumps(sorted(categories))}\n\n"
              f"Transactions:\n{listing}\n\n"
              'Reply as {"<id>": "<category>", ...}. Use only the allowed categories, exactly as written. '
              "Use null if you are unsure. Money out is never income.")
    key = ["categorise", sorted((l["description"], l["amount"] < 0) for l in lines), sorted(categories)]
    data = extract_json(cached(conn, key, lambda: complete(prompt, system)))
    by_desc = {l["id"]: l for l in lines}
    out = {}
    for raw_id, category in data.items():
        try:
            line = by_desc[int(raw_id)]
        except (ValueError, KeyError):
            continue  # an id we didn't send: ignore it
        if category not in categories:
            continue  # invented or misspelled category: ignore it
        if line["amount"] < 0 and category.startswith("income"):
            continue
        out[line["id"]] = category
    return out


def plan_question(conn, question, today, account_names, months):
    """Turn a plain-English question into a query plan (see query.py)."""
    question = question.strip()[:300]
    if not question:
        raise AIError("Ask a question first.")
    system = ("You translate questions about personal spending into a JSON query plan. "
              "You never answer the question yourself. Reply with one JSON object only.")
    prompt = f"""Today is {today.isoformat()}. Data exists for these months: {', '.join(months) or 'none'}.
Categories: {json.dumps([n for n in account_names if n.split(':')[0] in ('expenses', 'income')])}

Plan format:
{{"metric": "total"|"count"|"average"|"largest"|"list",
  "flow": "spending"|"income",
  "category": one of the categories or null,
  "merchant": a shop name to search descriptions for, or null,
  "start": "YYYY-MM-DD", "end": "YYYY-MM-DD",
  "group_by": null|"month"|"category"|"merchant"}}

"last month" means the most recent full month with data. With no period given, use all months with data.

Question: {question}"""
    return extract_json(cached(conn, ["plan", question.lower(), today.isoformat(), account_names, months],
                               lambda: complete(prompt, system, 300)))
