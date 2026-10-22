"""Result step: one recommendation per differing parameter (branch A) and hidden variables for a side that has no
differences (branch B). Two sides are handled separately: "materials_conditions" and "equipment".
Code decides WHICH parameters differ (logic.compute_all); the model only writes the advice. Nothing here runs a solver."""
import json, re, time
import extras as X

SIDES = ("materials_conditions", "equipment")


def side_of(field):
    return "equipment" if field.get("role") == "equipment" else "materials_conditions"


def differing(pr, comp_param):
    """Fields the user changed AND that code marks as different (never assumed or untouched values)."""
    out = []
    for f in pr.get("fields", []):
        if f.get("role") == "results":
            continue
        u = (pr.get("compare") or {}).get(f["id"]) or {}
        st = (comp_param.get(f["id"]) or {}).get("status")
        if st == "different" and not u.get("prefill") and str(u.get("value", "")).strip() != "":
            out.append((f, u))
    return out


RECS_SYS = """You help a researcher whose experiment or simulation differs from a paper. Code has already decided which parameters differ; you only write advice.
You get JSON: "differing" (each with id, name, side, paper value/unit, the user's value/unit, the verified paper quote and page), "results" (paper results and the user's results), "same_sides" (sides where nothing differs), "paper_parameters" (names already reported in the paper) and PAPER EXCERPTS.
Return valid JSON only:
{"items":[{"id":"id copied from differing","recommendation":"one imperative sentence, e.g. 'Repeat the run at the paper value of 85 degC' or 'Check the pressure gauge calibration'","reason":"why this parameter can change the result, <=30 words","details":"fuller reasoning, <=110 words, plain text","affects":["names of results it may affect"],"basis":"paper|general","paper_quote":{"page":int,"quote":"text copied EXACTLY from that page, <=200 chars"} or null,"confidence":"low|medium|high"}],
 "hidden":[{"side":"materials_conditions|equipment","variable":"name","reason":"how it could change the outcome, <=30 words","range":"suggested range WITH numbers and unit, or the allowed options","unit":"unit or empty","how_to_control":"how to hold it fixed or measure it"}]}
RULES:
1. One item per differing parameter, same id. Each has its own recommendation and reason; never merge two parameters.
2. "basis":"paper" only when the reason is supported by a quote you copy EXACTLY from the excerpts into paper_quote; otherwise "general" and paper_quote null.
3. Never say a cause is proven; use 'may', 'possible', 'worth checking'. Never invent numbers, pages or quotes.
4. "hidden": ONLY for sides listed in same_sides (max 5 per side, most important first). They must be variables the paper does NOT report (never repeat a name in paper_parameters). For the equipment side give equipment-related variables (calibration, dead volume, material of construction, sensor accuracy, heat-transfer, ...). Every range is an AI estimate for a typical setup; if you cannot defend a numeric range, give the allowed options. If a side is not in same_sides return no hidden items for it.
5. Plain English, short. Do not name brands."""


def _clip(s, n):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def build_recs(pr, ask, verify, comp_param):
    diff = differing(pr, comp_param)
    sides_diff = {side_of(f) for f, _ in diff}
    same = [s for s in SIDES if s not in sides_diff]
    pages = pr.get("pages", [])
    flds = pr.get("fields", [])
    dj = [{"id": f["id"], "name": f["name"], "side": side_of(f), "paper_value": f["value"], "paper_unit": f.get("unit", ""),
           "user_value": u.get("value"), "user_unit": u.get("unit", ""), "paper_quote": _clip(f.get("quote"), 220), "page": f.get("page")}
          for f, u in diff]
    res = [{"name": f["name"], "paper_value": f["value"], "unit": f.get("unit", "")} for f in flds if f.get("role") == "results" and f.get("verified")][:10]
    ures = [{"name": h.get("name"), "value": h.get("value"), "unit": h.get("unit", "")} for h in pr.get("results", []) if str(h.get("value") or "").strip()][:10]
    q = " ".join([d["name"] for d in dj] + [r["name"] for r in res[:4]]) or "method"
    pick = X.retrieve(pages, q, budget=9000, k=4) or [0]
    pg = "\n".join(f"[PAGE {i + 1}]\n{pages[i][:2500]}" for i in pick)
    base = {"differing": dj, "results": {"paper": res, "user": ures}, "same_sides": same, "paper_parameters": [f["name"] for f in flds][:60]}
    inp = json.dumps(base, ensure_ascii=False) + "\n\nPAPER EXCERPTS:\n" + pg
    if len(inp) + len(RECS_SYS) > 28500:
        inp = inp[: 28500 - len(RECS_SYS)]
    out = ask(RECS_SYS, inp)
    ids = {d["id"]: d for d in dj}
    items, seen, bad_quote = [], set(), 0
    for x in out.get("items") or []:
        if not isinstance(x, dict) or x.get("id") not in ids or x["id"] in seen:
            continue
        seen.add(x["id"])
        d = ids[x["id"]]
        pq = x.get("paper_quote") if isinstance(x.get("paper_quote"), dict) else None
        cite = None
        if pq and pq.get("quote"):
            ok = bool(verify(pr, pq.get("page"), pq["quote"]))
            if ok:
                cite = {"page": pq.get("page"), "quote": _clip(pq["quote"], 220), "verified": True}
            else:
                bad_quote += 1
        basis = "paper" if cite else "general"
        items.append({"id": d["id"], "name": d["name"], "side": d["side"],
                      "paper_value": d["paper_value"], "paper_unit": d["paper_unit"], "user_value": d["user_value"], "user_unit": d["user_unit"],
                      "paper_quote": d["paper_quote"], "page": d["page"],
                      "recommendation": _clip(x.get("recommendation"), 200), "reason": _clip(x.get("reason"), 240),
                      "details": _clip(x.get("details"), 900), "affects": [_clip(a, 60) for a in (x.get("affects") or [])[:4] if str(a).strip()],
                      "basis": basis, "cite": cite, "confidence": x.get("confidence") if x.get("confidence") in ("low", "medium", "high") else "low"})
    for d in dj:  # a differing parameter the model skipped still gets a card, built by code, so nothing is silently lost
        if d["id"] not in seen:
            items.append({"id": d["id"], "name": d["name"], "side": d["side"], "paper_value": d["paper_value"], "paper_unit": d["paper_unit"],
                          "user_value": d["user_value"], "user_unit": d["user_unit"], "paper_quote": d["paper_quote"], "page": d["page"],
                          "recommendation": "Repeat with the paper value, or record why yours differs.", "reason": "The model gave no specific advice for this parameter.",
                          "details": "", "affects": [], "basis": "general", "cite": None, "confidence": "low", "no_ai": True})
    hidden, dropped = [], 0
    by_side = {}
    for x in out.get("hidden") or []:
        if isinstance(x, dict) and x.get("side") in same:
            by_side.setdefault(x["side"], []).append(x)
    for sd, lst in by_side.items():
        norm, dr = X.normalize_controls(lst, pr)
        dropped += dr
        for n in norm[:5]:
            n["side"] = sd
            hidden.append(n)
    return {"built": time.strftime("%Y-%m-%d %H:%M:%S"), "items": items, "hidden": hidden, "same_sides": same,
            "dropped_already_in_paper": dropped, "quotes_unverified": bad_quote, "n_differing": len(dj)}
