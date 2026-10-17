"""Figures the writer may ask for, and their layout into Waypoint visuals.js specs.

The writer fills a small, flat `FigureDraft` (easy for structured output to get right);
`build_figure` turns it into a standard visuals.js `chart` or `diagram` spec, which is what
post.json stores (PLAN.md 3.2). Diagrams are laid out here, so the LLM never writes
coordinates. Layout limits that would make labels collide raise FigureError, and the message
goes back to the writer as feedback.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

FigureKind = Literal["column", "bar", "line", "timeline", "steps"]


class SeriesDraft(BaseModel):
    name: str = Field(description="short series label, e.g. '7% a year'")
    values: list[float] = Field(description="one value per x label")
    highlight: bool = Field(description="true for the one series the story is about")


class FigureDraft(BaseModel):
    kind: FigureKind
    title: str = Field(description="states the takeaway, <= 8 words, e.g. 'Bees count to four'")
    labels: list[str] = Field(
        description="column/bar: category names; line: x-axis labels; timeline: event names; "
                    "steps: step names")
    values: list[float] = Field(
        description="column/bar: one value per label, taken from SOURCE. Empty otherwise.")
    series: list[SeriesDraft] = Field(description="line only: 1-3 series. Empty otherwise.")
    notes: list[str] = Field(
        description="timeline: the date of each event (e.g. 'around 3200 BCE'); steps: an "
                    "optional short line under each step. Empty otherwise.")
    highlight: int | None = Field(
        description="column/bar/timeline/steps: 0-based index of the one highlighted item")
    prefix: str = Field(description="value prefix such as '$', or empty")
    suffix: str = Field(description="value suffix such as '%' or 'k', or empty")


class FigureError(ValueError):
    pass


# Diagram layout (viewBox units; the renderer scales the SVG to the card width).
TIMELINE_W, TIMELINE_H, TIMELINE_MARGIN = 540, 150, 80
STEPS_W, STEPS_H, STEPS_MARGIN = 600, 130, 80
CHAR_W = 9.2        # visuals.js nodeBox: ~9.2 units per label character
SUB_CHAR_W = 7.6    # sub-labels are drawn smaller
MIN_GAP = 34        # space between neighbouring steps for the arrow


def _need(cond: bool, msg: str) -> None:
    if not cond:
        raise FigureError(msg)


def _highlight(fd: FigureDraft, n: int) -> int | None:
    if fd.highlight is None:
        return None
    _need(0 <= fd.highlight < n, f"highlight index {fd.highlight} is out of range")
    return fd.highlight


def _chart(fd: FigureDraft, source: str) -> dict[str, Any]:
    spec: dict[str, Any] = {"type": "chart", "kind": fd.kind, "title": fd.title}
    if fd.prefix:
        spec["prefix"] = fd.prefix
    if fd.suffix:
        spec["suffix"] = fd.suffix
    if fd.kind in ("column", "bar"):
        n = len(fd.labels)
        _need(2 <= n <= 6, f"a {fd.kind} chart needs 2-6 categories, got {n}")
        _need(len(fd.values) == n, f"{n} labels but {len(fd.values)} values")
        _need(all(len(lbl) <= 22 for lbl in fd.labels), "category labels must be <= 22 chars")
        spec["data"] = [[lbl, v] for lbl, v in zip(fd.labels, fd.values, strict=True)]
        hi = _highlight(fd, n)
        if hi is not None:
            spec["highlight"] = hi
    else:  # line
        n = len(fd.labels)
        _need(3 <= n <= 8, f"a line chart needs 3-8 x labels, got {n}")
        _need(1 <= len(fd.series) <= 3, "a line chart needs 1-3 series")
        _need(sum(s.highlight for s in fd.series) <= 1, "highlight at most one series")
        for s in fd.series:
            _need(len(s.values) == n, f"series {s.name!r} needs {n} values")
            _need(len(s.name) <= 14, f"series name {s.name!r} must be <= 14 chars")
        spec["x"] = list(fd.labels)
        spec["series"] = [{"name": s.name, "values": list(s.values),
                           **({"highlight": True} if s.highlight else {})} for s in fd.series]
    spec["source"] = source
    return spec


def _fits(texts: list[str], spacing: float, char_w: float, what: str) -> None:
    room = int((spacing - MIN_GAP) / char_w)
    for t in texts:
        _need(len(t) <= room, f"{what} {t!r} is too long for {len(texts)} items "
                              f"(max {room} characters; use fewer items or shorter words)")


def _timeline(fd: FigureDraft) -> dict[str, Any]:
    n = len(fd.labels)
    _need(2 <= n <= 4, f"a timeline needs 2-4 events, got {n}")
    _need(len(fd.notes) in (0, n), f"a timeline needs one date per event ({n})")
    xs = [TIMELINE_MARGIN + i * (TIMELINE_W - 2 * TIMELINE_MARGIN) / (n - 1) for i in range(n)]
    spacing = xs[1] - xs[0]
    _fits(fd.labels, spacing, CHAR_W, "event name")
    _fits(fd.notes, spacing, SUB_CHAR_W, "date")
    hi = _highlight(fd, n)
    nodes, edges = [], []
    for i, (x, label) in enumerate(zip(xs, fd.labels, strict=True)):
        color = "hot" if i == hi else "grey"
        nodes.append({"id": f"d{i}", "at": [round(x), 40], "shape": "circle", "w": 18,
                      "label": "", "color": color})
        node = {"id": f"t{i}", "at": [round(x), 90], "shape": "text", "label": label,
                "color": "hot" if i == hi else "white"}
        if fd.notes:
            node["sub"] = fd.notes[i]
        nodes.append(node)
        if i:
            edges.append({"from": f"d{i - 1}", "to": f"d{i}", "color": "grey", "arrow": False})
    return {"type": "diagram", "title": fd.title, "size": [TIMELINE_W, TIMELINE_H],
            "nodes": nodes, "edges": edges}


def _steps(fd: FigureDraft) -> dict[str, Any]:
    n = len(fd.labels)
    _need(2 <= n <= 4, f"a steps diagram needs 2-4 steps, got {n}")
    _need(len(fd.notes) in (0, n), f"steps need one note per step ({n}) or none")
    xs = [STEPS_MARGIN + i * (STEPS_W - 2 * STEPS_MARGIN) / (n - 1) for i in range(n)]
    spacing = xs[1] - xs[0]
    _fits(fd.labels, spacing - 22, CHAR_W, "step name")
    _fits(fd.notes, spacing - 22, SUB_CHAR_W, "step note")
    hi = _highlight(fd, n)
    box_w = round(spacing - MIN_GAP)
    nodes, edges = [], []
    for i, (x, label) in enumerate(zip(xs, fd.labels, strict=True)):
        node = {"id": f"s{i}", "at": [round(x), STEPS_H // 2], "label": label, "w": box_w,
                "color": "hot" if i == hi else "blue"}
        if fd.notes and fd.notes[i]:
            node["sub"] = fd.notes[i]
        nodes.append(node)
        if i:
            edges.append({"from": f"s{i - 1}", "to": f"s{i}", "color": "grey"})
    return {"type": "diagram", "title": fd.title, "size": [STEPS_W, STEPS_H],
            "nodes": nodes, "edges": edges}


def build_figure(fd: FigureDraft, source: str) -> dict[str, Any]:
    """FigureDraft -> visuals.js spec. Raises FigureError with a writer-facing message."""
    _need(bool(fd.title.strip()), "a figure needs a title that states the takeaway")
    _need(len(fd.title.split()) <= 10, "figure title must be <= 10 words")
    if fd.kind in ("column", "bar", "line"):
        return _chart(fd, source)
    if fd.kind == "timeline":
        return _timeline(fd)
    return _steps(fd)


def figure_data_numbers(fd: FigureDraft) -> list[float]:
    """The numeric data in a figure (values and series values)."""
    return list(fd.values) + [v for s in fd.series for v in s.values]


def figure_texts(fd: FigureDraft) -> list[str]:
    """Every text in a figure, for the number check on labels, dates and titles."""
    return [fd.title, *fd.labels, *fd.notes, *(s.name for s in fd.series)]
