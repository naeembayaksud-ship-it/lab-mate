"""Pre-lab checklist builder and paper-grounded Q&A. Every model call goes through the `ask(system, user)` passed in
(logic.hermes_ask). Nothing here runs a solver or an experiment."""
import json, re, time

STOPW = set("the of and for with from into over under per via use used using each all any not are was were this that these those then than also"
            " value values level levels amount type rate size mean total final initial".split())


def _toks(s):
    return {w for w in re.findall(r"[a-z0-9]+", str(s).lower()) if len(w) >= 3 and w not in STOPW}


def _norm(s):
    return re.sub(r"\s+", "", str(s)).lower()


def in_paper(name, fields, pages):
    """True if the variable is already covered: matches an extracted parameter name, or all its words sit on one page."""
    t = _toks(name)
    if not t:
        return True
    for f in fields:
        ft = _toks(f.get("name", ""))
        if ft and len(t & ft) / min(len(t), len(ft)) >= 0.6:
            return True
    for p in pages:
        pt = _toks(p)
        if t <= pt and len(t) >= 2:
            return True
    return False


CHECK_SYS = """You help a researcher who is about to repeat an experiment or simulation from a paper, and who will later have to find out why a result differs from the paper. You get the paper's method steps and its verified parameters (JSON). Write the part of a pre-lab checklist that the paper itself does NOT give.
Return valid JSON only:
{"extra_notes":[{"item":"what to record","reason":"why it can explain a gap between labs"}],
 "controls_not_in_paper":[{"variable":"name","reason":"how it could change the outcome","range":"suggested range WITH numbers and unit, or the allowed options","unit":"unit or empty","how_to_control":"how to hold it fixed or measure it","steps":"id of the method step it applies to, e.g. s3, or empty"}],
 "check_points":[{"steps":"method step id","check":"one short sanity check before moving on"}]}
RULES:
1. controls_not_in_paper: ONLY variables that are NOT in the given parameter list and that plausibly change this paper's outcome (max 8, most important first). Never repeat a given parameter. Never name brands.
2. If a range has numbers, the unit MUST be written inside "range" itself (e.g. "20-30 degC"). Every range is an AI estimate for a typical setup; do not present it as a value from the paper. If you cannot give a defensible numeric range, give the allowed options or write "measure it first".
3. extra_notes: max 6 things to log (batch/lot, calibration date, ambient conditions, software version, random seed, hold times ...), only ones relevant to this method.
4. check_points: max 8, one per critical step, concrete and checkable.
5. Plain English, short. Do not invent paper values or claim what the paper did."""


def build_checklist(pr, ask):
    m = pr.get("method") or {}
    steps = [{"id": s["id"], "title": s["title"], "technique": s.get("technique", ""), "page": s.get("page")} for s in m.get("steps", [])]
    fields = [f for f in pr.get("fields", []) if f.get("verified")]
    fl = [{"name": f["name"], "value": f["value"], "unit": f.get("unit", ""), "role": f["role"]} for f in fields]
    inp = json.dumps({"kind": pr.get("kind"), "method_summary": m.get("summary", ""), "method_steps": steps, "paper_parameters": fl}, ensure_ascii=False)
    while len(inp) > 22000 and fl:
        fl = fl[: int(len(fl) * 0.8)]
        inp = json.dumps({"kind": pr.get("kind"), "method_summary": m.get("summary", ""), "method_steps": steps, "paper_parameters": fl}, ensure_ascii=False)
    out = ask(CHECK_SYS, inp)
    ids = {s["id"] for s in steps}
    dropped = 0
    ctl = []
    for x in out.get("controls_not_in_paper") or []:
        if not isinstance(x, dict) or not str(x.get("variable", "")).strip():
            continue
        if in_paper(x["variable"], pr.get("fields", []), pr.get("pages", [])):
            dropped += 1
            continue
        r = str(x.get("range", "")).strip()
        ctl.append({"variable": str(x["variable"]).strip(), "reason": str(x.get("reason", "")).strip(), "range": r,
                    "unit": str(x.get("unit", "")).strip(), "how_to_control": str(x.get("how_to_control", "")).strip(),
                    "steps": x.get("steps") if x.get("steps") in ids else "", "numeric": bool(re.search(r"\d", r))})
    rec = [{"item": str(x.get("item", "")).strip(), "reason": str(x.get("reason", "")).strip()}
           for x in out.get("extra_notes") or [] if isinstance(x, dict) and str(x.get("item", "")).strip()][:6]
    check = [{"steps": x.get("steps") if x.get("steps") in ids else "", "check": str(x.get("check", "")).strip()}
           for x in out.get("check_points") or [] if isinstance(x, dict) and str(x.get("check", "")).strip()][:8]
    return {"built": time.strftime("%Y-%m-%d %H:%M:%S"), "controls_not_in_paper": ctl[:8], "extra_notes": rec, "check_points": check,
            "dropped_already_in_paper": dropped}


