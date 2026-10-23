"""Public text helpers used by transcript comparison and practice feedback."""

import pytest

from chatterpal.utils.text import (
    TextProcessor,
    extract_pronunciation_targets,
    format_transcript_with_timestamps,
    generate_pronunciation_feedback,
    remove_filler_words,
)


def test_clean_text_preserves_case_and_punctuation_when_requested():
    processor = TextProcessor()
    assert processor.clean_text("  Hello,\x00 WORLD!  ") == "hello, world!"
    assert processor.clean_text(
        "  Hello, WORLD!  ", remove_punctuation=True, lowercase=False,
        remove_extra_spaces=False,
    ) == " Hello WORLD "
    assert processor.clean_text("") == ""


def test_word_sentence_and_keyword_extraction_keeps_content_words():
    processor = TextProcessor()
    text = "The clear voice is clear. We practice voice! Go?"
    assert processor.tokenize_words("The Clear voice!", remove_stop_words=True) == [
        "clear", "voice"
    ]
    assert processor.tokenize_sentences(text) == [
        "The clear voice is clear", "We practice voice", "Go"
    ]
    assert processor.extract_keywords(text, top_k=2) == [("clear", 2), ("voice", 2)]
    assert TextProcessor(language="fr").tokenize_words(
        "le son", remove_stop_words=True
    ) == ["le", "son"]


def test_readability_reports_counts_and_empty_input():
    processor = TextProcessor()
    result = processor.calculate_readability_score("Go go. We sing!")
    assert result == {
        "avg_sentence_length": 2.0,
        "avg_word_length": 2.5,
        "lexical_diversity": 0.75,
    }
    assert processor.calculate_readability_score("?!") == {
        "avg_sentence_length": 0.0,
        "avg_word_length": 0.0,
        "lexical_diversity": 0.0,
    }


@pytest.mark.parametrize(
    "original,recognized,expected",
    [
        ("Hello, WORLD!", "hello world", 1.0),
        ("", "", 1.0),
        ("hello", "", 0.0),
        ("", "hello", 0.0),
        ("clear voice", "clear speech", 1 / 3),
    ],
)
def test_transcript_similarity_normalizes_case_spacing_and_punctuation(
    original, recognized, expected
):
    assert TextProcessor().calculate_text_similarity(original, recognized) == pytest.approx(
        expected
    )


def test_phonetic_letter_heuristics_and_practice_targets():
    processor = TextProcessor()
    assert processor.extract_phonetic_features("Hello!") == {
        "vowel_count": 2, "consonant_count": 3, "syllable_count": 2,
        "total_letters": 5,
    }
    targets = extract_pronunciation_targets("I go hello communication.")
    assert targets == [
        {"word": "hello", "syllables": 2, "difficulty": "easy"},
        {"word": "communication", "syllables": 5, "difficulty": "medium"},
    ]


def test_filler_removal_uses_whole_words_and_supports_language_fallback():
    assert remove_filler_words("UM we uh practice you know clearly") == "we practice clearly"
    assert remove_filler_words("umbrella likelihood") == "umbrella likelihood"
    assert remove_filler_words("bonjour le monde", language="fr") == "bonjour le monde"


def test_transcript_timestamps_require_one_timestamp_per_word():
    assert format_transcript_with_timestamps("Hello world", [0.125, 1.5]) == (
        "[0.12s] Hello\n[1.50s] world"
    )
    assert format_transcript_with_timestamps("Hello world", [0.0]) == "Hello world"
    assert format_transcript_with_timestamps("", []) == ""


@pytest.mark.parametrize(
    "recognized,similarity,level,missing,extra",
    [
        ("The clear voice", 1.0, "excellent", set(), set()),
        ("the clear voice today", 0.75, "good", set(), {"today"}),
        ("the quiet room", 0.2, "needs_improvement", {"clear", "voice"}, {"quiet", "room"}),
    ],
)
def test_pronunciation_feedback_reports_transcript_overlap(
    recognized, similarity, level, missing, extra
):
    feedback = generate_pronunciation_feedback("The clear voice!", recognized)
    assert feedback["similarity_score"] == pytest.approx(similarity)
    assert feedback["accuracy_percentage"] == pytest.approx(similarity * 100)
    assert feedback["feedback_level"] == level
    assert set(feedback["missing_words"]) == missing
    assert set(feedback["extra_words"]) == extra
    assert feedback["word_count_original"] == 3
    assert feedback["word_count_recognized"] == len(recognized.split())
