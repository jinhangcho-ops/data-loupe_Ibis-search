"""감사 22의 대시보드 고침: 함수 사전 전에 빈 카드 [데이터 정하기]가 멈추던 것, 쿼리에서 넣은 차트 카드의 or 묶음·따옴표 끈 조건을 선반이 망가뜨리던 것,
[데이터 정하기]를 연 채 패널을 다시 그리면 고른 칸이 비워지던 것."""
import json
import shutil
import subprocess

import pytest

from conftest import ROOT

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")

# 가짜 DOM: el(tag, props, ...kids)와 select의 value(없는 값이면 첫 선택지)만 흉내 낸다
PRELUDE = """
class E { constructor(t){this.tag=t;this.children=[];this.dataset={};this.style={};this.classList={toggle(){},add(){},remove(){}};this._v="";}
  append(...k){k.forEach(c=>{if(c instanceof E){c.parent?.children.splice(c.parent.children.indexOf(c),1);c.parent=this;}});this.children.push(...k)}
  replaceChildren(...k){this.children=[];this.append(...k); if(this.tag==='select') this._v=k[0]?.value??"";}
  remove(){this.parent?.children.splice(this.parent.children.indexOf(this),1);this.parent=null;} prepend(){}
  get value(){return this._v} set value(v){ this._v = this.tag!=='select' || this.children.some(o=>o.value===v) ? v : (this.children[0]?.value??""); }
  get textContent(){return this._t} set textContent(v){this._t=v}
  setAttribute(){} querySelector(){return {classList:{toggle(){}}}} get lastChild(){return this.children.at(-1)} }
var el=(t,p={},...k)=>{const e=new E(t);const {value,...r}=p;Object.assign(e,r);e.append(...k.filter(x=>x!==""));e._v=value??(t==="select"&&e.children[0]?e.children[0].value:"");return e;};
var SERVER={dash:{types:{bar:"막대"},aggs:[]}};
var lineEditor=()=>({root:new E('div'),read:()=>[]}); var parseRuleLine,parseStateLine,parseLinkLine,parseBlankLine,targetsNote=()=>{},freshColors=()=>({});
var isShell=c=>!c.source||!Object.keys(c.source).length;
var api=async u=>u.startsWith('/api/tables')?{tables:["srv"]}:u==='/api/targets'?{path:"",rows:[]}:{columns:[["host","문자"],["kind","문자"],["t","날짜시간"],["t2","날짜시간"],["cpu","정수"]]};
var tick=()=>new Promise(r=>setTimeout(r,10));
"""


