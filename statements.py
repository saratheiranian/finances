"""Reading bank statement files and checking them before anything is imported.

HSBC's CSV download is modelled here as three columns: date (DD/MM/YYYY),
description, and a signed amount (negative for money out). Real exports can
differ, so the parser checks every line and reports exactly which ones it
couldn't read, rather than guessing.
"""

import csv
import hashlib
import io
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime

from money import fmt, to_pence


class StatementError(ValueError):
    pass


@dataclass(frozen=True)
class Line:
    date: date
    description: str
    amount: int  # pence; negative = money out
    line_no: int


def _looks_like_date(text):
    try:
        datetime.strptime(text.strip(), "%d/%m/%Y")
        return True
    except ValueError:
        return False


def parse_hsbc_csv(text):
    """Turn the file's text into Lines. Collects every problem before failing,
    so you can fix the file in one go."""
    lines, problems = [], []
    rows = list(csv.reader(io.StringIO(text)))
    for n, row in enumerate(rows, start=1):
        cells = [c.strip() for c in row]
        if not any(cells):
            continue  # blank line
        if n == 1 and not _looks_like_date(cells[0]):
            continue  # a header row like "Date,Description,Amount"
        if len(cells) != 3:
            problems.append(f"line {n}: expected 3 columns, found {len(cells)}")
            continue
        raw_date, description, raw_amount = cells
        try:
            when = datetime.strptime(raw_date, "%d/%m/%Y").date()
        except ValueError:
            problems.append(f"line {n}: '{raw_date}' isn't a DD/MM/YYYY date")
            continue
        try:
            amount = to_pence(raw_amount)
        except ValueError:
            problems.append(f"line {n}: '{raw_amount}' isn't an amount")
            continue
        if amount == 0:
            problems.append(f"line {n}: amount is zero")
            continue
        if not description:
            problems.append(f"line {n}: description is empty")
            continue
        lines.append(Line(when, " ".join(description.split()), amount, n))

    if problems:
        shown = "\n  ".join(problems[:10])
        more = f"\n  ...and {len(problems) - 10} more" if len(problems) > 10 else ""
        raise StatementError(f"Couldn't read {len(problems)} line(s):\n  {shown}{more}")
    if not lines:
        raise StatementError("The file has no transactions in it.")
    return lines


def check_balances(lines, opening, closing):
    """Opening balance + every transaction must equal the closing balance.
    If not, a line is missing or misread, and importing would corrupt the books."""
    total = sum(l.amount for l in lines)
    if opening + total != closing:
        raise StatementError(
            f"The statement doesn't add up: opening {fmt(opening)} plus transactions "
            f"{fmt(total)} is {fmt(opening + total)}, but the closing balance is {fmt(closing)} "
            f"(a difference of {fmt(closing - opening - total)}). Nothing was imported."
        )
    money_in = sum(l.amount for l in lines if l.amount > 0)
    money_out = -sum(l.amount for l in lines if l.amount < 0)
    return {"money_in": money_in, "money_out": money_out,
            "start": min(l.date for l in lines), "end": max(l.date for l in lines)}


def external_ids(account, lines):
    """Give every line an id that's the same every time the same bank line is seen.

    The id comes from the line's content, not its position in the file, so it
    survives re-downloads and overlapping date ranges. Two identical coffees on
    the same day are real, separate purchases, so each copy is numbered
    (#1, #2) to keep them apart instead of merging them.
    """
    seen = Counter()
    ids = []
    for l in sorted(lines, key=lambda l: (l.date, l.line_no)):
        key = (account, l.date.isoformat(), l.amount, l.description.upper())
        seen[key] += 1
        digest = hashlib.sha256(repr((*key, seen[key])).encode()).hexdigest()[:20]
        ids.append((l, f"bank:{digest}"))
    return ids


def file_hash(text):
    return hashlib.sha256(text.encode()).hexdigest()
