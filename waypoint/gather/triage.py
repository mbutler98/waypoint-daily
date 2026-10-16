"""Triage: label each candidate with faculty, topic, key claim, surprise (1-5) and a safety
verdict (PLAN.md 4.3 exclusions), plus the cheap keyword safety filter that runs first.

With Gemini, candidates are labelled in batches (one call per `triage_batch_size`), and
labels are cached in .cache/triage.json by URL so re-runs don't spend calls. With --no-llm,
simple keyword heuristics stand in.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from waypoint.dedupe import url_key
from waypoint.models import FACULTIES, Candidate
from waypoint.write.gemini import GeminiClient

log = logging.getLogger(__name__)

# --- keyword safety filter (4.3) ------------------------------------------------------------

_UNSAFE = {
    "politics": r"elections?|electoral|ballots?|campaign(?:s|ing)?|senators?|senate|congress"
                r"|congressional|parliament|prime minister|president(?:ial)?|white house"
                r"|democrats?|republicans?|gop|tories|labou?r party|trump|biden|harris|vance"
                r"|putin|zelensky|netanyahu|xi jinping|tariffs?|sanctions|immigration"
                r"|abortion|gun control|partisan|legislat(?:ion|ors?|ure)",
    "tragedy": r"killed|kills|deaths?|death toll|died|dies|fatal(?:ity|ities)?|shooting"
               r"|massacre|murder(?:ed|s)?|homicide|suicide|terror(?:ism|ist)?|bomb(?:ing|s)?"
               r"|war crimes?|genocide|famine|disaster victims|crash(?:ed)? kill|hostages?"
               r"|wars?|invasion|airstrikes?|missile strikes?|crash(?:es)?|disasters?|accidents?",
    "graphic": r"gore|graphic (?:images|video)|torture|mutilat\w*|sexual assault|rape|abuse"
               r"|pornograph\w*|self-harm",
    "advice": r"should you (?:take|buy|invest|stop)|best stocks?|stock picks?|buy now"
              r"|diet plan|lose weight fast|cure for|miracle cure|dosage|legal advice"
              r"|tax tips|how to invest",
}
UNSAFE_RE = {k: re.compile(rf"\b(?:{v})\b", re.I) for k, v in _UNSAFE.items()}


def keyword_unsafe(text: str) -> str | None:
    """The first excluded category the text hits, as 'keyword:<category>:<word>'."""
    for cat, rx in UNSAFE_RE.items():
        m = rx.search(text)
        if m:
            return f"keyword:{cat}:{m.group(0).lower()}"
    return None


# --- heuristic faculty (--no-llm) -----------------------------------------------------------

_FACULTY_WORDS = {
    "Animals & Nature": r"animals?|species|birds?|fish|insects?|mammals?|plants?|trees?|forest"
                        r"|wildlife|ecolog\w*|evolution\w*|predators?|whales?|bees?|frogs?",
    "Space": r"space|planets?|stars?|galax\w+|nasa|orbit\w*|mars|moon|lunar|solar system"
             r"|asteroids?|comets?|telescope|black holes?|astronom\w+|cosmic|universe",
    "Human Body & Health": r"brain|heart|blood|cells?|immune|disease|health|body|muscles?"
                           r"|bones?|gut|sleep|genes?|dna|patients?|virus\w*|bacteria",
    "Mind & Behaviour": r"psycholog\w*|behaviou?r\w*|memory|emotions?|cognitive|attention"
                        r"|habits?|decisions?|bias\w*|mental|personality|learning",
    "Everyday Physics & Chemistry": r"physics|chemi\w+|molecules?|atoms?|quantum|heat|light"
                                    r"|magnet\w*|crystals?|water|thermo\w+|reaction",
    "Trades & Home": r"hvac|heating|ventilation|air condition\w*|plumbing|woodwork\w*|carpentry"
                     r"|furnace|heat pumps?|insulation|appliances?|home|house|wood",
    "Engineering & Tech": r"engineer\w*|bridges?|engines?|batter(?:y|ies)|robots?|machines?"
                          r"|materials?|solar panels?|grid|chips?|semiconductor\w*|3d print\w*",
    "AI & Computing": r"ai|artificial intelligence|machine learning|neural|algorithms?|computers?"
                      r"|software|llms?|language models?|data|code|programming|internet",
    "Money & Markets": r"money|markets?|econom\w+|prices?|inflation|finance|financial|banks?"
                       r"|stocks?|trade|investors?|wages?|currency|interest rates?",
    "History": r"history|historic\w*|ancient|medieval|centur(?:y|ies)|archaeolog\w+|fossils?"
               r"|empire|ruins|civili[sz]ation|prehistoric",
    "Geography & Earth": r"earth|climate|ocean|volcan\w+|earthquakes?|rivers?|glaciers?"
                         r"|mountains?|geolog\w+|weather|rocks?|soil|tectonic",
    "Food & Cooking": r"food|cooking|cook|recipes?|bread|baking|flavou?rs?|taste|coffee|chocolate"
                      r"|cheese|kitchen|ferment\w*|nutrition|diet",
    "Sport & Strength": r"sports?|athletes?|exercise|training|fitness|running|strength|olympic"
                        r"|football|soccer|marathon|workout",
    "Flight": r"flight|aircraft|planes?|airplanes?|aviation|wings?|pilots?|airlines?|drones?"
              r"|aerodynamic\w*|jets?|helicopters?",
}
_FACULTY_RE = {k: re.compile(rf"\b(?:{v})\b", re.I) for k, v in _FACULTY_WORDS.items()}


def guess_faculty(c: Candidate) -> str | None:
    """Single hint wins; otherwise the hinted (or any) faculty with most keyword hits."""
    if len(c.faculty_hints) == 1:
        return c.faculty_hints[0]
    options = c.faculty_hints or list(FACULTIES)
    text = f"{c.title} {c.title} {c.summary} {c.text[:3000]}"
    scores = {f: len(_FACULTY_RE[f].findall(text)) for f in options}
    best = max(options, key=lambda f: scores[f])
    if scores[best] == 0:
        return c.faculty_hints[0] if c.faculty_hints else None
    return best


def heuristic_label(c: Candidate) -> None:
    c.faculty = guess_faculty(c)
    c.topic = c.topic or c.title[:60]
    c.key_claim = c.key_claim or c.title
    c.surprise = c.surprise or 3


# --- Gemini triage --------------------------------------------------------------------------

FacultyName = Literal[FACULTIES]  # type: ignore[valid-type]


class _Label(BaseModel):
    index: int
    faculty: FacultyName
    topic: str = Field(description="the subject in 1-4 words, e.g. 'Heat pumps'")
    key_claim: str = Field(description="the single most interesting fact, one plain sentence")
    surprise: int = Field(ge=1, le=5)
    safe: bool
    safety_reason: str | None = None


class _Labels(BaseModel):
    labels: list[_Label]


TRIAGE_PROMPT = """You pick source articles for "Waypoint", an Instagram account that posts
short, true, surprising explainers in plain English for curious adults.

