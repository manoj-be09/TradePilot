import asyncio
import json
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from packages.market_data import upstox_v3_pb2 as feed_proto
from packages.market_data.contracts import MarketDataPlanRequired, MarketDataProviderError
from packages.market_data.upstox import UpstoxMarketDataProvider, _stream_quote
from packages.paper_trading.equity import quote_problem


def test_news_snapshot_keeps_only_pre_receipt_upstox_headlines():
    published = datetime.now(timezone.utc) - timedelta(minutes=2)
    future = datetime.now(timezone.utc) + timedelta(days=1)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/news"
        assert request.url.params["category"] == "instrument_keys"
        assert request.url.params["instrument_keys"] == "NSE_INDEX|Nifty 50"
        return httpx.Response(200, json={"status": "success", "data": {
            "NSE_INDEX|Nifty 50": [
                {"heading": "Market update", "article_link":
                 "https://upstox.com/news/market-news/update/",
                 "published_time": int(published.timestamp() * 1000)},
                {"heading": "Future", "article_link":
                 "https://upstox.com/news/market-news/future/",
                 "published_time": int(future.timestamp() * 1000)},
                {"heading": "Other publisher", "article_link":
                 "https://example.com/story", "published_time":
                 int(published.timestamp() * 1000)},
                {"heading": "Broken URL", "article_link": "https://[broken",
                 "published_time": int(published.timestamp() * 1000)},
            ],
        }})

    provider = UpstoxMarketDataProvider("test-token", transport=httpx.MockTransport(handler))
    observed = asyncio.run(provider.get_recent_news("NSE_INDEX|Nifty 50"))
    assert observed["status"] == "OBSERVED_UNVERIFIED_COVERAGE"
    assert observed["coverage_reviewed"] is False
    assert [item["heading"] for item in observed["items"]] == ["Market update"]
    assert datetime.fromisoformat(observed["items"][0]["published_at"]) < datetime.fromisoformat(
        observed["received_at"]
    )


@pytest.mark.parametrize(
    ("interval", "unit", "size"),
    [("15m", "minutes", "15"), ("1h", "hours", "1"), ("1d", "days", "1")],
)
def test_historical_chart_intervals_map_to_provider_paths(interval, unit, size):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == (
            f"/v3/historical-candle/NSE_EQ|INE002A01018/{unit}/{size}/"
            "2026-09-28/2026-09-25"
        )
        return httpx.Response(200, json={"status": "success", "data": {"candles": [
            ["2026-09-25T15:15:00+05:30", 100, 102, 99, 101, 1234],
        ]}})

    provider = UpstoxMarketDataProvider("test-token", transport=httpx.MockTransport(handler))
    bars = asyncio.run(provider.get_historical(
        "NSE_EQ|INE002A01018", datetime.fromisoformat("2026-09-25T00:00:00+05:30"),
        datetime.fromisoformat("2026-09-28T00:00:00+05:30"), interval,
    ))
    assert len(bars) == 1
    assert bars[0].volume == 1234
    assert str(bars[0].close) == "101"


def test_intraday_chart_candles_use_current_session_endpoint():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == (
            "/v3/historical-candle/intraday/NSE_FO|NIFTY_OPTION/minutes/15"
        )
        return httpx.Response(200, json={"status": "success", "data": {"candles": [
            ["2026-09-28T13:15:00+05:30", 100, 104, 99, 103, 500, 2500],
        ]}})

    provider = UpstoxMarketDataProvider("test-token", transport=httpx.MockTransport(handler))
    bars = asyncio.run(provider.get_intraday("NSE_FO|NIFTY_OPTION", "15m"))
    assert len(bars) == 1
    assert str(bars[0].close) == "103"
    assert bars[0].open_interest == 2500