def run_js(tmp_path, funcs, body):
    """가짜 DOM + funcs(Funcs 정의) 뒤에 dash_shelf.js·dash_menu.js·dash_panel.js와 body를 붙여 node로 돌린 마지막 줄."""
    src = "".join((ROOT / "static" / p).read_text(encoding="utf-8") for p in ("core/dash_shelf.js", "dashboard/dash_menu.js", "dashboard/dash_panel.js"))
    script = tmp_path / "t.js"
    script.write_text(PRELUDE + funcs + src + "\n(async()=>{\n" + body + "\n})().catch(e=>{console.error(e);process.exit(1);});\n", encoding="utf-8")
    done = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert done.returncode == 0, done.stderr[-3000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


NO_FUNCS = "var Funcs={cat:null,load:()=>new Promise(()=>{}),loadSupport:()=>Promise.resolve()};\n"


def test_preset_before_funcs_returns_then_fills(tmp_path):
    """사전 전에 preset이 끝나고(예전엔 무한 반복), 사전이 오면 Funcs.load().then(show)가 값 줄을 채운다."""
    funcs = """let arrive; var Funcs={cat:null,load:()=>new Promise(r=>{arrive=r;}),loadSupport:()=>Promise.resolve(),
  picker:()=>({el:new E('span'),xColumns:c=>c,yMode:()=>"col",refresh(){}})};
"""
    body = """
const root=new E('div'), m=initCardMenu(root,{metricNames:()=>[],layout:()=>({})}), rows=root.children.find(c=>c.className==='cm-rows');
m.preset("dual_trend");
const before=rows.children.length;
Funcs.cat={screens:{dash:["sum"]},byId:{sum:{aggregate:true}}}; arrive(); await tick();
console.log(JSON.stringify({before, after:rows.children.length}));
"""
    assert run_js(tmp_path, funcs, body) == {"before": 0, "after": 2}  # 두 계열 추이는 값 2줄


def test_shelf_keeps_server_conditions(tmp_path):
    """or 묶음·따옴표 끈 조건: 칩은 글자로, X축만 바꾸면 조건은 그대로, 필터를 더하고 빼도 나머지 항목은 서버 모양 그대로."""
    body = """
const body={ base:"t", x:"region", series:[{y:"amt",agg:"sum"}], conditions:[{any:["a = 1",{line:"b = '2'",quotes:false}]},{line:"memo = 'x'",quotes:false}] };
const keep=JSON.parse(JSON.stringify(body.conditions)), card={kind:"chart",source:{body}};
const p=new CardPanel(new E('div'),{columns:()=>[["region","문자"],["city","문자"],["amt","정수"]],recompute:async()=>{},changed:()=>{}});
p.show=()=>{};
const view=()=>p.shelfSection(card,new E('div'))[1], slots=v=>v.children[1].children, wraps=v=>slots(v)[3].children.filter(c=>c.className==='shelf-wrap');
let v=view(); const chips=wraps(v).map(w=>w.children[0].textContent);
v.children[0].children.find(c=>c.textContent==='city').onclick(); slots(v)[0].onclick(); await tick();
const afterX=JSON.parse(JSON.stringify(body.conditions));
v=view(); v.children[0].children.find(c=>c.textContent==='region').onclick(); slots(v)[3].onclick();  // 필터 칸: 값 입력
const form=slots(v)[3].children.find(c=>c.className==='shelf-form'); form.children[2].value="서울"; form.children[3].onclick({stopPropagation(){}}); await tick();
const added=JSON.parse(JSON.stringify(body.conditions));
v=view(); wraps(v)[0].children[1].onclick({stopPropagation(){}}); await tick();  // 첫 칩(or 묶음) 빼기
console.log(JSON.stringify({chips, sameAfterX:JSON.stringify(afterX)===JSON.stringify(keep), x:body.x, added, removed:body.conditions}));
"""
    got = run_js(tmp_path, NO_FUNCS, body)
    assert got["chips"] == ["a = 1 or b = '2'", "memo = 'x'"]  # 예전엔 [object Object]
    assert got["sameAfterX"] and got["x"] == "city"  # 예전엔 quotes:false가 떨어진 글자로 저장
    assert got["added"] == [{"any": ["a = 1", {"line": "b = '2'", "quotes": False}]}, {"line": "memo = 'x'", "quotes": False}, "region = 서울"]
    assert got["removed"] == [{"line": "memo = 'x'", "quotes": False}, "region = 서울"]


def test_redraw_keeps_menu_picks(tmp_path):
    """[데이터 정하기]를 연 채 패널을 다시 그려도(boardChanged → show → dataSection) 고른 칸이 남고, 메뉴 칸은 새 패널에 붙는다. 닫았다 열면 새로 채운다."""
    body = """
const menuRoot=new E('div'), popRoot=new E('div'), m=initCardMenu(menuRoot,{metricNames:()=>[],layout:()=>({})});
const all=()=>{const out=[];const w=e=>{if(e&&e.children){out.push(e);e.children.forEach(w);}};w(menuRoot);return out;};
const pick=t=>all().find(e=>e.tag==='select'&&String(e.title).startsWith(t));
const host={menu:m,menuRoot,popRoot,menuTarget:null}, p=new CardPanel(new E('div'),host), card={kind:"status",source:{}};
p.card=card; p.builderOpen=true;
p.dataSection(card,new E('div')); await tick();
const table=pick('카드가 읽을'); table.value="srv"; await table.onchange(); await tick();
pick('서버, 장비').value="host"; pick('데이터가 마지막').value="t2"; pick('배치도에서').value="kind";
popRoot.append(menuRoot);  // show()가 먼저 메뉴 칸을 [카드 추가] 자리로 돌려놓는다
const again=p.dataSection(card,new E('div')); await tick();
const kept={entity:pick('서버, 장비').value,time:pick('데이터가 마지막').value,kind:pick('배치도에서').value}, attached=menuRoot.parent===again.children[3];
host.menuTarget=null; p.dataSection(card,new E('div')); await tick();  // close() 뒤 다시 열기
console.log(JSON.stringify({kept, attached, reopened:pick('서버, 장비').value}));
"""
    got = run_js(tmp_path, NO_FUNCS, body)
    assert got["kept"] == {"entity": "host", "time": "t2", "kind": "kind"}  # 예전엔 모두 ""
    assert got["attached"] and got["reopened"] == ""


def test_redraw_follows_chart_type_from_settings(tmp_path):
    """[데이터 다시 정하기]를 연 채 설정 줄에서 차트 종류를 바꾸면 메뉴의 차트 종류도 따라간다(적용할 때 예전 종류로 덮지 않게)."""
    body = """
SERVER.dash.types.line="선";
const menuRoot=new E('div'), popRoot=new E('div'), m=initCardMenu(menuRoot,{metricNames:()=>[],layout:()=>({})});
const all=()=>{const out=[];const w=e=>{if(e&&e.children){out.push(e);e.children.forEach(w);}};w(menuRoot);return out;};
const type=()=>all().find(e=>e.tag==='select'&&String(e.title).startsWith('차트 종류')).value;
const host={menu:m,menuRoot,popRoot,menuTarget:null}, p=new CardPanel(new E('div'),host), card={kind:"chart",view:{type:"bar"},source:{}};
p.card=card; p.builderOpen=true;
p.dataSection(card,new E('div')); await tick();
const opened=type();
card.view.type="line"; popRoot.append(menuRoot); p.dataSection(card,new E('div')); await tick();
console.log(JSON.stringify({opened, after:type()}));
"""
    got = run_js(tmp_path, NO_FUNCS, body)
    assert got == {"opened": "bar", "after": "line"}


def test_switching_cards_resets_chart_type(tmp_path):
    """메뉴는 하나뿐이다: 종류가 '선'인 카드 다음에 자동('')인 카드를 열면 차트 종류도 자동이 된다(적용할 때 선으로 바뀌지 않게)."""
    body = """
SERVER.dash.types.line="선";
const menuRoot=new E('div'), popRoot=new E('div'), m=initCardMenu(menuRoot,{metricNames:()=>[],layout:()=>({})});
const all=()=>{const out=[];const w=e=>{if(e&&e.children){out.push(e);e.children.forEach(w);}};w(menuRoot);return out;};
const type=()=>all().find(e=>e.tag==='select'&&String(e.title).startsWith('차트 종류')).value;
const host={menu:m,menuRoot,popRoot,menuTarget:null}, p=new CardPanel(new E('div'),host);
const a={kind:"chart",view:{type:"line"},source:{}}, b={kind:"chart",view:{type:""},source:{}};
p.card=a; p.builderOpen=true; p.dataSection(a,new E('div')); await tick();
const onA=type();
p.card=b; p.dataSection(b,new E('div')); await tick();
console.log(JSON.stringify({onA, onB:type()}));
"""
    assert run_js(tmp_path, NO_FUNCS, body) == {"onA": "line", "onB": ""}
