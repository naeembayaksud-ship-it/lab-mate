"""Paper finder + parameter-to-result analysis.

find(): builds search queries from the researcher's hypotheses and parameters (one model call), searches OpenAlex and Crossref
(code, no model), merges and pre-ranks the hits (reciprocal-rank fusion over the queries + term overlap), then one model call
rates the top candidates from TITLE + ABSTRACT only. Each rating must carry a phrase copied from the abstract; code checks it.
relate(): for a question like "what drives Mn?", ranks the parameters by how the paper itself ties them to that result, with
quotes that code checks on the page. Evidence classes, not percentages."""
import html, json, math, re, time, urllib.error, urllib.parse
from concurrent.futures import ThreadPoolExecutor

import extras as X
import related as RL

LABEL = ("Ranked from titles and abstracts only (not full text). The rating is the AI's reading of the abstract against your "
         "hypotheses and parameters; it is not a measured effect. Add a paper to your set to have its full text read.")
TIER = {"high": 3, "medium": 2, "low": 1}
DOI_RX = re.compile(r"10\.\d{4,9}/[^\s\"<>]+", re.I)
STOP = set("the a an of in on for and or to with by from at as is are was were be this that these those its it into using use used via effect effects study "
           "paper results result method methods based between during after before under over".split())

QGEN_SYS = """You help a researcher whose experiment or simulation result differs from a paper (or who is stuck). From the context, write search queries that find OTHER papers able to help solve the problem: theory of the suspected mechanism, similar processes studied by other groups, troubleshooting and measurement issues.
Return valid JSON only:
{"queries":[{"q":"3-8 plain keywords, no boolean operators, no quotes","for":"which problem this query targets, <=15 words","hypotheses":[hypothesis rank numbers it targets, may be empty]}]}
RULES: 4 to 6 queries, each 3 to 6 keywords (short queries work better). Most queries must contain the material/process anchor from the context. Cover different hypotheses and at least one query on measurement or control of the outcome. Do not use the paper's own title. Use English keywords."""

RERANK_SYS = """You rate candidate papers for a researcher whose experiment or simulation result differs from a paper. You see ONLY titles and abstracts. Judge how much each candidate could help solve the researcher's problem: explain a suspected mechanism, give a value range or method for a parameter, or address a measurement problem.
Return valid JSON only:
{"results":[{"id":"c1","tier":"high|medium|low","helps":"<=40 words: what exactly this paper could give the researcher, based only on its abstract","related":["hypothesis #n or parameter name, may be empty"],"basis":"a phrase copied EXACTLY from the title or abstract, <=150 chars, that supports your rating"}]}
RULES: rate every candidate once. 'medium' needs the title/abstract to name the same material or process, or the same mechanism or measurement issue as a listed hypothesis; generic background, a different material with no shared mechanism, or a paper you would only read for context is 'low'. 'high' only when the abstract directly addresses a listed hypothesis, a listed parameter's effect, or the measurement problem. If the abstract is missing, the rating can be at most 'medium' and 'basis' must come from the title. Never invent content that is not in the abstract. Never claim a cause is proven. Write in English."""

REL_SYS = """You analyse ONE paper for a researcher. Given a question about a result in the paper, find which parameters the paper itself ties to that result.
Answer ONLY from the page excerpts and the parameter list. Return valid JSON only:
{"results":"the outcome the question is about, as named in the paper","brief":"<=60 words, plain text","parameter":[{"name":"parameter name; copy it from the PARAMETERS list when it is there","from_list":true|false,"direction":"up|down|peak|unclear","effect":"<=35 words: what the paper shows (or, for an inference, what would be expected)","evidence":"quantitative|qualitative|guess","page":int or null,"quote":"text copied EXACTLY from that page, <=200 chars, one continuous passage; empty string for guess"}],"not_discussed":"part of the question the paper does not cover, or empty string"}
RULES: 3 to 8 parameters, strongest relation first. 'quantitative' only when the quote has numbers showing the effect; 'qualitative' when the paper states the relation in words; 'guess' when the paper does not state it (AI inference, no quote). Never invent numbers, pages or quotes. 'direction' describes the paper's result (up = result rises when the parameter rises). Reply in the language of the question."""


