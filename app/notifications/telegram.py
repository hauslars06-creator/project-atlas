import asyncio
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv


ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(
    dotenv_path=ENV_PATH,
    override=True,
)


# Mindestabstand zwischen zwei Telegram-Nachrichten, um das
# Rate-Limit (ca. 1 Nachricht/Sekunde pro Chat) proaktiv
# nicht zu ueberschreiten, statt erst auf 429 zu reagieren.
_MIN_INTERVAL_SECONDS = 1.1
_last_send_lock = asyncio.Lock()
_last_send_at = 0.0

_MAX_RETRIES = 3


async def send_telegram_alert(
    message: str,
) -> bool:
    bot_token = os.getenv(
        "TELEGRAM_BOT_TOKEN"
    )
    chat_id = os.getenv(
        "TELEGRAM_CHAT_ID"
    )

    if not bot_token or not chat_id:
        print(
            "WARNUNG: Telegram-Konfiguration fehlt."
        )
        return False

    url = (
        f"https://api.telegram.org/"
        f"bot{bot_token}/sendMessage"
    )

    global _last_send_at

    for attempt in range(_MAX_RETRIES + 1):
        # Proaktive Drosselung: Mindestabstand zur letzten
        # gesendeten Nachricht einhalten.
        async with _last_send_lock:
            now = asyncio.get_event_loop().time()
            wait = _MIN_INTERVAL_SECONDS - (now - _last_send_at)

            if wait > 0:
                await asyncio.sleep(wait)

            _last_send_at = asyncio.get_event_loop().time()

        try:
            async with httpx.AsyncClient(
                timeout=10.0,
            ) as client:
                response = await client.post(
                    url,
                    json={
                        "chat_id": chat_id,
                        "text": message,
                    },
                )

                if response.status_code == 429:
                    retry_after = 2.0

                    try:
                        body = response.json()
                        retry_after = float(
                            body.get("parameters", {})
                            .get("retry_after", retry_after)
                        )
                    except Exception:
                        pass

                    if attempt < _MAX_RETRIES:
                        print(
                            "TELEGRAM: 429 erhalten, "
                            f"warte {retry_after}s und "
                            f"versuche erneut (Versuch "
                            f"{attempt + 1}/{_MAX_RETRIES})."
                        )
                        await asyncio.sleep(retry_after)
                        continue

                    print(
                        "TELEGRAM-FEHLER: 429 nach "
                        f"{_MAX_RETRIES} Versuchen weiterhin "
                        "blockiert, Nachricht verworfen."
                    )
                    return False

                response.raise_for_status()

                result = response.json()

                return result.get("ok") is True

        except Exception as exc:
            print(
                f"TELEGRAM-FEHLER: {exc}"
            )
            return False

    return False
