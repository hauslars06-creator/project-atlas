import hashlib
import os
import secrets
import time

import json
import httpx
from dotenv import load_dotenv


from pathlib import Path

ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(dotenv_path=ENV_PATH)


class BitunixClient:
    def __init__(self) -> None:
        self.api_key = os.getenv("BITUNIX_API_KEY")
        self.api_secret = os.getenv("BITUNIX_API_SECRET")
        self.base_url = os.getenv(
            "BITUNIX_BASE_URL",
            "https://fapi.bitunix.com",
        )

        if not self.api_key or not self.api_secret:
            raise RuntimeError(
                "BITUNIX_API_KEY oder BITUNIX_API_SECRET fehlt in der .env-Datei."
            )

    @staticmethod
    def _sha256(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _create_headers(
        self,
        query_string: str = "",
        body: str = "",
    ) -> dict[str, str]:

        nonce = secrets.token_hex(16)
        timestamp = str(int(time.time() * 1000))

        digest_input = (
            nonce
            + timestamp
            + self.api_key
            + query_string
            + body
        )

        digest = self._sha256(digest_input)

        sign = self._sha256(
            digest + self.api_secret
        )

        return {
            "api-key": self.api_key,
            "nonce": nonce,
            "timestamp": timestamp,
            "sign": sign,
            "language": "en-US",
            "Content-Type": "application/json",
        }
    async def get_usdt_account(self) -> dict:
        endpoint = "/api/v1/futures/account"

        query_string = "marginCoinUSDT"

        headers = self._create_headers(
            query_string=query_string
        )

        async with httpx.AsyncClient(
            base_url=self.base_url,
            timeout=10.0,
        ) as client:
            response = await client.get(
                endpoint,
                params={
                    "marginCoin": "USDT",
                },
                headers=headers,
            )

            response.raise_for_status()

            return response.json()

    async def get_pending_positions(self) -> dict:
        endpoint = "/api/v1/futures/position/get_pending_positions"

        query_string = "marginCoinUSDT"

        headers = self._create_headers(
            query_string=query_string
        )

        async with httpx.AsyncClient(
            base_url=self.base_url,
            timeout=10.0,
        ) as client:
            response = await client.get(
                endpoint,
                params={
                    "marginCoin": "USDT",
                },
                headers=headers,
            )

            response.raise_for_status()
            return response.json()

    async def get_ticker(self, symbol: str = "BTCUSDT") -> dict:
        endpoint = "/api/v1/futures/market/tickers"

        async with httpx.AsyncClient(
            base_url=self.base_url,
            timeout=10.0,
        ) as client:
            response = await client.get(
                endpoint,
                params={"symbols": symbol},
            )

            response.raise_for_status()
            return response.json()

    async def get_trading_pair(self, symbol: str = "BTCUSDT") -> dict:
        endpoint = "/api/v1/futures/market/trading_pairs"

        async with httpx.AsyncClient(
            base_url=self.base_url,
            timeout=10.0,
        ) as client:
            response = await client.get(
                endpoint,
                params={"symbols": symbol},
            )

            response.raise_for_status()
            return response.json()
        
    async def place_order(
        self,
        symbol: str,
        qty: str,
        side: str,
        trade_side: str,
        client_id: str | None = None,
    ) -> dict:
        endpoint = "/api/v1/futures/trade/place_order"

        payload = {
            "symbol": symbol,
            "qty": qty,
            "side": side,
            "tradeSide": trade_side,
            "orderType": "MARKET",
        }

        if client_id:
            payload["clientId"] = client_id

        body = json.dumps(
            payload,
            separators=(",", ":"),
        )

        headers = self._create_headers(
            body=body,
        )

        async with httpx.AsyncClient(
            base_url=self.base_url,
            timeout=10.0,
        ) as client:
            response = await client.post(
                endpoint,
                content=body,
                headers=headers,
            )

            response.raise_for_status()
            return response.json()

    async def get_order_detail(
        self,
        symbol: str,
        client_id: str,
    ) -> dict:
        endpoint = "/api/v1/futures/trade/get_order_detail"

        params = {
            "symbol": symbol,
            "clientId": client_id,
        }

        query_string = (
            f"clientId{client_id}"
            f"symbol{symbol}"
        )

        headers = self._create_headers(
            query_string=query_string,
        )

        async with httpx.AsyncClient(
            base_url=self.base_url,
            timeout=10.0,
        ) as client:
            response = await client.get(
                endpoint,
                params=params,
                headers=headers,
            )

            response.raise_for_status()
            return response.json()

    async def place_position_tpsl(
        self,
        symbol: str,
        position_id: str,
        tp_price: str,
        sl_price: str,
    ) -> dict:
        endpoint = "/api/v1/futures/tpsl/position/place_order"

        payload = {
            "symbol": symbol,
            "positionId": position_id,
            "tpPrice": tp_price,
            "tpStopType": "LAST_PRICE",
            "slPrice": sl_price,
            "slStopType": "LAST_PRICE",
        }

        body = json.dumps(
            payload,
            separators=(",", ":"),
        )

        headers = self._create_headers(
            body=body,
        )

        async with httpx.AsyncClient(
            base_url=self.base_url,
            timeout=10.0,
        ) as client:
            response = await client.post(
                endpoint,
                content=body,
                headers=headers,
            )

            response.raise_for_status()
            return response.json()
    async def flash_close_position(
        self,
        position_id: str,
    ) -> dict:
        endpoint = "/api/v1/futures/trade/flash_close_position"

        payload = {
            "positionId": position_id,
        }

        body = json.dumps(
            payload,
            separators=(",", ":"),
        )

        headers = self._create_headers(
            body=body,
        )

        async with httpx.AsyncClient(
            base_url=self.base_url,
            timeout=10.0,
        ) as client:
            response = await client.post(
                endpoint,
                content=body,
                headers=headers,
            )

            response.raise_for_status()
            return response.json()       