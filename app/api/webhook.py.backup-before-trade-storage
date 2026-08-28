import asyncio
import time
import uuid
from decimal import Decimal, ROUND_DOWN
from app.notifications.telegram import send_telegram_alert

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config.signals import SIGNALS
from app.exchanges.bitunix import BitunixClient


router = APIRouter()

LIVE_TRADING_ENABLED = True
EMERGENCY_STOP = False

TRADE_LOCKS: dict[str, asyncio.Lock] = {}


def get_trade_lock(
    symbol: str,
    side: str,
) -> asyncio.Lock:
    lock_key = f"{symbol}:{side}"

    if lock_key not in TRADE_LOCKS:
        TRADE_LOCKS[lock_key] = asyncio.Lock()

    return TRADE_LOCKS[lock_key]


class TradingViewSignal(BaseModel):
    signal_id: str


async def process_signal(
    signal_id: str,
    force_live: bool = False,
):
    received_at = time.perf_counter()
    if EMERGENCY_STOP:
        raise HTTPException(
            status_code=503,
            detail={
                "message": (
                    "Project Atlas Emergency Stop ist aktiv. "
                    "Es werden keine neuen Trades ausgeführt."
                ),
                "emergency_stop": True,
            },
        )

    signal = SIGNALS.get(signal_id)

    if signal is None:
        raise HTTPException(
            status_code=404,
            detail="Unbekannte Signal-ID",
        )

    if not signal["enabled"]:
        raise HTTPException(
            status_code=403,
            detail="Signal ist deaktiviert",
        )

    client = BitunixClient()

    symbol = signal["symbol"]

    margin_usdt = Decimal(
        str(signal["margin_usdt"])
    )

    leverage = Decimal(
        str(signal["leverage"])
    )

    # Aktuelle Marktdaten abrufen
    ticker_response, pair_response = await asyncio.gather(
    client.get_ticker(symbol),
    client.get_trading_pair(symbol),
    )

    ticker = ticker_response["data"][0]
    pair = pair_response["data"][0]

    current_price = Decimal(
        ticker["lastPrice"]
    )

    base_precision = int(
        pair["basePrecision"]
    )

    quote_precision = int(
        pair["quotePrecision"]
    )

    min_trade_volume = Decimal(
        str(pair["minTradeVolume"])
    )

    # Positionsgröße berechnen
    position_value = (
        margin_usdt * leverage
    )

    raw_qty = (
        position_value / current_price
    )

    qty_step = Decimal("1").scaleb(
        -base_precision
    )

    qty = raw_qty.quantize(
        qty_step,
        rounding=ROUND_DOWN,
    )

    if qty < min_trade_volume:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Berechnete Menge {qty} liegt "
                f"unter der Mindestmenge "
                f"{min_trade_volume}."
            ),
        )

    # TP und SL berechnen
    price_step = Decimal("1").scaleb(
        -quote_precision
    )

    tp_percent = (
        Decimal(
            str(
                signal[
                    "take_profit_percent"
                ]
            )
        )
        / Decimal("100")
    )

    sl_percent = (
        Decimal(
            str(
                signal[
                    "stop_loss_percent"
                ]
            )
        )
        / Decimal("100")
    )

    direction = signal["direction"]

    if direction == "LONG":
        side = "BUY"

        tp_price = (
            current_price
            * (
                Decimal("1")
                + tp_percent
            )
        ).quantize(
            price_step,
            rounding=ROUND_DOWN,
        )

        sl_price = (
            current_price
            * (
                Decimal("1")
                - sl_percent
            )
        ).quantize(
            price_step,
            rounding=ROUND_DOWN,
        )

    elif direction == "SHORT":
        side = "SELL"

        tp_price = (
            current_price
            * (
                Decimal("1")
                - tp_percent
            )
        ).quantize(
            price_step,
            rounding=ROUND_DOWN,
        )

        sl_price = (
            current_price
            * (
                Decimal("1")
                + sl_percent
            )
        ).quantize(
            price_step,
            rounding=ROUND_DOWN,
        )

    else:
        raise HTTPException(
            status_code=400,
            detail=(
                "Ungültige Handelsrichtung."
            ),
        )

    print(
        "\n--- TRADINGVIEW SIGNAL ---"
    )
    print(
        f"Signal:    "
        f"{signal['display_name']}"
    )
    print(f"Symbol:    {symbol}")
    print(f"Richtung:  {direction}")
    print(f"Preis:     {current_price}")
    print(
        f"Margin:    {margin_usdt} USDT"
    )
    print(f"Hebel:     {leverage}x")
    print(f"Menge:     {qty}")
    print(f"TP:        {tp_price}")
    print(f"SL:        {sl_price}")
    print("--------------------------\n")

    # Normale TradingView-Alarme bleiben
    # blockiert, solange LIVE_TRADING_ENABLED
    # False ist.
    should_execute_live = (
        LIVE_TRADING_ENABLED
        or force_live
    )

    if not should_execute_live:
        processing_time_ms = round(
            (
                time.perf_counter()
                - received_at
            )
            * 1000,
            3,
        )

        return {
            "status": "preview",
            "signal_id": signal_id,
            "live_order": False,
            "trade_preview": {
                "symbol": symbol,
                "direction": direction,
                "side": side,
                "margin_usdt": str(
                    margin_usdt
                ),
                "leverage": str(
                    leverage
                ),
                "reference_price": str(
                    current_price
                ),
                "qty": str(qty),
                "take_profit_price": str(
                    tp_price
                ),
                "stop_loss_price": str(
                    sl_price
                ),
            },
            "processing_time_ms": (
                processing_time_ms
            ),
            "message": (
                "Live-Trading ist deaktiviert."
            ),
        }

    # Lock für Symbol und Richtung
    trade_lock = get_trade_lock(
        symbol,
        side,
    )

    async with trade_lock:
        # Positionen vor der Order speichern
        positions_before_response = (
            await client.get_pending_positions()
        )

        positions_before = (
            positions_before_response.get(
                "data",
                [],
            )
        )

        old_position_ids = {
            str(position["positionId"])
            for position in positions_before
            if position.get("positionId")
        }

        # Eindeutige Trade-ID
        client_id = uuid.uuid4().hex

        # Market-Order senden
        # Bei Netzwerk-/Timeout-Fehler anschließend
        # über die eindeutige client_id nachprüfen.
        order_submit_exception = None


        try:
            order_result = (
                await client.place_order(
                    symbol=symbol,
                    qty=str(qty),
                    side=side,
                    trade_side="OPEN",
                    client_id=client_id,
                )
            )

        except Exception as exc:
            order_submit_exception = str(exc)
            order_result = None

        if order_submit_exception:
            recovered_order = None

            # Die Order könnte trotz Timeout/Netzwerkfehler
            # von Bitunix angenommen worden sein.
            for _ in range(10):
                await asyncio.sleep(0.2)

                try:
                    recovery_response = (
                        await client.get_order_detail(
                            symbol=symbol,
                            client_id=client_id,
                        )
                    )

                    if (
                        recovery_response.get("code") == 0
                        and recovery_response.get("data")
                    ):
                        recovered_order = (
                            recovery_response["data"]
                        )
                        break

                except Exception:
                    pass

            if recovered_order:
                # Order wurde trotz ursprünglichem
                # Netzwerkfehler bei Bitunix gefunden.
                order_result = {
                    "code": 0,
                    "data": {
                        "orderId": recovered_order.get(
                            "orderId"
                        ),
                        "clientId": client_id,
                    },
                    "msg": (
                        "Order recovered after "
                        "submission exception"
                    ),
                }

            else:
                alert_message = (
                    "🚨 PROJECT ATLAS – ORDER-SUBMISSION UNKLAR\n\n"
                    "Beim Senden der Market-Order ist ein "
                    "technischer Fehler aufgetreten und die "
                    "Order konnte anschließend auch nicht über "
                    "die Client-ID gefunden werden.\n\n"
                    f"Symbol: {symbol}\n"
                    f"Richtung: {direction}\n"
                    f"Client ID: {client_id}\n"
                    f"Menge: {qty}\n"
                    f"Fehler: {order_submit_exception}\n\n"
                    "ACHTUNG: Bitte Bitunix sofort prüfen."
                )

                telegram_sent = await send_telegram_alert(
                    alert_message
                )

                raise HTTPException(
                    status_code=500,
                    detail={
                        "message": (
                        "Der Status der Order ist nach "
                        "einem technischen Fehler unklar."
                        ),
                        "critical": True,
                        "client_id": client_id,
                        "symbol": symbol,
                        "direction": direction,
                        "submission_exception": (
                        order_submit_exception
                        ),
                        "telegram_alert_sent": telegram_sent,
                    },
                )

        if (
            order_result.get("code") != 0
            or not order_result.get("data")
        ):
            alert_message = (
                "🚨 PROJECT ATLAS – ORDER ABGELEHNT\n\n"
                "Bitunix hat die Market-Order nicht "
                "erfolgreich angenommen.\n\n"
                f"Symbol: {symbol}\n"
                f"Richtung: {direction}\n"
                f"Client ID: {client_id}\n"
                f"Menge: {qty}\n"
                f"Bitunix Code: {order_result.get('code')}\n"
                f"Meldung: {order_result.get('msg')}"
            )

            telegram_sent = await send_telegram_alert(
                alert_message
            )

            raise HTTPException(
                status_code=502,
                detail={
                    "message": (
                        "Bitunix hat die Market-Order "
                        "nicht erfolgreich angenommen."
                    ),
                    "critical": True,
                    "client_id": client_id,
                    "symbol": symbol,
                    "direction": direction,
                    "order_response": order_result,
                    "telegram_alert_sent": telegram_sent,
                },
            )
        
        # Genau diese Order als FILLED
        # bestätigen
        order_detail = None

        for _ in range(25):
            await asyncio.sleep(0.1)

            detail_response = (
                await client.get_order_detail(
                    symbol=symbol,
                    client_id=client_id,
                )
            )

            detail_data = (
                detail_response.get("data")
            )

            if (
                detail_response.get("code")
                == 0
                and detail_data
                and detail_data.get("status")
                == "FILLED"
            ):
                order_detail = detail_data
                break

        if not order_detail:
            alert_message = (
                "🚨 PROJECT ATLAS – ORDERSTATUS UNKLAR\n\n"
                "Eine Market-Order wurde an Bitunix gesendet, "
                "konnte aber nicht eindeutig als FILLED "
                "bestätigt werden.\n\n"
                f"Symbol: {symbol}\n"
                f"Richtung: {direction}\n"
                f"Client ID: {client_id}\n"
                f"Menge: {qty}\n\n"
                "ACHTUNG: Bitte Bitunix sofort prüfen. "
                "Die Order könnte trotzdem ausgeführt worden sein."
            )

            telegram_sent = await send_telegram_alert(
                alert_message
            )

            raise HTTPException(
                status_code=500,
                detail={
                    "message": (
                        "Die Order wurde gesendet, konnte "
                        "aber nicht eindeutig als FILLED "
                        "bestätigt werden. Bitunix muss "
                        "manuell geprüft werden."
                    ),
                    "critical": True,
                    "client_id": client_id,
                    "symbol": symbol,
                    "direction": direction,
                    "order_response": order_result,
                    "telegram_alert_sent": telegram_sent,
                },
            )

        # Neue Position suchen
        new_position = None

        for _ in range(25):
            await asyncio.sleep(0.1)

            positions_after_response = (
                await client
                .get_pending_positions()
            )

            positions_after = (
                positions_after_response.get(
                    "data",
                    [],
                )
            )

            candidates = []

            for position in positions_after:
                position_id = str(
                    position.get(
                        "positionId",
                        "",
                    )
                )

                if (
                    position_id
                    and position_id
                    not in old_position_ids
                    and position.get(
                        "symbol"
                    )
                    == symbol
                    and position.get(
                        "side"
                    )
                    == side
                ):
                    candidates.append(
                        position
                    )

            if len(candidates) == 1:
                new_position = (
                    candidates[0]
                )
                break

        if not new_position:
            alert_message = (
                "🚨 PROJECT ATLAS – POSITION NICHT ZUGEORDNET\n\n"
                "Eine Order wurde als FILLED bestätigt, "
                "aber die neue Position konnte nicht "
                "eindeutig gefunden werden.\n\n"
                f"Symbol: {symbol}\n"
                f"Richtung: {direction}\n"
                f"Client ID: {client_id}\n"
                f"Order ID: {order_detail.get('orderId')}\n"
                f"Menge: {qty}\n\n"
                "ACHTUNG: Bitte Bitunix sofort prüfen. "
                "Es könnte eine Position ohne TP/SL offen sein."
            )

            telegram_sent = await send_telegram_alert(
                alert_message
            )

            raise HTTPException(
                status_code=500,
                detail={
                    "message": (
                        "Order wurde ausgeführt, aber die "
                        "neue Position konnte nicht eindeutig "
                        "zugeordnet werden. Bitunix muss "
                        "manuell geprüft werden."
                    ),
                    "critical": True,
                    "client_id": client_id,
                    "symbol": symbol,
                    "direction": direction,
                    "order_detail": order_detail,
                    "telegram_alert_sent": telegram_sent,
                },
            )

        position_id = str(
            new_position["positionId"]
        )

        # Position TP/SL setzen – mit Retry
        tpsl_result = None
        tpsl_success = False

        for attempt in range(1, 4):
            try:
                tpsl_result = (
                    await client.place_position_tpsl(
                        symbol=symbol,
                        position_id=position_id,
                        tp_price=str(tp_price),
                        sl_price=str(sl_price),
                    )
                )

                if (
                    tpsl_result.get("code") == 0
                    and tpsl_result.get("data")
                ):
                    tpsl_success = True
                    break

            except Exception as exc:
                tpsl_result = {
                    "code": "exception",
                    "msg": str(exc),
                }

            if attempt < 3:
                await asyncio.sleep(0.25)

        if not tpsl_success:
            # FAIL-SAFE:
            # Ungeschützte Position sofort schließen
            try:
                close_result = (
                    await client.flash_close_position(
                        position_id=position_id,
                    )
                )

                close_success = (
                    close_result.get("code") == 0
                )

            except Exception as exc:
                close_result = {
                    "code": "exception",
                    "msg": str(exc),
                }
                close_success = False

            # Prüfen, ob Position wirklich geschlossen wurde
            position_closed = False

            if close_success:
                for _ in range(10):
                    await asyncio.sleep(0.2)

                    positions_response = (
                        await client.get_pending_positions()
                    )

                    open_positions = (
                        positions_response.get(
                            "data",
                            [],
                        )
                    )

                    still_open = any(
                        str(
                            position.get(
                                "positionId",
                                "",
                            )
                        )
                        == position_id
                        for position in open_positions
                    )

                    if not still_open:
                        position_closed = True
                        break
            alert_message = (
                "🚨 PROJECT ATLAS – KRITISCHER FEHLER\n\n"
                "TP/SL konnte nach 3 Versuchen "
                "nicht gesetzt werden.\n\n"
                f"Symbol: {symbol}\n"
                f"Richtung: {direction}\n"
                f"Position ID: {position_id}\n"
                f"Client ID: {client_id}\n"
                f"Fail-Safe angenommen: {close_success}\n"
                f"Position geschlossen: {position_closed}"
            )

            telegram_sent = await send_telegram_alert(
                alert_message
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "telegram_alert_sent": telegram_sent,
                    "message": (
                        "KRITISCH: TP/SL konnte nach "
                        "3 Versuchen nicht gesetzt werden. "
                        "Der automatische Fail-Safe wurde "
                        "ausgeführt."
                    ),
                    "critical": True,
                    "position_id": position_id,
                    "client_id": client_id,
                    "symbol": symbol,
                    "direction": direction,
                    "tpsl_response": tpsl_result,
                    "failsafe_close_success": close_success,
                    "failsafe_position_closed": position_closed,
                    "failsafe_close_response": close_result,
                },
            )

    processing_time_ms = round(
        (
            time.perf_counter()
            - received_at
        )
        * 1000,
        3,
    )

    return {
        "status": "trade_executed",
        "signal_id": signal_id,
        "client_id": client_id,
        "live_order": True,
        "symbol": symbol,
        "direction": direction,
        "position_id": position_id,
        "margin_usdt": str(margin_usdt),
        "leverage": str(leverage),
        "qty": str(qty),
        "tp_price": str(tp_price),
        "sl_price": str(sl_price),
        "order_response": order_result,
        "order_detail": order_detail,
        "position_tpsl_response": tpsl_result,
        "processing_time_ms": processing_time_ms,
    }


