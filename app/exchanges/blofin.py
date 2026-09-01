# ==========================================================
# Project Atlas
# File: app/exchanges/blofin.py
# Zweck: Anbindung an die Blofin-Futures-API. Blofin
#        unterstuetzt "Multi-Position Mode" - mehrere
#        unabhaengige Positionen im selben Symbol und
#        derselben Richtung (Aequivalent zu Bitunix'
#        "Futures Multi-Trade Mode"), UND fuehrt Aktien-
#        Futures (META, NVDA, GOOGL u.a.).
#
# WICHTIG: Auth-Verfahren unterscheidet sich sowohl von
# Bitunix als auch von Bitget - VIER Header-Werte
# (Key, Secret als Signierschluessel, Passphrase, PLUS
# eine Nonce), und die Signatur wird zweistufig gebildet:
# erst HMAC-SHA256-Hexdigest, DANN wird dieser Hex-String
# nochmal Base64-kodiert (nicht der rohe Digest direkt).
#
# Quelle: offizielle Blofin-API-Doku (docs.blofin.com).
# Vor produktivem Einsatz unbedingt mit einer kleinen
# Testorder verifizieren - insbesondere das Verhalten von
# Multi-Position Mode fuer die Aktien-Futures-Symbole ist
# noch nicht live gegen den Account getestet.
# ==========================================================

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import uuid
from typing import Any

import httpx


BLOFIN_BASE_URL = "https://openapi.blofin.com"


