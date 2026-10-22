"""Citation network among the candidate papers (no model call).

Edges come from the reference lists that publishers deposit with Crossref:
  - "cites": candidate A lists candidate B (by DOI) in its bibliography;
  - "prior": candidate A cites an earlier work that at least two candidates cite (a shared foundation).
Papers whose publisher did not deposit references are shown without edges and listed as such.
"""
import re
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

import related as RL

MAX_PAPERS = 14
MAX_PRIOR = 8
CR = "https://api.crossref.org/works/"


def _doi(s):
    s = str(s or "").strip().lower()
    s = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)", "", s)
    return s if s.startswith("10.") else ""


def _work(doi):
    """Crossref record for a DOI; one retry on rate limiting."""
    for attempt in (0, 1):
        try:
            return RL._json(CR + urllib.parse.quote(doi, safe="/"))["message"]
        except Exception as e:  # noqa: BLE001 - network/HTTP errors all mean "no data for this DOI"
            if attempt == 0 and "429" in str(e):
                time.sleep(2.5)
                continue
            raise
    return {}


def _refs(doi):
    out = {}
    for r in _work(doi).get("reference") or []:
        d = _doi(r.get("DOI"))
        if d:
            title = r.get("article-title") or r.get("volume-title") or r.get("unstructured") or ""
            out[d] = {"title": re.sub(r"\s+", " ", str(title)).strip()[:160], "year": str(r.get("year") or "")}
    return out


def _meta(doi):
    m = _work(doi)
    yr = ((m.get("issued") or {}).get("date-parts") or [[None]])[0][0]
    return {"title": re.sub(r"\s+", " ", " ".join(m.get("title") or [])).strip()[:200], "year": yr,
            "citations": m.get("is-referenced-by-count") or 0}


def candidates(pr):
    """Strong and possible papers from the automatic and the own search, one entry per paper, with a unique node id."""
    seen, used, out = set(), set(), []
    for name, src in (("rec", pr.get("recommendations")), ("own", pr.get("own_search"))):
        for c in (src or {}).get("candidates") or []:
            d = _doi(c.get("doi"))
            key = d or (name, c.get("id"))
            if c.get("tier") == "low" or key in seen:
                continue
            seen.add(key)
            nid = c["id"] if c["id"] not in used else name + "_" + c["id"]
            used.add(nid)
            out.append({**c, "nid": nid, "set": name})
    out.sort(key=lambda c: ({"high": 0, "medium": 1}.get(c.get("tier"), 2), -(c.get("citations") or 0)))
    return out[:MAX_PAPERS]


def build(pr, job=None):
    C = candidates(pr)
    if not C:
        raise RuntimeError("There are no strong or possible papers to connect yet. Run the paper search first.")
    if job is not None:
        job["stage"] = f"Reading the bibliographies of {len(C)} papers (Crossref)"
    dois = {c["nid"]: _doi(c.get("doi")) for c in C}
    refs, errors = {}, {}

    def one(c):
        d = dois[c["nid"]]
        if not d:
            return c["nid"], None, "no DOI"
        try:
            return c["nid"], _refs(d), ""
        except Exception as e:  # noqa: BLE001
            return c["nid"], None, type(e).__name__ + ": " + str(e)[:80]

    with ThreadPoolExecutor(4) as ex:
        for cid, r, err in ex.map(one, C):
            if r is None:
                errors[cid] = err
            else:
                refs[cid] = r

    for cid in [k for k, v in errors.items() if v != "no DOI"]:  # one calm retry for papers that failed (rate limit, timeout)
        time.sleep(1.5)
        _, r, err = one(next(c for c in C if c["nid"] == cid))
        if r is not None:
            refs[cid] = r
            errors.pop(cid)
        else:
            errors[cid] = err

    by_doi = {d: cid for cid, d in dois.items() if d}
    nodes = []
    for i, c in enumerate(C):
        nodes.append({"id": c["nid"], "cid": c["id"], "set": c["set"], "kind": "paper", "n": i + 1, "title": c.get("title", ""), "year": c.get("year"),
                      "citations": c.get("citations") or 0, "doi": dois[c["nid"]], "tier": c.get("tier"),
                      "helps": c.get("helps", ""), "related": c.get("related") or [],
                      "n_refs": len(refs[c["nid"]]) if c["nid"] in refs else None})
    edges = []
    for a, ra in refs.items():
        for d in ra:
            b = by_doi.get(d)
            if b and b != a:
                edges.append({"a": a, "b": b, "type": "cites"})

    cnt = {}
    for cid, ra in refs.items():
        for d in ra:
            if d not in by_doi:
                cnt.setdefault(d, []).append(cid)
    shared = sorted(((d, ids) for d, ids in cnt.items() if len(ids) >= 2), key=lambda x: -len(x[1]))[:MAX_PRIOR]
    if shared and job is not None:
        job["stage"] = f"Looking up {len(shared)} shared earlier works"

    def meta(d):
        try:
            return _meta(d)
        except Exception:  # noqa: BLE001
            return {}

    with ThreadPoolExecutor(4) as ex:
        metas = list(ex.map(meta, [d for d, _ in shared]))
    for k, ((d, ids), m) in enumerate(zip(shared, metas)):
        first = refs[ids[0]][d]
        nid = "x" + str(k + 1)
        nodes.append({"id": nid, "kind": "prior", "n": chr(97 + k), "title": m.get("title") or first["title"] or d,
                      "year": m.get("year") or (int(first["year"]) if first["year"].isdigit() else None),
                      "citations": m.get("citations") or 0, "doi": d, "tier": "", "helps": "", "related": [],
                      "n_refs": None})
        for cid in ids:
            edges.append({"a": cid, "b": nid, "type": "prior"})

    coupling = {}
    ids = list(refs)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            n = len(set(refs[a]) & set(refs[b]))
            if n >= 2:
                coupling.setdefault(a, []).append({"id": b, "n": n})
                coupling.setdefault(b, []).append({"id": a, "n": n})
    for k in coupling:
        coupling[k] = sorted(coupling[k], key=lambda x: -x["n"])[:4]

    return {"built": time.strftime("%Y-%m-%d %H:%M:%S"), "source": "Crossref reference lists", "nodes": nodes,
            "edges": edges, "coupling": coupling, "no_refs": {k: v for k, v in errors.items()},
            "stats": {"papers": len(C), "with_refs": len(refs), "cites": sum(e["type"] == "cites" for e in edges),
                      "prior": len(shared)}}
