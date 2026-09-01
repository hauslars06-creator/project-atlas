import asyncio

from fastapi import APIRouter, HTTPException
from app.database.database import SessionLocal
from app.database.models import TradeHistory, OpenTrade
from sqlalchemy import func, or_
from datetime import datetime, timedelta, timezone
from pydantic import BaseModel, Field

from app.database.trade_repository import (
    get_all_open_trades,
    get_open_trade,
    get_unlocked_open_trades,
    set_trade_lock,
    query_trade_history,
)

from app.database.tpsl_repository import (
    get_open_trade_tpsl_order,
    get_open_trade_tpsl_orders_by_position,
)
from app.exchanges.bitunix import BitunixClient
from app.mae_analysis import _analyze_post_stop_loss


router = APIRouter(
    prefix="/trades",
    tags=["Trades"],
)


def _positive_number(value) -> float | None:
    try:
        if value in (None, ""):
            return None
        parsed = float(value)
    except (TypeError, ValueError):
        return None

    return parsed if parsed > 0 else None


def serialize_tpsl_order(order) -> dict:
    return {
        "order_id": str(getattr(order, "exchange_order_id", "")),
        "position_id": str(getattr(order, "position_id", "")),
        "symbol": getattr(order, "symbol", None),
        "tp_price": getattr(order, "tp_price", None),
        "tp_stop_type": getattr(order, "tp_stop_type", None),
        "tp_order_type": getattr(order, "tp_order_type", None),
        "tp_order_price": getattr(order, "tp_order_price", None),
        "tp_quantity": getattr(order, "tp_quantity", None),
        "sl_price": getattr(order, "sl_price", None),
        "sl_stop_type": getattr(order, "sl_stop_type", None),
        "sl_order_type": getattr(order, "sl_order_type", None),
        "sl_order_price": getattr(order, "sl_order_price", None),
        "sl_quantity": getattr(order, "sl_quantity", None),
        "synced_at": (
            getattr(order, "synced_at", None).isoformat()
            if getattr(order, "synced_at", None)
            else None
        ),
    }


def _serialize_tpsl_levels(trade, orders: list, level_type: str) -> list[dict]:
    is_tp = level_type == "tp"
    levels = []

    for order in orders:
        price = _positive_number(
            getattr(order, "tp_price" if is_tp else "sl_price", None)
        )
        if price is None:
            continue

        levels.append({
            "order_id": str(getattr(order, "exchange_order_id", "")),
            "price": price,
            "quantity": getattr(
                order,
                "tp_quantity" if is_tp else "sl_quantity",
                None,
            ),
            "stop_type": getattr(
                order,
                "tp_stop_type" if is_tp else "sl_stop_type",
                None,
            ),
            "order_type": getattr(
                order,
                "tp_order_type" if is_tp else "sl_order_type",
                None,
            ),
            "order_price": getattr(
                order,
                "tp_order_price" if is_tp else "sl_order_price",
                None,
            ),
        })

    direction = str(getattr(trade, "direction", "") or "").strip().upper()
    reverse = direction == "SHORT" if is_tp else direction == "LONG"
    levels.sort(key=lambda level: level["price"], reverse=reverse)

    for index, level in enumerate(levels, start=1):
        level["level"] = index

    return levels


def serialize_open_trade(trade, tpsl_orders: list | None = None) -> dict:
    orders = list(tpsl_orders or [])
    tp_levels = _serialize_tpsl_levels(trade, orders, "tp")
    sl_levels = _serialize_tpsl_levels(trade, orders, "sl")

    return {
        "id": getattr(trade, "id", None),
        "position_id": str(getattr(trade, "position_id", "")),
        "signal_id": getattr(trade, "signal_id", None),
        "signal_name": getattr(trade, "signal_name", None),
        "symbol": getattr(trade, "symbol", None),
        "timeframe": getattr(trade, "timeframe", None),
        "direction": getattr(trade, "direction", None),
        "entry_price": getattr(trade, "entry_price", None),
        "tp_price": getattr(trade, "tp_price", None),
        "sl_price": getattr(trade, "sl_price", None),
        "margin_usdt": getattr(trade, "margin_usdt", None),
        "leverage": getattr(trade, "leverage", None),
        "quantity": getattr(trade, "quantity", None),
        "client_id": getattr(trade, "client_id", None),
        "order_id": getattr(trade, "order_id", None),
        "status": getattr(trade, "status", "OPEN"),
        "opened_at": (
            getattr(trade, "opened_at", None).isoformat()
            if getattr(trade, "opened_at", None)
            else None
        ),
        "trade_source": getattr(trade, "trade_source", "ATLAS"),
        "exchange": getattr(trade, "exchange", "BITUNIX"),
        "is_locked": getattr(trade, "is_locked", False),
        "current_price": getattr(trade, "current_price", None),
        "unrealized_pnl": getattr(trade, "unrealized_pnl", None),
        "realized_pnl": getattr(trade, "realized_pnl", None),
        "pnl_percent": getattr(trade, "pnl_percent", None),
        "current_margin": getattr(trade, "current_margin", None),
        "liquidation_price": getattr(trade, "liquidation_price", None),
        "last_exchange_sync": (
            trade.last_exchange_sync.isoformat()
            if getattr(trade, "last_exchange_sync", None)
            else None
        ),
        "updated_at": (
            trade.updated_at.isoformat()
            if getattr(trade, "updated_at", None)
            else None
        ),
        "created_at": (
            getattr(trade, "created_at", None).isoformat()
            if getattr(trade, "created_at", None)
            else None
        ),
        "tpsl_orders": [serialize_tpsl_order(order) for order in orders],
        "tpsl_order_count": len(orders),
        "tp_levels": tp_levels,
        "sl_levels": sl_levels,
        "tp_level_count": len(tp_levels),
        "sl_level_count": len(sl_levels),
        "has_multiple_tpsl": len(tp_levels) > 1 or len(sl_levels) > 1,
        "tpsl_edit_mode": (
            "MULTI_READ_ONLY"
            if len(tp_levels) > 1 or len(sl_levels) > 1
            else "LEGACY_SINGLE"
        ),
    }


