# ==========================================================
# Project Atlas
# File: app/trade_sync.py
# Zweck: Offene Atlas-Trades mit BitUnix synchronisieren
# ==========================================================

import asyncio
import logging
import json
import os
from pathlib import Path
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from app.database.trade_repository import (
    create_external_open_trade,
    get_all_open_trades,
    move_open_trade_to_history,
    mark_trade_tp_processed,
    update_open_trade_live_data,
    update_open_trade_tpsl_from_exchange,
)
from app.database.tpsl_repository import (
    replace_open_trade_tpsl_snapshot,
)
from app.database.webhook_queue_repository import (
    has_recent_webhook_activity,
)
from app.exchanges.bitunix import BitunixClient
from app.break_even_manager import manage_staged_break_even


logger = logging.getLogger(__name__)

TRADE_SYNC_HEALTH_FILE = Path(
    "/app/data/trade_sync_health.json"
)


def _write_trade_sync_health(
    *,
    status: str,
    consecutive_failures: int,
    error: str | None = None,
) -> None:
    payload = {
        "status": status,
        "consecutive_failures": consecutive_failures,
        "updated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "error": (
            str(error)[:1000]
            if error
            else None
        ),
    }

    TRADE_SYNC_HEALTH_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    TRADE_SYNC_HEALTH_FILE.write_text(
        json.dumps(
            payload,
            indent=2,
        )
    )

# Schutz vor kurzzeitigen API-Abweichungen:
# Eine Position muss bei drei Prüfungen hintereinander fehlen.
_missing_checks: dict[str, int] = {}

REQUIRED_MISSING_CHECKS = 3


# ==================================================
# PROJECT ATLAS M5.3B EXTERNAL TRADES START
# ==================================================

# Eine unbekannte Position muss drei aufeinanderfolgende
# Prüfungen bestehen. Dadurch wird verhindert, dass eine
# gerade erst durch den Webhook eröffnete Atlas-Position
# vorschnell als externer Trade importiert wird.
_unknown_position_checks: dict[str, int] = {}

REQUIRED_EXTERNAL_CHECKS = 3


def _safe_float(
    value,
    default: float = 0.0,
) -> float:
    try:
        if value in (None, ""):
            return float(default)

        return float(value)

    except (TypeError, ValueError):
        return float(default)


def _safe_int(
    value,
    default: int = 1,
) -> int:
    try:
        if value in (None, ""):
            return int(default)

        return int(float(value))

    except (TypeError, ValueError):
        return int(default)


def _parse_exchange_datetime(
    value,
) -> datetime | None:
    if value in (None, ""):
        return None

    try:
        if isinstance(value, (int, float)):
            timestamp = float(value)

        else:
            text = str(value).strip()

            if text.replace(".", "", 1).isdigit():
                timestamp = float(text)

            else:
                normalized = text.replace(
                    "Z",
                    "+00:00",
                )

                parsed = datetime.fromisoformat(
                    normalized
                )

                if parsed.tzinfo is None:
                    parsed = parsed.replace(
                        tzinfo=timezone.utc
                    )

                return parsed.astimezone(
                    timezone.utc
                )

        # BitUnix liefert Zeitstempel typischerweise
        # in Millisekunden.
        if timestamp > 10_000_000_000:
            timestamp /= 1000

        return datetime.fromtimestamp(
            timestamp,
            tz=timezone.utc,
        )

    except (TypeError, ValueError, OSError):
        return None


def _external_direction(
    position: dict,
) -> str | None:
    side = str(
        position.get("side")
        or position.get("positionSide")
        or ""
    ).strip().upper()

    if side in {"BUY", "LONG"}:
        return "LONG"

    if side in {"SELL", "SHORT"}:
        return "SHORT"

    return None



# ==================================================
# PROJECT ATLAS M5.4A TPSL HELPERS START
# ==================================================

def _positive_float_or_none(value) -> float | None:
    try:
        if value in (None, ""):
            return None
        parsed = float(value)
    except (TypeError, ValueError):
        return None

    return parsed if parsed > 0 else None


