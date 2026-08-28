import os
import secrets
import csv
import io
from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status, UploadFile, File
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from app.database.signal_repository import (
    create_signal,
    delete_signal,
    toggle_signal_enabled,
    bulk_update_signals,
    update_signal,
)
from app.database.database import SessionLocal
from app.database.models import Signal
from app.database.trade_repository import (
    get_all_open_trades,
    get_trade_history,
)
from app.database.signal_repository import (
    create_signal,
    toggle_signal_enabled,
    update_signal,
)


router = APIRouter()

security = HTTPBasic()

templates = Jinja2Templates(
    directory="app/templates"
)


def verify_admin(
    credentials: HTTPBasicCredentials = Depends(security),
):
    admin_username = os.getenv("ADMIN_USERNAME", "")
    admin_password = os.getenv("ADMIN_PASSWORD", "")

    username_correct = secrets.compare_digest(
        credentials.username.encode("utf8"),
        admin_username.encode("utf8"),
    )

    password_correct = secrets.compare_digest(
        credentials.password.encode("utf8"),
        admin_password.encode("utf8"),
    )

    if not (username_correct and password_correct):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ungültige Zugangsdaten",
            headers={
                "WWW-Authenticate": "Basic",
            },
        )

    return credentials.username


SIGNAL_SORTABLE_COLUMNS = {
    "signal_id": Signal.signal_id,
    "symbol": Signal.symbol,
    "direction": Signal.direction,
    "timeframe": Signal.timeframe,
    "margin_usdt": Signal.margin_usdt,
    "leverage": Signal.leverage,
    "take_profit_percent": Signal.take_profit_percent,
    "stop_loss_percent": Signal.stop_loss_percent,
    "enabled": Signal.enabled,
}


@router.get("/admin")
async def admin_dashboard(
    request: Request,
    direction: str = "",
    timeframe: str = "",
    symbol: str = "",
    signal_search: str = "",
    status: str = "",
    sort_by: str = "",
    sort_dir: str = "asc",
    page: int = 1,
    admin_user: str = Depends(verify_admin),
):
    db = SessionLocal()

    try:
        direction = direction.strip().upper()
        timeframe = timeframe.strip()
        symbol = symbol.strip().upper()
        signal_search = signal_search.strip()
        status = status.strip().lower()
        sort_by = sort_by.strip()
        sort_dir = (
            "desc" if sort_dir.strip().lower() == "desc"
            else "asc"
        )

        query = db.query(Signal)

        if direction in {"LONG", "SHORT"}:
            query = query.filter(
                Signal.direction == direction
            )

        if timeframe:
            query = query.filter(
                Signal.timeframe == timeframe
            )

        if symbol:
            query = query.filter(
                Signal.symbol == symbol
            )

        if signal_search:
            pattern = f"%{signal_search}%"

            query = query.filter(
                Signal.display_name.ilike(pattern)
                | Signal.signal_id.ilike(pattern)
            )

        if status == "active":
            query = query.filter(
                Signal.enabled.is_(True)
            )
        elif status == "paused":
            query = query.filter(
                Signal.enabled.is_(False)
            )

        signals_per_page = 10
        total_signals = query.count()

        total_pages = max(
            1,
            (
                total_signals
                + signals_per_page
                - 1
            )
            // signals_per_page,
        )

        page = max(
            1,
            min(page, total_pages),
        )

        order_column = SIGNAL_SORTABLE_COLUMNS.get(
            sort_by,
            Signal.id,
        )

        order_clause = (
            order_column.desc()
            if sort_dir == "desc"
            else order_column.asc()
        )

        signals = (
            query
            .order_by(order_clause, Signal.id.asc())
            .offset(
                (page - 1)
                * signals_per_page
            )
            .limit(signals_per_page)
            .all()
        )

        signal_symbols = [
            "BTCUSDT",
            "ETHUSDT",
            "SOLUSDT",
            "XRPUSDT",
            "BNBUSDT",
            "DOGEUSDT",
            "ADAUSDT",
            "LINKUSDT",
            "AVAXUSDT",
            "SUIUSDT",
            "SEIUSDT",
            "ONDOUSDT",
            "AAVEUSDT",
            "ALGOUSDT",
            "HYPEUSDT",
            "NEARUSDT",
            "TRXUSDT",
            "BCHUSDT",
            "ZECUSDT",
            "LTCUSDT",
            "UNIUSDT",
            "NVDAUSDT",
            "METAUSDT",
            "TSLAUSDT",
            "AMZNUSDT",
            "GOOGLUSDT",
            "AMDUSDT",
            "SAMSUNGUSDT",
            "INTCUSDT",
            "MSFTUSDT",
            "APPLUSDT",
            "PYPLUSDT",
        ]

        signal_timeframes = [
            row[0]
            for row in (
                db.query(Signal.timeframe)
                .distinct()
                .order_by(Signal.timeframe.asc())
                .all()
            )
            if row[0]
        ]

        signal_kpi_total = (
            db.query(Signal).count()
        )

        signal_kpi_active = (
            db.query(Signal)
            .filter(Signal.enabled.is_(True))
            .count()
        )

        signal_kpi_long = (
            db.query(Signal)
            .filter(Signal.direction == "LONG")
            .count()
        )

        signal_kpi_short = (
            db.query(Signal)
            .filter(Signal.direction == "SHORT")
            .count()
        )

        open_trades = get_all_open_trades()
        trade_history = get_trade_history(limit=0)

        return templates.TemplateResponse(
            request=request,
            name="admin_signals.html",
            context={
                "signals": signals,
                "open_trades": open_trades,
                "trade_history": trade_history,
                "admin_user": admin_user,
                "signal_symbols": signal_symbols,
                "signal_timeframes": signal_timeframes,
                "filter_direction": direction,
                "filter_timeframe": timeframe,
                "filter_symbol": symbol,
                "signal_search": signal_search,
                "filter_status": status,
                "sort_by": sort_by,
                "sort_dir": sort_dir,
                "signal_page": page,
                "signal_total_pages": total_pages,
                "signal_total_count": total_signals,
                "signal_kpi_total": signal_kpi_total,
                "signal_kpi_active": signal_kpi_active,
                "signal_kpi_long": signal_kpi_long,
                "signal_kpi_short": signal_kpi_short,
            },
        )

    finally:
        db.close()




