"""Tool 4: related papers.
(a) keyword co-occurrence counts from Scopus and OpenAlex (title/abstract[/keywords] only, NOT full text) plus a paper
    listing from Scopus, OpenAlex and Crossref (Crossref has no boolean counts, so it only lists);
(b) a literature set of full-text PDFs: the model reads each one and proposes variables to control, with ranges taken
    from the quoted evidence, only for variables the target paper does not report."""
import configparser, ipaddress, itertools, json, os, re, socket, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import extras as X
import pdfio

UA = "ResultGapDiagnosis/1.0 (local research tool)"
LABEL = ("Keyword co-occurrence in titles and abstracts (Scopus also counts author keywords). This is NOT full-text analysis and NOT "
         "the effect of a variable on a result: it only says how often two words appear together in the literature.")


def _public_url(url):
    """Allow only http(s) URLs that resolve to public addresses (blocks file:, localhost and LAN targets)."""
    u = urllib.parse.urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise RuntimeError("only http(s) links are allowed")
    for info in socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80)):
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise RuntimeError("the link points to a private or local address")


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_SafeRedirect)


def _http(url, headers=None, timeout=30):
    _public_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with _OPENER.open(req, timeout=timeout) as r:
        return r.read()


def _json(url, headers=None):
    return json.loads(_http(url, headers).decode("utf-8"))


def _scopus_key():
    c = configparser.ConfigParser()
    c.read(os.path.expanduser("~/.config/pybliometrics.cfg"))
    return c["Authentication"]["APIKey"].split(",")[0].strip()


def _clean(t):
    return re.sub(r'["()\\]', " ", str(t)).strip()


def _expr(terms, ctx):
    parts = ([f'"{_clean(ctx)}"'] if ctx else []) + [f'"{_clean(t)}"' for t in terms]
    return " AND ".join(parts)


def _oa_params(extra):
    p = dict(extra)
    if os.environ.get("OPENALEX_API_KEY"):
        p["api_key"] = os.environ["OPENALEX_API_KEY"]
    return urllib.parse.urlencode(p)


def oa_count(expr):
    return _json("https://api.openalex.org/works?" + _oa_params({"filter": "title_and_abstract.search:" + expr, "per-page": 1}))["meta"]["count"]


def sc_count(expr, key):
    q = urllib.parse.urlencode({"query": f"TITLE-ABS-KEY({expr})", "count": 1})
    d = _json("https://api.elsevier.com/content/search/scopus?" + q, {"X-ELS-APIKey": key, "Accept": "application/json"})
    return int(d["search-results"]["opensearch:totalResults"])


def _pct(a, b):
    return round(100.0 * a / b, 2) if b else None


def _stats(terms, ctx, counter):
    jobs = {"all": terms}
    for t in terms:
        jobs["s:" + t] = [t]
    for a, b in itertools.combinations(terms, 2):
        jobs[f"p:{a}|{b}"] = [a, b]
    with ThreadPoolExecutor(6) as ex:
        fut = {k: ex.submit(counter, _expr(v, ctx)) for k, v in jobs.items()}
        n = {k: f.result() for k, f in fut.items()}
    single = {t: n["s:" + t] for t in terms}
    pairs = []
    for a, b in itertools.combinations(terms, 2):
        ab = n[f"p:{a}|{b}"]
        pairs.append({"a": a, "b": b, "n_ab": ab, "pct_of_a": _pct(ab, single[a]), "pct_of_b": _pct(ab, single[b]),
                      "pct_either": _pct(ab, single[a] + single[b] - ab)})
    return {"single": single, "pairs": pairs, "all": n["all"],
            "all_pct_of": {t: _pct(n["all"], single[t]) for t in terms}}


