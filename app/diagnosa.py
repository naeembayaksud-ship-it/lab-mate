"""Result gap diagnosis: extraction (quotes verified by code) -> diagnosis.
Usage: uv run --with pypdf python diagnosa.py paper.(pdf|txt) user.json [--model sonnet]
user.json: {"kind":"experiment|simulation","parameter":{name:{"value":..,"unit":..}|null},
  "user_results":{...}, "notes":".."}
Runs no simulation. Claude is called through the Hermes CLI (`hermes -z`, Anthropic OAuth, no tools).
Note: JSON keys and enum tokens (value, unit, role, quote, page, ...) are the stored data schema and stay as they are."""
import json, os, re, shutil, subprocess, sys
from pathlib import Path

MODEL = sys.argv[sys.argv.index("--model") + 1] if "--model" in sys.argv else "claude-sonnet-5-5"

EXTRACTION = """Extract from the paper (text split by page) every parameter, material, equipment item, software, numerical setting, condition and reported result.
Return valid JSON only: {"parameter":[{"name","value","unit","role"(conditions|materials|equipment|software|numerical|results),"quote"(EXACT copy from the text, <=200 characters),"page"(int)}],"not_reported":[names of important parameters that are absent]}.
Do not invent anything. Without an exact quote, do not include the item."""

DIAGNOSE = """You are an assistant that diagnoses gaps between experiment/simulation results and a paper. You narrow the problem down; you do not establish causes.
ROLE LIMITS: you run no software. Every test step, including simulations, is only a recommendation that the user runs. Never write result numbers the user has not produced. Every effect direction must be labelled [AI guess] (AI guess, unverified).
LABELS: [Paper] only from verified parameters (include the page); [User data] (user data) from the user's input; [AI guess] for every inference or estimate, marked unverified. Never invent values, quotes or references. Never guess a value that is 'not reported'.
STEPS: 1) Verdict on the gap: real (real) / within normal variation (within normal variation) / cannot be assessed (cannot be assessed). Compare with uncertainty and replicates; for simulations consider seed, discretisation and sampling. If the verdict is not 'real', say so at the start. 2) Status of each parameter: match (match), different (different), within range (within range), not comparable (not comparable), not reported, not measured. 3) At most 7 hypotheses, from: differing parameters; not reported / not measured parameters; hidden factors (experiment: calibration, material variation, sample history, procedure order, data analysis; simulation: software version/defaults, units, solver/tolerances, mesh/time step, seed, initial/boundary conditions, post-processing); random variation; paper not reproducible or special conditions (REQUIRED, as its own hypothesis). 4) Ranking: relative gap, strength of reasoning, cheapness, informativeness. Without a scale relationship from the paper or the user, write 'not an effect estimate'. Mark parameters that are related to each other. 5) For each hypothesis: supporting evidence (labelled), weakening evidence or none, confidence low|medium|high (low|medium|high) with a reason, and the fixed note 'This difference does not yet prove a cause.' 6) Test plan from cheap to expensive. Simulation: repeat the paper's exact configuration, change the seed, refine mesh/time step, and only then change parameters. Experiment: reference sample, repeat the paper's exact conditions, and only then change parameters. Change one variable per step. 7) If everything matches but the result differs, trace hidden factors and request at most 5 extra data items.
OUTPUT valid JSON only: {"gap_verdict":{"verdict","basis"},"parameter_summary":[{"name","paper","user","status"}],"hypotheses":[{"rank","group","statement","evidence_supports":[],"evidence_weakens":[],"confidence","confidence_reason","notes"}],"test_plan":[{"order","related_hypotheses","change","hold","measure","min_replicates","supports_if","weakens_if","cost","time","safety","run_by":"user","output_recorded":[]}],"data_requested":[{"item","reason"}],"limitations":[]}
Never write 'the cause is'; use 'possibly' or 'worth checking'. Do not add hypotheses just to be complete. Write in clear English, concisely."""


def ask(system, user):
    # Hermes one-shot (Hermes's own Anthropic OAuth). The Windows command line limit is about 32k characters.
    prompt = system + "\n\nDo NOT use any tool. Reply with JSON only.\n\nINPUT:\n" + user
    if len(prompt) > 30000:
        sys.exit("prompt is over 30k characters: split the paper into parts first")
    r = subprocess.run([os.environ.get("HERMES_BIN") or shutil.which("hermes"), "-z", prompt, "-m", MODEL],
                       capture_output=True, text=True, encoding="utf-8", timeout=280)
    m = re.search(r"\{.*\}", r.stdout, re.S)
    if not m:
        sys.exit("hermes failed: " + (r.stdout + r.stderr)[:500])
    return json.loads(m.group(0))


def pages(path):
    p = Path(path)
    if p.suffix.lower() == ".pdf":
        from pypdf import PdfReader
        return [pg.extract_text() or "" for pg in PdfReader(p).pages]
    return p.read_text(encoding="utf-8").split("\f")


def norm(s):
    return re.sub(r"\s+", "", s).lower()


def verify(extract, pgs):
    ok, drop = [], []
    for it in extract["parameter"]:
        h = it.get("page")
        valid = isinstance(h, int) and 1 <= h <= len(pgs) and it.get("quote") and norm(it["quote"]) in norm(pgs[h - 1])
        (ok if valid else drop).append({**it, "verified": bool(valid)})
    return ok, drop


def extract_all(pgs):
    # split into page groups of <=20k characters and merge the results
    groups, cur, n = [], [], 0
    for i, t in enumerate(pgs):
        if cur and n + len(t) > 20000:
            groups.append(cur); cur, n = [], 0
        cur.append(i); n += len(t)
    groups.append(cur)
    results = {"parameter": [], "not_reported": []}
    for g in groups:
        page_text = "\n".join(f"[PAGE {i+1}]\n{pgs[i]}" for i in g)
        e = ask(EXTRACTION, page_text)
        results["parameter"] += e.get("parameter", [])
        results["not_reported"] += e.get("not_reported", [])
    return results


def main():
    pgs = pages(sys.argv[1])
    ek = extract_all(pgs)
    ok, drop = verify(ek, pgs)
    print(f"Extraction: {len(ok)} verified, {len(drop)} discarded (quote does not match)", file=sys.stderr)
    if "--extract-only" in sys.argv:
        print(json.dumps({"parameter": ok, "dropped": drop, "not_reported": ek["not_reported"]}, ensure_ascii=False, indent=1))
        return
    user = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    # "not reported" from a text chunk can be wrong: drop entries whose keywords appear in a verified parameter
    common = {"concentration", "instrument", "method", "result", "results", "condition", "conditions", "analysis", "software", "model", "brand", "the", "and"}
    tok = lambda s: {w for w in re.findall(r"[\w\-]+", s.lower()) if len(w) >= 3 and w not in common}
    known = set().union(*(tok(p["name"]) for p in ok))
    missing = sorted({x for x in ek["not_reported"] if not (tok(x) & known)})
    payload = {"paper_parameters_verified": ok, "not_reported_in_paper": missing, "user_data": user}
    out = ask(DIAGNOSE, json.dumps(payload, ensure_ascii=False))
    out["test_plan"] = [s for s in out.get("test_plan", []) if s.get("run_by") == "user"]
    out["_quote_dropped"] = [b.get("name") for b in drop]
    print(json.dumps(out, ensure_ascii=False, indent=1))


main()
