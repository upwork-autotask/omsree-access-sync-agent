"""HTTP client for the OmSree CRM sync endpoints.

Implements the three endpoints from docs/implementation-plan.md s.3:
  GET  /api/sync/outbound/?since=<cursor>&tables=...
  POST /api/sync/inbound/
  POST /api/sync/ack/

All requests carry `Authorization: Bearer <agent-token>`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

try:
    import requests
except ImportError:  # pragma: no cover - requests is a hard runtime dep
    requests = None  # type: ignore[assignment]


class CrmError(Exception):
    """Raised when the CRM returns an error or is unreachable."""


@dataclass
class Batch:
    """One table's worth of rows from an outbound response."""

    table: str
    key: str
    rows: list[dict[str, Any]]

    @classmethod
    def from_json(cls, obj: dict[str, Any]) -> "Batch":
        return cls(table=obj["table"], key=obj["key"], rows=list(obj.get("rows", [])))


@dataclass
class OutboundResponse:
    cursor: str | None
    batches: list[Batch]
    has_more: bool

    @classmethod
    def from_json(cls, obj: dict[str, Any]) -> "OutboundResponse":
        return cls(
            cursor=obj.get("cursor"),
            batches=[Batch.from_json(b) for b in obj.get("batches", [])],
            has_more=bool(obj.get("has_more", False)),
        )


class CrmClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        session: Any | None = None,
        timeout: float = 30.0,
    ) -> None:
        if session is None:
            if requests is None:
                raise CrmError("the 'requests' package is required; pip install -r requirements.txt")
            session = requests.Session()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session = session
        self._session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "User-Agent": "omsree-access-sync-agent",
            }
        )

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            resp = self._session.request(method, self._url(path), timeout=self.timeout, **kwargs)
        except Exception as exc:  # network errors, DNS, timeouts
            raise CrmError(f"{method} {path} failed: {exc}") from exc
        if resp.status_code >= 400:
            body = ""
            try:
                body = resp.text[:500]
            except Exception:  # pragma: no cover - defensive
                pass
            raise CrmError(f"{method} {path} -> HTTP {resp.status_code}: {body}")
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError as exc:
            raise CrmError(f"{method} {path} returned non-JSON body") from exc

    def fetch_outbound(self, since: str | None, tables: Sequence[str] | None = None) -> OutboundResponse:
        params: dict[str, str] = {}
        if since:
            params["since"] = since
        if tables:
            params["tables"] = ",".join(tables)
        return OutboundResponse.from_json(self._request("GET", "/api/sync/outbound/", params=params))

    def post_inbound(self, batches: list[dict[str, Any]]) -> dict[str, Any]:
        return self._request("POST", "/api/sync/inbound/", json={"batches": batches})

    def post_ack(self, cursor: str, results: list[dict[str, Any]]) -> dict[str, Any]:
        return self._request("POST", "/api/sync/ack/", json={"cursor": cursor, "results": results})
