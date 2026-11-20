"""Search variants for text typed with the wrong Russian/English keyboard layout."""

from __future__ import annotations


_EN = "`qwertyuiop[]asdfghjkl;'zxcvbnm,."
_RU = "ёйцукенгшщзхъфывапролджэячсмитьбю"
_EN_TO_RU = str.maketrans(_EN + _EN.upper(), _RU + _RU.upper())
_RU_TO_EN = str.maketrans(_RU + _RU.upper(), _EN + _EN.upper())


def variants(value: str | None) -> list[str]:
    """Return the original query and both keyboard-layout interpretations."""
    source = (value or "").strip()
    result: list[str] = []
    for candidate in (source, source.translate(_EN_TO_RU), source.translate(_RU_TO_EN)):
        if candidate and candidate not in result:
            result.append(candidate)
    return result
