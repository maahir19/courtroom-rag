"""The three agents: Prosecution (for), Defense (against) and the Judge.

Each agent has an LLM version and an offline (rule-based) version. The offline one
exists because (a) free API quotas run out at the worst time and (b) it's a decent
baseline to compare the LLM against in the report.
"""

from __future__ import annotations

from . import prompts
from .citations import clean_points
from .llm import LLM
from .retriever import HybridRetriever
from .text_utils import cue_scores, split_sentences, tokenize

SIDES = {
    "for": {"role": "the Prosecution", "stance": "TRUE / supported by the documents"},
    "against": {"role": "the Defense", "stance": "FALSE / not supported by the documents"},
}


def format_exhibits(exhibits: list[dict]) -> str:
    lines = []
    for e in exhibits:
        where = e["title"] + (f" › {e['section']}" if e.get("section") else "")
        lines.append(f"[{e['id']}] ({where})\n{e['text']}")
    return "\n\n".join(lines) if lines else "(no exhibits found)"


def format_points(points: list[dict]) -> str:
    good = [p for p in points if not p.get("struck")]
    if not good:
        return "(no admissible points)"
    return "\n".join(f"- {p['text']} [evidence: {', '.join(p['evidence'])}]" for p in good)


# ---------------------------------------------------------------- offline helpers

def _best_sentence(text: str, claim_tokens: set[str], side: str, avoid: set[str] | None = None) -> tuple[str, float]:
    best, best_score = "", -1e9
    for s in split_sentences(text):
        if avoid and s in avoid:
            continue
        toks = set(tokenize(s))
        overlap = len(toks & claim_tokens) / (len(claim_tokens) or 1)
        sup, opp = cue_scores(s)
        lean = (sup - opp) if side == "for" else (opp - sup)
        score = overlap * 3 + 0.2 * lean  # 0.08 was too low, 'for' side kept quoting 'not permitted' lines
        if score > best_score:
            best, best_score = s, score
    return best, best_score


def _short(title: str) -> str:
    t = title.split("—")[-1].split(":")[0].strip()
    return t if len(t) < 60 else t[:57] + "..."


OPEN_TEMPLATES = {
    "for": ["{src} directly backs the claim: “{q}”",
            "Look at {src}: “{q}” That is consistent with the claim.",
            "{src} adds to this: “{q}”"],
    "against": ["{src} says otherwise: “{q}”",
                "The claim ignores a condition in {src}: “{q}”",
                "{src} also limits it: “{q}”"],
}


