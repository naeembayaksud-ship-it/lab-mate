"""Capture the figures of a paper that belong to Methods or Results.
Runs in the system Python (pymupdf). Usage: python figures.py <pdf> <out_dir>   -> writes PNGs and <out_dir>/figures.json
Each figure = images/vector drawings found next to a 'Fig. N' caption, cropped with the caption and rendered at 160 dpi.
Nothing is generated: if no picture is found next to a caption, that caption is skipped."""
import json, os, re, sys

import pymupdf

CAP = re.compile(r"^\s*(fig(?:ure)?\.?|scheme)\s*(\d+|S\d+)\s*[.:|\-–—]?\s*", re.I)
METH = re.compile(r"^\s*(?:\d+(?:\.\d+)*\.?\s*)?(experimental(?: section| details| methods?)?|materials?(?: and| &) methods?|methods?|methodology|"
                  r"simulation(?: details| methods?)?|numerical(?: methods?| model)?|model(?:ling|ing)?(?: and simulation)?|procedure|apparatus|"
                  r"computational(?: details| methods?)?|theory)\s*$", re.I)
RES = re.compile(r"^\s*(?:\d+(?:\.\d+)*\.?\s*)?(?:[a-z]+ )?(results?(?: and| &) discussion|results?|discussion|findings|validation|application)\s*$", re.I)
SUB = re.compile(r"^\s*(\d+\.\d+(?:\.\d+)?)\.?\s+([A-Z].{3,80})$")
SETUP = re.compile(r"\b(setup|set-up|apparatus|schematic|scheme|reactor|geometry|mesh|workflow|flow ?chart|diagram|experimental|procedure|sample|specimen|image of the|photograph)\b", re.I)
OTHER = re.compile(r"^\s*(?:\d+(?:\.\d+)*\.?\s*)?(abstract|introduction|background|conclusions?|references|acknowledg\w+|nomenclature|summary)\s*$", re.I)
MAXFIG = 24


def heading_lines(pg):
    """Short lines that look like headings: whole block is short, or the line is set in bold."""
    out = []
    for b in pg.get_text("dict")["blocks"]:
        for ln in b.get("lines", []):
            sp = ln["spans"]
            t = " ".join("".join(x["text"] for x in sp).split())
            if not (2 < len(t) < 90):
                continue
            bold = all((x["flags"] & 16) or "bold" in x["font"].lower() for x in sp if x["text"].strip())
            out.append((ln["bbox"][1], t, bold, len(b["lines"])))
    return out


def sections(doc):
    """Return {page_index: [(y, kind, title)]}: kind is a section (Methods/Results/Other) or 'sub' for a numbered subsection."""
    marks = {}
    for pi, pg in enumerate(doc):
        for y, t, bold, nl in heading_lines(pg):
            s = "Methods" if METH.match(t) else "Results" if RES.match(t) else "Other" if OTHER.match(t) else None
            if s and (bold or nl == 1 or t.isupper()):
                marks.setdefault(pi, []).append((y, s, t))
            elif SUB.match(t) and (bold or nl == 1) and not t.endswith("."):
                marks.setdefault(pi, []).append((y, "sub", t))
    return marks


def section_at(marks, pi, y, state="Unsorted"):
    cur, sub = state, ""
    for p in range(pi + 1):
        for yy, k, t in sorted(marks.get(p, [])):
            if p < pi or yy <= y:
                if k == "sub":
                    sub = t
                else:
                    cur, sub = k, ""
    return cur, sub


def visuals(pg):
    rects = []
    for im in pg.get_image_info():
        r = pymupdf.Rect(im["bbox"])
        if r.width > 60 and r.height > 40:
            rects.append(r)
    try:
        for r in pg.cluster_drawings():
            r = pymupdf.Rect(r)
            if r.width > 60 and r.height > 40 and r.get_area() > 4000:
                rects.append(r)
    except Exception:
        pass
    return rects


def main(pdf, out):
    os.makedirs(out, exist_ok=True)
    doc = pymupdf.open(pdf)
    marks = sections(doc)
    figs, seen = [], set()
    for pi, pg in enumerate(doc):
        vis = visuals(pg)
        caps = []
        for b in pg.get_text("blocks"):
            t = " ".join(b[4].split())
            m = CAP.match(t)
            if m and len(t) > 12:
                caps.append((pymupdf.Rect(b[:4]), m, t))
        caps.sort(key=lambda c: c[0].y0)
        for ci, (cr, m, text) in enumerate(caps):
            label = (m.group(1).lower().startswith("scheme") and "Scheme " or "Fig. ") + m.group(2)
            if label in seen:
                continue
            top_lim = caps[ci - 1][0].y1 if ci else pg.rect.y0
            # pictures above the caption (usual), else below it
            above = [r for r in vis if r.y1 <= cr.y0 + 8 and r.y0 >= top_lim - 4 and r.y1 > cr.y0 - 400]
            below = [r for r in vis if r.y0 >= cr.y1 - 8 and r.y0 < cr.y1 + 400 and (ci + 1 >= len(caps) or r.y1 <= caps[ci + 1][0].y0 + 8)]
            if cr.width < 0.55 * pg.rect.width and cr.height > 25:  # single-column figure: ignore pictures of the other column
                above = [r for r in above if r.x1 > cr.x0 - 20 and r.x0 < cr.x1 + 20]
                below = [r for r in below if r.x1 > cr.x0 - 20 and r.x0 < cr.x1 + 20]
            use = above or below
            if not use:
                continue
            u = pymupdf.Rect(use[0])
            for r in use[1:]:
                u |= r
            colmode = cr.width < 0.55 * pg.rect.width and cr.height > 25
            if colmode:
                u = pymupdf.Rect(max(u.x0, cr.x0 - 4), u.y0, min(u.x1, cr.x1 + 4), u.y1)
            clip = (u | cr)
            clip = pymupdf.Rect(max(pg.rect.x0, clip.x0 - (1 if colmode else 6)), max(pg.rect.y0, clip.y0 - 6), min(pg.rect.x1, clip.x1 + 6), min(pg.rect.y1, clip.y1 + 8))
            sec, sub = section_at(marks, pi, cr.y0)
            inferred = False
            if sec == "Unsorted":
                sec, inferred = ("Methods" if SETUP.search(text[:160]) else "Results"), True
            if sec == "Other":
                continue
            seen.add(label)
            fn = f"fig{len(figs) + 1}_p{pi + 1}.png"
            pg.get_pixmap(clip=clip, dpi=160).save(os.path.join(out, fn))
            figs.append({"id": f"g{len(figs) + 1}", "label": label, "page": pi + 1, "section": sec, "subsection": sub, "inferred": inferred, "caption": text[:420], "file": fn})
            if len(figs) >= MAXFIG:
                break
        if len(figs) >= MAXFIG:
            break
    json.dump({"figures": figs, "pages": len(doc)}, open(os.path.join(out, "figures.json"), "w", encoding="utf-8"), ensure_ascii=False)
    print(len(figs))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
