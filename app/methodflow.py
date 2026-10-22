"""Method flowchart: how the paper did its work (experiment and/or simulation), step by step.
Two stages when the paper is long: (A) per page group, list the method steps found there;
(B) one call that merges them into an ordered workflow. Code then verifies every quote and setting
against the page text. Nothing here runs a solver or an experiment."""
import json, re

PHASE_TYPE = ("input", "preparation", "run", "measurement", "analysis", "validation")

STEPS_RULES = """Describe the METHOD the authors themselves used, as the ordered steps of their work flow. Cover the whole chain, whether it is experimental, computational/simulation, or both: what goes in (samples, materials, images, data, model), how it was prepared or set up, what was run (experiment, simulation, algorithm, optimisation), what was measured or computed, how the results were analysed, and how they were validated.
HARD RULES:
1. Only the paper's own method. No literature review, no background, no general claims. Reporting results is NOT a step, but comparing or validating against measurements IS a step.
2. Use plain English, short and specific. Name the actual technique, instrument, software or algorithm (e.g. "random-walk NMR simulation", "genetic algorithm", "micro-CT scan"), not vague words like "analysis was done". Do not name manufacturers or brands.
3. "quote" must be copied EXACTLY from the paper text (<=200 characters, one continuous passage, no ellipsis) and "page" must be the page it is on. If you cannot quote the step, leave the step out.
4. "parameter" lists only settings that are literally stated in that step (name and value copied from the text, e.g. {"name":"Number of walkers","value":"100,000"}). At most 6 per step, most important first. Empty list if none are stated.
5. Do not invent anything. Do not describe steps the paper only proposes as future work."""

STAGE_A = """You read part of a scientific paper (text split by page, marked [PAGE n]).
""" + STEPS_RULES + """
Return valid JSON only: {"kind":"experiment|simulation|mixed|no method","steps":[{"title":"<=7 words","description":"1-2 sentences: what is done and how","technique":"technique / instrument / software / algorithm used, short","parameter":[{"name","value"}],"output":"what this step produces, short","phase_type":"input|preparation|run|measurement|analysis|validation","quote","page":int}]}.
If this part has no method content, return {"kind":"no method","steps":[]}."""

STAGE_B = """Below are method steps extracted from different parts of one scientific paper (JSON). Several may describe the same step (e.g. once in the abstract and once in the methods section). Build the single method workflow of the paper.
RULES:
1. Merge duplicates into one step; keep the most specific wording, and copy "quote", "page" and "parameter" from the step you keep, unchanged. Never invent a quote or a setting.
2. Order the steps in the sequence the work is actually carried out (not the order of the pages). Give ids "s1","s2",... in that order.
3. "from" = ids of the steps whose output this step directly needs (empty for the first step). A step may have several predecessors (inputs merge) and several steps may share one predecessor (parallel branches).
4. If the method repeats or iterates (optimisation loop, convergence check, parameter sweep), put on the LAST step of the loop: "repeat":{"to":"<id of the first step of the loop>","conditions":"repeat until ... (short)"}; otherwise null.
5. "phase" = a short English phase heading (<=3 words), e.g. "Sample & data", "Simulation", "Optimisation", "Validation". Consecutive steps that belong together share the same "phase" text exactly. "phase_type" is one of input|preparation|run|measurement|analysis|validation.
6. "summary" = 2 sentences: the goal of the paper's method and how it is done (the approach). "kind" = experiment | simulation | mixed (experimental / simulation / both).
7. At most 14 steps. Merge minor steps rather than dropping important ones.
Return valid JSON only: {"kind","summary","steps":[{"id","phase","phase_type","title","description","technique","parameter":[{"name","value"}],"output","quote","page":int,"from":[ids],"repeat":null|{"to","conditions"}}]}."""

ONE_SHOT = """You read a scientific paper (text split by page, marked [PAGE n]) and build the single method workflow of the paper.
""" + STEPS_RULES + """
ALSO:
- Order the steps in the sequence the work is actually carried out. Ids "s1","s2",... in that order. At most 14 steps; merge minor steps rather than dropping important ones.
- "from" = ids of the steps whose output this step directly needs (empty for the first). Several predecessors and parallel branches are allowed.
- If the method iterates (optimisation loop, convergence check, sweep), put on the LAST step of the loop: "repeat":{"to":"<id of first step of loop>","conditions":"repeat until ... (short)"}; otherwise null.
- "phase" = short English phase heading (<=3 words); consecutive steps that belong together share the same "phase" text exactly.
- "summary" = 2 sentences: the goal of the paper's method and how it is done. "kind" = experiment | simulation | mixed.
Return valid JSON only: {"kind","summary","steps":[{"id","phase","phase_type","title","description","technique","parameter":[{"name","value"}],"output","quote","page":int,"from":[ids],"repeat":null|{"to","conditions"}}]}."""


def _norm(s):
    return re.sub(r"\s+", "", str(s)).lower()


def _groups(pages, limit=20000):
    g, cur, n = [], [], 0
    for i, t in enumerate(pages):
        if cur and n + len(t) > limit:
            g.append(cur)
            cur, n = [], 0
        cur.append(i)
        n += len(t)
    g.append(cur)
    return g


def _text(pages, idx):
    return "\n".join(f"[PAGE {i + 1}]\n{pages[i]}" for i in idx)


def count_calls(pages):
    tot = sum(len(t) for t in pages)
    return 1 if tot <= 22000 else len(_groups(pages)) + 1


