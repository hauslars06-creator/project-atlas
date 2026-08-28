# ==========================================================
# Project Atlas
# File: app/database/trade_repository.py
# Sprint: 1.1A
# Version: 1.0.0-dev
# Last Update: 2026-07-24
# ==========================================================

from datetime import datetime, timezone

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from app.database.database import SessionLocal
from app.database.models import OpenTrade, TradeHistory


def create_open_trade(
    *,
    position_id: str,
    signal_id: str,
    signal_name: str,
    symbol: str,
    timeframe: str,
    direction: str,
    entry_price: float,
    tp_price: float,
    sl_price: float,
    margin_usdt: float,
    leverage: int,
    quantity: float,
    client_id: str | None = None,
    order_id: str | None = None,
    tp1_order_id: str | None = None,
    tp2_order_id: str | None = None,
    sl_order_id: str | None = None,
    tp2_price: float | None = None,
    tp1_quantity: float | None = None,
    tp2_quantity: float | None = None,
    runner_quantity: float | None = None,
    break_even_mode: str = "OFF",
    trade_source: str = "ATLAS",
    is_locked: bool = False,
    opened_at: datetime | None = None,
) -> OpenTrade | None:
    """
    Speichert eine neu eröffnete Bitunix-Position.

    Gibt None zurück, wenn position_id oder client_id bereits
    gespeichert wurde.
    """

    db = SessionLocal()

    try:
        trade = OpenTrade(
            position_id=str(position_id),
            signal_id=signal_id.strip().upper(),
            signal_name=signal_name.strip(),
            symbol=symbol.strip().upper(),
            timeframe=timeframe.strip().upper() or "UNKNOWN",
            direction=direction.strip().upper(),
            entry_price=float(entry_price),
            tp_price=float(tp_price),
            sl_price=float(sl_price),
            margin_usdt=float(margin_usdt),
            leverage=int(leverage),
            quantity=float(quantity),
            status="OPEN",

            # PROJECT ATLAS M5.3B EXTERNAL TRADES START
            trade_source=(
                str(trade_source or "ATLAS")
                .strip()
                .upper()
            ),
            is_locked=bool(is_locked),
            opened_at=(
                opened_at
                if opened_at is not None
                else datetime.now(timezone.utc)
            ),
            # PROJECT ATLAS M5.3B EXTERNAL TRADES END

            client_id=client_id,
            order_id=order_id,
            tp1_order_id=str(tp1_order_id) if tp1_order_id not in (None, "") else None,
            tp2_order_id=str(tp2_order_id) if tp2_order_id not in (None, "") else None,
            sl_order_id=str(sl_order_id) if sl_order_id not in (None, "") else None,
            tp2_price=float(tp2_price) if tp2_price is not None else None,
            tp1_quantity=float(tp1_quantity) if tp1_quantity is not None else None,
            tp2_quantity=float(tp2_quantity) if tp2_quantity is not None else None,
            runner_quantity=float(runner_quantity) if runner_quantity is not None else None,
            break_even_mode=str(break_even_mode or "OFF").strip().upper(),
        )

        db.add(trade)
        db.commit()
        db.refresh(trade)

        return trade

    except IntegrityError:
        db.rollback()
        return None

    finally:
        db.close()


def get_open_trade(
    position_id: str,
) -> OpenTrade | None:
    db = SessionLocal()

    try:
        return (
            db.query(OpenTrade)
            .filter(
                OpenTrade.position_id == str(position_id)
            )
            .first()
        )

    finally:
        db.close()


def mark_trade_tp_processed(position_id: str, level: int, *, sl_price: float | None = None) -> OpenTrade | None:
    db = SessionLocal()
    try:
        trade = db.query(OpenTrade).filter(OpenTrade.position_id == str(position_id)).first()
        if trade is None:
            return None
        now = datetime.now(timezone.utc)
        if level == 1:
            trade.tp1_processed_at = now
        elif level == 2:
            trade.tp2_processed_at = now
        else:
            raise ValueError("TP-Level muss 1 oder 2 sein")
        if sl_price is not None:
            trade.sl_price = float(sl_price)
        trade.updated_at = now
        db.commit()
        db.refresh(trade)
        return trade
    finally:
        db.close()



