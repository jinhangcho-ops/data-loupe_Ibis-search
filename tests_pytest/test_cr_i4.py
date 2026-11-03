"""묶음 I4(분석 화면 퍼널 세그먼트): 요청 body에 by가 들어가고, 단계 표에 걸린 시간 열이 나오고, 세그먼트 표가 그려진다(node 슬라이스)."""
import json
import re
import shutil
import subprocess

import pytest

from conftest import ROOT

SCRIPT = r"""
const assert = require("assert");
const stubs = (by) => ({ $: id => ({ "a-fuser": { value: "u" }, "a-fstep": { value: "e" }, "a-ftime": { value: "t" }, "a-fwin": { value: "" }, "a-funit": { value: "day" }, "a-fby": { value: by } }[id]), akind: () => "funnel", FLOW: { steps: ["a", "b"] }, pickers: {} });
const how = by => { const s = stubs(by); return new Function("$", "akind", "FLOW", "pickers", HOW + "; return anaHow();")(s.$, s.akind, s.FLOW, s.pickers); };
assert.strictEqual("by" in how(""), false);
assert.strictEqual(how("plan").by, "plan");
const steps = [{ name: "a", users: 4, conv: 1, from_first: 1, drop: 0, median_sec: null }, { name: "b", users: 2, conv: 0.5, from_first: 0.5, drop: 2, median_sec: 90 }];
assert.strictEqual(funnelRows(steps, true)[1][FUNNEL_TIME], "1.5분");
assert.strictEqual(funnelRows(steps, true)[0][FUNNEL_TIME], "-");
assert.strictEqual("앞 단계에서 걸린 시간(중앙값)" in funnelRows(steps)[1], false);  // 대시보드 카드는 그대로
assert.deepStrictEqual([30, 3600, 172800].map(fsec), ["30초", "1시간", "2일"]);
console.log("ok");
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")
def test_funnel_by_and_median_in_node(tmp_path):
    prep = (ROOT / "static" / "prep" / "prep.html").read_text(encoding="utf-8")
    how = re.search(r"function anaHow\(\) \{.*?\n\}\n", prep, re.S).group(0)
    funnel = (ROOT / "static" / "core" / "funnel.js").read_text(encoding="utf-8")
    f = tmp_path / "i4.js"
    f.write_text("const HOW = " + json.dumps(how) + ";" + chr(10) + funnel + SCRIPT, encoding="utf-8")
    done = subprocess.run(["node", str(f)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0 and "ok" in done.stdout, done.stderr


def test_prep_has_segment_screen():
    prep = (ROOT / "static" / "prep" / "prep.html").read_text(encoding="utf-8")
    for part in ('id="a-fby"', 'id="a-segs"', "function segTables", "funnelRows(g.steps, true)", "funnelRows(d.steps, true)"):
        assert part in prep, part
