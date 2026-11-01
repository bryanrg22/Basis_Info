"""No secret leaves ``Settings`` through ``repr``, ``str`` or ``model_dump_json``.

Every ``SecretStr`` field is set to its own sentinel, and the connection string (which may
carry a password) too; none of the sentinels appears in the three renderings. The list of
secret fields is read from the model, so a new ``SecretStr`` setting is covered without a
change here, and a secret added as a plain ``str`` fails the companion test.
"""

from __future__ import annotations

from typing import get_args, get_origin

from pydantic import SecretStr

from basis.config import Settings

# Fields that carry a credential or a password-bearing connection string but are not
# SecretStr; each must be excluded from repr.
PLAIN_SENSITIVE = {"database_url"}


def secret_fields() -> list[str]:
    names = []
    for name, field in Settings.model_fields.items():
        annotation = field.annotation
        options = get_args(annotation) if get_origin(annotation) is not None else (annotation,)
        if SecretStr in options:
            names.append(name)
    return names


def test_every_secret_sounding_field_is_a_secretstr_or_hidden() -> None:
    plain = [
        name
        for name in Settings.model_fields
        if name.endswith(("_key", "_token", "_secret", "_password")) and name not in secret_fields()
    ]
    assert plain == [], plain
    for name in PLAIN_SENSITIVE:
        assert Settings.model_fields[name].repr is False, name


def test_sentinels_never_appear_in_repr_str_or_dump_json() -> None:
    sentinels = {name: f"sentinel-{name}-9f8e7d" for name in secret_fields()}
    values: dict[str, object] = dict(sentinels)
    values["database_url"] = "postgresql+asyncpg://basis:sentinel-db-password-1a2b@db/basis"
    settings = Settings(_env_file=None, **values)  # type: ignore[call-arg]
    for rendered in (repr(settings), str(settings)):
        for sentinel in sentinels.values():
            assert sentinel not in rendered
        assert "sentinel-db-password-1a2b" not in rendered
    dumped = settings.model_dump_json()
    for sentinel in sentinels.values():
        assert sentinel not in dumped
