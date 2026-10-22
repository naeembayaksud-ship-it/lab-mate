"""Builds the COMPLEX Lab Mate demo: a FICTIONAL 4-page paper (two-stage seed-mediated silver nanoparticle synthesis plus a
population-balance model) with 4 figures. Everything is invented for testing software.
Run with a Python that has pymupdf + matplotlib:  python make_complex.py

Planted ground truth (kept in ground_truth.json, never shown to the app):
 scenario X (multi-error): seed aging 90 min (paper 30), manual dropwise addition (paper syringe pump 0.5 mL/min),
   temperature 27 C (paper 25; the paper shows 20-30 C has NO effect = decoy), pH 9.8 (paper 10.0 +/-0.2 = within range = decoy).
 scenario Y (all reported parameters equal): hidden cause = chloride/organic contamination (low-grade water, unwashed glassware)
   and light exposure of the AgNO3 stock. The paper never mentions water grade, glassware cleaning or light for the stock.
"""
import json, pathlib
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pymupdf

OUT = pathlib.Path(__file__).parent
NAVY, TERRA = "#0A1526", "#B5472A"


def line(name, x, y, xl, yl, ylim, labels=None, w=4.4):
    f, ax = plt.subplots(figsize=(w, 2.7), dpi=200)
    ax.plot(x if labels is None else range(len(x)), y, "o-", color=NAVY, lw=1.6, ms=5)
    if labels:
        ax.set_xticks(range(len(x))); ax.set_xticklabels(labels, fontsize=6.5)
    ax.set_xlabel(xl, fontsize=8); ax.set_ylabel(yl, fontsize=8); ax.set_ylim(*ylim); ax.grid(alpha=.25); ax.tick_params(labelsize=7)
    for i, b in enumerate(y):
        ax.annotate(str(b), (i if labels else x[i], b), textcoords="offset points", xytext=(0, 6), ha="center", fontsize=6.5, color=TERRA)
    f.savefig(OUT / name, bbox_inches="tight"); plt.close(f)


def fig_setup():
    f, ax = plt.subplots(figsize=(6.6, 2.6), dpi=200)
    ax.axis("off"); ax.set_xlim(0, 10); ax.set_ylim(0, 4)
    def box(x, y, w, h, t, fc="#F0EFEA"):
        ax.add_patch(plt.Rectangle((x, y), w, h, fc=fc, ec=NAVY, lw=1.2)); ax.text(x + w / 2, y + h / 2, t, ha="center", va="center", fontsize=6.6, color=NAVY)
    box(0.1, 1.2, 2.3, 1.7, "Stage 1: seeds\nAgNO3 0.25 mM + citrate\n+ NaBH4 (ice-cold)\n25 °C, 400 rpm", "#DCE2EA")
    box(3.0, 1.2, 1.5, 1.7, "Seed aging\n30 min, dark\n25 °C")
    box(5.1, 1.2, 2.3, 1.7, "Stage 2: growth\nsyringe pump 0.5 mL/min\nAgNO3 1.0 mM feed\npH 10.0, 400 rpm", "#DCE2EA")
    box(8.0, 1.2, 1.9, 1.7, "Analysis\nUV-vis, TEM, DLS,\nICP-OES")
    for x0, x1 in [(2.4, 3.0), (4.5, 5.1), (7.4, 8.0)]:
        ax.annotate("", xy=(x1, 2.05), xytext=(x0, 2.05), arrowprops=dict(arrowstyle="->", color=TERRA, lw=1.4))
    f.savefig(OUT / "cfig1.png", bbox_inches="tight"); plt.close(f)


fig_setup()
line("cfig2.png", [0, 15, 30, 60, 90, 120], [21, 21, 22, 27, 35, 43], "Seed aging time (min)", "Mean diameter (nm)", (10, 55))
line("cfig3.png", [0]*6, [0.31, 0.19, 0.12, 0.11, 0.15, 0.22], "Growth-feed addition mode (mL/min; last = manual dropwise)", "PDI",
     (0.0, 0.4), labels=["0.1", "0.25", "0.5", "1.0", "2.0", "manual"])
line("cfig4.png", [20, 25, 30], [22, 22, 23], "Reaction temperature (°C)", "Mean diameter (nm)", (10, 40), w=3.6)