@router.get("/open")
async def list_open_trades(
    lock_scope: str = "",
):
    try:
        lock_scope = (
            str(lock_scope or "")
            .strip()
            .upper()
        )

        trades = get_all_open_trades()

        if lock_scope == "LOCKED":
            trades = [
                trade
                for trade in trades
                if bool(trade.is_locked)
            ]

        elif lock_scope == "ACTIVE":
            trades = [
                trade
                for trade in trades
                if not bool(trade.is_locked)
            ]
        position_ids = {str(trade.position_id) for trade in trades}
        tpsl_by_position = get_open_trade_tpsl_orders_by_position(position_ids)

        return {
            "success": True,
            "count": len(trades),
            "tpsl_order_count": sum(
                len(orders) for orders in tpsl_by_position.values()
            ),
            "trades": [
                serialize_open_trade(
                    trade,
                    tpsl_by_position.get(str(trade.position_id), []),
                )
                for trade in trades
            ],
        }

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="Offene Trades konnten nicht geladen werden.",
        ) from exc



def _actual_margin_usdt(trade) -> float | None:
    """
    Rekonstruiert die tatsaechlich ausgefuehrte Margin aus
    pnl_usdt und pnl_percent (die bereits korrekt auf Basis
    der echten, von BitUnix gemeldeten Margin gespeichert
    wurden) - konsistent mit der angezeigten ROI-Prozentzahl.

    Fallback auf die konfigurierte Ziel-Margin, falls die
    Rueckrechnung nicht moeglich ist (z.B. Breakeven-Trades
    mit pnl_percent == 0).
    """

    pnl_usdt = getattr(trade, "pnl_usdt", None)
    pnl_percent = getattr(trade, "pnl_percent", None)
    configured_margin = getattr(trade, "margin_usdt", None)

    if (
        pnl_usdt is None
        or pnl_percent is None
        or pnl_percent == 0
    ):
        return configured_margin

    try:
        return float(pnl_usdt) / (float(pnl_percent) / 100.0)
    except (ZeroDivisionError, TypeError, ValueError):
        return configured_margin


def serialize_history_trade(trade) -> dict:
    opened_at = getattr(trade, "opened_at", None)
    closed_at = getattr(trade, "closed_at", None)

    duration_seconds = None

    if opened_at and closed_at:
        try:
            duration_seconds = max(
                0,
                int(
                    (
                        closed_at - opened_at
                    ).total_seconds()
                ),
            )
        except Exception:
            duration_seconds = None

    return {
        "id": getattr(trade, "id", None),
        "position_id": str(
            getattr(trade, "position_id", "")
        ),
        "signal_id": getattr(
            trade,
            "signal_id",
            None,
        ),
        "signal_name": getattr(
            trade,
            "signal_name",
            None,
        ),
        "symbol": getattr(trade, "symbol", None),
        "timeframe": getattr(
            trade,
            "timeframe",
            None,
        ),
        "direction": getattr(
            trade,
            "direction",
            None,
        ),
        "entry_price": getattr(
            trade,
            "entry_price",
            None,
        ),
        "exit_price": getattr(
            trade,
            "exit_price",
            None,
        ),
        "margin_usdt": _actual_margin_usdt(trade),
        "configured_margin_usdt": getattr(
            trade,
            "margin_usdt",
            None,
        ),
        "leverage": getattr(
            trade,
            "leverage",
            None,
        ),
        "quantity": getattr(
            trade,
            "quantity",
            None,
        ),
        "pnl_usdt": getattr(
            trade,
            "pnl_usdt",
            None,
        ),
        "pnl_percent": getattr(
            trade,
            "pnl_percent",
            None,
        ),
        "close_reason": getattr(
            trade,
            "close_reason",
            None,
        ),
        "exchange": getattr(
            trade,
            "exchange",
            "BITUNIX",
        ),
        "is_locked": getattr(
            trade,
            "is_locked",
            False,
        ),
        "opened_at": (
            opened_at.isoformat()
            if opened_at
            else None
        ),
        "closed_at": (
            closed_at.isoformat()
            if closed_at
            else None
        ),
        "duration_seconds": duration_seconds,
    }


@router.get("/history")
async def list_trade_history(
    search: str = "",
    direction: str = "",
    symbol: str = "",
    timeframe: str = "",
    close_reason: str = "",
    result: str = "",
    status: str = "",
    lock_scope: str = "",
    exchange: str = "",
    days: int | None = None,
    sort_by: str = "closed_at",
    sort_dir: str = "desc",
    page: int = 1,
    per_page: int = 25,
):
    try:
        result = query_trade_history(
            search=search,
            direction=direction,
            symbol=symbol,
            timeframe=timeframe,
            close_reason=close_reason,
            result=result,
            lock_scope=lock_scope,
            exchange=exchange,
            days=days,
            sort_by=sort_by,
            sort_dir=sort_dir,
            page=page,
            per_page=per_page,
        )

        return {
            "success": True,
            "count": len(result["trades"]),
            "total": result["total"],
            "page": result["page"],
            "per_page": result["per_page"],
            "total_pages": result["total_pages"],
            "symbols": result["symbols"],
            "timeframes": result["timeframes"],
            "stats": result["stats"],
            "stats_by_scope": result.get("stats_by_scope"),
            "trades": [
                serialize_history_trade(trade)
                for trade in result["trades"]
            ],
        }

    except Exception as exc:
        import traceback
        traceback.print_exc()

        raise HTTPException(
            status_code=500,
            detail=str(exc),
        ) from exc