@router.post("/admin/signals/bulk")
async def bulk_update_signals_route(
    selection_mode: str = Form("selected"),
    signal_ids: list[str] = Form(default=[]),
    direction: str = Form(""),
    timeframe: str = Form(""),
    symbol: str = Form(""),
    signal_search: str = Form(""),
    margin_usdt: str = Form(""),
    leverage: str = Form(""),
    take_profit_percent: str = Form(""),
    stop_loss_percent: str = Form(""),
    take_profit_2_mode: str = Form("UNCHANGED"),
    take_profit_2_percent: str = Form(""),
    break_even_mode: str = Form(""),
    bulk_action: str = Form(""),
    admin_user: str = Depends(verify_admin),
):
    direction = direction.strip().upper()
    timeframe = timeframe.strip()
    symbol = symbol.strip().upper()
    signal_search = signal_search.strip()


    if bulk_action in {
        "pause_selected",
        "pause_long",
        "pause_short",
        "pause_all",
        "resume_selected",
        "resume_long",
        "resume_short",
        "resume_all",
    }:

        db = SessionLocal()

        try:
            query = db.query(Signal)

            if selection_mode == "selected":
                query = query.filter(
                    Signal.signal_id.in_(signal_ids)
                )

            elif selection_mode == "filtered":

                if direction in {"LONG", "SHORT"}:
                    query = query.filter(
                        Signal.direction == direction
                    )

                if timeframe:
                    query = query.filter(
                        Signal.timeframe == timeframe
                    )

                if symbol:
                    query = query.filter(
                        Signal.symbol == symbol
                    )

                if signal_search:
                    pattern = f"%{signal_search}%"
                    query = query.filter(
                        Signal.display_name.ilike(pattern)
                        | Signal.signal_id.ilike(pattern)
                    )

            signals = query.all()

            for signal in signals:

                if bulk_action in {
                    "pause_selected",
                    "pause_all",
                }:
                    signal.enabled = False

                elif bulk_action == "pause_long":
                    if signal.direction == "LONG":
                        signal.enabled = False

                elif bulk_action == "pause_short":
                    if signal.direction == "SHORT":
                        signal.enabled = False

                elif bulk_action in {
                    "resume_selected",
                    "resume_all",
                }:
                    signal.enabled = True

                elif bulk_action == "resume_long":
                    if signal.direction == "LONG":
                        signal.enabled = True

                elif bulk_action == "resume_short":
                    if signal.direction == "SHORT":
                        signal.enabled = True

            db.commit()

        finally:
            db.close()

        return RedirectResponse(
            url="/admin#atlas-signals",
            status_code=303,
        )


    def optional_float(value: str):
        value = value.strip().replace(",", ".")

        if not value:
            return None

        return float(value)

    def optional_int(value: str):
        value = value.strip()

        if not value:
            return None

        return int(value)

    try:
        new_margin = optional_float(margin_usdt)
        new_leverage = optional_int(leverage)
        new_tp = optional_float(take_profit_percent)
        new_sl = optional_float(stop_loss_percent)
        new_tp2 = optional_float(take_profit_2_percent)

    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Ungültiger Zahlenwert in der Bulk-Änderung",
        )

    normalized_tp2_mode = str(
        take_profit_2_mode or "UNCHANGED"
    ).strip().upper()

    normalized_break_even = str(
        break_even_mode or ""
    ).strip().upper()

    if normalized_tp2_mode not in {
        "UNCHANGED",
        "ENABLE",
        "DISABLE",
    }:
        raise HTTPException(
            status_code=400,
            detail="Ungültiger TP2-Modus",
        )

    if normalized_break_even not in {
        "",
        "OFF",
        "TP1",
        "TP2",
    }:
        raise HTTPException(
            status_code=400,
            detail="Ungültiger Break-even-Modus",
        )

    if new_tp2 is not None and new_tp2 <= 0:
        raise HTTPException(
            status_code=400,
            detail="TP2 muss größer als 0 sein",
        )

    if (
        normalized_tp2_mode == "ENABLE"
        and new_tp2 is None
    ):
        raise HTTPException(
            status_code=400,
            detail="Zum Aktivieren von TP2 muss TP2 % angegeben werden",
        )

    if (
        new_margin is None
        and new_leverage is None
        and new_tp is None
        and new_sl is None
        and normalized_tp2_mode == "UNCHANGED"
        and not normalized_break_even
    ):
        raise HTTPException(
            status_code=400,
            detail="Mindestens ein Änderungsfeld muss ausgefüllt sein",
        )

    if new_margin is not None and new_margin <= 0:
        raise HTTPException(
            status_code=400,
            detail="Margin muss größer als 0 sein",
        )

    if new_leverage is not None and new_leverage < 1:
        raise HTTPException(
            status_code=400,
            detail="Hebel muss mindestens 1 sein",
        )

    if new_tp is not None and new_tp < 0:
        raise HTTPException(
            status_code=400,
            detail="Take Profit darf nicht negativ sein",
        )

    if new_sl is not None and new_sl < 0:
        raise HTTPException(
            status_code=400,
            detail="Stop Loss darf nicht negativ sein",
        )

    target_ids = list(dict.fromkeys(signal_ids))

    if selection_mode == "filtered":
        db = SessionLocal()

        try:
            query = db.query(Signal)

            if direction in {"LONG", "SHORT"}:
                query = query.filter(
                    Signal.direction == direction
                )

            if timeframe:
                query = query.filter(
                    Signal.timeframe == timeframe
                )

            if symbol:
                query = query.filter(
                    Signal.symbol == symbol
                )

            if signal_search:
                pattern = f"%{signal_search}%"

                query = query.filter(
                    Signal.display_name.ilike(pattern)
                    | Signal.signal_id.ilike(pattern)
                )

            target_ids = [
                row[0]
                for row in (
                    query
                    .with_entities(Signal.signal_id)
                    .order_by(Signal.id.asc())
                    .all()
                )
            ]

        finally:
            db.close()

    if not target_ids:
        raise HTTPException(
            status_code=400,
            detail="Keine Signale ausgewählt",
        )

    if bulk_action in {"pause_long", "pause_short", "pause_all"}:
        db = SessionLocal()

        try:
            signals = (
                db.query(Signal)
                .filter(Signal.signal_id.in_(target_ids))
                .all()
            )

            for signal in signals:
                if bulk_action == "pause_all":
                    signal.enabled = False

                elif bulk_action == "pause_long" and signal.direction == "LONG":
                    signal.enabled = False

                elif bulk_action == "pause_short" and signal.direction == "SHORT":
                    signal.enabled = False

            db.commit()

        finally:
            db.close()

        return RedirectResponse(
            url="/admin#atlas-signals",
            status_code=303,
        )

    bulk_update_signals(
        signal_ids=target_ids,
        margin_usdt=new_margin,
        leverage=new_leverage,
        take_profit_percent=new_tp,
        stop_loss_percent=new_sl,
        take_profit_2_mode=normalized_tp2_mode,
        take_profit_2_percent=new_tp2,
        break_even_mode=(
            normalized_break_even
            if normalized_break_even
            else None
        ),
    )

    return RedirectResponse(
        url="/admin#atlas-signals",
        status_code=303,
    )


