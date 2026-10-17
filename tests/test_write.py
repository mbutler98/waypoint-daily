"""Phase 4: writer, figures, number check, fact-check and the draft loop, with a fake Gemini
and a fake renderer (no network, no browser)."""

from __future__ import annotations

import copy
import json

import pytest
from helpers import fake_gemini, make_candidate

from waypoint.draft import draft_slot
from waypoint.models import Post
from waypoint.render.render import RenderResult, SlideOverflowError
from waypoint.write.factcheck import ClaimCheck, check_quotes
from waypoint.write.figures import FigureDraft, FigureError, build_figure
from waypoint.write.numbers import check_numbers, source_numbers
from waypoint.write.writer import WriterDraft, assemble, hashtags

SOURCE = (
    "Honeybees can count. In a new study, researchers trained bees to fly past a row of "
    "landmarks to reach sugar water. Bees learned to stop after the third landmark, even when "
    "the gaps between landmarks changed. The team tested 120 bees over 3 weeks. About 80 "
    "percent of trained bees stopped at the right landmark and 20 percent at the wrong one. "
    "A bee brain has about one million neurons, while a human brain has about 86 billion. "
    "The researchers say counting may help bees find their way home. "
    "Over the past half-century, bee research has grown fast."
)

GOOD = {
    "evidence": ["Honeybees can count."] * 5,
    "kicker": "Bees",
    "hook": "Bees can count. They do it on the wing.",
    "insights": [
        {"headline": "They count landmarks", "body": "Bees learned to stop after the third "
         "landmark on the way to sugar water.", "figure": None},
        {"headline": "Even when the gaps change", "body": "Moving the landmarks did not fool "
         "them. They counted, not measured.", "figure": None},
        {"headline": "A tiny brain does it", "body": "A bee brain has about one million cells. "
         "Ours has about 86 billion.", "figure": {
             "kind": "column", "title": "Bees stopped at the right spot",
             "labels": ["Right landmark", "Wrong landmark"], "values": [80, 20],
             "series": [], "notes": [], "highlight": 0, "prefix": "", "suffix": "%"}},
    ],
    "takeaway": "Even tiny brains can count.",
    "takeaway_support": "Counting may help bees find their way home.",
    "caption_body": "Bees learned to stop at the third landmark.\n\nThe catch? Moving the "
                    "landmarks did not fool them.\n\nThe payoff? Counting may help them get home.",
    "hashtags": ["#Bees", "#Insects", "Animal Minds"],
    "alt_text": ["Title slide.", "Bees and landmarks.", "Gaps change.", "Chart.", "Takeaway."],
    "key_claim": "Honeybees can count landmarks to find food.",
}


def draft(**changes) -> WriterDraft:
    d = copy.deepcopy(GOOD)
    d.update(changes)
    return WriterDraft.model_validate(d)


def cand():
    c = make_candidate("Bees can count", faculty="Animals & Nature", topic="Bee counting")
    c.text = SOURCE
    return c


def supported(text="Honeybees can count."):
    return {"claims": [{"where": "slide 1", "claim": "Bees can count", "supported": True,
                        "evidence_quote": text, "problem": ""}]}


UNSUPPORTED = {"claims": [{"where": "slide 4", "claim": "Bees count to ten", "supported": False,
                           "evidence_quote": "", "problem": "SOURCE says three landmarks."}]}


def fake_render(fail_first: int = 0):
    calls = {"n": 0}

    def render(post, out_dir, cfg):
        calls["n"] += 1
        if calls["n"] <= fail_first:
            raise SlideOverflowError(2, "insight body takes 9 lines (max 8)")
        (out_dir / "slides").mkdir(parents=True, exist_ok=True)
        (out_dir / "preview.png").write_bytes(b"png")
        return RenderResult(slides=[], preview=out_dir / "preview.png", sizes=[])

    render.calls = calls
    return render


def scripted(writes: list[dict], checks: list[dict]):
    """respond() that serves writer drafts and fact-check verdicts in order."""
    writes, checks = list(writes), list(checks)

    def respond(prompt: str) -> dict:
        return checks.pop(0) if prompt.startswith("SOURCE:") else writes.pop(0)

    return respond


# --- assembly and local checks --------------------------------------------------------------


def test_good_draft_assembles(cfg):
    asm = assemble(draft(), cand(), "2026-10-17-am", "am", cfg, SOURCE)
    assert asm.problems == []
    post = asm.post
    assert isinstance(post, Post)
    assert [s.type for s in post.slides] == ["hook", "insight", "insight", "insight", "cta"]
    assert post.caption.startswith("Bees can count. They do it on the wing.\n\n")
    assert "Source: Example — Bees can count" in post.caption
    assert post.caption.endswith(cfg.brand.caption_cta[-1])
    assert post.hashtags == ["#Bees", "#Insects", "#AnimalMinds", "#WaypointDaily"]
    fig = post.slides[3].figure.model_dump()
    assert fig["type"] == "chart"
    assert fig["data"] == [["Right landmark", 80], ["Wrong landmark", 20]]