@router.get("/pnl-summary")
async def pnl_summary():

    db = SessionLocal()

    try:
        now = datetime.now(timezone.utc)

        def calculate(days=None):
            # Manuelle/externe BitUnix-Trades zaehlen IMMER
            # als "Gesperrt" - unabhaengig vom is_locked-
            # Schalter (der nur steuert, ob der Bot den Trade
            # automatisch verwalten darf). Konsistent zur
            # Trade-Historie- und Performance-Analyse-Logik.

            active_history = db.query(
                func.coalesce(
                    func.sum(TradeHistory.pnl_usdt),
                    0
                )
            ).filter(
                TradeHistory.is_locked.is_(False),
                TradeHistory.signal_id != "EXTERNAL",
            )

            locked_history = db.query(
                func.coalesce(
                    func.sum(TradeHistory.pnl_usdt),
                    0
                )
            ).filter(
                or_(
                    TradeHistory.is_locked.is_(True),
                    TradeHistory.signal_id == "EXTERNAL",
                )
            )

            if days:
                cutoff = now - timedelta(days=days)

                active_history = active_history.filter(
                    TradeHistory.closed_at >= cutoff
                )

                locked_history = locked_history.filter(
                    TradeHistory.closed_at >= cutoff
                )

            active_open = db.query(
                func.coalesce(
                    func.sum(OpenTrade.unrealized_pnl),
                    0
                )
            ).filter(
                OpenTrade.is_locked.is_(False),
                OpenTrade.signal_id != "EXTERNAL",
                OpenTrade.status == "OPEN"
            ).scalar()

            locked_open = db.query(
                func.coalesce(
                    func.sum(OpenTrade.unrealized_pnl),
                    0
                )
            ).filter(
                or_(
                    OpenTrade.is_locked.is_(True),
                    OpenTrade.signal_id == "EXTERNAL",
                ),
                OpenTrade.status == "OPEN"
            ).scalar()

            return {
                "active":
                    float(active_history.scalar() or 0)
                    +
                    float(active_open or 0),

                "locked":
                    float(locked_history.scalar() or 0)
                    +
                    float(locked_open or 0)
            }

        return {
            "success": True,
            "periods": {
                "today": calculate(1),
                "7_days": calculate(7),
                "14_days": calculate(14),
                "30_days": calculate(30),
                "all": calculate(None)
            }
        }

    finally:
        db.close()

@router.post("/close-all")
async def close_all_unlocked_trades(
    confirm: bool = False,
):
    """
    Schließt ausschließlich entsperrte offene Trades.

    Ohne confirm=true wird nur eine Vorschau geliefert.
    Gesperrte Trades werden bewusst nicht berücksichtigt.

    Lokale OpenTrade-Einträge werden hier nicht gelöscht.
    trade_sync.py übernimmt nach Bestätigung durch BitUnix
    die Übertragung in die Trade-Historie.
    """

    try:
        all_trades = get_all_open_trades()
        unlocked_trades = get_unlocked_open_trades()

        locked_count = sum(
            1
            for trade in all_trades
            if bool(getattr(trade, "is_locked", False))
        )

        position_ids = [
            str(trade.position_id)
            for trade in unlocked_trades
        ]

        if not confirm:
            return {
                "success": True,
                "executed": False,
                "confirmation_required": True,
                "open_trade_count": len(all_trades),
                "unlocked_trade_count": len(unlocked_trades),
                "locked_trade_count": locked_count,
                "positions_to_close": position_ids,
                "message": (
                    "Vorschau erstellt. Zum tatsächlichen "
                    "Schließen confirm=true übergeben."
                ),
            }

        if not unlocked_trades:
            return {
                "success": True,
                "executed": True,
                "open_trade_count": len(all_trades),
                "requested_count": 0,
                "closed_request_count": 0,
                "failed_count": 0,
                "locked_trade_count": locked_count,
                "results": [],
                "message": (
                    "Keine entsperrten offenen Trades "
                    "zum Schließen vorhanden."
                ),
            }

        client = BitunixClient()
        results = []

        for trade in unlocked_trades:
            position_id = str(trade.position_id)

            try:
                exchange_response = (
                    await client.flash_close_position(
                        position_id=position_id,
                    )
                )

                accepted = (
                    exchange_response.get("code") == 0
                )

                results.append(
                    {
                        "position_id": position_id,
                        "symbol": getattr(
                            trade,
                            "symbol",
                            None,
                        ),
                        "accepted": accepted,
                        "exchange_response": (
                            exchange_response
                        ),
                    }
                )

            except Exception as exc:
                results.append(
                    {
                        "position_id": position_id,
                        "symbol": getattr(
                            trade,
                            "symbol",
                            None,
                        ),
                        "accepted": False,
                        "error": str(exc),
                    }
                )

        accepted_count = sum(
            1
            for result in results
            if result["accepted"]
        )

        failed_count = (
            len(results) - accepted_count
        )

        return {
            "success": failed_count == 0,
            "executed": True,
            "open_trade_count": len(all_trades),
            "requested_count": len(results),
            "closed_request_count": accepted_count,
            "failed_count": failed_count,
            "locked_trade_count": locked_count,
            "results": results,
            "message": (
                "Close-all-Anfragen wurden verarbeitet. "
                "Gesperrte Trades wurden übersprungen."
            ),
        }

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Die Close-all-Aktion konnte nicht "
                "verarbeitet werden."
            ),
        ) from exc