def list_openalex(terms, ctx, n=10):
    d = _json("https://api.openalex.org/works?" + _oa_params({"filter": "title_and_abstract.search:" + _expr(terms, ctx), "per-page": n, "sort": "cited_by_count:desc"}))
    res = []
    for w in d.get("results", []):
        loc = w.get("best_oa_location") or {}
        res.append({"title": w.get("display_name") or "", "year": w.get("publication_year"), "doi": (w.get("doi") or "").replace("https://doi.org/", ""),
                    "journal": ((w.get("primary_location") or {}).get("source") or {}).get("display_name") or "", "citations": w.get("cited_by_count"),
                    "oa": bool((w.get("open_access") or {}).get("is_oa")), "pdf": loc.get("pdf_url") or "", "src": ["OpenAlex"]})
    return res


def list_scopus(terms, ctx, key, n=10):
    q = urllib.parse.urlencode({"query": f"TITLE-ABS-KEY({_expr(terms, ctx)})", "count": n, "sort": "-citedby-count"})
    d = _json("https://api.elsevier.com/content/search/scopus?" + q, {"X-ELS-APIKey": key, "Accept": "application/json"})["search-results"]
    res = []
    for e in d.get("entry", []):
        if "dc:title" not in e:
            continue
        cd = (e.get("prism:coverDate") or "")[:4]
        res.append({"title": e["dc:title"], "year": int(cd) if cd.isdigit() else None, "doi": e.get("prism:doi") or "",
                    "journal": e.get("prism:publicationName") or "", "citations": int(e.get("citedby-count") or 0),
                    "oa": str(e.get("openaccessFlag")).lower() == "true", "pdf": "", "src": ["Scopus"]})
    return res


def list_crossref(terms, ctx, n=10):
    q = urllib.parse.urlencode({"query.bibliographic": " ".join(([ctx] if ctx else []) + terms), "rows": n,
                                "select": "DOI,title,issued,container-title,is-referenced-by-count"})
    d = _json("https://api.crossref.org/works?" + q)["message"]["items"]
    d = [w for w in d if not re.match(r"(review for|decision letter|author response|referee report|reviewer)", (w.get("title") or [""])[0], re.I)]
    return [{"title": (w.get("title") or [""])[0], "year": ((w.get("issued") or {}).get("date-parts") or [[None]])[0][0], "doi": w.get("DOI") or "",
             "journal": (w.get("container-title") or [""])[0], "citations": w.get("is-referenced-by-count"), "oa": False, "pdf": "", "src": ["Crossref"]} for w in d]


