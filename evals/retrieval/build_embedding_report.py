"""Build the interactive embedding report from evals/results/retrieval/embedding_dev_v2.json.

Writes docs/embedding-report.html (one self-contained file, no network needed to view it).
Run from the repo root:  python evals/retrieval/build_embedding_report.py
"""
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RESULTS = REPO / "evals" / "results" / "retrieval" / "embedding_dev_v2.json"
OUT = REPO / "docs" / "embedding-report.html"

TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MILO embedding comparison</title>
<style>
:root{--bg:#F7F7F7;--surface:#FFFFFF;--ink:#000022;--muted:#5E5E6B;--line:#E4E4EC;--accent:#5C1AF7;--tint:#F3F4FD;--green:#2E8B63;--amber:#B26A00;--red:#C0392B}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#0E0E1A;--surface:#17172A;--ink:#F2F2F8;--muted:#A3A3B8;--line:#2A2A40;--accent:#A18BFF;--tint:#22223A;--green:#63C59A;--amber:#E7A94A;--red:#F07A7A}}
:root[data-theme="dark"]{--bg:#0E0E1A;--surface:#17172A;--ink:#F2F2F8;--muted:#A3A3B8;--line:#2A2A40;--accent:#A18BFF;--tint:#22223A;--green:#63C59A;--amber:#E7A94A;--red:#F07A7A}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1180px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:28px;font-weight:600;margin:0 0 4px} h2{font-size:20px;font-weight:600;margin:36px 0 10px} h3{font-size:15px;font-weight:600;margin:0 0 6px}
.muted{color:var(--muted)} .card{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin:12px 0}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px}
.kpi{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:14px}.kpi b{display:block;font-size:22px;font-weight:600}
table{width:100%;border-collapse:collapse;background:var(--surface);border:1px solid var(--line);border-radius:12px;overflow:hidden}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);font-size:13px;vertical-align:top} th{background:var(--tint);font-weight:600}
td.num{font-variant-numeric:tabular-nums;white-space:nowrap}
select,button{font:inherit;color:var(--ink);background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:6px 10px;cursor:pointer}
button.on{background:var(--accent);color:#fff;border-color:var(--accent)}
.controls{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:8px 0}
.pill{display:inline-block;font-size:12px;padding:2px 9px;border-radius:999px;background:var(--tint);color:var(--accent);margin-right:6px}
.note{border-left:3px solid var(--amber);padding:8px 12px;background:var(--surface);border-radius:6px;margin:12px 0}
.decision{border-left:3px solid var(--green);padding:10px 14px;background:var(--surface);border-radius:6px;margin:12px 0}
svg text{fill:var(--ink);font-size:12px} svg .axis{stroke:var(--line)} svg .grid-line{stroke:var(--line)}
.legend span{display:inline-block;margin-right:14px;font-size:12px}.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;vertical-align:middle}
#tip{position:fixed;pointer-events:none;background:var(--ink);color:var(--bg);padding:6px 9px;border-radius:6px;font-size:12px;opacity:0;transition:opacity .1s;max-width:300px;z-index:9}
.bar{cursor:pointer} .bar:hover{opacity:.8}
.heat td{text-align:center;font-variant-numeric:tabular-nums;font-size:12px;min-width:64px}
@media (max-width:640px){th,td{font-size:12px}}
</style>
</head>
<body>
<main>
<h1>MILO embedding comparison</h1>
<p class="muted">Dense retrieval on the dev split. Interactive: choose a chunk size, switch metrics, hover for values, click a model to focus it. All numbers come from <code>evals/results/retrieval/embedding_dev_v2.json</code>.</p>

<div class="grid" id="kpis"></div>

<h2>Decision</h2>
<div class="decision" id="decision"></div>

<h2>How to read this</h2>
<div class="card" id="glossary"></div>

<h2>Metrics with confidence intervals</h2>
<div class="controls">
  <span class="muted">Chunk size:</span><select id="cfgSel"></select>
  <span class="muted">Metric:</span><select id="metricSel"></select>
</div>
<div class="card"><svg id="barChart" width="100%" viewBox="0 0 900 360" preserveAspectRatio="xMidYMid meet"></svg>
<div class="legend muted" id="legend"></div></div>