For each ARTICLE below return:
- faculty: the best fit from this list: {faculties}. If the article lists allowed
  faculties, choose from those.
- topic: the subject in 1-4 words (e.g. "Heat pumps", "Octopus camouflage").
- key_claim: the single most interesting, specific fact the article supports, in one plain
  sentence. Only facts stated in the article.
- surprise: 1-5. 5 = most people would say "wait, really?" and want to swipe; 3 = useful but
  familiar; 1 = dry, incremental or only interesting to specialists.
- safe: false if the article is mainly about politics or elections, war, crime, a tragedy
  or deaths, graphic content, a named private individual, or if a post would have to give
  personal medical, legal or financial ADVICE. Explaining how something works is safe.
  If false, give a short safety_reason.

{articles}"""


def _article_block(i: int, c: Candidate) -> str:
    hints = f"\nallowed faculties: {', '.join(c.faculty_hints)}" if c.faculty_hints else ""
    body = re.sub(r"\s+", " ", c.text)[:1500]
    return f"ARTICLE {i}\ntitle: {c.title}\nsource: {c.publisher}{hints}\ntext: {body}\n"


class TriageCache:
    def __init__(self, path: Path | None):
        self.path = path
        self.data: dict[str, dict] = {}
        if path and path.is_file():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                self.data = {}

    def get(self, c: Candidate, model: str) -> dict | None:
        return self.data.get(f"{model}|{url_key(c.url)}")

    def put(self, c: Candidate, model: str, label: dict) -> None:
        self.data[f"{model}|{url_key(c.url)}"] = label

    def save(self) -> None:
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, indent=1), encoding="utf-8")


def _apply(c: Candidate, label: dict) -> None:
    fac = label["faculty"]
    if c.faculty_hints and fac not in c.faculty_hints:
        fac = guess_faculty(c)  # model ignored the allowed list; fall back
    c.faculty = fac
    c.topic = label["topic"].strip()[:60]
    c.key_claim = label["key_claim"].strip()
    c.surprise = label["surprise"]
    c.safe = label["safe"]
    c.safety_reason = None if label["safe"] else (label.get("safety_reason") or "llm: unsafe")


def llm_label(cands: Sequence[Candidate], gemini: GeminiClient, batch_size: int,
              cache: TriageCache) -> list[Candidate]:
    """Label candidates in place; returns those that got a label (a failed batch drops out)."""
    model = gemini.cfg.fast_model
    todo = []
    for c in cands:
        hit = cache.get(c, model)
        if hit:
            _apply(c, hit)
        else:
            todo.append(c)
    log.info("triage: %d cached, %d to label", len(cands) - len(todo), len(todo))
    for start in range(0, len(todo), batch_size):
        batch = todo[start:start + batch_size]
        prompt = TRIAGE_PROMPT.format(
            faculties=", ".join(FACULTIES),
            articles="\n".join(_article_block(i, c) for i, c in enumerate(batch, 1)))
        res = gemini.generate_json(prompt, _Labels, purpose="triage")
        for label in res.labels:
            if 1 <= label.index <= len(batch):
                data = label.model_dump(exclude={"index"})
                _apply(batch[label.index - 1], data)
                cache.put(batch[label.index - 1], model, data)
    cache.save()
    return [c for c in cands if c.key_claim is not None]
