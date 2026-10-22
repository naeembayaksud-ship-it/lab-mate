# Lab Mate

**Experiment failed, or your result differs from the paper?** Lab Mate reads the paper, links every extracted parameter to the
exact source text (checked by code), compares your values with the paper's (code decides what differs, not the AI), and returns
ranked hypotheses, a test plan, hidden variables to control, and related papers.

Flow: **Paper** (read it, verify every parameter) > **Compare** (enter your values) > **Result** (hypotheses, per-parameter
recommendations, hidden variables, related papers, citation network, report as HTML/PDF).

Demo video: attached to the [latest release](../../releases/latest).

## Quick start

```
git clone https://github.com/naeembayaksud-ship-it/lab-mate
cd lab-mate
python run.py
```

Needs Python 3.9 or newer. On the first run Lab Mate installs its one required package (`pypdf`) and opens
http://127.0.0.1:8650 in your browser. Then:

1. Click **Settings** (top bar).
2. Pick a provider and paste your own key (see the table below), or choose Hermes sign-in.
3. Press **Test connection**, then **Save**. Drop a paper PDF on the home screen.

| Option | What you need | Notes |
|---|---|---|
| **Anthropic API key** | key from console.anthropic.com | Billed to your account. Default model `claude-sonnet-4-5`; change it in Settings. |
| **OpenAI-compatible API** | key + base URL | OpenAI, OpenRouter, or a local server such as Ollama (`http://localhost:11434/v1`, no key). Quality depends on the model. |
| **Hermes sign-in (OAuth)** | the Hermes CLI, signed in once (`hermes login`) | Sign-in is handled by Hermes, not by Lab Mate. |

The key stays on your computer in `app/settings.json` (ignored by git, never sent to the browser). Environment variables such as
`ANTHROPIC_API_KEY` also work and win over the file; see `.env.example`.

Optional extras (scanned-PDF OCR and the Figures tab): `python run.py --install`. These need Python 3.11 or 3.12 (no builds
exist yet for 3.13+). The app works without them.

## Privacy
With a cloud provider, the paper text is sent to that provider. Use a local model (Ollama) for unpublished or confidential
papers. Lab Mate recommends only; it never runs an experiment or a simulation.

## What is in the repo

```
run.py              one-command launcher (any OS)
requirements.txt    required + optional packages
.env.example        environment variables the app understands
app/                the application: server.py, logic.py, llm.py, index.html (single-file UI), assets/
tests/              UI smoke test, accessibility audit, screenshot helper (need Edge or Chrome)
examples/           fictional papers and sample "my experiment" files to try it with
```

## Try it without your own data
Upload `examples/demo_paper_aspirin.pdf`, open Compare, and enter the values from `examples/user_A_temperature_mistake.json`
(water bath 60 C instead of 75 C) or `user_B_all_same_low_yield.json` (everything matches, yield is still low). A harder
fictional paper with planted causes is in `examples/silver_nanoparticles/`. All example data is fictional; see `examples/README.md`.

## Development
```
python run.py --no-browser          # server only
python tests/smoke.py <project-id>  # clicks through the UI in headless Edge (server must be running)
```
Optional search keys: Scopus (`~/.config/pybliometrics.cfg`) and `OPENALEX_API_KEY`. Without them, Find papers uses the free
OpenAlex and Crossref APIs, and OpenAlex may hit its daily limit.

## Known limits
- Hypothesis quality depends on the model you connect. A small local model may return invalid JSON.
- Keyword percentages are title/abstract co-occurrence, not the size of a variable's effect.
- Hidden-variable ranges are AI estimates and are labelled as such.
- Save PDF needs Edge or Chrome (set `PDF_BROWSER` for another path). Tested on Windows; macOS and Linux are untested.

## Papers and data
Papers you upload keep their own rights.
