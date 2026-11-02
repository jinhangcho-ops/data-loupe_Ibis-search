"""F22: 지표 단위 흐름(사전 unit → 문장 지표 탭 결과·대시보드 KPI unit_text·사전 목록), 지표 목록 접기, 자주 쓴 지표 묶음.
"무조건 원"의 뿌리: 금액 역할(amount)이 이름 단서 없이 아무 숫자 컬럼(인원수·횟수)에나 맞춰져, 단위가 원인 매출 지표가 쓸 수 있다고 나오고 그 컬럼으로 미리 채워졌다."""
import json
import shutil
import subprocess

import pytest

from conftest import ROOT
from core import server
from core import use_counts
from metrics import catalog_app
from metrics import metric_catalog as cat

need_node = pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")


# ---------- ① 매칭: 금액이 아닌 숫자 컬럼에 원 단위 지표를 맞추지 않는다 ----------

def test_금액_단서_없는_숫자_컬럼은_매출_지표에_맞추지_않는다():
    got = cat.available({"user_id": "text", "인원수": "num", "신고 횟수": "num", "day": "date"})
    for i in ("gross_revenue", "arpu", "arppu", "gmv", "aov", "mrr"):
        assert not got[i]["usable"] and "금액" in got[i]["reason"], i
    assert got["uv"]["usable"] and got["dau"]["usable"]  # 사람 수 지표는 그대로


def test_금액_단서가_있으면_원_단위_지표를_쓴다():
    got = cat.available({"user_id": "text", "인원수": "num", "결제금액": "num"})
    assert got["gross_revenue"]["usable"] and got["gross_revenue"]["suggest"] == {"금액": "결제금액"}
    assert cat.available({"u": "text", "price_krw": "num"})["arpu"]["suggest"]["금액"] == "price_krw"


def test_사전_항목의_단위는_명_회_원이_섞여_있다():
    units = {e["id"]: e["unit"] for e in cat.ENTRIES}
    assert units["uv"] == "명" and units["pv"] == "회" and units["gross_revenue"] == "원" and units["asd"] == "초"


def test_내_지표로_저장하면_사전_단위를_그대로(client):
    cols = {"u": "text", "ts": "date"}
    r = client.post("/api/catalog/save", json={"id": "uv", "slots": {"사용자": "u"}, "scope": {"table": "t", "key": "url:x|"}, "columns": cols})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["metric"]["unit"] == "명"
    r = client.post("/api/catalog/save", json={"id": "retention_classic", "slots": {"사용자": "u", "시각": "ts"}, "scope": {"table": "t", "key": "url:x|"}, "columns": cols})
    assert r.status_code == 200 and r.get_json()["metric"]["unit"] == "%"


# ---------- ③ 자주 쓴 지표 ----------

def test_자주_쓴_지표는_대시보드_카드가_먼저_그다음_사용_횟수():
    mine = [{"id": "m1", "template": "catalog:dau"}, {"id": "m2", "template": "e01"}]
    cards = [{"title": "내 카드", "source": {"body": {"names": ["m1"]}}}, {"title": "arpu"}, {"title": "m2 카드", "source": {"metric_id": "m2"}}]
    assert cat.frequent(cards, mine, ["pv", "dau", "없는항목"]) == ["dau", "arpu", "pv"]
    assert cat.frequent([], [], []) == []


def test_frequent_라우트는_FREE이고_계산할_때마다_센다(client, monkeypatch):
    assert "/api/catalog/frequent" in server.FREE
    monkeypatch.setattr(catalog_app, "scan_ask", lambda *a, **k: None)
    monkeypatch.setattr(catalog_app, "compute_entry", lambda *a, **k: {"value": 1})
    for _ in range(2):
        assert client.post("/api/catalog/compute", json={"id": "pv", "slots": {}, "body": {"base": "t"}}).status_code == 200
    assert client.post("/api/catalog/compute", json={"id": "uv", "slots": {}, "body": {"base": "t"}}).status_code == 200
    assert use_counts.load()["catalog"]["pv"]["count"] == 2
    assert client.get("/api/catalog/frequent").get_json()["ids"] == ["pv", "uv"]


