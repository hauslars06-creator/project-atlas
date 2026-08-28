from datetime import datetime, timezone

from sqlalchemy import select, update

from app.database.database import SessionLocal
from app.database.models import Signal, WebhookQueueItem


def get_pending_queue_signals() -> set[str]:
    db = SessionLocal()

    try:
        rows = (
            db.query(WebhookQueueItem.signal_id)
            .filter(
                WebhookQueueItem.status.in_(
                    [
                        "PENDING",
                        "PROCESSING",
                    ]
                )
            )
            .all()
        )

        return {
            str(row[0]).strip().upper()
            for row in rows
            if row[0]
        }

    finally:
        db.close()


def enqueue_signal(signal_id: str) -> WebhookQueueItem:
    db = SessionLocal()

    try:
        item = WebhookQueueItem(
            signal_id=signal_id.strip().upper(),
            status="PENDING",
            attempts=0,
        )

        db.add(item)
        db.commit()
        db.refresh(item)

        return item

    finally:
        db.close()


def claim_next_signal() -> WebhookQueueItem | None:
    db = SessionLocal()

    try:
        now = datetime.now(timezone.utc)

        candidate = (
            select(WebhookQueueItem.id)
            .where(
                WebhookQueueItem.status == "PENDING"
            )
            .order_by(WebhookQueueItem.id.asc())
            .limit(1)
            .scalar_subquery()
        )

        statement = (
            update(WebhookQueueItem)
            .where(
                WebhookQueueItem.id == candidate,
                WebhookQueueItem.status == "PENDING",
            )
            .values(
                status="PROCESSING",
                attempts=WebhookQueueItem.attempts + 1,
                started_at=now,
            )
            .returning(WebhookQueueItem.id)
        )

        row = db.execute(statement).first()

        if row is None:
            db.rollback()
            return None

        item_id = int(row[0])

        db.commit()

        item = db.get(
            WebhookQueueItem,
            item_id,
        )

        if item is None:
            return None

        db.expunge(item)

        return item

    finally:
        db.close()



def mark_done(item_id: int) -> None:
    db = SessionLocal()

    try:
        item = db.get(WebhookQueueItem, item_id)

        if item is None:
            return

        item.status = "DONE"
        item.finished_at = datetime.now(timezone.utc)
        item.last_error = None

        db.commit()

    finally:
        db.close()


def mark_failed(
    item_id: int,
    error: str,
) -> None:
    db = SessionLocal()

    try:
        item = db.get(WebhookQueueItem, item_id)

        if item is None:
            return

        item.status = "FAILED"
        item.finished_at = datetime.now(timezone.utc)
        item.last_error = str(error)[:1000]

        db.commit()

    finally:
        db.close()


def has_recent_webhook_activity(
    seconds: int = 180,
    symbol: str | None = None,
) -> bool:
    """
    Prueft, ob kuerzlich ein Webhook verarbeitet wurde
    (PENDING/PROCESSING/FAILED).

    Wird `symbol` angegeben, gilt die Pruefung nur fuer
    Webhooks von Signalen desselben Symbols - ein
    laufendes Signal fuer ein anderes Symbol blockiert
    dann nicht mehr die Erkennung unabhaengiger, manuell
    eroeffneter Positionen auf anderen Symbolen.
    Ohne `symbol` verhaelt sich die Funktion wie zuvor
    (global, alle Symbole).
    """

    from datetime import timedelta

    cutoff = (
        datetime.now(timezone.utc)
        - timedelta(seconds=seconds)
    )

    db = SessionLocal()

    try:
        query = db.query(WebhookQueueItem).filter(
            WebhookQueueItem.created_at >= cutoff,
            WebhookQueueItem.status.in_(
                ["PENDING", "PROCESSING", "FAILED"]
            ),
        )

        if symbol:
            query = query.join(
                Signal,
                Signal.signal_id
                == WebhookQueueItem.signal_id,
            ).filter(
                Signal.symbol == str(symbol).strip().upper()
            )

        return query.first() is not None

    finally:
        db.close()


def recover_processing_items() -> int:
    db = SessionLocal()

    try:
        items = (
            db.query(WebhookQueueItem)
            .filter(
                WebhookQueueItem.status == "PROCESSING"
            )
            .all()
        )

        count = len(items)

        for item in items:
            item.status = "PENDING"
            item.started_at = None
            item.finished_at = None

        db.commit()

        return count

    finally:
        db.close()


def requeue_item(
    item_id: int,
    error: str,
) -> None:
    db = SessionLocal()

    try:
        item = db.get(WebhookQueueItem, item_id)

        if item is None:
            return

        item.status = "PENDING"
        item.started_at = None
        item.finished_at = None
        item.last_error = str(error)[:1000]

        db.commit()

    finally:
        db.close()
