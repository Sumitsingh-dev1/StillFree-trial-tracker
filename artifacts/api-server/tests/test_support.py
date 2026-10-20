"""Ephemeral test inputs; never reuse deployment credentials in tests."""

from secrets import token_urlsafe

TEST_PASSWORD = token_urlsafe(24)