def _representative_tpsl(
    trade,
    orders: list[dict],
) -> tuple[float, float]:
    """Kompatible Einzelwerte: jeweils das Entry-nächste sinnvolle Level."""

    tp_prices = sorted({
        value
        for order in orders
        if (value := _positive_float_or_none(order.get("tpPrice"))) is not None
    })
    sl_prices = sorted({
        value
        for order in orders
        if (value := _positive_float_or_none(order.get("slPrice"))) is not None
    })

    entry_price = _safe_float(getattr(trade, "entry_price", 0.0))
    direction = str(getattr(trade, "direction", "") or "").strip().upper()

    representative_tp = 0.0
    representative_sl = 0.0

    if direction == "LONG":
        valid_tp = [price for price in tp_prices if price > entry_price]
        valid_sl = [price for price in sl_prices if price < entry_price]
        representative_tp = min(valid_tp) if valid_tp else (min(tp_prices) if tp_prices else 0.0)
        representative_sl = max(valid_sl) if valid_sl else (max(sl_prices) if sl_prices else 0.0)

    elif direction == "SHORT":
        valid_tp = [price for price in tp_prices if price < entry_price]
        valid_sl = [price for price in sl_prices if price > entry_price]
        representative_tp = max(valid_tp) if valid_tp else (max(tp_prices) if tp_prices else 0.0)
        representative_sl = min(valid_sl) if valid_sl else (min(sl_prices) if sl_prices else 0.0)

    else:
        representative_tp = tp_prices[0] if tp_prices else 0.0
        representative_sl = sl_prices[0] if sl_prices else 0.0

    return float(representative_tp), float(representative_sl)

# ==================================================
# PROJECT ATLAS M5.4A TPSL HELPERS END
# ==================================================


def _external_trade_payload(
    position_id: str,
    position: dict,
) -> dict | None:
    symbol = str(
        position.get("symbol")
        or position.get("coin")
        or ""
    ).strip().upper()

    direction = _external_direction(position)

    entry_price = _safe_float(
        position.get("avgOpenPrice")
        or position.get("entryPrice")
        or position.get("avgPrice")
    )

    quantity = abs(
        _safe_float(
            position.get("qty")
            or position.get("quantity")
            or position.get("size")
        )
    )

    if (
        not symbol
        or direction is None
        or entry_price <= 0
        or quantity <= 0
    ):
        logger.warning(
            (
                "Externe Position konnte noch nicht "
                "importiert werden: position_id=%s, "
                "symbol=%s, direction=%s, entry=%s, qty=%s"
            ),
            position_id,
            symbol,
            direction,
            entry_price,
            quantity,
        )

        return None

    margin = _safe_float(
        position.get("margin")
        or position.get("positionMargin")
        or position.get("initialMargin")
    )

    leverage = max(
        1,
        _safe_int(
            position.get("leverage"),
            1,
        ),
    )

    tp_price = _safe_float(
        position.get("tpPrice")
        or position.get("takeProfitPrice")
        or position.get("takeProfit")
    )

    sl_price = _safe_float(
        position.get("slPrice")
        or position.get("stopLossPrice")
        or position.get("stopLoss")
    )

    opened_at = _parse_exchange_datetime(
        position.get("ctime")
        or position.get("createTime")
        or position.get("createdTime")
        or position.get("openTime")
        or position.get("createdAt")
    )

    return {
        "position_id": str(position_id),
        "symbol": symbol,
        "direction": direction,
        "entry_price": entry_price,
        "tp_price": tp_price,
        "sl_price": sl_price,
        "margin_usdt": margin,
        "leverage": leverage,
        "quantity": quantity,
        "opened_at": opened_at,
    }


# ==================================================
# PROJECT ATLAS M5.3B EXTERNAL TRADES END
# ==================================================