def _toks(s):
    return [w for w in re.findall(r"[a-z0-9]+", str(s).lower()) if len(w) >= 3 and w not in STOP]


def _plain(s):
    return html.unescape(re.sub(r"<[^>]+>", " ", str(s or ""))).strip()


# ---------------- context ----------------
def context(pr):
    last = (pr.get("report") or [None])[-1]
    h = (last or {}).get("results") or {}
    hyps = [{"rank": x.get("rank"), "statement": str(x.get("statement", ""))[:240], "related_parameters": x.get("related_parameters") or []}
            for x in (h.get("hypotheses") or [])[:6]]
    ctrl = [c.get("variable") for c in (h.get("variables_controlled") or []) if c.get("variable")]
    if not ctrl:
        ctrl = [c.get("variable") for c in ((pr.get("checklist") or {}).get("controls_not_in_paper") or []) if c.get("variable")]
    F = [f for f in pr.get("fields", []) if f.get("verified")]
    params = [f"{f['name']} = {f['value']} {f.get('unit', '')}".strip() for f in F if f.get("role") in ("conditions", "materials")][:24]
    results = [f["name"] for f in F if f.get("role") == "results"][:10]
    diff = []
    for f in pr.get("fields", []):
        u = (pr.get("compare") or {}).get(f["id"]) or {}
        if u.get("value") not in (None, "") and not u.get("prefill") and str(u["value"]).strip() != str(f.get("value", "")).strip():
            diff.append(f"{f['name']}: paper {f['value']} vs yours {u['value']} {u.get('unit', '')}".strip())
    for r in pr.get("results", []):
        if r.get("value") not in (None, ""):
            diff.append(f"your result {r.get('name')}: {r.get('value')} {r.get('unit', '')}".strip())
    topic = ((pr.get("method") or {}).get("summary") or (pr.get("pages") or [""])[0])[:450]
    return {"paper_topic": topic, "hypotheses": hyps, "variables_controlled": ctrl[:8], "paper_parameters": params, "paper_results": results,
            "user_differences": diff[:12], "basis": "hypotheses" if hyps else "paper parameters only (no diagnosis yet)"}


def fallback_queries(ctx):
    base = " ".join(_toks(ctx["paper_topic"])[:4])
    qs = []
    for h in ctx["hypotheses"][:4]:
        t = [w for p in h["related_parameters"][:2] for w in _toks(p)[:2]]
        if t:
            qs.append({"q": (base + " " + " ".join(dict.fromkeys(t)))[:90], "for": "hypothesis " + str(h["rank"]), "hypotheses": [h["rank"]]})
    for r in ctx["paper_results"][:2]:
        qs.append({"q": (base + " " + " ".join(_toks(r)[:3]))[:90], "for": "the result " + r, "hypotheses": []})
    return qs[:6]


def gen_queries(ctx, ask):
    try:
        out = ask(QGEN_SYS, json.dumps(ctx, ensure_ascii=False))
        qs = []
        for x in out.get("queries") or []:
            q = re.sub(r"[\"()]", " ", str(x.get("q", ""))).strip()
            q = re.sub(r"\s+", " ", q)[:90]
            if len(_toks(q)) >= 2:
                qs.append({"q": q, "for": str(x.get("for", ""))[:120],
                           "hypotheses": [int(k) for k in (x.get("hypotheses") or []) if str(k).isdigit()]})
        if qs:
            return qs[:6], "AI"
    except Exception as e:
        if "Cancelled" in type(e).__name__:
            raise
    return fallback_queries(ctx), "from your hypotheses (AI query writer unavailable)"


# ---------------- search ----------------
def _abstract(inv):
    if not inv:
        return ""
    pos = {}
    for w, ix in inv.items():
        for i in ix:
            pos[i] = w
    return " ".join(pos[i] for i in sorted(pos))


OA_SEL = "id,doi,display_name,publication_year,cited_by_count,primary_location,open_access,best_oa_location,abstract_inverted_index"


