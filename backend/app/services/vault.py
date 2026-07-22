from __future__ import annotations

from collections.abc import Iterable

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.config import settings


class VaultError(RuntimeError):
    pass


def _normalize_keys(
    master_key: str | None = None,
    master_keys: Iterable[str] | str | None = None,
) -> tuple[str, ...]:
    values: list[str] = []
    if isinstance(master_keys, str):
        values.extend(master_keys.replace("\r", "\n").replace(",", "\n").splitlines())
    elif master_keys is not None:
        values.extend(str(item) for item in master_keys)
    elif master_key:
        values.append(master_key)
    else:
        values.extend(settings.credential_keyring)

    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = str(raw or "").strip()
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    if not result:
        raise VaultError(
            "CREDENTIAL_MASTER_KEYS or CREDENTIAL_MASTER_KEY is required"
        )
    return tuple(result)


class CredentialVault:
    """Versioned Fernet keyring.

    The first key encrypts every new value. All configured keys can decrypt,
    which allows a rolling deployment where old and new application replicas
    overlap safely during key rotation.
    """

    def __init__(
        self,
        master_key: str | None = None,
        *,
        master_keys: Iterable[str] | str | None = None,
    ):
        keys = _normalize_keys(master_key, master_keys)
        try:
            self._fernets = tuple(Fernet(key.encode()) for key in keys)
        except (ValueError, TypeError) as exc:
            raise VaultError("Every credential master key must be a valid Fernet key") from exc
        self._primary = self._fernets[0]
        self._multi = MultiFernet(list(self._fernets))

    @property
    def key_count(self) -> int:
        return len(self._fernets)

    def encrypt(self, plaintext: str) -> bytes:
        return self._primary.encrypt(plaintext.encode())

    def decrypt(self, ciphertext: bytes) -> str:
        try:
            return self._multi.decrypt(bytes(ciphertext)).decode()
        except InvalidToken as exc:
            raise VaultError(
                "Credential cannot be decrypted with the configured keyring"
            ) from exc
        except (TypeError, UnicodeDecodeError) as exc:
            raise VaultError("Credential ciphertext is invalid") from exc

    def is_encrypted_with_primary(self, ciphertext: bytes) -> bool:
        try:
            self._primary.decrypt(bytes(ciphertext))
            return True
        except (InvalidToken, TypeError):
            return False

    def needs_rotation(self, ciphertext: bytes) -> bool:
        # First verify the keyring can decrypt the value. A corrupted credential
        # must never be silently rewritten.
        self.decrypt(ciphertext)
        return not self.is_encrypted_with_primary(ciphertext)

    def rotate(self, ciphertext: bytes) -> bytes:
        """Re-encrypt a token with the primary key while preserving its timestamp."""
        try:
            if not self.needs_rotation(ciphertext):
                return bytes(ciphertext)
            return self._multi.rotate(bytes(ciphertext))
        except InvalidToken as exc:
            raise VaultError(
                "Credential cannot be rotated with the configured keyring"
            ) from exc
