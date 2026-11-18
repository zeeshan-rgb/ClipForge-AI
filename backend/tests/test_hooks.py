"""Hooks and endings: a flash-forward opening when the payoff comes late, the reaction kept after the last line,
and the opening / ending / dead-air checks in the quality report."""

from __future__ import annotations

from clipforge.captions import clip_words
from clipforge.duration import Context, DurationSettings, plan_moment
from clipforge.qc import story_checks
from clipforge.schemas import ProjectOptions

from .test_duration import no_word_is_cut, script

STORY = script(
    (100.0, "Okay so we are sneaking into the base now.", 4.0),
    (105.0, "Nobody has seen us and the alarm is off.", 5.0),
    (111.0, "I am going to open this door very quietly.", 6.0),
    (118.0, "There is a guard right there but he is asleep.", 6.0),
    (125.0, "And he woke up, everybody run!", 2.5),
)


def plan(hook: str, **kw):
    ctx = Context(STORY, duration=200, **kw)
    [p] = plan_moment(ctx, 100.0, 127.5, 126.0, DurationSettings(target=35, mode="smart", hook=hook))
    return p


def test_a_late_payoff_gets_a_short_flash_forward_then_the_story_in_order():
    p = plan("auto")
    assert p.valid and p.report["hook"]["strategy"] == "cold_open"
    (ta, tb), first_story = p.segments[0], p.segments[1]
    assert 1.0 <= tb - ta <= 3.6 and ta >= 124.8, "the teaser is a short span of the payoff"
    assert first_story[0] <= 100.0, "then the story plays from its start"
    assert no_word_is_cut(p, STORY)
    words = [w.text for w in clip_words([{"s": w.s, "e": w.e, "w": w.t} for w in STORY], p.segments)]
    assert words[:2] == ["And", "he"] and words[-1] == "run!", "captions follow the clip order, payoff said twice"
    assert any("flash-forward" in r for r in p.report["reasons"])


def test_story_order_and_early_payoffs_are_left_alone():
    assert plan("story").report["hook"]["strategy"] == "story"
    early = script((100.0, "Watch this, he fell off the roof!", 2.5), (103.0, "That was amazing honestly.", 2.0))
    [p] = plan_moment(Context(early, duration=200), 100.0, 105.0, 101.0, DurationSettings(target=15, hook="auto"))
    assert p.report["hook"]["strategy"] == "story" and len(p.segments) == 1


def test_strict_mode_never_goes_over_for_a_teaser():
    ctx = Context(STORY, duration=200)
    [p] = plan_moment(ctx, 100.0, 127.5, 126.0, DurationSettings(target=28, mode="strict", hook="cold_open"))
    assert p.duration <= 28.05


def test_a_reaction_right_after_the_last_line_is_kept():
    p = plan("story", events=[127.7])
    assert p.report.get("finishing_beat") and p.end >= 128.5
    assert any("reaction after the last line" in r for r in p.report["reasons"])


def test_projects_default_to_the_automatic_hook():
    assert ProjectOptions().duration_settings().hook == "auto"
    assert ProjectOptions(hook="story").duration_settings().hook == "story"


def test_story_checks_flag_a_slow_opening_dead_air_and_an_abrupt_ending():
    slow = {c.name: c for c in story_checks({"ending_quality": 40, "problems": ["the last sentence trails off"]},
                                            [(3.0, 4.0), (9.0, 10.0)], 12.0)}
    assert slow["opening"].status == "warn" and "3.0 s in" in slow["opening"].detail
    assert slow["dead_air"].status == "warn"
    assert slow["ending"].status == "warn" and "trails off" in slow["ending"].detail
    good = {c.name: c for c in story_checks({"ending_quality": 85, "hook": {"strategy": "cold_open", "seconds": 2.2}},
                                            [(0.1, 2.0), (2.3, 5.0), (5.2, 9.5)], 10.0)}
    assert all(c.status == "ok" for c in good.values()) and "flash-forward" in good["opening"].detail
