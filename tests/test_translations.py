"""All translation files must contain the same keys and placeholders as en.json."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

DIR = Path(__file__).parent.parent / "custom_components" / "zendure_ha" / "translations"


def _flat(data: dict, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in data.items():
        if isinstance(value, dict):
            out.update(_flat(value, f"{prefix}{key}."))
        else:
            out[f"{prefix}{key}"] = value
    return out


def _load(lang: str) -> dict[str, str]:
    return _flat(json.loads((DIR / f"{lang}.json").read_text(encoding="utf-8")))


@pytest.mark.parametrize("lang", ["nl", "de", "fr", "pl"])
def test_translation_is_complete(lang: str) -> None:
    en, other = _load("en"), _load(lang)
    assert sorted(set(en) - set(other)) == []
    assert sorted(set(other) - set(en)) == []
    for key, text in en.items():
        assert sorted(re.findall(r"\{[^}]*\}", text)) == sorted(re.findall(r"\{[^}]*\}", other[key])), key


def test_new_options_and_issues_are_translated() -> None:
    en = _load("en")
    for key in ("options.step.lifetime.title", "options.step.device_ips.title", "options.error.invalid_address", "issues.zensdk_unreachable.title"):
        assert key in en
