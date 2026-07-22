import os
from pathlib import Path

import httpx
from dotenv import load_dotenv


ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(
    dotenv_path=ENV_PATH,
    override=True,
)


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

            response.raise_for_status()

            result = response.json()

            return result.get("ok") is True

    except Exception as exc:
        print(
            f"TELEGRAM-FEHLER: {exc}"
        )
        return False
