"""Curated strategy provenance and paper-eligibility catalog."""

# Source descriptions are immutable migration data; keep each sentence intact.
# ruff: noqa: E501

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0014_strategy_catalog"
down_revision = "0013_equity_deployments"
branch_labels = None
depends_on = None

VERSION = "catalog-v1"
PAPER_EXIT = (
    "Exit at the protective stop or 2R target. After a 1R favorable move, "
    "trail the stop one initial risk unit behind the highest observed bid."
)
PAPER_STOP = (
    "Initial distance is 2× 14-day ATR, at least 0.5% of signal close; "
    "position size is capped by allocation and planned risk. Gaps can exceed it."
)


def row(
    key: str,
    name: str,
    order: int,
    *,
    implementation_key: str | None,
    trader: str,
    source_name: str,
    source_url: str | None,
    source_kind: str,
    instruments: list[str],
    conditions: str,
    entry: str,
    exit: str,
    stop: str,
    caveat: str,
) -> dict[str, object]:
    return {
        "key": key,
        "name": name,
        "status": "PAPER_READY" if implementation_key else "RESEARCH_ONLY",
        "implementation_key": implementation_key,
        "sort_order": order,
        "catalog_version": VERSION,
        "details": {
            "trader_or_source": trader,
            "source_name": source_name,
            "source_url": source_url,
            "source_kind": source_kind,
            "instrument_types": instruments,
            "market_conditions": conditions,
            "entry": entry,
            "exit": exit,
            "stop_loss": stop,
            "limitations": caveat,
        },
    }


