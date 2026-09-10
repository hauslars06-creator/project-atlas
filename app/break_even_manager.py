import os
import logging
from decimal import Decimal, ROUND_HALF_UP

from app.database.trade_repository import (
    mark_trade_tp_processed,
    update_manual_break_even_tracking,
)
from app.database.tpsl_repository import (
    get_open_trade_tpsl_orders_by_position,
)


logger = logging.getLogger(__name__)


def _safe_decimal(value, default="0"):
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


async def manage_staged_break_even(
    client,
    trades,
    position_lookup,
    active_tpsl_orders,
):
    active_order_ids = {
        str(order.get("id") or "").strip()
        for order in active_tpsl_orders
        if str(order.get("id") or "").strip()
    }

    buffer_percent = _safe_decimal(
        os.getenv("ATLAS_NET_BE_BUFFER_PERCENT", "0.20")
    ) / Decimal("100")

    for trade in trades:
        try:
            mode = str(
                getattr(trade, "break_even_mode", "OFF") or "OFF"
            ).strip().upper()

            if mode not in {"TP1", "TP2"}:
                continue

            position_id = str(trade.position_id)
            position = position_lookup.get(position_id)

            if not position:
                continue

            current_qty = _safe_decimal(position.get("qty"))
            original_qty = _safe_decimal(trade.quantity)
            tp1_qty = _safe_decimal(
                getattr(trade, "tp1_quantity", 0)
            )
            tp2_qty = _safe_decimal(
                getattr(trade, "tp2_quantity", 0)
            )

            if (
                current_qty <= 0
                or original_qty <= 0
                or tp1_qty <= 0
            ):
                continue

            tolerance = max(
                original_qty * Decimal("0.001"),
                Decimal("0.00000001"),
            )

            tp1_missing = (
                bool(getattr(trade, "tp1_order_id", None))
                and str(trade.tp1_order_id) not in active_order_ids
            )

            tp1_qty_reached = (
                current_qty
                <= original_qty - tp1_qty + tolerance
            )

            if (
                tp1_missing
                and tp1_qty_reached
                and getattr(trade, "tp1_processed_at", None) is None
            ):
                pair_response = await client.get_trading_pair(
                    str(trade.symbol)
                )
                pair = pair_response["data"][0]
                quote_precision = int(pair["quotePrecision"])
                price_step = Decimal("1").scaleb(-quote_precision)
                entry = _safe_decimal(trade.entry_price)
                direction = str(trade.direction).upper()

                if direction == "LONG":
                    new_sl = (
                        entry * (Decimal("1") + buffer_percent)
                    ).quantize(
                        price_step,
                        rounding=ROUND_HALF_UP,
                    ) + price_step
                else:
                    new_sl = (
                        entry * (Decimal("1") - buffer_percent)
                    ).quantize(
                        price_step,
                        rounding=ROUND_HALF_UP,
                    ) - price_step

                result = await client.modify_tpsl_order(
                    order_id=str(trade.sl_order_id),
                    sl_price=str(new_sl),
                    sl_qty=str(current_qty),
                )

                if str(result.get("code")) == "0":
                    mark_trade_tp_processed(
                        position_id,
                        1,
                        sl_price=float(new_sl),
                    )
                    trade.tp1_processed_at = True
                    logger.info(
                        "TP1 verarbeitet: position_id=%s, sl=%s",
                        position_id,
                        new_sl,
                    )

            if mode != "TP2" or tp2_qty <= 0:
                continue

            tp2_missing = (
                bool(getattr(trade, "tp2_order_id", None))
                and str(trade.tp2_order_id) not in active_order_ids
            )

            tp2_qty_reached = (
                current_qty
                <= original_qty - tp1_qty - tp2_qty + tolerance
            )

            if (
                tp2_missing
                and tp2_qty_reached
                and getattr(trade, "tp2_processed_at", None) is None
            ):
                tp1_price = _safe_decimal(trade.tp_price)

                result = await client.modify_tpsl_order(
                    order_id=str(trade.sl_order_id),
                    sl_price=str(tp1_price),
                    sl_qty=str(current_qty),
                )

                if str(result.get("code")) == "0":
                    mark_trade_tp_processed(
                        position_id,
                        2,
                        sl_price=float(tp1_price),
                    )
                    logger.info(
                        "TP2 verarbeitet: position_id=%s, sl=%s",
                        position_id,
                        tp1_price,
                    )

        except Exception:
            logger.exception(
                "Gestufter Break-even fehlgeschlagen: position_id=%s",
                getattr(trade, "position_id", "UNKNOWN"),
            )


