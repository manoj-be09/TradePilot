"""Bounded, read-only NSE daily index archive lookup for expiry review."""

import csv
import hashlib
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from io import StringIO

import httpx

MAX_ARCHIVE_BYTES = 512_000
INDEX_NAME = "Nifty 50"
REQUIRED_COLUMNS = {
    "Index Name", "Index Date", "Open Index Value", "High Index Value",
    "Low Index Value", "Closing Index Value",
}


class OfficialCloseUnavailable(ValueError):
    """The daily archive cannot provide one trustworthy expiry-date close."""


@dataclass(frozen=True)
class OfficialIndexClose:
    close: Decimal
    source_url: str
    sha256: str
    retrieved_at: datetime
    index_name: str = INDEX_NAME


def archive_url(expiry: date) -> str:
    return (
        "https://archives.nseindia.com/content/indices/"
        f"ind_close_all_{expiry:%d%m%Y}.csv"
    )


def parse_nifty_close(raw: bytes, expiry: date) -> Decimal:
    return parse_nse_index_close(raw, expiry, INDEX_NAME)


def parse_nse_index_close(raw: bytes, expiry: date, index_name: str) -> Decimal:
    if not index_name or len(index_name) > 128:
        raise OfficialCloseUnavailable("An exact NSE archive index name is required.")
    if not raw or len(raw) > MAX_ARCHIVE_BYTES:
        raise OfficialCloseUnavailable("NSE daily index archive has an invalid size.")
    try:
        reader = csv.DictReader(StringIO(raw.decode("utf-8-sig"), newline=""))
        if not reader.fieldnames or not REQUIRED_COLUMNS.issubset(reader.fieldnames):
            raise OfficialCloseUnavailable("NSE daily index archive columns are incomplete.")
        matches = [row for row in reader if row.get("Index Name") == index_name]
    except (UnicodeDecodeError, csv.Error) as error:
        raise OfficialCloseUnavailable("NSE daily index archive is malformed.") from error
    if len(matches) != 1 or matches[0].get("Index Date") != expiry.strftime("%d-%m-%Y"):
        raise OfficialCloseUnavailable(f"Exactly one matching {index_name} expiry row is required.")
    row = matches[0]
    try:
        opening, high, low, close = (
            Decimal(row[column].strip()) for column in (
                "Open Index Value", "High Index Value", "Low Index Value",
                "Closing Index Value",
            )
        )
    except (InvalidOperation, AttributeError, KeyError) as error:
        raise OfficialCloseUnavailable(
            "NSE daily index archive has invalid OHLC values."
        ) from error
    if not (
        all(
            value.is_finite() and 0 < value < 1_000_000
            and value == value.quantize(Decimal("0.01"))
            for value in (opening, high, low, close)
        )
        and low <= min(opening, close) <= max(opening, close) <= high
    ):
        raise OfficialCloseUnavailable("NSE daily index archive has invalid OHLC values.")
    return close.quantize(Decimal("0.01"))


async def fetch_nifty_close(expiry: date) -> OfficialIndexClose:
    return await fetch_nse_index_close(expiry, INDEX_NAME)


async def fetch_nse_index_close(expiry: date, index_name: str) -> OfficialIndexClose:
    if not index_name or len(index_name) > 128:
        raise OfficialCloseUnavailable("An exact NSE archive index name is required.")
    url = archive_url(expiry)
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            async with client.stream("GET", url, headers={"Accept": "text/csv"}) as response:
                if response.status_code != 200 or (
                    response.headers.get("content-type", "").split(";", 1)[0].lower()
                    != "text/csv"
                ):
                    raise OfficialCloseUnavailable("NSE daily index archive is unavailable.")
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_ARCHIVE_BYTES:
                        raise OfficialCloseUnavailable(
                            "NSE daily index archive exceeds size limit."
                        )
                    chunks.append(chunk)
    except httpx.HTTPError as error:
        raise OfficialCloseUnavailable("NSE daily index archive could not be reached.") from error
    raw = b"".join(chunks)
    return OfficialIndexClose(
        close=parse_nse_index_close(raw, expiry, index_name), source_url=url,
        sha256=hashlib.sha256(raw).hexdigest(), retrieved_at=datetime.now(timezone.utc),
        index_name=index_name,
    )
