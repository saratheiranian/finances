import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import connect  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "test.db")
    yield c
    c.close()


@pytest.fixture
def books(conn):
    """A ledger with a few standard accounts already open."""
    import ledger
    for name in ["assets:hsbc:current", "assets:hsbc:savings", "expenses:food:groceries",
                 "expenses:food:eating-out", "expenses:transport", "income:salary", "equity:opening"]:
        ledger.open_account(conn, name)
    return conn
