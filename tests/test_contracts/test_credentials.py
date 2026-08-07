"""Typed credential-broker contract tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from dream.contracts.credentials import (
    CredentialDelivery,
    CredentialGrantMode,
    CredentialHttpMethod,
    CredentialName,
    CredentialOwner,
    CredentialRequest,
)


def test_request_is_typed_and_never_contains_secret_material() -> None:
    request = CredentialRequest(
        credential=CredentialName("github"),
        owner=CredentialOwner("employee:ada"),
        audience=CredentialOwner("employee:backend"),
        purpose="create the release pull request",
        mode=CredentialGrantMode.ONCE,
        delivery=CredentialDelivery.BROKER,
        allowed_methods=(CredentialHttpMethod.GET, CredentialHttpMethod.POST),
        allowed_path_prefixes=("/repos/acme/app",),
        requested_at=datetime.now(UTC),
    )

    assert request.credential.value == "github"
    assert request.allowed_methods == (CredentialHttpMethod.GET, CredentialHttpMethod.POST)
    assert "secret" not in request.__dataclass_fields__


def test_empty_identity_and_purpose_are_rejected() -> None:
    with pytest.raises(ValueError, match="credential name"):
        CredentialName("")
    with pytest.raises(ValueError, match="owner"):
        CredentialOwner(" ")
    with pytest.raises(ValueError, match="purpose"):
        CredentialRequest(
            credential=CredentialName("github"),
            owner=CredentialOwner("employee:ada"),
            audience=CredentialOwner("employee:backend"),
            purpose="",
            mode=CredentialGrantMode.ONCE,
            delivery=CredentialDelivery.BROKER,
            requested_at=datetime.now(UTC),
        )
