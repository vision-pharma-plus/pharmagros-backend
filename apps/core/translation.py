from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

def translate_text(text: str, target_lang: str) -> str:
    """
    Translate text to the target language (usually 'fr' or 'en').
    Returns the original text if deep-translator is not installed,
    if there's a network error, or if text is empty.
    """
    if not text or not isinstance(text, str):
        return text

    # Strip whitespace
    text = text.strip()
    if not text:
        return text

    try:
        from deep_translator import GoogleTranslator
        # GoogleTranslator auto-detects source language
        translated = GoogleTranslator(source="auto", target=target_lang).translate(text)
        if translated:
            return translated
    except ImportError:
        logger.warning("deep-translator is not installed; skipping translation")
    except Exception as e:
        logger.warning(f"Translation failed for '{text}' to '{target_lang}': {e}")

    return text
