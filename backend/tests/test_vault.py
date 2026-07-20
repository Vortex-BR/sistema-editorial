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
    monkeypatch.setattr(settings, "credential_master_key", "")

    with pytest.raises(VaultError, match="CREDENTIAL_MASTER_KEY is required"):
        CredentialVault()
