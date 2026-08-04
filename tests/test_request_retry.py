import httpx
import pytest

import astrbot.core.provider.sources.request_retry as request_retry
from astrbot.core.provider.sources.request_retry import retry_provider_request


@pytest.mark.asyncio
async def test_retry_provider_request_uses_configured_max_retries(monkeypatch):
    monkeypatch.setattr(request_retry, "REQUEST_RETRY_WAIT_MIN_S", 0)
    monkeypatch.setattr(request_retry, "REQUEST_RETRY_WAIT_MAX_S", 0)

    calls = 0

    async def request():
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("temporary connection failure")

    with pytest.raises(httpx.ConnectError):
        await retry_provider_request(
            "Test",
            request,
            max_attempts=2,
        )

    assert calls == 2


class InsufficientBalanceError(Exception):
    """OpenAI-compatible 403 used to exercise the retry classification."""

    status_code = 403


def test_insufficient_account_balance_403_is_retryable():
    error = InsufficientBalanceError("Error code: 403 - Insufficient account balance")

    assert request_retry._is_retryable_provider_request_error(
        error,
        retry_rate_limits=True,
    )


def test_unrelated_permission_denied_403_is_not_retryable():
    error = InsufficientBalanceError("Error code: 403 - API key lacks permission")

    assert not request_retry._is_retryable_provider_request_error(
        error,
        retry_rate_limits=True,
    )


@pytest.mark.asyncio
async def test_retry_provider_request_retries_insufficient_account_balance(monkeypatch):
    monkeypatch.setattr(request_retry, "REQUEST_RETRY_WAIT_MIN_S", 0)
    monkeypatch.setattr(request_retry, "REQUEST_RETRY_WAIT_MAX_S", 0)

    calls = 0

    async def request():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise InsufficientBalanceError("Insufficient account balance")
        return "recovered"

    assert await retry_provider_request("Test", request, max_attempts=2) == "recovered"
    assert calls == 2
