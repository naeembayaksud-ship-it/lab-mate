"""Builds the Lab Mate demo set: a short FICTIONAL paper (PDF) and three user-data files with simple mistakes.
Run with a Python that has pymupdf + matplotlib:  python make_demo.py
Everything here is invented for testing. None of the numbers come from a real study."""
import json, pathlib
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pymupdf

OUT = pathlib.Path(__file__).parent
NAVY, TERRA = "#0A1526", "#B5472A"

# ---------- figures ----------
T = [55, 60, 65, 70, 75, 80, 85, 90]
YT = [48, 62, 74, 81, 85, 84, 79, 72]
TM = [5, 10, 15, 20, 30, 40]
YM = [41, 63, 79, 85, 86, 85]


def fig_setup():
    f, ax = plt.subplots(figsize=(6.4, 2.6), dpi=200)
    ax.axis("off"); ax.set_xlim(0, 10); ax.set_ylim(0, 4)
    def box(x, y, w, h, t, fc="#F0EFEA"):
        ax.add_patch(plt.Rectangle((x, y), w, h, fc=fc, ec=NAVY, lw=1.2)); ax.text(x + w / 2, y + h / 2, t, ha="center", va="center", fontsize=7, color=NAVY)
    box(0.2, 1.2, 2.2, 1.6, "Water bath\n75 °C (±1 °C)\nmagnetic stirrer\n300 rpm", "#DCE2EA")
    box(3.3, 1.2, 2.0, 1.6, "125 mL flask\n2.0 g salicylic acid\n5.0 mL acetic anhydride\n5 drops H2SO4")
    box(6.2, 1.2, 1.6, 1.6, "Ice bath\n15 min,\n50 mL cold water")
    box(8.4, 1.2, 1.4, 1.6, "Büchner\nfiltration,\ndry at 50 °C")
    for x0, x1 in [(2.4, 3.3), (5.3, 6.2), (7.8, 8.4)]:
        ax.annotate("", xy=(x1, 2.0), xytext=(x0, 2.0), arrowprops=dict(arrowstyle="->", color=TERRA, lw=1.4))
    f.savefig(OUT / "fig1.png", bbox_inches="tight"); plt.close(f)


def fig_line(name, x, y, xl, yl, ylim):
    f, ax = plt.subplots(figsize=(4.6, 2.8), dpi=200)
    ax.plot(x, y, "o-", color=NAVY, lw=1.6, ms=5)
    ax.set_xlabel(xl, fontsize=8); ax.set_ylabel(yl, fontsize=8); ax.set_ylim(*ylim); ax.grid(alpha=.25)
    ax.tick_params(labelsize=7)
    for a, b in zip(x, y):
        ax.annotate(str(b), (a, b), textcoords="offset points", xytext=(0, 6), ha="center", fontsize=6.5, color=TERRA)
    f.savefig(OUT / name, bbox_inches="tight"); plt.close(f)


fig_setup()
fig_line("fig2.png", T, YT, "Reaction temperature (°C)", "Aspirin yield (%)", (30, 100))
fig_line("fig3.png", TM, YM, "Reaction time at 75 °C (min)", "Aspirin yield (%)", (30, 100))

