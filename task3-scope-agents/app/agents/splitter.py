"""Step 1, the splitter (plain code, no model): transcript text -> speaker turns -> chunks.

A long meeting doesn't fit well in one extraction call, and a cheap model reads a short piece
more carefully. So the transcript is cut into chunks of about 3,000 tokens, always between two
speaker turns, never inside a sentence. Each chunk repeats the last turn of the one before, so
a request that starts at the end of one chunk is read whole in the next; the merger then removes
the duplicate this can produce.

Accepted line formats (whatever Meet, Zoom, Teams or a hand-typed transcript produces):

    [00:01:23] Alice: text        00:01:23 Alice: text        Alice (00:01:23): text
    Alice: text                   WebVTT cues: "00:01:23.000 --> 00:01:27.000" then "Alice: text"

A line that matches none of these continues the turn before it.
"""

import re

from app.handoffs import Chunk, Turn

# About 4 characters per token in English
CHARS_PER_TOKEN = 4
CHUNK_TOKENS = 3000

_TIME = r"(\d{1,2}:\d{2}(?::\d{2})?)"
_SPEAKER = r"([A-Za-z][\w .'\-]{0,59}?)"
_TIME_FIRST = re.compile(rf"^\[?{_TIME}\]?\s*[-\u2013]?\s*{_SPEAKER}\s*:\s*(.*)$")
_SPEAKER_FIRST = re.compile(rf"^{_SPEAKER}\s*[\(\[]{_TIME}[\)\]]\s*:?\s*(.*)$")
_SPEAKER_ONLY = re.compile(rf"^{_SPEAKER}:\s+(.*)$")
_VTT_CUE = re.compile(rf"^{_TIME}[.,]\d{{1,3}}\s*-->")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


class SplitError(ValueError):
    """The transcript has nothing to read."""


def normalize_time(raw: str) -> str:
    """ "1:23" -> "00:01:23", "1:02:03" -> "01:02:03"."""
    parts = [int(part) for part in raw.split(":")]
    if len(parts) == 2:
        parts.insert(0, 0)
    hours, minutes, seconds = parts
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _is_speaker(name: str) -> bool:
    # A real name is short; "Note that the URL is: http..." is not a speaker
    return len(name.split()) <= 5 and "http" not in name.lower()


def parse_turns(transcript: str) -> list[Turn]:
    """Read the transcript line by line into speaker turns."""
    turns: list[Turn] = []
    cue_time: str | None = None  # a WebVTT cue's start time, for the line after it

    for raw_line in transcript.splitlines():
        line = raw_line.strip()
        if not line or line == "WEBVTT" or line.isdigit():
            continue
        if cue := _VTT_CUE.match(line):
            cue_time = normalize_time(cue.group(1))
            continue

        speaker: str | None = None
        time: str | None = None
        text = line
        if (match := _TIME_FIRST.match(line)) and _is_speaker(match.group(2)):
            time, speaker, text = normalize_time(match.group(1)), match.group(2), match.group(3)
        elif (match := _SPEAKER_FIRST.match(line)) and _is_speaker(match.group(1)):
            speaker, time, text = match.group(1), normalize_time(match.group(2)), match.group(3)
        elif (match := _SPEAKER_ONLY.match(line)) and _is_speaker(match.group(1)):
            speaker, text = match.group(1), match.group(2)
            time, cue_time = cue_time, None

        if speaker is not None:
            turns.append(Turn(index=len(turns), speaker=speaker.strip(), time=time, text=text))
        elif turns:
            # A line with no speaker continues the turn before it
            last = turns[-1]
            turns[-1] = last.model_copy(update={"text": f"{last.text} {text}".strip()})
        else:
            turns.append(Turn(index=0, speaker="Unknown", time=cue_time, text=text))

    turns = [turn for turn in turns if turn.text.strip()]
    if not turns:
        raise SplitError("The transcript has no text to read")
    return _split_long_turns(turns, CHUNK_TOKENS * CHARS_PER_TOKEN)


def _split_long_turns(turns: list[Turn], max_chars: int) -> list[Turn]:
    """Cut a monologue longer than one chunk into several turns, between sentences, so no
    chunk is ever over budget. Each piece keeps its speaker and time."""
    result: list[Turn] = []
    for turn in turns:
        pieces: list[str] = [turn.text]
        if len(turn.text) > max_chars:
            pieces, current = [], ""
            for sentence in _SENTENCE_END.split(turn.text):
                if current and len(current) + len(sentence) + 1 > max_chars:
                    pieces.append(current)
                    current = ""
                current = f"{current} {sentence}".strip()
            pieces.append(current)
        for piece in pieces:
            result.append(turn.model_copy(update={"index": len(result), "text": piece}))
    return result


def split(transcript: str, chunk_tokens: int = CHUNK_TOKENS) -> list[Chunk]:
    """The transcript as chunks of whole turns, each about `chunk_tokens` long, with each
    chunk's first turn repeating the previous chunk's last turn."""
    turns = parse_turns(transcript)
    max_chars = chunk_tokens * CHARS_PER_TOKEN
    chunks: list[Chunk] = []
    current: list[Turn] = []
    new_turns = 0  # turns in `current` besides the one repeated from the previous chunk
    size = 0

    for turn in turns:
        length = len(turn.line()) + 1
        if new_turns and size + length > max_chars:
            chunks.append(Chunk(index=len(chunks), turns=current))
            current, new_turns, size = [current[-1]], 0, len(current[-1].line()) + 1
        current.append(turn)
        new_turns += 1
        size += length

    chunks.append(Chunk(index=len(chunks), turns=current))
    return chunks