class Advocate:
    def __init__(self, side: str, llm: LLM, retriever: HybridRetriever):
        self.side = side
        self.llm = llm
        self.retriever = retriever

    def opening(self, claim: str, pool: list[dict]) -> dict:
        allowed = {e["id"] for e in pool}
        if self.llm.enabled:
            sys = prompts.LAWYER_SYSTEM.format(**SIDES[self.side])
            usr = prompts.LAWYER_OPENING_USER.format(claim=claim, exhibits=format_exhibits(pool))
            data = self.llm.chat_json(sys, usr)
            return {"summary": str(data.get("summary", "")).strip(),
                    "points": clean_points(data.get("points", []), allowed)}
        return self._offline_opening(claim, pool)

    def rebuttal(self, claim: str, pool: list[dict], opponent: dict, opp_pool: list[dict]) -> dict:
        cited = {e for p in opponent["points"] for e in p["evidence"]}
        extra = [e for e in opp_pool if e["id"] in cited and e["id"] not in {x["id"] for x in pool}]
        full = pool + extra
        allowed = {e["id"] for e in full}
        if self.llm.enabled:
            sys = prompts.LAWYER_SYSTEM.format(**SIDES[self.side])
            usr = prompts.LAWYER_REBUTTAL_USER.format(claim=claim, opponent_points=format_points(opponent["points"]),
                                                      exhibits=format_exhibits(full))
            data = self.llm.chat_json(sys, usr)
            return {"summary": str(data.get("summary", "")).strip(),
                    "points": clean_points(data.get("points", []), allowed)}
        return self._offline_rebuttal(claim, pool, opponent, full)

    # -- offline versions -----------------------------------------------

    def _offline_opening(self, claim, pool):
        ct = set(tokenize(claim))
        ranked, used = [], set()
        for e in pool:
            s, sc = _best_sentence(e["text"], ct, self.side, avoid=used)
            if s:
                used.add(s)
                ranked.append((sc + e["score"], e, s))
        ranked.sort(key=lambda x: -x[0])
        points = []
        for i, (_, e, s) in enumerate(ranked[:3]):
            tpl = OPEN_TEMPLATES[self.side][i % 3]
            points.append({"text": tpl.format(src=_short(e["title"]), q=s), "evidence": [e["id"]],
                           "invalid": [], "struck": False})
        if self.side == "for":
            summary = "The documents contain passages that back the claim." if points else "I couldn't find support for the claim."
        else:
            summary = "The documents put limits or conditions on the claim." if points else "I couldn't find anything against the claim."
        return {"summary": summary, "points": points}

    def _offline_rebuttal(self, claim, pool, opponent, full):
        ct = set(tokenize(claim))
        by_id = {e["id"]: e for e in full}
        points, used = [], set()
        for op in [p for p in opponent["points"] if not p["struck"]][:2]:
            target = op["evidence"][0]
            # first check if the opponent's own exhibit has a sentence that cuts our way
            quoted = {p["text"] for p in points}
            own_s, own_sc = _best_sentence(by_id[target]["text"], ct, self.side) if target in by_id else ("", -1)
            if any(own_s and own_s in q for q in quoted):
                own_s = ""
            sup, opp = cue_scores(own_s) if own_s else (0, 0)
            lean = (sup - opp) if self.side == "for" else (opp - sup)
            if own_s and lean > 0 and own_s not in op["text"]:
                points.append({"text": f"The other side leans on {target}, but the same exhibit also says: “{own_s}”",
                               "evidence": [target], "invalid": [], "struck": False})
                continue
            # otherwise counter with our strongest unused exhibit
            for e in pool:
                if e["id"] in used or e["id"] == target:
                    continue
                s, _ = _best_sentence(e["text"], ct, self.side)
                if s:
                    used.add(e["id"])
                    points.append({"text": f"Reading {target} alone is misleading. {e['id']} says: “{s}”",
                                   "evidence": [e["id"], target], "invalid": [], "struck": False})
                    break
        return {"summary": "Responding to the other side's strongest points.", "points": points}


