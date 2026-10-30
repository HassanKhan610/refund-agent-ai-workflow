"""API-key auth with role-based permissions."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Request, status

PERMISSIONS = {
    "requester": {"orders:read", "runs:create", "runs:read_own"},
    "operator": {
        "orders:read",
        "runs:create",
        "runs:read_own",
        "runs:read_all",
        "runs:decide",
        "admin",
    },
}


@dataclass(frozen=True)
class Principal:
    key_id: str  # non-secret identifier recorded in traces
    role: str

    def can(self, permission: str) -> bool:
        return permission in PERMISSIONS.get(self.role, set())


def current_principal(request: Request, x_api_key: str | None = Header(default=None)) -> Principal:
    keys: dict[str, str] = request.app.state.container.settings.api_keys
    if not x_api_key or x_api_key not in keys:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "missing or invalid API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    role = keys[x_api_key]
    digest = hashlib.sha256(x_api_key.encode()).hexdigest()[:8]
    return Principal(key_id=f"{role}:{digest}", role=role)


def require(permission: str):
    def dep(p: Principal = Depends(current_principal)) -> Principal:
        if not p.can(permission):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"role '{p.role}' lacks '{permission}'")
        return p

    return dep
