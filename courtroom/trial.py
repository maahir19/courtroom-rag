"""Runs one full trial and yields events as it goes (so the UI can show it live).

Order: retrieval (both sides) -> openings -> rebuttals -> clerk admits evidence -> verdict
"""

from __future__ import annotations

import time
from functools import lru_cache

from . import config
from .agents import Advocate, Judge
from .citations import admitted_from
from .ingest import load_collection
from .llm import LLM, LLMError
from .retriever import HybridRetriever

# stance hints appended to the query. small thing but it noticeably changes what each
# side retrieves, otherwise both lawyers get the exact same top-6
QUERY_HINTS = {
    "for": "allowed eligible yes supports benefit",
    "against": "not except only limitation exception however risk",
}


@lru_cache(maxsize=16)
def get_retriever(case_key: str) -> HybridRetriever:
    folder = config.DATA_DIR / case_key
    return HybridRetriever(load_collection(folder))


def reset_cache():
    get_retriever.cache_clear()


def run_trial(claim: str, case_key: str, rounds: int = 2):
    t0 = time.time()
    llm = LLM()
    retriever = get_retriever(case_key)
    pause = 0 if llm.enabled else config.OFFLINE_STAGE_DELAY

    yield {"type": "start", "claim": claim, "case": case_key, "mode": llm.describe(),
           "chunks": len(retriever.chunks)}

    if not retriever.chunks:
        yield {"type": "error", "message": "This case file has no readable documents. Add .md, .txt or .pdf files."}
        return

    pools = {}
    for side in ("for", "against"):
        pools[side] = retriever.search(f"{claim} {QUERY_HINTS[side]}", side=side)
        time.sleep(pause / 2)
        yield {"type": "retrieval", "side": side, "exhibits": pools[side]}

    lawyers = {s: Advocate(s, llm, retriever) for s in ("for", "against")}

    def safe(fn, *args):
        """If the LLM dies mid-trial, finish this step offline instead of crashing."""
        try:
            return fn(*args), None
        except LLMError as e:
            llm.provider = "offline"
            return fn(*args), f"LLM error, switched to offline mode for the rest of this trial ({e})"

    args = {}
    for side in ("for", "against"):
        res, warn = safe(lawyers[side].opening, claim, pools[side])
        if warn:
            yield {"type": "notice", "message": warn}
        args[side] = {"opening": res}
        time.sleep(pause)
        yield {"type": "argument", "side": side, "round": "opening", **res}

    if rounds >= 2:
        for side, other in (("for", "against"), ("against", "for")):
            res, warn = safe(lawyers[side].rebuttal, claim, pools[side], args[other]["opening"], pools[other])
            if warn:
                yield {"type": "notice", "message": warn}
            args[side]["rebuttal"] = res
            time.sleep(pause)
            yield {"type": "argument", "side": side, "round": "rebuttal", **res}

    def all_points(side):
        a = args[side]
        return a["opening"]["points"] + a.get("rebuttal", {}).get("points", [])

    for_pts, against_pts = all_points("for"), all_points("against")
    admitted_ids = admitted_from(for_pts, against_pts)
    retrieved_ids = {e["id"] for p in pools.values() for e in p}
    struck = [p for p in for_pts + against_pts if p["struck"]]
    admitted = [retriever.by_id[i].to_dict() for i in admitted_ids]
    yield {"type": "admitted", "ids": admitted_ids,
           "unused": sorted(retrieved_ids - set(admitted_ids), key=lambda x: int(x[1:])),
           "struck_points": len(struck)}

    judge = Judge(llm, retriever)
    ruling, warn = safe(judge.rule, claim, for_pts, against_pts, admitted)
    if warn:
        yield {"type": "notice", "message": warn}
    time.sleep(pause)
    yield {"type": "verdict", **ruling}
    yield {"type": "done", "seconds": round(time.time() - t0, 2)}
