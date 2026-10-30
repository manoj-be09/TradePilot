"""Authenticated NIFTY futures paper trading; simulated fills only, never broker orders."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, Response, status

from apps.api.auth import CurrentUser, SessionDep
from apps.api.paper_schemas import PaperDeploymentResponse, PaperExpirySettlementRequest
from apps.api.services.futures_deployments import (
    FuturesDeploymentCreateRequest,
    FuturesStrategyResponse,
    arm_futures_deployment,
    list_strategies,
)
from apps.api.services.futures_paper import (
    FuturesContractResponse,
    FuturesError,
    FuturesQuoteResponse,
    FuturesTradeCreateRequest,
    FuturesTradeResponse,
    close_trade,
    list_contracts,
    list_trades,
    open_trade,
    quote_contract,
    settle_expired_trade,
)
from apps.api.settings import get_settings
from packages.market_data.provider import get_market_data_provider

router = APIRouter(prefix="/paper/futures", tags=["paper futures"])


def _http(error: FuturesError) -> HTTPException:
    return HTTPException(status_code=error.status_code, detail=error.detail)


@router.get("/strategies", response_model=list[FuturesStrategyResponse])
def futures_strategies(user: CurrentUser, response: Response) -> list[FuturesStrategyResponse]:
    del user
    response.headers["Cache-Control"] = "no-store"
    return list_strategies()


@router.post(
    "/deployments", response_model=PaperDeploymentResponse, status_code=status.HTTP_201_CREATED
)
async def arm_futures_strategy(
    body: FuturesDeploymentCreateRequest,
    user: CurrentUser,
    session: SessionDep,
    idempotency_key: Annotated[
        str, Header(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    ],
) -> PaperDeploymentResponse:
    try:
        return await arm_futures_deployment(
            body, user.id, session, idempotency_key, get_market_data_provider(), get_settings()
        )
    except FuturesError as error:
        raise _http(error) from error


@router.get("/contracts", response_model=list[FuturesContractResponse])
async def futures_contracts(user: CurrentUser, response: Response) -> list[FuturesContractResponse]:
    del user
    response.headers["Cache-Control"] = "no-store"
    try:
        return await list_contracts(get_market_data_provider())
    except FuturesError as error:
        raise _http(error) from error


@router.get("/quote", response_model=FuturesQuoteResponse)
async def futures_quote(
    user: CurrentUser,
    response: Response,
    instrument_key: Annotated[str, Query(min_length=3, max_length=160)],
) -> FuturesQuoteResponse:
    del user
    response.headers["Cache-Control"] = "no-store"
    try:
        return await quote_contract(get_market_data_provider(), instrument_key, get_settings())
    except FuturesError as error:
        raise _http(error) from error


@router.get("/trades", response_model=list[FuturesTradeResponse])
def futures_trades(
    user: CurrentUser, session: SessionDep, response: Response
) -> list[FuturesTradeResponse]:
    response.headers["Cache-Control"] = "no-store"
    return list_trades(session, user.id)


@router.post(
    "/trades", response_model=FuturesTradeResponse, status_code=status.HTTP_201_CREATED
)
async def open_futures_trade(
    body: FuturesTradeCreateRequest,
    user: CurrentUser,
    session: SessionDep,
    idempotency_key: Annotated[
        str, Header(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    ],
) -> FuturesTradeResponse:
    try:
        return await open_trade(
            body, user.id, session, idempotency_key, get_market_data_provider(), get_settings()
        )
    except FuturesError as error:
        raise _http(error) from error


@router.post("/trades/{trade_id}/close", response_model=FuturesTradeResponse)
async def close_futures_trade(
    trade_id: UUID, user: CurrentUser, session: SessionDep
) -> FuturesTradeResponse:
    try:
        return await close_trade(
            trade_id, user.id, session, get_market_data_provider(), get_settings()
        )
    except FuturesError as error:
        raise _http(error) from error


@router.post("/trades/{trade_id}/expiry-settlement", response_model=FuturesTradeResponse)
async def settle_futures_trade(
    trade_id: UUID, body: PaperExpirySettlementRequest, user: CurrentUser, session: SessionDep,
) -> FuturesTradeResponse:
    # Import here: main includes this router, and the NSE recheck lives with the option flow.
    from apps.api.main import _check_nse_settlement_reference

    await _check_nse_settlement_reference(body)
    try:
        return settle_expired_trade(trade_id, body, user.id, session)
    except FuturesError as error:
        raise _http(error) from error
