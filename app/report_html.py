"""Renders one Lab Mate project into a self-contained HTML results page (images and fonts embedded; no network needed).
build_html(pr) -> str; build_pdf(html) -> bytes (headless Edge/Chrome print-to-pdf)."""
import base64, html, json, os, shutil, subprocess, tempfile, time

APP = os.path.dirname(os.path.abspath(__file__))


E = lambda s: html.escape(str(s if s is not None else ''), quote=True)  # noqa: E731
TIER = {'high': ('Strong match', 'ok'), 'medium': ('Possible match', 'info'), 'low': ('Weak match', 'gray')}
ST = {'match': ('Match', 'ok'), 'different': ('Different', 'bad'), 'within_range': ('Within paper range', 'info'),
      'not_comparable': ('Not comparable', 'warn'), 'not_yet_known': ('Unknown', 'gray')}
SIDE = {'materials_conditions': 'Materials & conditions', 'equipment': 'Equipment'}
ROLE = {'materials': 'Materials', 'conditions': 'Conditions', 'equipment': 'Equipment', 'results': 'Results'}


def b64(path, mime):
    return 'data:%s;base64,%s' % (mime, base64.b64encode(open(path, 'rb').read()).decode())


def fonts_css():
    out = []
    for fam, f, w in [('Newsreader', 'newsreader-latin-400-normal', 400), ('Newsreader', 'newsreader-latin-500-normal', 500),
                      ('Plus Jakarta Sans', 'plus-jakarta-sans-latin-400-normal', 400), ('Plus Jakarta Sans', 'plus-jakarta-sans-latin-700-normal', 700),
                      ('JetBrains Mono', 'jetbrains-mono-latin-400-normal', 400)]:
        p = os.path.join(APP, 'assets', 'fonts', f + '.woff2')
        if os.path.exists(p):
            out.append("@font-face{font-family:'%s';font-weight:%d;font-display:swap;src:url(%s) format('woff2')}" % (fam, w, b64(p, 'font/woff2')))
    return '\n'.join(out)


CSS = """
:root{--bg:#F9F8F6;--sand:#F0EFEA;--panel:#fff;--ink:#0A1526;--mut:#525866;--line:#E2E0D8;--acc:#B5472A;--terra:#D97757;--blue:#DCE2EA;
--ok:#14532d;--okbg:#e3f1e7;--bad:#8a1c1c;--badbg:#fbe4e1;--warn:#6b4500;--warnbg:#fbefcf;--info:#173a85;--infobg:#dde6f7;--gray:#3d4452;--graybg:#e9e8e3}
*{box-sizing:border-box}html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 'Plus Jakarta Sans',system-ui,-apple-system,'Segoe UI',sans-serif}
h1,h2,h3{font-family:Newsreader,Georgia,serif;font-weight:400;letter-spacing:-.02em;line-height:1.15;margin:0}
h1{font-size:40px}h2{font-size:28px}h3{font-size:19px;font-weight:500}
a{color:#9C3F25}
header.top{position:sticky;top:0;z-index:5;background:rgba(249,248,246,.88);backdrop-filter:blur(8px);border-bottom:1px solid var(--line);display:flex;align-items:center;gap:22px;padding:10px 28px}
header.top img{height:36px}nav{display:flex;gap:18px;flex-wrap:wrap;font-size:14px}nav a{color:var(--ink);text-decoration:none;border-bottom:1px solid transparent;padding:2px 0}nav a:hover{border-color:var(--acc)}
.grow{flex:1}.btn{border:1px solid var(--ink);padding:6px 14px;border-radius:4px;text-decoration:none;color:var(--ink);font-size:14px}.btn:hover{background:var(--ink);color:var(--bg)}
main{max-width:1280px;margin:0 auto;padding:0 28px 80px}
.hero{padding:56px 0 30px;position:relative;border-bottom:1px solid var(--line)}
.mono{font:12px/1.3 'JetBrains Mono',ui-monospace,Consolas,monospace;text-transform:uppercase;letter-spacing:.06em;color:var(--mut)}
.lede{max-width:820px;color:var(--mut);font-size:17px;margin:14px 0 0}
section{padding:44px 0 6px;border-bottom:1px solid var(--line)}section>h2{margin-bottom:6px}.sub{color:var(--mut);margin:0 0 18px;max-width:900px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:0;border:1px solid var(--line);margin-top:26px;background:var(--panel)}
.stat{padding:14px 18px;border-right:1px solid var(--line)}.stat:last-child{border-right:0}.stat b{display:block;font:400 30px Newsreader,Georgia,serif}.stat span{color:var(--mut);font-size:13px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:16px 18px;margin:0 0 14px}
.card:hover{border-color:var(--ink)}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(380px,1fr));gap:14px}
.chip{display:inline-block;font:12px/1.5 'JetBrains Mono',monospace;border:1px solid;border-radius:3px;padding:0 7px;margin:0 4px 2px 0;white-space:normal}
.c-ok{background:var(--okbg);color:var(--ok);border-color:#9cc9a9}.c-bad{background:var(--badbg);color:var(--bad);border-color:#e2a49d}
.c-warn{background:var(--warnbg);color:var(--warn);border-color:#e0c680}.c-info{background:var(--infobg);color:var(--info);border-color:#a9bde6}.c-gray{background:var(--graybg);color:var(--gray);border-color:#c9c7bf}
table{border-collapse:collapse;width:100%;background:var(--panel);font-size:14px}th,td{border:1px solid var(--line);padding:7px 10px;text-align:left;vertical-align:top}
th{background:var(--sand);font:12px 'JetBrains Mono',monospace;text-transform:uppercase;letter-spacing:.05em;color:var(--mut)}tr.diff td{background:#fdf3f1}tr.grp td{background:var(--sand);font-weight:700}
.tw{overflow:auto;border:1px solid var(--line)}
.quote{border-left:3px solid var(--terra);padding:2px 12px;margin:8px 0;color:var(--mut);font-style:italic}
.small{font-size:13px;color:var(--mut)}.vs{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:6px 0}.vs b{font:400 22px Newsreader,Georgia,serif}
.arrow{color:var(--acc)}.fig{margin:0 0 14px}.fig img{width:100%;border:1px solid var(--line);background:#fff}
.steps{counter-reset:s;list-style:none;padding:0;margin:0}.steps li{counter-increment:s;border-left:2px solid var(--ink);padding:2px 0 14px 18px;position:relative}
.steps li::before{content:counter(s);position:absolute;left:-13px;top:0;background:var(--ink);color:var(--bg);width:24px;height:24px;border-radius:50%;font-size:12px;display:flex;align-items:center;justify-content:center}
footer{padding:30px 0;color:var(--mut);font-size:13px}
.waves{position:absolute;right:0;top:0;width:420px;opacity:.07;pointer-events:none}
@media(max-width:700px){h1{font-size:30px}.grid2{grid-template-columns:1fr}header.top{padding:8px 14px}main{padding:0 14px 60px}}
@media print{header.top{position:static}.btn{display:none}.card,.fig,tr,.quote{break-inside:avoid}h2,h3{break-after:avoid}}
"""


