"""Automated accessibility + layout audit of the live app in headless Edge (axe-core, WCAG 2.2 AA tags).
Usage: python axecheck.py <project_id> [width]   -> prints violations per state/theme and horizontal-overflow offenders."""
import asyncio, json, os, shutil, subprocess, sys, time, urllib.request, aiohttp
APP = os.path.dirname(os.path.abspath(__file__))
PROF = os.path.join(os.environ.get("TEMP", APP), "edge-axe-prof"); PORT = 9464
PID = sys.argv[1]; W = int(sys.argv[2]) if len(sys.argv) > 2 else 1280
AXE = open(os.path.join(APP, "axe.min.js"), encoding="utf-8").read()
STATES = {
    "home": "pr=null;render();",
    "t1": "ui.v1='flow';step=1;render();",
    "t5hyp": "ui.v5='rec';ui.fp='hyp';step=5;render();",
    "t5par": "ui.v5='rec';ui.fp='par';step=5;render();",
    "t5net": "ui.v5='rec';ui.fp='net';ui.gsel='';step=5;render();",
    "t5netsel": "ui.v5='rec';ui.fp='net';ui.gsel=pr.graph.nodes.find(n=>n.n==8).id;step=5;render();",
    "t1figs": "ui.v1='figs';step=1;render();",
    "t6": "step=6;ui.detail=null;render();",
    "t6det": "(async()=>{step=6;await recompute();const x=diffRows()[0];ui.detail=x?x.f.id:null;render()})();",
    "t1list": "ui.v1='list';step=1;render();",
    "t2": "step=2;render();",
    "t3": "ui.rv='ctl';step=3;render();",
    "t4": "step=4;render();",
    "t5rec": "ui.v5='rec';ui.askOpen=false;step=5;render();",
    "t5kw": "ui.v5='kw';step=5;render();",
    "ask": "ui.askOpen=true;step=1;ui.v1='flow';render();",
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
                        if r.get("method") == "Runtime.exceptionThrown": errs.append(str(r["params"]["exceptionDetails"])[:300])

                async def ev(x):
                    r = await send("Runtime.evaluate", expression=x, returnByValue=True, awaitPromise=True)
                    return r.get("result", {}).get("value", r.get("exceptionDetails"))
                await send("Runtime.enable"); await send("Page.enable")
                await send("Emulation.setDeviceMetricsOverride", width=W, height=900, deviceScaleFactor=1, mobile=W < 700)
                await send("Page.navigate", url="http://127.0.0.1:8650/"); await asyncio.sleep(2)
                await ev(f"localStorage.setItem('pid','{PID}')")
                await send("Page.navigate", url="http://127.0.0.1:8650/"); await asyncio.sleep(3)
                await ev("window.__pr=pr")
                await ev(AXE)
                for theme in ("light", "dark"):
                    await ev(f"document.documentElement.dataset.theme='{theme}'")
                    for name, js in STATES.items():
                        await ev("(()=>{if(!pr&&window.__pr)pr=window.__pr;" + js + "return 1})()")
                        await asyncio.sleep(0.6)
                        res = await ev("""axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa','wcag22aa','best-practice']}}).then(r=>({v:r.violations.map(v=>({id:v.id,impact:v.impact,n:v.nodes.length,help:v.help,ex:v.nodes.slice(0,2).map(x=>x.target.join(' ')+' :: '+(x.any[0]||x.all[0]||x.none[0]||{}).message)}))}))""")
                        over = await ev("[...document.querySelectorAll('body *')].filter(e=>{const r=e.getBoundingClientRect();return r.width>0&&r.right>innerWidth+2&&!e.closest('.tw,.flowwrap,.stepper,.askd,pre,dialog')}).slice(0,3).map(e=>e.tagName+'.'+e.className)")
                        vs = (res or {}).get("v", []) if isinstance(res, dict) else []
                        print(f"[{theme} {W}px] {name}: {len(vs)} rule(s) violated; h-overflow: {over}")
                        for v in vs:
                            print(f"   - {v['id']} ({v['impact']}) x{v['n']}: {v['help']}")
                            for e in v["ex"]: print("       ", e[:210])
                print("js errors:", errs)
    finally:
        p.terminate()
asyncio.run(main())
