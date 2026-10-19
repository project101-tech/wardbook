/* Wardbook games: the shared game screen. Each game registers a renderer (see games-*.js). */
(function () {
  'use strict';
  var NS = 'http://www.w3.org/2000/svg';
  var G = window.WBGame = { renderers: {} };

  /* ---------- tiny helpers shared by every renderer ---------- */
  G.el = function (tag, attrs, kids) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === 'class') e.className = attrs[k]; else if (k === 'text') e.textContent = attrs[k];
      else if (k === 'style') e.style.cssText = attrs[k]; else e.setAttribute(k, attrs[k]);
    });
    (kids || []).forEach(function (c) { if (c) e.appendChild(typeof c === 'string' ? document.createTextNode(c) : c); });
    return e;
  };
  G.svg = function (tag, attrs, kids) {
    var e = document.createElementNS(NS, tag);
    Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    (kids || []).forEach(function (c) { if (c) e.appendChild(c); });
    return e;
  };
  G.sleep = function (ms) { return new Promise(function (r) { setTimeout(r, ms); }); };
  G.register = function (name, impl) { G.renderers[name] = impl; };

  /* ---------- sound (tiny synthesised effects; nothing to download) ---------- */
  var audio = null, soundOn = true;
  try { soundOn = localStorage.getItem('wb-game-sound') !== '0'; } catch (e) {}
  function ac() {
    if (!audio) { try { audio = new (window.AudioContext || window.webkitAudioContext)(); } catch (e) { return null; } }
    if (audio.state === 'suspended') audio.resume();
    return audio;
  }
  function tone(freq, dur, type, vol, delay, slide) {
    var a = ac(); if (!a || !soundOn) return;
    var t = a.currentTime + (delay || 0), o = a.createOscillator(), g = a.createGain();
    o.type = type || 'sine'; o.frequency.setValueAtTime(freq, t);
    if (slide) o.frequency.exponentialRampToValueAtTime(slide, t + dur);
    g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(vol || 0.12, t + 0.008); g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    o.connect(g); g.connect(a.destination); o.start(t); o.stop(t + dur + 0.02);
  }
  G.sfx = function (name) {
    if (!soundOn) return;
    if (name === 'move') tone(480, 0.07, 'triangle', 0.16, 0, 360);
    else if (name === 'capture') { tone(220, 0.12, 'square', 0.1, 0, 90); tone(520, 0.05, 'triangle', 0.14); }
    else if (name === 'dice') { for (var i = 0; i < 6; i++) tone(260 + Math.random() * 380, 0.045, 'square', 0.05, i * 0.07); }
    else if (name === 'hop') tone(640, 0.04, 'sine', 0.09);
    else if (name === 'turn') { tone(660, 0.1, 'sine', 0.12); tone(880, 0.14, 'sine', 0.12, 0.1); }
    else if (name === 'win') [523, 659, 784, 1047].forEach(function (f, i) { tone(f, 0.22, 'triangle', 0.16, i * 0.12); });
    else if (name === 'lose') { tone(392, 0.25, 'triangle', 0.13); tone(330, 0.25, 'triangle', 0.13, 0.2); tone(262, 0.4, 'triangle', 0.13, 0.4); }
    else if (name === 'error') tone(180, 0.15, 'sawtooth', 0.08);
    else if (name === 'tick') tone(900, 0.03, 'square', 0.05);
  };
  G.buzz = function (ms) { try { if (navigator.vibrate) navigator.vibrate(ms || 15); } catch (e) {} };

  /* ---------- the dice (shared by Snakes & Ladders and Ludo) ---------- */
  var PIPS = { 1: [4], 2: [0, 8], 3: [0, 4, 8], 4: [0, 2, 6, 8], 5: [0, 2, 4, 6, 8], 6: [0, 2, 3, 5, 6, 8] };
  G.makeDie = function () {
    var d = G.el('button', { class: 'die', type: 'button', 'aria-label': 'Dice' });
    for (var i = 0; i < 9; i++) d.appendChild(G.el('i'));
    return d;
  };
  G.showDie = function (d, v) {
    d.classList.toggle('six', v === 6); d.classList.toggle('one', v === 1);
    for (var i = 0; i < 9; i++) d.children[i].className = (PIPS[v] || []).indexOf(i) >= 0 ? 'p' : '';
  };
  G.stopTumble = function (d, final) {
    clearInterval(d._iv); d._iv = null; d.classList.remove('rolling'); if (final) G.showDie(d, final);
  };
  G.tumble = function (d, ms, final) {                 // shake through random faces, then land on `final` (or stop early with stopTumble)
    G.stopTumble(d); d.classList.add('rolling'); G.sfx('dice');
    var iv = d._iv = setInterval(function () { G.showDie(d, 1 + Math.floor(Math.random() * 6)); }, 80);
    return G.sleep(ms).then(function () { if (d._iv === iv) G.stopTumble(d, final); });
  };

  /* ---------- boot ---------- */
  G.boot = function (cfg) {
    var p = cfg.payload, prev = null, csrf = cfg.csrf, urls = cfg.urls;
    var impl = G.renderers[p.game];
    var $ = function (id) { return document.getElementById(id); };
    var stage = $('gm-stage'), topEl = $('gm-top'), botEl = $('gm-bottom'), actionsEl = $('gm-actions'), statusEl = $('gm-status'), helpEl = $('gm-help');
    var clockEls = {}, resultShown = false, wasActive = p.active, busy = false, toastTimer = null, lastTurnMine = (p.turn === p.you && p.active);

    var ctx = G.ctx = {
      stage: stage, me: p.you, urls: urls,
      player: function (id) { return (p.players || []).filter(function (x) { return x.id === id; })[0]; },
      isMyTurn: function (pl) { pl = pl || p; return pl.active && pl.turn === pl.you; },
      busy: function () { return busy; },
      toast: toast, sfx: G.sfx, buzz: G.buzz, fetchState: fetchState,
      send: send, apply: apply
    };

    function toast(msg) {
      var t = $('gm-toast'); t.textContent = msg; t.classList.add('show');
      clearTimeout(toastTimer); toastTimer = setTimeout(function () { t.classList.remove('show'); }, 2600);
    }

    /* ----- talking to the server ----- */
    function send(action, value) {
      var fd = new FormData(); fd.append('action', action); if (value !== undefined && value !== null) fd.append('move', value);
      busy = true;
      return fetch(urls.move, { method: 'POST', headers: { 'X-CSRFToken': csrf }, body: fd, credentials: 'same-origin' })
        .then(function (r) { return r.json().catch(function () { return { error: 'Something went wrong. Try again.' }; }); })
        .catch(function () { return { error: 'No connection. Try again.' }; })
        .then(function (j) {
          busy = false;
          if (j.game) apply(j.game);
          if (j.error) { toast(j.error); G.sfx('error'); }
          return j;
        });
    }
    function fetchState() {
      return fetch(urls.state, { credentials: 'same-origin' }).then(function (r) { return r.json(); })
        .then(function (j) { if (j.game) apply(j.game); }).catch(function () {});
    }

    /* ----- players ----- */
    function avatar(pl) {
      var a = G.el('div', { class: 'gav' });
      if (pl.avatar) a.appendChild(G.el('img', { src: pl.avatar, alt: '' })); else a.textContent = pl.initial;
      a.appendChild(G.el('span', { class: 'od' + (pl.online ? ' on' : '') }));
      return a;
    }
    function card(pl) {
      var d = (impl.decorate && impl.decorate(pl, p)) || {};
      var turn = p.active && p.turn === pl.id;
      var name = G.el('div', { class: 'nm', text: pl.name }); if (pl.id === p.you) name.appendChild(G.el('span', { class: 'me-tag', text: 'YOU' }));
      var kids = [];
      if (d.chip) kids.push(G.el('span', { class: 'chip', style: 'background:' + d.chip }));
      kids.push(avatar(pl));
      kids.push(G.el('div', { class: 'grow' }, [name, G.el('div', { class: 'sub', text: d.sub || (pl.house ? 'House ' + pl.house : '') })]));
      if (d.extra) kids.push(d.extra);
      if (d.clock) { var c = G.el('div', { class: 'gm-clock', 'data-side': d.clock, text: '--:--' }); clockEls[d.clock] = c; kids.push(c); }
      return G.el('div', { class: 'gm-player' + (turn ? ' turn' : ''), 'data-id': pl.id }, kids);
    }
    function renderPlayers() {
      clockEls = {}; topEl.replaceChildren(); botEl.replaceChildren();
      var list = p.players || [];
      if (list.length === 2) {
        topEl.className = 'gm-players duo'; botEl.className = 'gm-players duo';
        var me = list.filter(function (x) { return x.id === p.you; })[0], other = list.filter(function (x) { return x.id !== p.you; })[0];
        if (other) topEl.appendChild(card(other)); if (me) botEl.appendChild(card(me));
      } else {
        topEl.className = 'gm-players';
        list.forEach(function (pl) { topEl.appendChild(card(pl)); });
      }
      tickClocks();
    }

    /* ----- clocks (chess) ----- */
    function fmt(ms) {
      ms = Math.max(0, ms); var s = Math.ceil(ms / 1000), m = Math.floor(s / 60);
      if (ms < 10000 && ms > 0) return '0:' + (ms / 1000).toFixed(1).padStart(4, '0');
      return m + ':' + String(s % 60).padStart(2, '0');
    }
    function tickClocks() {
      if (!p.clock) return;
      var elapsed = performance.now() - (p._at || performance.now());     // time since the server told us the clock values
      ['w', 'b'].forEach(function (side) {
        var elc = clockEls[side]; if (!elc) return;
        var running = !!(p.clock.running && p.clock.turn === side && p.active), left = p.clock[side];
        if (running) left = Math.max(0, left - elapsed);
        elc.textContent = fmt(left);
        elc.classList.toggle('on', running);
        elc.classList.toggle('low', running && left < 20000);
        if (running && left <= 0 && !tickClocks.fired) { tickClocks.fired = true; fetchState().then(function () { tickClocks.fired = false; }); }
      });
    }
    setInterval(tickClocks, 100);

    /* ----- status line, actions, result ----- */
    function statusFor() {
      var s = impl.status && impl.status(p, ctx); if (s) return s;
      if (!p.active) {
        if (!p.winner) return { text: 'Draw game', tone: '' };
        return p.winner === p.you ? { text: 'You won! 🎉', tone: 'win' } : { text: (ctx.player(p.winner) || {}).name + ' won', tone: 'lose' };
      }
      if (p.turn === p.you) return { text: 'Your turn', tone: 'you' };
      var t = ctx.player(p.turn); return { text: 'Waiting for ' + (t ? t.name : 'your neighbour') + '…', tone: '' };
    }
    function renderStatus() {
      var s = statusFor(); statusEl.textContent = s.text; statusEl.className = 'gm-status ' + (s.tone || '');
      helpEl.textContent = (impl.help && impl.help(p)) || '';
      document.title = (ctx.isMyTurn() && document.hidden ? '▶ ' : '') + p.label + ' · Wardbook';
    }
    function renderActions() {
      actionsEl.replaceChildren();
      if (p.active && p.can_resign) {
        var b = G.el('button', { class: 'gm-btn danger', type: 'button' }, [G.el('i', { class: 'bi bi-flag' }), 'Resign']), armed = false, timer;
        b.onclick = function () {
          if (!armed) { armed = true; b.classList.add('sure'); b.lastChild.textContent = 'Tap again to resign'; timer = setTimeout(function () { armed = false; b.classList.remove('sure'); b.lastChild.textContent = 'Resign'; }, 3500); return; }
          clearTimeout(timer); send('resign');
        };
        actionsEl.appendChild(b);
      }
      if (impl.actions) impl.actions(p, ctx, actionsEl);
      if (!p.active) {
        var f = G.el('form', { method: 'post', action: urls.rematch, style: 'display:inline' });
        f.appendChild(G.el('input', { type: 'hidden', name: 'csrfmiddlewaretoken', value: csrf }));
        f.appendChild(G.el('button', { class: 'gm-btn', style: 'background:var(--wb-accent);color:#fff;border-color:var(--wb-accent)' }, [G.el('i', { class: 'bi bi-arrow-repeat' }), p.game === 'teen_patti' ? 'New table' : 'Play again']));
        actionsEl.appendChild(f);
        actionsEl.appendChild(G.el('a', { class: 'gm-btn', href: urls.games }, [G.el('i', { class: 'bi bi-grid' }), 'All games']));
      }
    }
    function confetti() {
      var box = G.el('div', { class: 'gm-confetti' }), colors = ['#1877f2', '#e63946', '#f2b01e', '#22a35a', '#a78bfa', '#ff7a59'];
      for (var i = 0; i < 70; i++) {
        var c = G.el('i', { style: 'left:' + Math.random() * 100 + '%;background:' + colors[i % colors.length] + ';animation-duration:' + (2.2 + Math.random() * 2) + 's;animation-delay:' + Math.random() * 0.7 + 's;transform:rotate(' + Math.random() * 360 + 'deg)' });
        box.appendChild(c);
      }
      document.body.appendChild(box); setTimeout(function () { box.remove(); }, 5200);
    }
    function showResult() {
      if (resultShown) return; resultShown = true;
      var won = p.winner === p.you, draw = !p.winner, box = $('gm-result');
      box.className = 'gm-result show ' + (draw ? '' : won ? 'win' : 'lose');
      $('gm-result-big').textContent = draw ? '🤝' : won ? '🏆' : '😅';
      $('gm-result-title').textContent = draw ? 'Draw!' : won ? 'You won!' : ((ctx.player(p.winner) || {}).name || 'Your neighbour') + ' won';
      $('gm-result-why').textContent = p.reason || '';
      G.sfx(draw ? 'move' : won ? 'win' : 'lose'); if (won) confetti();
    }
    $('gm-result-close').onclick = function () { $('gm-result').classList.remove('show'); };
    $('gm-result').addEventListener('click', function (e) { if (e.target === this) this.classList.remove('show'); });

    /* ----- applying a new payload from the server ----- */
    var rev = 0;
    function apply(np) {
      if (np.rev < rev) return;                 // an older answer arriving late
      rev = np.rev;
      prev = p; p = np; ctx.me = p.you;
      p._at = performance.now();
      if (p.rematch_url && location.pathname !== p.rematch_url) { toast('Starting the rematch…'); setTimeout(function () { location.href = p.rematch_url; }, 700); }
      renderPlayers(); impl.update(p, prev, ctx); renderStatus(); renderActions();
      var mine = ctx.isMyTurn();
      if (mine && !lastTurnMine && prev && prev.turn !== p.you) { G.sfx('turn'); G.buzz(25); }
      lastTurnMine = mine;
      if (wasActive && !p.active) setTimeout(showResult, 900);
      wasActive = p.active;
    }

    /* ----- chat ----- */
    var chatLog = $('gm-chat-log'), chatAfter = 0;
    function addChat(list) {
      list.forEach(function (m) {
        if (m.id <= chatAfter) return; chatAfter = m.id;
        var row = G.el('div', { class: 'gm-msg' + (m.mine ? ' mine' : '') });
        if (!m.mine) row.appendChild(G.el('small', { text: m.sender }));
        row.appendChild(G.el('span', { text: m.body })); chatLog.appendChild(row);
      });
      if (list.length) chatLog.scrollTop = chatLog.scrollHeight;
    }
    function pollChat() { fetch(urls.chat + '?after=' + chatAfter, { credentials: 'same-origin' }).then(function (r) { return r.json(); }).then(function (j) { addChat(j.messages || []); }).catch(function () {}); }
    function say(body) {
      var fd = new FormData(); fd.append('body', body);
      fetch(urls.chat + '?after=' + chatAfter, { method: 'POST', headers: { 'X-CSRFToken': csrf }, body: fd, credentials: 'same-origin' }).then(function (r) { return r.json(); }).then(function (j) { addChat(j.messages || []); });
    }
    $('gm-chat-form').addEventListener('submit', function (e) { e.preventDefault(); var i = $('gm-chat-input'), b = i.value.trim(); if (b) { say(b); i.value = ''; } });
    document.querySelectorAll('.gm-react button').forEach(function (b) { b.onclick = function () { say(b.textContent); }; });

    /* ----- sound toggle ----- */
    var sbtn = $('gm-sound');
    function paintSound() { sbtn.innerHTML = '<i class="bi ' + (soundOn ? 'bi-volume-up-fill' : 'bi-volume-mute-fill') + '"></i>'; sbtn.title = soundOn ? 'Sound on' : 'Sound off'; }
    sbtn.onclick = function () { soundOn = !soundOn; try { localStorage.setItem('wb-game-sound', soundOn ? '1' : '0'); } catch (e) {} paintSound(); if (soundOn) G.sfx('tick'); };
    paintSound();
    document.addEventListener('pointerdown', function once() { ac(); document.removeEventListener('pointerdown', once); });

    /* ----- start ----- */
    rev = p.rev; p._at = performance.now();
    impl.init(ctx, p);
    renderPlayers(); impl.update(p, null, ctx); renderStatus(); renderActions();
    if (window.wbLive) wbLive.on('g' + cfg.sessionId, function () { fetchState(); pollChat(); });
    setInterval(function () { if (!document.hidden) fetchState(); }, 8000);          // clocks, online dots, safety net
    document.addEventListener('visibilitychange', function () { if (!document.hidden) { fetchState(); pollChat(); } renderStatus(); });
    pollChat();
  };
})();