def get_open_trades_by_signal(
    signal_id: str,
) -> list[OpenTrade]:
    db = SessionLocal()

    try:
        return (
            db.query(OpenTrade)
            .filter(
                OpenTrade.signal_id == signal_id.strip().upper(),
                OpenTrade.status == "OPEN",
            )
            .all()
        )

    finally:
        db.close()


def get_all_open_trades() -> list[OpenTrade]:
    db = SessionLocal()

    try:
        return (
            db.query(OpenTrade)
            .order_by(
                OpenTrade.trade_source.desc(),
                OpenTrade.is_locked.desc(),
                OpenTrade.opened_at.desc(),
            )
            .all()
        )

    finally:
        db.close()


def get_unlocked_open_trades() -> list[OpenTrade]:
    """
    Wird später für den globalen Not-Aus verwendet.
    Gesperrte Trades werden bewusst ausgeschlossen.
    """

    db = SessionLocal()

    try:
        return (
            db.query(OpenTrade)
            .filter(
                OpenTrade.is_locked.is_(False),
                OpenTrade.status == "OPEN",
            )
            .order_by(OpenTrade.opened_at.asc())
            .all()
        )

    finally:
        db.close()


def set_trade_lock(
    position_id: str,
    is_locked: bool,
) -> bool | None:
    """
    Sperrt oder entsperrt einen offenen Trade.

    Rückgabe:
    - True: gesperrt
    - False: entsperrt
    - None: Trade nicht gefunden
    """

    db = SessionLocal()

    try:
        trade = (
            db.query(OpenTrade)
            .filter(
                OpenTrade.position_id == str(position_id)
            )
            .first()
        )

        if trade is None:
            return None

        trade.is_locked = bool(is_locked)
        trade.updated_at = datetime.now(timezone.utc)

        db.commit()
        db.refresh(trade)

        return trade.is_locked

    finally:
        db.close()


def toggle_trade_lock(
    position_id: str,
) -> bool | None:
    db = SessionLocal()

    try:
        trade = (
            db.query(OpenTrade)
            .filter(
                OpenTrade.position_id == str(position_id)
            )
            .first()
        )

        if trade is None:
            return None

        trade.is_locked = not trade.is_locked
        trade.updated_at = datetime.now(timezone.utc)

        db.commit()
        db.refresh(trade)

        return trade.is_locked

    finally:
        db.close()


def update_open_trade_prices(
    position_id: str,
    *,
    tp_price: float | None = None,
    sl_price: float | None = None,
) -> OpenTrade | None:
    db = SessionLocal()

    try:
        trade = (
            db.query(OpenTrade)
            .filter(
                OpenTrade.position_id == str(position_id)
            )
            .first()
        )

        if trade is None:
            return None

        if trade.is_locked:
            return None

        if tp_price is not None:
            trade.tp_price = float(tp_price)

        if sl_price is not None:
            trade.sl_price = float(sl_price)

        trade.updated_at = datetime.now(timezone.utc)

        db.commit()
        db.refresh(trade)

        return trade

    finally:
        db.close()
def update_open_trade_live_data(
    position_id: str,
    *,
    current_price: float,
    liquidation_price: float | None,
    unrealized_pnl: float,
    realized_pnl: float,
    pnl_percent: float,
    current_margin: float,
) -> OpenTrade | None:
    db = SessionLocal()

    try:
        trade = (
            db.query(OpenTrade)
            .filter(
                OpenTrade.position_id == str(position_id)
            )
            .first()
        )

        if trade is None:
            return None

        sync_time = datetime.now(timezone.utc)

        trade.current_price = float(current_price)
        trade.liquidation_price = (
            float(liquidation_price)
            if liquidation_price is not None
            else None
        )
        trade.unrealized_pnl = float(unrealized_pnl)
        trade.realized_pnl = float(realized_pnl)
        trade.pnl_percent = float(pnl_percent)
        if current_margin is not None:
            trade.current_margin = float(current_margin)
        trade.last_exchange_sync = sync_time
        trade.updated_at = sync_time

        db.commit()
        db.refresh(trade)

        return trade

    finally:
        db.close()

