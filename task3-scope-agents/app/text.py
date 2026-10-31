"""Small text helpers the agents share: fencing untrusted text, and finding quotes in it."""

import re

_TAG_CHARS = re.compile(r"[<>]")
_NOT_WORD = re.compile(r"[^\w]+")


def as_data(tag: str, text: str) -> str:
    """Put untrusted text (a transcript, a help article) inside <tag> ... </tag>.

    The prompts say text inside these tags is data, never instructions. Angle brackets in the
    text are escaped as &lt; and &gt;, so a speaker can't close the tag early and have the rest
    of their words read as instructions.
    """
    safe = _TAG_CHARS.sub(lambda match: "&lt;" if match.group() == "<" else "&gt;", text)
    return f"<{tag}>\n{safe}\n</{tag}>"


def normalize(text: str) -> str:
    """Lower case, with every run of punctuation and spaces turned into one space. Quotes are
    compared this way, so a model fixing a comma or a curly apostrophe still matches."""
    return _NOT_WORD.sub(" ", text.lower()).strip()


def contains_quote(haystack: str, quote: str) -> bool:
    """True when `quote` appears in `haystack`, ignoring case, punctuation and spacing. An empty
    quote matches nothing: a requirement has to point at something the client actually said."""
    needle = normalize(quote)
    return bool(needle) and f" {needle} " in f" {normalize(haystack)} "
