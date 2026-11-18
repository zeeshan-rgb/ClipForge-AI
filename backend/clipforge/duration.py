"""Smart Duration: where exactly a clip starts and ends.

The target length guides the cut; it never forces one. A clip always starts at
the beginning of a sentence and ends after a finished one, and the engine
prefers endings where the speaker has stopped, nobody is about to answer, and
no loud event is still under way. It works from word timings, the measured
silences, scene changes and sudden loud moments. No AI call is made here: the
AI's judgement of where the setup and payoff are arrives as the moment's
`core_start`, `core_end` and `peak`.

Nothing in this module cuts video. It returns a plan (kept ranges plus a
validation report) that the renderer follows.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

PRESETS = [15, 20, 30, 35, 45, 60, 90]
MODES = ("strict", "smart", "flexible")
CONTEXT_LEVELS = ("tight", "balanced", "full")
HOOK_MODES = ("auto", "story", "cold_open")

MIN_TARGET, MAX_TARGET = 10, 180
FLEX_FACTOR = 1.6  # how far Flexible may run past the target
HEAD_PAD, TAIL_PAD = 0.25, 0.45  # breathing room around the first and last word
TRIM_SILENCE_OVER = 2.0  # dead air longer than this is shortened...
TRIM_KEEPS = 0.3  # ...keeping this much at each side
SOFT_END_GAP = 1.0  # a pause this long before a capitalised word also ends a sentence
ANSWER_WINDOW = 2.5  # a reply starting this soon after a question belongs with it
EVENT_BEFORE, EVENT_AFTER = 0.3, 2.0  # a loud moment this close to the cut is still under way
BEAT_GAP = 2.0  # a pause this long separates one beat from the next
LONG_GAP = 4.0  # a pause this long ends a sentence even after a comma
TAG_ON_WORDS = 3  # a line this short, right after another, belongs with it ("Completely gone.")
TAG_ON_GAP = 1.0
EXTRA_LINE_COST = 3.0  # score cost of each line taken beyond the moment's own last line
MIN_VALID_ENDING = 40
MIN_KEPT_SHARE = 0.5  # a single clip must keep at least this much of the moment it was cut from
# Hook: a Short is judged in its first seconds. When the payoff comes late, the
# clip may open with a short flash-forward of it, then play the story in order.
HOOK_LATE = 6.0  # Auto: a payoff this far into the clip gets a teaser up front
TEASER_MIN, TEASER_MAX = 1.0, 3.5  # a teaser is a whole-word span of this length
TEASER_PAD_IN, TEASER_PAD_OUT = 0.08, 0.2
# Finishing beat: a laugh or loud reaction right after the last word is kept.
BEAT_START_WITHIN = 0.8  # a loud moment starting this soon after the last word...
BEAT_KEEP = 1.2  # ...is kept for this long, never into the next words

_TERMINAL = (".", "?", "!", "…")
_CLOSERS = "\"'”’)]"


@dataclass(frozen=True)
class Word:
    s: float
    e: float
    t: str


@dataclass
class Sentence:
    i0: int  # index of first word
    i1: int  # index of last word
    start: float
    end: float
    text: str
    terminal: bool  # ended by . ? ! (False = ended only by a long pause)
    question: bool
    gap_before: float  # silence between the previous word and this sentence
    gap_after: float  # silence before the next word (negative = speakers overlap)


@dataclass
class DurationSettings:
    target: float = 45.0
    mode: str = "smart"
    tolerance: float = 0.10  # Smart: share of the target the result may differ by
    split: bool = False
    max_parts: int = 3
    context: str = "balanced"
    trim_silence: bool = True
    hook: str = "story"  # story | auto | cold_open (projects default to auto, see schemas.ProjectOptions)

    @property
    def low(self) -> float:
        return self.target * (1 - self.tolerance) if self.mode == "smart" else 0.0

    @property
    def high(self) -> float:
        """The longest a single clip may be in this mode."""
        if self.mode == "strict":
            return self.target
        if self.mode == "smart":
            return self.target * (1 + self.tolerance)
        return self.target * FLEX_FACTOR

    @property
    def lead_sentences(self) -> int:
        """Extra sentences allowed before the moment's own first line."""
        return {"tight": 0, "balanced": 1, "full": 3}.get(self.context, 1)

    @property
    def lead_seconds(self) -> float:
        return {"tight": 0.0, "balanced": 6.0, "full": 15.0}.get(self.context, 6.0)