def test_full_quote_distinguishes_response_time_from_last_trade_time():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v3/market-quote/quotes"
        return httpx.Response(200, json={"status": "success", "data": {
            "NSE_EQ:RELIANCE": {
                "timestamp": "2026-09-28T15:31:00+05:30",
                "last_trade_time": "1790585400000",
                "last_price": 1234.5,
                "depth": {
                    "buy": [{"price": 1234, "quantity": 20}],
                    "sell": [{"price": 1235, "quantity": 25}],
                },
            }
        }})

    provider = UpstoxMarketDataProvider(
        "test-token", transport=httpx.MockTransport(handler)
    )
    quote = asyncio.run(provider.get_quote("NSE_EQ|RELIANCE"))

    assert quote.timestamp_basis == "provider_response"
    assert quote.timestamp.isoformat() == "2026-09-28T15:31:00+05:30"
    assert quote.received_at is not None
    assert quote.received_at.tzinfo is not None
    assert quote.received_at != quote.timestamp
    assert quote.last_trade_time is not None
    assert int(quote.last_trade_time.timestamp() * 1000) == 1790585400000
    assert quote.last_trade_time != quote.timestamp
    assert quote.bid_quantity == 20
    assert quote.ask_quantity == 25


def test_full_quote_labels_api_receipt_fallback_and_unknown_last_trade():
    provider = UpstoxMarketDataProvider(
        "test-token", transport=httpx.MockTransport(lambda _: httpx.Response(
            200, json={"status": "success", "data": {"NSE_EQ:RELIANCE": {
                "last_price": 1234.5, "last_trade_time": "invalid",
            }}}
        ))
    )
    quote = asyncio.run(provider.get_quote("NSE_EQ|RELIANCE"))

    assert quote.timestamp_basis == "api_response_received"
    assert quote.received_at == quote.timestamp
    assert quote.last_trade_time is None


def test_stream_quote_retains_receipt_separately_from_provider_tick():
    received_at = datetime(2026, 10, 23, 10, 0, 1, tzinfo=timezone.utc)
    tick_at = received_at - timedelta(seconds=1)
    frame = feed_proto.FeedResponse()
    market = frame.feeds["NSE_EQ|TEST"].fullFeed.marketFF
    market.ltpc.ltp = 100
    market.ltpc.ltt = int((tick_at - timedelta(seconds=1)).timestamp() * 1000)
    quote = _stream_quote("NSE_EQ|TEST", frame.feeds["NSE_EQ|TEST"], tick_at, received_at)
    assert quote is not None
    assert quote.timestamp == tick_at
    assert quote.timestamp_basis == "provider_tick"
    assert quote.received_at == received_at
    assert quote.book_as_of is None
    assert quote.book_timestamp_basis == "unknown"


def test_stream_without_provider_tick_does_not_call_receipt_an_api_response():
    received_at = datetime(2026, 10, 23, 10, 0, 1, tzinfo=timezone.utc)
    frame = feed_proto.FeedResponse()
    market = frame.feeds["NSE_EQ|TEST"].fullFeed.marketFF
    market.ltpc.ltp = 100
    quote = _stream_quote("NSE_EQ|TEST", frame.feeds["NSE_EQ|TEST"], None, received_at)
    assert quote is not None
    assert quote.timestamp == quote.received_at == received_at
    assert quote.timestamp_basis == "unknown"
    assert quote.book_as_of is None
    assert quote.book_timestamp_basis == "unknown"


