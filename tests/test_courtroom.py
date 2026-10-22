"""Run with:  pytest -q"""

import os

os.environ["OFFLINE_STAGE_DELAY"] = "0"

from courtroom import config  # noqa: E402
from courtroom.agents import Judge  # noqa: E402
from courtroom.citations import admitted_from, clean_points  # noqa: E402
from courtroom.ingest import load_collection  # noqa: E402
from courtroom.llm import LLM, parse_json  # noqa: E402
from courtroom.retriever import HybridRetriever  # noqa: E402
from courtroom.trial import get_retriever, run_trial  # noqa: E402

POLICIES = config.DATA_DIR / "company_policies"


def test_chunks_have_unique_ids_and_skip_disclaimer():
    chunks = load_collection(POLICIES)
    ids = [c.id for c in chunks]
    assert len(ids) == len(set(ids))
    assert not any("Synthetic sample document" in c.text for c in chunks)


def test_retriever_finds_outside_india_rule_for_dubai():
    r = HybridRetriever(load_collection(POLICIES))
    hits = r.search("Can I work from Dubai for three weeks?", side="against")
    assert any("outside India" in h["text"] for h in hits[:3])


def test_max_per_doc_cap():
    r = HybridRetriever(load_collection(POLICIES))
    hits = r.search("leave encashment carry forward", k=10)
    docs = [h["doc"] for h in hits]
    assert max(docs.count(d) for d in set(docs)) <= config.MAX_PER_DOC


def test_made_up_citations_are_struck():
    pts = clean_points([{"text": "trust me", "evidence": ["E999"]},
                        {"text": "real one", "evidence": ["E1"]}], allowed={"E1", "E2"})
    assert pts[0]["struck"] and pts[0]["invalid"] == ["E999"]
    assert not pts[1]["struck"]
    assert admitted_from(pts) == ["E1"]


def test_parse_json_handles_code_fences():
    assert parse_json('Sure!\n```json\n{"a": 1}\n```') == {"a": 1}


class FakeLLM(LLM):
    """Pretends to be a model. Lawyers cite one real and one fake exhibit;
    judge tries to rely on an exhibit nobody cited."""

    def __init__(self):
        super().__init__()
        self.provider = "fake"
        self.base_url = "http://fake"

    @property
    def enabled(self):
        return True

    def chat_json(self, system, user, retries=0):
        if "impartial judge" in system:
            return {"verdict": "SUPPORTED", "confidence": 90, "headline": "yes",
                    "reasoning": [{"text": "because of E40", "evidence": ["E40"]}]}
        first_id = user.split("[", 1)[1].split("]", 1)[0]
        return {"summary": "x", "points": [{"text": "good point", "evidence": [first_id]},
                                           {"text": "invented point", "evidence": ["E777"]}]}


def test_judge_cannot_use_unadmitted_evidence():
    r = get_retriever("company_policies")
    admitted = [r.chunks[0].to_dict()]
    ruling = Judge(FakeLLM(), r).rule("claim", [], [], admitted)
    assert ruling["verdict"] == "INSUFFICIENT"
    assert ruling["struck_reasoning"]


def test_full_trial_with_fake_llm(monkeypatch):
    import courtroom.trial as trial
    monkeypatch.setattr(trial, "LLM", FakeLLM)
    events = list(run_trial("Interns get the home setup allowance", "company_policies"))
    args = [e for e in events if e["type"] == "argument"]
    assert all(any(p["struck"] for p in a["points"]) for a in args)  # E777 always struck
    admitted = next(e for e in events if e["type"] == "admitted")
    assert "E777" not in admitted["ids"]
    assert events[-1]["type"] == "done"


def test_offline_trial_runs_for_every_sample_claim():
    for case in ("company_policies", "job_offer", "apple_policies", "reliance_governance"):
        claims = (config.DATA_DIR / case / "claims.txt").read_text().splitlines()
        for c in claims[:2]:
            types = [e["type"] for e in run_trial(c, case)]
            assert types[0] == "start" and "verdict" in types