@dataclass
class Context:
    """What is known about the stretch of video around a moment."""

    words: list[Word]
    silences: list[tuple[float, float]] = field(default_factory=list)
    cuts: list[float] = field(default_factory=list)
    events: list[float] = field(default_factory=list)  # times of sudden loud moments
    duration: float = 0.0  # length of the whole video (0 = unknown)


@dataclass
class ClipPlan:
    start: float
    end: float
    segments: list[tuple[float, float]]  # kept ranges, in order; dead air is left out
    duration: float  # total of the kept ranges
    valid: bool
    report: dict[str, Any]
    part: int = 1
    parts: int = 1


# ---- sentences -----------------------------------------------------------------


def _ends_sentence(text: str) -> bool:
    return text.rstrip(_CLOSERS).endswith(_TERMINAL)


def build_sentences(words: list[Word]) -> list[Sentence]:
    """Group words into sentences.

    A sentence ends at . ? or !. Speech-to-text does not always write the
    full stop, so a pause can end one too:

    * two seconds or more, when the last word has no comma: a finished thought,
      whatever the next word's capital letter says (the speech model's casing
      is unreliable after a pause);
    * one second or more before a capitalised word: probably finished, but not
      certain, so it is not trusted as a place to end a clip;
    * four seconds or more, even after a comma: nobody is still mid-sentence.

    A shorter pause after a comma is a speaker thinking ("what I mean is, ...
    you should"): the sentence carries on.
    """
    sentences: list[Sentence] = []
    start = 0
    for i, w in enumerate(words):
        nxt = words[i + 1] if i + 1 < len(words) else None
        terminal = _ends_sentence(w.t)
        soft = False
        if not terminal and nxt is not None:
            gap = nxt.s - w.e
            carries_on = w.t.rstrip(_CLOSERS).endswith((",", ";", ":", "-", "—"))
            if gap >= BEAT_GAP and not carries_on:
                terminal = True  # a clear pause after a complete-sounding word: treat as a full stop
            elif gap >= LONG_GAP:
                soft = True
            elif gap >= SOFT_END_GAP and nxt.t[:1].isupper() and not carries_on:
                soft = True
        if terminal or soft or nxt is None:
            first = words[start]
            prev = words[start - 1] if start > 0 else None
            sentences.append(Sentence(
                i0=start, i1=i, start=first.s, end=w.e,
                text=" ".join(x.t for x in words[start:i + 1]),
                terminal=terminal,
                question=w.t.rstrip(_CLOSERS).endswith("?"),
                gap_before=(first.s - prev.e) if prev else math.inf,
                gap_after=(nxt.s - w.e) if nxt else math.inf,
            ))
            start = i + 1
    return sentences


# ---- how good is a place to stop, or to start ------------------------------------


def _near(times: list[float], lo: float, hi: float) -> bool:
    return any(lo <= t <= hi for t in times)


def _is_tag_on(sentences: list[Sentence], k: int) -> bool:
    """Is sentence k a short afterthought to the one before it ("...was gone. Completely gone.")?"""
    if k <= 0 or k >= len(sentences):
        return False
    s = sentences[k]
    return (s.i1 - s.i0 + 1) <= TAG_ON_WORDS and 0 <= s.gap_before < TAG_ON_GAP


def ending_quality(
    sentences: list[Sentence], j: int, ctx: Context, core_last: int | None = None
) -> tuple[int, list[str]]:
    """0-100 for ending a clip after sentence j, with the problems found.

    `core_last` is the index of the moment's own last sentence, when ending
    before it would cut the payoff.
    """
    s = sentences[j]
    score, problems = 50, []
    if s.terminal:
        score += 20
    else:
        problems.append("the last sentence trails off without a clear end")

    if s.gap_after < 0:
        score -= 40
        problems.append("another voice is talking over the last words")
    elif s.gap_after < 0.15:
        score -= 25
        problems.append("the next words follow with no pause")
    elif s.gap_after >= 0.6:
        score += 15
    elif s.gap_after >= 0.3:
        score += 8

    nxt = sentences[j + 1] if j + 1 < len(sentences) else None
    if s.question and nxt is not None and s.gap_after < ANSWER_WINDOW:
        score -= 30
        problems.append("it ends on a question whose answer follows")
    if _is_tag_on(sentences, j + 1):
        score -= 15
        problems.append("a short follow-up to the last line comes right after")

    if _near(ctx.events, s.end - EVENT_BEFORE, s.end + EVENT_AFTER):
        score -= 30
        problems.append("a loud moment is still happening at the cut")

    if any(s.end - 0.2 <= qs <= s.end + 1.0 for qs, _ in ctx.silences):
        score += 10
    if _near(ctx.cuts, s.end - 0.1, s.end + 0.4):
        score += 5

    if core_last is not None and j < core_last:
        score -= 40
        problems.append("it stops before the payoff of the moment")
    return max(0, min(100, score)), problems