def move_open_trade_to_history(
    position_id: str,
    *,
    exit_price: float | None = None,
    pnl_usdt: float | None = None,
    pnl_percent: float | None = None,
    close_reason: str = "UNKNOWN",
) -> TradeHistory | None:
    """
    Verschiebt einen bestätigten geschlossenen Trade atomar
    von open_trades nach trade_history.
    """

    db = SessionLocal()

    try:
        trade = (
            db.query(OpenTrade)
            .filter(
                OpenTrade.position_id == str(position_id)
            )
            .first()
        )

        if trade is None:
            return None

        # M3.1A: Abschlusswerte aus letztem BitUnix-Snapshot
        #
        # Wenn der Aufrufer keine exakten Abschlusswerte liefert,
        # verwenden wir den zuletzt synchronisierten Positionsstand.
        # Dadurch werden keine leeren Exit-, PnL- oder ROI-Werte mehr
        # in die Trade-Historie geschrieben.
        resolved_exit_price = (
            float(exit_price)
            if exit_price is not None
            else (
                float(trade.current_price)
                if trade.current_price is not None
                else float(trade.entry_price)
            )
        )

        if pnl_usdt is not None:
            resolved_pnl_usdt = float(pnl_usdt)
        else:
            realized_component = (
                float(trade.realized_pnl)
                if trade.realized_pnl is not None
                else 0.0
            )

            unrealized_component = (
                float(trade.unrealized_pnl)
                if trade.unrealized_pnl is not None
                else 0.0
            )

            resolved_pnl_usdt = (
                realized_component
                + unrealized_component
            )

        if pnl_percent is not None:
            resolved_pnl_percent = float(pnl_percent)
        else:
            margin_basis = (
                float(trade.current_margin)
                if (
                    trade.current_margin is not None
                    and float(trade.current_margin) > 0
                )
                else float(trade.margin_usdt)
            )

            resolved_pnl_percent = (
                resolved_pnl_usdt / margin_basis * 100
                if margin_basis > 0
                else 0.0
            )

        history_entry = TradeHistory(
            position_id=trade.position_id,
            signal_id=trade.signal_id,
            signal_name=trade.signal_name,
            symbol=trade.symbol,
            timeframe=trade.timeframe,
            direction=trade.direction,
            is_locked=trade.is_locked,
            entry_price=trade.entry_price,
            exit_price=resolved_exit_price,
            tp_price=trade.tp_price,
            sl_price=trade.sl_price,
            margin_usdt=trade.margin_usdt,
            leverage=trade.leverage,
            quantity=trade.quantity,
            pnl_usdt=resolved_pnl_usdt,
            pnl_percent=resolved_pnl_percent,
            close_reason=(
                close_reason.strip().upper()
                or "UNKNOWN"
            ),
            client_id=trade.client_id,
            order_id=trade.order_id,
            opened_at=trade.opened_at,
            closed_at=datetime.now(timezone.utc),
        )

        db.add(history_entry)
        db.delete(trade)
        db.commit()
        db.refresh(history_entry)

        return history_entry

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


def delete_open_trade(
    position_id: str,
) -> bool:
    """
    Nur für technische Korrekturen verwenden.

    Normale geschlossene Trades müssen über
    move_open_trade_to_history() verarbeitet werden.
    """

    db = SessionLocal()

    try:
        trade = (
            db.query(OpenTrade)
            .filter(
                OpenTrade.position_id == str(position_id)
            )
            .first()
        )

        if trade is None:
            return False

        db.delete(trade)
        db.commit()

        return True

    finally:
        db.close()


def get_trade_history(
    limit: int = 100,
) -> list[TradeHistory]:
    safe_limit = max(1, min(int(limit), 1000))

    db = SessionLocal()

    try:
        return (
            db.query(TradeHistory)
            .order_by(TradeHistory.closed_at.desc())
            .limit(safe_limit)
            .all()
        )

    finally:
        db.close()