# ---------- paper ----------
HTML = """
<h1>DEMO (fictional, not a real paper): Effect of reaction temperature and time on the yield of aspirin synthesis</h1>
<p class="au">A. Demo, B. Example. Department of Imaginary Chemistry. This document was written only to test software.</p>
<h2>Abstract</h2>
<p>Acetylsalicylic acid (aspirin) was synthesised from salicylic acid and acetic anhydride with sulfuric acid as the catalyst.
The reaction temperature was varied from 55 to 90 °C and the reaction time from 5 to 40 min. The highest yield, 85 %, was obtained at
75 °C and 20 min. Lower temperatures gave incomplete conversion, and temperatures above 80 °C reduced the yield.</p>
<h2>1. Introduction</h2>
<p>Aspirin is made by acetylation of salicylic acid. The reaction is fast, but the isolated yield depends on temperature, time and
on how the product is crystallised. This short study reports a simple batch procedure and shows how yield responds to the two main
operating conditions.</p>
<h2>2. Methods</h2>
<h3>2.1 Materials</h3>
<p>Salicylic acid (99 % purity, 2.0 g) and acetic anhydride (98 % purity, 5.0 mL) were used as received. Concentrated sulfuric acid
(5 drops, about 0.25 mL) served as the catalyst. Distilled water (50 mL, ice cold) was used for crystallisation.</p>
<h3>2.2 Reaction procedure and equipment</h3>
<p>Salicylic acid was weighed on an analytical balance (±0.1 mg) into a 125 mL Erlenmeyer flask. Acetic anhydride and the catalyst were added.
The flask was held in a thermostatic water bath at 75 °C (±1 °C) for 20 min with a magnetic stirrer at 300 rpm. The flask was then
moved to an ice bath for 15 min, and 50 mL of ice-cold distilled water was added to crystallise the product. The crystals were collected
by vacuum filtration on a Büchner funnel and dried in an oven at 50 °C for 24 h.</p>
<img src="fig1.png" style="width:430px">
<p class="cap">Figure 1. Experimental setup and procedure of the aspirin synthesis (water bath, reaction flask, ice bath, filtration).</p>
<h3>2.3 Analysis</h3>
<p>The yield was calculated from the dried mass relative to the theoretical mass. The melting point was measured with a capillary
melting point apparatus. Each condition was run in triplicate.</p>
<h2>3. Results and discussion</h2>
<h3>3.1 Effect of reaction temperature</h3>
<p>The yield (see Fig. 2) at a fixed reaction time of 20 min. The yield rose from 48 % at 55 °C to a maximum of 85 % at 75 °C.
Above 80 °C it fell again, to 72 % at 90 °C, because acetic anhydride and aspirin hydrolyse faster at high temperature. At 60 °C the yield was only 62 %.</p>
<img src="fig2.png" style="width:330px">
<p class="cap">Figure 2. Effect of reaction temperature on aspirin yield (reaction time 20 min, 300 rpm).</p>
<h3>3.2 Effect of reaction time</h3>
<p>The yield at 75 °C is plotted in Fig. 3. The yield increased from 41 % after 5 min to 85 % after 20 min and then levelled off. Longer
times gave no further gain, so 20 min was chosen as the standard time.</p>
<img src="fig3.png" style="width:330px">
<p class="cap">Figure 3. Effect of reaction time on aspirin yield (75 °C, 300 rpm).</p>
<h3>3.3 Product quality</h3>
<p>Under the standard conditions (75 °C, 20 min, 300 rpm) the product melted at 135 °C and the average yield was 85 %.</p>
<h2>4. Conclusions</h2>
<p>A temperature of 75 °C and a reaction time of 20 min gave the best aspirin yield (85 %) in this batch procedure.</p>
<h2>References</h2>
<p>[1] A. Demo, Fictional Journal of Imaginary Chemistry 1 (2026) 1-5. This reference is invented.</p>
"""
CSS = """
body{font-family:sans-serif;font-variant-ligatures:none;font-feature-settings:"liga" 0,"clig" 0;font-size:10pt;line-height:1.35}
h1{font-size:15pt;margin:0 0 6pt 0} h2{font-size:12pt;margin:12pt 0 3pt 0} h3{font-size:10.5pt;margin:8pt 0 2pt 0}
p{margin:0 0 5pt 0} .au{font-style:italic;color:#444} .cap{font-size:9pt;margin:2pt 0 8pt 0}
img{margin-top:4pt}
"""
MB = pymupdf.paper_rect("a4")
WHERE = MB + (54, 54, -54, -54)
story = pymupdf.Story(html=HTML, user_css=CSS, archive=pymupdf.Archive(str(OUT)))
w = pymupdf.DocumentWriter(str(OUT / "_raw.pdf"))
more = 1
while more:
    dev = w.begin_page(MB)
    more, _ = story.place(WHERE)
    story.draw(dev)
    w.end_page()
w.close()
_d = pymupdf.open(str(OUT / "_raw.pdf")); _d.save(str(OUT / "demo_paper_aspirin.pdf"), garbage=4, deflate=True, deflate_fonts=True, clean=True); _d.close()
try:
    (OUT / "_raw.pdf").unlink()
except OSError:
    pass  # Windows may still hold the handle; the file is harmless

# ---------- user data (what the researcher actually did) ----------
def P(v, u=""):
    return {"value": v, "unit": u} if u else {"value": v}

same = {
    "Salicylic acid amount": P(2.0, "g"), "Salicylic acid purity": P(99, "%"),
    "Acetic anhydride volume": P(5.0, "mL"), "Acetic anhydride purity": P(98, "%"),
    "Concentrated sulfuric acid catalyst amount": P(0.25, "mL"), "Reaction time (standard)": P(20, "min"), "Stirring speed": P(300, "rpm"),
    "Ice bath time": P(15, "min"), "Ice-cold distilled water volume for crystallisation": P(50, "mL"), "Drying temperature": P(50, "°C"),
    "Drying time": P(24, "h"),
}
A = {"notes": "FICTIONAL demo A: the water bath was set to 60 C instead of 75 C (mistake in one condition).", "kind": "experiment",
     "parameter": {**same, "Reaction temperature (standard)": P(60, "°C")},
     "user_results": {"Maximum yield at 75 °C and 20 min": {"value": 61, "unit": "%", "replicates": 3, "uncertainty": "±3 %"},
                      "Product melting point": {"value": 129, "unit": "°C", "replicates": 3}}}
B = {"notes": "FICTIONAL demo B: every reported parameter matches the paper, but the yield is still low.", "kind": "experiment",
     "parameter": {**same, "Reaction temperature (standard)": P(75, "°C")},
     "user_results": {"Maximum yield at 75 °C and 20 min": {"value": 68, "unit": "%", "replicates": 3, "uncertainty": "±2 %"},
                      "Product melting point": {"value": 131, "unit": "°C", "replicates": 3}}}
C = {"notes": "FICTIONAL demo C: no magnetic stirrer (hot plate, swirled by hand) and gravity filtration instead of a Buchner funnel.", "kind": "experiment",
     "parameter": {**same, "Reaction temperature (standard)": P(75, "°C"), "Stirring speed": P(0, "rpm"),
                   "Reaction heating configuration": P("Hot plate with hand swirling, no magnetic stirrer"),
                   "Product isolation equipment": P("Gravity filtration funnel")},
     "user_results": {"Maximum yield at 75 °C and 20 min": {"value": 70, "unit": "%", "replicates": 3, "uncertainty": "±4 %"}}}
for n, d in [("A_temperature_mistake", A), ("B_all_same_low_yield", B), ("C_equipment_mistake", C)]:
    (OUT / f"user_{n}.json").write_text(json.dumps(d, indent=1, ensure_ascii=False), encoding="utf-8")
print("done")
