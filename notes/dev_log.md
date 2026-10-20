# dev log

rough notes I kept while building this. mostly for the viva / report.

## week 1
- first version was literally one prompt: "here are chunks, argue for, argue against, then judge". the judge just agreed with whoever wrote more text. useless.
- split it into 3 separate calls. better.
- chunking every 100 words → cut sentences in half. "Working from outside India is" | "not permitted". defense lost a case it should've won lol. switched to paragraph chunking.

## week 2
- tried sentence-transformers (all-MiniLM-L6-v2). works but 90MB + torch is ~2GB on windows. lab PCs died. went back to BM25 + tf-idf.
- wrote BM25 myself because rank_bm25 wasn't doing anything I couldn't do in 30 lines and sir asked us to "understand the algorithm".
- both lawyers kept retrieving the exact same top 6. added stance bias. first tried 2x multiplier → defense started pulling the infosec password section for a leave question because it says "not" a lot. toned it down to max +25%.
- "WFH" vs "work from home" vs "remote" → synonym map. hacky but works.

## week 3
- the BIG one: llama kept citing "E14" when it only had E3-E9. sometimes it invented exhibits that don't exist. added citations.py — any point without a valid id gets struck. the UI shows it crossed out, which looks really cool in the demo.
- judge also cited stuff nobody admitted. now the judge's reasons get validated too and if nothing survives → INSUFFICIENT.
- groq sometimes wraps JSON in ```json fences even with response_format. parse_json() strips that.

## week 4
- UI. first made it in streamlit but it reruns the whole script on every click and I couldn't do the live streaming nicely. moved to fastapi + plain html/js with SSE.
- gavel animation took longer than the entire retriever. worth it.
- offline mode: added because the free groq quota ran out 2 days before review. the offline judge first just compared which lawyer had "more points", which is circular (it's judging its own templates). now it reads the admitted exhibits directly and checks sentence stance. 8/16 on my eval set, LLM is much better.

## things I'd do differently
- start with the eval set on day 1, not at the end. I kept "fixing" things by looking at one claim.
- the cue word lists should probably be learned, not hand-written.