def _oa_item(w):
    loc = w.get("best_oa_location") or {}
    return {"title": w.get("display_name") or "", "year": w.get("publication_year"), "doi": (w.get("doi") or "").replace("https://doi.org/", ""),
            "journal": ((w.get("primary_location") or {}).get("source") or {}).get("display_name") or "", "citations": w.get("cited_by_count") or 0,
            "oa": bool((w.get("open_access") or {}).get("is_oa")), "pdf": loc.get("pdf_url") or "",
            "abstract": _abstract(w.get("abstract_inverted_index"))[:1500], "src": "OpenAlex"}


def _oa_get(params):
    try:
        return RL._json("https://api.openalex.org/works?" + RL._oa_params(params))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore") if e.code == 429 else ""
        if "budget" in body.lower():
            raise RuntimeError("OpenAlex free daily budget is used up (resets 00:00 UTC; a free OPENALEX_API_KEY environment variable raises it)")
        raise


def oa_search(q, n=15):
    d = _oa_get({"search": q, "per-page": n, "select": OA_SEL, "filter": "type:article,has_abstract:true"})
    return [_oa_item(w) for w in d.get("results", [])]


def oa_doi(doi):
    d = _oa_get({"filter": "doi:https://doi.org/" + doi, "select": OA_SEL})
    return [_oa_item(w) for w in d.get("results", [])]


def sc_search(q, key, n=15):
    """Scopus standard view: titles and metadata only (this key cannot read abstracts)."""
    t = list(dict.fromkeys(_toks(q)))[:5]
    for k in (len(t), 3):
        if k < 2 or k > len(t):
            continue
        qq = urllib.parse.urlencode({"query": "TITLE-ABS-KEY(" + " AND ".join(t[:k]) + ")", "count": n, "sort": "relevancy"})
        d = RL._json("https://api.elsevier.com/content/search/scopus?" + qq, {"X-ELS-APIKey": key, "Accept": "application/json"})["search-results"]
        res = []
        for e in d.get("entry", []):
            if "dc:title" not in e:
                continue
            cd = (e.get("prism:coverDate") or "")[:4]
            res.append({"title": e["dc:title"], "year": int(cd) if cd.isdigit() else None, "doi": e.get("prism:doi") or "",
                        "journal": e.get("prism:publicationName") or "", "citations": int(e.get("citedby-count") or 0),
                        "oa": str(e.get("openaccessFlag")).lower() == "true", "pdf": "", "abstract": "", "src": "Scopus"})
        if res:
            return res
    return []


def cr_doi(doi):
    w = RL._json("https://api.crossref.org/works/" + urllib.parse.quote(doi) + "?select=DOI,title,issued,container-title,is-referenced-by-count,abstract")["message"]
    return [{"title": (w.get("title") or [""])[0], "year": ((w.get("issued") or {}).get("date-parts") or [[None]])[0][0], "doi": w.get("DOI") or doi,
             "journal": (w.get("container-title") or [""])[0], "citations": w.get("is-referenced-by-count") or 0, "oa": False, "pdf": "",
             "abstract": _plain(w.get("abstract"))[:1500], "src": "Crossref"}]


def cr_abstract(doi):
    try:
        w = RL._json("https://api.crossref.org/works/" + urllib.parse.quote(doi) + "?select=abstract")["message"]
        return _plain(w.get("abstract"))[:1500]
    except Exception:
        return ""


def cr_search(q, n=10):
    d = RL._json("https://api.crossref.org/works?" + urllib.parse.urlencode(
        {"query.bibliographic": q, "rows": n, "select": "DOI,title,issued,container-title,is-referenced-by-count,abstract,type"}))["message"]["items"]
    res = []
    for w in d:
        t = (w.get("title") or [""])[0]
        if not t or re.match(r"(review for|decision letter|author response|referee report|reviewer)", t, re.I) or w.get("type") not in ("journal-article", "proceedings-article", "posted-content"):
            continue
        res.append({"title": t, "year": ((w.get("issued") or {}).get("date-parts") or [[None]])[0][0], "doi": w.get("DOI") or "",
                    "journal": (w.get("container-title") or [""])[0], "citations": w.get("is-referenced-by-count") or 0, "oa": False, "pdf": "",
                    "abstract": _plain(w.get("abstract"))[:1500], "src": "Crossref"})
    return res


