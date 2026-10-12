from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from packages.market_data.contracts import (
    DataQualityIssue,
    DataQualityReport,
    OptionChain,
)

_INDIA_TZ = ZoneInfo("Asia/Kolkata")
_ABNORMAL_SPREAD_RATIO = Decimal("0.30")


def assess_option_chain(chain: OptionChain) -> DataQualityReport:
    """Validate a provider snapshot and report issues without altering its quotes."""
    counts: dict[str, int] = {}
    issue_details: dict[str, tuple[str, str]] = {}

    def record(
        code: str,
        severity: Literal["warning", "critical"],
        message: str,
        count: int = 1,
    ) -> None:
        counts[code] = counts.get(code, 0) + count
        issue_details[code] = (severity, message)

    seen_instruments: set[str] = set()
    strike_sides: dict[Decimal, set[str]] = {}
    missing_field_count = 0
    crossed_market_count = 0
    abnormal_spread_count = 0

    if chain.expiry < datetime.now(_INDIA_TZ).date():
        record("EXPIRED_CHAIN", "critical", "The requested option-chain expiry has passed.")
    if chain.underlying_spot is None:
        record(
            "MISSING_UNDERLYING_SPOT",
            "warning",
            "The provider did not return an underlying spot price.",
        )
    unverified_contracts = sum(
        contract.quote.timestamp_basis != "exchange" for contract in chain.contracts
    )
    if chain.timestamp_basis != "exchange" or unverified_contracts:
        record(
            "SOURCE_TIMESTAMP_UNVERIFIED",
            "warning",
            "The response time is available, but per-contract exchange timestamps "
            "are not verified.",
            max(1, len(chain.contracts))
            if chain.timestamp_basis != "exchange" else unverified_contracts,
        )
    unverified_books = sum(
        contract.quote.book_timestamp_basis != "exchange"
        or contract.quote.book_as_of is None
        or contract.quote.book_as_of.tzinfo is None
        for contract in chain.contracts
    )
    if unverified_books:
        record(
            "BOOK_TIMESTAMP_UNVERIFIED", "warning",
            "Per-contract bid/ask book update times are not exchange-verified.",
            unverified_books,
        )
    book_ages = [
        datetime.now(timezone.utc) - contract.quote.book_as_of.astimezone(timezone.utc)
        for contract in chain.contracts
        if contract.quote.book_timestamp_basis == "exchange"
        and contract.quote.book_as_of is not None
        and contract.quote.book_as_of.tzinfo is not None
    ]
    stale_books = sum(age > timedelta(seconds=60) for age in book_ages)
    future_books = sum(age < timedelta(seconds=-10) for age in book_ages)
    if stale_books:
        record(
            "STALE_BOOK", "warning",
            "An exchange-timestamped bid/ask book update is over 60 seconds old.",
            stale_books,
        )
    if future_books:
        record(
            "FUTURE_BOOK_TIMESTAMP", "critical",
            "An exchange bid/ask book update is over 10 seconds in the future.",
            future_books,
        )
    if chain.timestamp_basis == "exchange" and chain.as_of.tzinfo is not None:
        snapshot_age = datetime.now(timezone.utc) - chain.as_of.astimezone(timezone.utc)
        if snapshot_age > timedelta(seconds=60):
            record(
                "STALE_SNAPSHOT",
                "warning",
                "The exchange-timestamped snapshot is over 60 seconds old.",
            )
        elif snapshot_age < timedelta(seconds=-10):
            record(
                "FUTURE_TIMESTAMP",
                "critical",
                "The snapshot timestamp is over 10 seconds in the future.",
            )
    elif chain.timestamp_basis == "exchange":
        record(
            "INVALID_SOURCE_TIMESTAMP", "critical", "An exchange timestamp must include a timezone."
        )

    for contract in chain.contracts:
        sides = strike_sides.setdefault(contract.strike, set())
        if contract.option_type in sides:
            record(
                "DUPLICATE_STRIKE_SIDE",
                "critical",
                "A strike has duplicate contracts for one option side.",
            )
        sides.add(contract.option_type)
        if contract.expiry != chain.expiry:
            record(
                "EXPIRY_MISMATCH",
                "critical",
                "A contract expiry does not match the requested chain expiry.",
            )
        if contract.option_type not in {"CE", "PE"}:
            record("INVALID_OPTION_SIDE", "critical", "A contract has an invalid option type.")
        if (
            not contract.instrument_token
            or contract.quote.instrument_token != contract.instrument_token
        ):
            record(
                "INVALID_INSTRUMENT_ID",
                "critical",
                "A contract has a missing or inconsistent instrument key.",
            )
        if contract.instrument_token in seen_instruments:
            record(
                "DUPLICATE_INSTRUMENT",
                "critical",
                "The same instrument key appeared more than once.",
            )
        seen_instruments.add(contract.instrument_token)

        fields = (
            contract.quote.bid,
            contract.quote.ask,
            contract.quote.volume,
            contract.open_interest,
            contract.previous_open_interest,
            contract.open_interest_change,
            contract.change,
            contract.bid_quantity,
            contract.ask_quantity,
            contract.implied_volatility,
        )
        missing_field_count += sum(value is None for value in fields)

        bid = contract.quote.bid
        ask = contract.quote.ask
        if bid is not None and ask is not None and bid > 0 and ask > 0:
            if bid > ask:
                crossed_market_count += 1
            else:
                midpoint = (bid + ask) / Decimal(2)
                if midpoint > 0 and (ask - bid) / midpoint > _ABNORMAL_SPREAD_RATIO:
                    abnormal_spread_count += 1

    for sides in strike_sides.values():
        if sides != {"CE", "PE"}:
            record("INCOMPLETE_STRIKE", "warning", "A strike is missing its call or put contract.")

    if missing_field_count:
        record(
            "MISSING_FIELDS",
            "warning",
            "Some quote, open-interest, or volatility fields are missing.",
            missing_field_count,
        )
    if crossed_market_count:
        record(
            "CROSSED_MARKET",
            "critical",
            "A contract has a bid above its ask.",
            crossed_market_count,
        )
    if abnormal_spread_count:
        record(
            "ABNORMAL_SPREAD",
            "warning",
            "A contract spread exceeds 30% of its midpoint.",
            abnormal_spread_count,
        )

    issues = [
        DataQualityIssue(code=code, severity=severity, count=counts[code], message=message)
        for code, (severity, message) in issue_details.items()
    ]
    status = (
        "FAIL"
        if any(issue.severity == "critical" for issue in issues)
        else "WARN"
        if issues
        else "PASS"
    )
    complete_strikes = sum(sides == {"CE", "PE"} for sides in strike_sides.values())
    return DataQualityReport(
        status=status,
        checked_at=datetime.now(timezone.utc),
        source_timestamp_verified=(
            chain.timestamp_basis == "exchange"
            and unverified_contracts == 0
            and unverified_books == 0
        ),
        contract_count=len(chain.contracts),
        strike_count=len(strike_sides),
        complete_strike_count=complete_strikes,
        missing_field_count=missing_field_count,
        crossed_market_count=crossed_market_count,
        abnormal_spread_count=abnormal_spread_count,
        issues=issues,
    )