def test_hashtags_capped_and_brand_last():
    d = draft(hashtags=["#a", "#b", "#c", "#d", "#e", "#WaypointDaily", "#A"])
    assert hashtags(d) == ["#a", "#b", "#c", "#d", "#WaypointDaily"]


def test_overlong_copy_is_reported(cfg):
    long_body = " ".join(["word"] * 35)
    d = draft(insights=[{**GOOD["insights"][0], "body": long_body}, *GOOD["insights"][1:]])
    asm = assemble(d, cand(), "2026-10-17-am", "am", cfg, SOURCE)
    assert asm.post is None
    assert any("35 words; the limit is 30" in p for p in asm.problems)


def test_reading_grade_is_enforced(cfg):
    hard = ("Comprehensive investigations demonstrated extraordinarily sophisticated "
            "numerical discrimination capabilities.")
    d = draft(insights=[{**i, "body": hard} for i in GOOD["insights"]])
    asm = assemble(d, cand(), "2026-10-17-am", "am", cfg, SOURCE)
    assert any("reading grade" in p for p in asm.problems)


def test_planted_invented_number_is_caught(cfg):
    d = draft(insights=[{**GOOD["insights"][0], "body": "Bees stopped at the right "
                         "landmark 73% of the time."}, *GOOD["insights"][1:]])
    asm = assemble(d, cand(), "2026-10-17-am", "am", cfg, SOURCE)
    assert asm.post is None
    assert any("number 73" in p for p in asm.problems)


def test_invented_figure_value_is_caught(cfg):
    fig = {**GOOD["insights"][2]["figure"], "values": [81, 20]}
    d = draft(insights=[*GOOD["insights"][:2], {**GOOD["insights"][2], "figure": fig}])
    asm = assemble(d, cand(), "2026-10-17-am", "am", cfg, SOURCE)
    assert any("figure value 81" in p for p in asm.problems)


def test_bad_figure_spec_is_reported(cfg):
    fig = {**GOOD["insights"][2]["figure"], "values": [80]}  # 2 labels, 1 value
    d = draft(insights=[*GOOD["insights"][:2], {**GOOD["insights"][2], "figure": fig}])
    asm = assemble(d, cand(), "2026-10-17-am", "am", cfg, SOURCE)
    assert asm.post is None
    assert any("insight 3 figure: 2 labels but 1 values" in p for p in asm.problems)


# --- numbers --------------------------------------------------------------------------------


def test_source_numbers_reads_words_scales_and_spans():
    nums = source_numbers(SOURCE)
    assert {120, 3, 80, 1_000_000, 86, 86e9, 50} <= nums


def test_copy_numbers_allow_rounding_but_not_years():
    src = "Growth was 19.7% in 1998, about 12,000 years after farming began."
    assert check_numbers(src, [], [], ["About 20% growth. 12,000 years."]) == []
    assert check_numbers(src, [], [], ["Growth hit 30%."]) != []
    assert check_numbers(src, [], [], ["In 1999, growth was strong."]) != []
    assert check_numbers(src, [19.7], [], []) == []
    assert check_numbers(src, [20], [], []) != []  # figure data must be exact


def test_display_scale_matches_thousands():
    assert check_numbers("It grew to $76,100.", [76.1], [], []) == []


# --- figures --------------------------------------------------------------------------------


def _fig(**kw) -> FigureDraft:
    base = {"kind": "timeline", "title": "Writing came long after farming", "labels": [],
            "values": [], "series": [], "notes": [], "highlight": None, "prefix": "",
            "suffix": ""}
    return FigureDraft.model_validate({**base, **kw})


def test_timeline_layout():
    spec = build_figure(_fig(labels=["Farming", "Writing", "Today"],
                             notes=["10,000 BCE", "3200 BCE", ""], highlight=1), "Src")
    assert spec["type"] == "diagram"
    dots = [n for n in spec["nodes"] if n["shape"] == "circle"]
    assert [d["color"] for d in dots] == ["grey", "hot", "grey"]
    assert len(spec["edges"]) == 2


def test_steps_layout_and_label_room():
    spec = build_figure(_fig(kind="steps", labels=["Boil", "Squeeze", "Cool"]), "Src")
    assert [n["label"] for n in spec["nodes"]] == ["Boil", "Squeeze", "Cool"]
    with pytest.raises(FigureError, match="too long"):
        build_figure(_fig(kind="steps", labels=["A very long step name", "B", "C", "D"]), "S")


