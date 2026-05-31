"""Semantic Hint Synthesis (SHS): LLM-grounded candidate generation.

Role (must stay consistent with the paper's critique of LLM-only fuzzing):
the LLM NEVER decides correctness. It is fed verified program facts extracted
from the target's own source (option, taint-sink type, comparison operand, a
source slice) and asked for ranked candidate VALUES. Every candidate is just an
ordinary KOFTA hint, accepted only if the forkserver shows new coverage or a 0
exit. A hallucination costs one fork. Program analysis supplies ground truth and
bounds the search; the LLM supplies semantics byte-mutation cannot reach.

This module is the "brain": prompt construction, JSON parsing, a persistent
prompt->response cache, an hourly call budget with graceful degradation, and
cost accounting. It does not touch the forkserver; validation stays in KOFTA.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Protocol


# ---- inputs / outputs ------------------------------------------------------

@dataclass
class BranchRecord:
    """Verified facts about one stuck / literal-operand branch."""
    option: str                 # e.g. "--format"
    sink_type: str              # "strcmp" | "switch-case" | "if-else"
    source_slice: str           # +-k source lines around the call site
    comparison_object: str = "" # variable being compared, if known
    observed_operand: str = ""  # a literal operand already seen at runtime

    def cache_key(self) -> str:
        h = hashlib.sha256()
        h.update(self.source_slice.encode("utf-8", "replace"))
        h.update(b"\x00")
        h.update(self.option.encode("utf-8", "replace"))
        return h.hexdigest()

    @classmethod
    def from_dict(cls, d: dict) -> "BranchRecord":
        return cls(
            option=str(d.get("option", "")),
            sink_type=str(d.get("sink_type", "")),
            source_slice=str(d.get("source_slice", "")),
            comparison_object=str(d.get("comparison_object", "")),
            observed_operand=str(d.get("observed_operand", "")),
        )


PROMPT_TEMPLATE = """You are assisting a fuzzer. Given an option and the
source code that parses its argument, list plausible
argument VALUES that drive execution down new branches.
Return ONLY a JSON array of strings, ranked most likely
first, at most {k} items, no explanation.

