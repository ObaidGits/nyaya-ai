"""Script-detection tests for the language layer (D-077)."""

from __future__ import annotations

from app.language.detection import detect_language
from app.language.models import LanguageCode


def test_latin_script_defaults_to_english() -> None:
    assert detect_language("What is the punishment for murder?") is LanguageCode.EN
    assert detect_language("") is LanguageCode.EN
    assert detect_language("12345 ??? !!!") is LanguageCode.EN


def test_each_supported_indic_script_is_detected() -> None:
    cases = {
        "धारा 103 में क्या प्रावधान है?": LanguageCode.HI,
        "ধারা 103 কী বলে?": LanguageCode.BN,
        "कायद्याविषयी विचारा": LanguageCode.MR,
        "કાયદો શું કહે છે?": LanguageCode.GU,
        "சட்டம் என்ன சொல்கிறது?": LanguageCode.TA,
        "చట్టం ఏమి చెబుతుంది?": LanguageCode.TE,
        "ಕಾನೂನು ಏನು ಹೇಳುತ್ತದೆ?": LanguageCode.KN,
        "നിയമം എന്ത് പറയുന്നു?": LanguageCode.ML,
        "ਕਾਨੂੰਨ ਕੀ ਕਹਿੰਦਾ ਹੈ?": LanguageCode.PA,
        "ଆଇନ କଣ କହୁଛି?": LanguageCode.OR,
        # Assamese-specific characters (ৰ) distinguish it from Bengali script.
        "আপুনি কি কৰিব পাৰে": LanguageCode.AS,
    }
    for message, expected in cases.items():
        assert detect_language(message) is expected, message


def test_bengali_script_without_assamese_characters_is_bengali() -> None:
    assert detect_language("ধারা ১০৩ কী বলে?") is LanguageCode.BN


def test_mixed_script_minority_indic_stays_english() -> None:
    # A short Indic fragment inside an English sentence must not flip the
    # detection: the dominant script wins only with real presence.
    assert detect_language("What does धारा mean in English?") is LanguageCode.EN


def test_mixed_script_majority_indic_wins() -> None:
    assert detect_language("धारा 103 BNS के अनुसार क्या कहता है") is LanguageCode.HI


def test_short_indic_fragment_below_threshold_stays_english() -> None:
    # A single Devanagari character is noise, not a language signal.
    assert detect_language("ok न") is LanguageCode.EN


def test_marathi_legal_query_detected_as_marathi() -> None:
    # Devanagari is shared with Hindi; the lexical marker count (आहे/मध्ये)
    # must resolve Marathi input to Marathi, not Hindi.
    assert detect_language("कलम १०३ मध्ये काय तरतूद आहे?") is LanguageCode.MR
    assert detect_language("खुनासाठी कोणती शिक्षा आहे?") is LanguageCode.MR
    # Hindi markers dominate in Hindi input.
    assert detect_language("हत्या के लिए क्या सज़ा है?") is LanguageCode.HI