def _is_source(c, pr):
    p1 = X._norm((pr.get("pages") or [""])[0])
    t = X._norm(c["title"])
    if len(t) > 25 and t in p1:
        return True
    dois = {d.lower().rstrip(".,;)") for d in DOI_RX.findall(" ".join((pr.get("pages") or [""])[:2]))}
    return bool(c["doi"]) and c["doi"].lower() in dois


def search_all(queries, pr, per=15):
    try:
        skey = RL._scopus_key()
    except Exception:
        skey = None
    srcs = [("OpenAlex", lambda q: oa_search(q, per)), ("Scopus", lambda q: sc_search(q, skey, 15)), ("Crossref", lambda q: cr_search(q, 10))]
    srcs = [x for x in srcs if x[0] != "Scopus" or skey]
    jobs = [(i, src, (lambda q=q["q"], f=f: f(q))) for src, f in srcs for i, q in enumerate(queries)]
    status = {src: {"ok": 0, "fail": 0, "error": ""} for src, _ in srcs}
    if not skey:
        status["Scopus"] = {"ok": 0, "fail": 0, "error": "no Scopus API key configured"}

    def run(j):
        err = ""
        for wait in (0, 2.0, 4.0):  # bursts get HTTP 429: retry politely
            time.sleep(wait)
            try:
                return j[0], j[1], j[2](), None
            except Exception as e:
                err = f"{type(e).__name__}: {e}"[:200]
                if "429" not in err or "budget" in err:
                    break
        return j[0], j[1], [], err

    def lane(src):  # one request at a time per API, short gap between them
        out, dead = [], None
        for j in [j for j in jobs if j[1] == src]:
            r = (j[0], src, [], dead) if dead else run(j)
            if r[3] and "budget" in r[3]:
                dead = r[3]
            out.append(r)
            time.sleep(0.3)
        return out

    with ThreadPoolExecutor(3) as ex:
        res = [r for part in ex.map(lane, [x[0] for x in srcs]) for r in part]
    merged = {}
    for qi, src, items, err in res:
        s = status[src]
        if err:
            s["fail"] += 1
            s["error"] = err
            continue
        s["ok"] += 1
        for rank, it in enumerate(items):
            if not it["title"]:
                continue
            k = it["doi"].lower() or X._norm(it["title"])[:80]
            m = merged.setdefault(k, {**it, "src": [], "hits": {}})
            for f in ("abstract", "pdf", "journal"):
                if not m.get(f) and it.get(f):
                    m[f] = it[f]
            m["oa"] = m["oa"] or it["oa"]
            m["citations"] = max(m["citations"] or 0, it["citations"] or 0)
            if src not in m["src"]:
                m["src"].append(src)
            m["hits"][qi] = min(rank, m["hits"].get(qi, 99))
    for c in merged.values():
        c["title"], c["journal"] = _plain(c["title"]), _plain(c.get("journal"))
    cands = [c for c in merged.values() if not _is_source(c, pr)]
    return cands, status


def prerank(cands, queries, ctx):
    qt = [set(_toks(q["q"])) for q in queries]
    key = set()
    for h in ctx["hypotheses"]:
        for p in h["related_parameters"]:
            key |= set(_toks(p))
    for v in ctx["variables_controlled"]:
        key |= set(_toks(v))
    for c in cands:
        rrf = sum(60.0 / (60 + r) for r in c["hits"].values())
        txt = set(_toks(c["title"] + " " + c["abstract"]))
        cov = max([len(qt[i] & txt) / max(1, len(qt[i])) for i in c["hits"] if i < len(qt)] or [0])
        ov = len(key & txt) / max(1, len(key)) if key else 0
        c["score"] = round(1.5 * cov + 0.5 * rrf + 0.8 * ov + 0.1 * math.log10(1 + (c["citations"] or 0)) + (0.15 if c["abstract"] else -0.3), 3)
        c["hits"] = sorted(c["hits"])
    return sorted(cands, key=lambda c: -c["score"])