def cooccur(terms, ctx=""):
    terms = list(dict.fromkeys(t.strip() for t in terms if t and t.strip()))[:4]
    if len(terms) < 2:
        raise ValueError("Give at least two keywords")
    out = {"label": LABEL, "terms": terms, "context": ctx.strip(), "date": time.strftime("%Y-%m-%d"), "source": {}, "items": []}
    try:
        key = _scopus_key()
    except Exception as e:
        key = None
        out["source"]["Scopus"] = {"ok": False, "error": "no Scopus API key configured: " + str(e)[:100]}
    tasks = {"OpenAlex": (lambda: _stats(terms, ctx, oa_count), lambda: list_openalex(terms, ctx))}
    if key:
        tasks["Scopus"] = (lambda: _stats(terms, ctx, lambda e: sc_count(e, key)), lambda: list_scopus(terms, ctx, key))
    allp = []
    for name, (st, ls) in tasks.items():
        try:
            out["source"][name] = {"ok": True, **st()}
        except Exception as e:
            out["source"][name] = {"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}
            continue
        try:
            allp += ls()
        except Exception as e:
            out["source"][name]["list_error"] = f"{type(e).__name__}: {e}"[:200]
    try:
        allp += list_crossref(terms, ctx)
        out["source"]["Crossref"] = {"ok": True, "notes": "listing only (relevance-ranked); Crossref has no boolean counts, so no percentages"}
    except Exception as e:
        out["source"]["Crossref"] = {"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}
    merged = {}
    for p in allp:
        k = p["doi"].lower() or p["title"].lower()[:80]
        if not k:
            continue
        if k in merged:
            m = merged[k]
            m["src"] = m["src"] + [s for s in p["src"] if s not in m["src"]]
            m["oa"] = m["oa"] or p["oa"]
            m["pdf"] = m["pdf"] or p["pdf"]
            m["citations"] = max(m["citations"] or 0, p["citations"] or 0)
        else:
            merged[k] = dict(p)
    out["items"] = sorted(merged.values(), key=lambda x: -(x["citations"] or 0))[:20]
    return out


def fetch_pdf(url, max_mb=40):
    data = _http(url, {"Accept": "application/pdf"}, timeout=60)
    if len(data) > max_mb * 1e6 or data[:5] != b"%PDF-":
        raise RuntimeError("the link did not return a PDF (it may need a login or a browser)")
    return data


# ---------------- literature set (full text) ----------------
KEYS = re.compile(r"experiment|method|procedure|temperature|ratio|concentration|varied|vary|effect|range|table|parameter|condition|optimi", re.I)


def pick_text(pages, limit=21000):
    if sum(len(p) for p in pages) <= limit:
        idx = list(range(len(pages)))
    else:
        sc = sorted(((len(KEYS.findall(p)) / max(len(p), 1), i) for i, p in enumerate(pages)), reverse=True)
        idx, used = [], 0
        for _, i in sc:
            if used + len(pages[i]) <= limit:
                idx.append(i)
                used += len(pages[i])
        idx.sort()
    return "\n".join(f"[PAGE {i + 1}]\n{pages[i]}" for i in idx)


LIT_SYS = """You read one scientific paper (text split by page, marked [PAGE n]). List the variables the authors deliberately VARIED or STUDIED (independent variables: not fixed constants), because a researcher wants to know which variables matter and what ranges people test.
Return valid JSON only: {"title":"paper title","variables":[{"name":"variable name, short","range":"range or levels tested, exactly as stated, with unit","unit":"unit","effect":"reported effect on the main outcome in <=15 words, only if the paper states it, else empty","quote":"text copied EXACTLY from the page, <=200 chars, one continuous passage that contains the range or the effect","page":int}]}
RULES: max 10 variables, most important first. No guesses: every item needs an exact quote and its page. Do not list outcomes/measured results as variables."""

SYN_SYS = """A researcher is reproducing a TARGET paper and wants to know which variables to control, based on a set of other papers (JSON: for each paper the variables it varied, with ranges and effects).
Return valid JSON only: {"variables":[{"name":"variable name","reason":"why it matters for the target method, one sentence, using the effects reported","suggested_range":"range to control or scan, built ONLY from ranges in the papers' items (give the overlapping or widest range with unit)","unit":"unit","paper_idx":["ids of the papers that vary it, e.g. l1"]}]}
RULES: max 10, most widely studied first; include a variable only if at least one paper's item supports it; never invent a range; do NOT include variables that are already in the target parameter list (target_parameter_names); one entry per variable (merge synonyms)."""


def _pages_file(upl, pid, lid):
    return upl / f"{pid}_lit_{lid}.json"


def analyse(pr, upl, ask, job):
    lp = pr["library"]
    papers = lp["papers"]
    n = len(papers)
    job["total_calls"] = n + 1
    job["estimate_seconds"] = (n + 1) * 100
    for p in papers:  # scanned pages first, one page at a time
        pf = _pages_file(upl, pr["id"], p["id"])
        pg = json.loads(pf.read_text(encoding="utf-8"))
        pend = p.get("ocr_pending") or []
        if pend and p.get("file"):
            job["stage"] = f"Reading scanned pages of {p['name'][:40]} (about 1 min per page)"
            info = pdfio.ocr_fill(upl / p["file"], pg, pend, cancelled=lambda: job.get("cancel"))
            p["ocr_pending"] = [i for i in pend if (i + 1) not in info["ocr_pages"]]
            pf.write_text(json.dumps(pg, ensure_ascii=False), encoding="utf-8")
    ext, bad = {}, 0
    for k, p in enumerate(papers):
        job["stage"] = f"Reading paper {k + 1} of {n}: {p['name'][:50]}"
        pg = json.loads(_pages_file(upl, pr["id"], p["id"]).read_text(encoding="utf-8"))
        try:
            out = ask(LIT_SYS, pick_text(pg))
        except Exception as e:
            if type(e).__name__ == "Cancelled":
                raise
            p["error"] = str(e)[:200]
            job["progress"] = (k + 1) / (n + 1)
            continue
        items = []
        for v in out.get("variables") or []:
            if not isinstance(v, dict) or not str(v.get("name", "")).strip():
                continue
            h = v.get("page")
            ok = isinstance(h, int) and 1 <= h <= len(pg) and bool(v.get("quote")) and X._norm(v["quote"]) in X._norm(pg[h - 1])
            if not ok:
                bad += 1
                continue
            items.append({"name": str(v["name"]).strip(), "range": str(v.get("range", "")).strip(), "unit": str(v.get("unit", "")).strip(),
                          "effect": str(v.get("effect", "")).strip(), "quote": str(v["quote"])[:300], "page": h})
        p["title"] = str(out.get("title") or p["name"])[:200]
        p["n_variables"] = len(items)
        p.pop("error", None)
        ext[p["id"]] = items
        job["progress"] = (k + 1) / (n + 1)
    usable = {pid: it for pid, it in ext.items() if it}
    if not usable:
        raise RuntimeError("no variable with a verified quote could be read from the uploaded papers")
    job["stage"] = "Comparing the papers and choosing variables to control"
    names = {p["id"]: p.get("title") or p["name"] for p in papers}
    inp = json.dumps({"target_summary": (pr.get("method") or {}).get("summary", ""),
                      "target_parameter_names": [f["name"] for f in pr.get("fields", []) if f.get("verified")][:80],
                      "papers": [{"id": pid, "title": names[pid][:100], "variables": [{k: v[k] for k in ("name", "range", "effect")} for v in it]} for pid, it in usable.items()]},
                     ensure_ascii=False)
    out = ask(SYN_SYS, inp[:26000])
    res, dropped, no_ev = [], 0, 0
    proposed = [v for v in (out.get("variables") or []) if isinstance(v, dict) and str(v.get("name", "")).strip()]
    for v in proposed:
        if X.in_paper(v["name"], pr.get("fields", []), pr.get("pages", [])):
            dropped += 1
            continue
        vt = X._toks(v["name"])
        ev = []
        for pid, it in usable.items():
            for x in it:
                xt = X._toks(x["name"])
                if vt and xt and len(vt & xt) / min(len(vt), len(xt)) >= 0.5:
                    ev.append({"paper": pid, "paper_name": names[pid], "page": x["page"], "quote": x["quote"], "range": x["range"], "effect": x["effect"]})
        if not ev:
            no_ev += 1
            continue
        res.append({"name": str(v["name"]).strip(), "reason": str(v.get("reason", "")).strip(), "suggested_range": str(v.get("suggested_range", "")).strip(),
                    "unit": str(v.get("unit", "")).strip(), "paper_count": len({e["paper"] for e in ev}), "evidence": ev[:6]})
    res.sort(key=lambda x: -x["paper_count"])
    lp["results"] = {"built": time.strftime("%Y-%m-%d %H:%M:%S"), "n_paper": n, "n_read": len(usable), "variables": res,
                   "dropped_already_in_paper": dropped, "quotes_unverified": bad, "proposed": len(proposed), "no_evidence": no_ev,
                   "per_paper": [{"id": pid, "title": names[pid], "variables": [x["name"] for x in it]} for pid, it in usable.items()]}
    return len(res)