def start_quality(
    sentences: list[Sentence], i: int, ctx: Context, core_first: int
) -> tuple[int, list[str]]:
    """0-100 for starting a clip at sentence i. `core_first` is the moment's own first sentence."""
    s = sentences[i]
    score, notes = 60, []
    if s.gap_before >= 0.5:
        score += 15
    elif s.gap_before < 0.1:
        score -= 10
    if _near(ctx.cuts, s.start - 0.4, s.start + 0.1):
        score += 5
    prev = sentences[i - 1] if i > 0 else None
    if prev is not None and prev.question and prev.gap_after < ANSWER_WINDOW:
        score -= 20
        notes.append("it opens on the answer to a question that is cut off")
    if i > core_first:
        dropped = i - core_first
        score -= min(40, 12 * dropped)
        notes.append(f"{dropped} sentence(s) of setup left out")
    elif i < core_first:
        score -= 2 * (core_first - i)
    return max(0, min(100, score)), notes


# ---- turning a run of sentences into a clip --------------------------------------


def _edges(words: list[Word], first: int, last: int, total: float) -> tuple[float, float]:
    """Clip edges with a little air around the first and last word, never into a neighbour."""
    prev = words[first - 1] if first > 0 else None
    nxt = words[last + 1] if last + 1 < len(words) else None
    # At most half of the gap to a neighbouring word on each side: two parts that
    # meet in the same gap then share it instead of overlapping.
    lead = HEAD_PAD if prev is None else min(HEAD_PAD, max(0.0, words[first].s - prev.e) * 0.5)
    tail = TAIL_PAD if nxt is None else min(TAIL_PAD, max(0.0, nxt.s - words[last].e) * 0.5)
    start = max(0.0, words[first].s - lead)
    end = words[last].e + tail
    if total:
        end = min(end, total)
    return round(start, 2), round(end, 2)


def _kept_ranges(start: float, end: float, ctx: Context, trim: bool) -> list[tuple[float, float]]:
    """The clip's time range with long dead air shortened."""
    if not trim:
        return [(start, end)]
    ranges: list[tuple[float, float]] = []
    cursor = start
    for qs, qe in sorted(ctx.silences):
        if qe - qs < TRIM_SILENCE_OVER or qs < start or qe > end:
            continue
        cut_from, cut_to = qs + TRIM_KEEPS, qe - TRIM_KEEPS
        if cut_to - cut_from < 0.2 or cut_from <= cursor:
            continue
        # never trim across speech, whatever the loudness curve says
        if any(w.e > cut_from and w.s < cut_to for w in ctx.words):
            continue
        ranges.append((round(cursor, 2), round(cut_from, 2)))
        cursor = cut_to
    ranges.append((round(cursor, 2), end))
    return ranges


def _length(ranges: list[tuple[float, float]]) -> float:
    return round(sum(e - s for s, e in ranges), 2)


def _fit_score(length: float, st: DurationSettings) -> float:
    """100 = right on the target for this mode."""
    off = abs(length - st.target) / st.target
    if st.mode == "strict":
        return max(0.0, 100 - 100 * off)
    if st.mode == "smart":
        if length < st.low:
            return max(0.0, 100 - 150 * (st.low - length) / st.target)
        return 100 - 20 * off
    return max(0.0, 100 - (60 if length > st.target else 90) * off)


def _span_indexes(sentences: list[Sentence], core_start: float, core_end: float, peak: float) -> tuple[int, int, int]:
    """Indexes of the moment's first sentence, the sentence holding its key moment, and its last."""
    def holding(t: float) -> int:
        for k, s in enumerate(sentences):
            if s.end >= t:
                return k
        return len(sentences) - 1

    first = holding(core_start + 0.01)
    last = max(first, max((k for k, s in enumerate(sentences) if s.start <= core_end - 0.01), default=first))
    key = min(max(holding(peak + 0.01), first), last)
    return first, key, last