@router.post("/admin/signals/{signal_id}/toggle")
async def toggle_signal(
    signal_id: str,
    admin_user: str = Depends(verify_admin),
):
    new_status = toggle_signal_enabled(signal_id)

    if new_status is None:
        raise HTTPException(
            status_code=404,
            detail="Signal nicht gefunden",
        )

    return RedirectResponse(
        url="/admin",
        status_code=303,
    )


@router.get("/admin/signals/new")
async def new_signal_form(
    request: Request,
    admin_user: str = Depends(verify_admin),
):
    return templates.TemplateResponse(
        request=request,
        name="admin_signal_form.html",
        context={
            "mode": "create",
            "signal": None,
        },
    )


@router.post("/admin/signals/new")
async def create_signal_route(
    signal_id: str = Form(...),
    display_name: str = Form(...),
    symbol: str = Form(...),
    direction: str = Form(...),
    timeframe: str = Form(...),
    margin_usdt: float = Form(...),
    leverage: int = Form(...),
    take_profit_percent: float = Form(...),
    take_profit_2_percent: float | None = Form(None),
    break_even_trigger: str = Form("OFF"),
    stop_loss_percent: float = Form(...),
    margin_mode: str = Form("CROSS"),
    order_type: str = Form("MARKET"),
    enabled: bool = Form(False),
    admin_user: str = Depends(verify_admin),
):
    signal_id = signal_id.strip().upper()
    symbol = symbol.strip().upper()
    direction = direction.strip().upper()
    timeframe = timeframe.strip()

    if (
        take_profit_2_percent is not None
        and take_profit_2_percent <= 0
    ):
        take_profit_2_percent = None

    break_even_trigger = (
        break_even_trigger.strip().upper()
    )

    if break_even_trigger not in {
        "OFF",
        "TP1",
        "TP2",
    }:
        raise HTTPException(
            status_code=400,
            detail="Ungültiger Break-even-Modus",
        )

    if take_profit_2_percent is None:
        break_even_trigger = "OFF"

    elif (
        take_profit_2_percent
        <= take_profit_percent
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "TP2 muss größer als TP1 sein."
            ),
        )

    valid_timeframes = {
        "1m",
        "3m",
        "5m",
        "15m",
        "30m",
        "1h",
        "2h",
        "4h",
        "12h",
        "1D",
    }

    if timeframe not in valid_timeframes:
        raise HTTPException(
            status_code=400,
            detail="Ungültiger Zeitrahmen",
        )

    if direction not in {"LONG", "SHORT"}:
        raise HTTPException(
            status_code=400,
            detail="Richtung muss LONG oder SHORT sein",
        )

    created = create_signal(
        signal_id=signal_id,
        display_name=display_name.strip(),
        symbol=symbol,
        direction=direction,
        timeframe=timeframe,
        margin_usdt=margin_usdt,
        leverage=leverage,
        take_profit_percent=take_profit_percent,
        take_profit_2_percent=take_profit_2_percent,
        break_even_mode=break_even_trigger,
        stop_loss_percent=stop_loss_percent,
        margin_mode=margin_mode.strip().upper(),
        order_type=order_type.strip().upper(),
        enabled=enabled,
    )

    if created is None:
        raise HTTPException(
            status_code=409,
            detail="Diese Signal-ID existiert bereits",
        )

    return RedirectResponse(
        url="/admin#atlas-signals",
        status_code=303,
    )