# ---------- 화면(node) ----------

def run_node(tmp_path, files, body, head="globalThis.SERVER = {};\n"):
    script = tmp_path / "case.js"
    script.write_text(head + "".join((ROOT / "static" / f).read_text(encoding="utf-8") for f in files) + body, encoding="utf-8")
    done = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr[-3000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


@need_node
def test_목록_묶음_자주_쓴_지표가_맨_위_찾기는_맞는_영역만(tmp_path):
    c = cat.catalog()
    got = run_node(tmp_path, ("query/sentences.js", "query/metric_catalog.js"),
                   "const cat = %s;\nconsole.log(JSON.stringify({ plain: mcGroups(cat).map(g => g.id), freq: mcGroups(cat, ['dau', 'arpu']).slice(0, 1).map(g => [g.id, g.entries.map(e => e.id)]),"
                   " found: mcGroups(cat, ['dau'], 'arppu').map(g => [g.id, g.entries.map(e => e.id)]),"
                   " units: ['uv', 'gross_revenue', 'retention_classic', 'gini'].map(i => mcUnit(cat.entries.find(e => e.id === i))) }));" % json.dumps(c))
    assert got["plain"] == [a["id"] for a in cat.AREAS]  # 자주 쓴 것이 없으면 묶음도 없다
    assert got["freq"] == [["frequent", ["dau", "arpu"]]]
    assert got["found"] == [["revenue", ["pu", "arppu"]]]  # pu는 설명에 ARPPU가 든다
    assert got["units"] == ["명", "원", "%", ""]


@need_node
def test_사전_목록은_접힌_영역이고_자주_쓴_것만_펼친다(tmp_path):
    src = (ROOT / "static" / "query" / "metric_catalog.js").read_text(encoding="utf-8")
    assert 'el("details"' in src and 'el("summary"' in src


@need_node
def test_KPI_카드에_단위를_싣고_지표_카드는_지표의_형식을_쓴다(tmp_path):
    prelude = (ROOT / "tests_pytest" / "test_fix_audit22_dash.py").read_text(encoding="utf-8").split('PRELUDE = """')[1].split('"""')[0]
    body = """
var Funcs={cat:null,load:()=>new Promise(()=>{}),loadSupport:()=>Promise.resolve()};
var tick=()=>new Promise(r=>setTimeout(r,10));
(async()=>{
const cards=[], root=new E('div'), m=initCardMenu(root,{metricNames:()=>["매출"],layout:()=>({}),add:async c=>cards.push(c)});
const all=[]; const walk=n=>{ if(!(n instanceof E)) return; all.push(n); n.children.forEach(walk); }; walk(root);
const sel=all.filter(n=>n.tag==='select'), table=sel[0], metric=all.find(n=>n.title&&n.title.startsWith('지표 하나'));
const unit=all.find(n=>n.placeholder==='단위 (예: 명, 회, 원)');
await m.refill(); table.value="srv"; await table.onchange(); await tick();
m.preset("kpi"); metric.value="매출"; unit.value="명";
await all.find(n=>n.textContent==='카드 추가').onclick(); await tick();
console.log(JSON.stringify({unit:cards[0].view.unit_text, format:cards[0].view.format ?? null, names:cards[0].source.body.names}));
})().catch(e=>{console.error(e);process.exit(1);});
"""
    src = (ROOT / "static" / "dashboard" / "dash_menu.js").read_text(encoding="utf-8")
    script = tmp_path / "m.js"
    script.write_text(prelude + "var busy=(b,f)=>f();\n" + src + body, encoding="utf-8")
    done = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert done.returncode == 0, done.stderr[-3000:]
    got = json.loads(done.stdout.strip().splitlines()[-1])
    assert got == {"unit": "명", "format": None, "names": ["매출"]}  # 지표 카드는 지표 파일의 형식(data.values.format)을 쓴다
