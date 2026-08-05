from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

# Cache for a week. User-entered notes are edited rarely, so the second reader
# of a given note almost always gets a free, instant answer.
CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
CACHE_PREFIX = "mt"

SUPPORTED_LANGUAGES = ("fr", "en")

# Free text from a pharmacy record: a stock movement reason, a note on a
# returned batch. Long enough for a paragraph, short enough that a pasted
# document cannot run up a bill.
MAX_TEXT_LENGTH = 2000

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
ANTHROPIC_TIMEOUT_SECONDS = 20

_LANGUAGE_NAMES = {"fr": "French", "en": "English"}

_SYSTEM_PROMPT = """You are translating free-text notes from a pharmaceutical \
wholesale system used in Burundi. The text is written by pharmacy staff and \
may concern medicines, batches, expiry, stock movements, suppliers, invoices \
or payments.

Rules:
- Return ONLY the translation. No preamble, no explanation, no quotes.
- Preserve product names, dosages, batch numbers, document references and \
numbers exactly as written.
- Keep the register of the original: terse operational notes stay terse.
- If the text is already in the target language, return it unchanged.
- Never add information that is not in the source text."""


class TranslationUnavailable(Exception):
    """
    Raised when no engine could produce a translation.

    Deliberately distinct from "translated to the same string": a reader
    needs to know the difference between "this text is already English" and
    "we could not translate it", because only the second means the content
    on screen may not be what the writer meant.
    """


@dataclass(frozen=True)
class TranslationResult:
    text: str
    engine: str
    cached: bool


def _cache_key(text: str, target_lang: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"{CACHE_PREFIX}:{target_lang}:{digest}"


def _cache_get(key: str) -> dict | None:
    """
    Read through the cache, treating an unreachable Redis as a plain miss.

    The cache saves cost and latency; it is not a dependency of the feature.
    A Redis outage should make translation slower and more expensive, never
    unavailable, so connection errors here are swallowed rather than raised.
    """
    try:
        return cache.get(key)
    except Exception as exc:
        logger.warning("Translation cache unavailable on read: %s", exc)
        return None


def _cache_set(key: str, value: dict) -> None:
    try:
        cache.set(key, value, CACHE_TTL_SECONDS)
    except Exception as exc:
        logger.warning("Translation cache unavailable on write: %s", exc)


def _translate_with_claude(text: str, target_lang: str) -> str | None:
    """
    Preferred engine: handles pharmaceutical vocabulary and the abbreviated
    register of operational notes far better than general-purpose engines.
    Returns None if unconfigured or unavailable, so the caller can fall back.
    """
    api_key = getattr(settings, "ANTHROPIC_API_KEY", "")
    if not api_key:
        return None

    target_name = _LANGUAGE_NAMES.get(target_lang, target_lang)
    try:
        response = requests.post(
            ANTHROPIC_API_URL,
            headers={
                "x-api-key": api_key,
                "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
            json={
                "model": getattr(settings, "TRANSLATION_MODEL", "claude-sonnet-5"),
                "max_tokens": 2000,
                "system": _SYSTEM_PROMPT,
                "messages": [
                    {
                        "role": "user",
                        "content": f"Translate into {target_name}:\n\n{text}",
                    }
                ],
            },
            timeout=ANTHROPIC_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        blocks = response.json().get("content", [])
        parts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
        translated = "".join(parts).strip()
        return translated or None
    except Exception as exc:  # network, auth, quota, malformed response
        logger.warning("Claude translation failed (target=%s): %s", target_lang, exc)
        return None


def _translate_with_google(text: str, target_lang: str) -> str | None:
    """Fallback engine. Requires `deep-translator`; returns None without it."""
    try:
        from deep_translator import GoogleTranslator

        translated = GoogleTranslator(source="auto", target=target_lang).translate(text)
        return (translated or "").strip() or None
    except ImportError:
        logger.warning("deep-translator is not installed; Google fallback unavailable")
        return None
    except Exception as exc:
        logger.warning("Google translation failed (target=%s): %s", target_lang, exc)
        return None


def translate_on_demand(text: str, target_lang: str) -> TranslationResult:
    """
    Translate user-entered text for *display only*.

    This is the read-side path behind the "Translate" control. Nothing here
    writes to a model: the original text remains the record of truth, and a
    machine translation never becomes stored business or audit data.

    Raises TranslationUnavailable if no engine succeeded, so the caller can
    tell the reader plainly rather than showing the untranslated original as
    though it had been translated.
    """
    if target_lang not in SUPPORTED_LANGUAGES:
        raise TranslationUnavailable(f"Unsupported target language: {target_lang}")

    text = (text or "").strip()
    if not text:
        return TranslationResult(text="", engine="noop", cached=False)

    key = _cache_key(text, target_lang)
    hit = _cache_get(key)
    if hit is not None:
        return TranslationResult(text=hit["text"], engine=hit["engine"], cached=True)

    for engine_name, engine in (
        ("claude", _translate_with_claude),
        ("google", _translate_with_google),
    ):
        translated = engine(text, target_lang)
        if translated:
            _cache_set(key, {"text": translated, "engine": engine_name})
            return TranslationResult(text=translated, engine=engine_name, cached=False)

    raise TranslationUnavailable("No translation engine was able to handle the request")


def translate_text(text: str, target_lang: str) -> str:
    """
    Best-effort translation that falls back to the original on failure.

    Used by `save()` on the bilingual admin-managed models (category and role
    names, medicine names) where a missing translation must not block a save.
    Kept deliberately separate from `translate_on_demand`: silently returning
    the source text is the right behaviour when seeding a `name_en` column,
    and the wrong behaviour when a reader has asked to be shown a translation.
    """
    if not text or not isinstance(text, str):
        return text

    text = text.strip()
    if not text:
        return text

    try:
        return translate_on_demand(text, target_lang).text
    except TranslationUnavailable:
        return text
