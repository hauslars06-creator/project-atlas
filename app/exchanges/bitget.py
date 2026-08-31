# ==========================================================
# Project Atlas
# File: app/exchanges/bitget.py
# Zweck: Anbindung an die Bitget-Futures-API (USDT-M
#        Perpetuals), fuer Aktien-Futures, die ueber
#        Bitunix nicht per API handelbar sind.
#
# WICHTIG: Bitget nutzt ein anderes Auth-Verfahren als
# Bitunix - HMAC-SHA256 + Base64 ueber
# (timestamp + METHODE + Pfad + Querystring + Body),
# plus DREI Header-Werte (Key, Secret, Passphrase) statt
# nur zwei bei Bitunix.
#
# Quelle der Endpunkte/Signatur: offizielle Bitget-API-Doku
# (www.bitget.com/api-doc), Stand siehe Session-Notiz.
# Einige Feldnamen sind noch nicht live gegen einen echten
# Account getestet - vor dem produktiven Einsatz unbedingt
# mit einer kleinen Testorder verifizieren.
# ==========================================================

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any

import httpx


BITGET_BASE_URL = "https://api.bitget.com"
DEFAULT_PRODUCT_TYPE = "USDT-FUTURES"
DEFAULT_MARGIN_COIN = "USDT"


class BitgetClient:
    """
    Minimaler Bitget-Futures-Client fuer Atlas.
    Deckt die Grundoperationen ab: Symbol-Konfiguration
    abrufen, Ticker abrufen, Hebel setzen, Market-Order
    platzieren, TP/SL setzen.
    """

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        api_passphrase: str | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("BITGET_API_KEY", "")
        self.api_secret = api_secret or os.getenv(
            "BITGET_API_SECRET", ""
        )
        self.api_passphrase = api_passphrase or os.getenv(
            "BITGET_API_PASSPHRASE", ""
        )

        if not (
            self.api_key
            and self.api_secret
            and self.api_passphrase
        ):
            raise RuntimeError(
                "Bitget-Zugangsdaten fehlen. Bitte "
                "BITGET_API_KEY, BITGET_API_SECRET und "
                "BITGET_API_PASSPHRASE in der .env setzen."
            )

    # ------------------------------------------------------
    # Signatur / Authentifizierung
    # ------------------------------------------------------

    def _timestamp_ms(self) -> str:
        return str(int(time.time() * 1000))

    def _sign(
        self,
        timestamp: str,
        method: str,
        request_path: str,
        query_string: str,
        body: str,
    ) -> str:
        """
        Bitget-Signatur:
        base64(
            hmac_sha256(
                secret,
                timestamp + METHOD + path + "?"+query + body
            )
        )
        query_string bereits inkl. fuehrendem "?" oder leer.
        body ist der roh-JSON-String oder leer (bei GET).
        """
        message = (
            timestamp
            + method.upper()
            + request_path
            + query_string
            + body
        )
        digest = hmac.new(
            self.api_secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        return base64.b64encode(digest).decode("utf-8")

    def _headers(
        self,
        timestamp: str,
        method: str,
        request_path: str,
        query_string: str,
        body: str,
    ) -> dict[str, str]:
        signature = self._sign(
            timestamp, method, request_path, query_string, body
        )
        return {
            "ACCESS-KEY": self.api_key,
            "ACCESS-SIGN": signature,
            "ACCESS-PASSPHRASE": self.api_passphrase,
            "ACCESS-TIMESTAMP": timestamp,
            "Content-Type": "application/json",
            "locale": "de-DE",
        }

    # ------------------------------------------------------
    # Interner Request-Helfer
    # ------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        method = method.upper()

        query_string = ""
        if params:
            parts = [
                f"{key}={value}"
                for key, value in params.items()
                if value is not None
            ]
            if parts:
                query_string = "?" + "&".join(parts)

        body_str = ""
        if json_body is not None:
            body_str = json.dumps(
                json_body,
                separators=(",", ":"),
            )

        timestamp = self._timestamp_ms()
        headers = self._headers(
            timestamp, method, path, query_string, body_str
        )

        url = BITGET_BASE_URL + path + query_string

        async with httpx.AsyncClient(timeout=15.0) as client:
            if method == "GET":
                response = await client.get(url, headers=headers)
            else:
                response = await client.post(
                    url,
                    headers=headers,
                    content=body_str,
                )

        response.raise_for_status()
        return response.json()

    # ------------------------------------------------------
    # Oeffentliche Endpunkte
    # ------------------------------------------------------

    async def get_contracts(
        self, product_type: str = DEFAULT_PRODUCT_TYPE
    ) -> dict[str, Any]:
        return await self._request(
            "GET",
            "/api/v2/mix/market/contracts",
            params={"productType": product_type},
        )

    async def get_ticker(
        self,
        symbol: str,
        product_type: str = DEFAULT_PRODUCT_TYPE,
    ) -> dict[str, Any]:
        return await self._request(
            "GET",
            "/api/v2/mix/market/ticker",
            params={
                "symbol": symbol,
                "productType": product_type,
            },
        )

    # ------------------------------------------------------
    # Konto / Hebel
    # ------------------------------------------------------

    async def get_account(
        self,
        margin_coin: str = DEFAULT_MARGIN_COIN,
        product_type: str = DEFAULT_PRODUCT_TYPE,
    ) -> dict[str, Any]:
        return await self._request(
            "GET",
            "/api/v2/mix/account/accounts",
            params={
                "productType": product_type,
                "marginCoin": margin_coin,
            },
        )

    async def set_leverage(
        self,
        *,
        symbol: str,
        leverage: int,
        hold_side: str,
        margin_coin: str = DEFAULT_MARGIN_COIN,
        product_type: str = DEFAULT_PRODUCT_TYPE,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/api/v2/mix/account/set-leverage",
            json_body={
                "symbol": symbol,
                "productType": product_type,
                "marginCoin": margin_coin,
                "leverage": str(leverage),
                "holdSide": hold_side,
            },
        )

    # ------------------------------------------------------
    # Orders
    # ------------------------------------------------------

    async def place_market_order(
        self,
        *,
        symbol: str,
        side: str,
        size: str,
        margin_mode: str = "crossed",
        margin_coin: str = DEFAULT_MARGIN_COIN,
        product_type: str = DEFAULT_PRODUCT_TYPE,
        client_oid: str | None = None,
        reduce_only: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "symbol": symbol,
            "productType": product_type,
            "marginMode": margin_mode,
            "marginCoin": margin_coin,
            "size": size,
            "side": side,
            "orderType": "market",
            "reduceOnly": "yes" if reduce_only else "no",
        }

        if client_oid:
            payload["clientOid"] = client_oid

        return await self._request(
            "POST",
            "/api/v2/mix/order/place-order",
            json_body=payload,
        )

    async def place_tpsl_order(
        self,
        *,
        symbol: str,
        plan_type: str,
        trigger_price: str,
        hold_side: str,
        size: str,
        margin_coin: str = DEFAULT_MARGIN_COIN,
        product_type: str = DEFAULT_PRODUCT_TYPE,
        trigger_type: str = "mark_price",
        client_oid: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "marginCoin": margin_coin,
            "productType": product_type,
            "symbol": symbol,
            "planType": plan_type,
            "triggerPrice": trigger_price,
            "triggerType": trigger_type,
            "executePrice": "0",
            "holdSide": hold_side,
            "size": size,
        }

        if client_oid:
            payload["clientOid"] = client_oid

        return await self._request(
            "POST",
            "/api/v2/mix/order/place-tpsl-order",
            json_body=payload,
        )