ROWS = [
    row(
        "equity_trend_v1",
        "Trend continuation",
        1,
        implementation_key="trend",
        trader="Market Systems v1",
        source_name="Platform-defined daily equity rule",
        source_url=None,
        source_kind="original_platform_rule",
        instruments=["NSE equity", "NSE ETF"],
        conditions="Rising stock trend with usable daily history; avoid extreme volatility.",
        entry="Completed close above a rising 20-day average and 50-day average; wait for a later completed signal and verified quote.",
        exit=PAPER_EXIT,
        stop=PAPER_STOP,
        caveat="Platform rule, not attributed to a famous trader. One-year same-sample replay is not validation.",
    ),
    row(
        "equity_breakout_v1",
        "Volume breakout",
        2,
        implementation_key="breakout",
        trader="Market Systems v1",
        source_name="Platform-defined daily equity rule",
        source_url=None,
        source_kind="original_platform_rule",
        instruments=["NSE equity", "NSE ETF"],
        conditions="Breakout from a prior range with verified above-normal completed-session volume.",
        entry="Completed close above the prior 20-day high with volume over 1.5× the preceding 20-day mean.",
        exit=PAPER_EXIT,
        stop=PAPER_STOP,
        caveat="This is not the complete Turtle breakout system; no trader performance is inherited.",
    ),
    row(
        "equity_pullback_v1",
        "Trend pullback",
        3,
        implementation_key="pullback",
        trader="Market Systems v1",
        source_name="Platform-defined daily equity rule",
        source_url=None,
        source_kind="original_platform_rule",
        instruments=["NSE equity", "NSE ETF"],
        conditions="Rising stock trend with a short pullback and non-extreme volatility.",
        entry="Completed close recovers above the 5-day average while price remains above the 50-day average.",
        exit=PAPER_EXIT,
        stop=PAPER_STOP,
        caveat="Not Linda Raschke's Holy Grail: this rule has no ADX or 20-EMA trigger.",
    ),
    row(
        "equity_reversion_v1",
        "Mean reversion",
        4,
        implementation_key="reversion",
        trader="Market Systems v1",
        source_name="Platform-defined daily equity rule",
        source_url=None,
        source_kind="original_platform_rule",
        instruments=["NSE equity", "NSE ETF"],
        conditions="Mixed or sideways stock trend with subdued volatility; avoid strong breakdowns.",
        entry="After a close below the lower 20-day, 2σ band, the next completed close rises.",
        exit=PAPER_EXIT,
        stop=PAPER_STOP,
        caveat="The current rule does not prove a range will persist; gaps can defeat the planned stop.",
    ),
    row(
        "equity_momentum_v1",
        "Price momentum",
        5,
        implementation_key="momentum",
        trader="Market Systems v1",
        source_name="Platform-defined daily equity rule",
        source_url=None,
        source_kind="original_platform_rule",
        instruments=["NSE equity", "NSE ETF"],
        conditions="Rising stock trend with sufficient participation; avoid extreme volatility.",
        entry="Completed close has over 3% positive 10-day momentum and is a new 10-day closing high.",
        exit=PAPER_EXIT,
        stop=PAPER_STOP,
        caveat="Price-only momentum signal; company fundamentals and cross-sectional strength are not checked.",
    ),
    row(
        "turtle_system_1",
        "Original Turtle System 1",
        10,
        implementation_key=None,
        trader="Richard Dennis and William Eckhardt; published by original Turtles",
        source_name="The Original Turtle Trading Rules",
        source_url="https://tradingblox.com/originalturtles/originalturtlerules.htm",
        source_kind="published_primary_rules",
        instruments=["Futures"],
        conditions="Diversified liquid futures; trend following after a 20-day breakout. Requires portfolio correlation and volatility sizing.",
        entry="Break the prior 20-day high/low by a tick; original System 1 also skips a breakout after a winning preceding breakout, with a 55-day failsafe.",
        exit="Use the opposite 10-day breakout, subject to the protective stop and original portfolio rules.",
        stop="Original rule: about 2N (20-day true-range volatility) from entry; size units by N and account risk.",
        caveat="Historical fame is not proof of current profitability. Not implemented or backtested here; no futures paper deployment.",
    ),
    row(
        "turtle_system_2",
        "Original Turtle System 2",
        11,
        implementation_key=None,
        trader="Richard Dennis and William Eckhardt; published by original Turtles",
        source_name="The Original Turtle Trading Rules",
        source_url="https://tradingblox.com/originalturtles/originalturtlerules.htm",
        source_kind="published_primary_rules",
        instruments=["Futures"],
        conditions="Diversified liquid futures in sustained trends; requires volatility sizing and portfolio limits.",
        entry="Break the prior 55-day high/low by a tick; original System 2 takes all such breakouts.",
        exit="Use the opposite 20-day breakout, subject to the protective stop and original portfolio rules.",
        stop="Original rule: about 2N from entry; original adding and correlated-market limits must be reproduced.",
        caveat="The exact original system requires intraday breakout handling, unit adds and portfolio rules. Research-only here.",
    ),
    row(
        "raschke_holy_grail",
        "Holy Grail pullback",
        12,
        implementation_key=None,
        trader="Linda Bradford Raschke",
        source_name="Raschke interview and published Holy Grail buy rules",
        source_url="https://lindaraschke.net/wp-content/uploads/2026/01/august1997.pdf",
        source_kind="published_primary_interview",
        instruments=["Equity", "Futures"],
        conditions="Strong established trend; 14-period ADX above 30 and rising before a pullback to the 20-period EMA.",
        entry="After price touches the 20-period EMA, place a buy stop above the previous bar's high; reverse for shorts.",
        exit="Trail the protective stop as a profitable trend resumes; original execution includes trader discretion.",
        stop="Protective sell stop at the newly formed swing low for a long setup.",
        caveat="Published setup is partly discretionary and is not the platform's existing trend-pullback rule. Research-only.",
    ),
    row(
        "oneil_growth_breakout",
        "CAN SLIM growth breakout",
        13,
        implementation_key=None,
        trader="William J. O'Neil / Investor's Business Daily",
        source_name="IBD stock guide and CAN SLIM education",
        source_url="https://marketsurge-files.investors.com/2023/12/StockGuide-Q4-2023.pdf",
        source_kind="publisher_education",
        instruments=["Equity"],
        conditions="Constructive market trend, strong earnings/leadership and a valid stock base; volume confirms breakout.",
        entry="Buy a valid base breakout near its pivot with strong volume, after fundamental and market checks.",
        exit="Publisher guidance describes profit-taking and cutting losses; no single exit fits every base.",
        stop="IBD guidance limits an initial loss to roughly 7–8% from purchase price, often sooner if weakness appears.",
        caveat="Fundamentals, relative strength and base recognition are not available in this platform. Not implemented or ranked.",
    ),
    row(
        "williams_volatility_breakout",
        "Williams volatility breakout concept",
        14,
        implementation_key=None,
        trader="Larry Williams",
        source_name="Larry Williams' public innovation notes; World Cup historical result is separate",
        source_url="https://ireallytrade.com/innovation/",
        source_kind="trader_concept_only",
        instruments=["Futures", "Equity"],
        conditions="Range expansion from a prior price range; requires liquid markets and tested parameters.",
        entry="Public description discusses adding a fraction of recent range to an open or close. Exact entry parameter is not published here.",
        exit="Not publicly specified for this concept; must be defined and tested before use.",
        stop="Not publicly specified for this concept; a bounded protective rule must be designed before use.",
        caveat="Do not present this as Williams' 1987 competition system or paper-deploy it without complete sourced rules.",
    ),
]


def upgrade() -> None:
    table = op.create_table(
        "strategy_catalog",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("implementation_key", sa.String(32), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("catalog_version", sa.String(24), nullable=False),
        sa.Column("details", sa.JSON().with_variant(JSONB(), "postgresql"), nullable=False),
    )
    op.bulk_insert(table, ROWS)


def downgrade() -> None:
    op.drop_table("strategy_catalog")
