"""Download NSE's public daily F&O files into a folder of their own. Resumable and unhurried.

Only these public archive files are fetched. A refusal stops the run rather than trying another way
in, and a total size limit stops it if the files turn out far larger than expected. The files are
private working data and stay under `work/`. Run with `python -I` so nothing beside them can load.
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.history.nse_daily import ARCHIVE_HOST, file_name, file_url, read_member  # noqa: E402

_USER_AGENT = "TradePilot-research/1.0 (personal research; github.com/manoj-be09/TradePilot)"
_TIMEOUT_SECONDS = 60
_REFUSALS_BEFORE_STOP = 3
_ERRORS_BEFORE_STOP = 5


def _calendar(path: Path | None, first: date, last: date) -> list[date]:
    if path is not None:
        days = (date.fromisoformat(key) for key in json.loads(path.read_text(encoding="utf-8")))
        return sorted(day for day in days if first <= day <= last)
    span = (last - first).days + 1
    every = (first + timedelta(days=offset) for offset in range(span))
    return [day for day in every if day.weekday() < 5]


def _fetch(url: str) -> bytes:
    if not url.startswith(f"{ARCHIVE_HOST}/"):
        raise ValueError("Refusing a host other than NSE's archive")
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:  # noqa: S310  # nosec B310
        return bytes(response.read())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--first", type=date.fromisoformat, required=True)
    parser.add_argument("--last", type=date.fromisoformat, required=True)
    parser.add_argument("--calendar", type=Path,
                        help="JSON keyed by session date; else every weekday")
    parser.add_argument("--pause", type=float, default=1.0)
    parser.add_argument("--largest-total-mb", type=int, default=1500)
    arguments = parser.parse_args()
    arguments.directory.mkdir(parents=True, exist_ok=True)
    limit = arguments.largest_total_mb * 1024 * 1024
    total = sum(path.stat().st_size for path in arguments.directory.glob("*.zip"))
    fetched = present = absent = refusals = errors = 0
    missing: list[str] = []
    days = _calendar(arguments.calendar, arguments.first, arguments.last)
    for at, day in enumerate(days):
        target = arguments.directory / file_name(day)
        if target.exists():
            present += 1
            continue
        if total >= limit:
            print(f"Stopped: {total} bytes reached the size limit", flush=True)
            return 2
        try:
            payload = _fetch(file_url(day))
            read_member(payload)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                absent += 1
                missing.append(day.isoformat())
            else:
                refusals += 1
                print(f"{day} refused with {error.code}", flush=True)
                if refusals >= _REFUSALS_BEFORE_STOP:
                    print("Stopped: the archive refused repeatedly", flush=True)
                    return 3
        except (OSError, ValueError) as error:
            errors += 1
            print(f"{day} failed: {type(error).__name__}", flush=True)
            if errors >= _ERRORS_BEFORE_STOP:
                print("Stopped: too many failures in a row", flush=True)
                return 4
        else:
            partial = target.with_suffix(".part")
            partial.write_bytes(payload)
            partial.replace(target)
            total += len(payload)
            fetched += 1
            refusals = errors = 0
        if (at + 1) % 100 == 0:
            print(f"{at + 1}/{len(days)} fetched {fetched} {total // 1048576} MB", flush=True)
        time.sleep(arguments.pause)
    print(f"Done: fetched {fetched}, already here {present}, not on the archive {absent}, "
          f"{total // 1048576} MB", flush=True)
    if missing:
        print("Not on the archive:", " ".join(missing), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
