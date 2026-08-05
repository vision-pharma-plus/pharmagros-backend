"""
Reader-triggered translation of user-entered notes.

The guarantee under test is not translation quality — it is that the feature
stays a *reading aid*: it never writes back, it never presents an untranslated
string as a translation, and it degrades rather than breaks when the cache or
an engine is down.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.urls import reverse

from apps.core.translation import (
    TranslationUnavailable,
    translate_on_demand,
    translate_text,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_translation_cache():
    """Each test starts with a cold cache so hits are never accidental."""
    from django.core.cache import cache

    try:
        cache.clear()
    except Exception:
        pass  # Redis absent locally; the code under test tolerates that.
    yield


# --- service layer ---------------------------------------------------------


def test_empty_text_needs_no_engine():
    with patch("apps.core.translation._translate_with_claude") as claude:
        result = translate_on_demand("   ", "en")
    assert result.text == ""
    claude.assert_not_called()


def test_unsupported_language_is_rejected():
    with pytest.raises(TranslationUnavailable):
        translate_on_demand("Lot périmé", "sw")


def test_claude_is_preferred_over_google():
    with patch("apps.core.translation._translate_with_claude", return_value="Expired batch") as claude, \
         patch("apps.core.translation._translate_with_google") as google:
        result = translate_on_demand("Lot périmé", "en")

    assert result.text == "Expired batch"
    assert result.engine == "claude"
    claude.assert_called_once()
    google.assert_not_called()


def test_falls_back_to_google_when_claude_unavailable():
    with patch("apps.core.translation._translate_with_claude", return_value=None), \
         patch("apps.core.translation._translate_with_google", return_value="Expired batch"):
        result = translate_on_demand("Lot périmé", "en")

    assert result.engine == "google"
    assert result.text == "Expired batch"


def test_raises_when_every_engine_fails():
    """
    The reader must not be handed the source text dressed up as a translation:
    silence here would look identical to "already in your language".
    """
    with patch("apps.core.translation._translate_with_claude", return_value=None), \
         patch("apps.core.translation._translate_with_google", return_value=None):
        with pytest.raises(TranslationUnavailable):
            translate_on_demand("Lot périmé", "en")


def test_second_call_is_served_from_cache(settings):
    settings.CACHES = {
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
    }
    from django.core.cache import cache

    cache.clear()

    with patch("apps.core.translation._translate_with_claude", return_value="Expired batch") as claude:
        first = translate_on_demand("Lot périmé", "en")
        second = translate_on_demand("Lot périmé", "en")

    assert claude.call_count == 1, "a cache hit must not call the paid engine"
    assert first.cached is False
    assert second.cached is True
    assert second.text == "Expired batch"


def test_cache_outage_degrades_to_a_live_call(settings):
    """A Redis outage should cost money and latency, never availability."""
    with patch("apps.core.translation.cache.get", side_effect=ConnectionError("redis down")), \
         patch("apps.core.translation.cache.set", side_effect=ConnectionError("redis down")), \
         patch("apps.core.translation._translate_with_claude", return_value="Expired batch"):
        result = translate_on_demand("Lot périmé", "en")

    assert result.text == "Expired batch"


def test_legacy_helper_still_falls_back_to_source():
    """
    `translate_text` backs `save()` on the bilingual models, where a failed
    translation must not block the write. It keeps the old silent behaviour.
    """
    with patch("apps.core.translation._translate_with_claude", return_value=None), \
         patch("apps.core.translation._translate_with_google", return_value=None):
        assert translate_text("Lot périmé", "en") == "Lot périmé"


# --- endpoint --------------------------------------------------------------


def test_endpoint_requires_authentication(api_client):
    response = api_client.post(
        reverse("translate"), {"text": "Lot périmé", "target": "en"}, format="json"
    )
    assert response.status_code == 401


def test_endpoint_returns_translation(auth_client, pharmacist):
    client = auth_client(pharmacist)
    with patch("apps.core.translation._translate_with_claude", return_value="Expired batch"):
        response = client.post(
            reverse("translate"), {"text": "Lot périmé", "target": "en"}, format="json"
        )

    assert response.status_code == 200
    assert response.data["translated"] == "Expired batch"
    assert response.data["target"] == "en"
    assert response.data["cached"] is False


def test_endpoint_reports_unavailability_as_503(auth_client, pharmacist):
    client = auth_client(pharmacist)
    with patch("apps.core.translation._translate_with_claude", return_value=None), \
         patch("apps.core.translation._translate_with_google", return_value=None):
        response = client.post(
            reverse("translate"), {"text": "Lot périmé", "target": "en"}, format="json"
        )

    assert response.status_code == 503


def test_endpoint_rejects_unsupported_language(auth_client, pharmacist):
    client = auth_client(pharmacist)
    response = client.post(
        reverse("translate"), {"text": "Lot périmé", "target": "sw"}, format="json"
    )
    assert response.status_code == 400


def test_endpoint_rejects_oversized_text(auth_client, pharmacist):
    client = auth_client(pharmacist)
    response = client.post(
        reverse("translate"),
        {"text": "x" * 5000, "target": "en"},
        format="json",
    )
    assert response.status_code == 400


def test_translating_a_note_does_not_modify_the_record(auth_client, pharmacist, product):
    """
    The core guarantee: the stored note is the record of truth. Several of
    these fields are audit evidence, so a machine translation must never be
    written back over one.
    """
    product.notes_fr = "Lot périmé, retour fournisseur"
    product.save(update_fields=["notes_fr"])
    original = product.notes_fr

    client = auth_client(pharmacist)
    with patch("apps.core.translation._translate_with_claude", return_value="Expired batch"):
        response = client.post(
            reverse("translate"), {"text": original, "target": "en"}, format="json"
        )

    assert response.status_code == 200
    product.refresh_from_db()
    assert product.notes_fr == original