def test_v3_full_feed_decodes_initial_and_live_quotes_without_exchange_book_claim(monkeypatch):
    key = "NSE_EQ|INE002A01018"
    frames = [feed_proto.FeedResponse(type=feed_proto.market_info).SerializeToString()]
    for kind, price in ((feed_proto.initial_feed, 100), (feed_proto.live_feed, 101)):
        frame = feed_proto.FeedResponse(type=kind, currentTs=1790586000000)
        market = frame.feeds[key].fullFeed.marketFF
        market.ltpc.ltp = price
        market.ltpc.ltt = 1790585999000
        market.vtt = 500
        depth = market.marketLevel.bidAskQuote.add()
        depth.bidP, depth.askP = price - 0.1, price + 0.1
        depth.bidQ, depth.askQ = 20, 25
        frames.append(frame.SerializeToString())

    class FakeSocket:
        sent: bytes | None = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def send(self, payload):
            self.sent = payload

        async def __aiter__(self):
            for frame in frames:
                yield frame

    socket = FakeSocket()
    monkeypatch.setattr("packages.market_data.upstox.connect", lambda *_a, **_k: socket)
    provider = UpstoxMarketDataProvider(
        "private-test-token", transport=httpx.MockTransport(lambda request: httpx.Response(
            200, json={"status": "success", "data": {
                "authorizedRedirectUri": "wss://example.invalid/feed?token=private-test-token"
            }},
        ))
    )

    captured_frames = []

    def record_frame(payload, received_at):
        captured_frames.append((payload, received_at))

    async def collect():
        return [
            item async for item in provider.subscribe(
                [key, key], raw_frame_recorder=record_frame,
            )
        ]

    quotes = asyncio.run(collect())
    assert [payload for payload, _ in captured_frames] == frames[1:]
    assert all(received_at.tzinfo is not None for _, received_at in captured_frames)
    assert len(quotes) == 2
    assert [quote.last_price for quote in quotes] == [100, 101]
    assert quotes[0].bid_quantity == 20
    assert quotes[0].ask_quantity == 25
    assert quotes[0].timestamp_basis == "provider_tick"
    assert quotes[0].received_at is not None
    assert quotes[0].received_at.tzinfo is not None
    assert quotes[0].book_as_of is None
    assert quotes[0].book_timestamp_basis == "unknown"
    assert quote_problem(quotes[0], key, quotes[0].timestamp) == (
        "Waiting for an exchange-timestamped bid/ask book"
    )
    assert quote_problem(quotes[0], key, quotes[0].timestamp, indicative=True) == (
        "Indicative paper fill needs a provider response timestamp"
    )
    assert isinstance(socket.sent, bytes)
    request = json.loads(socket.sent)
    assert request["data"] == {"mode": "full", "instrumentKeys": [key]}


def test_v3_feed_rejects_bad_frame_without_revealing_authorized_url(monkeypatch):
    secret = "not-for-logs"

    class BadSocket:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def send(self, _payload):
            return None

        async def __aiter__(self):
            yield b"\xff"

    monkeypatch.setattr("packages.market_data.upstox.connect", lambda *_a, **_k: BadSocket())
    provider = UpstoxMarketDataProvider(secret, transport=httpx.MockTransport(
        lambda _request: httpx.Response(200, json={"status": "success", "data": {
            "authorized_redirect_uri": f"wss://example.invalid/feed?token={secret}"
        }})
    ))

    async def collect():
        return [item async for item in provider.subscribe(["NSE_EQ|INE002A01018"])]

    with pytest.raises(MarketDataProviderError, match="invalid market frame") as raised:
        asyncio.run(collect())
    assert secret not in str(raised.value)


def test_instrument_search_normalizes_equity_discovery_without_exposing_token():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/instruments/search"
        assert request.url.params["query"] == "RELIANCE"
        assert request.url.params["segments"] == "EQ"
        assert request.url.params["exchanges"] == "NSE"
        assert request.url.params["records"] == "20"
        assert request.headers["Authorization"] == "Bearer test-token"
        return httpx.Response(200, json={"status": "success", "data": [{
            "instrument_key": "NSE_EQ|INE002A01018", "trading_symbol": "RELIANCE",
            "name": "RELIANCE INDUSTRIES LTD", "exchange": "NSE", "segment": "NSE_EQ",
            "instrument_type": "EQ", "lot_size": 1, "tick_size": 0.05,
            "unneeded_provider_field": "excluded",
        }]})

    provider = UpstoxMarketDataProvider("test-token", transport=httpx.MockTransport(handler))
    result = asyncio.run(provider.search_instruments("RELIANCE"))

    assert len(result) == 1
    assert result[0].instrument_key == "NSE_EQ|INE002A01018"
    assert result[0].instrument_type == "EQ"
    assert "unneeded_provider_field" not in result[0].model_dump()
    assert "test-token" not in str(result[0].model_dump())