HTML = """
<h1>DEMO (fictional, not a real paper): Size and polydispersity control in two-stage seed-mediated growth of silver nanoparticles</h1>
<p class="au">A. Demo, B. Example, C. Sample. Department of Imaginary Nanomaterials. Written only to test software.</p>
<h2>Abstract</h2>
<p>Silver nanoparticles were prepared in two stages: citrate-stabilised seeds were made by borohydride reduction and then grown by slow
addition of silver nitrate with ascorbic acid as the reducing agent. Seeds aged for 30 min and a growth feed of 0.5 mL/min gave particles of
22 nm with a polydispersity index (PDI) of 0.12 and a localised surface plasmon resonance (LSPR) peak at 408 nm. Longer seed aging and fast
or uncontrolled addition broadened the distribution. A population-balance model reproduced the measured size.</p>
<h2>1. Introduction</h2>
<p>Narrow size distributions of silver nanoparticles are needed for sensing and catalysis. Size is set by the number of seeds and by how much
silver is deposited on each seed. Secondary nucleation during growth creates a second, smaller population and widens the distribution.</p>
<h2>2. Methods</h2>
<h3>2.1 Materials</h3>
<p>Silver nitrate (99.9 % purity), trisodium citrate (99 %), sodium borohydride (98 %), L-ascorbic acid (99 %), polyvinylpyrrolidone (PVP,
molecular weight 40 000 g/mol) and sodium hydroxide were used as received. Deionised water was used throughout. The silver nitrate
concentration in the seed solution was 0.25 mM and the citrate concentration was 1.0 mM. The growth feed was 1.0 mM silver nitrate; the
growth solution contained 10 mM ascorbic acid and 0.5 wt % PVP.</p>
<h3>2.2 Stage 1: seed synthesis</h3>
<p>Twenty millilitres of the silver nitrate and citrate solution were stirred at 400 rpm with a PTFE-coated magnetic bar in a jacketed 50 mL
glass reactor held at 25 °C (±1 °C) by a circulating bath. Sodium borohydride solution (0.1 mM, 0.6 mL) was prepared freshly in ice-cold water,
used within 10 min, and added in one portion. The seeds were stirred for 5 min and then aged for 30 min at 25 °C in the dark.</p>
<h3>2.3 Stage 2: growth</h3>
<p>Two millilitres of aged seeds were added to 18 mL of growth solution. The pH was adjusted to 10.0 (±0.2) with sodium hydroxide and monitored
with a calibrated glass electrode. Ten millilitres of the 1.0 mM silver nitrate feed were added through a PTFE tube into the vortex with a
syringe pump at 0.5 mL/min, at 400 rpm and 25 °C. After the addition the dispersion was stirred for another 20 min.</p>
<img src="cfig1.png" style="width:440px">
<p class="cap">Figure 1. Two-stage synthesis: seed formation, aging, and syringe-pump growth, followed by characterisation.</p>
<h3>2.4 Characterisation</h3>
<p>UV-vis spectra were recorded from 300 to 700 nm in 1 cm quartz cuvettes. The mean diameter was obtained from transmission electron microscopy
(TEM) by counting at least 300 particles per sample. The PDI was obtained by dynamic light scattering (DLS) at a 173° backscatter angle. The
silver conversion was determined by ICP-OES after centrifugation at 12 000 g for 20 min. Every condition was run in triplicate.</p>
<h3>2.5 Population-balance model</h3>
<p>A population-balance model with nucleation, growth and Ostwald ripening was solved with a stiff BDF integrator, relative tolerance 1e-6,
absolute tolerance 1e-9, 200 size bins and a simulated time of 0 to 40 min. The nucleation rate constant was fitted to 3.2e-3 s-1.</p>
<h2>3. Results and discussion</h2>
<h3>3.1 Effect of seed aging</h3>
<p>The mean diameter of the final particles (Fig. 2) stayed at 21 to 22 nm for aging times up to 30 min and then increased to 27 nm after 60 min,
35 nm after 90 min and 43 nm after 120 min. Ageing lets the seeds ripen and aggregate, so fewer, larger seeds carry the same silver and
the final particles become larger and more polydisperse.</p>
<img src="cfig2.png" style="width:330px">
<p class="cap">Figure 2. Mean diameter of the final particles as a function of the seed aging time (growth feed 0.5 mL/min, 25 °C, pH 10.0).</p>
<h3>3.2 Effect of the addition mode</h3>
<p>The PDI (Fig. 3) fell from 0.31 at 0.1 mL/min to 0.12 at 0.5 mL/min and stayed at 0.11 at 1.0 mL/min, but rose to 0.15 at 2.0 mL/min and
to 0.22 when the feed was added manually dropwise. Local supersaturation at the drop causes secondary nucleation and a second, smaller population.</p>
<img src="cfig3.png" style="width:350px">
<p class="cap">Figure 3. Polydispersity index as a function of the growth-feed addition mode (seed aging 30 min, 25 °C, pH 10.0).</p>
<h3>3.3 Effect of temperature and pH</h3>
<p>Between 20 and 30 °C the diameter was 22, 22 and 23 nm (Fig. 4), so the temperature had no significant effect in this range. Below pH 9.0
the particles aggregated, while pH 9.8 to 10.2 gave stable dispersions with identical size within the error.</p>
<img src="cfig4.png" style="width:300px">
<p class="cap">Figure 4. Mean diameter as a function of the reaction temperature (seed aging 30 min, 0.5 mL/min, pH 10.0).</p>
<h3>3.4 Standard conditions and model</h3>
<p>Under the standard conditions (30 min aging, 0.5 mL/min, 25 °C, pH 10.0) the mean diameter was 22 nm (±2 nm), the PDI 0.12 and the LSPR peak
408 nm. The silver conversion was 94 %. The population-balance model predicted a diameter of 21 nm for the same conditions. A larger diameter
shifted the LSPR peak to longer wavelengths, about 1 nm per nanometre of diameter.</p>
<h2>4. Conclusions</h2>
<p>Narrow, 22 nm silver nanoparticles require short seed aging and a controlled growth feed.</p>
<h2>References</h2>
<p>[1] A. Demo, Fictional Journal of Imaginary Nanomaterials 2 (2026) 10-19. This reference is invented.</p>
"""
CSS = """
body{font-family:sans-serif;font-variant-ligatures:none;font-feature-settings:"liga" 0,"clig" 0;font-size:10pt;line-height:1.35}
h1{font-size:15pt;margin:0 0 6pt 0} h2{font-size:12pt;margin:12pt 0 3pt 0} h3{font-size:10.5pt;margin:8pt 0 2pt 0}
p{margin:0 0 5pt 0} .au{font-style:italic;color:#444} .cap{font-size:9pt;margin:2pt 0 8pt 0} img{margin-top:4pt}
"""
MB = pymupdf.paper_rect("a4"); WHERE = MB + (54, 54, -54, -54)
story = pymupdf.Story(html=HTML, user_css=CSS, archive=pymupdf.Archive(str(OUT)))
w = pymupdf.DocumentWriter(str(OUT / "_raw.pdf"))
more = 1
while more:
    dev = w.begin_page(MB); more, _ = story.place(WHERE); story.draw(dev); w.end_page()
w.close()
d = pymupdf.open(str(OUT / "_raw.pdf")); d.save(str(OUT / "complex_paper_silver_nanoparticles.pdf"), garbage=4, deflate=True, deflate_fonts=True, clean=True); d.close()
try:
    (OUT / "_raw.pdf").unlink()
except OSError:
    pass

GT = {
    "X": {"true_causes": ["seed aging", "manual dropwise addition"],
          "decoys_should_rank_low": ["temperature", "pH"],
          "match": {"seed aging": ["aging", "ageing"], "manual dropwise addition": ["dropwise", "manual", "syringe", "addition"]},
          "decoy_match": {"temperature": ["temperature"], "pH": ["ph "]}},
    "Y": {"true_causes": ["contamination (water/glassware)", "light exposure of AgNO3 stock"],
          "match": {"contamination (water/glassware)": ["chloride", "contamin", "water", "glassware", "clean", "resistiv", "conductiv"],
                    "light exposure of AgNO3 stock": ["light", "dark", "photo", "storage", "amber"]}},
}
(OUT / "ground_truth.json").write_text(json.dumps(GT, indent=1), encoding="utf-8")
print("done")