@router.get("/admin/signals/{signal_id}/edit")
async def edit_signal_form(
    signal_id: str,
    request: Request,
    admin_user: str = Depends(verify_admin),
):
    db = SessionLocal()

    try:
        signal = (
            db.query(Signal)
            .filter(Signal.signal_id == signal_id)
            .first()
        )

        if signal is None:
            raise HTTPException(
                status_code=404,
                detail="Signal nicht gefunden",
            )

        return templates.TemplateResponse(
            request=request,
            name="admin_signal_form.html",
            context={
                "mode": "edit",
                "signal": signal,
            },
        )

    finally:
        db.close()


@router.post("/admin/signals/{signal_id}/edit")
async def update_signal_route(
    signal_id: str,
    display_name: str = Form(...),
    symbol: str = Form(...),
    direction: str = Form(...),
    timeframe: str = Form(...),
    margin_usdt: float = Form(...),
    leverage: int = Form(...),
    take_profit_percent: float = Form(...),
    take_profit_2_percent: float | None = Form(None),
    break_even_trigger: str = Form("OFF"),
    stop_loss_percent: float = Form(...),
    margin_mode: str = Form("CROSS"),
    order_type: str = Form("MARKET"),
    enabled: bool = Form(False),
    admin_user: str = Depends(verify_admin),
):
    direction = direction.strip().upper()
    timeframe = timeframe.strip()

    if (
        take_profit_2_percent is not None
        and take_profit_2_percent <= 0
    ):
        take_profit_2_percent = None

    break_even_trigger = (
        break_even_trigger.strip().upper()
    )

    if break_even_trigger not in {
        "OFF",
        "TP1",
        "TP2",
    }:
        raise HTTPException(
            status_code=400,
            detail="Ungültiger Break-even-Modus",
        )

    if take_profit_2_percent is None:
        break_even_trigger = "OFF"

    elif (
        take_profit_2_percent
        <= take_profit_percent
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "TP2 muss größer als TP1 sein."
            ),
        )

    valid_timeframes = {
        "1m",
        "3m",
        "5m",
        "15m",
        "30m",
        "1h",
        "2h",
        "4h",
        "12h",
        "1D",
        "UNKNOWN",
    }

    if timeframe not in valid_timeframes:
        raise HTTPException(
            status_code=400,
            detail="Ungültiger Zeitrahmen",
        )

    if direction not in {"LONG", "SHORT"}:
        raise HTTPException(
            status_code=400,
            detail="Richtung muss LONG oder SHORT sein",
        )

    updated = update_signal(
        signal_id=signal_id,
        display_name=display_name.strip(),
        symbol=symbol.strip().upper(),
        direction=direction,
        timeframe=timeframe,
        margin_usdt=margin_usdt,
        leverage=leverage,
        take_profit_percent=take_profit_percent,
        take_profit_2_percent=take_profit_2_percent,
        break_even_mode=break_even_trigger,
        stop_loss_percent=stop_loss_percent,
        margin_mode=margin_mode.strip().upper(),
        order_type=order_type.strip().upper(),
        enabled=enabled,
    )

    if updated is None:
        raise HTTPException(
            status_code=404,
            detail="Signal nicht gefunden",
        )

    return RedirectResponse(
        url="/admin#atlas-signals",
        status_code=303,
    )

