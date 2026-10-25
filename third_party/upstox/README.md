# Upstox V3 protocol schema notice

The files `packages/market_data/upstox_v3.proto`,
`packages/market_data/upstox_v3_pb2.py`, and
`packages/market_data/upstox_v3_pb2.pyi` contain or are generated from the
Market Data Feed V3 Protocol Buffer schema. The local schema has the same
declarations as Upstox's [public Python SDK example](https://github.com/upstox/upstox-python/blob/master/examples/websocket/market_data/v3/MarketDataFeedV3.proto);
local differences are the source/retrieval header and omitted field comments.

The official SDK repository publishes that example under its
[MIT licence](https://github.com/upstox/upstox-python/blob/master/LICENSE.txt).
The required copyright and permission notice is reproduced in [LICENSE.txt](LICENSE.txt).
This code licence concerns distribution of the schema and generated bindings.
It does not grant market-data redistribution, paper-fill timing evidence, or
rights for other Upstox API responses.