class Judge:
    def __init__(self, llm: LLM, retriever: HybridRetriever):
        self.llm = llm
        self.retriever = retriever

    def rule(self, claim: str, for_points: list[dict], against_points: list[dict], admitted: list[dict]) -> dict:
        allowed = {e["id"] for e in admitted}
        if not admitted:
            return {"verdict": "INSUFFICIENT", "confidence": 0,
                    "headline": "Neither side presented admissible evidence, so the court cannot rule.",
                    "reasoning": [], "weak_points": [], "struck_reasoning": []}
        if self.llm.enabled:
            usr = prompts.JUDGE_USER.format(claim=claim, for_points=format_points(for_points),
                                            against_points=format_points(against_points),
                                            exhibits=format_exhibits(admitted))
            data = self.llm.chat_json(prompts.JUDGE_SYSTEM, usr)
            return self._validate(data, allowed)
        return self._offline_rule(claim, for_points, against_points, admitted)

    def _validate(self, data: dict, allowed: set[str]) -> dict:
        reasoning = clean_points(data.get("reasoning", []), allowed)
        kept = [r for r in reasoning if not r["struck"]]
        struck = [r for r in reasoning if r["struck"]]
        verdict = str(data.get("verdict", "INSUFFICIENT")).upper().replace(" ", "_")
        if verdict not in {"SUPPORTED", "NOT_SUPPORTED", "MIXED", "INSUFFICIENT"}:
            verdict = "MIXED"
        try:
            conf = max(0, min(100, int(float(data.get("confidence", 50)))))
        except (TypeError, ValueError):
            conf = 50
        headline = str(data.get("headline", "")).strip()
        if not kept and verdict != "INSUFFICIENT":
            # judge tried to rule without citing admitted evidence -> not allowed
            verdict, conf = "INSUFFICIENT", min(conf, 20)
            headline = "The ruling cited no admitted exhibits, so it was overturned to 'insufficient evidence'."
        return {"verdict": verdict, "confidence": conf, "headline": headline, "reasoning": kept,
                "weak_points": [str(w) for w in (data.get("weak_points") or [])][:4],
                "struck_reasoning": struck}

    def _offline_rule(self, claim, for_points, against_points, admitted):
        rel = self.retriever.relevance(claim)
        idx = {c.id: i for i, c in enumerate(self.retriever.chunks)}
        max_rel = max((rel[idx[e["id"]]] for e in admitted), default=0)

        claim_toks = set(tokenize(claim))

        def weight(points, side):
            """weight = how relevant the cited exhibit is * how on-topic the quoted line is,
            scaled by whether the line actually leans this side's way. A 'for' point that
            quotes "...is not permitted" gets heavily discounted."""
            total, best = 0.0, None
            for p in points:
                if p["struck"]:
                    continue
                r = max(rel[idx[e]] for e in p["evidence"] if e in idx)
                quote = p["text"].split("“")[-1] if "“" in p["text"] else p["text"]
                q_toks = set(tokenize(quote))
                overlap = len(q_toks & claim_toks) / (len(claim_toks) or 1)
                sup, opp = cue_scores(quote)
                lean = (sup - opp) if side == "for" else (opp - sup)
                stance = 1 + 0.12 * min(lean, 8) if lean >= 0 else 0.35
                w = (0.5 * r + 0.5 * min(overlap * 2, 1)) * stance
                total += w
                if best is None or w > best[0]:
                    best = (w, p)
            return total, best

        _, bf = weight(for_points, "for")
        _, ba = weight(against_points, "against")

        # The actual decision: read the admitted exhibits myself (not the lawyers' spin).
        # For each exhibit take its 2 most on-topic sentences and check which way they lean.
        # Negations count 1.2x because policies say "not permitted" once and mean it.
        pro = con = 0.0
        for e in admitted:
            r = rel[idx[e["id"]]]
            sents = sorted(split_sentences(e["text"]),
                           key=lambda x: -len(set(tokenize(x)) & claim_toks))[:2]
            for sent in sents:
                ov = len(set(tokenize(sent)) & claim_toks) / (len(claim_toks) or 1)
                if ov == 0:
                    continue
                sup, opp = cue_scores(sent)
                lean = sup - 1.2 * opp
                if lean > 0:
                    pro += r * ov
                elif lean < 0:
                    con += r * ov
        if max_rel < 0.3 or (pro + con) < 0.05:
            verdict, conf = "INSUFFICIENT", 25
        else:
            ratio = pro / (pro + con)
            if ratio > 0.6:
                verdict = "SUPPORTED"
            elif ratio < 0.4:
                verdict = "NOT_SUPPORTED"
            else:
                verdict = "MIXED"
            conf = int(45 + abs(ratio - 0.5) * 80)
        headlines = {
            "SUPPORTED": "On the admitted exhibits, the claim holds.",
            "NOT_SUPPORTED": "The admitted exhibits contradict the claim.",
            "MIXED": "The claim is only partly true; it depends on conditions in the exhibits.",
            "INSUFFICIENT": "The exhibits don't address the claim closely enough to rule.",
        }
        reasoning = []
        for side_name, best in (("Prosecution", bf), ("Defense", ba)):
            if best:
                reasoning.append({"text": f"Strongest {side_name.lower()} point: {best[1]['text']}",
                                  "evidence": best[1]["evidence"], "invalid": [], "struck": False})
        reasoning.sort(key=lambda r: 0 if (verdict == "NOT_SUPPORTED") == r["text"].startswith("Strongest defense") else 1)
        return {"verdict": verdict, "confidence": conf, "headline": headlines[verdict], "reasoning": reasoning,
                "weak_points": ["Offline judge: weights are keyword heuristics. Use an LLM for a real ruling."],
                "struck_reasoning": []}
