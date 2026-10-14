import pytest

from packages.market_data.index_catalog import parse_option_indexes


def test_catalog_discovers_all_listed_cash_index_options_and_excludes_other_products():
    def row(symbol, key, segment="NSE_FO", kind="CE", underlying_type="INDEX"):
        return dict(underlying_symbol=symbol, underlying_key=key, segment=segment,
                    instrument_type=kind, underlying_type=underlying_type)
    result = parse_option_indexes([
        row("NIFTY", "NSE_INDEX|Nifty 50"),
        row("NIFTY", "NSE_INDEX|Nifty 50", kind="PE"),
        row("NEWINDEX", "NSE_INDEX|New index"),
        row("SENSEX", "BSE_INDEX|SENSEX", "BSE_FO"),
        row("RELIANCE", "NSE_EQ|equity", underlying_type="EQUITY"),
        row("FUTONLY", "NSE_INDEX|Future index", kind="FUT"),
        row("MCXBULLDEX", "MCX_FO|568", "MCX_FO"),
    ])
    assert [item.symbol for item in result] == ["NIFTY", "NEWINDEX", "SENSEX"]
    assert result[-1].derivatives_exchange == "BFO"


def test_catalog_refuses_conflicting_or_cross_venue_identity():
    rows = [dict(underlying_type="INDEX", segment="NSE_FO", instrument_type="CE",
                 underlying_symbol="TEST", underlying_key="NSE_INDEX|Test")]
    with pytest.raises(ValueError, match="Conflicting"):
        parse_option_indexes(rows + [{**rows[0], "underlying_key": "NSE_INDEX|Other"}])
    with pytest.raises(ValueError, match="Invalid"):
        parse_option_indexes([{**rows[0], "underlying_key": "BSE_INDEX|Test"}])
