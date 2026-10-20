"""Tokenizing + a tiny stance lexicon.

I didn't want to pull in nltk/spacy just for stopwords and stemming, so this is a
hand-rolled version. It's crude but it's ~good enough for policy docs and papers.
"""

import re

STOPWORDS = set("""
a an the and or but if of to in on at for from by with as is are was were be been being
it its this that these those i me my we our you your he she they them their his her
do does did done can could should would will shall may might must not no yes than then
so such into about over under up down out very just also only more most less least
there here what which who whom whose when where why how all any both each few other some
own same too s t don now get got have has had having am per via vs
""".split())
# NOTE: "not", "no", "only" are in STOPWORDS for retrieval matching, but the stance
# lexicon below still looks at the raw text, so negations aren't lost.
# took me an embarrassingly long time to realise this mattered.

# people write the same thing in 5 ways. tf-idf can't know WFH == work from home.
SYNONYMS = {
    "wfh": "remote",
    "remotely": "remote",
    "abroad": "outside india",
    "overseas": "outside india",
    "dubai": "outside india",
    "leaves": "leave",
    "payout": "encash",
    "paid out": "encash",
    "encashment": "encash",
    "reimburse": "reimburs",
    "reimbursed": "reimburs",
    "reimbursable": "reimburs",
    "coffee": "caffeine",
    "laptops": "laptop",
    "ctc": "salary",
    "pay": "salary",
    "pays": "salary",
}


def _stem(w: str) -> str:
    # poor man's porter stemmer. good enough, don't judge
    for suf in ("ations", "ation", "ments", "ment", "ingly", "ing", "edly", "ed", "ies", "es", "ly", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[: -len(suf)] + ("y" if suf == "ies" else "")
    return w


def tokenize(text: str) -> list[str]:
    text = text.lower().replace("₹", " rupees ")
    for k, v in SYNONYMS.items():
        if " " in k:
            text = text.replace(k, v)
    words = re.findall(r"[a-z0-9]+", text)
    out = []
    for w in words:
        w = SYNONYMS.get(w, w)
        for part in w.split():
            if part and part not in STOPWORDS:
                out.append(_stem(part))
    return out


# --- stance cues ---------------------------------------------------------
# These nudge retrieval: the prosecution (FOR the claim) likes passages that sound
# affirmative, the defense (AGAINST) likes passages with limits / negations / exceptions.
# Also used by the offline judge. Not meant to be smart, just a bias.

SUPPORT_CUES = [
    "allowed", "permitted", "eligible", "may", "can", "improved", "improve", "increase",
    "higher", "significant", "effective", "benefit", "better", "yes", "covered",
    "reduced", "faster", "shorter", "approved", "provided", "receive", "promoted",
    "growth", "strong", "like", "excited", "ownership", "learning", "grew",
]
OPPOSE_CUES = [
    "not", "no ", "never", "cannot", "can't", "unfortunately", "rejected", "prohibited",
    "only", "except", "exception", "however", "but", "limitation", "risk", "worry",
    "declined", "lower", "less", "lapse", "lapses", "violation", "not significant",
    "no significant", "did not", "does not", "unknown", "mixed", "little or no",
    "worse", "laid off", "layoffs", "burnout", "clawback", "repaid", "blocked",
    "not eligible", "not reimbursable", "not covered", "harder", "small",
]


def cue_scores(text: str) -> tuple[float, float]:
    t = " " + text.lower() + " "
    sup = sum(t.count(" " + c + " ") + t.count(" " + c + ",") + t.count(" " + c + ".") for c in SUPPORT_CUES)
    opp = sum(t.count(" " + c.strip() + " ") + t.count(" " + c.strip() + ",") + t.count(" " + c.strip() + ".")
              for c in OPPOSE_CUES)
    n = max(len(t.split()), 1)
    # normalise per 100 words so long chunks don't win automatically
    return sup * 100 / n, opp * 100 / n


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [p.strip(" -•\t") for p in parts if len(p.split()) >= 4]