@router.post("/{position_id}/lock")
async def lock_trade(position_id: str):
    """
    Sperrt einen offenen Atlas-Trade gegen
    manuelle Änderungen und Schließaktionen.
    """

    try:
        status = set_trade_lock(
            position_id,
            True,
        )

        if status is None:
            raise HTTPException(
                status_code=404,
                detail="Offener Trade wurde nicht gefunden.",
            )

        return {
            "success": True,
            "position_id": str(position_id),
            "is_locked": bool(status),
            "message": "Trade wurde gesperrt.",
        }

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="Trade konnte nicht gesperrt werden.",
        ) from exc


@router.post("/{position_id}/unlock")
async def unlock_trade(position_id: str):
    """
    Entsperrt einen offenen Atlas-Trade.
    """

    try:
        status = set_trade_lock(
            position_id,
            False,
        )

        if status is None:
            raise HTTPException(
                status_code=404,
                detail="Offener Trade wurde nicht gefunden.",
            )

        return {
            "success": True,
            "position_id": str(position_id),
            "is_locked": bool(status),
            "message": "Trade wurde entsperrt.",
        }

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="Trade konnte nicht entsperrt werden.",
        ) from exc


# ==================================================
# PROJECT ATLAS M5.3A ACTION BACKEND START
# ==================================================

class TradeTpSlUpdateRequest(BaseModel):
    """
    Neue absolute TP- und SL-Preise für eine offene Position.

    Beide Werte werden gemeinsam übertragen, damit die
    bestehende BitUnix-Methode place_position_tpsl()
    eindeutig und konsistent verwendet werden kann.
    """

    tp_price: float = Field(
        ...,
        gt=0,
        description="Absoluter Take-Profit-Preis",
    )

    sl_price: float = Field(
        ...,
        gt=0,
        description="Absoluter Stop-Loss-Preis",
    )


def require_open_trade(position_id: str):
    """
    Liefert einen lokalen OpenTrade oder erzeugt HTTP 404.
    """

    trade = get_open_trade(
        str(position_id),
    )

    if trade is None:
        raise HTTPException(
            status_code=404,
            detail="Offener Trade wurde nicht gefunden.",
        )

    return trade


def require_unlocked_trade(position_id: str):
    """
    Verhindert Änderungen an gesperrten Trades.

    Externe/manuelle Positionen bleiben dadurch geschützt,
    bis sie ausdrücklich entsperrt wurden.
    """

    trade = require_open_trade(position_id)

    if bool(getattr(trade, "is_locked", False)):
        raise HTTPException(
            status_code=423,
            detail=(
                "Dieser Trade ist gesperrt. "
                "Bitte zuerst ausdrücklich entsperren."
            ),
        )

    return trade


class AddMarginRequest(BaseModel):
    amount_usdt: float = Field(gt=0)