option: {option}
parser sink type: {sink_type}
source slice:
{source_slice}
"""


def build_prompt(rec: BranchRecord, k: int) -> str:
    slice_text = "\n".join("    " + ln for ln in rec.source_slice.splitlines())
    return PROMPT_TEMPLATE.format(k=k, option=rec.option,
                                  sink_type=rec.sink_type, source_slice=slice_text)


_JSON_ARRAY = re.compile(r"\[.*?\]", re.DOTALL)
_LITERAL = re.compile(r'"((?:[^"\\]|\\.)*)"')


def parse_candidates(text: str, k: int) -> list[str]:
    """Extract a ranked, de-duplicated candidate list from a model reply.

    Tolerant: accepts a bare JSON array, a fenced block, or prose containing
    an array. Falls back to nothing (never raises) so a malformed reply simply
    yields no hints rather than crashing the campaign.
    """
    m = _JSON_ARRAY.search(text)
    out: list[str] = []
    if m:
        try:
            arr = json.loads(m.group(0))
            if isinstance(arr, list):
                out = [str(x) for x in arr if isinstance(x, (str, int, float))]
        except json.JSONDecodeError:
            out = []
    seen, uniq = set(), []
    for c in out:
        c = c.strip()
        if c and c not in seen:
            seen.add(c)
            uniq.append(c)
        if len(uniq) >= k:
            break
    return uniq


# ---- LLM clients -----------------------------------------------------------

class LLMClient(Protocol):
    name: str
    def complete(self, prompt: str) -> tuple[str, int]:
        """Return (reply_text, total_tokens). total_tokens may be 0 if unknown."""
        ...


class AnthropicClient:
    """Off-the-shelf chat-completion endpoint. Temperature 0 for reproducibility."""

    def __init__(self, model: str, max_tokens: int = 256):
        import anthropic  # imported lazily so mock mode needs no key/SDK
        self._anthropic = anthropic
        self.client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
        self.model = model
        self.name = model
        self.max_tokens = max_tokens

    def complete(self, prompt: str) -> tuple[str, int]:
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        tokens = 0
        usage = getattr(resp, "usage", None)
        if usage is not None:
            tokens = int(getattr(usage, "input_tokens", 0)) + \
                     int(getattr(usage, "output_tokens", 0))
        return text, tokens


class MockClient:
    """Deterministic offline client for tests / CI (no API, no cost).

    Stands in for the LLM by echoing the string literals it finds in the source
    slice as a JSON array. This exercises the full plumbing but is NOT a model:
    it cannot synthesize values absent from the slice (which is exactly what the
    real LLM contributes), so never use it to produce paper numbers.
    """
    name = "mock"

    def complete(self, prompt: str) -> tuple[str, int]:
        lits = _LITERAL.findall(prompt)
        # drop the template's own quoted words by keeping slice-looking tokens
        cands = [l for l in lits if l and not l.isspace()]
        return json.dumps(cands), 0


def make_client(model: str | None, mock: bool) -> LLMClient:
    if mock or not model:
        return MockClient()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("no ANTHROPIC_API_KEY set; pass --mock for offline runs")
    return AnthropicClient(model)


# ---- cache, budget, cost ---------------------------------------------------

class Cache:
    """Persistent prompt->candidates cache. Parsing source of an option is fixed
    at compile time, so each branch is queried at most once per campaign."""

    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, list[str]] = {}
        if path.is_file():
            try:
                self.data = json.loads(path.read_text())
            except json.JSONDecodeError:
                self.data = {}

    def get(self, key: str) -> list[str] | None:
        return self.data.get(key)

    def put(self, key: str, candidates: list[str]) -> None:
        self.data[key] = candidates
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data))
        tmp.replace(self.path)


class Budget:
    """Sliding-window cap of B real LLM calls per hour."""

    def __init__(self, per_hour: int):
        self.per_hour = per_hour
        self.window: list[float] = []

    def allow(self, now: float | None = None) -> bool:
        if self.per_hour <= 0:
            return False
        now = time.time() if now is None else now
        self.window = [t for t in self.window if now - t < 3600.0]
        return len(self.window) < self.per_hour

    def charge(self, now: float | None = None) -> None:
        self.window.append(time.time() if now is None else now)


@dataclass
class Cost:
    llm_calls: int = 0
    cache_hits: int = 0
    budget_skips: int = 0
    tokens: int = 0
    latency_s: list[float] = field(default_factory=list)
    shs_wall_s: float = 0.0
    campaign_wall_s: float = 0.0

    def dump(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2))


# ---- the service -----------------------------------------------------------

class SHSService:
    def __init__(self, client: LLMClient, cache: Cache, budget: Budget,
                 k: int = 8, cost: Cost | None = None):
        self.client = client
        self.cache = cache
        self.budget = budget
        self.k = k
        self.cost = cost or Cost()

    def query(self, rec: BranchRecord) -> list[str]:
        """Return ranked candidate values for one stuck branch.

        Cache hit -> free. Budget exhausted -> [] (KOFTA degrades to byte-level
        mutation, never worse than baseline). Otherwise one LLM call.
        """
        key = rec.cache_key()
        cached = self.cache.get(key)
        if cached is not None:
            self.cost.cache_hits += 1
            return cached[: self.k]

        if not self.budget.allow():
            self.cost.budget_skips += 1
            return []

        prompt = build_prompt(rec, self.k)
        t0 = time.time()
        text, tokens = self.client.complete(prompt)
        dt = time.time() - t0
        self.budget.charge()
        self.cost.llm_calls += 1
        self.cost.tokens += tokens
        self.cost.latency_s.append(dt)
        self.cost.shs_wall_s += dt

        candidates = parse_candidates(text, self.k)
        self.cache.put(key, candidates)
        return candidates
