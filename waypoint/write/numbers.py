"""Programmatic number check (PLAN.md 4.3): numbers we publish must come from SOURCE.

- Figure data and every number in a figure's text must appear in the source exactly
  (allowing a display scale: a value of 76.1 with suffix 'k' matches 76,100 in the source).
- Numbers in slide copy and the caption may be rounded (within 5%), except years, which must
  match exactly. Small counting numbers (integers up to 10) in copy are not checked: they are
  usually structure ("3 steps"), and the LLM fact-check covers their meaning.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable

NUM_RE = re.compile(r"(?<![\w.])\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![\w.,])\d+(?:\.\d+)?")
SCALE_WORDS = {"thousand": 1e3, "million": 1e6, "billion": 1e9, "trillion": 1e12}
SCALE_RE = re.compile(
    r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*(thousand|million|billion|trillion)", re.I)
WORD_NUMBERS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100, "thousand": 1000,
    "million": 1e6, "billion": 1e9, "dozen": 12, "half": 0.5, "twice": 2, "double": 2,
    "triple": 3, "quarter": 0.25,
}
WORD_RE = re.compile(r"\b(" + "|".join(WORD_NUMBERS) + r")\b", re.I)
SPANS = {  # spans of time a writer may restate in numbers ("past half-century" = 50 years)
    r"half[- ](?:a[- ])?century": 50, r"quarter[- ](?:of[- ]a[- ])?century": 25,
    r"century": 100, r"decade": 10, r"millenni(?:um|a)": 1000,
}
SPAN_RE = {re.compile(rf"\b{k}\b", re.I): v for k, v in SPANS.items()}
DISPLAY_SCALES =(1, 1e3, 1e6, 1e9, 1e12)
COPY_TOLERANCE = 0.05
SMALL_COPY_INT = 10


def parse_numbers(text: str) -> list[float]:
    return [float(m.replace(",", "")) for m in NUM_RE.findall(text)]


def source_numbers(text: str) -> set[float]:
    """Every number the source states: digits, scaled digits ('1.5 million'), number words."""
    nums = set(parse_numbers(text))
    for digits, word in SCALE_RE.findall(text):
        nums.add(float(digits.replace(",", "")) * SCALE_WORDS[word.lower()])
    nums |= {float(WORD_NUMBERS[w.lower()]) for w in WORD_RE.findall(text)}
    nums |= {float(v) for rx, v in SPAN_RE.items() if rx.search(text)}
    return nums


def _close(a: float, b: float, rel: float) -> bool:
    return math.isclose(a, b, rel_tol=rel, abs_tol=1e-9)


def is_year(v: float) -> bool:
    return v.is_integer() and 1000 <= v <= 2100


def exact_match(v: float, nums: set[float]) -> bool:
    return any(_close(v * k, m, 1e-6) for m in nums for k in DISPLAY_SCALES)


def copy_match(v: float, nums: set[float]) -> bool:
    if v.is_integer() and v <= SMALL_COPY_INT:
        return True
    if is_year(v):
        return v in nums
    return any(_close(v * k, m, COPY_TOLERANCE) for m in nums for k in DISPLAY_SCALES)


def _fmt(v: float) -> str:
    return f"{v:g}"


def check_numbers(source: str, figure_data: Iterable[float], figure_text: Iterable[str],
                  copy_text: Iterable[str]) -> list[str]:
    """Problems, one string per number that the source does not support (empty = pass)."""
    nums = source_numbers(source)
    problems = []
    for v in figure_data:
        if not exact_match(v, nums):
            problems.append(f"figure value {_fmt(v)} does not appear in SOURCE")
    for t in figure_text:
        for v in parse_numbers(t):
            if not exact_match(v, nums):
                problems.append(f"number {_fmt(v)} in figure text {t!r} is not in SOURCE")
    for t in copy_text:
        for v in parse_numbers(t):
            if not copy_match(v, nums):
                problems.append(f"number {_fmt(v)} in {t[:60]!r} is not in SOURCE")
    return problems