def test_search_permission_error_is_not_mislabeled_as_expired_history_entitlement():
    provider = UpstoxMarketDataProvider(
        "test-token", transport=httpx.MockTransport(
            lambda _: httpx.Response(403, json={"error_code": "UDAPI1149"})
        )
    )
    with pytest.raises(MarketDataProviderError) as raised:
        asyncio.run(provider.search_instruments("RELIANCE"))
    assert type(raised.value) is MarketDataProviderError
    assert "Expired-contract" not in str(raised.value)


def test_option_chain_maps_read_only_upstox_response_and_authenticates_request():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/option/chain"
        assert request.url.params["instrument_key"] == "NSE_INDEX|Nifty 50"
        assert request.url.params["expiry_date"] == "2026-10-01"
        assert request.headers["Authorization"] == "Bearer test-token"
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": [
                    {
                        "expiry": "2026-10-01",
                        "strike_price": 25000,
                        "underlying_spot_price": 25123.4,
                        "call_options": {
                            "instrument_key": "NSE_FO|call-key",
                            "market_data": {
                                "ltp": 150.25,
                                "bid_price": 149.5,
                                "ask_price": 151.0,
                                "bid_qty": 45,
                                "ask_qty": 67,
                                "close_price": 160.0,
                                "volume": 234,
                                "oi": 12345,
                                "prev_oi": 12000,
                            },
                            "option_greeks": {
                                "iv": 12.5,
                                "delta": 0.52,
                                "gamma": 0.01,
                                "theta": -4.2,
                                "vega": 8.1,
                                "pop": 0.55,
                            },
                        },
                        "put_options": {
                            "instrument_key": "NSE_FO|put-key",
                            "market_data": {"ltp": 90, "volume": 321, "oi": 54321},
                            "option_greeks": {"iv": 13.1, "delta": -0.48},
                        },
                    }
                ],
            },
        )

    provider = UpstoxMarketDataProvider("test-token", transport=httpx.MockTransport(handler))
    result = asyncio.run(provider.get_option_chain("NIFTY", date(2026, 10, 1)))

    assert result.underlying == "NIFTY"
    assert str(result.underlying_spot) == "25123.4"
    assert len(result.contracts) == 2
    call = result.contracts[0]
    assert call.instrument_token == "NSE_FO|call-key"
    assert call.quote.timestamp_basis == "api_response_received"
    assert call.quote.received_at == result.as_of
    assert call.option_type == "CE"
    assert str(call.quote.last_price) == "150.25"
    assert call.open_interest == 12345
    assert call.open_interest_change == 345
    assert str(call.change) == "-9.75"
    assert call.bid_quantity == 45
    assert call.ask_quantity == 67
    assert str(call.greeks["delta"]) == "0.52"
    assert result.as_of.tzinfo is not None


def test_option_chain_rejects_explicitly_wrong_underlying_key():
    provider = UpstoxMarketDataProvider(
        "test-token", transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"status": "success", "data": [{
                "underlying_key": "NSE_INDEX|Nifty Bank", "expiry": "2026-10-01",
                "strike_price": 25000,
                "call_options": {"instrument_key": "NSE_FO|call-key", "market_data": {"ltp": 100}},
            }]})
        ),
    )
    with pytest.raises(MarketDataProviderError, match="selected underlying"):
        asyncio.run(provider.get_option_chain("NIFTY", date(2026, 10, 1)))


def test_invalid_token_error_does_not_disclose_token():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer do-not-disclose-this"
        return httpx.Response(401, json={"errors": ["private upstream detail"]})

    provider = UpstoxMarketDataProvider(
        "do-not-disclose-this", transport=httpx.MockTransport(handler)
    )

    with pytest.raises(MarketDataProviderError) as raised:
        asyncio.run(provider.get_option_chain("NIFTY", date(2026, 10, 1)))

    assert "do-not-disclose-this" not in str(raised.value)
    assert "private upstream detail" not in str(raised.value)


