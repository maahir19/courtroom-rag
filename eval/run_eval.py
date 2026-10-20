"""Tiny eval: runs every claim in expected.csv and checks the verdict.

These labels are MY judgement after reading the docs (some claims are honestly
debatable, so a few rows accept two answers, written as A|B).

    python eval/run_eval.py
"""

import csv
import os
import sys
from pathlib import Path

os.environ.setdefault("OFFLINE_STAGE_DELAY", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from courtroom.llm import LLM  # noqa: E402
from courtroom.trial import run_trial  # noqa: E402


def main():
    rows = list(csv.DictReader(open(Path(__file__).parent / "expected.csv", encoding="utf-8")))
    print(f"mode: {LLM().describe()['label']}\n")
    hits = 0
    for r in rows:
        verdict = next(e for e in run_trial(r["claim"], r["case"]) if e["type"] == "verdict")
        ok = verdict["verdict"] in r["expected"].split("|")
        hits += ok
        print(f"{'✓' if ok else '✗'}  {verdict['verdict']:<14} (want {r['expected']:<22}) {r['claim']}")
    print(f"\n{hits}/{len(rows)} correct ({hits / len(rows):.0%})")


if __name__ == "__main__":
    main()