def chip(label, kind):
    return '<span class="chip c-%s">%s</span>' % (kind, E(label))


def val(v, u=''):
    return E((str(v) + ' ' + str(u)).strip()) or '-'


def build(pr):
    import logic as L  # lazy: logic imports this module's siblings
    pid = pr['id']
    logo = b64(os.path.join(APP, 'assets', 'lab-mate-logo-sm.png'), 'image/png')
    wave = b64(os.path.join(APP, 'assets', 'waves.svg'), 'image/svg+xml')
    comp = L.compute_all(pr)
    fic = bool(pr.get('fictional'))
    fields = {f['id']: f for f in pr['fields']}
    rep = (pr.get('report') or [{}])[-1].get('results') or {}
    vr = pr.get('variable_recs') or {}
    rc = pr.get('recommendations') or {}
    fg = (pr.get('figures') or {}).get('items') or []
    n_diff = sum(1 for v in comp['param'].values() if v['status'] == 'different')
    n_res_gap = sum(1 for v in comp['results'].values() if v['status'] == 'different')
    ver = [f for f in pr['fields'] if f.get('verified')]
    o = []
    o.append('<section id="paper"><h2>1 · The paper</h2><p class="sub">%d parameters read from %d pages. Every parameter is traced directly to the exact line in the paper (%d of %d verified).</p>'
             % (len(pr['fields']), len(pr['pages']), len(ver), len(pr['fields'])))
    ms = (pr.get('method') or {}).get('steps') or []
    if ms:
        o.append('<h3>How the paper\'s method works</h3><p class="small">%s</p><ol class="steps">' % E((pr['method'].get('summary') or '')))
        for s in ms:
            o.append('<li><b>%s</b> <span class="small">· %s</span><div class="small">%s</div></li>' % (E(s['title']), E(s.get('phase', '')), E(s.get('description', ''))))
        o.append('</ol>')
    if fg:
        o.append('<h3 style="margin-top:26px">Figures captured from the PDF (Methods and Results)</h3><div class="grid2">')
        for g in fg:
            p = os.path.join(APP, 'uploads', 'figs', pid, g['file'])
            if os.path.exists(p):
                o.append('<figure class="fig"><img alt="%s" src="%s"><figcaption class="small"><b>%s</b> · p. %s · %s%s</figcaption></figure>'
                         % (E(g['caption'][:120]), b64(p, 'image/png'), E(g['label']), g['page'], E(g['section']), ' (guessed)' if g.get('inferred') else ''))
        o.append('</div>')
    o.append('<h3 style="margin-top:26px">Parameters by process step</h3>')
    groups = (pr.get('param_groups') or {}).get('groups') or []
    o.append('<div class="tw"><table><thead><tr><th>Parameter</th><th>Value</th><th>Page</th></tr></thead><tbody>')
    for g in groups or [{'name': 'All', 'ids': list(fields)}]:
        o.append('<tr class="grp"><td colspan="3">%s <span class="small">(%d)</span></td></tr>' % (E(g['name']), len(g['ids'])))
        for i in g['ids']:
            f = fields.get(i)
            if f:
                o.append('<tr><td>%s</td><td>%s</td><td>%s</td></tr>' % (E(f['name']), val(f['value'], f.get('unit', '')), E(f.get('page') or '-')))
    o.append('</tbody></table></div></section>')

    o.append('<section id="compare"><h2>2 · Your experiment vs the paper</h2><p class="sub">%sCode, not the model, decides which values differ (tolerance 5%%).</p>' % ('Your values are fictional demo data. ' if fic else ''))
    o.append('<div class="tw"><table><thead><tr><th>Parameter</th><th>Paper</th><th>Your run</th><th>Status</th></tr></thead><tbody>')
    rows = sorted([f for f in pr['fields'] if f['role'] != 'results'], key=lambda f: comp['param'][f['id']]['status'] != 'different')
    for f in rows:
        c = comp['param'][f['id']]
        u = (pr.get('compare') or {}).get(f['id']) or {}
        lab, k = ST.get(c['status'], (c['status'], 'gray'))
        o.append('<tr class="%s"><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % ('diff' if c['status'] == 'different' else '', E(f['name']), val(f['value'], f.get('unit', '')), val(u.get('value'), u.get('unit', '')), chip(lab, k)))
    o.append('</tbody></table></div><h3 style="margin-top:22px">Results</h3><div class="tw"><table><thead><tr><th>Result</th><th>Paper</th><th>Your result</th><th>Status</th><th>Computed by code</th></tr></thead><tbody>')
    for h in pr.get('results') or []:
        c = comp['results'][h['id']]
        pf = fields.get(h.get('paper_fid')) or {}
        lab, k = ST.get(c['status'], (c['status'], 'gray'))
        o.append('<tr class="%s"><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td class="small">%s</td></tr>' % ('diff' if c['status'] == 'different' else '', E(h['name']), val(pf.get('value'), pf.get('unit', '')), val(h['value'], h.get('unit', '')), chip(lab, k), E(c.get('reason', ''))))
    o.append('</tbody></table></div></section>')

    o.append('<section id="result"><h2>3 · What to do next</h2>')
    items = vr.get('items') or []
    if items:
        o.append('<p class="sub">Branch A: a separate recommendation for every parameter that differs. The model writes the advice; source text are checked by code.</p><div class="grid2">')
        for it in items:
            o.append('<div class="card"><div class="mono">%s</div><h3>%s</h3><div class="vs"><span class="small">Paper</span><b>%s</b><span class="arrow">→</span><span class="small">Yours</span><b>%s</b></div>'
                     '<p style="margin:8px 0"><b>%s</b></p><p class="small" style="margin:0 0 6px">%s</p>' % (E(SIDE.get(it['side'], it['side'])), E(it['name']), val(it['paper_value'], it['paper_unit']), val(it['user_value'], it['user_unit']), E(it['recommendation']), E(it['reason'])))
            if it.get('details'):
                o.append('<details><summary class="small">Details</summary><p class="small">%s</p></details>' % E(it['details']))
            if it.get('cite') or it.get('paper_quote'):
                q = (it.get('cite') or {}).get('quote') or it.get('paper_quote')
                o.append('<div class="quote">“%s” <span class="small">p. %s</span></div>' % (E(q), E((it.get('cite') or {}).get('page') or it.get('page') or '-')))
            o.append(chip('basis: ' + it['basis'] + (' (verified in paper)' if it['basis'] == 'paper' else ' (general knowledge, unverified)'), 'ok' if it['basis'] == 'paper' else 'warn')
                     + chip('confidence: ' + it['confidence'], 'gray'))
            if it.get('affects'):
                o.append('<div class="small">May affect: %s</div>' % E(', '.join(it['affects'])))
            o.append('</div>')
        o.append('</div>')
    hid = vr.get('hidden') or []
    if hid:
        sides = ', '.join(SIDE.get(s, s) for s in vr.get('same_sides') or [])
        o.append('<h3 style="margin-top:22px">Branch B: hidden variables the paper does not report</h3><p class="sub">Everything on the %s side matches the paper, so these unreported variables are the next place to look. Ranges are <b>AI estimates</b>, not measured facts.</p><div class="grid2">' % E(sides))
        for h in hid:
            o.append('<div class="card"><div class="mono">%s</div><h3>%s</h3><p class="small">%s</p>' % (E(SIDE.get(h.get('side'), '')), E(h['variable']), E(h['reason'])))
            o.append('<p style="margin:6px 0"><b>Suggested range:</b> %s %s</p><p class="small"><b>How to control it:</b> %s</p>%s</div>' % (E(h.get('range', '')), chip('AI estimate', 'warn'), E(h.get('how_to_control', '')), ''))
        o.append('</div>')
    o.append('</section>')

    if rep:
        v = rep.get('gap_verdict') or {}
        o.append('<section id="diagnosis"><h2>4 · Diagnosis: ranked hypotheses</h2><p class="sub">%s %s</p>' % (chip('verdict: ' + str(v.get('verdict', '')), 'bad' if v.get('verdict') == 'real' else 'gray'), E(v.get('basis', ''))))
        for h in rep.get('hypotheses') or []:
            def ev(lst):
                return ''.join('<li>%s %s%s</li>' % (chip(e.get('label', ''), 'ok' if e.get('label') == 'Paper' else ('info' if e.get('label') == 'Your data' else 'warn')), E(e.get('text', '')), (' <span class="small">p. %s</span>' % e['page']) if e.get('page') else '') for e in lst) or '<li class="small">none</li>'
            o.append('<div class="card"><div class="mono">#%s · %s · confidence %s</div><h3>%s</h3><p>%s</p><div class="grid2"><div><b>Supports</b><ul>%s</ul></div><div><b>Weakens</b><ul>%s</ul></div></div><p class="small">%s</p>%s</div>'
                     % (h['rank'], E(h.get('group', '')), E(h.get('confidence', '')), E(h.get('title', '')), E(h.get('statement', '')), ev(h.get('evidence_supports') or []), ev(h.get('evidence_weakens') or []), E(h.get('confidence_reason', '')),
                        ''.join(chip(p, 'gray') for p in h.get('related_parameters') or [])))
        tp = rep.get('test_plan') or []
        if tp:
            o.append('<h3 style="margin-top:22px">Test plan</h3><div class="tw"><table><thead><tr><th>#</th><th>Test</th><th>Change</th><th>Hold</th><th>Measure</th><th>Supports if</th><th>Weakens if</th><th>Cost · time</th></tr></thead><tbody>')
            for t in tp:
                o.append('<tr><td>%s</td><td><b>%s</b></td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s · %s</td></tr>' % (t.get('order'), E(t.get('title', '')), E(t.get('change', '')), E(t.get('hold', '')), E(t.get('measure', '')), E(t.get('supports_if', '')), E(t.get('weakens_if', '')), E(t.get('cost', '')), E(t.get('time', ''))))
            o.append('</tbody></table></div>')
        dr = rep.get('data_requested') or []
        if dr:
            o.append('<h3 style="margin-top:22px">Data that would help</h3><ul>%s</ul>' % ''.join('<li><b>%s</b>: %s <span class="small">%s</span></li>' % (E(d.get('title', '')), E(d.get('item', '')), E(d.get('reason', ''))) for d in dr))
        o.append('<p class="small">%s</p></section>' % E(' '.join(rep.get('limitations') or [])))

    cands = rc.get('candidates') or []
    if cands:
        o.append('<section id="papers"><h2>5 · Related papers that could help</h2><p class="sub">Found from your hypotheses and parameters (OpenAlex, Scopus, Crossref). Rated from title and abstract only, not full text.</p>')
        for c in [c for c in cands if c.get('tier') in ('high', 'medium')][:10]:
            lab, k = TIER[c['tier']]
            link = ('<a href="https://doi.org/%s" target="_blank" rel="noopener">%s</a>' % (E(c['doi']), E(c['title']))) if c.get('doi') else E(c['title'])
            o.append('<div class="card">%s <b>%s</b> <span class="small">%s · %s citations%s</span><p class="small" style="margin:6px 0 0">%s</p><div>%s</div></div>'
                     % (chip(lab, k), link, E(c.get('year', '')), E(c.get('citations', 0)), (' · DOI ' + E(c['doi'])) if c.get('doi') else '', E(c.get('helps', '')), ''.join(chip(r, 'gray') for r in c.get('related') or [])))
        gs = (pr.get('graph') or {}).get('stats')
        if gs:
            o.append('<p class="small">Citation network over %d papers: %d direct citations between them, %d earlier works cited by two or more (bibliographies from Crossref).</p>' % (gs['papers'], gs['cites'], gs['prior']))
        o.append('</section>')

    cl = pr.get('checklist') or {}
    if cl.get('controls_not_in_paper'):
        o.append('<section id="checklist"><h2>6 · Pre-lab checklist: variables to control</h2><p class="sub">Not reported in the paper. Ranges are AI estimates.</p><div class="tw"><table><thead><tr><th>Variable</th><th>Suggested range</th><th>Why it may matter</th><th>How to control it</th></tr></thead><tbody>')
        for c in cl['controls_not_in_paper']:
            o.append('<tr><td><b>%s</b></td><td>%s</td><td>%s</td><td>%s</td></tr>' % (E(c.get('variable', '')), E(c.get('range', '')), E(c.get('reason', '')), E(c.get('how_to_control', ''))))
        o.append('</tbody></table></div></section>')

    stats = ('<div class="stats"><div class="stat"><b>%d</b><span>parameters read</span></div><div class="stat"><b>%d / %d</b><span>parameters verified</span></div>'
             '<div class="stat"><b>%d</b><span>parameters differ</span></div><div class="stat"><b>%d</b><span>result gaps</span></div>'
             '<div class="stat"><b>%d</b><span>related papers</span></div></div>') % (len(pr['fields']), len(ver), len(pr['fields']), n_diff, n_res_gap, len([c for c in cands if c.get('tier') in ('high', 'medium')]))
    nav = ''.join('<a href="#%s">%s</a>' % (a, t) for a, t in [('paper', 'Paper'), ('compare', 'Compare'), ('result', 'Result'), ('diagnosis', 'Diagnosis'), ('papers', 'Related papers'), ('checklist', 'Checklist')])
    note = 'Saved results · user data is fictional' if fic else 'Saved results'
    page = ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Lab Mate · %s</title><style>%s%s</style></head><body>'
            '<header class="top"><img alt="Lab Mate" src="%s"><nav>%s</nav><span class="grow"></span><a class="btn" href="#" onclick="window.print();return false">Print / save as PDF</a></header><main>'
            '<div class="hero"><img class="waves" alt="" src="%s"><div class="mono">%s · %s</div><h1>%s</h1>'
            '<p class="lede">Paper file: %s. Lab Mate read the paper, compared it with the experiment entered in the app, and wrote the recommendations below.</p>%s</div>%s'
            '<footer>Generated by Lab Mate on %s. Effect directions and ranges marked AI are unverified.</footer></main></body></html>'
            % (E(pr['name']), fonts_css(), CSS, logo, nav, wave, note, time.strftime('%Y-%m-%d'), E(pr['name']), E(pr.get('filename') or pr['name']), stats, ''.join(o), time.strftime('%Y-%m-%d %H:%M')))
    return page


