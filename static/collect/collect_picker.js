// 수집용 창의 페이지에 넣는 스크립트. collect_browser.py가 이 파일을 읽어 뒤에 .pick(…)·.peek(…)·.read(…) 등을 붙여 실행한다(식 하나: 함수들을 담은 객체).
// name은 실행마다 무작위로 정한 이름이라 페이지의 이름과 겹치지 않는다. 돌려주는 것은 선택자·글자뿐이고, 서버가 모양·길이를 다시 검사한다.
({
  // 무작위처럼 보이는 클래스(숫자가 섞인 것, css-·sc- 같은 자동 이름, 너무 긴 것)와 고르기 표시용 클래스는 선택자에 쓰지 않는다.
  plain: function (c, name) {
    return !!c && c.indexOf(name) !== 0 && !/\d/.test(c) && !/^(css|sc|jsx|svelte|emotion|tw)-/i.test(c) && c.length <= 40;
  },
  classes: function (el, name) {
    var self = this;
    return Array.prototype.filter.call(el.classList || [], function (c) { return self.plain(c, name); });
  },
  // 태그.고정 클래스
  step: function (el, name) {
    return el.tagName.toLowerCase() + this.classes(el, name).map(function (c) { return "." + CSS.escape(c); }).join("");
  },
  // 태그:nth-of-type(번호). 같은 태그의 형제가 없으면 번호를 붙이지 않는다.
  nth: function (el) {
    var k = 1, n = 0, sib;
    for (sib = el.parentElement ? el.parentElement.firstElementChild : el; sib; sib = sib.nextElementSibling) {
      if (sib.tagName === el.tagName) { n++; if (sib === el) k = n; }
    }
    return el.tagName.toLowerCase() + (n > 1 ? ":nth-of-type(" + k + ")" : "");
  },
  // 고정된 것 같고 문서에 하나뿐인 id면 #id
  idOf: function (el) {
    if (!el.id || /\d/.test(el.id) || el.id.length > 40) return "";
    var sel = "#" + CSS.escape(el.id);
    return document.querySelectorAll(sel).length === 1 ? sel : "";
  },
  // 문서에서 el까지의 구조 경로(태그·:nth-of-type). 고정된 id가 있는 조상이나 body에서 시작한다.
  path: function (el) {
    var parts = [];
    for (; el && el !== document.documentElement; el = el.parentElement) {
      var id = this.idOf(el);
      if (id || el === document.body) { parts.unshift(id || "body"); break; }
      parts.unshift(this.nth(el));
    }
    return parts.join(" > ");
  },
  // 같은 모양의 형제: 태그가 같고, 고정 클래스가 둘 다 없거나 하나 이상 겹친다.
  same: function (a, b, name) {
    if (a.tagName !== b.tagName) return false;
    var ca = this.classes(a, name), cb = this.classes(b, name);
    return (!ca.length && !cb.length) || ca.some(function (c) { return cb.indexOf(c) >= 0; });
  },
  // 눌린 요소에서 위로 올라가며 같은 모양의 형제가 2개 이상인 첫 조상 = 항목. 선택자는 부모까지의 경로 > 태그.공통 고정 클래스
  findItem: function (el, name) {
    var self = this;
    for (var node = el; node && node.parentElement && node !== document.body; node = node.parentElement) {
      var here = node;
      var sibs = Array.prototype.filter.call(here.parentElement.children, function (s) { return self.same(s, here, name); });
      if (sibs.length >= 2) {
        var common = this.classes(here, name).filter(function (c) { return sibs.every(function (s) { return s.classList.contains(c); }); });
        return {node: here, selector: this.path(here.parentElement) + " > " + here.tagName.toLowerCase() + common.map(function (c) { return "." + CSS.escape(c); }).join("")};
      }
    }
    return null;
  },
  // 항목 안에서 el을 가리키는 상대 선택자("" = 항목 자체). 짧은 것부터 해 보고 그 항목에서 el을 찾는 첫 것을 쓴다.
  relative: function (item, el, name) {
    if (item === el) return "";
    var tries = [this.step(el, name)], steps = [], nths = [], node;
    for (node = el; node && node !== item; node = node.parentElement) { steps.unshift(this.step(node, name)); nths.unshift(this.nth(node)); }
    tries.push(steps.join(" > "), ":scope > " + nths.join(" > "));
    for (var i = 0; i < tries.length; i++) {
      try { if (item.querySelector(tries[i]) === el) return tries[i]; } catch (e) {}
    }
    return tries[tries.length - 1];
  },
  quoted: function (v) {
    return '"' + v.replace(/\\/g, "\\\\").replace(/"/g, '\\"') + '"';
  },
  // 다음 버튼: rel=next, 고정 id, aria-label·title, 태그.클래스, 경로 중 문서에서 그 요소 하나만 가리키는 첫 것
  nextSelector: function (el, name) {
    var self = this, tag = el.tagName.toLowerCase(), tries = [];
    if (tag === "a" && /(^|\s)next(\s|$)/i.test(el.getAttribute("rel") || "")) tries.push('a[rel~="next"]');
    if (this.idOf(el)) tries.push(this.idOf(el));
    ["aria-label", "title"].forEach(function (a) {
      var v = el.getAttribute(a);
      if (v && v.length <= 60 && !/[\r\n]/.test(v)) tries.push(tag + "[" + a + "=" + self.quoted(v) + "]");
    });
    tries.push(this.step(el, name));
    if (el.parentElement) tries.push(this.step(el.parentElement, name) + " > " + this.step(el, name));
    tries.push(this.path(el));
    for (var i = 0; i < tries.length; i++) {
      try { var found = document.querySelectorAll(tries[i]); if (found.length === 1 && found[0] === el) return tries[i]; } catch (e) {}
    }
    return this.path(el);
  },
  abs: function (v) {
    try { return new URL(v, document.baseURI).href; } catch (e) { return v; }
  },
  text: function (el) {
    return ((el.innerText !== undefined ? el.innerText : el.textContent) || "").trim();
  },
  attrs: function (el) {
    var self = this, out = {};
    Array.prototype.forEach.call(el.attributes || [], function (a) {
      var n = a.name.toLowerCase();
      if (["href", "src", "title", "alt", "value"].indexOf(n) >= 0 || /^data-/.test(n)) {
        out[n] = (n === "href" || n === "src" ? self.abs(a.value) : a.value).slice(0, 200);
      }
    });
    return out;
  },
  count: function (sel) {
    try { return document.querySelectorAll(sel).length; } catch (e) { return 0; }
  },
  pickField: function (el, name, items) {
    var item = null;
    if (items) { try { item = el.closest(items); } catch (e) { item = null; } }
    var found = item ? {node: item, selector: items} : this.findItem(el, name);
    if (!found) found = {node: el, selector: this.path(el)};
    var link = el.closest("a[href]");  // 링크 안의 글자를 눌렀으면 링크를 고른다(주소도 읽을 수 있게).
    if (link && link !== el && !el.hasAttribute("href") && found.node.contains(link) && this.text(link) === this.text(el)) el = link;
    this.mark(name, found.selector);
    return {target: "field", items: found.selector, count: this.count(found.selector), selector: this.relative(found.node, el, name),
            text: this.text(el).slice(0, 200), attrs: this.attrs(el)};
  },
  pickNext: function (el, name) {
    el = el.closest('a,button,[role="button"],input[type="button"],input[type="submit"]') || el;
    var sel = this.nextSelector(el, name);
    return {target: "next", items: "", count: this.count(sel), selector: sel, text: this.text(el).slice(0, 200), attrs: this.attrs(el)};
  },
  // 고른 항목들에 옅은 표시(최대 500개)
  mark: function (name, sel) {
    Array.prototype.forEach.call(document.querySelectorAll("." + name + "-mark"), function (e) { e.classList.remove(name + "-mark"); });
    try { Array.prototype.slice.call(document.querySelectorAll(sel), 0, 500).forEach(function (e) { e.classList.add(name + "-mark"); }); } catch (e) {}
  },
  // 고르기 켜기(target: field·next)·끄기(off). 켜져 있는 동안 마우스를 올린 요소에 테두리, 누르면 페이지의 원래 동작은 막고 결과를 window[name].picks에 쌓는다.
  pick: function (name, target, items) {
    var self = this, old = window[name];
    if (old) old.off();
    if (target === "off") return true;
    var css = "." + name + "-hover{outline:2px solid #e8590c!important;outline-offset:-2px!important;cursor:crosshair!important}" +
              "." + name + "-mark{outline:1px dashed #1c7ed6!important;background-color:rgba(28,126,214,.08)!important}";
    var sheet = null, style = null;
    try {  // 페이지의 CSP가 style 요소를 막아도 되도록 만든 시트로 넣는다.
      sheet = new CSSStyleSheet();
      sheet.replaceSync(css);
      document.adoptedStyleSheets = document.adoptedStyleSheets.concat([sheet]);
    } catch (e) {
      sheet = null;
      style = document.createElement("style");
      style.textContent = css;
      (document.head || document.documentElement).appendChild(style);
    }
    var hovered = null, blocked = ["mousedown", "mouseup", "pointerdown", "pointerup", "dblclick", "auxclick", "submit"];
    function over(e) {
      if (hovered) hovered.classList.remove(name + "-hover");
      hovered = e.target instanceof Element ? e.target : null;
      if (hovered) hovered.classList.add(name + "-hover");
    }
    function block(e) { e.preventDefault(); e.stopImmediatePropagation(); }
    function click(e) {
      block(e);
      if (!(e.target instanceof Element) || e.target === document.body || e.target === document.documentElement) return;  // 빈 곳(문서 전체)은 고르지 않는다.
      try {
        state.picks.push(target === "next" ? self.pickNext(e.target, name) : self.pickField(e.target, name, items));
        if (state.picks.length > 20) state.picks.shift();
      } catch (err) {}
    }
    var state = {picks: [], off: function () {
      window.removeEventListener("mouseover", over, true);
      window.removeEventListener("click", click, true);
      blocked.forEach(function (k) { window.removeEventListener(k, block, true); });
      if (sheet) document.adoptedStyleSheets = document.adoptedStyleSheets.filter(function (s) { return s !== sheet; });
      if (style) style.remove();
      ["-hover", "-mark"].forEach(function (k) {
        Array.prototype.forEach.call(document.querySelectorAll("." + name + k), function (e) { e.classList.remove(name + k); });
      });
      delete window[name];
    }};
    window.addEventListener("mouseover", over, true);
    window.addEventListener("click", click, true);
    blocked.forEach(function (k) { window.addEventListener(k, block, true); });
    window[name] = state;
    if (target === "field" && items) this.mark(name, items);
    return true;
  },
  // 지금 주소·제목, 고르기가 켜져 있는지, 쌓인 고른 결과(가져가면 비운다)
  peek: function (name) {
    var s = window[name];
    return {url: location.href, title: document.title, on: !!s, picks: s ? s.picks.splice(0) : []};
  },
  value: function (el, attr) {
    if (attr === "text") return this.text(el);
    var v = el.getAttribute(attr);
    return v === null ? null : attr === "href" || attr === "src" ? this.abs(v) : v;
  },
  // 항목(items)마다 fields [{selector, attr}]의 값. start번째 항목부터 limit개(null이면 끝까지)
  read: function (items, fields, start, limit) {
    var self = this, nodes;
    try { nodes = document.querySelectorAll(items); } catch (e) { return {error: "항목 선택자가 올바르지 않습니다: " + items}; }
    for (var f = 0; f < fields.length; f++) {
      if (fields[f].selector) { try { document.querySelector(fields[f].selector); } catch (e) { return {error: "컬럼 선택자가 올바르지 않습니다: " + fields[f].selector}; } }
    }
    var rows = [], end = limit === null ? nodes.length : Math.min(nodes.length, start + limit);
    for (var i = start; i < end; i++) {
      rows.push(fields.map(function (field) {
        var el = field.selector ? nodes[i].querySelector(field.selector) : nodes[i];
        return el ? self.value(el, field.attr) : null;
      }));
    }
    return {count: nodes.length, rows: rows, url: location.href};
  },
  // 기다리기용: 항목 수, 첫·끝 항목 글자(바뀌었는지 보려고), 주소, 문서 상태, 이 문서에 표시(nav)가 남아 있는지
  probe: function (items, nav) {
    var n = 0, sig = "";
    try {
      var nodes = document.querySelectorAll(items);
      n = nodes.length;
      if (n) sig = (nodes[0].textContent || "").slice(0, 300) + "\u0000" + (nodes[n - 1].textContent || "").slice(0, 300);
    } catch (e) { n = -1; }
    return {count: n, sig: sig, url: location.href, ready: document.readyState, same: !!nav && window[nav] === true};
  },
  // 이 문서에 표시를 남긴다. 이동한 뒤 새 문서인지 알아보는 데 쓴다.
  tag: function (nav) {
    window[nav] = true;
    return true;
  },
  // 다음 버튼 찾기(press면 누르기). {found, href(링크면 절대 주소)}. 꺼진 버튼(disabled, aria-disabled, disabled 클래스)은 없는 것으로 본다.
  next: function (sel, press) {
    var el;
    try { el = document.querySelector(sel); } catch (e) { return {error: "다음 버튼 선택자가 올바르지 않습니다: " + sel}; }
    var off = el && (el.disabled || el.getAttribute("aria-disabled") === "true" || /(^|[\s_-])disabled($|[\s_-])/i.test(el.getAttribute("class") || ""));
    if (!el || off) return {found: false, href: ""};  // 마지막 쪽에서 버튼이 꺼져 있으면 없는 것으로 본다.
    var link = el.closest("a[href]");
    if (press) el.click();
    return {found: true, href: link ? link.href : ""};
  },
  // 끝까지 내려 더 불러오게 한다. 안쪽 스크롤 영역을 쓰는 목록을 위해 마지막 항목도 보이게 한다.
  scroll: function (items) {
    try { var nodes = document.querySelectorAll(items); if (nodes.length) nodes[nodes.length - 1].scrollIntoView({block: "end"}); } catch (e) {}
    var root = document.scrollingElement || document.documentElement;
    window.scrollTo(0, root.scrollHeight);
    return root.scrollHeight;
  }
})
