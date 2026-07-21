from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Credential
from app.services.vault import CredentialVault, VaultError


class CredentialRotationError(RuntimeError):
    pass


@dataclass(frozen=True)
class CredentialRotationResult:
    key_count: int
    total_credentials: int
    already_primary: int
    pending_rotation: int
    rotated: int
    dry_run: bool
    providers: list[str] = field(default_factory=list)


class CredentialRotationService:
    """Rotate every stored provider credential in one database transaction."""

    async def execute(
        self,
        db: AsyncSession,
        *,
        dry_run: bool = True,
        vault: CredentialVault | None = None,
    ) -> CredentialRotationResult:
        active_vault = vault or CredentialVault()
        rows = list(
            (
                await db.scalars(
                    select(Credential)
                    .order_by(Credential.provider)
                    .with_for_update()
                )
            ).all()
        )
        already_primary = 0
        pending: list[tuple[Credential, str]] = []
        failures: list[str] = []

        for row in rows:
            provider = str(getattr(row.provider, "value", row.provider))
            try:
                plaintext = active_vault.decrypt(bytes(row.encrypted_value))
                if active_vault.needs_rotation(bytes(row.encrypted_value)):
                    pending.append((row, plaintext))
                else:
                    already_primary += 1
            except VaultError:
                failures.append(provider)

        if failures:
            raise CredentialRotationError(
                "Credential keyring cannot decrypt providers: "
                + ", ".join(sorted(failures))
            )

        rotated = 0
        if not dry_run:
            for row, plaintext in pending:
                rotated_value = active_vault.rotate(bytes(row.encrypted_value))
                if active_vault.decrypt(rotated_value) != plaintext:
                    raise CredentialRotationError(
                        "Credential rotation verification failed before commit"
                    )
                if not active_vault.is_encrypted_with_primary(rotated_value):
                    raise CredentialRotationError(
                        "Credential was not encrypted with the primary key"
                    )
                row.encrypted_value = rotated_value
                rotated += 1
            await db.flush()

        return CredentialRotationResult(
            key_count=active_vault.key_count,
            total_credentials=len(rows),
            already_primary=already_primary,
            pending_rotation=len(pending),
            rotated=rotated,
            dry_run=dry_run,
            providers=[
                str(getattr(row.provider, "value", row.provider)) for row, _ in pending
            ],
        )