async def synchronize_open_trades(
    client: BitunixClient,
) -> dict:
    """
    Vergleicht Atlas-OpenTrades mit offenen BitUnix-Positionen.
    """

    response = await client.get_pending_positions()

    if response.get("code") != 0:
        raise RuntimeError(
            f"BitUnix-Positionsabfrage fehlgeschlagen: {response}"
        )

    positions = response.get("data")

    if not isinstance(positions, list):
        raise RuntimeError(
            "BitUnix hat keine gültige Positionsliste geliefert."
        )

    position_lookup = {
        str(position["positionId"]): position
        for position in positions
        if position.get("positionId")
    }

    bitunix_position_ids = set(position_lookup.keys())

    # Wird innerhalb des folgenden try-Blocks gesetzt, sobald
    # verfuegbar - dient als Wiederverwendungs-Cache fuer eine
    # sonst redundante DB-Abfrage direkt danach.
    open_trades_after_tpsl_sync: list | None = None

    # ==================================================
    # PROJECT ATLAS M5.4A MULTI TPSL READ SYNC START
    # ==================================================

    tpsl_order_count = 0
    tpsl_tp_level_count = 0
    tpsl_sl_level_count = 0
    tpsl_page_count = 0

    try:
        all_tpsl_orders: list[dict] = []
        seen_tpsl_order_ids: set[str] = set()
        tpsl_skip = 0
        tpsl_limit = 100
        maximum_tpsl_pages = 100

        for page_number in range(1, maximum_tpsl_pages + 1):
            tpsl_response = await client.get_pending_tpsl_orders(
                skip=tpsl_skip,
                limit=tpsl_limit,
            )

            page_orders = tpsl_response.get("data") or []
            if not isinstance(page_orders, list):
                raise RuntimeError("Ungültige BitUnix-TP/SL-Liste.")

            tpsl_page_count = page_number

            for order in page_orders:
                if not isinstance(order, dict):
                    continue

                order_id = str(order.get("id") or "").strip()
                if not order_id:
                    logger.warning(
                        "BitUnix-TP/SL-Auftrag ohne Order-ID ignoriert: %s",
                        order,
                    )
                    continue

                if order_id in seen_tpsl_order_ids:
                    continue

                seen_tpsl_order_ids.add(order_id)
                all_tpsl_orders.append(order)

            if len(page_orders) < tpsl_limit:
                break

            tpsl_skip += len(page_orders)
        else:
            raise RuntimeError(
                f"BitUnix-TP/SL-Pagination erreichte {maximum_tpsl_pages} Seiten."
            )

        active_tpsl_orders = [
            order
            for order in all_tpsl_orders
            if str(order.get("positionId") or "").strip() in bitunix_position_ids
        ]

        snapshot_result = replace_open_trade_tpsl_snapshot(
            active_tpsl_orders,
            active_position_ids=bitunix_position_ids,
        )

        pending_tpsl_by_position: dict[str, list[dict]] = {}
        for order in active_tpsl_orders:
            position_id = str(order.get("positionId") or "").strip()
            if position_id:
                pending_tpsl_by_position.setdefault(position_id, []).append(order)

        tpsl_order_count = int(snapshot_result["orders"])
        tpsl_tp_level_count = int(snapshot_result["tp_levels"])
        tpsl_sl_level_count = int(snapshot_result["sl_levels"])

        legacy_changes = 0

        for synced_trade in get_all_open_trades():
            synced_position_id = str(synced_trade.position_id)
            if synced_position_id not in bitunix_position_ids:
                continue

            new_tp, new_sl = _representative_tpsl(
                synced_trade,
                pending_tpsl_by_position.get(synced_position_id, []),
            )

            old_tp = float(getattr(synced_trade, "tp_price", 0.0) or 0.0)
            old_sl = float(getattr(synced_trade, "sl_price", 0.0) or 0.0)

            if abs(old_tp - new_tp) <= 1e-12 and abs(old_sl - new_sl) <= 1e-12:
                continue

            update_open_trade_tpsl_from_exchange(
                synced_position_id,
                tp_price=new_tp,
                sl_price=new_sl,
            )
            legacy_changes += 1

        open_trades_after_tpsl_sync = get_all_open_trades()

        await manage_staged_break_even(
            client,
            open_trades_after_tpsl_sync,
            position_lookup,
            active_tpsl_orders,
        )

        if snapshot_result["changed"] or legacy_changes:
            logger.info(
                "BitUnix-Multi-TP/SL-Snapshot synchronisiert: "
                "orders=%s, tp_levels=%s, sl_levels=%s, "
                "inserted=%s, updated=%s, deleted=%s, "
                "legacy_changes=%s, pages=%s",
                tpsl_order_count,
                tpsl_tp_level_count,
                tpsl_sl_level_count,
                snapshot_result["inserted"],
                snapshot_result["updated"],
                snapshot_result["deleted"],
                legacy_changes,
                tpsl_page_count,
            )

    except Exception:
        logger.exception(
            "BitUnix-Multi-TP/SL-Leseabfrage fehlgeschlagen. "
            "Der vorhandene lokale Snapshot bleibt unverändert."
        )

    # ==================================================
    # PROJECT ATLAS M5.4A MULTI TPSL READ SYNC END
    # ==================================================

    # Wiederverwendung statt erneuter DB-Abfrage: zwischen
    # dem Fetch fuer manage_staged_break_even() und hier
    # passiert kein weiterer Schreibzugriff auf open_trades,
    # eine erneute Abfrage waere redundant.
    atlas_trades = (
        open_trades_after_tpsl_sync
        if open_trades_after_tpsl_sync is not None
        else get_all_open_trades()
    )

    # ==================================================
    # PROJECT ATLAS M5.3B EXTERNAL TRADES START
    # ==================================================

    known_position_ids = {
        str(trade.position_id)
        for trade in atlas_trades
    }

    unknown_position_ids = (
        bitunix_position_ids
        - known_position_ids
    )

    # Nicht mehr offene unbekannte Positionen aus dem
    # temporären Prüfspeicher entfernen.
    for cached_position_id in list(
        _unknown_position_checks
    ):
        if cached_position_id not in unknown_position_ids:
            _unknown_position_checks.pop(
                cached_position_id,
                None,
            )

    imported_external_positions: list[str] = []

    for position_id in sorted(unknown_position_ids):
        check_count = (
            _unknown_position_checks.get(
                position_id,
                0,
            )
            + 1
        )

        _unknown_position_checks[position_id] = (
            check_count
        )

        if check_count < REQUIRED_EXTERNAL_CHECKS:
            continue

        position = position_lookup[position_id]

        # Schutz für gerade eingegangene Atlas-Webhooks:
        # Während der Queue-Schutzfrist keine unbekannte
        # Position vorschnell als extern/manuell importieren.
        # Symbol-spezifisch geprüft, damit ein laufendes
        # Signal für ein anderes Symbol nicht die Erkennung
        # unabhängiger manueller Positionen blockiert.
        if has_recent_webhook_activity(
            seconds=180,
            symbol=position.get("symbol"),
        ):
            continue

        payload = _external_trade_payload(
            position_id,
            position,
        )

        if payload is None:
            # Nach weiteren drei Prüfungen erneut versuchen.
            _unknown_position_checks[position_id] = 0
            continue

        existing_atlas_trade = any(
            str(t.position_id) == str(position_id)
            for t in atlas_trades
        )

        if existing_atlas_trade:
            _unknown_position_checks.pop(
                position_id,
                None,
            )
            continue

        external_trade = create_external_open_trade(
            **payload
        )

        _unknown_position_checks.pop(
            position_id,
            None,
        )

        if external_trade is not None:
            imported_external_positions.append(
                position_id
            )

            logger.warning(
                (
                    "Externe BitUnix-Position importiert "
                    "und automatisch gesperrt: "
                    "position_id=%s, symbol=%s"
                ),
                position_id,
                payload["symbol"],
            )

    if imported_external_positions:
        # Neu importierte Positionen noch im selben
        # Durchlauf in die Live-Synchronisation aufnehmen.
        atlas_trades = get_all_open_trades()

    # ==================================================
    # PROJECT ATLAS M5.3B EXTERNAL TRADES END
    # ==================================================

    archived_positions: list[str] = []

    for trade in atlas_trades:
        position_id = str(trade.position_id)

        if position_id in bitunix_position_ids:
            position = position_lookup[position_id]

            unrealized = float(
                position.get("unrealizedPNL", 0)
            )
            realized = float(
                position.get("realizedPNL", 0)
            )
            margin = float(
                position.get("margin", 0)
            )
            quantity = float(
                position.get("qty", 0)
            )
            average_open_price = float(
                position.get("avgOpenPrice", 0)
            )
            exchange_side = str(
                position.get("side", "")
            ).strip().upper()

            liquidation_raw = position.get("liqPrice")
            liquidation_price = (
                float(liquidation_raw)
                if liquidation_raw not in (None, "")
                else None
            )

            if quantity > 0 and average_open_price > 0:
                price_difference = unrealized / quantity

                if exchange_side == "BUY":
                    current_price = (
                        average_open_price + price_difference
                    )
                elif exchange_side == "SELL":
                    current_price = (
                        average_open_price - price_difference
                    )
                else:
                    current_price = average_open_price
            else:
                current_price = average_open_price

            pnl_percent = (
                unrealized / margin * 100
                if margin > 0
                else 0
            )

            update_open_trade_live_data(
                position_id,
                current_price=current_price,
                liquidation_price=liquidation_price,
                unrealized_pnl=unrealized,
                realized_pnl=realized,
                pnl_percent=pnl_percent,
                current_margin=(
                    margin if margin > 0 else None
                ),
            )

            _missing_checks.pop(position_id, None)
            continue

        missing_count = _missing_checks.get(
            position_id,
            0,
        ) + 1

        _missing_checks[position_id] = missing_count

        if missing_count < REQUIRED_MISSING_CHECKS:
            continue

        # M3.1B:
        # Vor der Archivierung versuchen wir, die exakten
        # Abschlusswerte aus der BitUnix-Positionshistorie
        # abzurufen.
        #
        # Falls BitUnix noch keinen Eintrag liefert oder die
        # Abfrage fehlschlägt, bleiben alle Werte None.
        # move_open_trade_to_history() verwendet dann
        # automatisch den bewährten M3.1A-Fallback.
        exact_exit_price: float | None = None
        exact_pnl_usdt: float | None = None
        exact_pnl_percent: float | None = None

        try:
            closed_position = (
                await client.get_history_position(
                    position_id
                )
            )

            if closed_position is not None:
                close_price_raw = closed_position.get(
                    "closePrice"
                )
                realized_pnl_raw = closed_position.get(
                    "realizedPNL"
                )

                if close_price_raw not in (None, ""):
                    exact_exit_price = float(
                        close_price_raw
                    )

                if realized_pnl_raw not in (None, ""):
                    # BitUnix realizedPNL wird als
                    # maßgeblicher finaler Abschluss-PnL
                    # übernommen.
                    #
                    # Die Gebühr wird nicht zusätzlich
                    # addiert oder subtrahiert, da die
                    # reale API-Antwort gezeigt hat, dass
                    # realizedPNL bereits dem Nettowert
                    # des abgeschlossenen Trades entspricht.
                    exact_pnl_usdt = float(
                        realized_pnl_raw
                    )

                margin_basis = (
                    float(trade.current_margin)
                    if (
                        trade.current_margin is not None
                        and float(trade.current_margin) > 0
                    )
                    else float(trade.margin_usdt)
                )

                if (
                    exact_pnl_usdt is not None
                    and margin_basis > 0
                ):
                    exact_pnl_percent = (
                        exact_pnl_usdt
                        / margin_basis
                        * 100
                    )

                logger.info(
                    (
                        "Exakte BitUnix-Abschlussdaten "
                        "gefunden: position_id=%s, "
                        "exit=%s, pnl=%s, roi=%s"
                    ),
                    position_id,
                    exact_exit_price,
                    exact_pnl_usdt,
                    exact_pnl_percent,
                )
            else:
                logger.warning(
                    (
                        "Noch keine BitUnix-History für "
                        "Position %s gefunden. "
                        "M3.1A-Fallback wird verwendet."
                    ),
                    position_id,
                )

        except Exception:
            logger.exception(
                (
                    "BitUnix-History-Abfrage für Position "
                    "%s fehlgeschlagen. "
                    "M3.1A-Fallback wird verwendet."
                ),
                position_id,
            )

        close_reason = "BITUNIX_POSITION_CLOSED"

        if exact_exit_price is not None:

            try:
                exit_value = float(exact_exit_price)
                tp_value = float(trade.tp_price)
                sl_value = float(trade.sl_price)

                candidates = []

                if tp_value > 0:
                    tp_distance_percent = (
                        abs(exit_value - tp_value)
                        / tp_value
                        * 100.0
                    )

                    candidates.append(
                        (
                            tp_distance_percent,
                            "TAKE_PROFIT",
                        )
                    )

                if sl_value > 0:
                    sl_distance_percent = (
                        abs(exit_value - sl_value)
                        / sl_value
                        * 100.0
                    )

                    candidates.append(
                        (
                            sl_distance_percent,
                            "STOP_LOSS",
                        )
                    )

                if candidates:
                    candidates.sort(
                        key=lambda item: item[0]
                    )

                    distance_percent, detected_reason = (
                        candidates[0]
                    )

                    # Konservativer Toleranzbereich für
                    # Slippage / Market-Ausführung.
                    if distance_percent <= 0.20:
                        close_reason = detected_reason

            except (
                TypeError,
                ValueError,
                ZeroDivisionError,
            ):
                close_reason = (
                    "BITUNIX_POSITION_CLOSED"
                )

        history_entry = move_open_trade_to_history(
            position_id,
            exit_price=exact_exit_price,
            pnl_usdt=exact_pnl_usdt,
            pnl_percent=exact_pnl_percent,
            close_reason=close_reason,
        )

        if history_entry is not None:
            archived_positions.append(position_id)

        _missing_checks.pop(position_id, None)

    return {
        "bitunix_positions": len(bitunix_position_ids),
        "atlas_open_trades": len(atlas_trades),

        # PROJECT ATLAS M5.3B EXTERNAL TRADES START
        "imported_external_positions": (
            imported_external_positions
        ),
        # PROJECT ATLAS M5.3B EXTERNAL TRADES END

        "pending_tpsl_orders": tpsl_order_count,
        "pending_tp_levels": tpsl_tp_level_count,
        "pending_sl_levels": tpsl_sl_level_count,
        "tpsl_pages": tpsl_page_count,
        "archived_positions": archived_positions,
    }