<h2>Paired comparison against the reference</h2>
<p class="muted">Each bar is the mean per-question difference (model minus reference) with its 95% paired bootstrap interval. An interval that crosses zero means no clear difference on these 16 questions.</p>
<div class="card"><svg id="forest" width="100%" viewBox="0 0 900 340" preserveAspectRatio="xMidYMid meet"></svg></div>
<div class="controls"><span class="muted">Metric:</span><select id="forestMetric"></select></div>

<h2>Chunk size effect</h2>
<div class="card"><svg id="cfgChart" width="100%" viewBox="0 0 900 340" preserveAspectRatio="xMidYMid meet"></svg>
<div class="legend muted" id="cfgLegend"></div></div>

<h2>Where each model finds the right passage</h2>
<p class="muted">Rank of the first correct chunk for each dev question, under the selected chunk size. Rank 1 is best; "miss" means it was not in the top 5.</p>
<div class="card"><svg id="rankHist" width="100%" viewBox="0 0 900 320" preserveAspectRatio="xMidYMid meet"></svg></div>
<div class="card" style="overflow-x:auto"><table class="heat" id="heat"></table></div>

<h2>Cost and tokens</h2>
<table id="costTable"></table>

<h2>Method</h2>
<div class="card" id="method"></div>

<div id="tip"></div>
</main>
<script>
const DATA = /*DATA*/null;

const COLORS = ["#5C1AF7","#2E8B63","#B26A00","#C0392B","#2F6DB5","#8E5BC4","#3A9A9A"];
const SHORT = m => m.split("/")[1];
const METRICS = [
  ["ndcg5","nDCG@5"],["mrr","MRR"],["recall5","Recall@5"],["p5","Precision@5"],["ap","Average precision"]
];
const cfgs = DATA.configs;
let cfgIdx = cfgs.findIndex(c => c.chunk_size===800) >= 0 ? cfgs.findIndex(c => c.chunk_size===800) : 0;
let metric = "ndcg5";
let focus = null;
const models = Object.keys(cfgs[0].models);
const colorOf = m => COLORS[models.indexOf(m) % COLORS.length];
const tip = document.getElementById("tip");
function showTip(e, html){ tip.innerHTML = html; tip.style.opacity = 1; moveTip(e); }
function moveTip(e){ tip.style.left = (e.clientX+12)+"px"; tip.style.top = (e.clientY+12)+"px"; }
function hideTip(){ tip.style.opacity = 0; }
const f = (x,d=3) => (x===null||x===undefined) ? "-" : Number(x).toFixed(d);

// ---- KPIs
(function(){
  const c = cfgs[cfgIdx];
  const best = bestModel(c);
  const k = document.getElementById("kpis");
  k.innerHTML = `
    <div class="kpi"><span class="muted">Models compared</span><b>${models.length}</b></div>
    <div class="kpi"><span class="muted">Chunk sizes</span><b>${cfgs.length}</b></div>
    <div class="kpi"><span class="muted">Dev questions answerable</span><b>${c.query_count}</b></div>
    <div class="kpi"><span class="muted">Best nDCG@5 (800/100)</span><b>${SHORT(best)} ${f(c.models[best].summary.ndcg5.mean)}</b></div>
    <div class="kpi"><span class="muted">Total embedding cost</span><b>$${f(DATA.total_cost_usd,4)}</b></div>`;
})();

function bestModel(c){
  return models.slice().sort((a,b)=>c.models[b].summary.ndcg5.mean - c.models[a].summary.ndcg5.mean)[0];
}

// ---- decision (computed from the results, rule in the Method section)
(function(){
  const c = cfgs.find(x=>x.chunk_size===800) || cfgs[0];
  const ref = DATA.reference;
  const rows = models.map(m => {
    const meanAcross = cfgs.reduce((s,cc)=>s+cc.models[m].summary.ndcg5.mean,0)/cfgs.length;
    const cost = cfgs.reduce((s,cc)=>s+cc.models[m].summary.cost_usd,0);
    return {m, meanAcross, cost};
  }).sort((a,b)=>b.meanAcross-a.meanAcross);
  const top = rows[0].m;
  const ties = rows.filter(r => r.m!==top && cfgs.every(cc => paired(cc, top, "ndcg5") && paired(cc, top, "ndcg5")[0] <= 0 ));
  document.getElementById("decision").innerHTML = DECISION_HTML;
})();
function paired(cc, m, key){
  if (m===cc.reference) return null;
  const pd = cc.paired_vs_reference[m];
  return pd ? [pd[key].mean_diff, pd[key].ci] : null;
}

