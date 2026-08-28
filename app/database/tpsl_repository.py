# ==========================================================
# Project Atlas – M5.4a
# Read-only Multi-TP/SL persistence
# ==========================================================

from datetime import datetime, timezone

from app.database.database import SessionLocal
from app.database.models import OpenTradeTpSlOrder


TEXT_FIELDS = (
    "symbol",
    "base_asset",
    "quote_asset",
    "tp_stop_type",
    "tp_order_type",
    "sl_stop_type",
    "sl_order_type",
)

FLOAT_FIELDS = (
    "tp_price",
    "tp_order_price",
    "tp_quantity",
    "sl_price",
    "sl_order_price",
    "sl_quantity",
)


def optional_text(value) -> str | None:
    if value in (None, ""):
        return None
    normalized = str(value).strip()
    return normalized or None


def optional_positive_float(value) -> float | None:
    try:
        if value in (None, ""):
            return None
        parsed = float(value)
    except (TypeError, ValueError):
        return None

    return parsed if parsed > 0 else None


def normalize_order(
    order: dict,
    active_position_ids: set[str],
) -> dict | None:
    if not isinstance(order, dict):
        return None

    exchange_order_id = str(order.get("id") or "").strip()
    position_id = str(order.get("positionId") or "").strip()

    if (
        not exchange_order_id
        or not position_id
        or position_id not in active_position_ids
    ):
        return None

    tp_price = optional_positive_float(order.get("tpPrice"))
    sl_price = optional_positive_float(order.get("slPrice"))

    if tp_price is None and sl_price is None:
        return None

    return {
        "exchange_order_id": exchange_order_id,
        "position_id": position_id,
        "symbol": str(order.get("symbol") or "UNKNOWN").strip().upper() or "UNKNOWN",
        "base_asset": optional_text(order.get("base")),
        "quote_asset": optional_text(order.get("quote")),
        "tp_price": tp_price,
        "tp_stop_type": optional_text(order.get("tpStopType")),
        "tp_order_type": optional_text(order.get("tpOrderType")),
        "tp_order_price": optional_positive_float(order.get("tpOrderPrice")),
        "tp_quantity": optional_positive_float(order.get("tpQty")),
        "sl_price": sl_price,
        "sl_stop_type": optional_text(order.get("slStopType")),
        "sl_order_type": optional_text(order.get("slOrderType")),
        "sl_order_price": optional_positive_float(order.get("slOrderPrice")),
        "sl_quantity": optional_positive_float(order.get("slQty")),
    }


def replace_open_trade_tpsl_snapshot(
    orders: list[dict],
    *,
    active_position_ids: set[str],
) -> dict:
    """Ersetzt den lokalen Snapshot erst nach vollständiger Leseabfrage."""

    active_ids = {
        str(position_id).strip()
        for position_id in active_position_ids
        if str(position_id).strip()
    }

    normalized_by_id: dict[str, dict] = {}

    for order in orders:
        normalized = normalize_order(order, active_ids)
        if normalized is not None:
            normalized_by_id[normalized["exchange_order_id"]] = normalized

    db = SessionLocal()

    try:
        existing_rows = db.query(OpenTradeTpSlOrder).all()
        existing_by_id = {
            str(row.exchange_order_id): row
            for row in existing_rows
        }

        inserted = 0
        updated = 0
        deleted = 0
        now = datetime.now(timezone.utc)

        for exchange_order_id, row in existing_by_id.items():
            if exchange_order_id not in normalized_by_id:
                db.delete(row)
                deleted += 1

        for exchange_order_id, payload in normalized_by_id.items():
            row = existing_by_id.get(exchange_order_id)

            if row is None:
                db.add(OpenTradeTpSlOrder(**payload, synced_at=now))
                inserted += 1
                continue

            changed = False
            for field_name in ("position_id", *TEXT_FIELDS, *FLOAT_FIELDS):
                new_value = payload[field_name]
                if getattr(row, field_name) != new_value:
                    setattr(row, field_name, new_value)
                    changed = True

            if changed:
                row.synced_at = now
                updated += 1

        if inserted or updated or deleted:
            db.commit()
        else:
            db.rollback()

        return {
            "orders": len(normalized_by_id),
            "tp_levels": sum(
                1 for payload in normalized_by_id.values()
                if payload["tp_price"] is not None
            ),
            "sl_levels": sum(
                1 for payload in normalized_by_id.values()
                if payload["sl_price"] is not None
            ),
            "inserted": inserted,
            "updated": updated,
            "deleted": deleted,
            "changed": bool(inserted or updated or deleted),
        }

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


def get_open_trade_tpsl_orders_by_position(
    position_ids: list[str] | set[str] | None = None,
) -> dict[str, list[OpenTradeTpSlOrder]]:
    normalized_ids = None

    if position_ids is not None:
        normalized_ids = {
            str(position_id).strip()
            for position_id in position_ids
            if str(position_id).strip()
        }
        if not normalized_ids:
            return {}

    db = SessionLocal()

    try:
        query = db.query(OpenTradeTpSlOrder)

        if normalized_ids is not None:
            query = query.filter(
                OpenTradeTpSlOrder.position_id.in_(normalized_ids)
            )

        rows = query.order_by(
            OpenTradeTpSlOrder.position_id.asc(),
            OpenTradeTpSlOrder.id.asc(),
        ).all()

        grouped: dict[str, list[OpenTradeTpSlOrder]] = {}
        for row in rows:
            grouped.setdefault(str(row.position_id), []).append(row)

        return grouped

    finally:
        db.close()

# ==================================================
# PROJECT ATLAS M5.5A MULTI LEVEL PERCENT EDITOR START
# ==================================================

def get_open_trade_tpsl_order(
    position_id: str,
    exchange_order_id: str,
) -> OpenTradeTpSlOrder | None:
    """
    Lädt exakt einen lokal synchronisierten offenen
    TP-/SL-Auftrag einer offenen Position.
    """

    db = SessionLocal()

    try:
        return (
            db.query(OpenTradeTpSlOrder)
            .filter(
                OpenTradeTpSlOrder.position_id
                == str(position_id),
                OpenTradeTpSlOrder.exchange_order_id
                == str(exchange_order_id),
            )
            .first()
        )

    finally:
        db.close()

# ==================================================
# PROJECT ATLAS M5.5A MULTI LEVEL PERCENT EDITOR END
# ==================================================

