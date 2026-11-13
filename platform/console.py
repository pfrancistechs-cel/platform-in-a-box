"""Platform console: what is live, how the users are doing (SLO), what the pipeline decided, what is in the registry.
Standard library only."""
import json
import os
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROUTER = os.environ.get("ROUTER_URL", "http://router:8080")
PROM = os.environ.get("PROM_URL", "http://prometheus:9090")
REGISTRY = os.environ.get("REGISTRY_URL", "http://registry:5000")
REPO = os.environ.get("REPO", "platform/shop")
RESULTS = os.environ.get("RESULTS_DIR", "/results")
STATE = os.environ.get("STATE_DIR", "/state")
SLO_TARGET = 0.99
ACCEPT = ("application/vnd.oci.image.manifest.v1+json,application/vnd.oci.image.index.v1+json,"
          "application/vnd.docker.distribution.manifest.v2+json,application/vnd.docker.distribution.manifest.list.v2+json")
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get(url, method="GET", headers=None, timeout=3):
    return OPENER.open(urllib.request.Request(url, method=method, headers=headers or {}), timeout=timeout)


def read_json(directory, name, default):
    try:
        with open(os.path.join(directory, name), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def prom_value(query):
    try:
        data = json.load(get(PROM + "/api/v1/query?" + urllib.parse.urlencode({"query": query})))
        result = data["data"]["result"]
        return float(result[0]["value"][1]) if result else None
    except Exception:
        return None


def prom_alerts():
    try:
        alerts = json.load(get(PROM + "/api/v1/alerts"))["data"]["alerts"]
        return [{"name": a["labels"].get("alertname"), "state": a["state"], "summary": a["annotations"].get("summary", "")} for a in alerts]
    except Exception:
        return None


def router_state():
    try:
        return json.load(get(f"{ROUTER}/_router/state?since={time.time() - 30}"))
    except Exception:
        return None


def registry_images():
    try:
        tags = json.load(get(f"{REGISTRY}/v2/{REPO}/tags/list")).get("tags") or []
    except Exception:
        return []
    rows = []
    for tag in sorted(t for t in tags if not t.endswith((".sig", ".att"))):
        try:
            digest = get(f"{REGISTRY}/v2/{REPO}/manifests/{tag}", "HEAD", {"Accept": ACCEPT}).headers.get("Docker-Content-Digest", "")
        except Exception:
            digest = ""
        key = "sha256-" + digest.replace("sha256:", "")
        rows.append({"tag": tag, "digest": digest[7:19], "signed": key + ".sig" in tags, "sbom": key + ".att" in tags})
    return rows


def slo():
    ratio = prom_value("plat:error_ratio")
    errors = prom_value('sum(increase(plat_requests_total{code=~"5.."}[5m]))') or 0.0
    total = prom_value("sum(increase(plat_requests_total[5m]))") or 0.0
    availability = 1 - errors / total if total else None
    budget = None if availability is None else max(0.0, 1 - (1 - availability) / (1 - SLO_TARGET))
    return {"error_ratio_now": ratio, "availability_5m": availability, "budget_left": budget, "target": SLO_TARGET}


def state():
    return {"router": router_state(), "live": read_json(STATE, "live.json", None), "slo": slo(), "alerts": prom_alerts(),
            "runs": list(reversed(read_json(RESULTS, "runs.json", []))), "images": registry_images()}


PAGE = """<!doctype html><meta charset=utf-8><title>Platform console</title>
<style>
body{font-family:system-ui,sans-serif;margin:0;background:#f5f6f8;color:#1b1f24}
header{background:#18222d;color:#fff;padding:14px 22px}header h1{margin:0;font-size:18px}
main{display:grid;gap:16px;padding:16px;grid-template-columns:repeat(auto-fit,minmax(430px,1fr))}
section{background:#fff;border-radius:8px;padding:14px;box-shadow:0 1px 3px #0002}
h2{margin:0 0 8px;font-size:15px}.big{font-size:34px;font-weight:700}.muted{color:#667085;font-size:13px}
.row{display:flex;gap:24px;flex-wrap:wrap}table{border-collapse:collapse;width:100%;font-size:13px}
td,th{padding:5px 6px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:top}
.ok{color:#0a7a3c;font-weight:600}.bad{color:#b42318;font-weight:600}.warn{color:#b54708;font-weight:600}
.pill{padding:1px 8px;border-radius:10px;font-size:12px}.firing{background:#fee4e2;color:#b42318}.fine{background:#d1fadf;color:#05603a}
</style>
<header><h1>Platform in a box: ship, gate, release, observe</h1></header>
<main>
<section><h2>Live service</h2><div class=row>
<div><div class=big id=ver>-</div><div class=muted id=slot>nothing deployed</div></div>
<div><div class=big id=cand>-</div><div class=muted id=weight>no release in progress</div></div></div>
<div class=muted id=digest style="margin-top:8px"></div></section>
<section><h2>Users: SLO 99% of requests succeed</h2><div class=row>
<div><div class=big id=avail>-</div><div class=muted>availability, last 5 min</div></div>
<div><div class=big id=err>-</div><div class=muted>errors right now</div></div>
<div><div class=big id=budget>-</div><div class=muted>error budget left</div></div></div>
<div style="margin-top:8px" id=alerts class=muted>no alert data</div></section>
<section style="grid-column:1/-1"><h2>Pipeline runs (newest first)</h2><table id=runs></table></section>
<section><h2>Registry (platform/shop)</h2><table id=images></table></section>
</main>
<script>
const esc=s=>String(s==null?'':s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const pct=v=>v==null?'-':(v*100).toFixed(v>0.995?2:1)+'%';
async function tick(){try{const s=await (await fetch('api/state')).json();
const l=s.live,r=s.router;
document.getElementById('ver').textContent=l?l.version:'-';
document.getElementById('slot').textContent=l?'slot '+l.slot+' ('+(r&&r.stats&&r.stats[l.slot]?r.stats[l.slot].requests+' requests in the last 30 s':'')+')':'nothing deployed';
document.getElementById('digest').textContent=l?l.digest:'';
document.getElementById('cand').textContent=r&&r.candidate?r.candidate:'-';
document.getElementById('weight').textContent=r&&r.candidate?'canary: '+r.weight+'% of traffic':'no release in progress';
document.getElementById('avail').textContent=pct(s.slo.availability_5m);
document.getElementById('avail').className='big '+(s.slo.availability_5m!=null&&s.slo.availability_5m<s.slo.target?'bad':'');
document.getElementById('err').textContent=pct(s.slo.error_ratio_now);
document.getElementById('budget').textContent=pct(s.slo.budget_left);
document.getElementById('alerts').innerHTML=s.alerts==null?'Prometheus not reachable':(s.alerts.length?s.alerts.map(a=>`<span class="pill ${a.state=='firing'?'firing':'fine'}">${esc(a.name)}: ${esc(a.state)}</span> ${esc(a.summary)}`).join('<br>'):'<span class="pill fine">no alerts</span>');
document.getElementById('runs').innerHTML='<tr><th>time</th><th>version</th><th>result</th><th>stage</th><th>detail</th></tr>'+
 s.runs.map(x=>`<tr><td>${esc(x.time)}</td><td>${esc(x.version)}</td><td class="${x.result=='LIVE'?'ok':(x.result=='ROLLED BACK'?'warn':'bad')}">${esc(x.result)}</td><td>${esc(x.stage)}</td><td>${esc(x.reason)}</td></tr>`).join('');
document.getElementById('images').innerHTML='<tr><th>tag</th><th>digest</th><th>signed</th><th>SBOM</th></tr>'+
 s.images.map(i=>`<tr><td>${esc(i.tag)}</td><td><code>${esc(i.digest)}</code></td><td class="${i.signed?'ok':'bad'}">${i.signed?'yes':'no'}</td><td class="${i.sbom?'ok':'bad'}">${i.sbom?'yes':'no'}</td></tr>`).join('');
}catch(e){}}
tick();setInterval(tick,2500);
</script>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/healthz":
            return self.send(200, b"ok\n", "text/plain")
        if self.path == "/api/state":
            return self.send(200, state())
        if self.path == "/":
            return self.send(200, PAGE.encode(), "text/html; charset=utf-8")
        self.send(404, {"error": "not found"})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8081"))), Handler).serve_forever()