def _report(
    sentences: list[Sentence], i: int, j: int, ctx: Context, st: DurationSettings,
    start: float, end: float, length: float, removed: float,
    end_q: int, end_problems: list[str], start_q: int, start_notes: list[str],
    core_first: int, core_last: int, core_end: float,
) -> dict[str, Any]:
    first_w, last_w = ctx.words[sentences[i].i0], ctx.words[sentences[j].i1]
    last = sentences[j]
    reasons: list[str] = []
    diff = length - st.target
    if diff > 0.5:
        why = "so the last sentence can finish"
        if j > core_last:
            why = "so the reaction after the key moment is included"
        reasons.append(f"Runs {diff:.0f} s over the {st.target:.0f} s target {why}.")
    elif diff < -max(1.0, st.target * st.tolerance):
        if i > core_first:
            reasons.append(f"{-diff:.0f} s under the target: part of the setup was left out to stay inside the limit.")
        else:
            reasons.append(
                f"Ends {-diff:.0f} s before the {st.target:.0f} s target at a natural stopping point; "
                "what follows is not part of this moment."
            )
    if i > core_first:
        reasons.append(f"Starts {i - core_first} sentence(s) into the setup to fit the limit.")
    elif i < core_first:
        reasons.append(f"Starts {core_first - i} sentence(s) early for context.")
    if removed >= 0.5:
        reasons.append(f"{removed:.1f} s of dead air removed.")

    overlap = last.gap_after < 0
    event_done = not _near(ctx.events, last.end - EVENT_BEFORE, last.end + EVENT_AFTER) and last.end >= core_end - 0.5
    return {
        "requested_seconds": st.target,
        "actual_seconds": length,
        "mode": st.mode,
        "first_word": first_w.t,
        "last_word": last_w.t,
        "sentence_complete": last.terminal,
        "question_answered": not (last.question and last.gap_after < ANSWER_WINDOW and j + 1 < len(sentences)),
        "event_complete": event_done,
        "speakers_overlap_at_end": overlap,
        "ending_quality": end_q,
        "start_quality": start_q,
        # the last word ends before the clip does, so its caption can be shown in full
        "last_word_fully_inside": last_w.e <= end + 1e-6 and first_w.s >= start - 1e-6,
        "setup_sentences_dropped": max(0, i - core_first),
        "dead_air_removed": round(removed, 2),
        "reasons": reasons,
        "problems": end_problems + start_notes,
    }