@router.get("/{position_id}/mae-test")
async def mae_test_single_trade(position_id: str):
    """
    TESTROUTE (Schritt 1 der MAE-Analyse):

    Berechnet fuer genau EINEN abgeschlossenen Trade
    (aus trade_history) die Maximum Adverse Excursion
    (MAE) - wie weit der Preis nach Entry maximal in die
    falsche Richtung gelaufen ist, bevor der Trade
    geschlossen wurde. Nutzt oeffentliche BitUnix-
    Kerzendaten (kein Backtest, keine gespeicherten
    Kursdaten - alles live nachgeladen).
    """

    db = SessionLocal()

    try:
        trade = (
            db.query(TradeHistory)
            .filter(
                TradeHistory.position_id == str(position_id)
            )
            .first()
        )
    finally:
        db.close()

    if trade is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "Kein abgeschlossener Trade mit dieser "
                "position_id in trade_history gefunden."
            ),
        )

    opened_at = trade.opened_at
    closed_at = trade.closed_at

    if opened_at is None or closed_at is None:
        raise HTTPException(
            status_code=422,
            detail="Trade hat keine vollstaendigen Zeitstempel.",
        )

    if opened_at.tzinfo is None:
        opened_at = opened_at.replace(tzinfo=timezone.utc)
    if closed_at.tzinfo is None:
        closed_at = closed_at.replace(tzinfo=timezone.utc)

    start_ms = int(opened_at.timestamp() * 1000)
    end_ms = int(closed_at.timestamp() * 1000)

    duration_minutes = (
        (closed_at - opened_at).total_seconds() / 60.0
    )

    # Feinste Aufloesung waehlen, die die gesamte
    # Trade-Dauer noch innerhalb des 200-Kerzen-Limits
    # von BitUnix abdeckt.
    if duration_minutes <= 200:
        interval = "1m"
    elif duration_minutes <= 200 * 5:
        interval = "5m"
    elif duration_minutes <= 200 * 15:
        interval = "15m"
    elif duration_minutes <= 200 * 60:
        interval = "1h"
    else:
        interval = "4h"

    try:
        client = BitunixClient()

        kline_response = await client.get_kline(
            symbol=str(trade.symbol),
            interval=interval,
            start_time_ms=start_ms,
            end_time_ms=end_ms,
            limit=200,
        )

        raw_candles = kline_response.get("data") or []

        # Sicherheitsfilter: BitUnix scheint startTime/endTime
        # nicht immer strikt serverseitig einzuhalten - wir
        # filtern deshalb clientseitig zusaetzlich auf das
        # tatsaechliche Trade-Fenster, um keine Kerzen von
        # vor Entry oder nach Exit einzubeziehen.
        candles = [
            c for c in raw_candles
            if start_ms <= int(c.get("time", 0)) <= end_ms
        ]

        if not candles:
            raise HTTPException(
                status_code=502,
                detail=(
                    "Nach Zeitfilterung blieben keine "
                    "Kerzendaten fuer diesen Trade uebrig "
                    f"(roh erhalten: {len(raw_candles)})."
                ),
            )

        direction = str(trade.direction or "").strip().upper()
        entry_price = float(trade.entry_price)

        lows = [float(c["low"]) for c in candles]
        highs = [float(c["high"]) for c in candles]

        if direction == "LONG":
            worst_price = min(lows)
            best_price = max(highs)
            mae_percent = (
                (entry_price - worst_price) / entry_price * 100.0
            )
            mfe_percent = (
                (best_price - entry_price) / entry_price * 100.0
            )
        elif direction == "SHORT":
            worst_price = max(highs)
            best_price = min(lows)
            mae_percent = (
                (worst_price - entry_price) / entry_price * 100.0
            )
            mfe_percent = (
                (entry_price - best_price) / entry_price * 100.0
            )
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unbekannte Richtung: {direction}",
            )

        post_sl_analysis = None

        if str(trade.close_reason or "").strip().upper() == "STOP_LOSS":
            post_sl_analysis = await _analyze_post_stop_loss(
                client=client,
                symbol=str(trade.symbol),
                direction=direction,
                entry_price=entry_price,
                tp_price=float(trade.tp_price),
                closed_at=closed_at,
                lookback_days=7,
            )

        return {
            "success": True,
            "position_id": str(position_id),
            "symbol": trade.symbol,
            "direction": direction,
            "entry_price": entry_price,
            "exit_price": trade.exit_price,
            "sl_price": trade.sl_price,
            "tp_price": trade.tp_price,
            "close_reason": trade.close_reason,
            "opened_at": opened_at.isoformat(),
            "closed_at": closed_at.isoformat(),
            "duration_minutes": round(duration_minutes, 1),
            "candle_interval_used": interval,
            "candle_count_raw": len(raw_candles),
            "candle_count_filtered": len(candles),
            "mae_percent": round(mae_percent, 4),
            "mfe_percent": round(mfe_percent, 4),
            "worst_price_reached": worst_price,
            "best_price_reached": best_price,
            "post_stop_loss_analysis": post_sl_analysis,
        }

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        ) from exc


@router.post("/{position_id}/margin")
async def add_position_margin(
    position_id: str,
    payload: AddMarginRequest,
):
    """
    Erhoeht eine offene Position durch eine zusaetzliche
    Market-Order in dieselbe Richtung.

    Das ist keine reine Margin-Isolierung, sondern eine
    echte zusaetzliche Order: Positionsgroesse und
    eingesetzte Margin steigen, und der durchschnittliche
    Einstiegspreis verschiebt sich entsprechend. Funktioniert
    sowohl fuer CROSS- als auch ISOLATED-Positionen, da es
    sich technisch um eine normale OPEN-Order handelt.
    """

    from decimal import Decimal, ROUND_DOWN

    trade = require_unlocked_trade(position_id)

    direction = str(
        getattr(trade, "direction", "") or ""
    ).strip().upper()

    if direction == "LONG":
        side = "BUY"
    elif direction == "SHORT":
        side = "SELL"
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Unbekannte Richtung: {direction}",
        )

    leverage = int(getattr(trade, "leverage", 0) or 0)

    if leverage <= 0:
        raise HTTPException(
            status_code=400,
            detail="Kein gueltiger Hebel fuer diesen Trade hinterlegt.",
        )

    symbol = str(trade.symbol)

    try:
        client = BitunixClient()

        ticker_response = await client.get_ticker(symbol)
        pair_response = await client.get_trading_pair(symbol)

        current_price = Decimal(
            str(ticker_response["data"][0]["lastPrice"])
        )

        pair = pair_response["data"][0]
        base_precision = int(pair["basePrecision"])
        min_trade_volume = Decimal(str(pair["minTradeVolume"]))

        position_value = (
            Decimal(str(payload.amount_usdt)) * leverage
        )

        raw_qty = position_value / current_price

        qty_step = Decimal("1").scaleb(-base_precision)

        qty = raw_qty.quantize(
            qty_step,
            rounding=ROUND_DOWN,
        )

        if qty < min_trade_volume:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Der Betrag ergibt eine zu kleine Menge "
                    f"({qty} {symbol}). Mindestens "
                    f"{min_trade_volume} erforderlich."
                ),
            )

        exchange_response = await client.place_order(
            symbol=symbol,
            qty=str(qty),
            side=side,
            trade_side="OPEN",
            position_id=str(position_id),
        )

        accepted = (
            str(exchange_response.get("code")) == "0"
        )

        if not accepted:
            raise HTTPException(
                status_code=502,
                detail=(
                    exchange_response.get("msg")
                    or "BitUnix hat die zusaetzliche Order "
                       "nicht bestaetigt."
                ),
            )

        return {
            "success": True,
            "position_id": str(position_id),
            "symbol": symbol,
            "amount_usdt": payload.amount_usdt,
            "qty": str(qty),
            "message": (
                "Zusaetzliche Order wurde platziert. "
                "Position und Einstiegspreis werden beim "
                "naechsten Sync aktualisiert."
            ),
        }

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        ) from exc