def test_expired_option_expiries_are_parsed_and_sorted_newest_first():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/expired-instruments/expiries"
        assert request.url.params["instrument_key"] == "NSE_INDEX|Nifty 50"
        return httpx.Response(
            200,
            json={"status": "success", "data": ["2025-01-02", "2024-12-26", "2025-01-02"]},
        )

    provider = UpstoxMarketDataProvider("test-token", transport=httpx.MockTransport(handler))
    result = asyncio.run(provider.get_expired_option_expiries())

    assert result == [date(2025, 1, 2), date(2024, 12, 26)]


def test_expired_option_contracts_use_provider_metadata():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/expired-instruments/option/contract"
        assert request.url.params["expiry_date"] == "2025-04-17"
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": [
                    {
                        "instrument_key": "NSE_FO|47982|17-04-2025",
                        "trading_symbol": "NIFTY 20400 CE 17 APR 25",
                        "expiry": "2025-04-17",
                        "strike_price": 20400,
                        "instrument_type": "CE",
                        "lot_size": 75,
                        "tick_size": 0.05,
                    }
                ],
            },
        )

    provider = UpstoxMarketDataProvider("test-token", transport=httpx.MockTransport(handler))
    result = asyncio.run(provider.get_expired_option_contracts("NIFTY", date(2025, 4, 17)))

    assert len(result) == 1
    assert result[0].instrument_key == "NSE_FO|47982|17-04-2025"
    assert result[0].expiry == date(2025, 4, 17)
    assert result[0].option_type == "CE"
    assert result[0].lot_size == 75


def test_expired_candles_sort_time_and_preserve_option_open_interest():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.raw_path.decode().endswith(
            "/NSE_FO%7C47982%7C17-04-2025/5minute/2025-04-17/2025-04-16"
        )
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "candles": [
                        ["2025-04-17T09:20:00+05:30", 105, 108, 103, 106, 300, 1200],
                        ["2025-04-17T09:15:00+05:30", 100, 107, 99, 105, 250, 1100],
                    ]
                },
            },
        )

    provider = UpstoxMarketDataProvider("test-token", transport=httpx.MockTransport(handler))
    bars = asyncio.run(
        provider.get_expired_historical(
            "NSE_FO|47982|17-04-2025", date(2025, 4, 16), date(2025, 4, 17), "5m"
        )
    )

    assert [bar.timestamp.minute for bar in bars] == [15, 20]
    assert [bar.open_interest for bar in bars] == [1100, 1200]


def test_expired_option_endpoints_disclose_plus_requirement_without_upstream_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={"errors": [{"errorCode": "UDAPI1149", "message": "private provider detail"}]},
        )

    provider = UpstoxMarketDataProvider("test-token", transport=httpx.MockTransport(handler))
    with pytest.raises(MarketDataPlanRequired, match="Upstox Plus") as raised:
        asyncio.run(provider.get_expired_option_expiries())

    assert "private provider detail" not in str(raised.value)


def test_connection_status_uses_read_only_request_and_never_returns_token():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/v2/option/contract"
        assert request.headers["Authorization"] == "Bearer private-test-token"
        return httpx.Response(200, json={"status": "success", "data": []})

    provider = UpstoxMarketDataProvider(
        "private-test-token", transport=httpx.MockTransport(handler)
    )
    health = asyncio.run(provider.health_check())

    assert health.configured is True
    assert health.healthy is True
    assert "private-test-token" not in health.model_dump_json()


def test_connection_status_sanitizes_upstream_auth_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "private-test-token"})

    provider = UpstoxMarketDataProvider(
        "private-test-token", transport=httpx.MockTransport(handler)
    )
    health = asyncio.run(provider.health_check())

    assert health.configured is True
    assert health.healthy is False
    assert "private-test-token" not in health.model_dump_json()
    assert "rejected the token" in health.detail


