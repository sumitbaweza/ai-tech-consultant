"""Step 1: reading transcript formats and cutting long meetings into chunks."""

from itertools import pairwise

import pytest

from app.agents.splitter import SplitError, normalize_time, parse_turns, split


@pytest.mark.parametrize(
    ("line", "speaker", "time", "text"),
    [
        ("[00:01:23] Priya Shah: We need a map.", "Priya Shah", "00:01:23", "We need a map."),
        ("00:01:23 Priya: We need a map.", "Priya", "00:01:23", "We need a map."),
        ("Priya Shah (1:23): We need a map.", "Priya Shah", "00:01:23", "We need a map."),
        ("Priya [01:02:03] We need a map.", "Priya", "01:02:03", "We need a map."),
        ("Priya: We need a map.", "Priya", None, "We need a map."),
    ],
)
def test_each_line_format(line: str, speaker: str, time: str | None, text: str) -> None:
    [turn] = parse_turns(line)

    assert (turn.speaker, turn.time, turn.text) == (speaker, time, text)


def test_webvtt_cue_times_the_next_line() -> None:
    vtt = "WEBVTT\n\n1\n00:00:04.000 --> 00:00:09.000\nMark: Hello there.\n"

    [turn] = parse_turns(vtt)

    assert (turn.speaker, turn.time, turn.text) == ("Mark", "00:00:04", "Hello there.")


def test_a_line_without_a_speaker_continues_the_turn() -> None:
    turns = parse_turns("[00:00:01] Priya: We need a map\nwith every location on it.")

    assert [t.text for t in turns] == ["We need a map with every location on it."]


def test_a_sentence_with_a_colon_is_not_a_speaker() -> None:
    turns = parse_turns(
        "Priya: Here's the plan.\nThe thing we need most from the new site is this: speed."
    )

    assert len(turns) == 1


def test_empty_transcript_is_an_error() -> None:
    with pytest.raises(SplitError):
        split("  \n\n ")


def test_normalize_time() -> None:
    assert normalize_time("1:23") == "00:01:23"
    assert normalize_time("1:02:03") == "01:02:03"


def test_long_meeting_is_chunked_between_turns_with_one_turn_of_overlap() -> None:
    transcript = "\n".join(
        f"[00:{n // 60:02d}:{n % 60:02d}] {'Priya' if n % 2 else 'Sumit'}: " + "word " * 60
        for n in range(200)
    )

    chunks = split(transcript, chunk_tokens=1000)

    assert len(chunks) > 3
    for chunk in chunks:
        assert len(chunk.text) <= 1000 * 4 + 400  # a chunk may run over by one turn at most
    for before, after in pairwise(chunks):
        assert after.turns[0] == before.turns[-1]  # the overlap
    covered = {turn.index for chunk in chunks for turn in chunk.turns}
    assert covered == set(range(200))  # nothing is lost


def test_a_monologue_longer_than_a_chunk_is_split_between_sentences() -> None:
    transcript = "Priya: " + "This is one sentence about the site. " * 2000

    chunks = split(transcript, chunk_tokens=1000)

    assert len(chunks) > 1
    assert all(turn.speaker == "Priya" for chunk in chunks for turn in chunk.turns)
    assert all(turn.text.endswith("site.") for chunk in chunks for turn in chunk.turns)