class BreakEvenRequest(BaseModel):
    slippage_percent: float = Field(ge=0, le=5, default=0.1)


@router.post("/{position_id}/break-even")
async def set_position_break_even(
    position_id: str,
    payload: BreakEvenRequest,
):
    """
    Verschiebt den Stop-Loss einer offenen Position auf
    Netto-Break-Even (Einstiegspreis + tatsaechlich gezahlte
    BitUnix-Gebuehren + optionaler Slippage-Puffer).

    Die Gebuehr wird live von BitUnix uebernommen (Entry-Fee
    aus den Positionsdaten, Exit-Fee als gleich hoch
    angenaehert), nicht geschaetzt.
    """

    from decimal import Decimal, ROUND_HALF_UP

    # Break-Even ist eine rein risikoreduzierende Aktion und
    # daher auch fuer gesperrte (manuell/extern importierte)
    # Trades erlaubt - anders als schliessende oder
    # positionsvergroessernde Aktionen.
    trade = require_open_trade(position_id)

    symbol = str(trade.symbol)
    direction = str(
        getattr(trade, "direction", "") or ""
    ).strip().upper()

    entry_price = Decimal(str(trade.entry_price))

    try:
        client = BitunixClient()

        positions = await client.get_pending_positions()

        position_list = (
            positions.get("data") or []
            if isinstance(positions, dict)
            else []
        )

        live_position = next(
            (
                p for p in position_list
                if str(p.get("positionId")) == str(position_id)
            ),
            None,
        )

        if live_position is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    "Position wurde bei BitUnix nicht "
                    "gefunden. Sie ist moeglicherweise "
                    "bereits geschlossen."
                ),
            )

        live_qty = Decimal(str(live_position.get("qty") or "0"))
        entry_fee = Decimal(
            str(live_position.get("fee") or "0")
        ).copy_abs()

        if live_qty <= 0:
            raise HTTPException(
                status_code=422,
                detail="Ungueltige Positionsgroesse von BitUnix erhalten.",
            )

        pair_response = await client.get_trading_pair(symbol)
        pair = pair_response["data"][0]
        quote_precision = int(pair["quotePrecision"])
        price_step = Decimal("1").scaleb(-quote_precision)

        # Entry-Gebuehr ist bekannt, Exit-Gebuehr wird als
        # gleich hoch angenaehert (gleiche Notional-Groesse).
        estimated_total_fee = entry_fee * 2

        fee_price_offset = (
            estimated_total_fee / live_qty
            if live_qty > 0
            else Decimal("0")
        )

        slippage_offset = entry_price * (
            Decimal(str(payload.slippage_percent)) / Decimal("100")
        )

        if direction == "LONG":
            sl_price = (
                entry_price + fee_price_offset + slippage_offset
            ).quantize(price_step, rounding=ROUND_HALF_UP)
        elif direction == "SHORT":
            sl_price = (
                entry_price - fee_price_offset - slippage_offset
            ).quantize(price_step, rounding=ROUND_HALF_UP)
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unbekannte Richtung: {direction}",
            )

        # SL laeuft bei BitUnix immer als Positions-SL
        # (nicht als separate TP/SL-Order) - dieselbe
        # Methode, die auch beim urspruenglichen Eroeffnen
        # verwendet wird. Erneutes Aufrufen ersetzt den
        # bestehenden Positions-SL.
        exchange_response = await client.place_position_sl(
            symbol=symbol,
            position_id=str(position_id),
            sl_price=str(sl_price),
        )

        accepted = (
            str(exchange_response.get("code")) == "0"
        )

        if not accepted:
            raise HTTPException(
                status_code=502,
                detail=(
                    exchange_response.get("msg")
                    or "BitUnix hat die Break-Even-Aenderung "
                       "nicht bestaetigt."
                ),
            )

        return {
            "success": True,
            "position_id": str(position_id),
            "symbol": symbol,
            "entry_price": float(entry_price),
            "sl_price": float(sl_price),
            "fee_price_offset": float(fee_price_offset),
            "slippage_percent": payload.slippage_percent,
            "message": (
                f"Stop-Loss auf Netto-Break-Even gesetzt: {sl_price}"
            ),
        }

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        ) from exc


@router.post("/{position_id}/close")
async def close_single_trade(
    position_id: str,
):
    """
    Schließt genau eine entsperrte offene BitUnix-Position.

    Der lokale Datenbankeintrag wird nicht vorschnell
    gelöscht. trade_sync.py bestätigt die Schließung über
    BitUnix und verschiebt den Trade anschließend korrekt
    in die Historie.
    """

    trade = require_unlocked_trade(position_id)

    try:
        client = BitunixClient()

        exchange_response = (
            await client.flash_close_position(
                position_id=str(position_id),
            )
        )

        accepted = (
            str(exchange_response.get("code")) == "0"
        )

        if not accepted:
            raise HTTPException(
                status_code=502,
                detail=(
                    "BitUnix hat die Schließung "
                    "der Position nicht bestätigt."
                ),
            )

        return {
            "success": True,
            "accepted": True,
            "position_id": str(position_id),
            "symbol": getattr(trade, "symbol", None),
            "exchange_response": exchange_response,
            "message": (
                "Die Schließung der Position wurde "
                "von BitUnix angenommen."
            ),
        }

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Der Trade konnte nicht geschlossen werden."
            ),
        ) from exc


