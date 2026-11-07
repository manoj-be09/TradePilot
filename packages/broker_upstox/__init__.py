"""Live-trading foundations (LIVE_TRADING.md steps L2-L4). There is no send path here.

Nothing in this package can place, change or cancel an order: it holds the order state
machine, hedge-first leg ordering, pre-trade controls, the live halt flag and read-only
positions/funds reads for reconciliation. Live trading stays locked by
`Settings.effective_live_trading_enabled`, which always returns False.
"""
