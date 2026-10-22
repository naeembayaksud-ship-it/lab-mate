"""Screenshots of the tabs (headless Edge, no LLM). Usage: python tabshot.py <project_id> <tag> [shotname ...]"""
import asyncio, base64, json, os, shutil, subprocess, sys, time, urllib.request, aiohttp
APP = os.path.dirname(os.path.abspath(__file__)); SHOTS = os.path.join(APP, "shots")
PROF = os.path.join(os.environ.get("TEMP", APP), "edge-tab-prof"); PORT = 9463
PID = sys.argv[1]; TAG = sys.argv[2]; ONLY = set(sys.argv[3:])
STATES = {
    "home": "pr=null;render();",
    "t1": "ui.v1='flow';step=1;render();",
    "t1figs": "ui.v1='figs';step=1;render();",
    "t1figsel": "(async()=>{ui.v1='figs';ui.fig='g2';step=1;await recompute();render()})();",
    "t5relmock": "pr.relation_log=[{q:'What drives Mn?',results:'Mn',brief:'Pressure dominates.',time:'now',pages_read:[3],not_discussed:'',kw:{base:42,source:'Scopus',result_kw:'molar mass',label:'keyword co-occurrence',context:'PVDF'},parameter:[{name:'VDF gas pressure',direction:'up',evidence:'quantitative',effect:'Mn rises 5 to 15 bar',quote:'',page:3,from_list:true,kw:{n:10,pct:23.8}},{name:'Residence time',direction:'up',evidence:'qualitative',effect:'small',kw:{n:4,pct:9.5}},{name:'Initiator',direction:'up',evidence:'guess',effect:'?',kw:{n:2,pct:4.8}},{name:'Membrane pore size',direction:'up',evidence:'guess',effect:'?'}]}];ui.v5='rel';ui.askOpen=false;step=5;render();",
    "t5hyp": "ui.v5='rec';ui.fp='hyp';ui.askOpen=false;step=5;render();",
    "t5par": "ui.v5='rec';ui.fp='par';ui.askOpen=false;step=5;render();",
    "t5net": "ui.v5='rec';ui.fp='net';ui.gsel='';ui.askOpen=false;step=5;render();",
    "t5netsel": "ui.v5='rec';ui.fp='net';(()=>{const c={};pr.graph.edges.forEach(e=>{c[e.a]=(c[e.a]||0)+1;c[e.b]=(c[e.b]||0)+1});ui.gsel=Object.entries(c).sort((a,b)=>b[1]-a[1])[0][0]})();ui.askOpen=false;step=5;render();",
    "t6": "step=6;ui.detail=null;render();",
    "t6det": "(async()=>{step=6;await recompute();const x=diffRows()[0];ui.detail=x?x.f.id:null;render()})();",
    "t2shut": "(async()=>{await go(2);await new Promise(r=>setTimeout(r,500));const t=document.querySelectorAll('.gtog');t[0].click();t[2].click();return 1})();",
    "t1list": "ui.v1='list';step=1;render();",
    "t4": "step=4;render();",
    "t2": "step=2;render();",
    "t3": "ui.rv='ctl';step=3;render();",
    "t3hyp": "ui.rv='hyp';step=3;render();",
    "t3plan": "ui.rv='plan';step=3;render();",
    "t3more": "ui.rv='more';step=3;render();",
    "t5kw": "ui.v5='kw';ui.kw='temperature, molecular weight';ui.kwc='PMMA';step=5;render();",
    "t5lit": "ui.v5='lit';step=5;render();",
    "t5rec": "ui.v5='rec';ui.askOpen=false;step=5;render();",
    "t5recown": "ui.v5='rec';ui.ownq='chain transfer agent PVDF';ui.askOpen=false;step=5;render();",
    "t5rel": "ui.v5='rel';ui.askOpen=false;step=5;render();",
    "t5dock": "ui.v5='rec';ui.askOpen=true;step=5;render();",
    "t5litmock": "pr.library.results={built:'2026-10-21',n_paper:2,n_read:2,ids:pr.library.papers.map(p=>p.id),proposed:3,no_evidence:1,dropped_already_in_paper:1,quotes_unverified:1,per_paper:[{id:'l2',title:'Continuous Flow Synthesis of PVDF',variables:['Residence time','VDF gas pressure']}],variables:[{name:'Residence time',reason:'Controls conversion in both papers.',suggested_range:'1-10 min',unit:'min',paper_count:2,evidence:[{paper:'l2',paper_name:'Continuous Flow Synthesis of PVDF',page:1,quote:'190kgm-3h-1 at just 15bar gas pressure',range:'1-10 min',effect:'yield rises'}]}]};ui.v5='lit';step=5;render();",
}


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
                        if r.get("method") == "Runtime.exceptionThrown": errs.append(str(r["params"]["exceptionDetails"])[:500])

                async def ev(x):
                    r = await send("Runtime.evaluate", expression=x, returnByValue=True, awaitPromise=True)
                    return r.get("result", {}).get("value", r.get("exceptionDetails"))

                async def shot(name, theme="light", full=True, w=int(os.environ.get("W", "1280"))):
                    await ev(f"document.documentElement.dataset.theme='{theme}'")
                    await send("Emulation.setDeviceMetricsOverride", width=w, height=900, deviceScaleFactor=1, mobile=False)
                    await asyncio.sleep(0.5)
                    h = 900
                    if full:
                        h = await ev("(()=>{document.querySelectorAll('main,#app,#view').forEach(e=>{e.style.overflow='visible';e.style.height='auto';e.style.maxHeight='none'});return Math.max(document.documentElement.scrollHeight,900)})()")
                        await send("Emulation.setDeviceMetricsOverride", width=w, height=min(int(h) + 20, 6000), deviceScaleFactor=1, mobile=False)
                        await asyncio.sleep(0.5)
                    r = await send("Page.captureScreenshot", format="png")
                    out = os.path.join(SHOTS, f"tab_{TAG}_{name}_{theme}.png"); open(out, "wb").write(base64.b64decode(r["data"])); print("shot", out, h)
                await send("Runtime.enable"); await send("Page.enable")
                await send("Page.navigate", url="http://127.0.0.1:8650/"); await asyncio.sleep(2)
                await ev(f"localStorage.setItem('pid','{PID}')")
                await send("Page.navigate", url="http://127.0.0.1:8650/"); await asyncio.sleep(3)
                for name, js in STATES.items():
                    if ONLY and name not in ONLY: continue
                    if name == "t5kw":
                        await ev("(async()=>{ui.cooc=await api('POST','/cooccur',{terms:['temperature','molecular weight','PMMA'],context:''})})()")
                    if name == "home":
                        await ev("(()=>{window.__pr=pr;" + js + "return 1})()")
                    else:
                        r_ = await ev("(()=>{if(!pr&&window.__pr)pr=window.__pr;" + js + "return 1})()")
                        if r_ != 1: print("EVAL PROBLEM", name, str(r_)[:300])
                    await asyncio.sleep(0.8)
                    await shot(name)
                    await shot(name, "dark")
                if not ONLY or "ask" in ONLY:
                    await ev("(()=>{if(!pr&&window.__pr)pr=window.__pr;step=1;ui.v1='flow';ui.askOpen=true;render();return 1})()")
                    await asyncio.sleep(0.8); await shot("ask", "light", full=False)
                print("errors:", errs)
    finally:
        p.kill()
asyncio.run(main())