# ==================================================
# PROJECT ATLAS M5.5A MULTI LEVEL PERCENT EDITOR START
# ==================================================

class TradeTpSlOrderPercentUpdateRequest(BaseModel):
    tp_percent: float | None = Field(
        default=None,
        gt=0,
        lt=100,
        description=(
            "Positive TP-Preisdifferenz zum Entry in Prozent."
        ),
    )

    sl_percent: float | None = Field(
        default=None,
        gt=0,
        lt=100,
        description=(
            "Positive SL-Preisdifferenz zum Entry in Prozent."
        ),
    )


def _price_from_entry_percent(
    *,
    entry_price: float,
    direction: str,
    level_type: str,
    percent: float,
) -> float:
    entry = float(entry_price)
    distance = float(percent) / 100.0
    normalized_direction = str(direction).strip().upper()
    normalized_type = str(level_type).strip().upper()

    if entry <= 0:
        raise ValueError("Ungültiger Entry-Preis.")

    if not 0 < distance < 1:
        raise ValueError(
            "Die Preisdifferenz muss zwischen 0 und 100 % liegen."
        )

    if normalized_direction == "LONG":
        price = (
            entry * (1 + distance)
            if normalized_type == "TP"
            else entry * (1 - distance)
        )

    elif normalized_direction == "SHORT":
        price = (
            entry * (1 - distance)
            if normalized_type == "TP"
            else entry * (1 + distance)
        )

    else:
        raise ValueError("Ungültige Handelsrichtung.")

    if price <= 0:
        raise ValueError(
            "Der berechnete TP-/SL-Preis ist ungültig."
        )

    return float(f"{price:.12f}")


def _positive_optional_float(value) -> float | None:
    try:
        if value in (None, ""):
            return None

        parsed = float(value)

    except (TypeError, ValueError):
        return None

    return parsed if parsed > 0 else None


def _exchange_prices_confirmed(
    order: dict,
    *,
    expected_tp: float | None,
    expected_sl: float | None,
) -> bool:
    tolerance_factor = 1e-8

    if expected_tp is not None:
        actual_tp = _positive_optional_float(
            order.get("tpPrice")
        )

        if actual_tp is None:
            return False

        tolerance = max(
            1e-8,
            abs(expected_tp) * tolerance_factor,
        )

        if abs(actual_tp - expected_tp) > tolerance:
            return False

    if expected_sl is not None:
        actual_sl = _positive_optional_float(
            order.get("slPrice")
        )

        if actual_sl is None:
            return False

        tolerance = max(
            1e-8,
            abs(expected_sl) * tolerance_factor,
        )

        if abs(actual_sl - expected_sl) > tolerance:
            return False

    return True


@router.post(
    "/{position_id}/tpsl-orders/{order_id}"
)
async def update_tpsl_order_by_percent(
    position_id: str,
    order_id: str,
    request: TradeTpSlOrderPercentUpdateRequest,
):
    """
    Ändert exakt einen vorhandenen TP-/SL-Auftrag.

    Die Eingaben sind positive Prozentabstände vom
    Entry-Preis. ROI, Margin und Hebel werden bei der
    Preisberechnung ausdrücklich nicht verwendet.
    """

    trade = require_unlocked_trade(position_id)

    if (
        request.tp_percent is None
        and request.sl_percent is None
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                "Mindestens TP oder SL muss geändert werden."
            ),
        )

    local_order = get_open_trade_tpsl_order(
        position_id,
        order_id,
    )

    if local_order is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "Der offene TP-/SL-Auftrag wurde lokal "
                "nicht gefunden. Bitte Dashboard aktualisieren."
            ),
        )

    entry_price = float(
        getattr(trade, "entry_price", 0) or 0
    )

    direction = str(
        getattr(trade, "direction", "") or ""
    ).strip().upper()

    symbol = str(
        getattr(trade, "symbol", "") or ""
    ).strip().upper()

    has_tp = _positive_optional_float(
        getattr(local_order, "tp_price", None)
    ) is not None

    has_sl = _positive_optional_float(
        getattr(local_order, "sl_price", None)
    ) is not None

    if request.tp_percent is not None and not has_tp:
        raise HTTPException(
            status_code=422,
            detail=(
                "Dieser Auftrag besitzt kein vorhandenes TP-Level."
            ),
        )

    if request.sl_percent is not None and not has_sl:
        raise HTTPException(
            status_code=422,
            detail=(
                "Dieser Auftrag besitzt kein vorhandenes SL-Level."
            ),
        )

    calculated_tp = None
    calculated_sl = None

    try:
        if request.tp_percent is not None:
            calculated_tp = _price_from_entry_percent(
                entry_price=entry_price,
                direction=direction,
                level_type="TP",
                percent=request.tp_percent,
            )

        if request.sl_percent is not None:
            calculated_sl = _price_from_entry_percent(
                entry_price=entry_price,
                direction=direction,
                level_type="SL",
                percent=request.sl_percent,
            )

    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    tp_quantity = (
        _positive_optional_float(
            getattr(local_order, "tp_quantity", None)
        )
        if calculated_tp is not None
        else None
    )

    sl_quantity = (
        _positive_optional_float(
            getattr(local_order, "sl_quantity", None)
        )
        if calculated_sl is not None
        else None
    )

    if calculated_tp is not None and tp_quantity is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "Die vorhandene TP-Menge fehlt. "
                "Der Auftrag wird nicht verändert."
            ),
        )

    if calculated_sl is not None and sl_quantity is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "Die vorhandene SL-Menge fehlt. "
                "Der Auftrag wird nicht verändert."
            ),
        )

    try:
        client = BitunixClient()

        exchange_response = await client.modify_tpsl_order(
            order_id=str(order_id),
            tp_price=calculated_tp,
            sl_price=calculated_sl,
            tp_stop_type=getattr(
                local_order,
                "tp_stop_type",
                None,
            ),
            sl_stop_type=getattr(
                local_order,
                "sl_stop_type",
                None,
            ),
            tp_order_type=getattr(
                local_order,
                "tp_order_type",
                None,
            ),
            sl_order_type=getattr(
                local_order,
                "sl_order_type",
                None,
            ),
            tp_order_price=getattr(
                local_order,
                "tp_order_price",
                None,
            ),
            sl_order_price=getattr(
                local_order,
                "sl_order_price",
                None,
            ),
            tp_qty=tp_quantity,
            sl_qty=sl_quantity,
        )

        confirmed = False
        confirmed_order = None

        for _ in range(5):
            await asyncio.sleep(0.4)

            pending_response = (
                await client.get_pending_tpsl_orders(
                    position_id=str(position_id),
                    skip=0,
                    limit=100,
                )
            )

            pending_orders = (
                pending_response.get("data")
                or []
            )

            confirmed_order = next(
                (
                    order
                    for order in pending_orders
                    if isinstance(order, dict)
                    and str(order.get("id") or "")
                    == str(order_id)
                ),
                None,
            )

            if (
                confirmed_order is not None
                and _exchange_prices_confirmed(
                    confirmed_order,
                    expected_tp=calculated_tp,
                    expected_sl=calculated_sl,
                )
            ):
                confirmed = True
                break

        return {
            "success": True,
            "accepted": True,
            "confirmed": confirmed,
            "position_id": str(position_id),
            "order_id": str(order_id),
            "symbol": symbol,
            "direction": direction,
            "entry_price": entry_price,
            "tp_percent": request.tp_percent,
            "sl_percent": request.sl_percent,
            "tp_price": calculated_tp,
            "sl_price": calculated_sl,
            "exchange_response": exchange_response,
            "message": (
                "TP-/SL-Auftrag wurde bei BitUnix bestätigt."
                if confirmed
                else (
                    "BitUnix hat die Änderung angenommen; "
                    "die Lese-Bestätigung steht noch aus."
                )
            ),
        }

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                "Der bestehende TP-/SL-Auftrag konnte "
                "bei BitUnix nicht geändert werden."
            ),
        ) from exc

