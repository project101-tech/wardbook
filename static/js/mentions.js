/* @mentions: type "@" in a post, comment, community post or group chat to pick a neighbour from a searchable list.
   Also highlights @Name in anything marked data-mentionable. Text boxes opt in with data-mention="all" | "group:ID" | "community:ID". */
(function () {
  'use strict';
  var css = document.createElement('style');
  css.textContent = [
    '.wb-mention { color: var(--wb-accent, #1877f2); font-weight: 700; background: var(--wb-accent-soft, #e7f0ff); border-radius: 6px; padding: 0 .2em; }',
    '.wb-mention.me { background: #fff1c2; color: #8a5a00; }',
    '.wb-mpop { position: fixed; z-index: 2000; background: #fff; border: 1px solid #dfe3ea; border-radius: 14px; box-shadow: 0 14px 40px rgba(20,30,60,.22); overflow: hidden; max-height: 360px; overflow-y: auto; -webkit-overflow-scrolling: touch; }',
    '.wb-mpop .hd { padding: .45rem .8rem; font-size: .72rem; font-weight: 700; letter-spacing: .04em; text-transform: uppercase; color: #6b7280; background: #f7f9fc; border-bottom: 1px solid #eef0f4; position: sticky; top: 0; }',
    '.wb-mpop .it { display: flex; align-items: center; gap: .6rem; padding: .45rem .8rem; cursor: pointer; }',
    '.wb-mpop .it.on, .wb-mpop .it:hover { background: var(--wb-accent-soft, #e7f0ff); }',
    '.wb-mpop .av { width: 32px; height: 32px; border-radius: 50%; flex: none; background: var(--wb-accent, #1877f2); color: #fff; display: grid; place-items: center; font-weight: 700; font-size: .9rem; object-fit: cover; position: relative; }',
    '.wb-mpop .nm { font-weight: 600; line-height: 1.15; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }',
    '.wb-mpop .hs { font-size: .75rem; color: #6b7280; font-weight: 500; }',
    '.wb-mpop .od { width: 9px; height: 9px; border-radius: 50%; background: #22a35a; flex: none; }',
    '.wb-mpop .empty { padding: .8rem; color: #6b7280; font-size: .9rem; }'
  ].join('\n');
  document.head.appendChild(css);

  /* ---------- highlighting ---------- */
  var re = null, me = '', loading = false;
  function esc(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }
  function loadNames() {
    if (re || loading) return; loading = true;
    fetch('/mentions/names/', { credentials: 'same-origin' }).then(function (r) { return r.json(); }).then(function (j) {
      me = (j.me || '').toLowerCase();
      var list = (j.names || []).slice().sort(function (a, b) { return b.length - a.length; });
      if (list.length) re = new RegExp('@(' + list.map(esc).join('|') + ')(?![\\w])', 'gi');
      decorate(document);
    }).catch(function () { loading = false; });
  }
  function wrapText(node) {
    var text = node.nodeValue; if (!re || text.indexOf('@') < 0) return;
    re.lastIndex = 0; var out = [], last = 0, m, any = false;
    while ((m = re.exec(text))) {
      if (m.index > 0 && /\w/.test(text.charAt(m.index - 1))) continue;           // an email address, not a mention
      any = true;
      if (m.index > last) out.push(document.createTextNode(text.slice(last, m.index)));
      var s = document.createElement('span'); s.className = 'wb-mention' + (m[1].toLowerCase().replace(/\s*\(.*\)$/, '') === me ? ' me' : ''); s.textContent = m[0]; out.push(s);
      last = m.index + m[0].length;
    }
    if (!any) return;
    if (last < text.length) out.push(document.createTextNode(text.slice(last)));
    var frag = document.createDocumentFragment(); out.forEach(function (n) { frag.appendChild(n); });
    node.parentNode.replaceChild(frag, node);
  }
  function decorate(root) {
    if (!re) return;
    var nodes = root.querySelectorAll ? root.querySelectorAll('[data-mentionable]:not([data-md])') : [];
    Array.prototype.forEach.call(nodes, function (el) {
      el.setAttribute('data-md', '1');
      var w = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, null), list = [], n;
      while ((n = w.nextNode())) list.push(n);
      list.forEach(wrapText);
    });
  }
  var pending = null;
  new MutationObserver(function () { clearTimeout(pending); pending = setTimeout(function () { if (re) decorate(document); else if (document.querySelector('[data-mentionable]')) loadNames(); }, 60); })
    .observe(document.documentElement, { childList: true, subtree: true });
  if (document.querySelector('[data-mentionable]')) loadNames();
  window.WBMention = { decorate: function (root) { if (re) decorate(root || document); else loadNames(); } };

  /* ---------- the picker ---------- */
  var pop = null, items = [], active = 0, target = null, tokenStart = -1, seq = 0, timer = null, cache = {};
  function closePop() { if (pop) { pop.remove(); pop = null; } items = []; tokenStart = -1; }
  function tokenAt(input) {
    var pos = input.selectionStart; if (pos == null) return null;
    var before = input.value.slice(0, pos), m = /(^|[\s(])@([^\s@]*(?:[ ][^\s@]*)?)$/.exec(before);
    if (!m) return null;
    return { start: pos - m[2].length - 1, query: m[2] };
  }
  function place(input) {
    if (!pop) return;
    var r = input.getBoundingClientRect(), vv = window.visualViewport, vh = vv ? vv.height : window.innerHeight, vw = window.innerWidth;
    var w = Math.min(Math.max(r.width, 270), vw - 16), left = Math.max(8, Math.min(r.left, vw - w - 8));
    pop.style.width = w + 'px'; pop.style.left = left + 'px';
    var below = vh - r.bottom - 8, above = r.top - 8, h = Math.min(pop.scrollHeight, 360);
    if (below >= Math.min(h, 220) || below >= above) { pop.style.top = (r.bottom + 4) + 'px'; pop.style.bottom = 'auto'; pop.style.maxHeight = Math.min(360, below) + 'px'; }
    else { pop.style.bottom = (window.innerHeight - r.top + 4) + 'px'; pop.style.top = 'auto'; pop.style.maxHeight = Math.min(360, above) + 'px'; }
  }
  function mk(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text) e.textContent = text; return e; }
  function render(people, input) {
    items = people; active = 0;
    if (!people.length) { closePop(); return; }
    if (!pop) { pop = mk('div', 'wb-mpop'); pop.setAttribute('role', 'listbox'); document.body.appendChild(pop); }
    pop.replaceChildren(mk('div', 'hd', 'Mention a neighbour'));
    people.forEach(function (p, i) {
      var row = mk('div', 'it' + (i === 0 ? ' on' : '')); row.setAttribute('role', 'option');
      var av;
      if (p.avatar) { av = mk('img', 'av'); av.src = p.avatar; av.alt = ''; } else av = mk('span', 'av', p.initial);
      var nm = mk('div', 'nm', p.name); if (p.house) nm.appendChild(mk('div', 'hs', p.unit || ('House ' + p.house)));
      row.appendChild(av); row.appendChild(nm); if (p.online) row.appendChild(mk('span', 'od'));
      row.addEventListener('pointerdown', function (e) { e.preventDefault(); choose(i); });
      row.addEventListener('pointermove', function () { setActive(i); });
      pop.appendChild(row);
    });
    place(input);
  }
  function setActive(i) {
    if (!pop) return; active = i;
    Array.prototype.forEach.call(pop.querySelectorAll('.it'), function (r, k) { r.classList.toggle('on', k === i); if (k === i) r.scrollIntoView({ block: 'nearest' }); });
  }
  function choose(i) {
    var p = items[i], input = target; if (!p || !input) return;
    var pos = input.selectionStart, v = input.value, ins = '@' + p.label + ' ';
    input.value = v.slice(0, tokenStart) + ins + v.slice(pos);
    var caret = tokenStart + ins.length; input.setSelectionRange(caret, caret);
    closePop(); input.focus();
    input.dispatchEvent(new Event('input', { bubbles: true }));
  }
  function search(input) {
    var scope = input.getAttribute('data-mention') || 'all', tok = tokenAt(input);
    if (!tok) { closePop(); return; }
    target = input; tokenStart = tok.start;
    var q = tok.query.trim(), key = scope + '|' + q.toLowerCase(), my = ++seq;
    if (cache[key]) { render(cache[key], input); return; }
    clearTimeout(timer);
    timer = setTimeout(function () {
      fetch('/mentions/search/?scope=' + encodeURIComponent(scope) + '&q=' + encodeURIComponent(q), { credentials: 'same-origin' })
        .then(function (r) { return r.json(); })
        .then(function (j) { cache[key] = j.people || []; if (my === seq && target === input && tokenAt(input)) render(cache[key], input); })
        .catch(function () {});
    }, cache[scope + '|'] ? 120 : 0);
  }
  function wire(input) {
    if (input._wbm) return; input._wbm = true;
    input.setAttribute('autocomplete', 'off');
    input.addEventListener('input', function () { search(input); });
    input.addEventListener('click', function () { search(input); });
    input.addEventListener('blur', function () { setTimeout(function () { if (target === input) closePop(); }, 120); });
    input.addEventListener('keydown', function (e) {
      if (!pop || !items.length) return;
      if (e.key === 'ArrowDown') { e.preventDefault(); setActive((active + 1) % items.length); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((active - 1 + items.length) % items.length); }
      else if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); e.stopImmediatePropagation(); choose(active); }
      else if (e.key === 'Escape') { e.preventDefault(); closePop(); }
    }, true);
  }
  function wireAll(root) { Array.prototype.forEach.call((root || document).querySelectorAll('[data-mention]'), wire); }
  wireAll();
  new MutationObserver(function () { wireAll(); }).observe(document.documentElement, { childList: true, subtree: true });
  window.addEventListener('resize', function () { if (pop && target) place(target); });
  window.addEventListener('scroll', function () { if (pop && target) place(target); }, true);
})();