def rate(ctx, top, ask):
    short = {"paper_topic": ctx["paper_topic"][:300], "hypotheses": ctx["hypotheses"], "variables_controlled": ctx["variables_controlled"],
             "paper_parameters": ctx["paper_parameters"][:12], "user_differences": ctx["user_differences"]}
    cand = [{"id": c["id"], "title": c["title"], "year": c["year"], "abstract": c["abstract"][:700] or "(no abstract available)"} for c in top]
    for lim in (700, 500, 300):
        inp = json.dumps({"problem": short, "candidates": cand}, ensure_ascii=False)
        if len(inp) + len(RERANK_SYS) < 27500:
            break
        cand = [{**k, "abstract": k["abstract"][:lim]} for k in cand]
    out = ask(RERANK_SYS, inp)
    by = {c["id"]: c for c in top}
    seen = set()
    for r in out.get("results") or []:
        c = by.get(r.get("id"))
        if not c or c["id"] in seen:
            continue
        seen.add(c["id"])
        tier = r.get("tier") if r.get("tier") in TIER else "low"
        basis = str(r.get("basis", "")).strip()
        text = X._norm(c["title"] + " " + c["abstract"])
        ok = bool(X._norm(basis)) and X._norm(basis) in text
        if not ok and TIER[tier] > 2:
            tier = "medium"
        if not c["abstract"] and TIER[tier] > 2:
            tier = "medium"
        c.update({"tier": tier, "helps": str(r.get("helps", "")).strip()[:420], "related": [str(x)[:80] for x in (r.get("related") or [])][:5],
                  "basis": basis[:220], "basis_ok": ok})
    for c in top:
        if c["id"] not in seen:
            c.update({"tier": "low", "helps": "", "related": [], "basis": "", "basis_ok": False, "not_assessed": True})
    return sorted(top, key=lambda c: (-TIER[c["tier"]], -(c["score"] or 0)))


def find(pr, ask, job=None, user_query="", n_top=18):
    def stage(t, p):
        if job is not None:
            job["stage"], job["progress"] = t, p

    ctx = context(pr)
    uq = user_query.strip()
    if uq:
        m = DOI_RX.search(uq)
        if m:  # a pasted DOI: look the paper up directly
            stage("Looking up the DOI", 0.2)
            doi = m.group(0).rstrip(".,;)")
            try:
                hits = oa_doi(doi)
            except Exception:
                hits = []
            if not hits:
                try:
                    hits = cr_doi(doi)
                except Exception:
                    raise RuntimeError("DOI not found in OpenAlex or Crossref: " + doi)
            for c in hits:
                c["hits"] = {0: 0}
            cands, status, queries, how = hits, {hits[0]["src"]: {"ok": 1, "fail": 0, "error": ""}}, [{"q": doi, "for": "your DOI", "hypotheses": []}], "your DOI"
        else:
            queries, how = [{"q": re.sub(r"\s+", " ", re.sub(r"[\"()]", " ", uq))[:120], "for": "your own query", "hypotheses": []}], "your own query"
            stage("Searching OpenAlex, Scopus and Crossref", 0.2)
            cands, status = search_all(queries, pr, per=25)
    else:
        stage("Writing search queries from your hypotheses and parameters", 0.1)
        queries, how = gen_queries(ctx, ask)
        stage(f"Searching OpenAlex, Scopus and Crossref ({len(queries)} queries)", 0.3)
        cands, status = search_all(queries, pr)
    if not cands:
        errs = "; ".join(f"{k}: {v['error']}" for k, v in status.items() if v["error"])
        raise RuntimeError("No papers found. " + (errs or "Try different words."))
    cands = prerank(cands, queries, ctx)[:n_top]
    need = [c for c in cands if not c["abstract"] and c["doi"]][:8]
    if need:
        stage("Fetching missing abstracts from Crossref", 0.5)
        for c in need:
            c["abstract"] = cr_abstract(c["doi"])
            time.sleep(0.3)
    for i, c in enumerate(cands, 1):
        c["id"] = f"c{i}"
    stage(f"Rating {len(cands)} candidates against your hypotheses", 0.6)
    warn = ""
    try:
        cands = rate(ctx, cands, ask)
    except Exception as e:
        if "Cancelled" in type(e).__name__:
            raise
        warn = "The AI rating failed (" + str(e)[:140] + "). The list below is ordered by the search score only."
        for c in cands:
            c.update({"tier": "low", "helps": "", "related": [], "basis": "", "basis_ok": False, "not_assessed": True})
    for c in cands:
        c["src"] = " + ".join(c["src"])
    sums = X_ver(pr)
    return {"built": time.strftime("%Y-%m-%d %H:%M:%S"), "label": LABEL, "basis": ctx["basis"], "query_by": how, "user": uq,
            "queries": queries, "warnings": warn, "source": status, "candidates": cands, "report_ver": sums,
            "notes": "Scopus returns titles only; abstracts come from OpenAlex or Crossref when they have them."}


