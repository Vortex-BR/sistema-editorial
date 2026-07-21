from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from app.db.models import CredentialProvider
from app.services.credential_rotation import (
    CredentialRotationError,
    CredentialRotationService,
)
from app.services.vault import CredentialVault


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Session:
    def __init__(self, rows):
        self.rows = rows
        self.flush_count = 0

    async def scalars(self, _statement):
        return _Scalars(self.rows)

    async def flush(self):
        self.flush_count += 1


def _row(provider, ciphertext):
    return SimpleNamespace(provider=provider, encrypted_value=ciphertext)


@pytest.mark.asyncio
async def test_rotation_dry_run_does_not_mutate_rows():
    old_key = Fernet.generate_key().decode()
    new_key = Fernet.generate_key().decode()
    ciphertext = Fernet(old_key.encode()).encrypt(b"secret")
    row = _row(CredentialProvider.openai, ciphertext)
    session = _Session([row])

    result = await CredentialRotationService().execute(
        session,
        dry_run=True,
        vault=CredentialVault(master_keys=[new_key, old_key]),
    )

    assert result.pending_rotation == 1
    assert result.rotated == 0
    assert row.encrypted_value == ciphertext
    assert session.flush_count == 0


@pytest.mark.asyncio
async def test_rotation_reencrypts_all_pending_rows_atomically():
    old_key = Fernet.generate_key().decode()
    new_key = Fernet.generate_key().decode()
    rows = [
        _row(
            CredentialProvider.openai,
            Fernet(old_key.encode()).encrypt(b"openai-secret"),
        ),
        _row(
            CredentialProvider.serper,
            Fernet(old_key.encode()).encrypt(b"serper-secret"),
        ),
    ]
    session = _Session(rows)
    vault = CredentialVault(master_keys=[new_key, old_key])

    result = await CredentialRotationService().execute(
        session,
        dry_run=False,
        vault=vault,
    )

    assert result.rotated == 2
    assert result.pending_rotation == 2
    assert session.flush_count == 1
    assert [vault.decrypt(row.encrypted_value) for row in rows] == [
        "openai-secret",
        "serper-secret",
    ]
    assert all(vault.is_encrypted_with_primary(row.encrypted_value) for row in rows)


@pytest.mark.asyncio
async def test_rotation_fails_before_mutation_when_any_credential_is_undecryptable():
    old_key = Fernet.generate_key().decode()
    new_key = Fernet.generate_key().decode()
    unknown_key = Fernet.generate_key().decode()
    valid = Fernet(old_key.encode()).encrypt(b"valid")
    invalid = Fernet(unknown_key.encode()).encrypt(b"unknown")
    rows = [
        _row(CredentialProvider.openai, valid),
        _row(CredentialProvider.gemini, invalid),
    ]
    session = _Session(rows)

    with pytest.raises(CredentialRotationError, match="gemini"):
        await CredentialRotationService().execute(
            session,
            dry_run=False,
            vault=CredentialVault(master_keys=[new_key, old_key]),
        )

    assert rows[0].encrypted_value == valid
    assert rows[1].encrypted_value == invalid
    assert session.flush_count == 0
