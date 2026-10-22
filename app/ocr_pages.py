"""OCR selected PDF pages. Runs in system Python 3.11 (pymupdf + rapidocr-onnxruntime).
Usage: python ocr_pages.py <pdf> <comma-separated 0-based page indexes> <out.json>
Writes {"pages": {"<idx>": text}, "seconds": {"<idx>": s}}; progress per page goes to <out.json>.part."""
import json, sys, time
import pymupdf
from rapidocr_onnxruntime import RapidOCR

pdf, idxs, out = sys.argv[1], [int(x) for x in sys.argv[2].split(",") if x != ""], sys.argv[3]
eng = RapidOCR()
doc = pymupdf.open(pdf)
res, secs = {}, {}
for i in idxs:
    t0 = time.time()
    png = doc[i].get_pixmap(dpi=170).tobytes("png")
    r, _ = eng(png)
    lines = []
    if r:  # reading order: rows by y, then x
        r = sorted(r, key=lambda b: (round(b[0][0][1] / 15), b[0][0][0]))
        lines = [b[1] for b in r]
    res[str(i)] = "\n".join(lines)
    secs[str(i)] = round(time.time() - t0, 1)
    json.dump({"pages": res, "seconds": secs}, open(out + ".part", "w", encoding="utf-8"))
json.dump({"pages": res, "seconds": secs}, open(out, "w", encoding="utf-8"))