def test_line_chart_needs_matching_series():
    with pytest.raises(FigureError, match="needs 3 values"):
        build_figure(_fig(kind="line", labels=["0", "5", "10"],
                          series=[{"name": "7%", "values": [1, 2], "highlight": True}]), "S")


# --- fact-check -----------------------------------------------------------------------------


def test_invented_evidence_quote_is_rejected():
    claims = [ClaimCheck(where="slide 2", claim="x", supported=True,
                         evidence_quote="Bees were tested in a wind tunnel on Mars.", problem=""),
              ClaimCheck(where="slide 3", claim="y", supported=True,
                         evidence_quote="Bees learned to stop after the third landmark",
                         problem="")]
    problems = check_quotes(claims, SOURCE)
    assert len(problems) == 1 and problems[0].startswith("slide 2")


# --- the draft loop -------------------------------------------------------------------------


def test_draft_slot_writes_complete_draft(cfg, tmp_path):
    gemini, models = fake_gemini(cfg, scripted([GOOD], [supported()]))
    res = draft_slot("am", "2026-10-17-am", [cand()], cfg, gemini, tmp_path / "drafts",
                     tmp_path / "stage", fake_render())
    out = tmp_path / "drafts" / "2026-10-17-am"
    assert res.post is not None and res.gemini_calls == 2
    assert {p.name for p in out.iterdir()} >= {"post.json", "source.txt", "factcheck.json",
                                                "preview.png", "slides"}
    assert json.loads((out / "factcheck.json").read_text(encoding="utf-8"))["passed"]
    assert not (out / "FAILED.md").exists()


def test_planted_unsupported_claim_triggers_rewrite_then_fails(cfg, tmp_path):
    gemini, models = fake_gemini(cfg, scripted([GOOD, GOOD], [UNSUPPORTED, UNSUPPORTED]))
    res = draft_slot("am", "2026-10-17-am", [cand()], cfg, gemini, tmp_path / "d",
                     tmp_path / "s", fake_render())
    assert res.post is None
    report = res.reports[0]
    assert report.reason == "fact-check failed after a rewrite"
    assert report.gemini_calls == 4  # write, check, rewrite, re-check
    rewrite_prompt = models.prompts[2]
    assert "Bees count to ten" in rewrite_prompt and "YOUR PREVIOUS DRAFT" in rewrite_prompt
    failed = (tmp_path / "d" / "2026-10-17-am" / "FAILED.md").read_text(encoding="utf-8")
    assert "fact-check failed" in failed
    assert not (tmp_path / "d" / "2026-10-17-am" / "post.json").exists()


def test_rewrite_fixes_unsupported_claim(cfg, tmp_path):
    gemini, _ = fake_gemini(cfg, scripted([GOOD, GOOD], [UNSUPPORTED, supported()]))
    res = draft_slot("am", "2026-10-17-am", [cand()], cfg, gemini, tmp_path / "d",
                     tmp_path / "s", fake_render())
    assert res.post is not None
    assert [a["stage"] for a in res.reports[0].attempts] == ["factcheck", "factcheck"]


def test_overflow_asks_writer_to_shorten(cfg, tmp_path):
    gemini, models = fake_gemini(cfg, scripted([GOOD, GOOD], [supported()]))
    render = fake_render(fail_first=1)
    res = draft_slot("am", "2026-10-17-am", [cand()], cfg, gemini, tmp_path / "d",
                     tmp_path / "s", render)
    assert res.post is not None and render.calls["n"] == 2
    assert "slide 2 does not fit" in models.prompts[1]


def test_bad_candidate_falls_through_to_next(cfg, tmp_path):
    bad = copy.deepcopy(GOOD)
    bad["hook"] = "Bees beat 73% of maths students."  # invented number, every time
    second = cand()
    second.url = "https://example.com/second"
    writes = [bad] * (1 + cfg.limits.max_writer_retries) + [GOOD]
    gemini, _ = fake_gemini(cfg, scripted(writes, [supported()]))
    res = draft_slot("am", "2026-10-17-am", [cand(), second], cfg, gemini, tmp_path / "d",
                     tmp_path / "s", fake_render())
    assert res.post is not None and res.candidate.url == "https://example.com/second"
    assert res.reports[0].reason.startswith("no valid draft after 3 writer calls")


def test_no_candidates_writes_failed(cfg, tmp_path):
    gemini, _ = fake_gemini(cfg)
    res = draft_slot("pm", "2026-10-17-pm", [], cfg, gemini, tmp_path / "d", tmp_path / "s",
                     fake_render())
    assert res.post is None
    assert (tmp_path / "d" / "2026-10-17-pm" / "FAILED.md").is_file()


