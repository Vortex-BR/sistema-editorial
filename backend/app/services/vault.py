from cryptography.fernet import Fernet, InvalidToken
from app.core.config import settings


class VaultError(RuntimeError):
    pass


class CredentialVault:
    def __init__(self, master_key: str | None = None):
        key = master_key or settings.credential_master_key
        if not key:
            raise VaultError("CREDENTIAL_MASTER_KEY is required")
        try:
            self._fernet = Fernet(key.encode())
        except (ValueError, TypeError) as exc:
            raise VaultError(
                "CREDENTIAL_MASTER_KEY must be a valid Fernet key"
            ) from exc

    def encrypt(self, plaintext: str) -> bytes:
        return self._fernet.encrypt(plaintext.encode())

    def decrypt(self, ciphertext: bytes) -> str:
        try:
            return self._fernet.decrypt(ciphertext).decode()
        except InvalidToken as exc:
            raise VaultError(
                "Credential cannot be decrypted with the active key"
            ) from exc