def _find_page(pages, quote, hint):
    q = _norm(quote)
    if not q:
        return None
    try:
        h = int(hint)
        if 1 <= h <= len(pages) and q in _norm(pages[h - 1]):
            return h
    except (TypeError, ValueError):
        pass
    for i, t in enumerate(pages):
        if q in _norm(t):
            return i + 1
    return None


def locate(pages, quote, hint):
    """(page, quote) where the returned quote is a contiguous piece of the model's quote that exists on that page.
    PDF text often garbles formulas, so when the whole quote fails, keep the longest verified head or tail (>=35 chars)."""
    quote = str(quote or "").strip()
    pg = _find_page(pages, quote, hint)
    if pg:
        return pg, quote
    w = quote.split()
    best = (None, "")
    for i in range(len(w)):
        for part in (" ".join(w[i:]), " ".join(w[:len(w) - i])):
            if len(_norm(part)) >= 35 and len(part) > len(best[1]):
                g = _find_page(pages, part, hint)
                if g:
                    best = (g, part)
        if best[0]:
            break
    return best


def _num_tokens(s):
    return re.findall(r"\d+(?:[.,]\d+)*", str(s))


def _setting_ok(val, nm, page_norm, all_norm):
    v = _norm(val)
    if not v:
        return False
    if v in page_norm or v in all_norm:
        return True
    nums = _num_tokens(val)
    return bool(nums) and all(_norm(x) in all_norm for x in nums)


def finalize(raw, pages):
    """Validate the model output: unique ids, real predecessors, verified quotes and settings."""
    steps_in = [s for s in (raw.get("steps") or []) if isinstance(s, dict)]
    all_norm = _norm(" ".join(pages))
    out, ids = [], {}
    for k, s in enumerate(steps_in[:16], 1):
        sid = str(s.get("id") or f"s{k}")
        if sid in ids:
            sid = f"s{k}"
        ids[sid] = True
        pg, qq = locate(pages, s.get("quote"), s.get("page"))
        if pg:
            s["quote"] = qq
        pn = _norm(pages[pg - 1]) if pg else ""
        sets = []
        for p in (s.get("parameter") or [])[:6]:
            if isinstance(p, dict) and p.get("name") and str(p.get("value", "")).strip() \
                    and _setting_ok(p["value"], p["name"], pn, all_norm):
                sets.append({"name": str(p["name"]).strip(), "value": str(p["value"]).strip()})
        ft = s.get("phase_type") if s.get("phase_type") in PHASE_TYPE else "run"
        out.append({
            "id": sid, "phase": str(s.get("phase") or "Method").strip()[:40], "phase_type": ft,
            "title": str(s.get("title") or "Step").strip(), "description": str(s.get("description") or "").strip(),
            "technique": str(s.get("technique") or "").strip(), "parameter": sets,
            "output": str(s.get("output") or "").strip(),
            "quote": str(s.get("quote") or "").strip(), "page": pg or (s.get("page") if isinstance(s.get("page"), int) else None),
            "verified": bool(pg), "from": [], "repeat": None,
        })
    have = {s["id"] for s in out}
    order = {s["id"]: i for i, s in enumerate(out)}
    for s, src in zip(out, steps_in):
        d = src.get("from") or []
        s["from"] = [x for x in dict.fromkeys(str(y) for y in d) if x in have and order[x] < order[s["id"]]]
        u = src.get("repeat")
        if isinstance(u, dict) and str(u.get("to")) in have and order[str(u["to"])] <= order[s["id"]]:
            s["repeat"] = {"to": str(u["to"]), "conditions": str(u.get("conditions") or "").strip()}
    # steps with no predecessor (except the first) follow the previous step, so the chain stays connected
    for i, s in enumerate(out):
        if i and not s["from"] and not any(s["id"] in t["from"] for t in out[:i]) and s["phase_type"] != "input":
            s["from"] = [out[i - 1]["id"]]
    work_kind = raw.get("kind") if raw.get("kind") in ("experiment", "simulation", "mixed") else "mixed"
    return {"kind": work_kind, "summary": str(raw.get("summary") or "").strip(), "steps": out}


def build(pages, ask, job=None, cancel_check=None):
    """ask(system, user) -> dict. Returns the method dict (see finalize)."""
    tot = sum(len(t) for t in pages)
    if tot <= 22000:
        if job is not None:
            job["stage"] = "Building the method flowchart (1 of 1)"
        raw = ask(ONE_SHOT, _text(pages, range(len(pages))))
        return finalize(raw, pages)
    grp = _groups(pages)
    steps, work_kind = [], []
    for gi, g in enumerate(grp, 1):
        if job is not None:
            job["stage"] = f"Reading the method, part {gi} of {len(grp)}"
        e = ask(STAGE_A, _text(pages, g))
        work_kind.append(e.get("kind"))
        for s in e.get("steps") or []:
            if isinstance(s, dict):
                steps.append(s)
    if not steps:
        raise RuntimeError("the model found no method steps in this paper")
    if job is not None:
        job["stage"] = "Merging the steps into one flowchart"
    payload = json.dumps(steps, ensure_ascii=False)
    if len(payload) > 22000:
        for s in steps:
            s["quote"] = str(s.get("quote", ""))[:140]
        payload = json.dumps(steps, ensure_ascii=False)
    raw = ask(STAGE_B, payload)
    return finalize(raw, pages)