def plan_clip(
    ctx: Context, sentences: list[Sentence], core_start: float, core_end: float, peak: float,
    st: DurationSettings,
) -> ClipPlan:
    """The best single clip for a moment under the chosen mode, or an invalid plan saying why none fits."""
    if not sentences:
        return ClipPlan(core_start, core_end, [(core_start, core_end)], round(core_end - core_start, 2), False,
                        {"requested_seconds": st.target, "actual_seconds": 0, "mode": st.mode,
                         "problems": ["there is no speech here to place the cut by"], "reasons": [],
                         "no_fit": "This moment has no transcript, so sentence boundaries cannot be found."})
    core_first, key, core_last = _span_indexes(sentences, core_start, core_end, peak)

    # how far before the moment's own first line a clip may start
    earliest = core_first
    for k in range(core_first - 1, -1, -1):
        if core_first - k > st.lead_sentences or sentences[core_first].start - sentences[k].start > st.lead_seconds:
            break
        if sentences[k].gap_after >= BEAT_GAP:
            break  # dead air: what came before it is a different beat, not context
        earliest = k
    # how far past the payoff to look for a natural stop (a reaction, an answer)
    reach = core_end + max(8.0, 0.3 * st.target)
    latest = core_last
    for k in range(core_last + 1, len(sentences)):
        if sentences[k].end > reach or sentences[k - 1].gap_after >= BEAT_GAP:
            break  # a line after dead air is a new beat, never padding for this clip
        latest = k

    best: tuple[float, ClipPlan] | None = None
    shortest_complete: float | None = None
    shortest_complete_plan: ClipPlan | None = None
    core_seconds = max(sentences[core_last].end - sentences[core_first].start, 0.01)
    for j in range(key, latest + 1):
        end_q, end_problems = ending_quality(sentences, j, ctx, core_last)
        for i in range(earliest, key + 1):
            start, end = _edges(ctx.words, sentences[i].i0, sentences[j].i1, ctx.duration)
            ranges = _kept_ranges(start, end, ctx, st.trim_silence)
            length = _length(ranges)
            # A complete clip ends on a finished sentence, with nobody talking over it,
            # and not before the moment's payoff: a clip that stops short of the
            # payoff is never "valid", however tidy its last sentence is.
            answered = not (
                sentences[j].question and sentences[j].gap_after < ANSWER_WINDOW and j + 1 < len(sentences)
            )
            usable_end = (
                end_q >= MIN_VALID_ENDING and sentences[j].terminal and sentences[j].gap_after >= 0
                and j >= core_last and answered
            )
            # Trimming a line or two of setup to fit is fine. Dropping most of the
            # moment is not: what is left is no longer the moment that was picked.
            kept = (min(sentences[j].end, sentences[core_last].end) - max(sentences[i].start, sentences[core_first].start))
            keeps_enough = kept / core_seconds >= MIN_KEPT_SHARE
            if usable_end and keeps_enough and (shortest_complete is None or length < shortest_complete):
                shortest_complete = length
                # Kept as the fallback when nothing fits: if the user approves anyway,
                # they get the whole moment, never a fragment of it.
                c_start_q, c_start_notes = start_quality(sentences, i, ctx, core_first)
                c_report = _report(sentences, i, j, ctx, st, start, end, length, (end - start) - length,
                                   end_q, end_problems, c_start_q, c_start_notes, core_first, core_last, core_end)
                if c_report["last_word_fully_inside"]:
                    shortest_complete_plan = ClipPlan(start, end, ranges, length, False, c_report)
            if length > st.high + 0.05:
                continue
            start_q, start_notes = start_quality(sentences, i, ctx, core_first)
            total = 0.55 * end_q + 0.25 * start_q + 0.20 * _fit_score(length, st)
            # Lines past the payoff are for reactions and answers. They are never
            # free: a clip does not grow just to sit nearer the target.
            total -= EXTRA_LINE_COST * max(0, j - core_last)
            removed = (end - start) - length
            report = _report(sentences, i, j, ctx, st, start, end, length, removed,
                             end_q, end_problems, start_q, start_notes, core_first, core_last, core_end)
            if not keeps_enough:
                report["problems"].append("more than half of the moment would be left out")
            valid = (usable_end and keeps_enough and report["question_answered"]
                     and report["last_word_fully_inside"])
            if not valid:
                total -= 100  # an incomplete ending never beats a complete one
            plan = ClipPlan(start, end, ranges, length, valid, report)
            if best is None or total > best[0]:
                best = (total, plan)

    if best is not None and best[1].valid:
        return best[1]

    # Nothing complete fits. Say so plainly rather than export a broken clip.
    limit = st.high
    if shortest_complete is not None and shortest_complete > limit:
        message = (
            f"No complete version of this moment fits {limit:.0f} s: the shortest one that keeps the key "
            f"moment and a finished ending is {shortest_complete:.0f} s."
        )
        hint = "Try Flexible mode, a longer target, or part splitting."
    else:
        message = "No clean ending was found for this moment within the limit."
        hint = "Try Flexible mode or a longer target."
    # Offer the complete moment, over the limit, rather than the best fragment:
    # found in testing on real speech, where the "best attempt" was 1.7 s long.
    fallback = shortest_complete_plan or (best[1] if best is not None else None)
    if fallback is not None:
        fallback.report["no_fit"] = f"{message} {hint}"
        fallback.report["shortest_complete_seconds"] = shortest_complete
        return fallback
    start, end = _edges(ctx.words, sentences[core_first].i0, sentences[core_last].i1, ctx.duration)
    return ClipPlan(start, end, [(start, end)], round(end - start, 2), False, {
        "requested_seconds": st.target, "actual_seconds": round(end - start, 2), "mode": st.mode,
        "reasons": [], "problems": [], "no_fit": f"{message} {hint}",
        "shortest_complete_seconds": shortest_complete,
    })


# ---- splitting a long moment into connected parts ---------------------------------


