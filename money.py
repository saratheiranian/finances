"""Money is stored as a whole number of pence, never as a float.

Floats can't represent most decimal amounts exactly: in Python,
0.1 + 0.2 == 0.30000000000000004. Summing thousands of transactions with
floats slowly drifts away from the truth. Integers never do.
"""

from decimal import Decimal, InvalidOperation


def to_pence(text):
    """'12.34' -> 1234, '-5' -> -500, '£1,200.50' -> 120050."""
    cleaned = str(text).replace("£", "").replace(",", "").strip()
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        raise ValueError(f"'{text}' isn't an amount of money.")
    if amount != amount.quantize(Decimal("0.01")):
        raise ValueError(f"'{text}' has more than two decimal places.")
    return int(amount * 100)


def fmt(pence):
    """1234 -> '£12.34', -500 -> '-£5.00'."""
    sign = "-" if pence < 0 else ""
    pounds, p = divmod(abs(pence), 100)
    return f"{sign}£{pounds:,}.{p:02d}"
