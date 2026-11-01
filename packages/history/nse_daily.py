"""NSE's public end-of-day F&O files: where they live and how to read NIFTY options from them.

NSE changed the file's name and columns on 8 July 2024. A contract with no trades that day has no
open or close here, so nothing downstream can mistake a carried-over price for a traded one. The
files are downloaded data, so they are read from memory, one expected member only, never extracted.
"""

import csv
import io
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime

ARCHIVE_HOST = "https://archives.nseindia.com"
FIRST_NEW_FORMAT = date(2024, 7, 8)
_LARGEST_MEMBER_BYTES = 64 * 1024 * 1024
_OLD_DATE_FORMATS = ("%d-%b-%Y", "%d-%B-%Y")


@dataclass(frozen=True)
class OptionRow:
    expiry: date
    strike: int
    kind: str
    open: float | None
    close: float | None
    contracts: int


def file_name(day: date) -> str:
    if day >= FIRST_NEW_FORMAT:
        return f"BhavCopy_NSE_FO_0_0_0_{day:%Y%m%d}_F_0000.csv.zip"
    return f"fo{day:%d}{day.strftime('%b').upper()}{day:%Y}bhav.csv.zip"


def file_url(day: date) -> str:
    if day >= FIRST_NEW_FORMAT:
        return f"{ARCHIVE_HOST}/content/fo/{file_name(day)}"
    month = day.strftime("%b").upper()
    return f"{ARCHIVE_HOST}/content/historical/DERIVATIVES/{day:%Y}/{month}/{file_name(day)}"


def read_member(archive_bytes: bytes) -> str:
    """The one CSV inside a daily zip, in memory. Anything else in its place raises ValueError."""
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        members = archive.infolist()
        if len(members) != 1 or not members[0].filename.lower().endswith(".csv"):
            raise ValueError("Expected exactly one CSV in the daily file")
        if members[0].file_size > _LARGEST_MEMBER_BYTES:
            raise ValueError("Daily file is larger than expected")
        return archive.read(members[0]).decode("utf-8", "replace")


def _old_expiry(text: str) -> date:
    for pattern in _OLD_DATE_FORMATS:
        try:
            return datetime.strptime(text.strip(), pattern).date()
        except ValueError:
            continue
    raise ValueError(f"Unreadable expiry {text!r}")


def _traded(price: str, contracts: int) -> float | None:
    value = float(price or 0)
    return value if contracts > 0 and value > 0 else None


def _row(expiry: date, strike: str, kind: str, opened: str, closed: str,
         contracts: str) -> OptionRow:
    count = int(float(contracts or 0))
    return OptionRow(expiry, int(float(strike)), kind.strip(), _traded(opened, count),
                     _traded(closed, count), count)


def nifty_options(text: str) -> Iterator[OptionRow]:
    """NIFTY index options from either format; an unknown header raises instead of guessing."""
    reader = csv.DictReader(io.StringIO(text))
    fields = reader.fieldnames or []
    if "INSTRUMENT" in fields:
        for line in reader:
            if line["INSTRUMENT"].strip() == "OPTIDX" and line["SYMBOL"].strip() == "NIFTY":
                yield _row(_old_expiry(line["EXPIRY_DT"]), line["STRIKE_PR"], line["OPTION_TYP"],
                           line["OPEN"], line["CLOSE"], line["CONTRACTS"])
    elif "FinInstrmTp" in fields:
        for line in reader:
            if line["FinInstrmTp"].strip() == "IDO" and line["TckrSymb"].strip() == "NIFTY":
                yield _row(date.fromisoformat(line["XpryDt"].strip()), line["StrkPric"],
                           line["OptnTp"], line["OpnPric"], line["ClsPric"], line["TtlTradgVol"])
    else:
        raise ValueError("Unknown daily file format")
