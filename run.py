#!/usr/bin/env python3
"""Start Lab Mate on any OS:  python run.py [--no-browser] [--install]

First run installs everything Lab Mate needs, by itself:
  - pypdf (required)
  - pymupdf, Pillow, rapidocr-onnxruntime (OCR for scanned PDFs and the Figures tab)
If this Python is too new for the OCR packages (3.13+), a small helper environment with Python 3.12 is created
in .labmate_aux/ (using py/python3.x if present, otherwise `uv`, which downloads Python 3.12 itself).
--install retries the optional packages after a failure. Needs Python 3.9+ and an internet connection on first run.
"""
import importlib.util, os, shutil, subprocess, sys, threading, time, webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP = ROOT / "app"
PORT = int(os.environ.get("LABMATE_PORT", "8650"))
AUX = ROOT / ".labmate_aux"
STAMP = ROOT / ".labmate_deps_checked"
REQUIRED = [("pypdf", "pypdf>=4.0")]
OPTIONAL = [("pymupdf", "pymupdf>=1.24"), ("PIL", "Pillow>=10.0"), ("rapidocr_onnxruntime", "rapidocr-onnxruntime>=1.3")]
OPT_SPECS = [s for _, s in OPTIONAL]


def missing(mod):
    return importlib.util.find_spec(mod) is None


def run(cmd, quiet=True):
    try:
        return subprocess.run(cmd, stdout=subprocess.DEVNULL if quiet else None, stderr=subprocess.DEVNULL if quiet else None).returncode == 0
    except OSError:
        return False


def aux_python():
    p = AUX / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return p if p.exists() else None


def aux_ready(py):
    return run([str(py), "-c", "import pymupdf, PIL, rapidocr_onnxruntime"])


def find_uv():
    uv = shutil.which("uv")
    if uv:
        return [uv]
    print("  installing the small tool 'uv' (needed to fetch a compatible Python) ...")
    if run([sys.executable, "-m", "pip", "install", "--quiet", "uv"]):
        return [sys.executable, "-m", "uv"]
    return None


def find_compatible_python():
    """A Python 3.10-3.12 already on this machine, or None."""
    cands = []
    if os.name == "nt" and shutil.which("py"):
        cands += [["py", "-3.12"], ["py", "-3.11"], ["py", "-3.10"]]
    cands += [[c] for c in ("python3.12", "python3.11", "python3.10") if shutil.which(c)]
    for c in cands:
        try:
            out = subprocess.run(c + ["-c", "import sys;print((3,10)<=sys.version_info[:2]<(3,13))"],
                                 capture_output=True, text=True, timeout=30).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            continue
        if out == "True":
            return c
    return None


def build_aux():
    """Create .labmate_aux with a compatible Python and the optional packages. Returns True on success."""
    print("Setting up the helper environment for OCR and figures (one time, may take a few minutes) ...")
    shutil.rmtree(AUX, ignore_errors=True)
    py = find_compatible_python()
    if py and run(py + ["-m", "venv", str(AUX)]) and aux_python():
        ok = run([str(aux_python()), "-m", "pip", "install", "--quiet"] + OPT_SPECS)
    else:
        uv = find_uv()
        if not uv:
            return False
        shutil.rmtree(AUX, ignore_errors=True)
        if not run(uv + ["venv", "--python", "3.12", str(AUX)]) or not aux_python():
            return False
        ok = run(uv + ["pip", "install", "--python", str(aux_python())] + OPT_SPECS)
    return ok and aux_ready(aux_python())


def setup_optional(force):
    py = aux_python()
    if py and aux_ready(py):
        os.environ.setdefault("OCR_PYTHON", str(py))
        return
    need = [(m, s) for m, s in OPTIONAL if missing(m)]
    if not need:
        return
    if STAMP.exists() and not force:
        print("note: OCR / Figures packages are not installed (an earlier attempt failed). Retry with: python run.py --install")
        return
    if sys.version_info < (3, 13):
        print("Installing OCR and figure packages ...")
        run([sys.executable, "-m", "pip", "install", "--quiet"] + [s for _, s in need], quiet=False)
        need = [(m, s) for m, s in OPTIONAL if missing(m)]
    if need:
        if build_aux():
            os.environ["OCR_PYTHON"] = str(aux_python())
            print("OCR and figures are ready (helper environment).")
        else:
            print("note: could not install the OCR / Figures packages. Everything else works. To fix, install Python 3.12 and run:\n"
                  "      python3.12 run.py --install")
    STAMP.write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")


def check_pdf_browser():
    exe = os.environ.get("PDF_BROWSER")
    if exe and os.path.exists(exe):
        return
    names = ("msedge", "microsoft-edge", "google-chrome", "chrome", "chromium", "chromium-browser")
    fixed = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
             r"C:\Program Files\Google\Chrome\Application\chrome.exe", r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
             "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
    if any(shutil.which(n) for n in names) or any(os.path.exists(p) for p in fixed):
        return
    print("note: no Edge/Chrome found, so 'Save PDF' will not work (install one, or set PDF_BROWSER). Save HTML still works.")


def main():
    if sys.version_info < (3, 9):
        sys.exit("Lab Mate needs Python 3.9 or newer.")
    force = "--install" in sys.argv
    for mod, spec in REQUIRED:
        if missing(mod):
            print(f"First run: installing {spec} ...")
            if not run([sys.executable, "-m", "pip", "install", "--quiet", spec]):
                sys.exit(f"Could not install {spec}. Check your internet connection, or run:  pip install {spec}")
    setup_optional(force)
    check_pdf_browser()
    os.chdir(APP)
    sys.path.insert(0, str(APP))
    import server  # noqa: E402
    url = f"http://127.0.0.1:{PORT}"
    if "--no-browser" not in sys.argv:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    print(f"Lab Mate is running at {url}  (Ctrl+C to stop)")
    print("First time: click Settings (top bar) and add your own API key, or choose Hermes sign-in.")
    try:
        server.ThreadingHTTPServer(("127.0.0.1", server.PORT), server.H).serve_forever()
    except KeyboardInterrupt:
        pass
    except OSError as e:
        sys.exit(f"Could not start on port {PORT}: {e}. Another program may be using it; set LABMATE_PORT.")


if __name__ == "__main__":
    main()