def _can_split_after(sentences: list[Sentence], k: int, ctx: Context, key: int) -> tuple[bool, int]:
    """May one part end after sentence k and the next begin at k+1? Returns (allowed, quality)."""
    s = sentences[k]
    quality, _ = ending_quality(sentences, k, ctx, None)
    if k + 1 == key:
        return False, quality  # the line before the punchline stays with the punchline
    if not s.terminal or s.gap_after < 0:
        return False, quality  # mid-sentence, or someone is talking over it
    if s.question and s.gap_after < ANSWER_WINDOW:
        return False, quality  # the answer belongs with its question
    if _near(ctx.events, s.end - EVENT_BEFORE, s.end + EVENT_AFTER):
        return False, quality  # a loud moment or reaction is under way
    if _is_tag_on(sentences, k + 1):
        return False, quality  # the next line is an afterthought to this one: keep them together
    return quality >= MIN_VALID_ENDING, quality


def plan_parts(
    ctx: Context, sentences: list[Sentence], core_start: float, core_end: float, peak: float,
    st: DurationSettings,
) -> list[ClipPlan]:
    """Split a moment that is too long for one clip into connected parts, or return [] if it cannot be done.

    Parts are consecutive runs of whole sentences: nothing is repeated and
    nothing is skipped. Every split falls where a clip could decently end.
    """
    if not sentences or st.max_parts < 2:
        return []
    first, key, core_last = _span_indexes(sentences, core_start, core_end, peak)

    # the whole moment ends at the best natural stop at or shortly after its payoff
    reach = core_end + max(8.0, 0.3 * st.target)
    last, last_q = core_last, -1
    for k in range(core_last, len(sentences)):
        if k > core_last and (sentences[k].end > reach or sentences[k - 1].gap_after >= BEAT_GAP):
            break
        q, _ = ending_quality(sentences, k, ctx, core_last)
        usable = sentences[k].terminal and sentences[k].gap_after >= 0
        if usable and q > last_q:
            last, last_q = k, q
    if last_q < MIN_VALID_ENDING:
        return []

    n = last - first + 1
    if n < 2:
        return []
    shortest = max(8.0, 0.4 * st.target)  # a part shorter than this is not worth posting on its own

    def piece(i: int, j: int) -> tuple[float, float, list[tuple[float, float]], float]:
        start, end = _edges(ctx.words, sentences[i].i0, sentences[j].i1, ctx.duration)
        ranges = _kept_ranges(start, end, ctx, st.trim_silence)
        return start, end, ranges, _length(ranges)

    split_ok = {k: _can_split_after(sentences, k, ctx, key) for k in range(first, last)}

    def solve(longest: float) -> list[tuple[int, int]] | None:
        # best[p][j]: lowest cost of covering sentences first..j with p parts
        best: list[dict[int, tuple[float, int]]] = [dict() for _ in range(st.max_parts + 1)]
        for j in range(first, last + 1):
            length = piece(first, j)[3]
            if shortest <= length <= longest + 0.05 and (j == last or split_ok[j][0]):
                best[1][j] = (100 * abs(length - st.target) / st.target, -1)
        for p in range(2, st.max_parts + 1):
            for j in range(first + p - 1, last + 1):
                if j != last and not split_ok[j][0]:
                    continue
                for m, (cost_before, _) in best[p - 1].items():
                    if m >= j:
                        continue
                    length = piece(m + 1, j)[3]
                    if not (shortest <= length <= longest + 0.05):
                        continue
                    cost = cost_before + 100 * abs(length - st.target) / st.target + 0.5 * (100 - split_ok[m][1])
                    if j not in best[p] or cost < best[p][j][0]:
                        best[p][j] = (cost, m)
        choices = [(best[p][last][0] / p, p) for p in range(2, st.max_parts + 1) if last in best[p]]
        if not choices:
            return None
        _, parts = min(choices)
        bounds, j = [], last
        for p in range(parts, 0, -1):
            m = best[p][j][1]
            bounds.append((m + 1 if p > 1 else first, j))
            j = m
        return list(reversed(bounds))

    bounds = solve(st.high)
    stretched = False
    if bounds is None and st.mode != "strict":
        # allow parts to run long before giving up, and say so
        bounds = solve(st.target * FLEX_FACTOR)
        stretched = bounds is not None
    if bounds is None:
        return []

    plans: list[ClipPlan] = []
    previous_end: float | None = None
    for number, (i, j) in enumerate(bounds, start=1):
        start, end, ranges, length = piece(i, j)
        is_last = number == len(bounds)
        end_q, end_problems = ending_quality(sentences, j, ctx, core_last if is_last else None)
        start_q, start_notes = start_quality(sentences, i, ctx, i)
        report = _report(sentences, i, j, ctx, st, start, end, length, (end - start) - length,
                         end_q, end_problems, start_q, start_notes, i, j, sentences[j].end)
        # the single-clip wording ("what follows is not part of this moment") is wrong for a part
        removed = (end - start) - length
        reasons = [f"Part {number} of {len(bounds)} of one moment, split at a finished sentence."]
        diff = length - st.target
        if diff < -max(1.0, st.target * st.tolerance):
            reasons.append(
                f"{-diff:.0f} s under the {st.target:.0f} s target: "
                + ("the story ends here." if is_last
                   else "this is where a sentence finishes and the story can pause; it continues in the next part.")
            )
        elif diff > 0.5:
            reasons.append(f"Runs {diff:.0f} s over the {st.target:.0f} s target so the last sentence can finish.")
        if removed >= 0.5:
            reasons.append(f"{removed:.1f} s of dead air removed.")
        report["reasons"] = reasons
        if stretched and length > st.high + 0.05:
            report["reasons"].append(
                f"This part runs past the {st.high:.0f} s limit because no finished sentence falls nearer the target."
            )
        report.update(
            part=number, parts=len(bounds),
            continues_from_previous=number > 1,
            gap_from_previous_seconds=None if previous_end is None else round(start - previous_end, 2),
            repeats_dialogue=False,  # parts are consecutive whole sentences: by construction
            skips_dialogue=False,
        )
        valid = (sentences[j].terminal and sentences[j].gap_after >= 0 and end_q >= MIN_VALID_ENDING
                 and report["question_answered"] and report["last_word_fully_inside"])
        plans.append(ClipPlan(start, end, ranges, length, valid, report, part=number, parts=len(bounds)))
        previous_end = end
    return plans