class BlofinClient:
    """
    Minimaler Blofin-Futures-Client fuer Atlas.
    Deckt die Grundoperationen ab: Instrumente/Symbole
    abrufen, Ticker abrufen, Positionsmodus abrufen/setzen,
    Hebel setzen, Market-Order platzieren (inkl. Multi-
    Position Mode), TP/SL setzen.
    """

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        api_passphrase: str | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("BLOFIN_API_KEY", "")
        self.api_secret = api_secret or os.getenv(
            "BLOFIN_API_SECRET", ""
        )
        self.api_passphrase = api_passphrase or os.getenv(
            "BLOFIN_API_PASSPHRASE", ""
        )

        if not (
            self.api_key
            and self.api_secret
            and self.api_passphrase
        ):
            raise RuntimeError(
                "Blofin-Zugangsdaten fehlen. Bitte "
                "BLOFIN_API_KEY, BLOFIN_API_SECRET und "
                "BLOFIN_API_PASSPHRASE in der .env setzen."
            )

    # ------------------------------------------------------
    # Signatur / Authentifizierung
    # ------------------------------------------------------

    def _timestamp_ms(self) -> str:
        return str(int(time.time() * 1000))

    def _nonce(self) -> str:
        return str(uuid.uuid4())

    def _sign(
        self,
        path: str,
        method: str,
        timestamp: str,
        nonce: str,
        body_str: str,
    ) -> str:
        prehash = (
            path + method.upper() + timestamp + nonce + body_str
        )
        hex_signature = hmac.new(
            self.api_secret.encode("utf-8"),
            prehash.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest().encode("utf-8")

        return base64.b64encode(hex_signature).decode("utf-8")

    def _headers(
        self,
        path: str,
        method: str,
        timestamp: str,
        nonce: str,
        body_str: str,
    ) -> dict[str, str]:
        signature = self._sign(
            path, method, timestamp, nonce, body_str
        )
        return {
            "ACCESS-KEY": self.api_key,
            "ACCESS-SIGN": signature,
            "ACCESS-TIMESTAMP": timestamp,
            "ACCESS-NONCE": nonce,
            "ACCESS-PASSPHRASE": self.api_passphrase,
            "Content-Type": "application/json",
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

        signing_path = path + query_string

        body_str = ""
        if json_body is not None:
            body_str = json.dumps(
                json_body,
                separators=(",", ":"),
            )

        timestamp = self._timestamp_ms()
        nonce = self._nonce()
        headers = self._headers(
            signing_path, method, timestamp, nonce, body_str
        )

        url = BLOFIN_BASE_URL + path + query_string

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

    async def get_instruments(
        self, inst_type: str = "SWAP"
    ) -> dict[str, Any]:
        return await self._request(
            "GET",
            "/api/v1/market/instruments",
            params={"instType": inst_type},
        )

    async def get_ticker(self, inst_id: str) -> dict[str, Any]:
        return await self._request(
            "GET",
            "/api/v1/market/tickers",
            params={"instId": inst_id},
        )

    # ------------------------------------------------------
    # Konto / Positionsmodus / Hebel
    # ------------------------------------------------------

    async def get_position_mode(self) -> dict[str, Any]:
        return await self._request(
            "GET", "/api/v1/account/position-mode"
        )

    async def set_position_mode(
        self, position_mode: str
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/api/v1/account/set-position-mode",
            json_body={"positionMode": position_mode},
        )

    async def get_margin_mode(self) -> dict[str, Any]:
        return await self._request(
            "GET", "/api/v1/account/margin-mode"
        )

    async def set_margin_mode(
        self, margin_mode: str
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/api/v1/account/set-margin-mode",
            json_body={"marginMode": margin_mode},
        )

    async def set_leverage(
        self,
        *,
        inst_id: str,
        leverage: str,
        margin_mode: str = "cross",
        position_side: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "instId": inst_id,
            "leverage": leverage,
            "marginMode": margin_mode,
        }
        if position_side:
            payload["positionSide"] = position_side

        return await self._request(
            "POST",
            "/api/v1/account/set-leverage",
            json_body=payload,
        )

    async def get_positions(
        self, inst_id: str | None = None
    ) -> dict[str, Any]:
        params = {"instId": inst_id} if inst_id else None
        return await self._request(
            "GET", "/api/v1/account/positions", params=params
        )

    async def cancel_tpsl_order(
        self, *, inst_id: str, tpsl_id: str
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/api/v1/trade/cancel-tpsl",
            json_body=[
                {
                    "instId": inst_id,
                    "tpslId": tpsl_id,
                    "clientOrderId": "",
                }
            ],
        )

    async def get_balance(self) -> dict[str, Any]:
        """
        Liefert die Futures-Kontostaende (alle Waehrungen).
        Fuer USDT-Margin-Konten interessiert i.d.R. nur der
        USDT-Eintrag - Filterung erfolgt aufrufseitig.
        """
        return await self._request(
            "GET", "/api/v1/account/balance"
        )

    # ------------------------------------------------------
    # Orders
    # ------------------------------------------------------

    async def place_market_order(
        self,
        *,
        inst_id: str,
        side: str,
        size: str,
        margin_mode: str = "cross",
        position_side: str = "long",
        client_order_id: str | None = None,
        reduce_only: bool = False,
        position_id: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "instId": inst_id,
            "marginMode": margin_mode,
            "positionSide": position_side,
            "side": side,
            "orderType": "market",
            "size": size,
            "reduceOnly": "true" if reduce_only else "false",
        }

        if client_order_id:
            payload["clientOrderId"] = client_order_id

        if position_id:
            payload["positionId"] = position_id

        return await self._request(
            "POST",
            "/api/v1/trade/order",
            json_body=payload,
        )

    async def place_tpsl_order(
        self,
        *,
        inst_id: str,
        position_side: str,
        size: str,
        tp_trigger_price: str | None = None,
        sl_trigger_price: str | None = None,
        margin_mode: str = "cross",
        position_id: str | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        # "side" ist bei Blofin die SCHLIESSENDE Richtung:
        # eine Long-Position wird durch "sell" geschlossen,
        # eine Short-Position durch "buy". Pflichtfeld, ohne
        # das Blofin mit Code 152001 ablehnt.
        closing_side = "sell" if position_side == "long" else "buy"

        payload: dict[str, Any] = {
            "instId": inst_id,
            "marginMode": margin_mode,
            "positionSide": position_side,
            "side": closing_side,
            "size": size,
        }

        if tp_trigger_price:
            payload["tpTriggerPrice"] = tp_trigger_price
            payload["tpOrderPrice"] = "-1"

        if sl_trigger_price:
            payload["slTriggerPrice"] = sl_trigger_price
            payload["slOrderPrice"] = "-1"

        if position_id:
            payload["positionId"] = position_id

        if client_order_id:
            payload["clientOrderId"] = client_order_id

        return await self._request(
            "POST",
            "/api/v1/trade/order-tpsl",
            json_body=payload,
        )