# ==================================================
# PROJECT ATLAS HISTORY V2 START
# ==================================================

def query_trade_history(
    *,
    search: str = "",
    direction: str = "",
    symbol: str = "",
    timeframe: str = "",
    close_reason: str = "",
    result: str = "",
    lock_scope: str = "",
    days: int | None = None,
    sort_by: str = "closed_at",
    sort_dir: str = "desc",
    page: int = 1,
    per_page: int = 25,
) -> dict:
    """
    Zentrale History-Abfrage für Dashboard und spätere
    Performance-Auswertungen.

    Filter, Sortierung, Pagination und KPI-Berechnung
    erfolgen serverseitig.
    """

    from datetime import datetime, timedelta, timezone
    from sqlalchemy import or_, func, case

    db = SessionLocal()

    try:
        query = db.query(TradeHistory)

        search = str(search or "").strip()
        direction = str(direction or "").strip().upper()
        symbol = str(symbol or "").strip().upper()
        timeframe = str(timeframe or "").strip()
        close_reason = (
            str(close_reason or "")
            .strip()
            .upper()
        )
        result = (
            str(result or "")
            .strip()
            .lower()
        )

        lock_scope = (
            str(lock_scope or "")
            .strip()
            .upper()
        )

        sort_by = str(sort_by or "closed_at").strip()
        sort_dir = str(sort_dir or "desc").strip().lower()

        if search:
            pattern = f"%{search}%"

            query = query.filter(
                or_(
                    TradeHistory.signal_name.ilike(pattern),
                    TradeHistory.signal_id.ilike(pattern),
                    TradeHistory.symbol.ilike(pattern),
                    TradeHistory.timeframe.ilike(pattern),
                    TradeHistory.direction.ilike(pattern),
                    TradeHistory.close_reason.ilike(pattern),
                    TradeHistory.position_id.ilike(pattern),
                )
            )

        if direction in {"LONG", "SHORT"}:
            query = query.filter(
                TradeHistory.direction == direction
            )

        if symbol:
            query = query.filter(
                TradeHistory.symbol == symbol
            )

        if timeframe:
            query = query.filter(
                TradeHistory.timeframe == timeframe
            )

        if close_reason:
            query = query.filter(
                TradeHistory.close_reason
                == close_reason
            )

        if days is not None and int(days) > 0:
            cutoff = (
                datetime.now(timezone.utc)
                - timedelta(days=int(days))
            )

            query = query.filter(
                TradeHistory.closed_at >= cutoff
            )

        pnl_value = func.coalesce(
            TradeHistory.pnl_usdt,
            0.0,
        )

        roi_value = func.coalesce(
            TradeHistory.pnl_percent,
            0.0,
        )

        def _compute_stats_for(stat_query) -> dict:
            row = stat_query.with_entities(
                func.count(TradeHistory.id),
                func.coalesce(func.sum(pnl_value), 0.0),
                func.coalesce(func.avg(roi_value), 0.0),
                func.sum(
                    case(
                        (pnl_value > 0, 1),
                        else_=0,
                    )
                ),
                func.sum(
                    case(
                        (pnl_value < 0, 1),
                        else_=0,
                    )
                ),
            ).first()

            row_trade_count = int(row[0] or 0)
            row_total_pnl = float(row[1] or 0.0)
            row_avg_roi = float(row[2] or 0.0)
            row_winners = int(row[3] or 0)
            row_losers = int(row[4] or 0)
            row_evaluated = row_winners + row_losers

            return {
                "trade_count": row_trade_count,
                "total_pnl": row_total_pnl,
                "avg_roi": row_avg_roi,
                "win_rate": (
                    row_winners / row_evaluated * 100.0
                    if row_evaluated > 0
                    else 0.0
                ),
                "winners": row_winners,
                "losers": row_losers,
            }

        # Manuelle/externe BitUnix-Trades zaehlen statistisch
        # IMMER als "Gesperrt", unabhaengig vom is_locked-
        # Schalter (der nur steuert, ob der Bot den Trade
        # automatisch verwalten darf - eine andere Sache als
        # die statistische Kategorie "manuell vs. Signal").
        is_manual_trade = (
            TradeHistory.signal_id == "EXTERNAL"
        )

        # Kennzahlen fuer alle drei Status-Gruppen gleich-
        # zeitig berechnen (unabhaengig vom aktuell gewaehlten
        # lock_scope-Filter), damit das Frontend Gesamt/Aktiv/
        # Gesperrt nebeneinander zeigen kann, ohne umschalten
        # zu muessen.
        stats_by_scope = {
            "all": _compute_stats_for(query),
            "active": _compute_stats_for(
                query.filter(
                    TradeHistory.is_locked.is_(False),
                    TradeHistory.signal_id != "EXTERNAL",
                )
            ),
            "locked": _compute_stats_for(
                query.filter(
                    or_(
                        TradeHistory.is_locked.is_(True),
                        is_manual_trade,
                    )
                )
            ),
        }

        if lock_scope == "LOCKED":
            query = query.filter(
                or_(
                    TradeHistory.is_locked.is_(True),
                    is_manual_trade,
                )
            )

        elif lock_scope == "ACTIVE":
            query = query.filter(
                TradeHistory.is_locked.is_(False),
                TradeHistory.signal_id != "EXTERNAL",
            )

        if lock_scope == "LOCKED":
            current_stats = stats_by_scope["locked"]
        elif lock_scope == "ACTIVE":
            current_stats = stats_by_scope["active"]
        else:
            current_stats = stats_by_scope["all"]

        trade_count = current_stats["trade_count"]
        total_pnl = current_stats["total_pnl"]
        avg_roi = current_stats["avg_roi"]
        win_rate = current_stats["win_rate"]
        winners = current_stats["winners"]
        losers = current_stats["losers"]

        # KPI-Schnellfilter erst NACH der
        # Statistikberechnung anwenden.
        #
        # Dadurch zeigen Gewinner / Verlierer
        # weiterhin die Gesamtverteilung der
        # übrigen aktiven History-Filter.
        if result == "winner":
            query = query.filter(
                TradeHistory.pnl_usdt > 0
            )

        elif result == "loser":
            query = query.filter(
                TradeHistory.pnl_usdt < 0
            )

        # total gehört zur tatsächlich
        # angezeigten Ergebnisliste.
        total = query.count()

        sort_columns = {
            "closed_at": TradeHistory.closed_at,
            "opened_at": TradeHistory.opened_at,
            "pnl": TradeHistory.pnl_usdt,
            "roi": TradeHistory.pnl_percent,
            "margin": TradeHistory.margin_usdt,
            "leverage": TradeHistory.leverage,
            "symbol": TradeHistory.symbol,
            "signal": TradeHistory.signal_name,
            "direction": TradeHistory.direction,
            "timeframe": TradeHistory.timeframe,
            "entry": TradeHistory.entry_price,
            "exit": TradeHistory.exit_price,
            "reason": TradeHistory.close_reason,
            "duration": (
                func.julianday(
                    TradeHistory.closed_at
                )
                - func.julianday(
                    TradeHistory.opened_at
                )
            ),
        }

        sort_column = sort_columns.get(
            sort_by,
            TradeHistory.closed_at,
        )

        if sort_dir == "asc":
            query = query.order_by(
                sort_column.asc(),
                TradeHistory.id.asc(),
            )
        else:
            query = query.order_by(
                sort_column.desc(),
                TradeHistory.id.desc(),
            )

        per_page = max(
            10,
            min(int(per_page), 100),
        )

        page = max(1, int(page))

        total_pages = max(
            1,
            (total + per_page - 1) // per_page,
        )

        page = min(page, total_pages)

        trades = (
            query
            .offset((page - 1) * per_page)
            .limit(per_page)
            .all()
        )

        symbols = [
            row[0]
            for row in (
                db.query(OpenTrade.symbol)
                .distinct()
                .order_by(OpenTrade.symbol.asc())
                .all()
            )
            if row[0]
        ]

        history_symbols = [
            row[0]
            for row in (
                db.query(TradeHistory.symbol)
                .distinct()
                .order_by(TradeHistory.symbol.asc())
                .all()
            )
            if row[0]
        ]

        symbols = sorted(
            set(symbols + history_symbols)
        )

        timeframes = [
            row[0]
            for row in (
                db.query(TradeHistory.timeframe)
                .distinct()
                .order_by(TradeHistory.timeframe.asc())
                .all()
            )
            if row[0]
        ]

        if "DAILY" not in timeframes:
            timeframes.append("DAILY")

        timeframe_order = [
            "15M",
            "30M",
            "1H",
            "2H",
            "4H",
            "DAILY",
            "MANUAL",
        ]

        timeframes = sorted(
            timeframes,
            key=lambda x:
                timeframe_order.index(x)
                if x in timeframe_order
                else 99
        )

        return {
            "trades": trades,
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
            "symbols": symbols,
            "timeframes": timeframes,
            "stats": {
                "trade_count": trade_count,
                "total_pnl": total_pnl,
                "avg_roi": avg_roi,
                "winners": winners,
                "losers": losers,
                "win_rate": win_rate,
            },
            "stats_by_scope": stats_by_scope,
        }

    finally:
        db.close()


