/* Music Taste: live neighbour search (discover page) and link preview (manage page). Plain DOM, no libraries. */
(function () {
  'use strict';
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }

  /* ---------- discover: search as you type ---------- */
  var search = document.getElementById('music-search'), results = document.getElementById('music-results');
  if (search && results) {
    var status = document.getElementById('music-status'), timer = null, seq = 0, form = search.form;
    function run() {
      var q = search.value.trim(), my = ++seq;
      results.setAttribute('aria-busy', 'true'); status.textContent = 'Searching…';
      fetch(form.action + '?partial=1&q=' + encodeURIComponent(q), { credentials: 'same-origin', headers: { 'X-Requested-With': 'fetch' } })
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.text(); })
        .then(function (html) {
          if (my !== seq) return;
          results.innerHTML = html;       // our own server-rendered, auto-escaped fragment
          status.textContent = '';
          try { history.replaceState(null, '', q ? form.action + '?q=' + encodeURIComponent(q) : form.action); } catch (e) {}
        })
        .catch(function () {
          if (my !== seq) return;
          status.textContent = '';
          var box = el('div', 'alert alert-danger d-flex justify-content-between align-items-center', "Couldn't load results. Check your connection.");
          var b = el('button', 'btn btn-sm btn-soft', 'Try again'); b.type = 'button'; b.addEventListener('click', run); box.appendChild(b);
          results.replaceChildren(box);
        })
        .then(function () { if (my === seq) results.removeAttribute('aria-busy'); });
    }
    search.addEventListener('input', function () { clearTimeout(timer); timer = setTimeout(run, 250); });
    form.addEventListener('submit', function (e) { e.preventDefault(); clearTimeout(timer); run(); });
  }

  /* ---------- manage: preview a pasted link ---------- */
  var url = document.getElementById('mm-url'), box = document.getElementById('mm-preview');
  if (url && box) {
    var btn = document.getElementById('mm-preview-btn'), last = '', pseq = 0;
    function show(cover, title, sub, tone) {
      box.hidden = false; box.replaceChildren();
      var art;
      if (cover) { art = el('img'); art.src = cover; art.alt = ''; art.referrerPolicy = 'no-referrer'; } else { art = el('span', 'ph'); art.appendChild(el('i', 'bi bi-music-note-list')); }
      var t = el('div', 'min-w-0'); t.appendChild(el('div', 'fw-bold', title));
      var s = el('div', 'small ' + (tone === 'bad' ? 'text-danger' : tone === 'warn' ? 'text-warning-emphasis' : 'text-muted'), sub); t.appendChild(s);
      box.appendChild(art); box.appendChild(t);
    }
    function preview() {
      var v = url.value.trim();
      if (!v) { box.hidden = true; last = ''; return; }
      if (v === last) return; last = v;
      var my = ++pseq;
      box.hidden = false; box.replaceChildren(el('span', 'mm-spin'), el('span', 'small text-muted ms-2', 'Checking the link…'));
      fetch(btn.dataset.url + '?url=' + encodeURIComponent(v), { credentials: 'same-origin' })
        .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
        .then(function (x) {
          if (my !== pseq) return;
          var j = x.j;
          if (!x.ok || !j.ok) { show('', 'Can’t use that link', (j && j.message) || 'Something went wrong. Try again.', 'bad'); return; }
          if (j.duplicate) show(j.cover, j.title || 'Spotify playlist', 'You’ve already added this playlist.', 'warn');
          else if (j.status === 'unavailable') show(j.cover, j.title || 'Spotify playlist', 'Spotify can’t find this playlist. It may be private or deleted. You can still add it.', 'warn');
          else if (j.status === 'ok') show(j.cover, j.title || 'Spotify playlist', 'Looks good. Ready to add.');
          else show('', 'Spotify playlist', 'The link is valid, but we couldn’t load its title or artwork right now. You can still add it.');
        })
        .catch(function () { if (my === pseq) show('', 'Couldn’t check the link', 'Check your connection and try again. You can still add it.', 'warn'); });
    }
    btn.addEventListener('click', function () { last = ''; preview(); });
    url.addEventListener('change', preview);
    url.addEventListener('paste', function () { setTimeout(preview, 0); });
  }
})();
