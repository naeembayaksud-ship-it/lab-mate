#!/usr/bin/env python3
"""Start Lab Mate on any OS:  python run.py [--no-browser] [--install]
Needs Python 3.9+. Installs the one required package (pypdf) on first run; --install adds the optional ones (OCR, Figures)."""
import importlib.util, os, subprocess, sys, threading, time, webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP = ROOT / "app"
PORT = int(os.environ.get("LABMATE_PORT", "8650"))


def missing(mod):
    return importlib.util.find_spec(mod) is None


def main():
    if sys.version_info < (3, 9):
        sys.exit("Lab Mate needs Python 3.9 or newer.")
    if "--install" in sys.argv:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")])
    if missing("pypdf"):  # the only required package: install it automatically on first run
        print("First run: installing the required package pypdf ...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "pypdf>=4.0"])
        except (subprocess.CalledProcessError, OSError):
            sys.exit("Could not install pypdf. Run:  pip install pypdf   then start again.")
    for mod, what in (("pymupdf", "the Figures tab and scanned-PDF pages"), ("rapidocr_onnxruntime", "OCR of scanned PDFs")):
        if missing(mod):
            print(f"note: optional package '{mod}' not installed; {what} will not work (python run.py --install).")
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
