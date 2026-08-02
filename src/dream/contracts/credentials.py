"""Typed seam for brokered credentials.

The contract deliberately exposes grants and opaque leases, never secret values. Concrete
credential stores and delivery mechanisms belong to the host application.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable


class CredentialDelivery(StrEnum):
    ENVIRONMENT = "environment"
    BROKER = "broker"


class CredentialGrantMode(StrEnum):
    ONCE = "once"
    STANDING = "standing"


class CredentialGrantStatus(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"
    USED = "used"


class CredentialRequestStatus(StrEnum):
    GRANTED = "granted"
    APPROVAL_REQUIRED = "approval_required"


class CredentialHttpMethod(StrEnum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"
    HEAD = "HEAD"
    OPTIONS = "OPTIONS"


@dataclass(frozen=True)
class CredentialInjection:
    header: str = "Authorization"
    scheme: str = "Bearer"

    def __post_init__(self) -> None:
        if not self.header.strip():
            raise ValueError("credential injection header must not be empty")


@dataclass(frozen=True)
class CredentialProxyHeader:
    name: str
    value: str


@dataclass(frozen=True)
class CredentialProxyRequest:
    method: CredentialHttpMethod
    url: str
    headers: tuple[CredentialProxyHeader, ...] = ()
    body: str | None = None


@dataclass(frozen=True)
class CredentialProxyResponse:
    status: int
    body: str
    headers: tuple[CredentialProxyHeader, ...] = ()


@runtime_checkable
class CredentialEnvironmentTarget(Protocol):
    async def set_credential(self, name: str, value: str) -> None: ...


@dataclass(frozen=True)
class CredentialName:
    value: str

    def __post_init__(self) -> None:
        if not self.value.strip():
            raise ValueError("credential name must not be empty")


@dataclass(frozen=True)
class CredentialOwner:
    value: str

    def __post_init__(self) -> None:
        if not self.value.strip():
            raise ValueError("owner must not be empty")


@dataclass(frozen=True)
class CredentialAskId:
    value: str


@dataclass(frozen=True)
class CredentialGrantId:
    value: str


@dataclass(frozen=True)
class CredentialSession:
    value: str

    def __post_init__(self) -> None:
        if not self.value.strip():
            raise ValueError("session must not be empty")


@dataclass(frozen=True)
class CredentialRequest:
    credential: CredentialName
    owner: CredentialOwner
    audience: CredentialOwner
    purpose: str
    mode: CredentialGrantMode
    delivery: CredentialDelivery
    environment_key: str | None = None
    allowed_host: str | None = None
    injection: CredentialInjection = field(default_factory=CredentialInjection)
    allowed_methods: tuple[CredentialHttpMethod, ...] = (CredentialHttpMethod.GET,)
    allowed_path_prefixes: tuple[str, ...] = ("/",)
    requested_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not self.purpose.strip():
            raise ValueError("purpose must not be empty")
        if any(not prefix.startswith("/") for prefix in self.allowed_path_prefixes):
            raise ValueError("allowed path prefixes must be absolute paths")
        if self.delivery is CredentialDelivery.ENVIRONMENT and not self.environment_key:
            raise ValueError("environment delivery requires an environment key")
        if self.delivery is CredentialDelivery.BROKER and self.environment_key is not None:
            raise ValueError("broker delivery must not carry an environment key")


@dataclass(frozen=True)
class CredentialAsk:
    id: CredentialAskId
    request: CredentialRequest
    expires_at: datetime


@dataclass(frozen=True)
class CredentialUse:
    session: CredentialSession
    used_at: datetime


@dataclass(frozen=True)
class CredentialGrant:
    id: CredentialGrantId
    request: CredentialRequest
    status: CredentialGrantStatus
    granted_at: datetime
    expires_at: datetime | None = None
    uses: tuple[CredentialUse, ...] = ()


@dataclass(frozen=True)
class CredentialLease:
    """Opaque materialization handle; it intentionally has no secret field."""

    grant: CredentialGrantId
    session: CredentialSession
    delivery: CredentialDelivery
    opaque_handle: str
    env_key: str | None = None


@dataclass(frozen=True)
class CredentialRequestResult:
    status: CredentialRequestStatus
    grant: CredentialGrant | None = None
    ask: CredentialAsk | None = None


@runtime_checkable
class CredentialBrokerPort(Protocol):
    async def request_access(self, request: CredentialRequest) -> CredentialRequestResult: ...

    async def approve(
        self,
        ask: CredentialAskId,
        owner: CredentialOwner,
        mode: CredentialGrantMode,
    ) -> CredentialGrant: ...

    async def materialize(
        self,
        grant: CredentialGrantId,
        session: CredentialSession,
    ) -> CredentialLease: ...

    async def revoke(
        self,
        grant: CredentialGrantId,
        owner: CredentialOwner,
    ) -> bool: ...

    async def proxy(
        self,
        lease: CredentialLease,
        request: CredentialProxyRequest,
    ) -> CredentialProxyResponse: ...

    async def inject_environment(
        self,
        lease: CredentialLease,
        target: CredentialEnvironmentTarget,
    ) -> None: ...


__all__ = [
    "CredentialAsk",
    "CredentialAskId",
    "CredentialBrokerPort",
    "CredentialDelivery",
    "CredentialEnvironmentTarget",
    "CredentialGrant",
    "CredentialGrantId",
    "CredentialGrantMode",
    "CredentialGrantStatus",
    "CredentialHttpMethod",
    "CredentialInjection",
    "CredentialLease",
    "CredentialName",
    "CredentialOwner",
    "CredentialProxyHeader",
    "CredentialProxyRequest",
    "CredentialProxyResponse",
    "CredentialRequest",
    "CredentialRequestResult",
    "CredentialRequestStatus",
    "CredentialSession",
    "CredentialUse",
]
