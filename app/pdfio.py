"""PDF text reading with an OCR fallback for scanned pages (also used for supplementary information)."""
import json, os, re, shutil, subprocess, sys, tempfile, time
from pathlib import Path

OCR_SCRIPT = Path(__file__).resolve().parent / "ocr_pages.py"
STOP = set("the of and in to a is was for with that this are were by on as at from be or an which der die das le la les et de".split())
MIN_CHARS = 80  # a page with less text than this is treated as an image


def clean_env():
    """Environment for the system Python 3.11 helpers: drop the host app's PYTHONPATH/PYTHONHOME, otherwise they load
    the wrong site-packages (e.g. a PIL built for another Python) and fail with 'cannot import name _imaging'."""
    return {k: v for k, v in os.environ.items() if k.upper() not in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP")}


def _garbled(text):
    w = re.findall(r"[a-zA-Z]+", text.lower())
    return len(w) > 150 and sum(1 for x in w if x in STOP) / len(w) < 0.03  # font-encoded text layer


def _find_aux_python():
    """Python used for OCR and figure cropping (needs pymupdf). Order: OCR_PYTHON, this interpreter, then python3/python on PATH."""
    for c in (os.environ.get("OCR_PYTHON"), sys.executable, shutil.which("python3"), shutil.which("python")):
        if not c or not os.path.exists(c):
            continue
        try:
            if subprocess.run([c, "-c", "import pymupdf"], capture_output=True, timeout=30, env=clean_env()).returncode == 0:
                return c
        except (OSError, subprocess.SubprocessError):
            continue
    return os.environ.get("OCR_PYTHON") or sys.executable


_AUX = []


def aux_python():
    if not _AUX:
        _AUX.append(_find_aux_python())
    return _AUX[0]


def ocr_pages(pdf, idxs, timeout=900):
    """Return ({idx: text}, {idx: seconds}). Raises RuntimeError if OCR is unavailable."""
    py = aux_python()
    if subprocess.run([py, "-c", "import pymupdf, rapidocr_onnxruntime"], capture_output=True, env=clean_env()).returncode != 0:
        raise RuntimeError("OCR is not installed (pip install rapidocr-onnxruntime pymupdf, or set OCR_PYTHON). Text PDFs still work.")
    tmpdir = tempfile.mkdtemp(dir=os.environ.get("TMPDIR"))
    out = os.path.join(tmpdir, "ocr.json")
    p = subprocess.run([py, str(OCR_SCRIPT), str(pdf), ",".join(map(str, idxs)), out],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, env=clean_env())
    if not os.path.exists(out):
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise RuntimeError("OCR failed: " + (p.stderr or p.stdout)[-300:])
    d = json.load(open(out, encoding="utf-8"))
    shutil.rmtree(tmpdir, ignore_errors=True)
    return {int(k): v for k, v in d["pages"].items()}, {int(k): v for k, v in d["seconds"].items()}


# Typographic ligatures break exact-quote checks (the model writes "fixed", the PDF text says "ﬁxed").
LIGATURES = str.maketrans({"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl"})


def read_pdf(path):
    """Fast text-layer read. Returns (pages, pending) where pending = 0-based indexes that need OCR."""
    from pypdf import PdfReader
    pages = [(pg.extract_text() or "").translate(LIGATURES) for pg in PdfReader(path).pages]
    pending = [i for i, t in enumerate(pages) if len(t.strip()) < MIN_CHARS]
    if _garbled(chr(10).join(pages)):  # unreadable text layer: OCR everything
        pending = list(range(len(pages)))
    return pages, pending


def ocr_fill(path, pages, pending, tick=None, cancelled=None):
    """OCR the pending pages one at a time (about 35-60 s per page on this CPU). Returns info dict."""
    info = {"ocr_pages": [], "ocr_seconds": 0.0, "ocr_error": None}
    t0 = time.time()
    for n, i in enumerate(pending):
        if cancelled and cancelled():
            break
        if tick:
            tick(n, len(pending))
        try:
            txt, _ = ocr_pages(path, [i])
        except Exception as e:
            info["ocr_error"] = str(e)[:300]
            break
        if len(txt.get(i, "").strip()) > len(pages[i].strip()):
            pages[i] = txt[i]
            info["ocr_pages"].append(i + 1)
    info["ocr_seconds"] = round(time.time() - t0, 1)
    return info
