"""Core logic of the Result Gap Diagnosis app.
Reuses diagnosa.py (prompts, extract_all, verify, norm, pages) without modifying it:
the file is read as text, the main() call is removed, and the rest is executed in its own namespace.
The app runs no simulation or solver; it only gives recommendations."""
import copy, difflib, json, os, re, shutil, subprocess, sys, threading, time, uuid
from pathlib import Path

import llm
import methodflow as MF
import extras as X
import recs as RC
import pdfio
import related as R_
import graph as GR
import finder as FD

APP = Path(__file__).resolve().parent
SRC = APP / "diagnosa.py"
PROJ = APP / "projects"
UPL = APP / "uploads"
PROJ.mkdir(exist_ok=True)
UPL.mkdir(exist_ok=True)

# ---------- load diagnosa.py as it is ----------
_src = SRC.read_text(encoding="utf-8")
_src = re.sub(r"\nmain\(\)\s*$", "\n", _src)
D = {"__name__": "diagnose_reuse", "__file__": str(SRC)}
exec(compile(_src, str(SRC), "exec"), D)
MODEL = D["MODEL"]
norm = D["norm"]
FIXED_NOTE = "This difference does not yet prove a cause."

# The app's own extraction prompt (replaces EXTRACTION from diagnosa.py without editing that file)
D["EXTRACTION"] = """From the paper (text split by page) extract ONLY the important parameters that determine the outcome, in three groups only: materials (name, purity/grade, concentration, composition), conditions (operating conditions: temperature, pressure, time, flow rate, pH, speed, ratios, etc.) and equipment (type or configuration and its dimensions). Also extract the key reported results (role "results") because they will be compared.
HARD RULES:
1. Do NOT include brands, manufacturers, suppliers, cities, countries, catalogue numbers or company names. Write the material or equipment type only (e.g. "VDF 99%", not the supplier).
2. Do NOT include parameters that are not reported. There is no 'not reported' list; return "not_reported":[].
3. One entry per parameter per condition. No duplicates: if the same value appears in several places, keep one entry with the best quote.
4. When there are two or more parameters of the same kind, DISTINGUISH them in the name by stating what differs, e.g. "Polymerization temperature" vs "Preheating temperature", or "Mn at 5 bar", "Mn at 10 bar". Never give two entries the same name.
5. Skip trivial details (generic lab equipment codes, washing procedures, safety notes, settings that do not influence the result).
6. Write parameter names and units in English. Keep the quote EXACTLY as in the paper.
Return valid JSON only: {"parameter":[{"name","value","unit","role"(materials|conditions|equipment|results),"quote"(EXACT copy from the text, <=200 characters),"page"(int)}],"not_reported":[]}.
Do not invent anything. Without an exact quote, do not include the item."""

DIAG_EXTRA = """
ADDITIONAL FORMAT (mandatory; it does not change the rules above):
- Every item in evidence_supports and evidence_weakens is an object {"label":"Paper|User data|AI guess","text":"...","page":int}. 'page' is required for the label Paper and must be a page of a verified parameter; without a verified page use the label AI guess.
- Every hypothesis, every test_plan item and every data_requested item has "title": a plain noun phrase of at most 8 words (no full sentence, no trailing period) that names it, e.g. "Operating pressure differs from the paper".
- Every hypothesis has "related_parameters":[names of parameters that are related to each other] (may be empty).
- Add at top level "eliminated":[{"statement","reason"}] when user_trials rules out a hypothesis from previous_report (may be empty).
- 'comparison_status' is computed by code; do not change it. Use user_trials and previous_report (when present) to re-rank the hypotheses.
- The hypothesis 'paper not reproducible or special conditions' is required. data_requested has at most 5 items.
- A result whose status is 'not_comparable' (e.g. Mn vs Mw vs 'molar mass', different unit or definition) must not be compared as a gap; never swap similar quantities.

- Add at top level "variables_controlled":[{"variables","reason","range","unit","how_to_control","related_hypotheses":[hypothesis rank numbers]}] (max 6): variables the researcher should control or scan that are NOT reported among the paper parameters but could explain the gap. "range" must be a suggested numeric range with unit (an AI estimate, never a paper value) or the allowed options. Never repeat a paper parameter. May be empty.

LANGUAGE AND ENUMS (mandatory): write every free-text value in clear English (statements, evidence text, reasons, test plan fields, data requests, limitations). Never mention JSON field names or code identifiers (for example user_results) in free text: say "your results" instead. Keep JSON keys EXACTLY as specified. Enum values stay in these exact tokens: gap_verdict.verdict must be exactly one of "real", "within normal variation", "cannot be assessed" (meaning: real / within normal variation / cannot be assessed); confidence one of "low", "medium", "high" (low/medium/high); label one of "Paper", "User data", "AI guess"; test_plan[].run_by must be exactly "user". Use "possibly" / "worth checking" instead of "the cause is". Write every [AI guess] claim in English and label it "AI guess". The fixed sentence for every hypothesis is "This difference does not yet prove a cause.".
Parameters whose user value has source="assumed equal to paper" were NOT confirmed by the user: treat them as assumptions, never as evidence that those conditions match.
"""

# ---------- Hermes calls (one at a time, cancellable) ----------
HERMES_LOCK = threading.Lock()
_proc_lock = threading.Lock()
_current = {"proc": None, "cancel": False}


class Cancelled(Exception):
    pass


def hermes_ask(system, user, timeout=420):
    prompt = system + "\n\nDo NOT use any tool. Reply with JSON only.\n\nINPUT:\n" + user
    if len(prompt) > 30000:
        raise RuntimeError(f"prompt is {len(prompt)} characters, over the 30000 limit")
    cfg = llm.current()
    if cfg["provider"] in ("anthropic", "openai"):
        return _ask_api(prompt, timeout)
    if cfg["provider"] != "hermes":
        raise RuntimeError("No model provider is set up yet. Click Settings in the top bar and add your API key (or choose Hermes sign-in).")
    exe = os.environ.get("HERMES_BIN") or shutil.which("hermes")
    if not exe:
        raise RuntimeError("The Hermes CLI was not found. Install Hermes and sign in, or choose an API key in Settings.")
    if _current["cancel"]:
        raise Cancelled()
    p = subprocess.Popen([exe, "-z", prompt, "-m", cfg["model"] or MODEL], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, encoding="utf-8", errors="replace")
    with _proc_lock:
        _current["proc"] = p
    out = {}

    def comm():
        out["o"], out["e"] = p.communicate()
    t = threading.Thread(target=comm, daemon=True)
    t.start()
    t0 = time.time()
    while t.is_alive():
        t.join(0.5)
        if _current["cancel"]:
            p.kill()
            raise Cancelled()
        if time.time() - t0 > timeout:
            p.kill()
            raise RuntimeError(f"hermes timed out after {timeout} seconds")
    with _proc_lock:
        _current["proc"] = None
    txt = out.get("o", "")
    m = re.search(r"\{.*\}", txt, re.S)
    if not m:
        raise RuntimeError("hermes did not return JSON: " + (txt + out.get("e", ""))[:400])
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise ValueError("invalid JSON: " + str(e))


def _ask_api(prompt, timeout):
    """API-key providers: run the HTTPS call in a thread so Cancel still works; parse the JSON reply."""
    if _current["cancel"]:
        raise Cancelled()
    res = {}

    def run():
        try:
            res["t"] = llm.complete(prompt, timeout)
        except Exception as e:  # noqa: BLE001
            res["e"] = e
    t = threading.Thread(target=run, daemon=True)
    t.start()
    while t.is_alive():
        t.join(0.5)
        if _current["cancel"]:
            raise Cancelled()
    if "e" in res:
        raise RuntimeError(str(res["e"]))
    m = re.search(r"\{.*\}", res["t"], re.S)
    if not m:
        raise RuntimeError("The model did not return JSON: " + res["t"][:300])
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise ValueError("invalid JSON: " + str(e)) from None


def test_model():
    """One tiny call to check the credentials. Returns (ok, message)."""
    try:
        r = hermes_ask('Reply with exactly this JSON and nothing else: {"ok": true}', "ping", timeout=60)
        return bool(r), "Connected."
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:400]


D["ask"] = hermes_ask  # extract_all() calls the global `ask` in the diagnose namespace

