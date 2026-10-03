from __future__ import annotations

import json
import logging
from typing import Any

import httpx

logger = logging.getLogger("YourOwnVision.database")


class DatabaseError(RuntimeError):
    pass


class DatabaseNotFound(DatabaseError):
    pass


class DatabaseConflict(DatabaseError):
    pass


class Database:
    """Small async Supabase REST/RPC boundary.

    Supabase is the source of truth. Render memory contains only the HTTP
    client and short-lived asyncio locks; no game state is cached in RAM.
    """

    def __init__(self, base_url: str, service_key: str, *, timeout: float = 15.0):
        if not base_url or not service_key:
            raise ValueError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required")
        self.base_url = base_url.rstrip("/")
        if self.base_url.endswith("/rest/v1"):
            self.base_url = self.base_url[:-8].rstrip("/")
        self.headers = {
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
            "Content-Type": "application/json",
        }
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=6.0),
            headers=self.headers,
        )
        self.closed = False

    async def close(self) -> None:
        if not self.closed:
            self.closed = True
            await self.client.aclose()

    async def _request(self, method: str, url: str, *, params=None, body=None, prefer=None):
        if self.closed:
            raise DatabaseError("Database client is closed")
        headers = {"Prefer": prefer} if prefer else {}
        try:
            response = await self.client.request(method, url, params=params, json=body, headers=headers)
        except httpx.TimeoutException as exc:
            raise DatabaseError("Supabase request timed out") from exc
        except httpx.HTTPError as exc:
            raise DatabaseError(f"Supabase HTTP error: {exc}") from exc

        if response.status_code >= 400:
            text = response.text
            logger.error("Supabase %s %s -> %s: %s", method, url, response.status_code, text)
            if response.status_code == 404:
                raise DatabaseNotFound(text or "Supabase resource not found")
            if response.status_code in (409, 412):
                raise DatabaseConflict(text or "Supabase state conflict")
            raise DatabaseError(f"Supabase error {response.status_code}: {text}")

        if not response.content:
            return None
        try:
            return response.json()
        except json.JSONDecodeError:
            return response.text

    async def request(self, method: str, table: str, *, params=None, json=None, prefer=None):
        return await self._request(method, f"{self.base_url}/rest/v1/{table}", params=params, body=json, prefer=prefer)

    async def rpc(self, function_name: str, payload: dict[str, Any] | None = None):
        return await self._request("POST", f"{self.base_url}/rest/v1/rpc/{function_name}", body=payload or {}, prefer="return=representation")
