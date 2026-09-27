import pytest
from pydantic import ValidationError

from app.api.models.tokens import TokenRequest
from app.services.auth import auth_service


def test_saved_languages_reach_the_user_settings():
    settings = auth_service._build_user_settings(TokenRequest(allowed_languages=["en", "fr"]))

    assert settings.allowed_languages == ["en", "fr"]


def test_no_languages_saved_means_no_filter():
    assert auth_service._build_user_settings(TokenRequest()).allowed_languages == []


@pytest.mark.parametrize("code", ["EN", "english", "e", "en-US"])
def test_a_language_must_be_a_two_letter_code(code):
    """TMDB's original_language and spoken_languages use lowercase ISO 639-1 codes; any
    other form would silently match nothing and hide every title."""
    with pytest.raises(ValidationError):
        TokenRequest(allowed_languages=[code])
