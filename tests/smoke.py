"""UI smoke test (headless Edge, no model calls): real clicks through home, project open, every tab, search, theme, Ask drawer, Mermaid copy.
Usage: python smoke.py <project_id>   -> prints PASS/FAIL per check and any JS errors."""
import asyncio, json, os, shutil, subprocess, sys, time, urllib.request, aiohttp
APP = os.path.dirname(os.path.abspath(__file__))
PROF = os.path.join(os.environ.get("TEMP", APP), "edge-smoke-prof"); PORT = 9465
PID = sys.argv[1]
fails = []


def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + (" :: " + str(info) if info and not ok else ""))
    if not ok: fails.append(name)


async def main():
    shutil.rmtree(PROF, ignore_errors=True)
    p = subprocess.Popen([r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", "--headless=new", f"--remote-debugging-port={PORT}",
                          f"--user-data-dir={PROF}", "--no-first-run", "--disable-gpu", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(30):
            try:
                tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json")); break
            except Exception:
                time.sleep(1)
        tab = [t for t in tabs if t["type"] == "page"][0]
        async with aiohttp.ClientSession() as ses:
            async with ses.ws_connect(tab["webSocketDebuggerUrl"], max_msg_size=0) as ws:
                n = [0]; errs = []

                async def send(m, **pa):
                    n[0] += 1; i = n[0]; await ws.send_json({"id": i, "method": m, "params": pa})
                    while True:
                        r = await ws.receive_json()
                        if r.get("id") == i: return r.get("result", {})
                        if r.get("method") == "Runtime.exceptionThrown": errs.append(str(r["params"]["exceptionDetails"])[:300])

                async def ev(x):
                    r = await send("Runtime.evaluate", expression=x, returnByValue=True, awaitPromise=True)
                    return r.get("result", {}).get("value", r.get("exceptionDetails"))

                async def click(sel):
                    ok = await ev(f"(()=>{{const e=document.querySelector({json.dumps(sel)});if(!e||e.disabled)return false;e.click();return true}})()")
                    await asyncio.sleep(0.7)
                    return ok
                await send("Runtime.enable"); await send("Page.enable")
                await send("Emulation.setDeviceMetricsOverride", width=1280, height=900, deviceScaleFactor=1, mobile=False)
                await send("Page.navigate", url="http://127.0.0.1:8650/"); await asyncio.sleep(2)
                await ev("localStorage.removeItem('pid');localStorage.removeItem('job')")
                await send("Page.navigate", url="http://127.0.0.1:8650/"); await asyncio.sleep(2.5)
                check("home shows welcome + 3 stat cards", await ev("!!document.querySelector('.welcome')&&document.querySelectorAll('.home-metrics .metric').length==3"))
                check("dropzone present", await ev("!!document.querySelector('#dz')&&!!document.querySelector('#fi')"))
                check("recent project listed", await ev(f"!!document.querySelector('.recent-project[data-open=\"{PID}\"]')"))
                await ev("document.querySelector('#project-search').value='zzzz';document.querySelector('#project-search').dispatchEvent(new Event('input',{bubbles:true}))")
                check("sidebar search filters the list", await ev("document.querySelectorAll('#plist .pitem').length==0&&/No project matches/.test(document.querySelector('#plist').textContent)"))
                await ev("document.querySelector('#project-search').value='';document.querySelector('#project-search').dispatchEvent(new Event('input',{bubbles:true}))")
                check("search cleared restores the list", await ev("document.querySelectorAll('#plist .pitem').length>=1"))
                check("open project from recent list", await click(f".recent-project[data-open=\"{PID}\"]"))
                await asyncio.sleep(1.5)
                check("breadcrumb shows the project", await ev("document.querySelector('#project-title').textContent!=='Overview'"))
                check("header has the three steps Paper, Compare, Result", await ev("[...document.querySelectorAll('#phases .ph')].map(x=>x.textContent.replace(/[^A-Za-z]/g,'')).join()==='Paper,Compare,Result'"))
                PHASE = {1: "paper", 4: "paper", 2: "compare", 6: "result", 3: "result", 5: "result"}
                for s, title in [(4, "Checklist"), (2, "Discrepancy"), (6, "Recommendations"), (3, "Hypotheses"), (5, "Find papers"), (1, "Flowchart")]:
                    await click(f".ph[data-ph=\"{PHASE[s]}\"]")
                    ok = await click(f".step[data-step=\"{s}\"]")
                    await asyncio.sleep(1.0)
                    h2 = await ev("document.querySelector('.stage-intro h2')&&document.querySelector('.stage-intro h2').textContent")
                    cur = await ev("document.querySelector('.step.on')&&document.querySelector('.step.on').dataset.step")
                    check(f"tab {title}: opens, header + aria-current", ok and h2 == title and str(cur) == str(s), (ok, h2, cur))
                    if s != 1:
                        check(f"tab {title}: stat cards use real numbers", await ev("(()=>{const m=[...document.querySelectorAll('#stage-metrics .metric strong')].map(x=>x.textContent);return m.length==3&&m.every(v=>v==='–'||/^[0-9]+$/.test(v))})()"))
                check("flowchart: Copy as Mermaid works", await click("[data-act=mmd]") and await ev("document.querySelector('#toast').textContent.length>0"))
                await ev("step=5;ui.v5='rec';ui.fp='';render()")
                c1 = await click("[data-fp=hyp]")
                h1 = await ev("[/Papers for your hypotheses/.test(document.querySelector('#view').textContent),/Papers for your parameters/.test(document.querySelector('#view').textContent)]")
                c2 = await click("[data-fp=par]")
                h2 = await ev("[/Papers for your hypotheses/.test(document.querySelector('#view').textContent),/Papers for your parameters/.test(document.querySelector('#view').textContent)]")
                check("Find papers: Hypotheses and Parameters are separate sub-views", c1 and c2 and h1 == [True, False] and h2 == [False, True], (c1, h1, c2, h2))
                check("Citation network: renders nodes and edges", await click("[data-fp=net]") and await ev("document.querySelectorAll('svg.gsvg .gn').length>=3&&document.querySelectorAll('svg.gsvg path[marker-end]').length>=1"))
                check("Citation network: clicking a node shows its details", await ev("(()=>{document.querySelector('svg.gsvg .gn').dispatchEvent(new MouseEvent('click',{bubbles:true}));return /Cites \(in this set\)|Cited by papers/.test(document.querySelector('#gview').textContent)})()"))
                check("Citation network: highlight filter dims other papers", await ev("(()=>{const s=document.querySelector('#gfocus');if(s.options.length<2)return false;s.value=s.options[1].value;s.dispatchEvent(new Event('change',{bubbles:true}));return [...document.querySelectorAll('svg.gsvg .gn')].some(g=>g.getAttribute('opacity')=='0.25')})()"))
                await ev("step=2;render()")
                check("Discrepancy: parameters are grouped by category", await ev("document.querySelectorAll('#tb2 tr.gh').length>=2"))
                await ev("(async()=>{const f=pr.fields.find(x=>x.role=='conditions'&&x.verified);window.__f=f;pr.compare[f.id]={value:'999',unit:f.unit||'',tol:'5%'};pr.fields.filter(x=>x.role=='equipment').forEach(x=>{pr.compare[x.id]={value:x.value,unit:x.unit||'',tol:'5%',prefill:true}});await recompute();step=6;ui.detail=null;render()})()")
                await asyncio.sleep(1.0)
                check("Result: two side panels (materials and equipment)", await ev("document.querySelectorAll('.sides .side').length==2"))
                check("Result branch A: a differing parameter gets its own card with Details", await ev("document.querySelectorAll('.rc6 [data-det]').length>=1&&/Branch A/.test(document.querySelector('.sides').textContent)"))
                check("Result branch B: a side with no differences shows hidden-variable branch", await ev("/Branch B/.test(document.querySelector('.sides').textContent)"))
                check("Result: Details opens a separate page", await click(".rc6 [data-det]") and await ev("!!document.querySelector('.detail')&&!document.querySelector('.sides')&&location.hash==='#details'"))
                check("Result: back returns to the recommendations", await click("[data-act=back6]") and await ev("!!document.querySelector('.sides')&&!document.querySelector('.detail')"))
                check("export: Markdown has per-parameter recommendations section and the Lab Mate footer", await ev("(()=>{pr.variable_recs={items:[{name:'x',paper_value:'1',paper_unit:'',user_value:'2',user_unit:'',recommendation:'Do y.',reason:'Because z.',cite:null}],hidden:[]};const m=mdExport();return /Recommendations per parameter/.test(m)&&/Generated by Lab Mate/.test(m)})()"))
                await ev("(()=>{delete pr.compare[window.__f.id];ui.detail=null})();recompute().then(()=>{step=1;ui.v1='flow';render()})")
                await asyncio.sleep(1.0)
                check("brand: title, logo image loaded, favicon", await ev("document.title==='Lab Mate'&&(()=>{const i=document.querySelector('img.lm-l');return i&&i.complete&&i.naturalWidth>0})()&&!!document.querySelector('link[rel=icon]')"))
                check("brand: serif headings, sans body, mono labels from self-hosted fonts", await ev("(async()=>{await document.fonts.ready;const f=x=>getComputedStyle(document.querySelector(x)).fontFamily;return /Newsreader/.test(f('.stage-intro h2'))&&/Plus Jakarta/.test(f('body'))&&/JetBrains/.test(f('.eyebrow'))&&document.fonts.check('16px Newsreader')})()"))
                check("no external requests at load", await ev("performance.getEntriesByType('resource').every(r=>new URL(r.name).origin===location.origin)"))
                check("Check parameters is reachable in the Paper step", await ev("[...document.querySelectorAll('[data-v1]')].some(b=>/Check parameters/.test(b.textContent))"))
                await ev("step=1;ui.v1='flow';render()")
                check("Ask AI drawer opens", await click("[data-act=askopen]") and await ev("!document.querySelector('#askd').hidden"))
                await ev("document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))")
                await asyncio.sleep(0.5)
                check("Escape closes the Ask drawer", await ev("document.querySelector('#askd').hidden"))
                before = await ev("document.documentElement.dataset.theme")
                await click("[data-act=theme]")
                check("theme toggle flips the theme", await ev("document.documentElement.dataset.theme") != before)
                check("close project returns to home", await click("[data-act=close].sm") and await ev("!!document.querySelector('.welcome')"))
                check("no JS errors", not errs, errs[:2])
    finally:
        p.terminate()
    print("FAILED:" if fails else "ALL PASSED", fails if fails else "")
asyncio.run(main())
