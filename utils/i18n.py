# This file is part of Limey.
# Copyright (c) 2025-Present Limey
#
# Limey is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# You should have received a copy of the GNU General Public License
# along with Limey. If not, see <https://www.gnu.org/licenses/>.


"""
Limey i18n — multilingual dashboard support.

Translations are contributed by humans in the dashboard's "Translate" section
(admin only) and stored in the GitHub data repo. The default language is
English, which is also the source language the templates are authored in.

Data-repo layout:
    i18n/catalog.json        # languages, string keys + translations, stale flags
    i18n/source/*.html       # snapshot copies of the templates ("copy of the site")

Change detection:
    Every template's content is hashed (SHA-256). When a template changes, its
    hash no longer matches ``source_hash`` in the catalog. The next sync
    re-extracts the string keys, marks every string whose English text changed
    as "stale" (needs re-translation), and re-uploads the template snapshots.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import time
from html.parser import HTMLParser

from utils.github_data_store import ghd

_log = logging.getLogger(__name__)

DEFAULT_LANGUAGE = "en"

# ── Supported languages ────────────────────────────────────────────────
# English is the source/default language and is always first and never removed.
LANGUAGES = [
    {"code": "en", "name": "English", "native": "English", "flag": "🇬🇧", "rtl": False},
    {"code": "es", "name": "Spanish", "native": "Español", "flag": "🇪🇸", "rtl": False},
    {"code": "fr", "name": "French", "native": "Français", "flag": "🇫🇷", "rtl": False},
    {"code": "de", "name": "German", "native": "Deutsch", "flag": "🇩🇪", "rtl": False},
    {"code": "it", "name": "Italian", "native": "Italiano", "flag": "🇮🇹", "rtl": False},
    {"code": "pt", "name": "Portuguese", "native": "Português", "flag": "🇧🇷", "rtl": False},
    {"code": "nl", "name": "Dutch", "native": "Nederlands", "flag": "🇳🇱", "rtl": False},
    {"code": "pl", "name": "Polish", "native": "Polski", "flag": "🇵🇱", "rtl": False},
    {"code": "ru", "name": "Russian", "native": "Русский", "flag": "🇷🇺", "rtl": False},
    {"code": "uk", "name": "Ukrainian", "native": "Українська", "flag": "🇺🇦", "rtl": False},
    {"code": "cs", "name": "Czech", "native": "Čeština", "flag": "🇨🇿", "rtl": False},
    {"code": "sk", "name": "Slovak", "native": "Slovenčina", "flag": "🇸🇰", "rtl": False},
    {"code": "sl", "name": "Slovenian", "native": "Slovenščina", "flag": "🇸🇮", "rtl": False},
    {"code": "hr", "name": "Croatian", "native": "Hrvatski", "flag": "🇭🇷", "rtl": False},
    {"code": "sr", "name": "Serbian", "native": "Српски", "flag": "🇷🇸", "rtl": False},
    {"code": "bg", "name": "Bulgarian", "native": "Български", "flag": "🇧🇬", "rtl": False},
    {"code": "ro", "name": "Romanian", "native": "Română", "flag": "🇷🇴", "rtl": False},
    {"code": "hu", "name": "Hungarian", "native": "Magyar", "flag": "🇭🇺", "rtl": False},
    {"code": "el", "name": "Greek", "native": "Ελληνικά", "flag": "🇬🇷", "rtl": False},
    {"code": "tr", "name": "Turkish", "native": "Türkçe", "flag": "🇹🇷", "rtl": False},
    {"code": "sv", "name": "Swedish", "native": "Svenska", "flag": "🇸🇪", "rtl": False},
    {"code": "no", "name": "Norwegian", "native": "Norsk", "flag": "🇳🇴", "rtl": False},
    {"code": "da", "name": "Danish", "native": "Dansk", "flag": "🇩🇰", "rtl": False},
    {"code": "fi", "name": "Finnish", "native": "Suomi", "flag": "🇫🇮", "rtl": False},
    {"code": "ar", "name": "Arabic", "native": "العربية", "flag": "🇸🇦", "rtl": True},
    {"code": "he", "name": "Hebrew", "native": "עברית", "flag": "🇮🇱", "rtl": True},
    {"code": "fa", "name": "Persian", "native": "فارسی", "flag": "🇮🇷", "rtl": True},
    {"code": "ur", "name": "Urdu", "native": "اردو", "flag": "🇵🇰", "rtl": True},
    {"code": "hi", "name": "Hindi", "native": "हिन्दी", "flag": "🇮🇳", "rtl": False},
    {"code": "bn", "name": "Bengali", "native": "বাংলা", "flag": "🇧🇩", "rtl": False},
    {"code": "ja", "name": "Japanese", "native": "日本語", "flag": "🇯🇵", "rtl": False},
    {"code": "ko", "name": "Korean", "native": "한국어", "flag": "🇰🇷", "rtl": False},
    {"code": "zh-CN", "name": "Chinese (Simplified)", "native": "简体中文", "flag": "🇨🇳", "rtl": False},
    {"code": "zh-TW", "name": "Chinese (Traditional)", "native": "繁體中文", "flag": "🇹🇼", "rtl": False},
    {"code": "th", "name": "Thai", "native": "ไทย", "flag": "🇹🇭", "rtl": False},
    {"code": "vi", "name": "Vietnamese", "native": "Tiếng Việt", "flag": "🇻🇳", "rtl": False},
    {"code": "id", "name": "Indonesian", "native": "Bahasa Indonesia", "flag": "🇮🇩", "rtl": False},
    {"code": "ms", "name": "Malay", "native": "Bahasa Melayu", "flag": "🇲🇾", "rtl": False},
    {"code": "fil", "name": "Filipino", "native": "Filipino", "flag": "🇵🇭", "rtl": False},
]

LANGUAGE_CODES = [lang["code"] for lang in LANGUAGES]


def language_info(code: str) -> dict | None:
    """Return the registry entry for a language code, or None."""
    for lang in LANGUAGES:
        if lang["code"] == code:
            return lang
    return None


# ── Storage paths ──────────────────────────────────────────────────────

CATALOG_PATH = "i18n/catalog.json"
SOURCE_DIR = "i18n/source"

# Local template path -> repo snapshot name (the "copy of the html site").
SOURCE_TEMPLATES = [
    ("dashboard/templates/index.html", "index.html"),
    ("dashboard/templates/login.html", "login.html"),
    ("dashboard/templates/homepage.html", "homepage.html"),
    ("dashboard/templates/404.html", "404.html"),
]

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read_local(path: str) -> str:
    with open(os.path.join(_PROJECT_ROOT, path), "r", encoding="utf-8") as f:
        return f.read()


# ── Key extraction ─────────────────────────────────────────────────────

_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}


class _Extractor(HTMLParser):
    """Collect `data-i18n*` attributes, using each element's own text (or an
    explicit `data-i18n-fallback`) as the English source string."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.result: dict[str, str] = {}
        self._frames: list[dict] = []

    def _begin(self, tag: str, attrs: list) -> None:
        a = dict(attrs)
        key = a.get("data-i18n") or a.get("data-i18n-ph") or a.get("data-i18n-title")
        self._frames.append({
            "tag": tag,
            "key": key,
            "fallback": a.get("data-i18n-fallback"),
            "placeholder": a.get("placeholder"),
            "text": [],
        })

    def _end(self) -> None:
        if not self._frames:
            return
        frame = self._frames.pop()
        if not frame["key"]:
            return
        text = frame["fallback"] or " ".join("".join(frame["text"]).split())
        if not text and frame.get("placeholder"):
            text = frame["placeholder"].strip()
        self.result[frame["key"]] = text

    def handle_starttag(self, tag: str, attrs: list) -> None:
        self._begin(tag, attrs)
        if tag in _VOID_TAGS:
            self._end()

    def handle_startendtag(self, tag: str, attrs: list) -> None:
        # Self-closing (XHTML-style) element: record and finalize immediately.
        self._begin(tag, attrs)
        self._end()

    def handle_endtag(self, tag: str) -> None:
        self._end()

    def handle_data(self, data: str) -> None:
        for frame in self._frames:
            frame["text"].append(data)


