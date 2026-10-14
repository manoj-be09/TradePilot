from test_paper_trading import FakeUpstoxProvider, _token

from packages.market_data.index_catalog import OptionIndex


def test_index_catalog_and_contract_selection_are_authenticated_and_isolated(client, monkeypatch):
    provider = FakeUpstoxProvider()
    calls = []

    async def catalog():
        return [OptionIndex(symbol="SENSEX", name="SENSEX", instrument_key="BSE_INDEX|SENSEX",
                            exchange="BSE", derivatives_exchange="BFO")]

    async def contracts(underlying="NIFTY", expiry=None):
        calls.append((underlying, expiry))
        return []

    provider.get_option_contracts = contracts
    monkeypatch.setattr("apps.api.main.get_market_data_provider", lambda: provider)
    monkeypatch.setattr("apps.api.main.option_indexes", catalog)
    assert client.get("/market-data/option-indexes").status_code == 401
    assert client.get("/market-data/option-contracts?underlying=SENSEX").status_code == 401
    headers = _token(client)
    assert client.get("/market-data/option-indexes", headers=headers).json()[0]["symbol"] == "SENSEX"
    assert client.get("/market-data/option-contracts?underlying=SENSEX", headers=headers).status_code == 200
    assert client.get("/market-data/option-contracts", headers=headers).status_code == 200
    assert calls == [("SENSEX", None), ("NIFTY", None)]
