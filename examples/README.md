# Lab Mate demo (all data fictional)

Paper: demo_paper_aspirin.pdf - a made-up 2-page paper on aspirin synthesis (3 figures, Methods + Results).
Rebuild everything with: python make_demo.py (needs pymupdf).

How to try it
1. Open http://127.0.0.1:8650, drop demo_paper_aspirin.pdf, wait until the paper is read.
2. Compare step: import one of the user_*.json files (or type the values), then open Result.

Scenarios
- A  user_A_temperature_mistake.json: bath set to 60 C instead of 75 C. Expect: Branch A, one card "Reaction temperature", with the paper quote, plus hidden-variable suggestions for the equipment side.
- B  user_B_all_same_low_yield.json: every parameter equals the paper, yield 68 % vs 80 %. Expect: Branch B, hidden variables with "AI estimate" ranges (e.g. thermometer calibration, mixing, filtration).
- C  user_C_equipment_mistake.json: no magnetic stirrer, gravity filtration. Expect: Branch A on both sides (stirring speed, heating configuration, isolation equipment).

The model's wording differs between runs; the differing parameters are decided by code and are stable.