# ==================================================
# PROJECT ATLAS HISTORY V2 END
# ==================================================



# ==================================================
# PROJECT ATLAS M5.3B EXTERNAL TRADES START
# ==================================================

def create_external_open_trade(
    *,
    position_id: str,
    symbol: str,
    direction: str,
    entry_price: float,
    tp_price: float,
    sl_price: float,
    margin_usdt: float,
    leverage: int,
    quantity: float,
    opened_at: datetime | None = None,
) -> OpenTrade | None:

    db = SessionLocal()

    try:
        existing = (
            db.query(OpenTrade)
            .filter(
                OpenTrade.position_id == str(position_id)
            )
            .first()
        )

        if existing is not None:
            return existing

    finally:
        db.close()
    """
    Importiert eine auf BitUnix vorhandene Position, die
    nicht durch Project Atlas gespeichert wurde.

    Externe Positionen sind grundsätzlich gesperrt, bis
    der Benutzer sie ausdrücklich im Dashboard entsperrt.
    """

    return create_open_trade(
        position_id=str(position_id),
        signal_id="EXTERNAL",
        signal_name="Manueller BitUnix-Trade",
        symbol=str(symbol),
        timeframe="MANUAL",
        direction=str(direction),
        entry_price=float(entry_price),
        tp_price=float(tp_price),
        sl_price=float(sl_price),
        margin_usdt=float(margin_usdt),
        leverage=int(leverage),
        quantity=float(quantity),
        client_id=None,
        order_id=None,
        trade_source="EXTERNAL",
        is_locked=True,
        opened_at=opened_at,
    )


# ==================================================
# PROJECT ATLAS M5.3B EXTERNAL TRADES END
# ==================================================


# ==================================================
# PROJECT ATLAS M5.3D2 TPSL READ SYNC V3 START
# ==================================================

def update_open_trade_tpsl_from_exchange(
    position_id: str,
    *,
    tp_price: float,
    sl_price: float,
) -> OpenTrade | None:
    """
    Speichert bei BitUnix gelesene TP-/SL-Werte
    ausschließlich in der lokalen OpenTrade-Tabelle.

    Diese Funktion löst keine Exchange-Anfrage aus.
    """

    db = SessionLocal()

    try:
        trade = (
            db.query(OpenTrade)
            .filter(
                OpenTrade.position_id
                == str(position_id)
            )
            .first()
        )

        if trade is None:
            return None

        sync_time = datetime.now(
            timezone.utc
        )

        trade.tp_price = float(
            tp_price
        )

        trade.sl_price = float(
            sl_price
        )

        trade.updated_at = sync_time

        db.commit()
        db.refresh(trade)

        return trade

    finally:
        db.close()


# ==================================================
# PROJECT ATLAS M5.3D2 TPSL READ SYNC V3 END
# ==================================================

