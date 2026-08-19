"""A secret that masks itself in `repr` but survives serialisation.

`SecretStr` alone is not enough for anything persisted. Pydantic serialises it as
`**********`, so a model dumped to JSON and written to the keychain stores ten asterisks
where the token should be, and the failure only surfaces on the next API call. This
alias keeps the `repr` masking that Article VII.7 relies on while letting
`model_dump_json` emit the real value for storage.

Use `SecretStr` directly for anything that is never serialised.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import PlainSerializer, SecretStr


def _reveal(secret: SecretStr) -> str:
    return secret.get_secret_value()


StoredSecret = Annotated[
    SecretStr,
    PlainSerializer(_reveal, return_type=str, when_used="json"),
]
