"""The 'court clerk'. Checks that every cited exhibit actually exists in the pool the
agent was allowed to see. This is the part that keeps the judge honest — the judge
never sees an exhibit that wasn't properly cited by one of the lawyers.
"""

from __future__ import annotations

import re

ID_RE = re.compile(r"\bE\d+\b")


def ids_in(text: str) -> list[str]:
    return ID_RE.findall(text or "")


def clean_points(points: list, allowed: set[str]) -> list[dict]:
    """Normalise model output and mark points with no valid evidence as struck."""
    out = []
    for p in points or []:
        if isinstance(p, str):  # sometimes the model returns plain strings
            p = {"text": p, "evidence": []}
        text = str(p.get("text", "")).strip()
        if not text:
            continue
        raw = p.get("evidence") or []
        if isinstance(raw, str):
            raw = ids_in(raw)
        cited = list(dict.fromkeys([str(e).strip().upper() for e in raw] + ids_in(text)))
        valid = [e for e in cited if e in allowed]
        invalid = [e for e in cited if e not in allowed]
        out.append({
            "text": text,
            "evidence": valid,
            "invalid": invalid,
            "struck": len(valid) == 0,
        })
    return out


def admitted_from(*point_lists: list[dict]) -> list[str]:
    seen = []
    for pl in point_lists:
        for p in pl:
            if p.get("struck"):
                continue
            for e in p["evidence"]:
                if e not in seen:
                    seen.append(e)
    # sort E2 < E10 numerically, not as strings
    return sorted(seen, key=lambda x: int(x[1:]))