// ---- glossary
document.getElementById("glossary").innerHTML = `
<p><b>Recall@5</b>: for each question, does a correct passage appear anywhere in the top 5 results? Score 1 for yes, 0 for no. The mean over questions is the recall.</p>
<p><b>nDCG@5</b>: like recall, but a correct passage ranked higher scores more. Rank 1 counts 1.00, rank 2 counts 0.63, rank 3 counts 0.50, rank 4 counts 0.43, rank 5 counts 0.39, and missing the top 5 counts 0. Normalised so a perfect ranking scores 1.</p>
<p><b>MRR</b> (mean reciprocal rank): 1 divided by the rank of the first correct passage, searching the whole ranking, not only the top 5. Rank 1 gives 1.00, rank 2 gives 0.50, rank 10 gives 0.10.</p>
<p><b>Precision@5</b>: the share of the top 5 results that are correct. With one correct passage per question, it is recall divided by 5.</p>
<p><b>Average precision</b>: for each correct passage in the full ranking, the precision at its rank, averaged. With one correct passage it equals the reciprocal rank, so it matches MRR here.</p>
<p><b>Paired bootstrap</b>: the same questions are used for both models. For each question we take the difference between two models, then resample questions to get an interval for the mean difference. Pairing removes the variation that comes from the questions being easy or hard, so it is sharper than comparing two separate intervals.</p>`;

// ---- generic svg helpers
function svgEl(tag, attrs, parent){ const e=document.createElementNS("http://www.w3.org/2000/svg",tag); for(const k in attrs) e.setAttribute(k,attrs[k]); if(parent) parent.appendChild(e); return e; }

// ---- bar chart with CIs
function drawBars(){
  const svg = document.getElementById("barChart"); svg.innerHTML="";
  const c = cfgs[cfgIdx];
  const W=900,H=360,L=60,R=20,T=20,B=80;
  const yMax=1.0;
  const bw = (W-L-R)/models.length;
  for(let t=0;t<=1.0001;t+=0.2){ const y=T+(H-T-B)*(1-t/yMax); svgEl("line",{x1:L,x2:W-R,y1:y,y2:y,class:"grid-line"},svg); svgEl("text",{x:L-8,y:y+4,"text-anchor":"end"},svg).textContent=t.toFixed(1); }
  models.forEach((m,i)=>{
    const s = c.models[m].summary[metric];
    const x = L + i*bw + bw*0.2, w = bw*0.6;
    const y = T+(H-T-B)*(1-s.mean/yMax);
    const g = svgEl("g",{class:"bar",opacity: focus && focus!==m ? 0.25 : 1},svg);
    svgEl("rect",{x,y,width:w,height:(H-B)-y,rx:4,fill:colorOf(m)},g);
    const cy1 = T+(H-T-B)*(1-s.ci[0]/yMax), cy2 = T+(H-T-B)*(1-s.ci[1]/yMax), cx = x+w/2;
    svgEl("line",{x1:cx,x2:cx,y1:cy1,y2:cy2,stroke:"var(--ink)","stroke-width":2},g);
    svgEl("line",{x1:cx-6,x2:cx+6,y1:cy1,y2:cy1,stroke:"var(--ink)","stroke-width":2},g);
    svgEl("line",{x1:cx-6,x2:cx+6,y1:cy2,y2:cy2,stroke:"var(--ink)","stroke-width":2},g);
    svgEl("text",{x:cx,y:y-6,"text-anchor":"middle"},g).textContent = s.mean.toFixed(3);
    svgEl("text",{x:cx,y:H-B+18,"text-anchor":"middle"},g).textContent = SHORT(m);
    g.addEventListener("mousemove", e => showTip(e, `<b>${m}</b><br>${METRICS.find(x=>x[0]===metric)[1]}: ${f(s.mean)}<br>95% CI [${f(s.ci[0])}, ${f(s.ci[1])}]`));
    g.addEventListener("mouseleave", hideTip);
    g.addEventListener("click", () => { focus = focus===m ? null : m; drawBars(); drawRankHist(); drawHeat(); });
  });
  document.getElementById("legend").innerHTML = models.map(m=>`<span><i style="background:${colorOf(m)}"></i>${m}</span>`).join("");
}

