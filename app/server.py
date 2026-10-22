"""Local server for Result Gap Diagnosis, port 8650.
Run: uv run --with pypdf python server.py   (HERMES_BIN is set to hermes.exe)"""
import gzip, json, os, re, shutil, sys, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import llm
import logic as L
import related as RL
import report_html as RH

PORT = int(os.environ.get("LABMATE_PORT", "8650"))
INDEX = L.APP / "index.html"


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, obj, ctype="application/json; charset=utf-8", filename=None):
        b = obj if isinstance(obj, bytes) else json.dumps(obj, ensure_ascii=False).encode("utf-8")
        gz = len(b) > 1400 and "gzip" in (self.headers.get("Accept-Encoding") or "")
        if gz:
            b = gzip.compress(b, 5)
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        if gz:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        if filename:
            self.send_header("Content-Disposition", 'attachment; filename="%s"' % filename)
        self.end_headers()
        self.wfile.write(b)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def jbody(self):
        b = self.body()
        return json.loads(b.decode("utf-8")) if b else {}

    ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}

    def guard(self, method):
        """Local-only app: refuse DNS-rebinding (foreign Host) and cross-site writes (foreign Origin)."""
        if (self.headers.get("Host") or "") not in self.ALLOWED_HOSTS:
            return "unexpected Host header"
        origin = self.headers.get("Origin")
        if method != "GET" and origin and origin.split("://", 1)[-1] not in self.ALLOWED_HOSTS:
            return "cross-origin request refused"
        if method != "GET" and self.headers.get("Sec-Fetch-Site") == "cross-site":
            return "cross-site request refused"
        return None

    ASSET_TYPES = {".woff2": "font/woff2", ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon"}

    def asset(self, name):
        """Static brand files: a flat allow-list (letters, digits, dash, dot) inside app/assets, no sub-folders, no traversal."""
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", name):
            return self.send(404, {"error": "not found"})
        rel = name.replace("fonts-", "fonts/", 1) if name.startswith("fonts-") else name
        p = (L.APP / "assets" / rel).resolve()
        ext = p.suffix.lower()
        if (L.APP / "assets").resolve() not in p.parents or ext not in self.ASSET_TYPES or not p.is_file():
            return self.send(404, {"error": "not found"})
        b = p.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", self.ASSET_TYPES[ext])
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "max-age=3600")
        self.end_headers()
        self.wfile.write(b)

    def route(self, method):
        bad = self.guard(method)
        if bad:
            return self.send(403, {"error": bad})
        u = urlparse(self.path)
        parts = [p for p in u.path.split("/") if p]
        q = parse_qs(u.query)
        if method == "GET" and not parts:
            return self.send(200, INDEX.read_bytes(), "text/html; charset=utf-8")
        if method == "GET" and len(parts) == 2 and parts[0] == "assets":
            return self.asset(parts[1])
        if not parts or parts[0] != "api":
            return self.send(404, {"error": "not found"})
        a = parts[1:]
        if a == ["projects"]:
            if method == "GET":
                return self.send(200, L.list_projects())
        if a == ["upload"] and method == "POST":
            name = q.get("name", ["paper.pdf"])[0]
            data = self.body()
            if not data:
                return self.send(400, {"error": "empty file"})
            try:
                pr = L.new_project(name.rsplit(".", 1)[0], data, name)
            except Exception as e:
                return self.send(400, {"error": f"Failed at stage: reading the PDF: {e}"})
            return self.send(200, pr)
        if a == ["cooccur"] and method == "POST":
            d = self.jbody()
            try:
                return self.send(200, RL.cooccur(d.get("terms") or [], d.get("context") or ""))
            except ValueError as e:
                return self.send(400, {"error": str(e)})
        if a == ["settings"] and method == "GET":
            return self.send(200, llm.public())
        if a == ["settings"] and method == "POST":
            try:
                llm.save(self.jbody())
            except ValueError as e:
                return self.send(400, {"error": str(e)})
            return self.send(200, llm.public())
        if a == ["settings", "test"] and method == "POST":
            with L.HERMES_LOCK:
                ok, msg = L.test_model()
            return self.send(200, {"ok": ok, "message": msg})
        if a == ["compare"] and method == "POST":
            return self.send(200, L.compute_all(self.jbody()))
        if len(a) >= 2 and a[0] == "job":
            job = L.JOBS.get(a[1])
            if not job:
                return self.send(404, {"error": "job not found"})
            if len(a) == 3 and a[2] == "cancel" and method == "POST":
                L.cancel_job(a[1])
            j = dict(job)
            j["elapsed_seconds"] = round(time.time() - job["started"], 1)
            return self.send(200, j)
        if len(a) >= 2 and a[0] == "projects":
            pid = a[1]
            try:
                pr = L.load(pid)
            except KeyError:
                return self.send(404, {"error": "project not found"})
            if len(a) == 2:
                if method == "GET":
                    return self.send(200, pr)
                if method == "PUT":
                    d = self.jbody()
                    for k in ("pages", "report", "id", "created", "chat_user", "method", "method_error", "checklist", "checklist_error", "ask_log", "library", "si", "ocr_pending", "recommendations", "recommendations_error", "own_search", "relation_log", "graph", "graph_error", "figures", "param_groups", "paper_map", "variable_recs", "recs_error"):
                        d.pop(k, None)
                    pr.update(d)
                    L.save(pr)
                    return self.send(200, {"ok": True, "updated": pr["updated"]})
                if method == "DELETE":
                    L.ppath(pid).unlink()
                    shutil.rmtree(L.UPL / "figs" / pid, ignore_errors=True)
                    return self.send(200, {"ok": True})
            sub = a[2] if len(a) > 2 else ""
            if sub == "fig" and len(a) == 4 and method == "GET":
                if not re.fullmatch(r"fig\d+_p\d+\.png", a[3]):
                    return self.send(404, {"error": "not found"})
                fp = L.UPL / "figs" / pid / a[3]
                if not fp.is_file():
                    return self.send(404, {"error": "not found"})
                return self.send(200, fp.read_bytes(), "image/png")
            if sub in ("report.html", "report.pdf") and method == "GET":
                if not pr.get("extracted"):
                    return self.send(400, {"error": "Read the paper first; there is nothing to save yet"})
                try:
                    page = RH.build(pr)
                    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", pr.get("name") or pid).strip("_")[:60] + "_" + time.strftime("%Y%m%d")
                    saved = L.APP / "saved"
                    saved.mkdir(exist_ok=True)
                    if sub == "report.html":
                        data, ctype = page.encode("utf-8"), "text/html; charset=utf-8"
                    else:
                        data, ctype = RH.build_pdf(page), "application/pdf"
                    (saved / (stem + sub[6:])).write_bytes(data)  # keep a copy on disk next to the app
                    return self.send(200, data, ctype, filename=stem + sub[6:])
                except Exception as e:  # noqa: BLE001
                    return self.send(500, {"error": str(e)[:300]})
            if sub == "groups" and method == "POST":
                if not pr.get("extracted"):
                    return self.send(400, {"error": "The paper has not been read yet"})
                return self.send(200, L.start_job("groups", pid))
            if sub == "figures" and method == "POST":
                L.capture_figures(pr)
                L.save(pr)
                return self.send(200, pr["figures"])
            if sub == "extract" and method == "POST":
                return self.send(200, L.start_job("extract", pid))
            if sub == "method" and method == "POST":
                return self.send(200, L.start_job("method", pid))
            if sub == "recs" and method == "POST":
                if not pr.get("extracted"):
                    return self.send(400, {"error": "The paper has not been read yet"})
                return self.send(200, L.start_job("recs", pid))
            if sub == "checklist" and method == "POST":
                return self.send(200, L.start_job("checklist", pid))
            if sub == "find" and method == "POST":
                if not pr.get("extracted"):
                    return self.send(400, {"error": "The paper has not been read yet"})
                return self.send(200, L.start_job("search", pid, {"q": (self.jbody().get("q") or "").strip()[:200]}))
            if sub == "graph" and method == "POST":
                if not (pr.get("recommendations") or pr.get("own_search")):
                    return self.send(400, {"error": "Run the paper search first"})
                return self.send(200, L.start_job("graph", pid))
            if sub == "relate" and method == "POST":
                bd_ = self.jbody()
                q_ = (bd_.get("q") or "").strip()
                if not q_:
                    return self.send(400, {"error": "empty question"})
                if not pr.get("extracted"):
                    return self.send(400, {"error": "The paper has not been read yet"})
                return self.send(200, L.start_job("relation", pid, {"q": q_[:600], "ctx": bd_.get("ctx")}))
            if sub == "ask" and method == "POST":
                q_ = (self.jbody().get("text") or "").strip()
                if not q_:
                    return self.send(400, {"error": "empty question"})
                if not pr.get("extracted"):
                    return self.send(400, {"error": "The paper has not been read yet"})
                return self.send(200, L.start_job("ask", pid, {"q": q_[:1500]}))
            if sub == "attach" and method == "POST":
                data = self.body()
                if not data:
                    return self.send(400, {"error": "empty file"})
                try:
                    return self.send(200, L.attach_si(pr, q.get("name", ["si.pdf"])[0], data))
                except Exception as e:
                    return self.send(400, {"error": f"Failed at stage: reading the supplementary PDF: {e}"})
            if sub == "lit":
                what = a[3] if len(a) > 3 else ""
                if what == "" and method == "POST":
                    data = self.body()
                    if not data:
                        return self.send(400, {"error": "empty file"})
                    try:
                        return self.send(200, L.lit_add(pr, q.get("name", ["paper.pdf"])[0].rsplit(".", 1)[0], data))
                    except Exception as e:
                        return self.send(400, {"error": f"Failed at stage: reading the PDF: {e}"})
                if what == "fetch" and method == "POST":
                    d = self.jbody()
                    try:
                        data = RL.fetch_pdf(d.get("url", ""))
                        return self.send(200, L.lit_add(pr, (d.get("name") or "paper")[:100], data, d.get("doi", ""), "open access"))
                    except Exception as e:
                        return self.send(400, {"error": f"Could not fetch the open-access PDF: {e}"})
                if what == "analyse" and method == "POST":
                    if len((pr.get("library") or {}).get("papers", [])) < 2:
                        return self.send(400, {"error": "Add at least 2 papers first"})
                    return self.send(200, L.start_job("library", pid))
                if len(a) == 4 and method == "DELETE":
                    L.lit_remove(pr, what)
                    return self.send(200, {"ok": True})
            if sub == "diagnose" and method == "POST":
                d = self.jbody()
                if d.get("trials"):
                    pr.setdefault("trials", []).append({**d["trials"], "time": time.strftime("%Y-%m-%d %H:%M:%S")})
                    L.save(pr)
                if not pr.get("extracted"):
                    return self.send(400, {"error": "Paper extraction has not been run yet"})
                return self.send(200, L.start_job("diagnose", pid))
            if sub == "chat" and method == "POST":
                page_text = (self.jbody().get("text") or "").strip()
                if not page_text:
                    return self.send(400, {"error": "empty message"})
                if not pr.get("extracted"):
                    return self.send(400, {"error": "Paper extraction has not been run yet"})
                pr.setdefault("chat_user", []).append({"role": "user", "text": page_text, "time": time.strftime("%Y-%m-%d %H:%M:%S")})
                L.save(pr)
                return self.send(200, L.start_job("parse", pid))
            if sub == "prefill" and method == "POST":
                n = L.prefill_from_paper(pr, only_empty=bool(self.jbody().get("only_empty", True)))
                L.save(pr)
                return self.send(200, {"filled": n})
            if sub == "confirm" and method == "POST":
                n = 0
                for v in (pr.get("compare") or {}).values():
                    if v.get("prefill"):
                        v.pop("prefill", None)
                        n += 1
                L.save(pr)
                return self.send(200, {"confirmed": n})
            if sub == "clean" and method == "POST":
                keep = set(pr.get("results") and [h.get("paper_fid") for h in pr["results"]] or []) | {k for k, v in (pr.get("compare") or {}).items() if L.has_value((v or {}).get("value"))}
                before = len(pr["fields"])
                pr["fields"] = L.clean_up(pr["fields"], pr.get("kind"), keep)
                ids = {f["id"] for f in pr["fields"]}
                pr["compare"] = {k: v for k, v in (pr.get("compare") or {}).items() if k in ids}
                L.save(pr)
                return self.send(200, {"before": before, "after": len(pr["fields"])})
            if sub == "verify" and method == "POST":
                d = self.jbody()
                return self.send(200, {"verified": L.verify_quote(pr, d.get("page"), d.get("quote"))})
            if sub == "import_user" and method == "POST":
                return self.send(200, L.import_user(pr, self.jbody()))
        return self.send(404, {"error": "unknown route"})

    def handle_any(self, m):
        try:
            self.route(m)
        except Exception as e:
            try:
                self.send(500, {"error": f"{type(e).__name__}: {e}"})
            except Exception:
                pass

    def do_GET(self): self.handle_any("GET")
    def do_POST(self): self.handle_any("POST")
    def do_PUT(self): self.handle_any("PUT")
    def do_DELETE(self): self.handle_any("DELETE")


if __name__ == "__main__":
    print(f"Lab Mate at http://127.0.0.1:{PORT}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