async def manage_manual_break_even(client, trades, position_lookup):
    """
    Automatischer Break-Even fuer MANUELLE Trades
    (break_even_mode="AUTO"). Anders als der gestufte
    TP1/TP2-Modus (fuer automatisierte Signal-Trades) nutzt
    dieser Modus die tatsaechlich bei Bitunix synchronisierten
    TP/SL-Orders: sobald die Anzahl aktiver TP-Orders fuer
    eine Position sinkt (= eine TP-Stufe wurde getroffen oder
    entfernt), wird der SL auf Netto-Break-Even + Slippage-
    Puffer verschoben.
    """
    buffer_percent = _safe_decimal(
        os.getenv("ATLAS_NET_BE_BUFFER_PERCENT", "0.20")
    ) / Decimal("100")

    manual_trades = [
        trade
        for trade in trades
        if str(
            getattr(trade, "break_even_mode", "OFF") or "OFF"
        ).strip().upper()
        == "AUTO"
    ]

    if not manual_trades:
        return

    position_ids = [str(t.position_id) for t in manual_trades]
    orders_by_position = get_open_trade_tpsl_orders_by_position(
        position_ids
    )

    for trade in manual_trades:
        try:
            position_id = str(trade.position_id)
            position = position_lookup.get(position_id)

            if not position:
                continue

            orders = orders_by_position.get(position_id, [])
            tp_orders = [o for o in orders if o.tp_price is not None]
            sl_orders = [o for o in orders if o.sl_price is not None]

            current_tp_count = len(tp_orders)
            last_tp_count = getattr(
                trade, "manual_be_last_tp_count", None
            )
            already_triggered = bool(
                getattr(trade, "manual_be_triggered", False)
            )

            if last_tp_count is None:
                # Erste Beobachtung - nur Basiswert speichern,
                # noch nicht ausloesen (koennte sonst faelschlich
                # beim allerersten Sync-Durchlauf triggern).
                update_manual_break_even_tracking(
                    position_id, tp_count=current_tp_count
                )
                continue

            if (
                current_tp_count < last_tp_count
                and not already_triggered
                and sl_orders
            ):
                pair_response = await client.get_trading_pair(
                    str(trade.symbol)
                )
                pair = pair_response["data"][0]
                quote_precision = int(pair["quotePrecision"])
                price_step = Decimal("1").scaleb(-quote_precision)
                entry = _safe_decimal(trade.entry_price)
                direction = str(trade.direction).upper()

                if direction == "LONG":
                    new_sl = (
                        entry * (Decimal("1") + buffer_percent)
                    ).quantize(
                        price_step, rounding=ROUND_HALF_UP
                    ) + price_step
                else:
                    new_sl = (
                        entry * (Decimal("1") - buffer_percent)
                    ).quantize(
                        price_step, rounding=ROUND_HALF_UP
                    ) - price_step

                sl_order = sl_orders[0]
                current_qty = _safe_decimal(position.get("qty"))

                result = await client.modify_tpsl_order(
                    order_id=str(sl_order.exchange_order_id),
                    sl_price=str(new_sl),
                    sl_qty=str(current_qty),
                )

                if str(result.get("code")) == "0":
                    update_manual_break_even_tracking(
                        position_id,
                        tp_count=current_tp_count,
                        triggered=True,
                        sl_price=float(new_sl),
                    )
                    logger.info(
                        "Manueller Auto-Break-Even ausgeloest: "
                        "position_id=%s, sl=%s",
                        position_id,
                        new_sl,
                    )
                else:
                    logger.warning(
                        "Manueller Auto-Break-Even fehlgeschlagen "
                        "(Bitunix-Antwort): position_id=%s, %s",
                        position_id,
                        result,
                    )
            elif current_tp_count != last_tp_count:
                update_manual_break_even_tracking(
                    position_id, tp_count=current_tp_count
                )

        except Exception:
            logger.exception(
                "Manueller Auto-Break-Even fehlgeschlagen: "
                "position_id=%s",
                getattr(trade, "position_id", "UNKNOWN"),
            )