@router.post("/admin/signals/{signal_id}/delete")
async def delete_signal_route(
    signal_id: str,
    admin_user: str = Depends(verify_admin),
):
    deleted = delete_signal(signal_id)

    if not deleted:
        raise HTTPException(
            status_code=404,
            detail="Signal nicht gefunden",
        )

    return RedirectResponse(
        url="/admin",
        status_code=303,
    )


CSV_COLUMNS = [
    "signal_id",
    "display_name",
    "symbol",
    "direction",
    "timeframe",
    "margin_usdt",
    "leverage",
    "take_profit_percent",
    "take_profit_2_percent",
    "break_even_mode",
    "stop_loss_percent",
    "margin_mode",
    "order_type",
    "enabled",
]


@router.get("/admin/signals/export")
async def export_signals_csv(
    admin_user: str = Depends(verify_admin),
):
    db = SessionLocal()
    try:
        signals = (
            db.query(Signal)
            .order_by(Signal.signal_id.asc())
            .all()
        )
    finally:
        db.close()

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS)
    writer.writeheader()

    for s in signals:
        writer.writerow({
            "signal_id": s.signal_id,
            "display_name": s.display_name,
            "symbol": s.symbol,
            "direction": s.direction,
            "timeframe": s.timeframe,
            "margin_usdt": s.margin_usdt,
            "leverage": s.leverage,
            "take_profit_percent": s.take_profit_percent,
            "take_profit_2_percent": (
                "" if s.take_profit_2_percent is None
                else s.take_profit_2_percent
            ),
            "break_even_mode": s.break_even_mode,
            "stop_loss_percent": s.stop_loss_percent,
            "margin_mode": s.margin_mode,
            "order_type": s.order_type,
            "enabled": s.enabled,
        })

    buffer.seek(0)
    filename = f"project_atlas_signale_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"'
        },
    )


