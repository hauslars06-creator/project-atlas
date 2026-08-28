import asyncio
import hashlib
import json
from pathlib import Path

from app.notifications.telegram import send_telegram_alert
from app.database.database import SessionLocal
from app.database.models import WebhookQueueItem


PROJECT_ROOT = Path("/host_project")

STATE_FILE = Path(
    "/app/data/security_file_hashes.json"
)

CHECK_INTERVAL_SECONDS = 15


WATCHED_FILES = [
    ".env",
    "main.py",
    "docker-compose.yml",
    "Dockerfile",
    "app/api/webhook.py",
    "app/exchanges/bitunix.py",
    "app/trade_sync.py",
    "app/webhook_worker.py",
    "app/database/models.py",
    "app/database/database.py",
    "app/database/trade_repository.py",
    "app/database/webhook_queue_repository.py",
    "app/notifications/telegram.py",
]


def _hash_file(path: Path) -> str | None:
    if not path.is_file():
        return None

    digest = hashlib.sha256()

    with path.open("rb") as handle:
        for chunk in iter(
            lambda: handle.read(65536),
            b"",
        ):
            digest.update(chunk)

    return digest.hexdigest()


def _current_hashes() -> dict[str, str | None]:
    return {
        relative_path: _hash_file(
            PROJECT_ROOT / relative_path
        )
        for relative_path in WATCHED_FILES
    }


def _load_previous() -> dict:
    try:
        if STATE_FILE.is_file():
            return json.loads(
                STATE_FILE.read_text()
            )
    except Exception:
        pass

    return {}


def _save_current(
    hashes: dict[str, str | None],
) -> None:
    STATE_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    STATE_FILE.write_text(
        json.dumps(
            hashes,
            indent=2,
            sort_keys=True,
        )
    )


def _describe_change(
    old: str | None,
    new: str | None,
) -> str:
    if old is None and new is not None:
        return "neu angelegt"

    if old is not None and new is None:
        return "gelöscht"

    return "verändert"



def _queue_warnings(
    already_reported: set[str],
) -> list[tuple[str, str]]:

    from datetime import datetime, timezone

    db = SessionLocal()

    warnings = []

    try:
        now = datetime.now(timezone.utc)

        items = (
            db.query(WebhookQueueItem)
            .filter(
                WebhookQueueItem.status.in_(
                    ["PENDING", "PROCESSING", "FAILED"]
                )
            )
            .all()
        )

        for item in items:
            key = f"{item.id}:{item.status}"

            if key in already_reported:
                continue

            age_source = (
                item.started_at
                if item.status == "PROCESSING"
                and item.started_at
                else item.created_at
            )

            if age_source is not None:
                if age_source.tzinfo is None:
                    age_source = age_source.replace(
                        tzinfo=timezone.utc
                    )

                age_seconds = (
                    now - age_source
                ).total_seconds()
            else:
                age_seconds = 0

            if item.status == "FAILED":
                message = (
                    "🔴 PROJECT ATLAS – "
                    "WEBHOOK FEHLGESCHLAGEN\n\n"
                    f"Queue ID: {item.id}\n"
                    f"Signal: {item.signal_id}\n"
                    f"Versuche: {item.attempts}\n"
                    f"Fehler: {item.last_error or 'unbekannt'}"
                )

                warnings.append(
                    (key, message)
                )

            elif (
                item.status == "PROCESSING"
                and age_seconds >= 60
            ):
                message = (
                    "🟠 PROJECT ATLAS – "
                    "WEBHOOK HÄNGT MÖGLICHERWEISE\n\n"
                    f"Queue ID: {item.id}\n"
                    f"Signal: {item.signal_id}\n"
                    f"Status: PROCESSING\n"
                    f"Seit ca. {int(age_seconds)} Sekunden aktiv."
                )

                warnings.append(
                    (key, message)
                )

            elif (
                item.status == "PENDING"
                and age_seconds >= 60
            ):
                message = (
                    "🟠 PROJECT ATLAS – "
                    "QUEUE VERZÖGERUNG\n\n"
                    f"Queue ID: {item.id}\n"
                    f"Signal: {item.signal_id}\n"
                    f"Status: PENDING\n"
                    f"Seit ca. {int(age_seconds)} Sekunden wartend."
                )

                warnings.append(
                    (key, message)
                )

        return warnings

    finally:
        db.close()



TRADE_SYNC_HEALTH_FILE = Path(
    "/app/data/trade_sync_health.json"
)


def _read_trade_sync_health() -> dict | None:
    try:
        if not TRADE_SYNC_HEALTH_FILE.is_file():
            return None

        return json.loads(
            TRADE_SYNC_HEALTH_FILE.read_text()
        )

    except Exception as exc:
        print(
            "SECURITY MONITOR: "
            f"Trade-Sync-Health konnte nicht gelesen werden: {exc}"
        )
        return None


