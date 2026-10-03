"""Rule matching: Russian lemmas, aliases, and conservative typo handling."""

import re
from functools import lru_cache

import pymorphy3
from rapidfuzz.distance import DamerauLevenshtein

TOKEN_RE = re.compile(r"[\w]+", re.UNICODE)
morph = pymorphy3.MorphAnalyzer()


@lru_cache(maxsize=100_000)
def lemma(token):
    return morph.parse(token)[0].normal_form


def tokens(text):
    return TOKEN_RE.findall(text.casefold().replace("ё", "е"))


def phrase_matches(haystack, needle, fuzzy=True):
    if not needle or len(needle) > len(haystack):
        return False
    for start in range(len(haystack) - len(needle) + 1):
        window = haystack[start:start + len(needle)]
        if all(lemma(actual) == lemma(expected) for actual, expected in zip(window, needle)):
            return True
        if fuzzy and all(
            lemma(actual) == lemma(expected) or (
                min(len(actual), len(expected)) >= 6
                and abs(len(actual) - len(expected)) <= 1
                and DamerauLevenshtein.distance(actual, expected) <= 1
            )
            for actual, expected in zip(window, needle)
        ):
            return True
    return False


def match_rule(text, rule):
    haystack = tokens(text)
    if not haystack:
        return False
    exclusions = [line.strip() for line in rule.exclusions.splitlines() if line.strip()]
    if any(phrase_matches(haystack, tokens(term), fuzzy=False) for term in exclusions):
        return False
    phrases = [rule.phrase, *rule.aliases.splitlines()]
    return any(phrase_matches(haystack, tokens(term), rule.fuzzy) for term in phrases if term.strip())


def first_match(text, rules):
    return next((rule for rule in rules if match_rule(text, rule)), None)
