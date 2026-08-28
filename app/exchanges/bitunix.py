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


class _ReusedClientContext:
    """
    Minimaler Async-Context-Manager, der einen bereits
    bestehenden httpx.AsyncClient zurueckgibt, OHNE ihn beim
    Verlassen des "async with"-Blocks zu schliessen (im
    Gegensatz zu "async with httpx.AsyncClient() as client",
    das die Verbindung sofort wieder zumachen wuerde).
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def __aenter__(self) -> httpx.AsyncClient:
        return self._client

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        return None


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

        # Wiederverwendete, persistente HTTP-Verbindung statt
        # pro Methodenaufruf eine neue TCP/TLS-Verbindung
        # aufzubauen. Lazy erstellt beim ersten Aufruf.
        self._http_client: httpx.AsyncClient | None = None

    def _client_context(self):
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=15.0,
            )

        return _ReusedClientContext(self._http_client)

    async def aclose(self) -> None:
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

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

        async with self._client_context() as client:
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

        async with self._client_context() as client:
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

        async with self._client_context() as client:
            response = await client.get(
                endpoint,
                params={"symbols": symbol},
            )

            response.raise_for_status()
            return response.json()

    async def get_kline(
        self,
        symbol: str,
        interval: str,
        start_time_ms: int | None = None,
        end_time_ms: int | None = None,
        limit: int = 200,
    ) -> dict:
        """
        Laedt historische Kerzendaten (oeffentlich, kein
        Auth-Header noetig). limit ist von BitUnix auf
        maximal 200 begrenzt.
        """

        endpoint = "/api/v1/futures/market/kline"

        params = {
            "symbol": str(symbol).strip().upper(),
            "interval": str(interval),
            "limit": min(int(limit), 200),
        }

        if start_time_ms is not None:
            params["startTime"] = int(start_time_ms)

        if end_time_ms is not None:
            params["endTime"] = int(end_time_ms)

        async with self._client_context() as client:
            response = await client.get(
                endpoint,
                params=params,
            )

            response.raise_for_status()
            return response.json()

    async def get_trading_pair(self, symbol: str = "BTCUSDT") -> dict:
        endpoint = "/api/v1/futures/market/trading_pairs"

        async with self._client_context() as client:
            response = await client.get(
                endpoint,
                params={"symbols": symbol},
            )

            response.raise_for_status()
            return response.json()
        
    async def change_leverage(
        self,
        *,
        symbol: str,
        leverage: int,
        margin_coin: str = "USDT",
    ) -> dict:
        endpoint = "/api/v1/futures/account/change_leverage"

        payload = {
            "symbol": str(symbol).strip().upper(),
            "leverage": int(leverage),
            "marginCoin": str(margin_coin).strip().upper(),
        }

        body = json.dumps(
            payload,
            separators=(",", ":"),
        )

        headers = self._create_headers(
            body=body,
        )

        async with self._client_context() as client:
            response = await client.post(
                endpoint,
                content=body,
                headers=headers,
            )

            response.raise_for_status()
            return response.json()

    async def get_leverage_margin_mode(
        self,
        *,
        symbol: str,
        margin_coin: str = "USDT",
    ) -> dict:
        endpoint = (
            "/api/v1/futures/account/"
            "get_leverage_margin_mode"
        )

        normalized_symbol = (
            str(symbol).strip().upper()
        )
        normalized_margin_coin = (
            str(margin_coin).strip().upper()
        )

        params = {
            "symbol": normalized_symbol,
            "marginCoin": normalized_margin_coin,
        }

        query_string = (
            f"marginCoin{normalized_margin_coin}"
            f"symbol{normalized_symbol}"
        )

        headers = self._create_headers(
            query_string=query_string,
        )

        async with self._client_context() as client:
            response = await client.get(
                endpoint,
                params=params,
                headers=headers,
            )

            response.raise_for_status()
            return response.json()

    async def ensure_leverage(
        self,
        *,
        symbol: str,
        leverage: int,
        margin_coin: str = "USDT",
    ) -> dict:
        requested = int(leverage)

        change_result = await self.change_leverage(
            symbol=symbol,
            leverage=requested,
            margin_coin=margin_coin,
        )

        if str(change_result.get("code")) != "0":
            raise RuntimeError(
                "BitUnix-Hebel konnte nicht gesetzt "
                f"werden: {change_result}"
            )

        verification = await self.get_leverage_margin_mode(
            symbol=symbol,
            margin_coin=margin_coin,
        )

        if str(verification.get("code")) != "0":
            raise RuntimeError(
                "BitUnix-Hebel konnte nicht geprüft "
                f"werden: {verification}"
            )

        data = verification.get("data") or {}
        actual = int(data.get("leverage", 0))

        if actual != requested:
            raise RuntimeError(
                "BitUnix-Hebel stimmt nach Änderung nicht: "
                f"gewünscht={requested}, tatsächlich={actual}, "
                f"symbol={symbol}"
            )

        return {
            "requested_leverage": requested,
            "actual_leverage": actual,
            "margin_mode": data.get("marginMode"),
            "change_response": change_result,
            "verification_response": verification,
        }

    async def place_order(
        self,
        symbol: str,
        qty: str,
        side: str,
        trade_side: str,
        client_id: str | None = None,
        position_id: str | None = None,
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

        if position_id:
            payload["positionId"] = str(position_id)

        body = json.dumps(
            payload,
            separators=(",", ":"),
        )

        headers = self._create_headers(
            body=body,
        )

        async with self._client_context() as client:
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

        async with self._client_context() as client:
            response = await client.get(
                endpoint,
                params=params,
                headers=headers,
            )

            response.raise_for_status()
            return response.json()

    async def get_history_position(
        self,
        position_id: str,
        limit: int = 10,
    ) -> dict | None:
        """
        Ruft die exakten BitUnix-Abschlussdaten einer
        geschlossenen Position ab.

        Gibt die Position zurück oder None, falls BitUnix
        noch keinen passenden Historieneintrag liefert.
        """
        endpoint = (
            "/api/v1/futures/position/"
            "get_history_positions"
        )

        safe_limit = max(
            1,
            min(int(limit), 100),
        )

        params = {
            "positionId": str(position_id),
            "limit": safe_limit,
        }

        # BitUnix verlangt die Signaturparameter
        # alphabetisch nach Parameternamen:
        # limit, positionId
        query_string = (
            f"limit{safe_limit}"
            f"positionId{position_id}"
        )

        headers = self._create_headers(
            query_string=query_string,
        )

        async with self._client_context() as client:
            response = await client.get(
                endpoint,
                params=params,
                headers=headers,
            )

            response.raise_for_status()
            result = response.json()

        if result.get("code") != 0:
            raise RuntimeError(
                "BitUnix-History-Abfrage fehlgeschlagen: "
                f"{result}"
            )

        data = result.get("data") or {}
        positions = data.get("positionList") or []

        if not isinstance(positions, list):
            raise RuntimeError(
                "BitUnix hat keine gültige "
                "History-Positionsliste geliefert."
            )

        target_position_id = str(position_id)

        for position in positions:
            if (
                str(position.get("positionId"))
                == target_position_id
            ):
                return position

        return None


    # ==================================================
    # PROJECT ATLAS M5.3D2 TPSL READ SYNC V3 START
    # ==================================================

    async def get_pending_tpsl_orders(
        self,
        *,
        symbol: str | None = None,
        position_id: str | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> dict:
        """
        Lädt offene BitUnix-TP/SL-Aufträge.

        Diese Methode verwendet ausschließlich den
        lesenden Pending-TP/SL-Endpunkt.
        """

        endpoint = (
            "/api/v1/futures/tpsl/"
            "get_pending_orders"
        )

        safe_skip = max(
            0,
            int(skip),
        )

        safe_limit = max(
            1,
            min(
                int(limit),
                100,
            ),
        )

        params: dict[str, str | int] = {
            "limit": safe_limit,
            "skip": safe_skip,
        }

        if symbol:
            params["symbol"] = (
                str(symbol)
                .strip()
                .upper()
            )

        if position_id:
            params["positionId"] = str(
                position_id
            )

        # BitUnix-Signatur:
        # Parameternamen alphabetisch sortieren.
        query_string = "".join(
            f"{key}{params[key]}"
            for key in sorted(params)
        )

        headers = self._create_headers(
            query_string=query_string,
        )

        async with self._client_context() as client:
            response = await client.get(
                endpoint,
                params=params,
                headers=headers,
            )

            response.raise_for_status()
            result = response.json()

        if str(result.get("code")) != "0":
            raise RuntimeError(
                "BitUnix-TP/SL-Abfrage "
                f"fehlgeschlagen: {result}"
            )

        orders = result.get("data")

        if orders is None:
            result["data"] = []

        elif not isinstance(orders, list):
            raise RuntimeError(
                "BitUnix hat keine gültige "
                "TP/SL-Liste geliefert."
            )

        return result

    # ==================================================
    # PROJECT ATLAS M5.3D2 TPSL READ SYNC V3 END
    # ==================================================


    # ==================================================
    # PROJECT ATLAS M5.5A MULTI LEVEL PERCENT EDITOR START
    # ==================================================

    async def modify_tpsl_order(
        self,
        *,
        order_id: str,
        tp_price: str | float | None = None,
        sl_price: str | float | None = None,
        tp_stop_type: str | None = None,
        sl_stop_type: str | None = None,
        tp_order_type: str | None = None,
        sl_order_type: str | None = None,
        tp_order_price: str | float | None = None,
        sl_order_price: str | float | None = None,
        tp_qty: str | float | None = None,
        sl_qty: str | float | None = None,
    ) -> dict:
        """
        Ändert exakt einen bestehenden BitUnix-TP-/SL-
        Auftrag anhand seiner Exchange-Order-ID.
        """

        normalized_order_id = str(order_id).strip()

        if not normalized_order_id:
            raise ValueError(
                "TP-/SL-Order-ID darf nicht leer sein."
            )

        payload: dict[str, str] = {
            "orderId": normalized_order_id,
        }

        if tp_price is not None:
            if tp_qty is None:
                raise ValueError(
                    "Für eine TP-Änderung fehlt die TP-Menge."
                )

            payload["tpPrice"] = str(tp_price)
            payload["tpStopType"] = str(
                tp_stop_type or "LAST_PRICE"
            ).strip().upper()
            payload["tpOrderType"] = str(
                tp_order_type or "MARKET"
            ).strip().upper()
            payload["tpQty"] = str(tp_qty)

            if (
                payload["tpOrderType"] == "LIMIT"
                and tp_order_price is not None
            ):
                payload["tpOrderPrice"] = str(
                    tp_order_price
                )

        if sl_price is not None:
            if sl_qty is None:
                raise ValueError(
                    "Für eine SL-Änderung fehlt die SL-Menge."
                )

            payload["slPrice"] = str(sl_price)
            payload["slStopType"] = str(
                sl_stop_type or "LAST_PRICE"
            ).strip().upper()
            payload["slOrderType"] = str(
                sl_order_type or "MARKET"
            ).strip().upper()
            payload["slQty"] = str(sl_qty)

            if (
                payload["slOrderType"] == "LIMIT"
                and sl_order_price is not None
            ):
                payload["slOrderPrice"] = str(
                    sl_order_price
                )

        if "tpPrice" not in payload and "slPrice" not in payload:
            raise ValueError(
                "Mindestens TP oder SL muss geändert werden."
            )

        endpoint = "/api/v1/futures/tpsl/modify_order"

        body = json.dumps(
            payload,
            separators=(",", ":"),
        )

        headers = self._create_headers(
            body=body,
        )

        async with self._client_context() as http_client:
            response = await http_client.post(
                endpoint,
                content=body,
                headers=headers,
            )

            response.raise_for_status()
            result = response.json()

        if str(result.get("code")) != "0":
            raise RuntimeError(
                "BitUnix-TP-/SL-Auftrag konnte nicht "
                f"geändert werden: {result}"
            )

        return result

    # ==================================================
    # PROJECT ATLAS M5.5A MULTI LEVEL PERCENT EDITOR END
    # ==================================================


    # ==================================================
    # PROJECT ATLAS M5.5 MULTI TP/SL WRITE START
    # ==================================================

    async def place_tpsl_order(
        self,
        *,
        symbol: str,
        position_id: str,
        tp_price: str | float | None = None,
        sl_price: str | float | None = None,
        tp_qty: str | float | None = None,
        sl_qty: str | float | None = None,
        tp_stop_type: str = "LAST_PRICE",
        sl_stop_type: str = "LAST_PRICE",
        tp_order_type: str = "MARKET",
        sl_order_type: str = "MARKET",
        tp_order_price: str | float | None = None,
        sl_order_price: str | float | None = None,
    ) -> dict:
        """
        Legt genau einen mengenbezogenen BitUnix-TP-/SL-
        Auftrag für eine bestehende Position an.

        Für mehrere Take-Profit-Level wird diese Methode
        mehrfach aufgerufen:

        - TP1 mit eigener Menge
        - TP2 mit eigener Menge
        - SL separat mit der jeweils geschützten Restmenge

        Die bestehende Methode place_position_tpsl()
        bleibt für den bisherigen 100-Prozent-Ablauf
        unverändert erhalten.
        """

        normalized_symbol = (
            str(symbol)
            .strip()
            .upper()
        )

        normalized_position_id = str(
            position_id
        ).strip()

        if not normalized_symbol:
            raise ValueError(
                "Symbol darf nicht leer sein."
            )

        if not normalized_position_id:
            raise ValueError(
                "Position-ID darf nicht leer sein."
            )

        has_tp = (
            tp_price is not None
            and tp_qty is not None
        )

        has_sl = (
            sl_price is not None
            and sl_qty is not None
        )

        if not has_tp and not has_sl:
            raise ValueError(
                "Mindestens ein vollständiges TP- oder "
                "SL-Paar aus Preis und Menge ist erforderlich."
            )

        if (
            (tp_price is None) !=
            (tp_qty is None)
        ):
            raise ValueError(
                "TP-Preis und TP-Menge müssen gemeinsam "
                "übergeben werden."
            )

        if (
            (sl_price is None) !=
            (sl_qty is None)
        ):
            raise ValueError(
                "SL-Preis und SL-Menge müssen gemeinsam "
                "übergeben werden."
            )

        payload: dict[str, str] = {
            "symbol": normalized_symbol,
            "positionId": normalized_position_id,
        }

        if has_tp:
            normalized_tp_qty = str(
                tp_qty
            ).strip()

            if not normalized_tp_qty:
                raise ValueError(
                    "TP-Menge darf nicht leer sein."
                )

            payload.update(
                {
                    "tpPrice": str(tp_price),
                    "tpStopType": (
                        str(tp_stop_type)
                        .strip()
                        .upper()
                    ),
                    "tpOrderType": (
                        str(tp_order_type)
                        .strip()
                        .upper()
                    ),
                    "tpQty": normalized_tp_qty,
                }
            )

            if (
                payload["tpOrderType"] == "LIMIT"
            ):
                if tp_order_price is None:
                    raise ValueError(
                        "Für einen LIMIT-TP fehlt "
                        "tp_order_price."
                    )

                payload["tpOrderPrice"] = str(
                    tp_order_price
                )

        if has_sl:
            normalized_sl_qty = str(
                sl_qty
            ).strip()

            if not normalized_sl_qty:
                raise ValueError(
                    "SL-Menge darf nicht leer sein."
                )

            payload.update(
                {
                    "slPrice": str(sl_price),
                    "slStopType": (
                        str(sl_stop_type)
                        .strip()
                        .upper()
                    ),
                    "slOrderType": (
                        str(sl_order_type)
                        .strip()
                        .upper()
                    ),
                    "slQty": normalized_sl_qty,
                }
            )

            if (
                payload["slOrderType"] == "LIMIT"
            ):
                if sl_order_price is None:
                    raise ValueError(
                        "Für einen LIMIT-SL fehlt "
                        "sl_order_price."
                    )

                payload["slOrderPrice"] = str(
                    sl_order_price
                )

        valid_stop_types = {
            "LAST_PRICE",
            "MARK_PRICE",
        }

        valid_order_types = {
            "MARKET",
            "LIMIT",
        }

        for key in (
            "tpStopType",
            "slStopType",
        ):
            if (
                key in payload
                and payload[key]
                not in valid_stop_types
            ):
                raise ValueError(
                    f"Ungültiger Stop-Typ: "
                    f"{payload[key]}"
                )

        for key in (
            "tpOrderType",
            "slOrderType",
        ):
            if (
                key in payload
                and payload[key]
                not in valid_order_types
            ):
                raise ValueError(
                    f"Ungültiger Order-Typ: "
                    f"{payload[key]}"
                )

        endpoint = (
            "/api/v1/futures/tpsl/place_order"
        )

        body = json.dumps(
            payload,
            separators=(",", ":"),
        )

        headers = self._create_headers(
            body=body,
        )

        async with self._client_context() as http_client:
            response = await http_client.post(
                endpoint,
                content=body,
                headers=headers,
            )

            response.raise_for_status()
            result = response.json()

        if str(result.get("code")) != "0":
            raise RuntimeError(
                "BitUnix-TP-/SL-Auftrag konnte nicht "
                f"angelegt werden: {result}"
            )

        data = result.get("data")

        # BitUnix liefert je nach TP/SL Endpoint
        # unterschiedliche Datenstrukturen:
        # - dict: {"orderId": "..."}
        # - list: [{"orderId": "..."}]
        #
        # Beide Varianten sind gültige erfolgreiche Antworten.

        if isinstance(data, list):
            data = data[0] if data else None

        if not isinstance(data, dict):
            raise RuntimeError(
                "BitUnix hat keine gültigen TP-/SL-"
                f"Auftragsdaten geliefert: {result}"
            )

        order_id = data.get("orderId")

        if order_id in (None, ""):
            raise RuntimeError(
                "BitUnix hat keine TP-/SL-Order-ID "
                f"geliefert: {result}"
            )

        return result

    # ==================================================
    # PROJECT ATLAS M5.5 MULTI TP/SL WRITE END
    # ==================================================


    async def place_position_sl(
        self,
        *,
        symbol: str,
        position_id: str,
        sl_price: str | float,
        sl_stop_type: str = "LAST_PRICE",
    ) -> dict:
        """
        Legt einen Positions-Stop-Loss an.

        Beim Auslösen schließt BitUnix die zu diesem
        Zeitpunkt noch vorhandene Positionsmenge.
        """

        normalized_symbol = (
            str(symbol)
            .strip()
            .upper()
        )

        normalized_position_id = str(
            position_id
        ).strip()

        if not normalized_symbol:
            raise ValueError(
                "Symbol darf nicht leer sein."
            )

        if not normalized_position_id:
            raise ValueError(
                "Position-ID darf nicht leer sein."
            )

        payload = {
            "symbol": normalized_symbol,
            "positionId": normalized_position_id,
            "slPrice": str(sl_price),
            "slStopType": (
                str(sl_stop_type)
                .strip()
                .upper()
            ),
        }

        if payload["slStopType"] not in {
            "LAST_PRICE",
            "MARK_PRICE",
        }:
            raise ValueError(
                "Ungültiger SL-Stop-Typ."
            )

        endpoint = (
            "/api/v1/futures/tpsl/"
            "position/place_order"
        )

        body = json.dumps(
            payload,
            separators=(",", ":"),
        )

        headers = self._create_headers(
            body=body,
        )

        async with self._client_context() as http_client:
            response = await http_client.post(
                endpoint,
                content=body,
                headers=headers,
            )

            response.raise_for_status()
            result = response.json()

        if str(result.get("code")) != "0":
            raise RuntimeError(
                "BitUnix-Positions-SL konnte nicht "
                f"angelegt werden: {result}"
            )

        data = result.get("data")

        if not isinstance(data, dict):
            raise RuntimeError(
                "BitUnix hat keine gültigen "
                f"Positions-SL-Daten geliefert: {result}"
            )

        if data.get("orderId") in (None, ""):
            raise RuntimeError(
                "BitUnix hat keine Positions-SL-"
                f"Order-ID geliefert: {result}"
            )

        return result


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

        async with self._client_context() as client:
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

        async with self._client_context() as client:
            response = await client.post(
                endpoint,
                content=body,
                headers=headers,
            )

            response.raise_for_status()
            return response.json()       