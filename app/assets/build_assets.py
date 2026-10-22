"""Generates the brand SVGs (waves motif, favicon mark, inline logo) into app/assets. Run: python build_assets.py"""
import math, pathlib
OUT = pathlib.Path(__file__).parent

def curve(i, n):
    t = i / (n - 1)
    base = 14 + 86 * t ** 1.25                      # vertical position of the line at the left edge
    amp = 34 * (1 - t) ** 0.9 + 6                   # the ridge fades toward the bottom lines
    pts = []
    for k in range(13):
        x = k * 100 / 12
        u = x / 100
        ridge = math.exp(-((u - 0.42) / 0.26) ** 2)
        dip = 0.35 * math.exp(-((u - 0.88) / 0.12) ** 2)
        y = base - amp * ridge * (0.55 + 0.45 * (1 - t)) + dip * 5 * (1 - t) - 12 * (1 - u) * (1 - t) ** 2
        pts.append((x, y))
    d = f"M{pts[0][0]:.1f} {pts[0][1]:.1f}"
    for a in range(len(pts) - 1):                   # Catmull-Rom -> cubic Bezier
        p0, p1, p2, p3 = pts[max(a - 1, 0)], pts[a], pts[a + 1], pts[min(a + 2, len(pts) - 1)]
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        d += f"C{c1[0]:.1f} {c1[1]:.1f} {c2[0]:.1f} {c2[1]:.1f} {p2[0]:.1f} {p2[1]:.1f}"
    return d

N = 13
paths = [curve(i, N) for i in range(N)]
waves = "".join(f'<path d="{d}"/>' for d in paths)

(OUT / "waves.svg").write_text(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" fill="none" stroke="#0A1526" stroke-width="1.2" '
    f'stroke-linecap="round" stroke-linejoin="round" preserveAspectRatio="xMidYMid slice">{waves}</svg>', encoding="utf-8")

mark = ('<rect width="100" height="100" fill="#001639"/><g fill="none" stroke="#fff" stroke-width="2.6" stroke-linecap="round" '
        f'stroke-linejoin="round" clip-path="url(#c)">{waves}</g>')
(OUT / "favicon.svg").write_text(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><defs><clipPath id="c"><rect width="100" height="100"/></clipPath></defs>'
    + mark + '</svg>', encoding="utf-8")

# full logo for dark surfaces: the text uses currentColor so it follows the theme ink
logo = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 230 100" role="img" aria-label="Lab Mate"><defs><clipPath id="c"><rect width="100" height="100"/></clipPath></defs>'
        + mark + '<rect x="110" y="2.5" width="117.5" height="95" fill="none" stroke="currentColor" stroke-width="5"/>'
        '<text x="124" y="46" font-family="Plus Jakarta Sans,Montserrat,Inter,system-ui,sans-serif" font-weight="700" font-size="34" fill="currentColor">LAB</text>'
        '<text x="124" y="82" font-family="Plus Jakarta Sans,Montserrat,Inter,system-ui,sans-serif" font-weight="300" font-size="34" fill="currentColor">MATE</text></svg>')
(OUT / "lab-mate-logo.svg").write_text(logo, encoding="utf-8")
print({p.name: p.stat().st_size for p in OUT.glob("*.svg")})
