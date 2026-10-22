# Complex demo (all data fictional)

Paper: complex_paper_silver_nanoparticles.pdf - invented 3-page paper: two-stage seed-mediated silver nanoparticle synthesis
(9 method steps, 4 figures, population-balance model, 48 parameters). Rebuild: python make_complex.py (pymupdf + matplotlib).

Planted answers (ground_truth.json, never shown to the app)
- X (multi-error): seed aging 90 min (paper 30), manual dropwise feed (paper syringe pump 0.5 mL/min). Decoys: seed temperature
  27 C (paper shows 20-30 C has no effect), pH 9.8 (inside the paper's +/-0.2).
- Y (all reported parameters equal): hidden cause = chloride/organic contamination (water, glassware) and light exposure of the
  AgNO3 stock. The paper never mentions them.

Files: user_complex_X.json / user_complex_Y.json (import them in the Compare step), ground_truth.json (the planted causes, do not show the model).