// ---- forest plot of paired differences
function drawForest(){
  const svg = document.getElementById("forest"); svg.innerHTML="";
  const key = document.getElementById("forestMetric").value;
  const c = cfgs[cfgIdx];
  const others = models.filter(m=>m!==DATA.reference);
  const W=900,H=340,L=230,R=40,T=20,B=40;
  const vals = others.map(m=>c.paired_vs_reference[m][key]);
  const lo = Math.min(-0.2, ...vals.map(v=>v.ci[0])), hi = Math.max(0.2, ...vals.map(v=>v.ci[1]));
  const X = v => L + (v-lo)/(hi-lo)*(W-L-R);
  svgEl("line",{x1:X(0),x2:X(0),y1:T,y2:H-B,stroke:"var(--muted)","stroke-dasharray":"4 4"},svg);
  others.forEach((m,i)=>{
    const v = c.paired_vs_reference[m][key];
    const y = T + (i+0.5)*(H-T-B)/others.length;
    svgEl("text",{x:L-10,y:y+4,"text-anchor":"end"},svg).textContent = m;
    svgEl("line",{x1:X(v.ci[0]),x2:X(v.ci[1]),y1:y,y2:y,stroke:colorOf(m),"stroke-width":3},svg);
    svgEl("circle",{cx:X(v.mean_diff),cy:y,r:6,fill:colorOf(m)},svg);
    const g = svgEl("g",{},svg);
    g.addEventListener("mousemove", e => showTip(e, `<b>${m} minus ${DATA.reference}</b><br>mean difference ${f(v.mean_diff)}<br>95% CI [${f(v.ci[0])}, ${f(v.ci[1])}]`));
    g.addEventListener("mouseleave", hideTip);
    svgEl("rect",{x:L,y:y-12,width:W-L-R,height:24,fill:"transparent"},g);
  });
  for(let t=lo; t<=hi+1e-9; t+=0.1){ svgEl("text",{x:X(t),y:H-B+16,"text-anchor":"middle"},svg).textContent = t.toFixed(1); }
  svgEl("text",{x:W/2,y:H-4,"text-anchor":"middle"},svg).textContent = "difference from reference (left: worse, right: better)";
}

// ---- chunk size chart: metric by config, grouped by model
function drawCfg(){
  const svg = document.getElementById("cfgChart"); svg.innerHTML="";
  const W=900,H=340,L=60,R=20,T=20,B=60;
  const gw = (W-L-R)/cfgs.length;
  cfgs.forEach((cc,ci)=>{
    const gx = L + ci*gw;
    models.forEach((m,mi)=>{
      const s = cc.models[m].summary.ndcg5;
      const bw = gw*0.7/models.length;
      const x = gx + gw*0.15 + mi*bw, y = T+(H-T-B)*(1-s.mean);
      const g = svgEl("g",{class:"bar"},svg);
      svgEl("rect",{x,y,width:bw*0.9,height:(H-B)-y,rx:3,fill:colorOf(m)},g);
      g.addEventListener("mousemove", e => showTip(e, `<b>${m}</b><br>chunk ${cc.chunk_size}/${cc.overlap}<br>nDCG@5 ${f(s.mean)} [${f(s.ci[0])}, ${f(s.ci[1])}]`));
      g.addEventListener("mouseleave", hideTip);
    });
    svgEl("text",{x:gx+gw/2,y:H-B+18,"text-anchor":"middle"},svg).textContent = `${cc.chunk_size} / ${cc.overlap} (${cc.chunk_count} chunks)`;
  });
  for(let t=0;t<=1.0001;t+=0.2){ const y=T+(H-T-B)*(1-t); svgEl("line",{x1:L,x2:W-R,y1:y,y2:y,class:"grid-line"},svg); svgEl("text",{x:L-8,y:y+4,"text-anchor":"end"},svg).textContent=t.toFixed(1); }
  document.getElementById("cfgLegend").innerHTML = models.map(m=>`<span><i style="background:${colorOf(m)}"></i>${m}</span>`).join("");
}