# Matches t('key') / t("key") and t('key', 'fallback') / t("key", "fallback").
_JS_T_RE = re.compile(r"\bt\(\s*([\"'])([^\"']+)\1\s*(?:,\s*([\"'])([^\"']*)\3)?\s*\)")


def extract_keys() -> dict[str, str]:
    """Extract every translatable key from the templates, mapped to its
    current English source string."""
    result: dict[str, str] = {}
    for local_path, _name in SOURCE_TEMPLATES:
        text = _read_local(local_path)

        # Static elements tagged with data-i18n* attributes.
        parser = _Extractor()
        try:
            parser.feed(text)
        except Exception:
            _log.warning("Failed to parse %s for i18n keys", local_path, exc_info=True)
        for key, en in parser.result.items():
            if key and key not in result:
                result[key] = en

        # JS helper calls: t('key', 'English fallback').
        for m in _JS_T_RE.finditer(text):
            key = m.group(2)
            fallback = m.group(4) or ""
            if key and key not in result:
                result[key] = fallback

    # Any key with no English source yet falls back to its own id.
    for key in list(result):
        if not (result[key] or "").strip():
            result[key] = key
    return result


# ── Catalog persistence ────────────────────────────────────────────────

def _empty_catalog() -> dict:
    return {
        "version": 1,
        "default_language": DEFAULT_LANGUAGE,
        "source_hash": "",
        "source_changed_at": 0.0,
        "keys": {},
        "stale": [],
    }


