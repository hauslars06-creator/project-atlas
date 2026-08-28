from decimal import Decimal, ROUND_DOWN

from fastapi import FastAPI
import asyncio
from app.api.webhook import router as webhook_router
from app.admin.routes import router as admin_router
from app.exchanges.bitunix import BitunixClient
from app.api.v1.router import router as api_v1_router
from app.trade_sync import trade_sync_loop
from app.webhook_worker import webhook_worker_loop
from app.security_monitor import security_monitor_loop
from app.sl_analysis import sl_analysis_loop
from app.tp_analysis import tp_analysis_loop
from app.database.trade_repository import create_open_trade
from app.database.database import init_database
from app.database.webhook_queue_repository import recover_processing_items

app = FastAPI(
    title="Project Atlas",
    version="0.2.0",
)


app.include_router(
    webhook_router,
    prefix="/webhook",
    tags=["TradingView"],
)


app.include_router(
    admin_router,
    tags=["Admin"],
)
app.include_router(api_v1_router)

trade_sync_task: asyncio.Task | None = None
webhook_worker_task: asyncio.Task | None = None
security_monitor_task: asyncio.Task | None = None
sl_analysis_task: asyncio.Task | None = None


@app.on_event("startup")
async def startup_event():
    global trade_sync_task, webhook_worker_task, security_monitor_task
    global sl_analysis_task

    init_database()

    recovered = recover_processing_items()
    if recovered:
        print(f"Webhook Queue Recovery: {recovered} Job(s)")

    trade_sync_task = asyncio.create_task(
        trade_sync_loop(),
        name="project-atlas-trade-sync",
    )

    webhook_worker_task = asyncio.create_task(
        webhook_worker_loop(),
        name="project-atlas-webhook-worker",
    )

    security_monitor_task = asyncio.create_task(
        security_monitor_loop(),
        name="project-atlas-security-monitor",
    )

    sl_analysis_task = asyncio.create_task(
        sl_analysis_loop(),
        name="project-atlas-sl-analysis",
    )

    tp_analysis_task = asyncio.create_task(
        tp_analysis_loop(),
        name="project-atlas-tp-analysis",
    )


@app.on_event("shutdown")
async def shutdown_event():
    global trade_sync_task, webhook_worker_task, security_monitor_task
    global sl_analysis_task

    tasks = [
        trade_sync_task,
        webhook_worker_task,
        security_monitor_task,
        sl_analysis_task,
    ]

    for task in tasks:
        if task is not None:
            task.cancel()

    for task in tasks:
        if task is not None:
            try:
                await task
            except asyncio.CancelledError:
                pass

    from app.trade_sync import shared_bitunix_client

    if shared_bitunix_client is not None:
        await shared_bitunix_client.aclose()

    trade_sync_task = None
    webhook_worker_task = None
    security_monitor_task = None
    sl_analysis_task = None


@app.get("/")
async def home():
    return {
        "status": "Project Atlas läuft",
        "version": "0.2.0",
        "live_trading": False,
    }


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
    }
@app.get("/bitunix/account")
async def bitunix_account():
    client = BitunixClient()

    result = await client.get_usdt_account()

    return result
@app.get("/bitunix/positions")
async def bitunix_positions():
    client = BitunixClient()

    result = await client.get_pending_positions()

    return result
@app.get("/bitunix/order-preview")
async def bitunix_order_preview():
    client = BitunixClient()

    margin_usdt = 10.0
    leverage = 20

    ticker_response = await client.get_ticker("BTCUSDT")
    pair_response = await client.get_trading_pair("BTCUSDT")

    ticker = ticker_response["data"][0]
    pair = pair_response["data"][0]

    current_price = float(ticker["lastPrice"])
    base_precision = int(pair["basePrecision"])
    quote_precision = int(pair["quotePrecision"])
    min_trade_volume = float(pair["minTradeVolume"])

    position_value = margin_usdt * leverage

    raw_qty = Decimal(str(position_value)) / Decimal(str(current_price))

    step = Decimal("1").scaleb(-base_precision)

    qty = raw_qty.quantize(
        step,
        rounding=ROUND_DOWN,
    )

    if qty < Decimal(str(min_trade_volume)):
        raise ValueError(
            f"Berechnete Menge {qty} liegt unter der Mindestmenge "
            f"{min_trade_volume}."
        )

    take_profit_price = round(
        current_price * 1.0035,
        quote_precision,
    )

    stop_loss_price = round(
        current_price * 0.99,
        quote_precision,
    )

    return {
        "symbol": "BTCUSDT",
        "side": "BUY",
        "tradeSide": "OPEN",
        "orderType": "MARKET",
        "marginMode": "CROSS",

        "margin_usdt": margin_usdt,
        "leverage": leverage,

        "current_price": current_price,
        "position_value_usdt": position_value,

        "raw_qty_btc": float(raw_qty),
        "final_qty_btc": str(qty),
        "min_trade_volume": min_trade_volume,

        "take_profit_percent": 0.35,
        "take_profit_price": take_profit_price,

        "stop_loss_percent": -1.0,
        "stop_loss_price": stop_loss_price,

        "LIVE_ORDER_SENT": False,
    }
