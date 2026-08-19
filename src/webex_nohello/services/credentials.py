"""Credential persistence in the OS keychain, per Article VIII.4.

Tokens and the client secret are never written to a config file, a dotfile, a log or
an environment variable. The whole record is one keychain entry, so a partial write
cannot leave an access token without its refresh token.
"""

from __future__ import annotations

from typing import Protocol

import keyring
from keyring.errors import KeyringError, PasswordDeleteError
from pydantic import ValidationError

from webex_nohello.models.auth.stored_credentials import StoredCredentials
from webex_nohello.models.errors.credential_storage_error import CredentialStorageError

SERVICE_NAME = "webex-nohello"
ENTRY_NAME = "credentials"


class CredentialStore(Protocol):
    def load(self) -> StoredCredentials | None: ...

    def save(self, credentials: StoredCredentials) -> None: ...

    def delete(self) -> None: ...


class KeyringCredentialStore:
    def __init__(self, service_name: str = SERVICE_NAME, entry_name: str = ENTRY_NAME) -> None:
        self._service_name = service_name
        self._entry_name = entry_name

    def load(self) -> StoredCredentials | None:
        try:
            raw = keyring.get_password(self._service_name, self._entry_name)
        except KeyringError as exc:
            raise CredentialStorageError(
                f"Could not read credentials from the OS keychain: {exc}",
                remediation=(
                    "Check a keyring backend is available. On a headless Linux host you "
                    "may need to install and unlock gnome-keyring, or use keyrings.alt."
                ),
            ) from exc

        if raw is None:
            return None

        try:
            return StoredCredentials.model_validate_json(raw)
        except ValidationError as exc:
            raise CredentialStorageError(
                f"Stored credentials are not in a format this version understands: {exc}",
                remediation="Run 'webex-nohello auth logout' then 'webex-nohello auth login'.",
            ) from exc

    def save(self, credentials: StoredCredentials) -> None:
        try:
            keyring.set_password(
                self._service_name, self._entry_name, credentials.model_dump_json()
            )
        except KeyringError as exc:
            raise CredentialStorageError(
                f"Could not write credentials to the OS keychain: {exc}",
                remediation="Check a keyring backend is available and unlocked.",
            ) from exc

    def delete(self) -> None:
        try:
            keyring.delete_password(self._service_name, self._entry_name)
        except PasswordDeleteError:
            # Already absent. Article VIII.7 requires logout succeed unconditionally.
            pass
        except KeyringError as exc:
            raise CredentialStorageError(
                f"Could not delete credentials from the OS keychain: {exc}",
                remediation="Remove the 'webex-nohello' entry with your keychain tool.",
            ) from exc