def test_slot_cap_stops_before_a_candidate_it_cannot_finish(cfg, tmp_path):
    bad = copy.deepcopy(GOOD)
    bad["hook"] = "Bees beat 73% of maths students."  # invented number: 3 wasted writes
    second = cand()
    second.url = "https://example.com/second"
    gemini, models = fake_gemini(cfg, scripted([bad] * 3 + [GOOD], [supported()]))
    with gemini.capped(cfg.gemini.model, 4):
        res = draft_slot("am", "2026-10-17-am", [cand(), second], cfg, gemini,
                         tmp_path / "d", tmp_path / "s", fake_render())
    assert res.post is None and len(models.prompts) == 3  # 1 call left: second not started
    assert res.reports[1].reason.startswith("not tried: only 1")


def test_factcheck_sees_writer_caption_only(cfg):
    from waypoint.write.factcheck import render_draft

    post = assemble(draft(), cand(), "2026-10-17-am", "am", cfg, SOURCE).post
    text = render_draft(post)
    assert "The catch? Moving" in text
    assert "Source:" not in text and cfg.brand.caption_cta[0] not in text


def test_alt_hook_saves_a_rewrite_when_the_hook_overflows(cfg, tmp_path):
    long_hook = dict(GOOD, hook="Bees can count landmarks. They do it on the wing.",
                     alt_hooks=["Bees can count.", "Tiny brains count."])
    calls = {"n": 0}

    def render(post, out_dir, cfg):
        calls["n"] += 1
        if post.slides[0].headline == long_hook["hook"]:
            raise SlideOverflowError(1, "hook takes 4 lines (max 3)")
        return fake_render()(post, out_dir, cfg)

    gemini, models = fake_gemini(cfg, scripted([long_hook], [supported()]))
    res = draft_slot("am", "2026-10-17-am", [cand()], cfg, gemini, tmp_path / "d",
                     tmp_path / "s", render)
    assert res.post is not None and res.post.slides[0].headline == "Bees can count."
    assert res.post.caption.startswith("Bees can count.")
    assert res.gemini_calls == 2 and calls["n"] == 2  # one write, one check, two renders
    assert res.reports[0].attempts[0]["hook_used"] == "Bees can count."


def test_escaped_newlines_in_caption_become_paragraphs(cfg):
    d = draft(caption_body="Bees count landmarks.\\n\\nThe catch? Gaps change.")
    post = assemble(d, cand(), "2026-10-17-am", "am", cfg, SOURCE).post
    assert "\\n" not in post.caption and "landmarks.\n\nThe catch?" in post.caption


def test_pending_drafts_count_as_planned(cfg, tmp_path):
    from waypoint.draft import pending_drafts

    post = assemble(draft(), cand(), "2026-10-17-am", "am", cfg, SOURCE).post
    for pid in ("2026-10-17-am", "2026-10-18-am", "2026-10-16-pm"):
        (tmp_path / pid).mkdir()
        (tmp_path / pid / "post.json").write_text(
            post.model_copy(update={"id": pid}).model_dump_json(), encoding="utf-8")
    got = pending_drafts(tmp_path, {"2026-10-16-pm"}, "2026-10-18")
    assert [c.url for c in got] == [post.source.url]  # only 10-17: 10-16 posted, 10-18 is today


def test_content_failures_are_skipped_for_a_while(cfg, tmp_path):
    import datetime as dt

    from waypoint.draft import recently_skipped, skipped_entries
    from waypoint.state import append_skipped, load_skipped

    gemini, _ = fake_gemini(cfg, scripted([GOOD, GOOD], [UNSUPPORTED, UNSUPPORTED]))
    res = draft_slot("am", "2026-10-17-am", [cand()], cfg, gemini, tmp_path / "d",
                     tmp_path / "s", fake_render())
    path = tmp_path / "skipped.json"
    append_skipped(skipped_entries(res, "2026-10-16"), path)
    entries = load_skipped(path)
    assert [e["reason"] for e in entries] == ["fact-check failed after a rewrite"]
    assert recently_skipped(entries, dt.date(2026, 10, 20), 14) == {"example.com/bees-can-count"}
    assert recently_skipped(entries, dt.date(2026, 10, 30), 14) == set()


def test_pending_drafts_after_a_date(cfg, tmp_path):
    from waypoint.draft import pending_drafts

    post = assemble(draft(), cand(), "2026-10-17-am", "am", cfg, SOURCE).post
    for pid in ("2026-10-17-am", "2026-10-18-am"):
        (tmp_path / pid).mkdir()
        (tmp_path / pid / "post.json").write_text(
            post.model_copy(update={"id": pid}).model_dump_json(), encoding="utf-8")
    got = pending_drafts(tmp_path, set(), "", after="2026-10-17-zz")
    assert len(got) == 1  # only 10-18; the day being redone is not "later"