def normalize_controls(items, pr, label="AI guess"):
    """Shared by the diagnosis: variables to control that the paper does not report, with an estimated range."""
    res, dropped = [], 0
    for x in items or []:
        if not isinstance(x, dict) or not str(x.get("variable", "")).strip():
            continue
        if in_paper(x["variable"], pr.get("fields", []), pr.get("pages", [])):
            dropped += 1
            continue
        r = str(x.get("range", "")).strip()
        res.append({"variable": str(x["variable"]).strip(), "reason": str(x.get("reason", "")).strip(), "range": r,
                    "unit": str(x.get("unit", "")).strip(), "how_to_control": str(x.get("how_to_control", "")).strip(),
                    "related_hypotheses": [int(h) for h in (x.get("related_hypotheses") or []) if str(h).isdigit()],
                    "numeric": bool(re.search(r"\d", r)), "label": label})
    return res[:8], dropped


# ---------------- paper-grounded Q&A ----------------
ASK_SYS = """You are an assistant for a researcher whose experiment or simulation result differs from a paper (or who is stuck while running it). Answer ONLY from: (1) the page excerpts of the paper given, (2) the verified parameters, (3) the researcher's own data. Everything else is general knowledge and must be kept apart and flagged.
Return valid JSON only:
{"answer":"direct answer, <=170 words, plain text, no markdown",
 "from_paper":[{"page":int,"quote":"text copied EXACTLY from that page, <=200 chars, one continuous passage"}],
 "general_knowledge":"general-science points that are NOT in the paper, or empty string",
 "paper_answers":true|false,
 "search_keywords":["2-5 literature-search keywords, only when the paper does not cover the theory needed; else []"]}
If CANDIDATE PAPERS are given they are only titles/abstracts of papers found by the search tool: when you use one, name it by its id, say it comes from the abstract, and add {"id":"c3","quote":"phrase copied EXACTLY from that title/abstract, <=150 chars"} to "from_candidates" (add that key to the JSON, [] if unused). "parameter_analysis" holds earlier parameter-vs-result analyses of this paper.
RULES: never say a cause is proven (use 'possible' / 'worth checking'); never invent numbers, pages or quotes; if the excerpts do not answer the question say so and set paper_answers=false; reply in the language of the question; if a conclusion needs data the researcher has not given, say which data."""

QSTOP = STOPW | set("what why how does did can could should would which when where there their about have has had been being into your you our they them its it's".split())