# Modulweite Referenz, damit main.py den Client beim
# App-Shutdown sauber schliessen kann.
shared_bitunix_client: BitunixClient | None = None


async def trade_sync_loop(
    interval_seconds: int = 5,
) -> None:
    """
    Führt die Synchronisierung dauerhaft im Hintergrund aus.
    """

    global shared_bitunix_client

    client = BitunixClient()
    shared_bitunix_client = client

    consecutive_failures = 0

    while True:
        try:
            result = await synchronize_open_trades(client)

            consecutive_failures = 0

            _write_trade_sync_health(
                status="OK",
                consecutive_failures=0,
            )

            # PROJECT ATLAS M5.3B EXTERNAL TRADES START
            if result.get("imported_external_positions"):
                logger.warning(
                    "Neue externe Positionen erkannt: %s",
                    result["imported_external_positions"],
                )
            # PROJECT ATLAS M5.3B EXTERNAL TRADES END

            if result["archived_positions"]:
                logger.info(
                    "Geschlossene Positionen archiviert: %s",
                    result["archived_positions"],
                )

        except asyncio.CancelledError:
            raise

        except Exception as exc:
            consecutive_failures += 1

            _write_trade_sync_health(
                status="ERROR",
                consecutive_failures=consecutive_failures,
                error=(
                    f"{type(exc).__name__}: {exc}"
                ),
            )

            logger.exception(
                "Fehler bei der BitUnix-Trade-Synchronisierung."
            )

        await asyncio.sleep(interval_seconds)