async def security_monitor_loop() -> None:
    current = _current_hashes()
    previous = _load_previous()

    if previous:
        changed_while_offline = []

        for name, new_hash in current.items():
            old_hash = previous.get(name)

            if old_hash != new_hash:
                changed_while_offline.append(
                    (
                        name,
                        _describe_change(
                            old_hash,
                            new_hash,
                        ),
                    )
                )

        if changed_while_offline:
            lines = "\n".join(
                f"• {name}: {change}"
                for name, change
                in changed_while_offline
            )

            await send_telegram_alert(
                "🔴 PROJECT ATLAS – "
                "SICHERHEITSWARNUNG\n\n"
                "Beim Start wurden Änderungen an "
                "überwachten Dateien erkannt:\n\n"
                f"{lines}\n\n"
                "Bitte prüfen, ob diese Änderungen "
                "von dir autorisiert wurden."
            )

    _save_current(current)

    await send_telegram_alert(
        "🟠 PROJECT ATLAS – SYSTEMSTART\n\n"
        "Atlas wurde gestartet bzw. neu gestartet.\n"
        "Security Monitor ist aktiv.\n\n"
        "Falls dieser Neustart nicht von dir "
        "ausgelöst wurde, bitte Server und "
        "Atlas sofort prüfen."
    )

    baseline = current
    reported_queue_warnings: set[str] = set()

    sync_alert_level = 0
    sync_stale_reported = False

    while True:
        await asyncio.sleep(
            CHECK_INTERVAL_SECONDS
        )

        current = _current_hashes()

        sync_health = _read_trade_sync_health()

        if sync_health:
            sync_status = str(
                sync_health.get("status", "")
            ).upper()

            sync_failures = int(
                sync_health.get(
                    "consecutive_failures",
                    0,
                )
                or 0
            )

            sync_error = (
                sync_health.get("error")
                or "unbekannter Fehler"
            )

            sync_updated_at = (
                sync_health.get("updated_at")
            )

            sync_age_seconds = None

            if sync_updated_at:
                try:
                    updated = datetime.fromisoformat(
                        str(sync_updated_at).replace(
                            "Z",
                            "+00:00",
                        )
                    )

                    if updated.tzinfo is None:
                        updated = updated.replace(
                            tzinfo=timezone.utc
                        )

                    sync_age_seconds = (
                        datetime.now(timezone.utc)
                        - updated
                    ).total_seconds()

                except Exception:
                    sync_age_seconds = None

            if (
                sync_age_seconds is not None
                and sync_age_seconds >= 30
                and not sync_stale_reported
            ):
                sent = await send_telegram_alert(
                    "🔴 PROJECT ATLAS – "
                    "TRADE-SYNC REAGIERT NICHT\n\n"
                    "Der Trade-Sync-Health-State wurde "
                    f"seit ca. {int(sync_age_seconds)} Sekunden "
                    "nicht mehr aktualisiert.\n\n"
                    f"Letztes Update: {sync_updated_at}\n\n"
                    "Der Trade-Sync könnte hängen oder "
                    "abgestürzt sein. Bitte Atlas und "
                    "BitUnix prüfen."
                )

                if sent:
                    sync_stale_reported = True

            elif (
                sync_age_seconds is not None
                and sync_age_seconds < 30
                and sync_stale_reported
            ):
                sent = await send_telegram_alert(
                    "🟢 PROJECT ATLAS – "
                    "TRADE-SYNC WIEDER AKTIV\n\n"
                    "Der Trade-Sync-Health-State wird "
                    "wieder regelmäßig aktualisiert."
                )

                if sent:
                    sync_stale_reported = False

            if (
                sync_status == "ERROR"
                and sync_failures >= 12
                and sync_alert_level < 2
            ):
                sent = await send_telegram_alert(
                    "🔴 PROJECT ATLAS – "
                    "BITUNIX / TRADE-SYNC STÖRUNG\n\n"
                    f"{sync_failures} Fehler "
                    "hintereinander erkannt.\n\n"
                    f"Letzter Fehler:\n{sync_error}\n\n"
                    "Der automatische Abgleich mit "
                    "BitUnix ist aktuell erheblich gestört. "
                    "Bitte Atlas und BitUnix prüfen."
                )

                if sent:
                    sync_alert_level = 2

            elif (
                sync_status == "ERROR"
                and sync_failures >= 3
                and sync_alert_level < 1
            ):
                sent = await send_telegram_alert(
                    "🟠 PROJECT ATLAS – "
                    "TRADE-SYNC WARNUNG\n\n"
                    f"{sync_failures} Fehler "
                    "hintereinander erkannt.\n\n"
                    f"Letzter Fehler:\n{sync_error}\n\n"
                    "Atlas beobachtet die Störung weiter."
                )

                if sent:
                    sync_alert_level = 1

            elif (
                sync_status == "OK"
                and sync_alert_level > 0
            ):
                sent = await send_telegram_alert(
                    "🟢 PROJECT ATLAS – "
                    "TRADE-SYNC WIEDERHERGESTELLT\n\n"
                    "Der Abgleich mit BitUnix funktioniert "
                    "wieder normal.\n"
                    "Fehlerzähler: 0"
                )

                if sent:
                    sync_alert_level = 0

        queue_warnings = _queue_warnings(
            reported_queue_warnings
        )

        for key, message in queue_warnings:
            sent = await send_telegram_alert(
                message
            )

            if sent:
                reported_queue_warnings.add(
                    key
                )

        changes = []

        for name, new_hash in current.items():
            old_hash = baseline.get(name)

            if old_hash != new_hash:
                changes.append(
                    (
                        name,
                        _describe_change(
                            old_hash,
                            new_hash,
                        ),
                    )
                )

        if not changes:
            continue

        lines = "\n".join(
            f"• {name}: {change}"
            for name, change in changes
        )

        await send_telegram_alert(
            "🔴 PROJECT ATLAS – "
            "DATEIÄNDERUNG ERKANNT\n\n"
            "Eine oder mehrere sicherheitsrelevante "
            "Dateien wurden verändert:\n\n"
            f"{lines}\n\n"
            "Falls diese Änderung nicht von dir "
            "durchgeführt wurde, bitte den Server "
            "sofort prüfen."
        )

        baseline = current
        _save_current(current)