def test_nfo_session_uses_date_specific_read_only_market_timings():
    from zoneinfo import ZoneInfo

    zone = ZoneInfo("Asia/Kolkata")
    start = datetime(2026, 9, 28, 9, 15, tzinfo=zone)
    end = datetime(2026, 9, 28, 15, 40, tzinfo=zone)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/v2/market/timings/2026-09-28"
        assert request.headers["Authorization"] == "Bearer private-test-token"
        return httpx.Response(
            200,
            json={"status": "success", "data": [
                {"exchange": "NFO", "start_time": int(start.timestamp() * 1000),
                 "end_time": int(end.timestamp() * 1000)}
            ]},
        )

    provider = UpstoxMarketDataProvider(
        "private-test-token", transport=httpx.MockTransport(handler)
    )
    session = asyncio.run(provider.get_nfo_session(date(2026, 9, 28)))
    assert session is not None
    assert session.exchange == "NFO"
    assert session.start == start
    assert session.end == end


def test_nfo_session_fails_closed_when_missing_or_malformed():
    responses = [
        {"status": "success", "data": [{"exchange": "NSE", "start_time": 1, "end_time": 2}]},
        {"status": "success", "data": [{"exchange": "NFO", "start_time": True, "end_time": 2}]},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=responses.pop(0))

    provider = UpstoxMarketDataProvider(
        "private-test-token", transport=httpx.MockTransport(handler)
    )
    assert asyncio.run(provider.get_nfo_session(date(2026, 9, 28))) is None
    with pytest.raises(MarketDataProviderError, match="market timings are invalid"):
        asyncio.run(provider.get_nfo_session(date(2026, 9, 28)))


def _margin_leg(key: str, side: str = "SELL"):
    from packages.market_data.contracts import BrokerMarginLeg

    return BrokerMarginLeg(instrument_key=key, quantity=65, transaction_type=side)


def test_order_margin_posts_an_nrml_basket_and_parses_the_hedged_total():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"], seen["path"] = request.method, request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"status": "success", "data": {
            "margins": [
                {"span_margin": 120000.5, "exposure_margin": 30000, "equity_margin": 0,
                 "net_buy_premium": 0, "additional_margin": 0, "total_margin": 150000.5,
                 "tender_margin": 0},
                {"span_margin": 0, "exposure_margin": 0, "equity_margin": 0,
                 "net_buy_premium": 5000, "additional_margin": 0, "total_margin": 5000,
                 "tender_margin": 0},
            ],
            "required_margin": 155000.5, "final_margin": 34000,
        }})

    provider = UpstoxMarketDataProvider("test-token", transport=httpx.MockTransport(handler))
    margin = asyncio.run(provider.get_order_margin(
        [_margin_leg("NSE_FO|1"), _margin_leg("NSE_FO|2", "BUY")]
    ))
    assert seen["method"] == "POST" and seen["path"] == "/v2/charges/margin"
    assert seen["body"] == {"instruments": [
        {"instrument_key": "NSE_FO|1", "quantity": 65, "transaction_type": "SELL",
         "product": "D"},
        {"instrument_key": "NSE_FO|2", "quantity": 65, "transaction_type": "BUY",
         "product": "D"},
    ]}
    assert str(margin.final_margin) == "34000" and str(margin.margins[1].net_buy_premium) == "5000"


def test_order_margin_rejects_bad_baskets_and_mismatched_responses():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "success", "data": {
            "margins": [], "required_margin": 0, "final_margin": 0,
        }})

    provider = UpstoxMarketDataProvider("test-token", transport=httpx.MockTransport(handler))
    with pytest.raises(MarketDataProviderError, match="1 to 20"):
        asyncio.run(provider.get_order_margin([]))
    with pytest.raises(MarketDataProviderError, match="repeat"):
        asyncio.run(provider.get_order_margin([_margin_leg("NSE_FO|1"), _margin_leg("NSE_FO|1")]))
    with pytest.raises(MarketDataProviderError, match="does not match"):
        asyncio.run(provider.get_order_margin([_margin_leg("NSE_FO|1")]))


def test_only_listed_calculation_endpoints_may_be_posted():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request may be sent")

    provider = UpstoxMarketDataProvider("test-token", transport=httpx.MockTransport(handler))
    with pytest.raises(MarketDataProviderError, match="not a permitted"):
        asyncio.run(provider._send("POST", "/v2/order/place", body={}))