def X_ver(pr):
    L = pr.get("report") or []
    return L[-1].get("ver") if L else None


# ---------------- parameter <-> result ----------------
def _match_field(name, fields):
    a = set(_toks(name))
    best, sc = None, 0.0
    for f in fields:
        b = set(_toks(f["name"]))
        if not a or not b:
            continue
        s = len(a & b) / len(a | b)
        if s > sc:
            best, sc = f, s
    return best if sc >= 0.5 else None


KW_LABEL = ("Keyword co-occurrence in titles, abstracts and keywords (not full text): the share of papers on the result that also "
            "mention the parameter. It shows how often researchers discuss the two together. It is not an effect size.")


GENERIC_KW = set("range type value values level levels amount during synthesis condition conditions bar min kg wt mol mpa kpa pa degc rpm ml mg at per".split())


def _short_kw(s, n=4):
    w = re.sub(r"\(.*?\)", " ", str(s))
    w = re.split(r"\s=\s|\s[<>]\s", w)[0]  # the model sometimes writes "name = value"
    w = [x for x in re.findall(r"[A-Za-z][A-Za-z0-9\-]*", w) if x.lower() not in STOP and x.lower() not in GENERIC_KW and len(x) > 1]
    return " ".join(w[:n])


def default_context(pr):
    """A subject keyword for the keyword search, taken from the first verified material (the user can edit it)."""
    for f in pr.get("fields", []):
        if f.get("role") == "materials" and f.get("verified") and f.get("value"):
            return _short_kw(f["value"], 3)
    return ""


def kw_percent(result_term, names, ctx=""):
    """Share of papers on the result (and context) that also mention each parameter. Scopus first, OpenAlex as fallback."""
    res = _short_kw(result_term, 4)
    out = {"label": KW_LABEL, "result_kw": res, "context": ctx, "date": time.strftime("%Y-%m-%d"), "source": "", "base": None, "rows": {}, "error": ""}
    if not res:
        out["error"] = "no result keyword"
        return out
    counters = []
    try:
        key = RL._scopus_key()
        counters.append(("Scopus", lambda e: RL.sc_count(e, key)))
    except Exception:
        pass
    counters.append(("OpenAlex", RL.oa_count))
    errs = []
    for sname, counter in counters:
        try:
            base = counter(RL._expr([res], ctx))
            if base < 30 and ctx:  # too few papers with the subject keyword: widen to the result keyword alone
                out["context_dropped"] = ctx
                ctx = out["context"] = ""
                base = counter(RL._expr([res], ctx))
            if not base:
                errs.append(sname + ": no papers found for the result keyword")
                continue
            def one(name):
                w = _short_kw(name, 6).split()
                for k in dict.fromkeys([" ".join(w[-2:]), w[-1]] if w else []):  # two words first, then the last word alone
                    n = counter(RL._expr([res, k], ctx))
                    if n:
                        return {"kw": k, "n": n, "pct": RL._pct(n, base)}
                return {"kw": " ".join(w[-2:]), "n": 0, "pct": 0.0}
            with ThreadPoolExecutor(4) as ex:
                rows = dict(zip(names, ex.map(one, names)))
            out.update(source=sname, base=base, rows=rows)
            return out
        except Exception as e:
            errs.append(f"{sname}: {type(e).__name__}: {e}"[:160])
    out["error"] = "; ".join(errs)
    return out


