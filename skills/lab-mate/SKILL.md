---
name: lab-mate
description: Use when a user asks why an experiment or simulation differs from a paper, or wants to run Lab Mate. Drives the Paper -> Compare -> Result flow of the Lab Mate app through its local HTTP API.
---

# Lab Mate workflow

Lab Mate runs locally (`python run.py`, default http://127.0.0.1:8650). Follow the same order as the website. Never skip a step, and never invent values: every paper parameter must keep its verified source text, and AI guesses stay labelled.

## 0. Check the app
1. `GET /api/settings`. If `configured` is false, tell the user to open Settings in the app and add their own API key (or choose Hermes). Never ask for the key in chat and never write it to a file.
2. If the server is not running, start it with `python run.py --no-browser`.
3. Write requests with header `Origin: http://127.0.0.1:8650` (the server rejects other origins).

## 1. Paper
1. `POST /api/upload?name=<file>.pdf` with the PDF bytes -> project id `pid`. SI files: `POST /api/projects/<pid>/attach?name=<file>`.
2. `POST /api/projects/<pid>/extract`, then poll `GET /api/job/<id>` until `status` is `done`.
3. Read `GET /api/projects/<pid>`: `fields` (name, value, unit, page, source text, `verified`), `method` (flowchart steps), `checklist`.
4. Only report a value as "from the paper" when `verified` is true.

## 2. Compare
1. Ask the user for their actual values, same value and unit format. Write them with `PUT /api/projects/<pid>` (`compare` and `results`), or `POST /api/projects/<pid>/import_user` for a file like `examples/user_A_temperature_mistake.json`.
2. `POST /api/compare` returns the status per parameter. Code decides what differs (unit conversion, tolerance), not the model. Values the user did not touch are "assumed", not "match".

## 3. Result
- `POST /api/projects/<pid>/diagnose` -> ranked hypotheses, test plan, data requested.
- `POST /api/projects/<pid>/recs` -> one recommendation per differing parameter (Case 1). If nothing differs (Case 2), it returns hidden variables with ranges labelled "AI estimate".
- Present Case 1 and Case 2 separately. Say clearly which statements are verified in the paper and which are AI guesses.

## 4. Optional, after the result
- `POST /api/projects/<pid>/find` for related papers (OpenAlex, Scopus if a key exists, Crossref) with DOIs; `POST /api/projects/<pid>/graph` for the citation network.
- `POST /api/projects/<pid>/ask` with `{"text": ...}` for questions answered from the paper pages.
- `GET /api/projects/<pid>/report.html` or `report.pdf` to save the result.

## Rules
- Keyword percentages are co-occurrence in titles/abstracts, not the size of a variable's effect.
- Lab Mate gives recommendations. It does not run experiments or simulations.
- Uploaded papers are sent to the chosen model provider; warn the user for unpublished work.