@app.post("/bitunix/test-live-order")
async def bitunix_test_live_order(confirm: str):
    if confirm != "EXECUTE_TEST_TRADE":
        return {
            "status": "blocked",
            "message": "Live-Order nicht ausgeführt.",
        }

    client = BitunixClient()

    symbol = "BTCUSDT"
    margin_usdt = Decimal("10")
    leverage = Decimal("20")

    # Positionen VOR der neuen Order merken
    positions_before_response = await client.get_pending_positions()
    positions_before = positions_before_response.get("data", [])

    old_position_ids = {
        str(position["positionId"])
        for position in positions_before
        if position.get("positionId")
    }

    # Aktuellen Preis und Trading-Pair-Daten abrufen
    ticker_response = await client.get_ticker(symbol)
    pair_response = await client.get_trading_pair(symbol)

    ticker = ticker_response["data"][0]
    pair = pair_response["data"][0]

    current_price = Decimal(ticker["lastPrice"])
    base_precision = int(pair["basePrecision"])
    quote_precision = int(pair["quotePrecision"])

    position_value = margin_usdt * leverage

    raw_qty = position_value / current_price
    qty_step = Decimal("1").scaleb(-base_precision)

    qty = raw_qty.quantize(
        qty_step,
        rounding=ROUND_DOWN,
    )

    price_step = Decimal("1").scaleb(-quote_precision)

    tp_price = (
        current_price * Decimal("1.0035")
    ).quantize(
        price_step,
        rounding=ROUND_DOWN,
    )

    sl_price = (
        current_price * Decimal("0.99")
    ).quantize(
        price_step,
        rounding=ROUND_DOWN,
    )

    # Market-Order senden
    order_result = await client.place_order(
        symbol=symbol,
        qty=str(qty),
        side="BUY",
        trade_side="OPEN",
    )

    # Bis zu 5 Sekunden nach der neuen Position suchen
    new_position = None

    for _ in range(10):
        await asyncio.sleep(0.5)

        positions_after_response = await client.get_pending_positions()
        positions_after = positions_after_response.get("data", [])

        for position in positions_after:
            position_id = str(position.get("positionId", ""))

            if (
                position_id
                and position_id not in old_position_ids
                and position.get("symbol") == symbol
                and position.get("side") == "BUY"
            ):
                new_position = position
                break

        if new_position:
            break

    if not new_position:
        return {
            "status": "position_not_found",
            "message": (
                "Order wurde gesendet, aber die neue Position konnte "
                "noch nicht eindeutig erkannt werden. TP/SL wurde nicht gesetzt."
            ),
            "order_response": order_result,
        }

    position_id = str(new_position["positionId"])

    # Position TP/SL setzen
    tpsl_result = await client.place_position_tpsl(
        symbol=symbol,
        position_id=position_id,
        tp_price=str(tp_price),
        sl_price=str(sl_price),
    )
    create_open_trade(
        position_id=position_id,
        signal_id="BTC_15M_BULLISH_DIVERGENCE_REGULAR",
        signal_name="BTC 15 min Bullish Divergence Regular",
        symbol=symbol,
        timeframe="15M",
        direction="LONG",
        entry_price=float(current_price),
        tp_price=float(tp_price),
        sl_price=float(sl_price),
        margin_usdt=float(margin_usdt),
        leverage=int(leverage),
        quantity=float(qty),
        client_id=order_result["data"].get("clientId"),
        order_id=order_result["data"].get("orderId"),
    )

    return {
        "status": "success",
        "symbol": symbol,
        "position_id": position_id,
        "margin_usdt": str(margin_usdt),
        "leverage": str(leverage),
        "reference_price": str(current_price),
        "qty": str(qty),
        "tp_price": str(tp_price),
        "sl_price": str(sl_price),
        "order_response": order_result,
        "position_tpsl_response": tpsl_result,
    }
@app.get("/bitunix/order-detail-test")
async def bitunix_order_detail_test():
    client = BitunixClient()

    result = await client.get_order_detail(
        symbol="BTCUSDT",
        client_id="2079219697802899458",
    )

    return result