def load_catalog() -> dict:
    """Load the catalog from the data repo, returning a valid empty catalog
    if it doesn't exist yet."""
    data = ghd.read_json(CATALOG_PATH, default=None)
    if not isinstance(data, dict):
        data = _empty_catalog()
    data.setdefault("keys", {})
    data.setdefault("stale", [])
    data.setdefault("default_language", DEFAULT_LANGUAGE)
    data.setdefault("source_hash", "")
    data.setdefault("source_changed_at", 0.0)
    return data


def save_catalog(catalog: dict) -> bool:
    return ghd.write_json(CATALOG_PATH, catalog, message="Update i18n catalog")


def compute_source_hash() -> str:
    """SHA-256 over every template's content (with path delimiters)."""
    h = hashlib.sha256()
    for local_path, _name in SOURCE_TEMPLATES:
        h.update(local_path.encode("utf-8"))
        h.update(b"\x00")
        h.update(_read_local(local_path).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


# Write-attempt cooldown (seconds) so an unreachable data repo doesn't get
# hammered on every request while templates are out of sync.
_sync_state = {"cooldown_until": 0.0}


def _persist_snapshots(catalog: dict) -> bool:
    """Upload the template snapshots + catalog. Returns True if catalog saved."""
    ok = True
    for local_path, name in SOURCE_TEMPLATES:
        try:
            ok = ghd.write_file(
                f"{SOURCE_DIR}/{name}",
                _read_local(local_path),
                message=f"Snapshot i18n source: {name}",
            ) and ok
        except Exception:
            _log.warning("Failed to snapshot i18n source %s", name, exc_info=True)
            ok = False
    try:
        ok = save_catalog(catalog) and ok
    except Exception:
        _log.warning("Failed to save i18n catalog", exc_info=True)
        ok = False
    return ok


def sync_catalog(force: bool = False) -> dict:
    """Reconcile the catalog with the current templates.

    - If the templates haven't changed, returns the stored catalog as-is.
    - Otherwise re-extracts keys, diffs English source strings, marks any
      string whose English changed as stale for every language with an
      existing translation, drops removed keys, and re-uploads snapshots.
    """
    current_hash = compute_source_hash()
    catalog = load_catalog()

    if catalog.get("source_hash") == current_hash and catalog.get("keys"):
        return catalog

    current_keys = extract_keys()
    keys = catalog.setdefault("keys", {})
    stale = set(catalog.get("stale", []))

    for key, en in current_keys.items():
        entry = keys.get(key)
        if not isinstance(entry, dict):
            entry = {}
            keys[key] = entry
        old_en = entry.get(DEFAULT_LANGUAGE)
        entry[DEFAULT_LANGUAGE] = en
        for code in LANGUAGE_CODES:
            entry.setdefault(code, "")
        # English changed and translations exist → they're now out of date.
        if old_en is not None and old_en != "" and old_en != en:
            for code in LANGUAGE_CODES:
                if code != DEFAULT_LANGUAGE and (entry.get(code) or "").strip():
                    stale.add(f"{key}::{code}")

    for key in list(keys.keys()):
        if key not in current_keys:
            del keys[key]
            stale = {s for s in stale if not s.startswith(key + "::")}

    catalog["keys"] = keys
    catalog["stale"] = sorted(stale)
    catalog["source_hash"] = current_hash
    catalog["source_changed_at"] = time.time()
    catalog["default_language"] = DEFAULT_LANGUAGE

    now = time.time()
    if force or now >= _sync_state.get("cooldown_until", 0.0):
        if not _persist_snapshots(catalog):
            _sync_state["cooldown_until"] = now + 60.0

    return catalog


def public_catalog() -> dict:
    """The catalog shape served to the client (no secrets)."""
    catalog = sync_catalog()
    return {
        "default_language": catalog.get("default_language", DEFAULT_LANGUAGE),
        "languages": LANGUAGES,
        "keys": catalog.get("keys", {}),
        "stale": catalog.get("stale", []),
        "source_changed_at": catalog.get("source_changed_at", 0),
    }


def set_translation(key: str, language: str, value: str) -> tuple[bool, str]:
    """Save a human translation for one key/language and clear its stale flag.

    Returns (ok, error_message).
    """
    key = (key or "").strip()
    language = (language or "").strip()
    if not key:
        return False, "Missing string key"
    if language == DEFAULT_LANGUAGE:
        return False, "English is the source language and cannot be edited"
    if language not in LANGUAGE_CODES:
        return False, "Unsupported language"

    catalog = sync_catalog()
    keys = catalog.setdefault("keys", {})
    if key not in keys:
        return False, "Unknown string key"

    value = (value or "").strip()
    keys[key][language] = value
    stale = set(catalog.get("stale", []))
    stale.discard(f"{key}::{language}")
    catalog["stale"] = sorted(stale)

    if not save_catalog(catalog):
        return False, "Failed to save translation"
    return True, ""