def _clip_time(t: float, segments: list[tuple[float, float]]) -> float | None:
    acc = 0.0
    for a, b in segments:
        if a <= t <= b:
            return acc + t - a
        acc += b - a
    return None


def finishing_beat(plan: ClipPlan, ctx: Context) -> ClipPlan:
    """Keep a laugh or loud reaction that starts right after the last word, instead of cutting it off."""
    if not plan.valid or not plan.segments:
        return plan
    end = plan.segments[-1][1]
    last = max((w for w in ctx.words if w.e <= end + 0.01), key=lambda w: w.e, default=None)
    if last is None:
        return plan
    event = next((e for e in sorted(ctx.events) if last.e - 0.1 <= e <= last.e + BEAT_START_WITHIN), None)
    if event is None:
        return plan
    nxt = min((w.s for w in ctx.words if w.s >= last.e + 0.05), default=None)
    new_end = max(end, event + BEAT_KEEP)
    if nxt is not None:
        new_end = min(new_end, nxt - 0.1)
    if ctx.duration:
        new_end = min(new_end, ctx.duration)
    new_end = round(new_end, 2)
    if new_end <= end + 0.1:
        return plan
    a, _ = plan.segments[-1]
    plan.segments = [*plan.segments[:-1], (a, new_end)]
    plan.duration = round(plan.duration + new_end - end, 2)
    plan.end = new_end
    plan.report["reasons"] = [*plan.report.get("reasons", []),
                              f"Keeps the {new_end - end:.1f} s reaction after the last line instead of cutting it off."]
    plan.report["actual_seconds"] = plan.duration
    plan.report["finishing_beat"] = round(new_end - end, 2)
    return plan