@router.post("/tradingview")
async def receive_tradingview_signal(
    payload: TradingViewSignal,
):
    try:
        return await process_signal(
            signal_id=payload.signal_id,
            force_live=False,
        )

    except HTTPException:
        # Bereits kontrolliert behandelte Fehler
        # nicht erneut als technischen Fehler melden.
        raise

    except Exception as exc:
        alert_message = (
            "🚨 PROJECT ATLAS – TECHNISCHER FEHLER\n\n"
            "Bei der Verarbeitung eines TradingView-Signals "
            "ist ein unerwarteter Fehler aufgetreten.\n\n"
            f"Signal ID: {payload.signal_id}\n"
            f"Fehler: {type(exc).__name__}: {exc}\n\n"
            "Bitte Project Atlas und Bitunix prüfen."
        )

        telegram_sent = await send_telegram_alert(
            alert_message
        )

        raise HTTPException(
            status_code=500,
            detail={
                "message": (
                    "Unerwarteter technischer Fehler "
                    "bei der Signalverarbeitung."
                ),
                "critical": True,
                "signal_id": payload.signal_id,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "telegram_alert_sent": telegram_sent,
            },
        )



@router.get("/test-lock")
async def test_trade_lock():
    lock = get_trade_lock(
        "BTCUSDT",
        "BUY",
    )

    events = []

    async def simulated_trade(
        name: str,
    ):
        requested_at = time.perf_counter()

        async with lock:
            started_at = time.perf_counter()

            events.append({
                "trade": name,
                "event": "started",
                "wait_ms": round(
                    (
                        started_at
                        - requested_at
                    )
                    * 1000,
                    2,
                ),
            })

            await asyncio.sleep(1)

            events.append({
                "trade": name,
                "event": "finished",
            })

    await asyncio.gather(
        simulated_trade("trade_1"),
        simulated_trade("trade_2"),
    )

    return {
        "status": "lock_test_complete",
        "events": events,
    }