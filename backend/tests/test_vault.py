import pytest
from cryptography.fernet import Fernet

from app.core.config import settings
from app.services.vault import CredentialVault, VaultError


def test_vault_roundtrip_and_no_plaintext():
    vault = CredentialVault(Fernet.generate_key().decode())
    ciphertext = vault.encrypt("secret-api-key")
    assert b"secret-api-key" not in ciphertext
    assert vault.decrypt(ciphertext) == "secret-api-key"


def test_vault_requires_master_key(monkeypatch):
    monkeypatch.delenv("CREDENTIAL_MASTER_KEY", raising=False)
    monkeypatch.delenv("CREDENTIAL_MASTER_KEYS", raising=False)
    monkeypatch.setattr(settings, "credential_master_key", "")
    monkeypatch.setattr(settings, "credential_master_keys", "")

    with pytest.raises(VaultError, match="CREDENTIAL_MASTER_KEYS or CREDENTIAL_MASTER_KEY"):
        CredentialVault()


def test_multifernet_keyring_decrypts_old_and_encrypts_with_primary():
    old_key = Fernet.generate_key().decode()
    new_key = Fernet.generate_key().decode()
    old_ciphertext = Fernet(old_key.encode()).encrypt(b"provider-secret")

    vault = CredentialVault(master_keys=[new_key, old_key])

    assert vault.decrypt(old_ciphertext) == "provider-secret"
    assert vault.needs_rotation(old_ciphertext) is True
    rotated = vault.rotate(old_ciphertext)
    assert rotated != old_ciphertext
    assert vault.decrypt(rotated) == "provider-secret"
    assert vault.is_encrypted_with_primary(rotated) is True
    assert Fernet(new_key.encode()).decrypt(rotated) == b"provider-secret"


def test_rotation_is_idempotent_for_primary_ciphertext():
    primary = Fernet.generate_key().decode()
    secondary = Fernet.generate_key().decode()
    vault = CredentialVault(master_keys=f"{primary},{secondary}")
    ciphertext = vault.encrypt("secret")

    assert vault.needs_rotation(ciphertext) is False
    assert vault.rotate(ciphertext) == ciphertext


def test_settings_keyring_prefers_plural_and_keeps_legacy_fallback():
    first = Fernet.generate_key().decode()
    second = Fernet.generate_key().decode()
    config = settings.model_copy(
        update={
            "credential_master_keys": f"{first}\n{second}",
            "credential_master_key": second,
        }
    )

    assert config.credential_keyring == (first, second)