@router.post("/admin/signals/import")
async def import_signals_csv(
    file: UploadFile = File(...),
    admin_user: str = Depends(verify_admin),
):
    raw = await file.read()

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=400,
            detail="Datei konnte nicht als UTF-8 CSV gelesen werden.",
        )

    reader = csv.DictReader(io.StringIO(text))

    missing_cols = set(CSV_COLUMNS) - set(reader.fieldnames or [])
    if missing_cols:
        raise HTTPException(
            status_code=400,
            detail=f"CSV fehlen Spalten: {', '.join(sorted(missing_cols))}",
        )

    created_count = 0
    updated_count = 0
    error_rows = []

    for i, row in enumerate(reader, start=2):
        try:
            signal_id = row["signal_id"].strip().upper()
            if not signal_id:
                continue

            tp2_raw = (row.get("take_profit_2_percent") or "").strip()
            take_profit_2_percent = float(tp2_raw) if tp2_raw else None

            enabled_raw = str(row.get("enabled", "")).strip().lower()
            enabled = enabled_raw in ("true", "1", "yes", "ja")

            payload = dict(
                signal_id=signal_id,
                display_name=row["display_name"].strip(),
                symbol=row["symbol"].strip().upper(),
                direction=row["direction"].strip().upper(),
                timeframe=row["timeframe"].strip(),
                margin_usdt=float(row["margin_usdt"]),
                leverage=int(float(row["leverage"])),
                take_profit_percent=float(row["take_profit_percent"]),
                take_profit_2_percent=take_profit_2_percent,
                break_even_mode=(row.get("break_even_mode") or "OFF").strip().upper(),
                stop_loss_percent=float(row["stop_loss_percent"]),
                margin_mode=(row.get("margin_mode") or "CROSS").strip().upper(),
                order_type=(row.get("order_type") or "MARKET").strip().upper(),
                enabled=enabled,
            )

            created = create_signal(**payload)

            if created is not None:
                created_count += 1
            else:
                updated = update_signal(**payload)
                if updated is not None:
                    updated_count += 1
                else:
                    error_rows.append(f"Zeile {i}: Update fehlgeschlagen ({signal_id})")

        except Exception as e:
            error_rows.append(f"Zeile {i}: {e}")

    summary = f"{created_count} neu, {updated_count} aktualisiert"
    if error_rows:
        summary += f", {len(error_rows)} Fehler"

    return RedirectResponse(
        url=f"/admin#atlas-signals",
        status_code=303,
    )
