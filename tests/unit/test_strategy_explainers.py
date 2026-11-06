import re
from pathlib import Path

from packages.paper_trading.futures_strategies import list_futures_templates
from packages.paper_trading.strategies import STRATEGIES

EXPLAINERS = Path(__file__).parents[2] / "apps/web/app/strategyExplainers.ts"


def test_every_paper_strategy_has_a_plain_language_explainer() -> None:
    keys = set(re.findall(r"^  ([a-z_]+): \{$", EXPLAINERS.read_text(encoding="utf-8"), re.M))
    expected = {s.key for s in STRATEGIES} | {t.key for t in list_futures_templates()}
    assert expected - keys == set()