def relate(pr, question, ask, ctx=None):
    pages = pr.get("pages", [])
    F = [f for f in pr.get("fields", []) if f.get("verified")]
    qw = set(re.findall(r"[a-z0-9]{2,}", question.lower()))
    res_f = [f for f in F if f.get("role") == "results"]
    rel_f = [f for f in res_f if (qw & set(re.findall(r"[a-z0-9]{2,}", f["name"].lower()))) - STOP] or res_f
    pick = set(X.retrieve(pages, question + " " + " ".join(f["name"] for f in rel_f[:4]), budget=9000, k=3) or [0])
    for f in rel_f[:6]:  # pages where the paper reports the related results
        if isinstance(f.get("page"), int) and 1 <= f["page"] <= len(pages) and len(pick) < 5:
            pick.add(f["page"] - 1)
    pick = sorted(pick)
    fl = "\n".join(f"[{f.get('role')}] {f['name']} = {f['value']} {f.get('unit', '')} (p.{f.get('page')})" for f in F)
    pg = "\n".join(f"[PAGE {i + 1}]\n{pages[i][:4500]}" for i in pick)
    for lim in (6500, 3500, 1500, 0):
        inp = json.dumps({"questions": question}, ensure_ascii=False) + "\n\nPARAMETERS:\n" + fl[:lim] + "\n\nPAPER EXCERPTS:\n" + pg
        if len(inp) + len(REL_SYS) < 28500:
            break
    else:
        inp = inp[: 28500 - len(REL_SYS)]
    out = ask(REL_SYS, inp)
    rows, dropped = [], 0
    for p in out.get("parameter") or []:
        if not isinstance(p, dict) or not str(p.get("name", "")).strip():
            dropped += 1
            continue
        b = p.get("evidence") if p.get("evidence") in ("quantitative", "qualitative", "guess") else "guess"
        h = p.get("page") if isinstance(p.get("page"), int) else None
        q = str(p.get("quote") or "").strip()[:300]
        ok = bool(q) and X.quote_ok(pr, h, q)
        note = ""
        if b != "guess" and not ok:
            note = "quote not found on that page, so this is shown as an AI inference"
            b = "guess"
        if b == "quantitative" and not re.search(r"\d", q):
            b = "qualitative"
        f = _match_field(str(p["name"]), F)
        rows.append({"name": str(p["name"]).strip()[:120], "from_list": bool(f), "fid": f["id"] if f else None, "direction": str(p.get("direction", "unclear"))[:20],
                     "effect": str(p.get("effect", "")).strip()[:320], "evidence": b, "page": h if ok else (h if b != "guess" else None),
                     "quote": q if ok else "", "quote_ok": ok, "notes": note})
    order = {"quantitative": 0, "qualitative": 1, "guess": 2}
    rows = [r for _, r in sorted(enumerate(rows), key=lambda t: (order[t[1]["evidence"]], t[0]))]
    kw = {}
    try:
        kw = kw_percent(str(out.get("results") or question), [r["name"] for r in rows[:8]], default_context(pr) if ctx is None else ctx)
    except Exception as e:
        kw = {"label": KW_LABEL, "error": f"{type(e).__name__}: {e}"[:160], "rows": {}}
    for r in rows:
        r["kw"] = (kw.get("rows") or {}).get(r["name"])
    return {"q": question, "kw": kw, "time": time.strftime("%Y-%m-%d %H:%M:%S"), "results": str(out.get("results", "")).strip()[:200],
            "brief": str(out.get("brief", "")).strip()[:500], "parameter": rows[:8], "not_discussed": str(out.get("not_discussed") or "").strip()[:300],
            "pages_read": [i + 1 for i in pick], "dropped": dropped}
