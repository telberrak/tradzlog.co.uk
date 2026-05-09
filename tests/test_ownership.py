from typing import Any

import pytest
from fastapi import HTTPException

from tradzlog_api.main import load_journal, load_trade, owned_account


class FakeSession:
    def __init__(self, result: object | None) -> None:
        self.result = result
        self.statement: Any = None

    def scalar(self, statement: Any) -> object | None:
        self.statement = statement
        return self.result


def compiled_sql(statement: Any) -> str:
    return str(statement.compile(compile_kwargs={"literal_binds": True}))


def test_owned_account_lookup_scopes_by_account_and_user() -> None:
    session = FakeSession(result=object())

    assert owned_account(session, "user-1", "account-1") is session.result  # type: ignore[arg-type]

    sql = compiled_sql(session.statement)
    assert "accounts.id = 'account-1'" in sql
    assert "accounts.user_id = 'user-1'" in sql


def test_owned_account_returns_404_for_missing_or_cross_user_account() -> None:
    session = FakeSession(result=None)

    with pytest.raises(HTTPException) as exc:
        owned_account(session, "user-1", "account-2")  # type: ignore[arg-type]

    assert exc.value.status_code == 404


def test_load_trade_lookup_scopes_by_trade_and_user() -> None:
    session = FakeSession(result=object())

    assert load_trade(session, "user-1", "trade-1") is session.result  # type: ignore[arg-type]

    sql = compiled_sql(session.statement)
    assert "trades.id = 'trade-1'" in sql
    assert "trades.user_id = 'user-1'" in sql


def test_load_journal_lookup_scopes_by_journal_and_user() -> None:
    session = FakeSession(result=object())

    assert load_journal(session, "user-1", "journal-1") is session.result  # type: ignore[arg-type]

    sql = compiled_sql(session.statement)
    assert "journal_entries.id = 'journal-1'" in sql
    assert "journal_entries.user_id = 'user-1'" in sql