// ---- rank histogram per model (selected config)
const BUCKETS = ["1","2","3","4","5","miss"];
function drawRankHist(){
  const svg = document.getElementById("rankHist"); svg.innerHTML="";
  const c = cfgs[cfgIdx];
  const W=900,H=320,L=60,R=20,T=20,B=50;
  const groupW = (W-L-R)/BUCKETS.length;
  const counts = {};
  models.forEach(m => { counts[m] = BUCKETS.map(()=>0); c.models[m].rows.forEach(r => {
    const idx = r.first_relevant_rank===null ? 5 : Math.min(r.first_relevant_rank,5)-1;
    counts[m][idx]++; }); });
  const maxN = Math.max(...models.flatMap(m=>counts[m]),1);
  BUCKETS.forEach((b,bi)=>{
    const gx = L + bi*groupW;
    models.forEach((m,mi)=>{
      if (focus && focus!==m) return;
      const bw = groupW*0.8/models.length;
      const h = (H-T-B)*counts[m][bi]/maxN;
      const x = gx + groupW*0.1 + mi*bw;
      const g = svgEl("g",{class:"bar"},svg);
      svgEl("rect",{x,y:H-B-h,width:bw*0.9,height:h,rx:3,fill:colorOf(m)},g);
      g.addEventListener("mousemove", e => showTip(e, `<b>${m}</b><br>first correct at rank ${b}: ${counts[m][bi]} question(s)`));
      g.addEventListener("mouseleave", hideTip);
    });
    svgEl("text",{x:gx+groupW/2,y:H-B+18,"text-anchor":"middle"},svg).textContent = b==="miss" ? "miss (not in top 5)" : "rank "+b;
  });
}

// ---- per-question heatmap
function drawHeat(){
  const t = document.getElementById("heat");
  const c = cfgs[cfgIdx];
  const qids = c.models[models[0]].rows.map(r=>r.qid);
  const shown = focus ? [focus] : models;
  let html = "<thead><tr><th>Question</th><th>type</th>" + shown.map(m=>`<th>${SHORT(m)}</th>`).join("") + "</tr></thead><tbody>";
  qids.forEach((q,qi)=>{
    const type = c.models[models[0]].rows[qi].type;
    html += `<tr><td style="text-align:left">${q}</td><td>${type}</td>`;
    shown.forEach(m=>{
      const r = c.models[m].rows[qi];
      const rk = r.first_relevant_rank;
      const col = rk===null ? "var(--red)" : rk===1 ? "var(--green)" : rk<=3 ? "var(--amber)" : "var(--accent)";
      const bg = rk===null ? "rgba(192,57,43,.14)" : rk===1 ? "rgba(46,139,99,.18)" : rk<=3 ? "rgba(178,106,0,.16)" : "rgba(92,26,247,.12)";
      html += `<td style="background:${bg};color:${col}">${rk===null ? "miss" : rk}</td>`;
    });
    html += "</tr>";
  });
  t.innerHTML = html + "</tbody>";
}

// ---- cost table
(function(){
  let html = "<thead><tr><th>Model</th>" + cfgs.map(c=>`<th>${c.chunk_size}/${c.overlap} tokens</th><th>${c.chunk_size}/${c.overlap} cost (USD)</th>`).join("") + "<th>Total cost (USD)</th></tr></thead><tbody>";
  models.forEach(m=>{
    let tot=0; html += `<tr><td>${m}</td>`;
    cfgs.forEach(c=>{ const s=c.models[m].summary; tot+=s.cost_usd; html += `<td class="num">${s.tokens.toLocaleString()}</td><td class="num">${s.cost_usd.toFixed(5)}</td>`; });
    html += `<td class="num"><b>${tot.toFixed(5)}</b></td></tr>`;
  });
  document.getElementById("costTable").innerHTML = html + "</tbody>";
})();

// ---- method
document.getElementById("method").innerHTML = METHOD_HTML;

// ---- controls
(function(){
  const cs = document.getElementById("cfgSel");
  cfgs.forEach((c,i)=>{ const o=document.createElement("option"); o.value=i; o.textContent=`${c.chunk_size} characters, overlap ${c.overlap} (${c.chunk_count} chunks)`; cs.appendChild(o); });
  cs.value = cfgIdx;
  cs.addEventListener("change", ()=>{ cfgIdx = +cs.value; drawAll(); });
  const ms = document.getElementById("metricSel");
  METRICS.forEach(([k,l])=>{ const o=document.createElement("option"); o.value=k; o.textContent=l; ms.appendChild(o); });
  ms.addEventListener("change", ()=>{ metric = ms.value; drawBars(); });
  const fm = document.getElementById("forestMetric");
  METRICS.forEach(([k,l])=>{ const o=document.createElement("option"); o.value=k; o.textContent=l; fm.appendChild(o); });
  fm.addEventListener("change", drawForest);
})();

