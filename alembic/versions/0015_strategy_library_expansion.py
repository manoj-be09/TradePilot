"""Sourced paper adaptations and research methods; no third-party returns imported."""

import sqlalchemy as sa

from alembic import op

revision = "0015_strategy_library_expansion"
down_revision = "0014_strategy_catalog"
branch_labels = None
depends_on = None

TURTLE = "https://tradingblox.com/originalturtles/originalturtlerules.htm"
R2 = "https://tradingmarkets.com/recent/the_improved_r2_strategy_84_correct_with_just_6_rules_-674361"
BANDS = "https://www.bollingerbands.com/bollinger-band-rules"
ROBUST = "https://therobusttrader.com/best-trading-strategies/"
PAPER_EXIT = "Platform exit: initial stop, 2R target, and +1R-activated trailing stop."
PAPER_STOP = "2 times 14-session SMA true range, at least 0.5% of signal close; risk-capped size."


def entry(key, name, order, trader, url, conditions, trigger, *, implementation=None,
          exit_rule="Requires a complete testable exit specification before activation.",
          stop="Requires explicit protective risk rules before activation.", notes=""):
    return {
        "key": key, "name": name, "sort_order": order,
        "implementation_key": implementation,
        "status": "PAPER_READY" if implementation else "RESEARCH_ONLY",
        "catalog_version": "catalog-v2",
        "details": {
            "trader_or_source": trader, "source_url": url,
            "source_name": "Source method / concept" if implementation else "Author or publisher",
            "source_kind": "platform_adaptation" if implementation else "published_research",
            "source_reviewed_at": "2026-10-01",
            "instrument_types": ["NSE equity", "NSE ETF"] if implementation else ["Equity", "ETF"],
            "market_conditions": conditions, "entry": trigger,
            "exit": PAPER_EXIT if implementation else exit_rule,
            "stop_loss": PAPER_STOP if implementation else stop,
            "adaptation_notes": notes,
            "limitations": (
                "Paper adaptation, not the author's complete system. No source performance "
                "is imported; statistics come only from this platform's replay. "
                if implementation else "Research only; no local performance claim or Apply action. "
            ) + notes,
        },
    }


ROWS = [
    entry("equity_channel20_v1", "20-day channel breakout · adapted", 6,
          "Dennis / Eckhardt Turtle channel concept; platform adaptation", TURTLE,
          "Range expansion with usable volume; avoid extreme volatility.",
          "Completed close exceeds the preceding 20-session high.", implementation="channel20",
          notes="NSE daily long-only. Omits winner-skip, intraday entry, unit additions and "
                "10-day channel exits. Uses platform ATR stops, targets and trailing instead."),
    entry("equity_channel55_v1", "55-day channel breakout · adapted", 7,
          "Dennis / Eckhardt Turtle channel concept; platform adaptation", TURTLE,
          "Sustained upward trends in liquid shares.",
          "Completed close exceeds the preceding 55-session high.", implementation="channel55",
          notes="Needs 56 completed candles. No short futures, portfolio pyramiding or "
                "20-day channel exit; this uses the platform's protective exit model."),
    entry("equity_connors_r2_v1", "Connors R2 pullback · adapted", 8,
          "Larry Connors / TradingMarkets; platform adaptation", R2,
          "Short pullback within a longer-term uptrend.",
          "Close above SMA200; RSI(2) starts below 65 and decreases on the next two closes.",
          implementation="connors_r2",
          notes="Needs 200 completed candles. Uses Wilder RSI and next-session NSE entry. "
                "Replaces the source's RSI-above-75 exit with platform ATR stop/target/trail."),
    entry("equity_band_reclaim_v1", "Bollinger band reclaim · adapted", 9,
          "John Bollinger indicator; platform-defined reclaim rule", BANDS,
          "Sideways conditions and non-extreme volatility.",
          "Previous close below its lower 20-session band; current close rises back inside.",
          implementation="band_reclaim",
          notes="Uses 20-close SMA and 2 population standard deviations. Reclaim entry and "
                "ATR exits are platform choices; a band touch alone is not a Bollinger buy rule."),
    entry("equity_sma_cross_v1", "20/50 SMA crossover · adapted", 10,
          "Moving-average family in The Robust Trader; platform rule", ROBUST,
          "Emerging uptrend; vulnerable to sideways whipsaws.",
          "SMA20 crosses above SMA50 and close is above SMA20.", implementation="sma_cross",
          notes="The directory describes a strategy family. The 20/50 periods, price filter "
                "and protective exits are specified by this platform, not copied performance."),
    entry("connors_improved_r2", "Connors Improved R2 · original reference", 20,
          "Larry Connors", R2, "Pullbacks above a long-term market average.",
          "Above SMA200, RSI(2) below 65 followed by two lower RSI readings.",
          exit_rule="The published rule exits on a close with RSI(2) above 75.",
          stop="The article does not prescribe the platform's ATR protective stop.",
          notes="Source enters on the signal close and studies S&P-linked products. The "
                "separate paper adaptation changes both entry timing and exits."),
    entry("bollinger_method_iv", "Bollinger Method IV · breakout", 21,
          "John Bollinger", "https://www.bollingerbands.com/tradenavigator-bbtk",
          "Low relative volatility followed by directional expansion.",
          "Low-volatility setup; inside-band close, outside-band close, then follow-through.",
          notes="Public note gives the entry concept but not a full executable exit/risk "
                "specification. Do not substitute a generic breakout and call it Method IV."),
    entry("minervini_sepa", "SEPA stock-selection framework", 22,
          "Mark Minervini / McGraw Hill",
          "https://www.mheducation.com/highered/mhp/product/"
          "trade-like-stock-market-wizard-how-achieve-super-performance-stocks-any-market.html",
          "Growth-stock trend, fundamentals and precise entry selection.",
          "Combine trend, company-quality checks and a defined entry under the SEPA framework.",
          notes="Publisher overview is not a complete algorithm. Fundamental history, setup "
                "recognition, precise exits and independent testing remain required."),
    entry("elder_triple_screen", "Elder Triple Screen framework", 23,
          "Alexander Elder / Wiley",
          "https://www.wiley-vch.de/en/areas-interest/finance-economics-law/"
          "trading-for-a-living-978-0-471-59224-2",
          "Aligned higher-timeframe trend and shorter-timeframe setup.",
          "Screen the broader trend before choosing a lower-timeframe entry.",
          notes="Multi-timeframe synchronization, indicator choices and full entry/exit "
                "specification require implementation. This is an author-linked research record."),
    entry("kaufman_adaptive_trend", "Kaufman adaptive trend research", 24,
          "Perry Kaufman", "https://kaufmansignals.com/",
          "Systematic trend analysis with portfolio diversification and risk controls.",
          "Research adaptive trend and portfolio-selection signals; no copied subscriber signal.",
          notes="The public overview does not disclose all portfolio and execution rules. "
                "A KAMA indicator alone would not reproduce Kaufman's portfolio programs."),
]


def table():
    return sa.table("strategy_catalog", sa.column("key", sa.String),
                    sa.column("name", sa.String), sa.column("sort_order", sa.Integer),
                    sa.column("implementation_key", sa.String), sa.column("status", sa.String),
                    sa.column("catalog_version", sa.String), sa.column("details", sa.JSON))


def upgrade() -> None:
    op.bulk_insert(table(), ROWS)


def downgrade() -> None:
    catalog = table()
    op.execute(catalog.delete().where(catalog.c.key.in_([row["key"] for row in ROWS])))
