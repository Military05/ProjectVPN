from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from shop_bot.core.config import Settings
from shop_bot.infrastructure.nodes.auth import build_signed_headers, json_bytes


class NodeApiClient:
    def __init__(self, settings: Settings):
        self._settings = settings

    async def get_health(self, *, node: Mapping[str, Any], credential: Mapping[str, Any]) -> dict[str, Any]:
        return await self._request(
            node=node,
            credential=credential,
            method="GET",
            path="/agent/health",
            payload=None,
            idempotency_key=f"health:{node['node_id']}",
        )

    async def get_capabilities(self, *, node: Mapping[str, Any], credential: Mapping[str, Any]) -> dict[str, Any]:
        return await self._request(
            node=node,
            credential=credential,
            method="GET",
            path="/agent/capabilities",
            payload=None,
            idempotency_key=f"capabilities:{node['node_id']}",
        )

    async def get_status(self, *, node: Mapping[str, Any], credential: Mapping[str, Any]) -> dict[str, Any]:
        return await self._request(
            node=node,
            credential=credential,
            method="GET",
            path="/agent/status",
            payload=None,
            idempotency_key=f"status:{node['node_id']}",
        )

    async def provision_client(
        self,
        *,
        node: Mapping[str, Any],
        credential: Mapping[str, Any],
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        return await self._request(
            node=node,
            credential=credential,
            method="POST",
            path="/agent/clients/provision",
            payload=payload,
            idempotency_key=idempotency_key,
        )

    async def revoke_client(
        self,
        *,
        node: Mapping[str, Any],
        credential: Mapping[str, Any],
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        return await self._request(
            node=node,
            credential=credential,
            method="POST",
            path="/agent/clients/revoke",
            payload=payload,
            idempotency_key=idempotency_key,
        )

    async def _request(
        self,
        *,
        node: Mapping[str, Any],
        credential: Mapping[str, Any],
        method: str,
        path: str,
        payload: Mapping[str, Any] | None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        body = json_bytes(payload)
        headers = build_signed_headers(
            node_id=str(node["node_key"]),
            key_id=str(credential["key_id"]),
            secret=str(credential["shared_secret"]),
            method=method,
            path=path,
            body=body,
            idempotency_key=idempotency_key,
        )
        if body:
            headers["Content-Type"] = "application/json"
        verify = self._settings.node_http_verify_tls
        async with httpx.AsyncClient(
            base_url=str(node["api_base_url"]).rstrip("/"),
            timeout=self._settings.node_request_timeout_seconds,
            verify=verify,
        ) as client:
            response = await client.request(method=method, url=path, content=body or None, headers=headers)
            response.raise_for_status()
            if not response.content:
                return {}
            return dict(response.json())
