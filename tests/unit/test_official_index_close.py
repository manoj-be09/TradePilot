import asyncio
from datetime import date
from decimal import Decimal

import httpx
import pytest

from packages.paper_trading import official_index_close as archive

EXPIRY = date(2026, 9, 29)
ROW = "Nifty 50,29-09-2026,22732.45,22753.25,22569.65,22716.2\n"
HEADER = (
    "Index Name,Index Date,Open Index Value,High Index Value,"
    "Low Index Value,Closing Index Value\n"
)


def test_nse_archive_exact_expiry_row_and_rejects_ambiguous_or_bad_data():
    raw = (HEADER + ROW).encode()
    assert archive.parse_nifty_close(raw, EXPIRY) == Decimal("22716.20")
    for bad in (
        (HEADER + ROW + ROW).encode(),
        (HEADER + ROW.replace("29-09-2026", "28-09-2026")).encode(),
        (HEADER + ROW.replace("22716.2", "NaN")).encode(),
        (HEADER + ROW.replace("22716.2", "99999")).encode(),
        b"x" * (archive.MAX_ARCHIVE_BYTES + 1),
    ):
        with pytest.raises(archive.OfficialCloseUnavailable):
            archive.parse_nifty_close(bad, EXPIRY)


def test_selected_index_never_borrows_nifty_close():
    bank_row = "Nifty Bank,29-09-2026,50000,50500,49500,50100\n"
    raw = (HEADER + ROW + bank_row).encode()
    assert archive.parse_nse_index_close(raw, EXPIRY, "Nifty Bank") == Decimal("50100.00")
    assert archive.parse_nifty_close(raw, EXPIRY) == Decimal("22716.20")
    for bad, name in (
        ((HEADER + ROW).encode(), "Nifty Bank"),
        ((HEADER + ROW + bank_row + bank_row).encode(), "Nifty Bank"),
        (raw, "SENSEX"),
        (raw, ""),
    ):
        with pytest.raises(archive.OfficialCloseUnavailable):
            archive.parse_nse_index_close(bad, EXPIRY, name)


def test_underlying_routes_to_exact_official_name_and_unknown_fails_closed(monkeypatch):
    seen = []

    async def fetch(expiry, name):
        seen.append((expiry, name))
        return name

    monkeypatch.setattr(archive, "fetch_nse_index_close", fetch)
    for symbol, name in archive.NSE_INDEX_NAMES.items():
        assert asyncio.run(archive.fetch_index_close(EXPIRY, symbol)) == name
    assert len(seen) == 5
    for unsupported in ("SENSEX", "BANKEX", "NIFTYFPI", "banknifty", "UNKNOWN"):
        with pytest.raises(archive.OfficialCloseUnavailable):
            asyncio.run(archive.fetch_index_close(EXPIRY, unsupported))
    assert len(seen) == 5


def test_fetch_is_bounded_to_fixed_archive_and_rejects_redirects(monkeypatch):
    original_client = httpx.AsyncClient
    seen: list[str] = []

    def client_factory(**kwargs):
        assert kwargs["follow_redirects"] is False

        def handle(request):
            seen.append(str(request.url))
            return httpx.Response(200, headers={"content-type": "text/csv"},
                                  content=(HEADER + ROW).encode())

        return original_client(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(archive.httpx, "AsyncClient", client_factory)
    result = asyncio.run(archive.fetch_nifty_close(EXPIRY))
    assert seen == [archive.archive_url(EXPIRY)]
    assert result.close == Decimal("22716.20")
    assert len(result.sha256) == 64
    assert result.index_name == "Nifty 50"

    # A selected index must be present; a valid Nifty row is never a fallback.
    with pytest.raises(archive.OfficialCloseUnavailable):
        asyncio.run(archive.fetch_nse_index_close(EXPIRY, "Nifty Bank"))

    def selected_client(**kwargs):
        bank_row = "Nifty Bank,29-09-2026,50000,50500,49500,50100\n"
        return original_client(transport=httpx.MockTransport(
            lambda _: httpx.Response(200, headers={"content-type": "text/csv"},
                                     content=(HEADER + ROW + bank_row).encode())
        ), **kwargs)

    monkeypatch.setattr(archive.httpx, "AsyncClient", selected_client)
    selected = asyncio.run(archive.fetch_nse_index_close(EXPIRY, "Nifty Bank"))
    assert selected.close == Decimal("50100.00")
    assert selected.index_name == "Nifty Bank"
    assert selected.source_url == archive.archive_url(EXPIRY)
    assert selected.sha256 != result.sha256

    def redirect_factory(**kwargs):
        return original_client(transport=httpx.MockTransport(
            lambda _: httpx.Response(302, headers={"location": "https://example.com"})
        ), **kwargs)

    monkeypatch.setattr(archive.httpx, "AsyncClient", redirect_factory)
    with pytest.raises(archive.OfficialCloseUnavailable):
        asyncio.run(archive.fetch_nifty_close(EXPIRY))