# ==================================================
# PROJECT ATLAS M5.5A MULTI LEVEL PERCENT EDITOR END
# ==================================================


@router.post("/{position_id}/tpsl")
async def update_single_trade_tpsl(
    position_id: str,
    request: TradeTpSlUpdateRequest,
):
    """
    Aktualisiert Take Profit und Stop Loss einer
    entsperrten offenen Position direkt bei BitUnix.

    Die laufende Exchange-Synchronisation übernimmt danach
    die aktuellen Werte wieder in das Dashboard.
    """

    trade = require_unlocked_trade(position_id)

    symbol = getattr(trade, "symbol", None)

    if not symbol:
        raise HTTPException(
            status_code=422,
            detail="Der Trade besitzt kein gültiges Symbol.",
        )

    direction = str(
        getattr(trade, "direction", "")
    ).upper()

    entry_price = float(
        getattr(trade, "entry_price", 0) or 0
    )

    if entry_price <= 0:
        raise HTTPException(
            status_code=422,
            detail="Der Trade besitzt keinen gültigen Entry-Preis.",
        )

    if direction == "LONG":
        if request.tp_price <= entry_price:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Bei LONG muss Take Profit "
                    "über dem Entry-Preis liegen."
                ),
            )

        if request.sl_price >= entry_price:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Bei LONG muss Stop Loss "
                    "unter dem Entry-Preis liegen."
                ),
            )

    elif direction == "SHORT":
        if request.tp_price >= entry_price:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Bei SHORT muss Take Profit "
                    "unter dem Entry-Preis liegen."
                ),
            )

        if request.sl_price <= entry_price:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Bei SHORT muss Stop Loss "
                    "über dem Entry-Preis liegen."
                ),
            )

    else:
        raise HTTPException(
            status_code=422,
            detail="Ungültige Handelsrichtung.",
        )

    try:
        client = BitunixClient()

        exchange_response = (
            await client.place_position_tpsl(
                symbol=str(symbol),
                position_id=str(position_id),
                tp_price=float(request.tp_price),
                sl_price=float(request.sl_price),
            )
        )

        accepted = (
            str(exchange_response.get("code")) == "0"
        )

        if not accepted:
            raise HTTPException(
                status_code=502,
                detail=(
                    "BitUnix hat die TP/SL-Änderung "
                    "nicht bestätigt."
                ),
            )

        return {
            "success": True,
            "accepted": True,
            "position_id": str(position_id),
            "symbol": str(symbol),
            "tp_price": float(request.tp_price),
            "sl_price": float(request.sl_price),
            "exchange_response": exchange_response,
            "message": (
                "Take Profit und Stop Loss wurden "
                "von BitUnix angenommen."
            ),
        }

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Take Profit und Stop Loss konnten "
                "nicht aktualisiert werden."
            ),
        ) from exc


# ==================================================
# PROJECT ATLAS M5.3A ACTION BACKEND END
# ==================================================

