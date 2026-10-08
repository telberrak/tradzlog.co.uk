from collections.abc import Iterator

import pytest

from tradzlog_web.context import CURRENT_CSRF, CURRENT_USER_ID


@pytest.fixture
def signed_in() -> Iterator[str]:
    """Simulate the auth dependency for tests that call web handlers directly (user id "user-1")."""
    user_token = CURRENT_USER_ID.set("user-1")
    csrf_token = CURRENT_CSRF.set("test-csrf")
    yield "user-1"
    CURRENT_USER_ID.reset(user_token)
    CURRENT_CSRF.reset(csrf_token)
