import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx

from app.modules.gateway.snapshots import SnapshotStore

logger = logging.getLogger(__name__)


class KeepRiskConfigError(Exception):
    pass


class KeepRiskTransportError(Exception):
    pass


class KeepRiskResponseError(Exception):
    def __init__(self, message: str, *, raw_ref: str | None = None) -> None:
        self.raw_ref = raw_ref
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class GatewayEnvelope:
    code: str
    message: str
    data: Any
    raw_bytes: bytes
    raw_ref: str | None
    order_no: str | None
    charge: bool
    fetched_at: datetime


class KeepRiskClient:
    def __init__(
        self,
        *,
        base_url: str | None,
        app_code: str | None,
        timeout_seconds: float,
        snapshot_store: SnapshotStore,
        max_response_bytes: int = 20 * 1024 * 1024,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url
        self.app_code = app_code
        self.timeout_seconds = timeout_seconds
        self.snapshot_store = snapshot_store
        self.max_response_bytes = max_response_bytes
        self.transport = transport

    def _require_config(self) -> tuple[str, str]:
        if not self.base_url:
            raise KeepRiskConfigError("KEEPRISK_API_BASE_URL is not configured")
        if not self.app_code:
            raise KeepRiskConfigError("KEEPRISK_APP_CODE is not configured")
        return self.base_url, self.app_code

    async def query(self, pcode: str, body: dict[str, Any]) -> GatewayEnvelope:
        base_url, app_code = self._require_config()
        headers = {
            "Content-Type": "application/json;charset=utf-8",
            "appCode": app_code,
            "pcode": pcode,
        }
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout_seconds, connect=10.0),
                transport=self.transport,
                trust_env=False,
            ) as client:
                response = await client.post(base_url, json=body, headers=headers)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise KeepRiskTransportError(f"KeepRisk request failed: {exc}") from exc

        raw = response.content
        if len(raw) > self.max_response_bytes:
            raise KeepRiskResponseError(
                f"KeepRisk response exceeds {self.max_response_bytes} bytes"
            )
        raw_ref = await self.snapshot_store.put_bytes(
            f"keeprisk/{pcode}/{datetime.now(UTC).strftime('%Y%m%d')}/{uuid4().hex}.json",
            raw,
        )
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise KeepRiskResponseError("KeepRisk returned invalid JSON", raw_ref=raw_ref) from exc
        if not isinstance(payload, dict):
            raise KeepRiskResponseError("KeepRisk returned a non-object payload", raw_ref=raw_ref)

        code = str(payload.get("code", ""))
        logger.info("keeprisk response pcode=%s code=%s", pcode, code)
        return GatewayEnvelope(
            code=code,
            message=str(payload.get("message", "")),
            data=payload.get("data"),
            raw_bytes=raw,
            raw_ref=raw_ref,
            order_no=payload.get("orderNo"),
            charge=bool(payload.get("charge", False)),
            fetched_at=datetime.now(UTC),
        )