# ---------- storage ----------
SAVE_LOCK = threading.Lock()


def ppath(pid):
    if not re.fullmatch(r"[a-z0-9]{6,20}", pid):
        raise KeyError(pid)
    return PROJ / f"{pid}.json"


def load(pid):
    p = ppath(pid)
    if not p.exists():
        raise KeyError(pid)
    return json.loads(p.read_text(encoding="utf-8"))


def save(pr):
    pr["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with SAVE_LOCK:
        tmp = ppath(pr["id"]).with_suffix(".tmp")
        tmp.write_text(json.dumps(pr, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(ppath(pr["id"]))


def list_projects():
    res = []
    for f in sorted(PROJ.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            res.append({"id": d["id"], "name": d.get("name", d["id"]), "updated": d.get("updated", ""), "fictional": bool(d.get("fictional")),
                        "extracted": bool(d.get("extracted")), "diagnosed": bool(d.get("report"))})
        except Exception:
            pass
    return res


def new_project(name, data, filename):
    pid = uuid.uuid4().hex[:10]
    pending = []
    if filename.lower().endswith(".pdf"):
        f = UPL / f"{pid}.pdf"
        f.write_bytes(data)
        pgs, pend = pdfio.read_pdf(f)
        pending = [{"file": f.name, "i": i, "g": i, "prefix": ""} for i in pend]
    else:
        pgs = data.decode("utf-8", "replace").split("\f")
    pr = {"id": pid, "name": name, "filename": filename, "created": time.strftime("%Y-%m-%d %H:%M:%S"),
          "fictional": False, "kind": "experiment", "pages": pgs, "fields": [], "not_reported": [],
          "extracted": False, "compare": {}, "results": [], "sim": {}, "report": [], "trials": [],
          "ocr_pending": pending, "si": [],
          "warnings": [] if (pending or any(t.strip() for t in pgs)) else ["No readable text in this file."]}
    save(pr)
    return pr


def attach_si(pr, name, data):
    """Supplementary information: its pages are appended after the paper, each page starts with a label line."""
    k = len(pr.get("si") or []) + 1
    f = UPL / f"{pr['id']}_si{k}.pdf"
    f.write_bytes(data)
    pgs, pend = pdfio.read_pdf(f)
    pre = f"[Supplementary information: {name}]\n"
    g0 = len(pr["pages"])
    pr["pages"] = pr["pages"] + [pre + t for t in pgs]
    pr.setdefault("si", []).append({"name": name, "first": g0 + 1, "n": len(pgs)})
    pr.setdefault("ocr_pending", []).extend({"file": f.name, "i": i, "g": g0 + i, "prefix": pre} for i in pend)
    save(pr)
    return pr["si"][-1]


def ocr_stage(pr, job):
    """OCR pages without a text layer (about 35-60 s per page on this CPU). Failures are reported, not hidden."""
    pend = list(pr.get("ocr_pending") or [])
    if not pend:
        return
    job["estimate_seconds"] = len(pend) * 60 + 400
    left, err = [], None
    for k, e in enumerate(pend):
        if job.get("cancel"):
            raise Cancelled()
        job["stage"] = f"Reading scanned page {k + 1} of {len(pend)} with OCR (about 1 min per page)"
        job["progress"] = 0.0
        try:
            txt, _ = pdfio.ocr_pages(UPL / e["file"], [e["i"]])
            t = txt.get(e["i"], "")
            if len(t.strip()) > len(pr["pages"][e["g"]].strip()) - len(e.get("prefix", "")):
                pr["pages"][e["g"]] = e.get("prefix", "") + t
        except Exception as x:
            err = str(x)[:200]
            left = pend[k:]
            break
    cur = load(pr["id"])
    cur["pages"], cur["ocr_pending"] = pr["pages"], left
    if err:
        cur.setdefault("warnings", []).append("OCR stopped early: " + err)
    save(cur)
    pr.update({"pages": cur["pages"], "ocr_pending": left})


def lit_add(pr, name, data, doi="", source="upload"):
    lp = pr.setdefault("library", {"papers": [], "results": None})
    if len(lp["papers"]) >= 12:
        raise ValueError("At most 12 papers in the literature set")
    lid = "l" + str(max([int(p["id"][1:]) for p in lp["papers"]] + [0]) + 1)
    f = UPL / f"{pr['id']}_{lid}.pdf"
    f.write_bytes(data)
    pgs, pend = pdfio.read_pdf(f)
    (UPL / f"{pr['id']}_lit_{lid}.json").write_text(json.dumps(pgs, ensure_ascii=False), encoding="utf-8")
    meta = {"id": lid, "name": name[:120], "n_pages": len(pgs), "doi": doi, "source": source, "file": f.name, "ocr_pending": pend}
    lp["papers"].append(meta)
    save(pr)
    return meta


def lit_remove(pr, lid):
    lp = pr.get("library") or {"papers": []}
    for p in lp["papers"]:
        if p["id"] == lid:
            for fn in (UPL / p["file"], UPL / f"{pr['id']}_lit_{lid}.json"):
                if fn.exists():
                    fn.unlink()
    lp["papers"] = [p for p in lp["papers"] if p["id"] != lid]
    save(pr)


# ---------- units and comparison (pure code, no LLM) ----------
UNITS = {}


def _fam(fam, table):
    for u, v in table.items():
        UNITS[u.lower()] = (fam, v)


_fam("temperature", {"°c": (1, 273.15), "c": (1, 273.15), "degreesc": (1, 273.15), "degreec": (1, 273.15), "degreescelsius": (1, 273.15), "degreecelsius": (1, 273.15), "celsius": (1, 273.15), "degc": (1, 273.15), "k": (1, 0), "°k": (1, 0), "kelvin": (1, 0)})
_fam("pressure", {"pa": (1, 0), "kpa": (1e3, 0), "mpa": (1e6, 0), "bar": (1e5, 0), "atm": (101325, 0), "psi": (6894.757, 0)})
_fam("time", {"s": (1, 0), "sec": (1, 0), "seconds": (1, 0), "ms": (1e-3, 0), "min": (60, 0), "h": (3600, 0), "hr": (3600, 0)})
_fam("length", {"m": (1, 0), "cm": (1e-2, 0), "mm": (1e-3, 0), "µm": (1e-6, 0), "um": (1e-6, 0), "nm": (1e-9, 0)})
_fam("mass", {"g": (1, 0), "kg": (1e3, 0), "mg": (1e-3, 0)})
_fam("molar_mass", {"g/mol": (1, 0), "kg/mol": (1e3, 0), "da": (1, 0), "kda": (1e3, 0)})
_fam("concentration", {"g/l": (1, 0), "mg/l": (1e-3, 0), "kg/m3": (1, 0), "kg/m³": (1, 0), "g/ml": (1e3, 0)})
_fam("volume", {"l": (1, 0), "ml": (1e-3, 0)})
_fam("fraction", {"wt%": (1, 0), "%wt": (1, 0), "wt.%": (1, 0), "%b/b": (1, 0)})
_fam("fraction_not_specific", {"%": (1, 0)})


def unit_key(u):
    k = re.sub(r"\s+", "", str(u or "")).lower().replace("−", "-").replace("·", "").replace("⋅", "")
    m = re.fullmatch(r"([a-zµ°%]+?)([a-z]+)-1", k)  # "g mol-1" -> "g/mol"
    return f"{m.group(1)}/{m.group(2)}" if m else k


NUM = r"[-+]?\d+(?:[.,]\d+)?"


def parse_num(v):
    """float, (lo,hi) range, or None."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("\u2009", "").replace("\u00a0", "")
    s = re.sub(r"^[≈~<>≥≤]+\s*", "", s)
    s = s.replace("−", "-")

    def f(x):
        x = x.replace(" ", "")
        if re.fullmatch(r"\d{1,3}(,\d{3})+", x):
            x = x.replace(",", "")
        return float(x.replace(",", "."))
    ml = re.fullmatch(r"\d+(?:\.\d+)?(?:\s*,\s+\d+(?:\.\d+)?)+(?:\s*,?\s*(?:and)\s+\d+(?:\.\d+)?)?", s)
    if ml:  # list value "5, 10, 15" -> treated as the paper range min..max
        xs = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", s)]
        return (min(xs), max(xs))
    m = re.fullmatch(r"(" + NUM + r")\s*(?:–|—|to|-)\s*(" + NUM + r")", s)
    if m:
        a, b = f(m.group(1)), f(m.group(2))
        return (min(a, b), max(a, b))
    s2 = re.sub(r"(?<=\d) (?=\d{3}\b)", "", s)
    if re.fullmatch(NUM + r"|\d{1,3}(,\d{3})+", s2):
        try:
            return f(s2)
        except ValueError:
            return None
    return None


def to_base(x, unit):
    fam, (k, off) = UNITS[unit_key(unit)]
    return x * k + off


def convert(x, u_from, u_to):
    """Convert to u_to. Returns (value, None) or (None, reason)."""
    a, b = unit_key(u_from), unit_key(u_to)
    if a == b:
        return x, None
    if a in UNITS and b in UNITS and UNITS[a][0] == UNITS[b][0]:
        base = to_base(x, u_from)
        fam, (k, off) = UNITS[b]
        return (base - off) / k, None
    return None, f"units differ ({u_from or '-'} vs {u_to or '-'}) and the conversion is uncertain"


def parse_tol(t):
    """('rel', frac) or ('abs', x). Default 5%."""
    s = str(t if t not in (None, "") else "5%").strip()
    try:
        if s.endswith("%"):
            return "rel", float(s[:-1].replace(",", ".")) / 100
        return "abs", float(s.replace(",", "."))
    except ValueError:
        return "rel", 0.05


def cmp_numeric(pv, pu, uv, uu, tol):
    """Results: (status, reason)."""
    uconv, why = convert(uv if not isinstance(uv, tuple) else uv[0], uu, pu)
    if why:
        # an empty unit on both sides is already handled by a==b in convert
        return "not_comparable", why
    kind, t = parse_tol(tol)
    if isinstance(pv, tuple):
        if pv[0] <= uconv <= pv[1]:
            return "within_range", f"{uconv:g} is inside the paper range {pv[0]:g}–{pv[1]:g}"
        d = min(abs(uconv - pv[0]), abs(uconv - pv[1]))
        return "different", f"{uconv:g} is outside the paper range {pv[0]:g}–{pv[1]:g} (gap {d:g})"
    diff = abs(uconv - pv)
    lim = abs(pv) * t if kind == "rel" else t
    rel = (diff / abs(pv) * 100) if pv else float("inf")
    if diff <= lim + 1e-12:
        return "match", f"difference {diff:g} ({rel:.1f}%) ≤ tolerance"
    return "different", f"difference {diff:g} ({rel:.1f}%) > tolerance"


def eligible(f):
    """May be used as evidence or for comparison: the quote was verified by code."""
    return bool(f.get("verified")) and f.get("status") in ("verified", "cited", "graph")


def has_value(v):
    return v is not None and str(v).strip() != ""


def user_dec(v):
    """User text: a single comma is a decimal ('0,165' = 0.165), not a thousands separator."""
    s = str(v).strip() if v is not None else ""
    return s.replace(",", ".") if re.fullmatch(r"[-+]?\d+,\d+", s) else v


def compare_param(f, u):
    u = u or {}
    if f.get("status") == "not_reported" or not has_value(f.get("value")):
        return "not_yet_known", "the paper does not report this value"
    if not eligible(f):
        return "not_yet_known", "paper value is not verified, so it is not used for comparison"
    if not has_value(u.get("value")):
        return "not_yet_known", "no value entered for your experiment"
    pv, uv = parse_num(f["value"]), parse_num(user_dec(u["value"]))
    if pv is not None and uv is not None and not isinstance(uv, tuple):
        return cmp_numeric(pv, f.get("unit"), uv, u.get("unit"), u.get("tol"))
    if (pv is None) != (uv is None):
        return "not_comparable", "one side is numeric, the other is text"
    ta = set(re.findall(r"\w+", str(f["value"]).lower()))
    tb = set(re.findall(r"\w+", str(u["value"]).lower()))
    if ta and tb and (ta <= tb or tb <= ta):
        return "match", "same text, or one contains the other (punctuation ignored)"
    return "different", "text differs"


QCLASS = [("Mn", r"\bmn\b|number[\s-]*average"),
          ("Mw", r"\bmw\b|weight[\s-]*average"),
          ("Mz", r"\bmz\b|z[\s-]*average"),
          ("Mv", r"\bmv\b|viscosity[\s-]*average")]
AMBIG = r"molar\s*mass|molecular\s*weight|molecular\s*mass"


def qclass(text):
    t = str(text or "").lower()
    for name, rx in QCLASS:
        if re.search(rx, t):
            return name
    if re.search(AMBIG, t):
        return "ambiguous"
    return None


def result_value(h):
    """User value: a single value, or the mean of the last numeric column of a table/CSV."""
    if h.get("mode", "single") == "single":
        return parse_num(user_dec(h.get("value"))), None
    vals = []
    for row in h.get("table") or []:
        cells = row if isinstance(row, list) else str(row).split(",")
        for c in reversed(cells):
            n = parse_num(c)
            if isinstance(n, float):
                vals.append(n)
                break
    if not vals:
        return None, "table contains no numbers"
    return sum(vals) / len(vals), f"average of {len(vals)} rows"


def compare_result(h, fields):
    f = next((x for x in fields if x["id"] == h.get("paper_fid")), None)
    if not f:
        return "not_yet_known", "no paper result selected"
    if not has_value(f.get("value")) or not eligible(f):
        return "not_yet_known", "the paper result is not reported or not verified"
    uv, note = result_value(h)
    if uv is None:
        return "not_yet_known", note or "no result entered for your experiment"
    qp = qclass(f.get("name"))
    qu = qclass((h.get("name") or "") + " " + (h.get("definition") or ""))
    if qp == "ambiguous" or qu == "ambiguous":
        return "not_comparable", "'molar mass' without a type (Mn/Mw/Mz) is ambiguous; state its definition"
    if qp != qu:
        return "not_comparable", f"different quantities: paper={qp or 'unrecognised'}, yours={qu or 'unrecognised'}"
    note_def = "" if (h.get("definition") or "").strip() else " (quantity definition not filled in, check it yourself)"
    pv = parse_num(f["value"])
    if pv is None:
        return "not_yet_known", "paper value is not a number"
    st, why = cmp_numeric(pv, f.get("unit"), uv, h.get("unit"), h.get("tol") or "10%")
    unc = parse_num(h.get("uncertainty"))
    if st == "different" and isinstance(unc, float) and not isinstance(pv, tuple):
        uconv, _ = convert(uv, h.get("unit"), f.get("unit"))
        if uconv is not None and abs(uconv - pv) <= unc:
            st, why = "match", f"difference is within your uncertainty (±{unc:g})"
    return st, why + (f"; {note}" if note else "") + note_def


def compute_all(pr):
    fields = pr.get("fields", [])
    out = {"param": {}, "results": {}}
    for f in fields:
        if f.get("role") == "results":
            continue
        s, w = compare_param(f, (pr.get("compare") or {}).get(f["id"]))
        out["param"][f["id"]] = {"status": s, "reason": w}
    for h in pr.get("results", []):
        s, w = compare_result(h, fields)
        out["results"][h["id"]] = {"status": s, "reason": w}
    return out


# ---------- extraction: two passes ----------
def extract_twice(pr, job):
    pgs = pr["pages"]
    g, n = 1, 0
    cur = 0
    for t in pgs:
        if cur and n + len(t) > 20000:
            g += 1
            n = 0
            cur = 0
        cur += 1
        n += len(t)
    total = 2 * g + MF.count_calls(pgs) + 1  # +1: the pre-lab checklist call
    job["total_calls"] = total
    job["estimate_seconds"] = total * 100
    results = []
    for r in (1, 2):
        job["stage"] = f"Extraction pass {r} of 2 ({g} page groups)"
        try:
            ek = D["extract_all"](pgs)
        except Cancelled:
            raise
        except Exception as e:
            raise RuntimeError(f"Failed at stage: extraction pass {r}: {e}")
        ok, drop = D["verify"](ek, pgs)
        results.append((ok, drop, ek["not_reported"]))
        job["progress"] = r * g / total
    wtok = lambda x: {w for w in re.findall(r"\w+", str(x).lower()) if len(w) >= 2}

    def same(x, y):
        if x.get("page") != y.get("page") or norm(str(x.get("value", ""))) != norm(str(y.get("value", ""))):
            return False
        a_, b_ = wtok(x.get("name", "")), wtok(y.get("name", ""))
        return bool(a_ and b_) and len(a_ & b_) / min(len(a_), len(b_)) >= 0.5
    fields = []
    r1, r2 = results[0][0], results[1][0]
    used2 = set()
    for it in r1:
        j = next((k for k, y in enumerate(r2) if k not in used2 and same(it, y)), None)
        if j is not None:
            used2.add(j)
        fields.append(mkfield(it, True, once=j is None))
    for k, it in enumerate(r2):
        if k not in used2 and not any(same(it, y) for y in r1):
            fields.append(mkfield(it, True, once=True))
    seen = {(f["page"], norm(f["name"]), norm(f["value"])) for f in fields}
    for ok, drop, _ in results:
        for it in drop:
            k = (it.get("page"), norm(str(it.get("name", ""))), norm(str(it.get("value", ""))))
            if k not in seen:
                seen.add(k)
                fields.append(mkfield(it, False, once=False))
    fields = split_multi(fields)
    return clean_up(fields, pr.get("kind")), []


BRAND_NAME = re.compile(r"\b(brand|supplier|vendor|manufacturer|distributor|catalog|country|city|purchased from|source of purchase)\b", re.I)
CORP_SEG = re.compile(r"gmbh|\binc\b|\bltd\b|\bllc\b|\bco\.|\bcorp|\bag\b|s\.a\.|germany|sweden|netherlands|switzerland|swiss|france|italy|spain|singapore|\bb\.v\.|kgaa|company|international|\busa\b|japan|china|korea|sigma|aldrich|merck|alfa aesar|\btci\b|abcr|dupont|chemours|solvay|arkema|daikin|fisher|\bvwr\b|millipore|saudi", re.I)
ROLE_MAIN = {"materials", "conditions", "equipment", "results"}


SYNONYMS = {"materials": "material"}


def strip_brand(v, role=None):
    v = re.sub(r"\s*\([^)]*\)", lambda m: "" if CORP_SEG.search(m.group(0)) else m.group(0), str(v))
    if role == "equipment":  # trailing parenthesis holding a manufacturer name, e.g. (Knauer), (Biotech AB, Sweden)
        v = re.sub(r"\s*\((?=[A-Z])(?![A-Z0-9 .-]+\))[^)0-9]*\)\s*$", "", v)
    segs = [s for s in re.split(r",\s+", v) if s.strip() and not CORP_SEG.search(s)]
    return ", ".join(segs).strip()


def vsim(a, b):
    """Similar text value (cross-language: 6-letter stem) and same number -> treat as a duplicate entry."""
    na, nb = set(re.findall(r"\d+(?:\.\d+)?", str(a))), set(re.findall(r"\d+(?:\.\d+)?", str(b)))
    if na and nb and na != nb:
        return False
    ta = {w[:6] for w in re.findall(r"[a-z0-9]+", str(a).lower()) if len(w) >= 3}
    tb = {w[:6] for w in re.findall(r"[a-z0-9]+", str(b).lower()) if len(w) >= 3}
    return bool(ta and tb) and len(ta & tb) / min(len(ta), len(tb)) >= 0.5


def clean_up(fields, work_kind=None, keep_ids=()):
    """Only materials, operating conditions and equipment (+ key results); no brands; no 'not reported'; no duplicates; names made distinct."""
    wt = lambda x: {SYNONYMS.get(w, w) for w in re.findall(r"\w+", str(x).lower()) if len(w) >= 2}
    nv = lambda x: norm(re.sub(r"\s*\([^)]*\)", "", str(x)))
    for f in fields:  # idempotent: strip an old disambiguating suffix before recomputing
        if not f.get("edited") and not f.get("manual"):
            f["name"] = re.sub(r"\s*\((?:#\d+|p\. \d+)\)$", "", f["name"])
    out = []
    for f in fields:
        if f.get("edited") or f.get("manual"):
            out.append(f)
            continue
        keep = f["id"] in keep_ids
        if not keep:
            if f.get("status") == "not_reported" or not has_value(f.get("value")):
                continue
            if f.get("role") not in ROLE_MAIN and not (work_kind == "simulation" and f.get("role") in ("software", "numerical")):
                continue
            if BRAND_NAME.search(f.get("name", "")):
                continue
        f["value"] = strip_brand(f["value"], f.get("role")) or f["value"] if keep else strip_brand(f["value"], f.get("role"))
        if not f["value"] and not keep:
            continue
        out.append(f)
    kept = []
    for f in out:
        dup = None
        for k in kept:
            if k.get("edited") or f.get("edited"):
                continue
            if norm(k["name"]) == norm(f["name"]) and vsim(k["value"], f["value"]):
                dup = k
                break
            if (norm(k.get("unit", "")) == norm(f.get("unit", "")) or not k.get("unit") or not f.get("unit")) and (nv(k["value"]) == nv(f["value"]) or (len(nv(k["value"])) >= 4 and len(nv(f["value"])) >= 4 and (nv(k["value"]) in nv(f["value"]) or nv(f["value"]) in nv(k["value"])))):
                a, b = wt(k["name"]), wt(f["name"])
                if a and b and len(a & b) / min(len(a), len(b)) >= 0.5:
                    dup = k
                    break
        if dup is None:
            kept.append(f)
        else:
            if f["verified"] and not dup["verified"] or (f["verified"] == dup["verified"] and len(f["name"]) > len(dup["name"])):
                f["once"] = f.get("once") and dup.get("once")
                kept[kept.index(dup)] = f
            else:
                dup["once"] = dup.get("once") and f.get("once")
    # same name, different value -> disambiguate
    groups = {}
    for f in kept:
        groups.setdefault(norm(f["name"]), []).append(f)
    for g in groups.values():
        if len(g) < 2:
            continue
        pgs = [x.get("page") for x in g]
        for i, f in enumerate(g, 1):
            f["name"] = f"{f['name']} (p. {f['page']})" if len(set(pgs)) == len(pgs) and f.get("page") else f"{f['name']} (#{i})"
    return kept


def split_multi(fields):
    """'Mn at 5, 10, 15 bar' = '17000; 37000; 76000' -> one field per value (same quote, already verified)."""
    out = []
    for f in fields:
        vals = [v.strip() for v in re.split(r"[;]", f["value"]) if v.strip()]
        m = re.search(r"((?:\d+(?:\.\d+)?\s*,\s*)+\d+(?:\.\d+)?)\s*(\w+)", f["name"])
        nums = [x.strip() for x in m.group(1).split(",")] if m else []
        if len(vals) > 1 and len(vals) == len(nums) and f["verified"]:
            for n, v in zip(nums, vals):
                g = dict(f)
                g["id"] = "f" + uuid.uuid4().hex[:7]
                g["name"] = f["name"].replace(m.group(0), f"{n} {m.group(2)}")
                g["value"] = v
                out.append(g)
        else:
            out.append(f)
    return out


REF_RX = re.compile(r"supporting information|supplementary|\bSI\b|Table S\d|Fig(?:ure|\.)? ?S\d|see ref|previously reported|as described in ref", re.I)
ROLE = {"conditions", "materials", "equipment", "software", "numerical", "results"}


def mkfield(it, ok, once):
    st = "not_verified"
    if ok:
        st = "cited" if REF_RX.search(it.get("quote", "")) else "verified"
    role = it.get("role") if it.get("role") in ROLE else "conditions"
    return {"id": "f" + uuid.uuid4().hex[:7], "name": str(it.get("name", "")), "value": "" if it.get("value") is None else str(it.get("value")),
            "unit": it.get("unit") or "", "role": role, "quote": it.get("quote") or "", "page": it.get("page") if isinstance(it.get("page"), int) else None,
            "verified": bool(ok), "status": st, "edited": False, "once": bool(once)}


def verify_quote(pr, page, quote):
    try:
        h = int(page)
    except (TypeError, ValueError):
        return False
    pg = pr["pages"]
    return 1 <= h <= len(pg) and bool(quote) and norm(quote) in norm(pg[h - 1])


# ---------- diagnosis ----------
def build_input(pr, comp, max_chars=None):
    if max_chars is None:  # the whole prompt (instructions + input) must stay under 30000 characters
        max_chars = 29000 - len(D["DIAGNOSE"]) - len(DIAG_EXTRA) - 600
    fields = pr["fields"]
    ev = [f for f in fields if eligible(f)]
    def fld(f, q):
        d = {"name": f["name"], "value": f["value"], "unit": f["unit"], "role": f["role"], "page": f["page"]}
        if q:
            d["quote"] = f["quote"][:q]
        if f.get("status") != "verified":
            d["notes"] = {"cited": "refers to another source / SI", "graph": "read from a graph, approximate"}.get(f["status"], "")
        if f.get("once"):
            d["appears_once"] = True
        return d
    not_rep = [f["name"] for f in fields if f["status"] == "not_reported"]
    user_par, stat, assumption = {}, [], []
    for f in fields:
        if f.get("role") == "results":
            continue
        u = (pr.get("compare") or {}).get(f["id"]) or {}
        c = comp["param"].get(f["id"], {})
        if u.get("prefill") and c.get("status") in ("match", "within_range", None):
            assumption.append(f["name"])  # copied from the paper, not confirmed: only the name is sent
            continue
        if has_value(u.get("value")):
            user_par[f["name"]] = {"value": u["value"], "unit": u.get("unit", ""), "tolerance": u.get("tol") or "5%",
                                   "source": "assumed equal to paper" if u.get("prefill") else "entered or confirmed by the user"}
        stat.append({"name": f["name"], "paper": f["value"] if f["status"] != "not_reported" else None,
                     "user": u.get("value") if has_value(u.get("value")) else None, "status": STATUS_EN.get(c.get("status"), c.get("status")),
                     **({"notes": "assumed equal to paper, not confirmed by the user"} if u.get("prefill") else {})})
    results = []
    for h in pr.get("results", []):
        pf = next((x for x in fields if x["id"] == h.get("paper_fid")), None)
        uv, note = result_value(h)
        results.append({"name": h.get("name"), "user_value": uv, "unit": h.get("unit"), "replicates": h.get("replicates"),
                      "uncertainty": h.get("uncertainty"), "uncertainty_kind": h.get("uncertainty_kind"),
                      "quantity_definition": h.get("definition"), "paper_results": pf and {"name": pf["name"], "value": pf["value"], "unit": pf["unit"], "page": pf["page"]},
                      "comparison_status": STATUS_EN.get(comp["results"].get(h["id"], {}).get("status"), comp["results"].get(h["id"], {}).get("status")), "status_reason": comp["results"].get(h["id"], {}).get("reason")})
    prev = []
    if pr.get("report"):
        last = pr["report"][-1]["results"]
        prev = [{"rank": h["rank"], "group": h.get("group"), "statement": h["statement"]} for h in last.get("hypotheses", [])]
    for q in (200, 120, 60, 0, -1):
        payload = {"paper_parameters_verified": [fld(f, max(q, 0)) for f in (ev if q >= 0 else [x for x in ev if x.get("role") != "equipment" or x["id"] in {h.get("paper_fid") for h in pr.get("results", [])}])], "not_reported_in_paper": not_rep,
                   "user_data": {"kind": pr.get("kind"), "parameter": user_par, "parameters_assumed_equal_to_paper_unconfirmed": assumption, "user_results": results,
                                     "simulation_settings": pr.get("sim") if pr.get("kind") == "simulation" else None,
                                     "notes": ("FICTIONAL test data. " if pr.get("fictional") else "") + "Free-text description from the user (includes things not mapped to any parameter): " + " | ".join(m["text"] for m in pr.get("chat_user", []) if m.get("role") == "user")[:3000]},
                   "comparison_status": stat, "user_trials": pr.get("trials", []), "previous_report": prev}
        s = json.dumps(payload, ensure_ascii=False)
        if len(s) <= max_chars:
            return s
    raise RuntimeError(f"Diagnosis input is {len(s)} characters, too long even after trimming quotes and equipment details (limit {max_chars}). Remove unneeded parameters in step 1 (use Tidy up) and try again.")


LABELS = {"paper": "Paper", "data user": "User data", "guess ai": "AI guess"}


def norm_evidence(b):
    if isinstance(b, str):
        m = re.match(r"\s*\[([^\]]+)\]\s*(.*)", b, re.S)
        lab = LABELS.get(m.group(1).split(",")[0].strip().lower()[:13]) if m else None
        if m and not lab:
            for k, v in LABELS.items():
                if m.group(1).lower().startswith(k):
                    lab = v
        hm = re.search(r"(?:p\.?|page)\s*(\d+)", b, re.I)
        b = {"label": lab or "AI guess", "text": (m.group(2) if m else b), "page": int(hm.group(1)) if hm else None}
    lab = LABELS.get(str(b.get("label", "")).strip().lower(), "AI guess")
    h = b.get("page")
    return {"label": lab, "text": str(b.get("text", "")), "page": h if isinstance(h, int) else None}


BAN = re.compile(r"the\s+cause\s+is", re.I)
STATUS_EN = {"match": "match", "different": "different", "within_range": "within paper range", "not_comparable": "not comparable", "not_yet_known": "unknown"}


def ban_replace(o, cnt):
    if isinstance(o, str):
        n = len(BAN.findall(o))
        cnt[0] += n
        return BAN.sub("a possible cause is", o) if n else o
    if isinstance(o, list):
        return [ban_replace(x, cnt) for x in o]
    if isinstance(o, dict):
        return {k: ban_replace(v, cnt) for k, v in o.items()}
    return o


def validate_output(out, pr):
    """Return (clean_result, meta) or raise ValueError when rejected."""
    if not isinstance(out, dict):
        raise ValueError("output is not a JSON object")
    for k in ("gap_verdict", "hypotheses", "test_plan"):
        if k not in out:
            raise ValueError(f"key '{k}' is missing")
    ru = out["test_plan"]
    if not isinstance(ru, list) or not ru:
        raise ValueError("test_plan is empty")
    if any(not isinstance(s, dict) or s.get("run_by") != "user" for s in ru):
        raise ValueError('every test_plan step must have run_by="user"')
    v = out["gap_verdict"]
    vt = str(v.get("verdict", "")).lower() if isinstance(v, dict) else ""
    if "cannot" in vt or "not assess" in vt or "unassess" in vt or "undetermin" in vt:
        vk = "cannot_be_assessed"
    elif "normal" in vt or "within" in vt:
        vk = "normal"
    elif "real" in vt or "significant" in vt:
        vk = "real"
    else:
        raise ValueError("verdict not recognised: " + vt[:60])
    pages = {f["page"] for f in pr["fields"] if eligible(f) and isinstance(f.get("page"), int)}
    dropped = 0
    hips = []
    for i, h in enumerate(out["hypotheses"], 1):
        for k in ("evidence_supports", "evidence_weakens"):
            keep = []
            for b in h.get(k, []) or []:
                e = norm_evidence(b)
                if e["label"] == "Paper" and e["page"] not in pages:
                    dropped += 1
                    continue
                keep.append(e)
            h[k] = keep
        h["notes"] = FIXED_NOTE
        h["related_parameters"] = [str(x) for x in (h.get("related_parameters") or [])]
        h["rank"] = i
        hips.append(h)
    if not any(re.search(r"reproduc|special condition", str(h.get("statement", "")), re.I) for h in hips):
        hips.append({"rank": len(hips) + 1, "group": "paper not reproducible / special conditions",
                     "statement": "The paper may not be reproducible, or relies on special conditions that are not stated. This hypothesis is always included.",
                     "evidence_supports": [], "evidence_weakens": [], "confidence": "low",
                     "confidence_reason": "Fixed hypothesis; there is no direct evidence.", "notes": FIXED_NOTE, "related_parameters": [], "auto": True})
    out["hypotheses"] = hips
    limits = list(out.get("limitations") or [])
    if dropped:
        limits.append(f"{dropped} evidence item(s) labelled Paper were discarded by code because they have no verified page.")
    out["limitations"] = limits
    vd, vdrop = X.normalize_controls(out.get("variables_controlled"), pr)
    out["variables_controlled"] = vd
    if vdrop:
        out["limitations"].append(f"{vdrop} suggested control variable(s) were dropped by code because the paper already covers them.")
    out["data_requested"] = (out.get("data_requested") or [])[:5]
    out["eliminated"] = out.get("eliminated") or []
    out["verdict_code"] = vk
    cnt = [0]
    out = ensure_titles(ban_replace(out, cnt))
    return out, {"paper_evidence_dropped": dropped, "forbidden_phrases_replaced": cnt[0]}


def short_title(text, n=8):
    """Fallback title: first clause of a sentence, at most n words."""
    t = re.sub(r"^(possibly|perhaps|it is possible that|the|your|a|an)\s+", "", re.sub(r"\s+", " ", str(text or "")).strip(), flags=re.I)
    t = re.split(r"[.;:(]|, (?:and|but|so|because|which)\b", t)[0].strip(" ,")
    w = t.split()
    return " ".join(w[:n]) + ("…" if len(w) > n else "") if w else ""


def ensure_titles(out):
    """Every hypothesis, test step and data request carries a short title (model-written, or derived by code)."""
    for key, src in (("hypotheses", "statement"), ("test_plan", "change"), ("data_requested", "item")):
        for x in out.get(key) or []:
            if isinstance(x, dict):
                t = str(x.get("title") or "").strip().rstrip(".")
                if t:
                    x["title"] = t[:90]
                    x.pop("title_auto", None)
                else:
                    x["title"] = short_title(x.get(src) if not isinstance(x.get(src), (list, dict)) else json.dumps(x.get(src), ensure_ascii=False))
                    x["title_auto"] = True
    return out


def arrows(prev, cur):
    """Mark up/down/same/new; return the old hypotheses that disappeared."""
    if not prev:
        for h in cur["hypotheses"]:
            h["shift"] = {"direction": "new", "delta": 0}
        return []
    used = set()
    for h in cur["hypotheses"]:
        best, sc = None, 0.0
        for j, p in enumerate(prev["hypotheses"]):
            if j in used:
                continue
            r = difflib.SequenceMatcher(None, h["statement"].lower(), p["statement"].lower()).ratio()
            if p.get("group") == h.get("group"):
                r += 0.15
            if r > sc:
                best, sc = j, r
        if best is not None and sc >= 0.5:
            used.add(best)
            d = prev["hypotheses"][best]["rank"] - h["rank"]
            h["shift"] = {"direction": "up" if d > 0 else "down" if d < 0 else "same", "delta": abs(d), "from": prev["hypotheses"][best]["rank"]}
        else:
            h["shift"] = {"direction": "new", "delta": 0}
    gone = [{"statement": p["statement"], "old_rank": p["rank"], "reason": "no longer appears in this version"}
            for j, p in enumerate(prev["hypotheses"]) if j not in used]
    return gone


def run_diagnosis(pr, job):
    comp = compute_all(pr)
    job["stage"] = "Preparing diagnosis input"
    payload = build_input(pr, comp)
    job["estimate_seconds"] = 150
    job["stage"] = "Model is writing the diagnosis (1–3 min)"
    correction = ""
    last_err = None
    for trials in (1, 2):
        try:
            out = hermes_ask(D["DIAGNOSE"] + DIAG_EXTRA + correction, payload)
            results, meta = validate_output(out, pr)
            try:
                X.fill_titles(results, hermes_ask)
            except Cancelled:
                raise
            except Exception:  # titles are cosmetic: keep the code-derived ones
                pass
            break
        except Cancelled:
            raise
        except ValueError as e:
            last_err = e
            correction = f"\n\nCORRECTION: your previous output was rejected: {e}. Fix it and reply with valid JSON only, following the schema; every test_plan step must have run_by=\"user\"."
            job["stage"] = "Retrying diagnosis (previous output was rejected)"
            job["progress"] = 0.5
    else:
        raise RuntimeError(f"Failed at stage: validating the diagnosis output (after 1 retry): {last_err}")
    prev = pr["report"][-1]["results"] if pr.get("report") else None
    gone = arrows(prev, results)
    limits_model = results.get("eliminated") or []
    results["eliminated"] = limits_model + [g for g in gone if not any(g["statement"][:30] == (k.get("statement") or "")[:30] for k in limits_model)]
    ver = len(pr.get("report", [])) + 1
    pr.setdefault("report", []).append({"ver": ver, "time": time.strftime("%Y-%m-%d %H:%M:%S"), "results": results, "meta": meta,
                                         "n_trials": len(pr.get("trials", [])), "comparison_status": comp})
    save(pr)
    return ver


# ---------- import user data (format of the sample file pvdf_user_fictional.json) ----------
def toks(s):
    return {w for w in re.findall(r"[\w]+", str(s).lower()) if len(w) >= 2}


def import_user(pr, d):
    pr["kind"] = d.get("kind", pr.get("kind", "experiment"))
    if "FICTIONAL" in str(d.get("notes", "")).upper():
        pr["fictional"] = True
    cmp_ = pr.setdefault("compare", {})
    for nm, v in (d.get("parameter") or {}).items():
        if not isinstance(v, dict):
            continue
        cand = [f for f in pr["fields"] if f.get("role") != "results"]
        f = next((x for x in cand if norm(x["name"]) == norm(nm)), None)
        if not f:
            sc = [(len(toks(x["name"]) & toks(nm)) / max(1, len(toks(nm))), x) for x in cand]
            sc = [s for s in sc if s[0] > 0.5]
            f = max(sc, key=lambda s: s[0])[1] if sc else None
        if not f:
            f = {"id": "f" + uuid.uuid4().hex[:7], "name": nm, "value": "", "unit": "", "role": "conditions", "quote": "", "page": None,
                 "verified": False, "status": "not_reported", "edited": False, "once": False}
            pr["fields"].append(f)
        cmp_[f["id"]] = {"value": v.get("value", ""), "unit": v.get("unit", ""), "tol": cmp_.get(f["id"], {}).get("tol", "5%")}
    pr["results"] = []
    res_fields = [f for f in pr["fields"] if f.get("role") == "results" and eligible(f)]
    for nm, v in (d.get("user_results") or {}).items():
        sc = [(len(toks(x["name"]) & toks(nm)) + (5 if qclass(x["name"]) and qclass(x["name"]) == qclass(nm) else 0), x) for x in res_fields]
        sc = sorted([s for s in sc if s[0] >= 1], key=lambda s: -s[0])
        top = [s[1] for s in sc if sc and s[0] == sc[0][0]]
        pf = top[0]["id"] if top and len({(x["name"], x["value"]) for x in top}) == 1 else None  # tie: leave empty so the user chooses
        pr["results"].append({"id": "h" + uuid.uuid4().hex[:7], "name": nm, "paper_fid": pf, "mode": "single", "value": v.get("value", ""),
                            "unit": v.get("unit", ""), "replicates": v.get("replicates", ""), "uncertainty": v.get("uncertainty") or "",
                            "uncertainty_kind": "measurement_error", "definition": v.get("definition", ""), "tol": "10%"})
    save(pr)
    return pr


# ---------- conversation: the user describes, the model maps, code validates ----------
PARSE_SYS = """You are a data-mapping assistant. The user describes, in free text (any language), the parameters and results of their experiment or simulation. Your ONLY job is to map that description onto the paper parameters and paper results provided. You do not diagnose and you run nothing.
RULES: (1) Use only numbers and text the user actually wrote; never invent, guess or convert values. (2) 'fid' must be one of the ids in ITEMS_PAPER; if there is no clear match, put the item in 'mismatch'. (3) Pair a result with paper_fid only if it is the same kind of quantity (Mn with Mn, Mw with Mw); never pair 'molar mass' without a type with Mn/Mw. If unsure, paper_fid=null. (4) 'definition' only if the user stated it (e.g. GPC method, which average). (5) Copy units exactly as the user wrote them. (6) If something important is unclear, add a short question in 'questions' (max 4). (7) current_mapping holds values already mapped (some may be copied from the paper as an assumption); change a parameter only if the user's text says it differs.
Return valid JSON only: {"kind":"experiment|simulation|null","parameter":[{"fid","value","unit"}],"results":[{"name","paper_fid","value","unit","replicates","uncertainty","uncertainty_kind":"measurement_error|between_seed|discretization","definition"}],"simulation":{"version","seed","mesh","conv_tol","code"},"mismatch":[{"name","value","unit"}],"questions":[],"reply":"1-3 sentence summary in English of what you mapped"}
Leave empty (null or "") whatever the user did not mention. Write 'reply' and 'questions' in English."""


def _num_candidates(text):
    c = set()
    for tok in re.findall(r"\d+(?:[.,]\d+)*", text):
        for v in {tok, tok.replace(",", "."), tok.replace(",", ""), tok.replace(".", "").replace(",", ".")}:
            try:
                c.add(round(float(v), 9))
            except ValueError:
                pass
    return c


def _in_text(v, text, cands):
    """The value must really appear in the user text (anti-fabrication)."""
    if v is None or str(v).strip() == "":
        return True
    s = str(v).strip()
    try:
        return round(float(s.replace(",", ".")), 9) in cands
    except ValueError:
        pass
    n = parse_num(s)
    if isinstance(n, float):
        return round(n, 9) in cands
    return norm(s) in norm(text) or all(w in text.lower() for w in re.findall(r"\w{4,}", s.lower())[:3])


def parse_user(pr, job):
    chat = pr.setdefault("chat_user", [])
    users = [m["text"] for m in chat if m["role"] == "user"]
    if not users:
        raise RuntimeError("No user message")
    alltext = "\n".join(users)
    fields = [f for f in pr["fields"] if eligible(f) and f.get("role") != "results"]
    res = [f for f in pr["fields"] if eligible(f) and f.get("role") == "results"]
    listing = [f"{f['id']}|{f['name']}|{f['value']} {f['unit']}" for f in fields]
    items_h = [f"{f['id']}|{f['name']}|{f['value']} {f['unit']}|p.{f['page']}" for f in res]
    current = {"parameter": {f["name"]: v for f in fields for v in [pr.get("compare", {}).get(f["id"])] if v and has_value(v.get("value")) and not v.get("prefill")},
                "results": [{"name": h.get("name"), "value": h.get("value"), "unit": h.get("unit")} for h in pr.get("results", [])]}
    payload = json.dumps({"ITEMS_PAPER": listing, "PAPER_RESULTS": items_h, "current_mapping": current,
                          "user_story_sequential": users}, ensure_ascii=False)
    job["stage"] = "Model is mapping your description to the paper parameters"
    job["estimate_seconds"] = 90
    out, err = None, None
    for t in (1, 2):
        try:
            out = hermes_ask(PARSE_SYS + (f"\n\nKOREKSI: {err}" if err else ""), payload)
            if not isinstance(out, dict):
                raise ValueError("output is not an object")
            break
        except Cancelled:
            raise
        except ValueError as e:
            err = str(e)
            out = None
    if out is None:
        raise RuntimeError(f"Failed at stage: mapping your description (invalid JSON): {err}")
    cands = _num_candidates(alltext)
    ids = {f["id"] for f in fields}
    rids = {f["id"] for f in res}
    rejected, n_used = [], 0
    cmp_ = pr.setdefault("compare", {})
    for p in out.get("parameter") or []:
        if not isinstance(p, dict) or p.get("fid") not in ids or not has_value(p.get("value")):
            continue
        if not _in_text(p.get("value"), alltext, cands):
            rejected.append(f"{p.get('fid')}: value {p.get('value')} is not in your text")
            continue
        old = cmp_.get(p["fid"], {})
        cmp_[p["fid"]] = {"value": str(p["value"]), "unit": str(p.get("unit") or ""), "tol": old.get("tol", "5%")}
        n_used += 1
    for x in out.get("mismatch") or []:
        if not isinstance(x, dict) or not x.get("name") or not _in_text(x.get("value"), alltext, cands):
            continue
        f = next((q for q in pr["fields"] if norm(q["name"]) == norm(str(x["name"])) and q.get("role") != "results"), None)
        if not f:
            f = {"id": "f" + uuid.uuid4().hex[:7], "name": str(x["name"]), "value": "", "unit": "", "role": "conditions", "quote": "", "page": None,
                 "verified": False, "status": "not_reported", "edited": False, "once": False}
            pr["fields"].append(f)
        cmp_[f["id"]] = {"value": str(x.get("value") or ""), "unit": str(x.get("unit") or ""), "tol": cmp_.get(f["id"], {}).get("tol", "5%")}
        n_used += 1
    results = pr.setdefault("results", [])
    for h in out.get("results") or []:
        if not isinstance(h, dict) or not h.get("name") or not has_value(h.get("value")):
            continue
        if not (_in_text(h.get("value"), alltext, cands) and _in_text(h.get("uncertainty"), alltext, cands) and _in_text(h.get("replicates"), alltext, cands)):
            rejected.append(f"result {h.get('name')}: numbers are not in your text")
            continue
        pf = h.get("paper_fid") if h.get("paper_fid") in rids else ""
        jk = h.get("uncertainty_kind") if h.get("uncertainty_kind") in ("measurement_error", "between_seed", "discretization") else "measurement_error"
        defn = str(h.get("definition") or "")
        if defn and norm(defn) not in norm(alltext) and not all(w in alltext.lower() for w in re.findall(r"\w{3,}", defn.lower())[:3]):
            defn = ""
        new = {"name": str(h["name"]), "paper_fid": pf, "mode": "single", "value": str(h["value"]), "unit": str(h.get("unit") or ""),
               "replicates": str(h.get("replicates") or ""), "uncertainty": str(h.get("uncertainty") or ""), "uncertainty_kind": jk, "definition": defn}
        ex = next((q for q in results if norm(q.get("name", "")) == norm(new["name"])), None)
        if ex:
            for k, v in new.items():
                if v != "":
                    ex[k] = v
        else:
            results.append({"id": "h" + uuid.uuid4().hex[:7], "tol": "10%", "table": [], **new})
        n_used += 1
    if out.get("kind") in ("experiment", "simulation"):
        pr["kind"] = out["kind"]
    sim = out.get("simulation")
    if isinstance(sim, dict):
        pr.setdefault("sim", {})
        for k in ("version", "seed", "mesh", "conv_tol", "code"):
            if sim.get(k) not in (None, "") and _in_text(sim[k], alltext, cands):
                pr["sim"][k] = str(sim[k])
    reply = str(out.get("reply") or "")
    if rejected:
        reply += "\n(Code discarded: " + "; ".join(rejected) + ")"
    chat.append({"role": "assistant", "text": reply, "questions": [str(q) for q in (out.get("questions") or [])][:4],
                 "mapped": n_used, "time": time.strftime("%Y-%m-%d %H:%M:%S")})
    save(pr)
    return n_used


def prefill_from_paper(pr, only_empty=True):
    """Start: the user experiment = the paper values (assumed). Rows not yet touched are marked prefill=True."""
    n = 0
    cmp_ = pr.setdefault("compare", {})
    for f in pr["fields"]:
        if f.get("role") == "results" or not eligible(f) or not has_value(f.get("value")):
            continue
        cur = cmp_.get(f["id"])
        if only_empty and cur and has_value(cur.get("value")):
            continue
        cmp_[f["id"]] = {"value": f["value"], "unit": f.get("unit", ""), "tol": (cur or {}).get("tol", "5%"), "prefill": True}
        n += 1
    return n


def build_method(pr, job, done=0, total=None):
    """Method flowchart (steps of the paper's experiment/simulation). Returns the method dict."""
    total = total or MF.count_calls(pr["pages"])
    n = [done]

    def ask(system, user):
        r = hermes_ask(system, user)
        n[0] += 1
        job["progress"] = min(0.99, n[0] / total)
        return r
    job["total_calls"] = total
    job["estimate_seconds"] = total * 100
    m = MF.build(pr["pages"], ask, job)
    m["built"] = time.strftime("%Y-%m-%d %H:%M:%S")
    return m


# ---------- job ----------
JOBS = {}


def capture_figures(pr):
    """Crop the Methods/Results figures out of the PDF (pymupdf in the OCR Python, no model). Keeps the old list on failure."""
    import shutil as _sh
    pdf = UPL / f"{pr['id']}.pdf"
    out = UPL / "figs" / pr["id"]
    if not pdf.exists():
        pr["figures"] = {"items": [], "error": "The original PDF is not stored, so figures cannot be captured.", "built": time.strftime("%Y-%m-%d %H:%M:%S")}
        return pr["figures"]
    if out.exists():
        _sh.rmtree(out, ignore_errors=True)
    try:
        p = subprocess.run([pdfio.aux_python(), str(APP / "figures.py"), str(pdf), str(out)], capture_output=True, text=True, timeout=240, env=pdfio.clean_env())
        data = json.loads((out / "figures.json").read_text(encoding="utf-8")) if (out / "figures.json").exists() else None
        if data is None:
            raise RuntimeError((p.stderr or p.stdout or "no output")[-200:])
        pr["figures"] = {"items": data["figures"], "error": "", "built": time.strftime("%Y-%m-%d %H:%M:%S")}
    except Exception as e:
        pr["figures"] = {"items": [], "error": f"{type(e).__name__}: {e}"[:240], "built": time.strftime("%Y-%m-%d %H:%M:%S")}
    return pr["figures"]


def start_job(kind, pid, extra=None):
    jid = uuid.uuid4().hex[:10]
    job = {"id": jid, "kind": kind, "pid": pid, "status": "queued", "stage": "Waiting in queue (one Hermes call at a time)",
           "progress": 0.0, "started": time.time(), "estimate_seconds": 150, "error": None, "results": None}
    JOBS[jid] = job

    def run():
        with HERMES_LOCK:
            _current["cancel"] = job.get("cancel", False)
            if job.get("cancel"):
                job["status"] = "cancel"
                return
            job["status"] = "running"
            job["started"] = time.time()
            try:
                pr = load(pid)
                if kind == "extract":
                    ocr_stage(pr, job)
                    pr = load(pid)
                    job["stage"] = "Capturing the Methods and Results figures"
                    capture_figures(pr)
                    save(pr)
                    pr = load(pid)
                    fields, not_rep = extract_twice(pr, job)
                    pr = load(pid)
                    pr["fields"], pr["not_reported"], pr["extracted"] = fields, not_rep, True
                    pr["compare"] = {}
                    prefill_from_paper(pr)
                    pr.pop("method_error", None)
                    save(pr)
                    job["results"] = {"n": len(fields)}
                    try:  # a method failure must not throw away the finished extraction
                        m = build_method(pr, job, done=job["total_calls"] - 1 - MF.count_calls(pr["pages"]), total=job["total_calls"])
                        pr = load(pid)
                        pr["method"] = m
                        save(pr)
                    except Cancelled:
                        raise
                    except Exception as e:
                        pr = load(pid)
                        pr["method_error"] = str(e)
                        save(pr)
                    try:  # the checklist is built from the method + parameters; its failure keeps everything else
                        job["stage"] = "Writing the pre-lab checklist"
                        pr = load(pid)
                        cl = X.build_checklist(pr, hermes_ask)
                        pr = load(pid)
                        pr["checklist"] = cl
                        pr.pop("checklist_error", None)
                        save(pr)
                    except Cancelled:
                        raise
                    except Exception as e:
                        pr = load(pid)
                        pr["checklist_error"] = str(e)
                        save(pr)
                    try:  # categories for the parameter list; failure only means the list stays ungrouped
                        job["stage"] = "Grouping the parameters by process step"
                        pr = load(pid)
                        pg_ = X.group_parameters(pr, hermes_ask)
                        pr = load(pid)
                        pr["param_groups"] = pg_
                        save(pr)
                    except Cancelled:
                        raise
                    except Exception as e:
                        pr = load(pid)
                        pr["param_groups"] = {"groups": [], "error": str(e)[:200]}
                        save(pr)
                elif kind == "groups":
                    job["estimate_seconds"] = 40
                    pr["param_groups"] = X.group_parameters(pr, hermes_ask)
                    save(pr)
                    job["results"] = {"n": len(pr["param_groups"]["groups"])}
                elif kind == "method":
                    m = build_method(pr, job)
                    pr = load(pid)
                    pr["method"] = m
                    pr.pop("method_error", None)
                    save(pr)
                    job["results"] = {"n": len(m["steps"])}
                elif kind == "recs":
                    job["estimate_seconds"] = 80
                    job["stage"] = "Writing one recommendation per differing parameter"
                    out_r = RC.build_recs(pr, hermes_ask, verify_quote, compute_all(pr)["param"])
                    pr = load(pid)
                    pr["variable_recs"] = out_r
                    pr.pop("recs_error", None)
                    save(pr)
                    job["results"] = {"n": len(out_r["items"]), "hidden": len(out_r["hidden"])}
                elif kind == "checklist":
                    job["estimate_seconds"] = 100
                    cl = X.build_checklist(pr, hermes_ask)
                    pr = load(pid)
                    pr["checklist"] = cl
                    pr.pop("checklist_error", None)
                    save(pr)
                    job["results"] = {"n": len(cl["controls_not_in_paper"])}
                elif kind == "ask":
                    job["estimate_seconds"] = 60
                    job["stage"] = "Reading the relevant pages and answering"
                    res = X.ask_paper(pr, extra["q"], hermes_ask, X.quote_ok_fn)
                    pr = load(pid)
                    pr.setdefault("ask_log", []).append({"q": extra["q"], "a": res, "time": time.strftime("%Y-%m-%d %H:%M:%S")})
                    save(pr)
                    job["results"] = {"ok": True}
                elif kind == "search":
                    job["estimate_seconds"] = 90
                    own = (extra or {}).get("q", "").strip()
                    rec = FD.find(pr, hermes_ask, job, own)
                    pr = load(pid)
                    if own:
                        pr["own_search"] = rec
                    else:
                        pr["recommendations"] = rec
                        pr.pop("recommendations_error", None)
                    save(pr)
                    job["results"] = {"n": len(rec["candidates"])}
                    try:  # the citation network is a bonus; failure only leaves it unbuilt
                        gr_ = GR.build(pr, job)
                        pr = load(pid)
                        pr["graph"] = gr_
                        pr.pop("graph_error", None)
                    except Cancelled:
                        raise
                    except Exception as e:  # noqa: BLE001
                        pr = load(pid)
                        pr["graph_error"] = str(e)[:300]
                    save(pr)
                elif kind == "graph":
                    job["estimate_seconds"] = 25
                    gr_ = GR.build(pr, job)
                    pr = load(pid)
                    pr["graph"] = gr_
                    pr.pop("graph_error", None)
                    save(pr)
                    job["results"] = gr_["stats"]
                elif kind == "relation":
                    job["estimate_seconds"] = 60
                    job["stage"] = "Reading the pages that report the result"
                    res = FD.relate(pr, extra["q"], hermes_ask, extra.get("ctx"))
                    pr = load(pid)
                    pr["relation_log"] = ([res] + (pr.get("relation_log") or []))[:10]
                    save(pr)
                    job["results"] = {"n": len(res["parameter"])}
                elif kind == "library":
                    nv = R_.analyse(pr, UPL, hermes_ask, job)
                    lp = pr["library"]
                    lp["results"]["ids"] = [p["id"] for p in lp["papers"]]
                    pr = load(pid)
                    pr["library"] = lp
                    save(pr)
                    job["results"] = {"n": nv}
                elif kind == "parse":
                    job["results"] = {"mapped": parse_user(pr, job)}
                else:
                    job["results"] = {"ver": run_diagnosis(pr, job)}
                    try:  # right after a diagnosis, look for papers that could help; a failure here must not undo the diagnosis
                        pr = load(pid)
                        rec = FD.find(pr, hermes_ask, job)
                        pr = load(pid)
                        pr["recommendations"] = rec
                        pr.pop("recommendations_error", None)
                        save(pr)
                    except Cancelled:
                        raise
                    except Exception as e:
                        pr = load(pid)
                        pr["recommendations_error"] = str(e)
                        save(pr)
                job["status"], job["progress"], job["stage"] = "done", 1.0, "Done"
            except Cancelled:
                job["status"], job["stage"] = "cancel", "Cancelled by the user"
            except Exception as e:
                job["status"], job["error"] = "failed", str(e)
            finally:
                _current["cancel"] = False
                job["done"] = time.time()
    threading.Thread(target=run, daemon=True).start()
    return job


def cancel_job(jid):
    job = JOBS.get(jid)
    if not job:
        raise KeyError(jid)
    job["cancel"] = True
    if job["status"] == "running":
        _current["cancel"] = True
        with _proc_lock:
            p = _current["proc"]
        if p:
            try:
                p.kill()
            except Exception:
                pass
    elif job["status"] == "queued":
        job["status"] = "cancel"
    return job