def _teaser(ctx: Context, sentences: list[Sentence], peak: float) -> tuple[float, float, str] | None:
    """A whole-word span of the payoff, 1 to 3.5 s, for a flash-forward opening."""
    words = ctx.words
    if not words:
        return None
    holder = next((s for s in sentences if s.start - 0.3 <= peak <= s.end + 0.3), None)
    if holder is None:
        holder = min(sentences, key=lambda s: abs(s.start - peak), default=None)
    if holder is None:
        return None
    # the word said at the peak (or the first one after it), within the payoff sentence
    centre = min(range(holder.i0, holder.i1 + 1), key=lambda i: abs((words[i].s + words[i].e) / 2 - peak))
    best: tuple[float, int, int] | None = None
    for i in range(holder.i0, centre + 1):
        for j in range(centre, holder.i1 + 1):
            length = words[j].e - words[i].s
            if length > TEASER_MAX:
                break
            if length < TEASER_MIN:
                continue
            # prefer the whole sentence, then spans that end where the sentence ends, then longer
            score = length + (2.0 if (i == holder.i0 and j == holder.i1) else 0) + (1.0 if j == holder.i1 else 0)
            if best is None or score > best[0]:
                best = (score, i, j)
    if best is None:
        return None
    _, i, j = best
    prev_e = words[i - 1].e if i > 0 else 0.0
    next_s = words[j + 1].s if j + 1 < len(words) else words[j].e + TEASER_PAD_OUT
    start = max(prev_e + 0.02, words[i].s - TEASER_PAD_IN, 0.0)
    end = min(next_s - 0.02, words[j].e + TEASER_PAD_OUT)
    if ctx.duration:
        end = min(end, ctx.duration)
    text = " ".join(w.t for w in words[i:j + 1])
    return round(start, 2), round(end, 2), text


def cold_open(plan: ClipPlan, ctx: Context, sentences: list[Sentence], peak: float, st: DurationSettings) -> ClipPlan:
    """Open with a short flash-forward of the payoff when the clip takes too long to get there.

    The story then plays from its start, in order, so nothing that needs its
    setup is said without it. Only for a single, complete clip.
    """
    def note(strategy: str, why: str, **extra: Any) -> ClipPlan:
        plan.report["hook"] = {"strategy": strategy, "reason": why, **extra}
        return plan

    if st.hook == "story":
        return note("story", "Plays in story order (your choice).")
    if not plan.valid or plan.parts > 1 or not plan.segments:
        return note("story", "Connected parts and incomplete clips keep story order.")
    at = _clip_time(peak, plan.segments)
    if at is None:
        return note("story", "The key moment is not inside this clip.")
    if st.hook == "auto" and at < HOOK_LATE:
        return note("story", f"The key moment comes {at:.1f} s in, early enough to be the hook.")
    teaser = _teaser(ctx, sentences, peak)
    if teaser is None:
        return note("story", "No short, whole-word line at the key moment could be used as a teaser.")
    a, b, text = teaser
    length = b - a
    if _clip_time((a + b) / 2, plan.segments) is not None and _clip_time((a + b) / 2, plan.segments) < 1.5:
        return note("story", "The key line already opens the clip.")
    if plan.duration + length > st.high + 0.05 and st.mode == "strict":
        return note("story", f"A {length:.1f} s teaser would take the clip over the {st.target:.0f} s limit.")
    plan.segments = [(a, b), *plan.segments]
    plan.duration = round(plan.duration + length, 2)
    plan.report["actual_seconds"] = plan.duration
    plan.report["reasons"] = [*plan.report.get("reasons", []),
                              f"Opens with a {length:.1f} s flash-forward of the payoff (“{text}”), "
                              "then tells the story from its start."]
    return note("cold_open", f"The key moment comes {at:.1f} s in, so a {length:.1f} s teaser of it opens the clip.",
                seconds=round(length, 2), text=text, payoff_at=round(at + length, 2))


def plan_moment(ctx: Context, core_start: float, core_end: float, peak: float, st: DurationSettings) -> list[ClipPlan]:
    """Everything to cut for one moment: one clip, several connected parts, or one invalid plan saying why not."""
    sentences = build_sentences(ctx.words)
    single = plan_clip(ctx, sentences, core_start, core_end, peak, st)

    def finish(plan: ClipPlan) -> list[ClipPlan]:
        return [cold_open(finishing_beat(plan, ctx), ctx, sentences, peak, st)]

    if not st.split:
        return finish(single)
    # With splitting on, a moment that only fits in one clip by losing some of
    # its setup is told in parts instead, so nothing is left out.
    whole = single.valid and not single.report.get("setup_sentences_dropped")
    if whole:
        return finish(single)
    parts = plan_parts(ctx, sentences, core_start, core_end, peak, st)
    if parts:
        return parts
    if single.valid:
        return finish(single)
    single.report["no_fit"] = (
        single.report.get("no_fit", "This moment does not fit.")
        + f" It also could not be split into {st.max_parts} or fewer parts at finished sentences."
    )
    return [single]