BROWSERS = ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge', '/Applications/Chromium.app/Contents/MacOS/Chromium', '/snap/bin/chromium', r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe', r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
            r'C:\Program Files\Google\Chrome\Application\chrome.exe', r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
            '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge']


def build_pdf(page):
    exe = os.environ.get('PDF_BROWSER') or next((b for b in BROWSERS if os.path.exists(b)), None) or shutil.which('msedge') or shutil.which('chrome') or shutil.which('google-chrome') or shutil.which('chromium') or shutil.which('chromium-browser')
    if not exe:
        raise RuntimeError('No Edge or Chrome found for PDF export (set PDF_BROWSER to its path). The HTML export still works.')
    d = tempfile.mkdtemp(dir=os.environ.get('TMPDIR'))
    try:
        src, out = os.path.join(d, 'r.html'), os.path.join(d, 'r.pdf')
        open(src, 'w', encoding='utf-8').write(page)
        subprocess.run([exe, '--headless=new', '--disable-gpu', '--no-pdf-header-footer', '--virtual-time-budget=4000', '--user-data-dir=' + os.path.join(d, 'prof'),
                        '--print-to-pdf=' + out, 'file:///' + src.replace(os.sep, '/')], capture_output=True, timeout=120, check=False)
        if not os.path.exists(out):
            raise RuntimeError('The browser did not produce a PDF')
        return open(out, 'rb').read()
    finally:
        shutil.rmtree(d, ignore_errors=True)
