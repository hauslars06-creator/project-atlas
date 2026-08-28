import asyncio
import logging

from fastapi import HTTPException

from app.api.webhook import process_signal
from app.database.webhook_queue_repository import (
    claim_next_signal,
    mark_done,
    mark_failed,
    requeue_item,
)

logger = logging.getLogger(__name__)

MAX_CONCURRENT_WEBHOOKS = 5


async def process_queue_item(item) -> None:
    try:
        await process_signal(
            signal_id=item.signal_id,
            force_live=False,
        )

        mark_done(item.id)

        logger.info(
            "Webhook Queue DONE: id=%s signal=%s",
            item.id,
            item.signal_id,
        )

    except HTTPException as exc:
        status = int(exc.status_code)

        if (
            status >= 500
            and item.attempts < 3
        ):
            await asyncio.sleep(
                min(item.attempts, 3)
            )

            requeue_item(
                item.id,
                f"HTTP {status}: {exc.detail}",
            )

            logger.warning(
                "Webhook Queue RETRY: "
                "id=%s signal=%s attempt=%s",
                item.id,
                item.signal_id,
                item.attempts,
            )

        else:
            mark_failed(
                item.id,
                f"HTTP {status}: {exc.detail}",
            )

            logger.exception(
                "Webhook Queue FAILED: "
                "id=%s signal=%s",
                item.id,
                item.signal_id,
            )

    except Exception as exc:
        error = (
            f"{type(exc).__name__}: {exc}"
        )

        if item.attempts < 3:
            await asyncio.sleep(
                min(item.attempts, 3)
            )

            requeue_item(
                item.id,
                error,
            )

            logger.warning(
                "Webhook Queue RETRY: "
                "id=%s signal=%s attempt=%s",
                item.id,
                item.signal_id,
                item.attempts,
            )

        else:
            mark_failed(
                item.id,
                error,
            )

            logger.exception(
                "Webhook Queue FAILED: "
                "id=%s signal=%s",
                item.id,
                item.signal_id,
            )


async def webhook_worker_loop(
    interval_seconds: float = 0.10,
) -> None:
    running: set[asyncio.Task] = set()

    while True:
        try:
            finished = {
                task
                for task in running
                if task.done()
            }

            running -= finished

            for task in finished:
                try:
                    task.result()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception(
                        "Webhook Worker Task Fehler"
                    )

            while (
                len(running)
                < MAX_CONCURRENT_WEBHOOKS
            ):
                item = claim_next_signal()

                if item is None:
                    break

                task = asyncio.create_task(
                    process_queue_item(item)
                )

                running.add(task)

            if not running:
                await asyncio.sleep(
                    interval_seconds
                )
            else:
                await asyncio.sleep(0.05)

        except asyncio.CancelledError:
            for task in running:
                task.cancel()

            if running:
                await asyncio.gather(
                    *running,
                    return_exceptions=True,
                )

            raise

        except Exception:
            logger.exception(
                "Webhook Worker Loop Fehler"
            )
            await asyncio.sleep(1)