def retrieve(pages, question, budget=13000, k=5):
    q = {w for w in re.findall(r"[a-z0-9]+", question.lower()) if len(w) >= 3 and w not in QSTOP}
    df = {}
    pt = [set(re.findall(r"[a-z0-9]+", p.lower())) for p in pages]
    for s in pt:
        for w in q & s:
            df[w] = df.get(w, 0) + 1
    sc = sorted(((sum(1 / df[w] for w in q & s), i) for i, s in enumerate(pt)), reverse=True)
    pick, used = [], 0
    for s, i in sc[:k]:
        if s <= 0 and pick:
            break
        t = pages[i][: max(1500, budget // k)]
        if used + len(t) > budget:
            break
        pick.append(i)
        used += len(t)
    return sorted(pick)


def quote_ok(pr, h, quote):
    """Exact (whitespace-insensitive) match, or both the first and last 40 characters found in order on that page
    (PDF math/figures often garble the middle of a long quote)."""
    try:
        h = int(h)
    except (TypeError, ValueError):
        return False
    pg = pr.get("pages", [])
    if not (1 <= h <= len(pg)):
        return False
    t, q = _norm(pg[h - 1]), _norm(quote)
    if not q:
        return False
    if q in t:
        return True
    if len(q) < 90:
        return False
    i = t.find(q[:40])
    return i >= 0 and t.find(q[-40:], i) >= 0


quote_ok_fn = lambda pr, h, q: quote_ok(pr, h, q)


def ask_paper(pr, question, ask, verify):
    pages = pr.get("pages", [])
    pick = retrieve(pages, question) or [0]
    m = pr.get("method") or {}
    meth = {"summary": m.get("summary", ""), "steps": [f"{s['id']}: {s['title']} ({s.get('technique', '')}), p.{s.get('page')}" for s in m.get("steps", [])]}
    fl = [f"{f['name']} = {f['value']} {f.get('unit', '')} (p.{f.get('page')})" for f in pr.get("fields", []) if f.get("verified")]
    udata = []
    for f in pr.get("fields", []):
        u = (pr.get("compare") or {}).get(f["id"]) or {}
        if u.get("value") not in (None, "") and not u.get("prefill") and str(u["value"]).strip() != str(f["value"]).strip():
            udata.append(f"{f['name']}: paper {f['value']} vs yours {u['value']} {u.get('unit', '')}")
    for h in pr.get("results", []):
        if h.get("value") not in (None, ""):
            udata.append(f"result {h.get('name')}: yours {h.get('value')} {h.get('unit', '')}")
    hist = [{"q": a["q"], "a": a["a"].get("answer", "")[:500]} for a in (pr.get("ask_log") or [])[-3:]]
    pg = "\n".join(f"[PAGE {i + 1}]\n{pages[i]}" for i in pick)
    rec = pr.get("recommendations") or {}
    cand = {c["id"]: c for r_ in (rec, pr.get("own_search") or {}) for c in r_.get("candidates", [])}
    top = [c for c in (rec.get("candidates") or [])[:5]] + [c for c in ((pr.get("own_search") or {}).get("candidates") or [])[:2] if c not in (rec.get("candidates") or [])[:5]]
    ckx = [{"id": c["id"], "title": c["title"], "year": c.get("year"), "abstract": (c.get("abstract") or "")[:450], "helps": c.get("helps", "")[:200]} for c in top]
    rel = [{"questions": r_["q"], "results": r_["results"], "parameter": [f"{p['name']} ({p['direction']}, {p['evidence']})" for p in r_["parameter"]]} for r_ in (pr.get("relation_log") or [])[:2]]
    hyps = [f"#{h.get('rank')}: {str(h.get('statement', ''))[:200]}" for h in ((((pr.get("report") or [{}])[-1].get("results")) or {}).get("hypotheses") or [])[:5]]
    base = {"questions": question, "method": meth, "user_data": udata[:30], "previous_conversation": hist, "hypotheses": hyps,
            "CANDIDATE PAPERS": ckx, "parameter_analysis": rel}
    fl_txt = "\n".join(fl)
    for lim in (6000, 3000, 1000, 0):
        inp = json.dumps(base, ensure_ascii=False) + "\n\nPARAMETERS:\n" + fl_txt[:lim] + "\n\nPAPER EXCERPTS:\n" + pg
        if len(inp) + len(ASK_SYS) < 28500:
            break
    else:
        inp = inp[: 28500 - len(ASK_SYS)]
    out = ask(ASK_SYS, inp)
    cites = []
    for c in out.get("from_paper") or []:
        if isinstance(c, dict) and c.get("quote"):
            h = c.get("page")
            cites.append({"page": h if isinstance(h, int) else None, "quote": str(c["quote"])[:300], "verified": bool(verify(pr, h, c["quote"]))})
    kk = [str(x) for x in (out.get("search_keywords") or []) if str(x).strip()][:5]
    kand = []
    for c in out.get("from_candidates") or []:
        k = cand.get(c.get("id")) if isinstance(c, dict) else None
        if k and c.get("quote"):
            kand.append({"id": k["id"], "title": k["title"][:120], "doi": k.get("doi", ""), "quote": str(c["quote"])[:220],
                         "verified": _norm(c["quote"]) in _norm(k["title"] + " " + (k.get("abstract") or ""))})
    return {"answer": str(out.get("answer", "")).strip(), "from_paper": cites, "from_candidates": kand, "general_knowledge": str(out.get("general_knowledge") or "").strip(),
            "paper_answers": bool(out.get("paper_answers", True)), "search_keywords": kk, "pages_read": [i + 1 for i in pick]}


TITLE_SYS = """You write short titles. For each item (id -> text) write a plain noun phrase of at most 8 words that names it (no full sentence, no trailing period, no 'Possibly'). Keep technical terms. English only.
Return valid JSON only: {"titles":{"<id>":"<title>"}}"""


def fill_titles(out, ask):
    """Replace code-derived (title_auto) titles by model-written ones in one cheap call. Returns how many were filled."""
    todo = {}
    for key, tag, src in (("hypotheses", "h", "statement"), ("test_plan", "t", "change"), ("data_requested", "d", "item")):
        for i, x in enumerate(out.get(key) or []):
            if isinstance(x, dict) and x.get("title_auto"):
                v = x.get(src)
                todo[f"{tag}{i}"] = (x, str(v if not isinstance(v, (list, dict)) else json.dumps(v, ensure_ascii=False))[:300])
    if not todo:
        return 0
    res = ask(TITLE_SYS, json.dumps({k: t for k, (_, t) in todo.items()}, ensure_ascii=False))
    titles = res.get("titles") if isinstance(res, dict) else None
    n = 0
    for k, (x, _) in todo.items():
        t = re.sub(r"\s+", " ", str((titles or {}).get(k, ""))).strip().rstrip(".")
        if t and len(t.split()) <= 12:
            x["title"], n = t[:90], n + 1
            x.pop("title_auto", None)
    return n


GROUP_SYS = """You organise the parameters extracted from ONE scientific paper into categories for a researcher.
You get: the process phases of the paper's method (in order), the subsection headings of its Methods/Results sections, and the parameters (id | role | name | value).
Rules:
1. Parameters whose role is materials, equipment or conditions: group them by PROCESS STEP of the paper (for example Monomer feed, Polymerization, Degassing, Sampling and analysis). Use the given method phases as the guide and keep their order. 
2. Parameters whose role is results: group them by the Results subsection they are reported in, or by the quantity measured (for example Solid content, Molar mass, Particle size). Use the given subsection headings when they fit.
3. Every id must appear in exactly one group. Use at most 8 groups per kind. Category names: 1-4 plain English words, no numbering.
4. Do not invent parameters or ids.
Return valid JSON only: {"groups":[{"name":"...","kind":"process|results","ids":["f..."]}]}"""


def group_parameters(pr, ask):
    F = [f for f in pr.get("fields", []) if f.get("verified")]
    if not F:
        raise RuntimeError("There are no verified parameters to group.")
    phases = list(dict.fromkeys(s.get("phase", "") for s in (pr.get("method") or {}).get("steps", []) if s.get("phase")))
    subs = list(dict.fromkeys(g.get("subsection", "") for g in (pr.get("figures") or {}).get("items", []) if g.get("subsection")))
    lines = [f"{f['id']} | {f['role']} | {f['name']} | {str(f.get('value', ''))[:40]}" for f in F]
    inp = json.dumps({"method_phases": phases, "section_headings": subs}, ensure_ascii=False) + "\n\nPARAMETERS:\n" + "\n".join(lines)
    out = ask(GROUP_SYS, inp[:26000])
    by = {f["id"]: f for f in F}
    groups, used = [], set()
    for g in out.get("groups") or []:
        if not isinstance(g, dict) or not str(g.get("name", "")).strip():
            continue
        ids = [i for i in (g.get("ids") or []) if i in by and i not in used]
        if not ids:
            continue
        used.update(ids)
        kind = "results" if sum(by[i]["role"] == "results" for i in ids) * 2 > len(ids) else "process"
        groups.append({"name": str(g["name"]).strip()[:40], "kind": kind, "ids": ids})
    for kind, test in (("process", lambda f: f["role"] != "results"), ("results", lambda f: f["role"] == "results")):
        rest = [f["id"] for f in F if f["id"] not in used and test(f)]
        if rest:
            groups.append({"name": "Other", "kind": kind, "ids": rest})
    return {"built": time.strftime("%Y-%m-%d %H:%M:%S"), "groups": groups, "n_unplaced": len(F) - len(used)}
