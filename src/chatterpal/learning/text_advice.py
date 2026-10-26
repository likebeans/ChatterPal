"""Shared checks for provider advice grounded only in learner text."""

import re
import unicodedata
from collections import Counter

# Provider narratives have no acoustic evidence. Even a valid text quotation must
# not launder a pronunciation claim or a numeric ability score into the result.
_UNSUPPORTED_ADVICE = re.compile(
    r"\b(?:pronunci\w*|phonem\w*|phonetic\w*|acoustic\w*|prosod\w*|"
    r"intonation|fluency|vowels?|consonants?|accents?|articulation|"
    r"syllables?|scores?|ratings?)\b|\b(?:word|sentence) stress\b|"
    r"\d+(?:\.\d+)?\s*(?:%|/\s*(?:10|100)\b)|"
    r"\baccuracy\s*(?:is\s*)?[:=]?\s*\d|准确率\s*[:：]?\s*\d|"
    r"/[^/\n]{1,24}/|[ɑɐɒæəɚɛɜɝɞɪɔœɶøʊʌʏʔθðʃʒŋɹɾ]|"
    r"发音|音素|音标|声学|语调|重音|元音|辅音|流利度|口音|评分|分数|得分",
    re.IGNORECASE,
)

_NUMBER = r"(?<![\w.,/+\-−])(?:[+\-−]\s*)?(?:\d+(?:[.,]\d+|\s+\d+)*|[.,]\d+)"
_NUMERIC_VALUES = re.compile(_NUMBER + r"\s*%?")
# These are text topics, provided that the learner already supplied the same
# phrase and value. A topic word alone cannot authorize a new acoustic claim.
_TEXT_CONTEXT = re.compile(
    r"\b(?:accents?|pronunciation|phonetics?|intonation|vowels?|consonants?)\s+"
    r"(?:workshops?|classes|class|courses?|lessons?|books?)\b|"
    + _NUMBER
    + r"\s*%\s+(?:off|discount)\b",
    re.IGNORECASE,
)
_ARTICLE_RULE = re.compile(
    r"\b(?:use|choose)\s+[\"']?an?[\"']?\s+before\s+(?:an?\s+)?"
    r"(?:vowel|consonant)(?:\s+sounds?)?\.?",
    re.IGNORECASE,
)


def text_words(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold().replace("’", "'")
    return re.findall(r"\w+(?:'\w+)*", normalized)


def require_text_advice(text: str) -> None:
    """Reject acoustic claims and scores, allowing a general article rule."""
    if _UNSUPPORTED_ADVICE.search(text) and not _ARTICLE_RULE.fullmatch(text.strip()):
        raise ValueError(
            "Only text advice without acoustic claims or scores is allowed"
        )


def require_text_suggestion(original: str, suggested: str) -> None:
    """Allow text changes without introducing acoustic judgements or scores."""
    if not _UNSUPPORTED_ADVICE.search(suggested):
        return
    # Reordering must preserve numeric symbols as well as words: text_words
    # alone would treat 5%, .5% and -5% as the same quantity.
    if Counter(text_words(original)) == Counter(text_words(suggested)) and Counter(
        value.strip() for value in _NUMERIC_VALUES.findall(original)
    ) == Counter(value.strip() for value in _NUMERIC_VALUES.findall(suggested)):
        return
    contexts = Counter(
        match.group().casefold() for match in _TEXT_CONTEXT.finditer(original)
    )

    spans = []
    for match in _TEXT_CONTEXT.finditer(suggested):
        phrase = match.group().casefold()
        if contexts[phrase]:
            contexts[phrase] -= 1
            spans.append(match.span())

    # Check matches against the untouched text. Removing a permitted discount
    # would also hide an overlapping claim such as "accuracy is 10% off".
    for match in _UNSUPPORTED_ADVICE.finditer(suggested):
        if not any(
            start <= match.start() and match.end() <= end for start, end in spans
        ):
            require_text_advice(match.group())