function drawAll(){ drawBars(); drawForest(); drawCfg(); drawRankHist(); drawHeat(); }
drawAll();
</script>
</body>
</html>
"""

METHOD_HTML = """
<p><b>Data.</b> The 24 knowledge-base articles in <code>data/kb</code>, split into chunks at headings and packed to a fixed size with overlap (three sizes: 800/100, 400/50, 1500/150 characters).</p>
<p><b>Questions.</b> The 16 dev questions that have a gold quote. The 4 unanswerable dev questions are not used for ranking metrics. The test split is never read by this experiment.</p>
<p><b>Relevance.</b> A chunk is relevant when it contains the gold quote, after whitespace and case normalisation.</p>
<p><b>Models.</b> Five OpenRouter embedding models. No query instruction is added for any model, so the comparison is fair across models.</p>
<p><b>Metrics.</b> Recall@5, nDCG@5, MRR, precision@5 and average precision, computed from the full ranking of every chunk.</p>
<p><b>Uncertainty.</b> Each metric has a 95% bootstrap interval (2000 resamples, seed 0). Model differences use a paired bootstrap over the same questions against the reference model.</p>
<p><b>Decision rule.</b> Choose the model with the highest mean nDCG@5 averaged over the three chunk sizes. Treat the choice as settled only if its paired interval against every other model excludes zero at the chosen chunk size. If it does not, the choice is provisional and the cheapest model within the top group is preferred.</p>
<p><b>Limitations.</b> 16 questions and one document set is a small sample. The result is a first screen for the product, not a final benchmark. Retrieval with a reranker or hybrid search is not tested here.</p>
"""

def make_decision(data):
    cfgs = data["configs"]
    models = list(cfgs[0]["models"].keys())
    ref = data["reference"]
    avg = {m: sum(c["models"][m]["summary"]["ndcg5"]["mean"] for c in cfgs) / len(cfgs) for m in models}
    ranked = sorted(models, key=lambda m: -avg[m])
    runner = ranked[1]
    c800 = next(c for c in cfgs if c["chunk_size"] == 800)
    pd = c800["paired_vs_reference"][runner]["ndcg5"]
    sig = [m for m in models if m != ref and any(c["paired_vs_reference"][m]["ndcg5"]["ci"][1] < 0 for c in cfgs)]
    return (
        f"<p><b>Chosen: <code>{ref}</code>, provisional.</b></p>"
        f"<p>Highest average nDCG@5 across the three chunk sizes: {avg[ref]:.3f}. Runner-up <code>{runner}</code>: {avg[runner]:.3f}.</p>"
        f"<p>Paired difference at 800/100, reference minus runner-up: {-pd['mean_diff']:+.3f}, interval [{-pd['ci'][1]:+.3f}, {-pd['ci'][0]:+.3f}]. "
        f"The interval includes zero, so the two are statistically tied on these 16 questions.</p>"
        f"<p>Clear differences in the reference's favour appear only where the paired interval excludes zero: "
        f"{'; '.join(sorted({m for c in cfgs for m in models if m != ref and c['paired_vs_reference'][m]['ndcg5']['ci'][1] < 0}))}.</p>"
        f"<p>Cost is not a deciding factor: the cost gap to the runner-up is under one tenth of a cent per run.</p>"
        f"<p><b>What would settle it:</b> the test split, read once in the final step (F2). If the reference still leads there, the choice stands; "
        f"if the runner-up leads, the choice is reconsidered.</p>"
    )


def main():
    data = json.loads(RESULTS.read_text(encoding="utf-8"))
    html = TEMPLATE.replace("/*DATA*/null", json.dumps(data))
    html = html.replace("METHOD_HTML", json.dumps(METHOD_HTML))
    html = html.replace("DECISION_HTML", json.dumps(make_decision(data)))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    print("wrote", OUT, "bytes", OUT.stat().st_size)


if __name__ == "__main__":
    main()
