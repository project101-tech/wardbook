/* Chess: board, pieces (hand-drawn SVG, no font or image downloads), tap or drag to move, clocks, draw offers. */
(function () {
  'use strict';
  var G = window.WBGame, el = G.el, svg = G.svg;
  var FILES = 'abcdefgh', START = { p: 8, n: 2, b: 2, r: 2, q: 1 }, VALUE = { p: 1, n: 3, b: 3, r: 5, q: 9 };

  /* ---------- the pieces: simple, readable silhouettes that share one base ---------- */
  var BASE = 'M26 84 Q26 76 34 76 H66 Q74 76 74 84 Z';
  var SHAPES = {
    p: [{ d: 'M50 22 a11 11 0 1 1 -0.01 0 Z' }, { d: 'M42 40 H58 L57 48 Q66 58 66 76 H34 Q34 58 43 48 Z' }, { d: BASE }],
    r: [{ d: 'M29 20 H40 V28 H45 V20 H55 V28 H60 V20 H71 V38 H29 Z' }, { d: 'M34 38 H66 L63 50 V66 L68 76 H32 L37 66 V50 Z' }, { d: BASE }],
    n: [{ d: 'M32 76 C30 58 36 46 48 38 C42 38 38 40 34 44 L28 46 C26 38 34 26 44 20 L47 10 L55 18 C72 22 78 42 70 60 C68 66 68 70 70 76 Z' },
        { d: BASE }, { d: 'M50 30 a3 3 0 1 1 -0.01 0 Z', detail: 'eye' }, { d: 'M58 22 C64 28 68 38 66 50', detail: 'line' }],
    b: [{ d: 'M50 10 a6 6 0 1 1 -0.01 0 Z' }, { d: 'M50 20 C68 32 70 50 60 60 L66 76 H34 L40 60 C30 50 32 32 50 20 Z' }, { d: BASE }, { d: 'M44 36 L56 48 M56 36 L44 48', detail: 'line' }],
    q: [{ d: 'M26 34 a5 5 0 1 1 -0.01 0 Z' }, { d: 'M50 22 a5 5 0 1 1 -0.01 0 Z' }, { d: 'M74 34 a5 5 0 1 1 -0.01 0 Z' },
        { d: 'M30 76 L25 38 L40 54 L50 28 L60 54 L75 38 L70 76 Z' }, { d: BASE }, { d: 'M36 66 H64', detail: 'line' }],
    k: [{ d: 'M46 6 H54 V14 H62 V22 H54 V32 H46 V22 H38 V14 H46 Z' }, { d: 'M50 32 C66 32 72 44 66 56 C72 62 72 70 68 76 H32 C28 70 28 62 34 56 C28 44 34 32 50 32 Z' },
        { d: BASE }, { d: 'M38 62 H62', detail: 'line' }]
  };
  var TPL = {};
  function pieceSVG(color, type, big) {
    var key = color + type;
    if (!TPL[key]) {
      var white = color === 'w', fill = white ? '#fbfcff' : '#2c3548', line = white ? '#1d2638' : '#0b101b', det = white ? '#1d2638' : '#dbe3f5';
      var s = svg('svg', { viewBox: '0 0 100 100', 'aria-hidden': 'true' });
      SHAPES[type].forEach(function (sh) {
        if (sh.detail === 'eye') s.appendChild(svg('path', { d: sh.d, fill: det }));
        else if (sh.detail === 'line') s.appendChild(svg('path', { d: sh.d, fill: 'none', stroke: det, 'stroke-width': 3, 'stroke-linecap': 'round', opacity: white ? .55 : .7 }));
        else s.appendChild(svg('path', { d: sh.d, fill: fill, stroke: line, 'stroke-width': 3.4, 'stroke-linejoin': 'round' }));
      });
      TPL[key] = s;
    }
    return TPL[key].cloneNode(true);
  }

  /* ---------- helpers ---------- */
  function parseBoard(fen) {                         // -> array of 64 (index 0 = a8), each '' or e.g. 'wp'
    var rows = fen.split(' ')[0].split('/'), out = [];
    rows.forEach(function (r) {
      for (var i = 0; i < r.length; i++) {
        var ch = r[i];
        if (/\d/.test(ch)) { for (var k = 0; k < +ch; k++) out.push(''); }
        else out.push((ch === ch.toUpperCase() ? 'w' : 'b') + ch.toLowerCase());
      }
    });
    return out;
  }
  function idxOf(name) { return (8 - +name[1]) * 8 + FILES.indexOf(name[0]); }
  function nameOf(i) { return FILES[i % 8] + (8 - Math.floor(i / 8)); }

  G.register('chess', {
    flip: false, sel: null, drag: null, promo: null, board: null, cells: [], pieces: {}, soundedLen: -1, p: null, pending: null,

    init: function (ctx, p) {
      var self = this; this.ctx = ctx; this.flip = p.you_side === 'b';
      var wrap = el('div', { class: 'ch-wrap' });
      this.trayTop = el('div', { class: 'ch-tray' }); this.trayBot = el('div', { class: 'ch-tray' });
      this.board = el('div', { class: 'ch-board' });
      this.cells = [];
      for (var r = 0; r < 8; r++) for (var c = 0; c < 8; c++) {
        var f = this.flip ? 7 - c : c, rk = this.flip ? r : 7 - r;       // rk 0..7 is the rank number minus one
        var sq = el('div', { class: 'ch-sq ' + ((f + rk) % 2 === 0 ? 'd' : 'l'), 'data-sq': FILES[f] + (rk + 1) });
        if (c === 0) sq.appendChild(el('span', { class: 'co r', text: String(rk + 1) }));
        if (r === 7) sq.appendChild(el('span', { class: 'co f', text: FILES[f] }));
        this.board.appendChild(sq); this.cells.push(sq);
      }
      wrap.appendChild(this.trayTop); wrap.appendChild(this.board); wrap.appendChild(this.trayBot);
      ctx.stage.appendChild(wrap);
      this.board.addEventListener('pointerdown', function (e) { self.down(e); });
      document.addEventListener('pointermove', function (e) { self.move(e); });
      document.addEventListener('pointerup', function (e) { self.up(e); });
      document.addEventListener('pointercancel', function () { self.endDrag(); });
      document.addEventListener('keydown', function (e) { if (e.key === 'Escape') { self.clearSel(); self.closePromo(); } });
    },

    cellAt: function (name) {
      var f = FILES.indexOf(name[0]), rk = +name[1] - 1, r = this.flip ? rk : 7 - rk, c = this.flip ? 7 - f : f;
      return this.cells[r * 8 + c];
    },
    nameAtPoint: function (x, y) {
      var b = this.board.getBoundingClientRect();
      if (x < b.left || x > b.right || y < b.top || y > b.bottom) return null;
      var c = Math.min(7, Math.floor((x - b.left) / b.width * 8)), r = Math.min(7, Math.floor((y - b.top) / b.height * 8));
      return this.cells[r * 8 + c].dataset.sq;
    },

    /* ----- drawing the position ----- */
    update: function (p, prev, ctx) {
      var self = this, st = p.state, board = parseBoard(st.fen), hist = st.history || [];
      var oldFen = prev && prev.state ? prev.state.fen : null, grew = prev && prev.state && hist.length > (prev.state.history || []).length;
      this.p = p; this.pending = null;
      this.endDrag(); this.closePromo();
      if (!(p.legal && this.sel && p.legal[this.sel])) this.sel = null;

      this.cells.forEach(function (sq) {
        var pc = sq.querySelector('.ch-pc'); if (pc) pc.remove();
        sq.className = 'ch-sq ' + (sq.classList.contains('d') ? 'd' : 'l');
      });
      board.forEach(function (code, i) {
        if (!code) return;
        var pc = el('div', { class: 'ch-pc', 'data-p': code }, [pieceSVG(code[0], code[1])]);
        self.cellAt(nameOf(i)).appendChild(pc);
      });

      // highlights
      if (st.last) { this.cellAt(st.last[0]).classList.add('last'); this.cellAt(st.last[1]).classList.add('last'); }
      var kingOf = function (side) { var i = board.indexOf(side + 'k'); return i < 0 ? null : self.cellAt(nameOf(i)); };
      if (st.check && p.active) { var k = kingOf(st.turn_side); if (k) k.classList.add('chk'); }
      if (!p.active && st.result && st.result.reason === 'checkmate' && st.result.winner) { var kk = kingOf(st.result.winner === 'w' ? 'b' : 'w'); if (kk) kk.classList.add('chk'); }
      this.paintSel();

      // a slide for the move that just arrived, and a sound
      if (grew && st.last) {
        var mv = hist[hist.length - 1], mine = (p.you_side === (hist.length % 2 ? 'w' : 'b'));
        var pcEl = this.cellAt(st.last[1]).querySelector('.ch-pc');
        if (pcEl && !mine && oldFen) this.slide(pcEl, st.last[0], st.last[1]);
        if (!mine || this.soundedLen !== hist.length) ctx.sfx(/x/.test(mv.san) ? 'capture' : 'move');
        if (!mine) ctx.buzz(12);
        if (/[+#]/.test(mv.san) && !mine) setTimeout(function () { ctx.sfx('tick'); }, 120);
      }
      this.soundedLen = hist.length;

      this.paintTrays(board, p);
      this.paintMoves(hist);
    },
    slide: function (pcEl, from, to) {
      var a = this.cellAt(from).getBoundingClientRect(), b = this.cellAt(to).getBoundingClientRect();
      if (!pcEl.animate) return;
      pcEl.style.zIndex = 5;
      pcEl.animate([{ transform: 'translate(' + (a.left - b.left) + 'px,' + (a.top - b.top) + 'px)' }, { transform: 'translate(0,0)' }], { duration: 190, easing: 'cubic-bezier(.3,.8,.3,1)' });
    },
    paintTrays: function (board, p) {
      var have = { w: { p: 0, n: 0, b: 0, r: 0, q: 0 }, b: { p: 0, n: 0, b: 0, r: 0, q: 0 } }, score = { w: 0, b: 0 };
      board.forEach(function (c) { if (c && c[1] !== 'k') { have[c[0]][c[1]]++; score[c[0]] += VALUE[c[1]]; } });
      var mySide = p.you_side || 'w', oppSide = mySide === 'w' ? 'b' : 'w';
      function fill(tray, owner) {                     // pieces that `owner` has captured = the other side's missing pieces
        var other = owner === 'w' ? 'b' : 'w'; tray.replaceChildren();
        ['q', 'r', 'b', 'n', 'p'].forEach(function (t) {
          var missing = Math.max(0, START[t] - have[other][t]);
          for (var i = 0; i < missing; i++) tray.appendChild(pieceSVG(other, t));
        });
        var adv = score[owner] - score[other];
        if (adv > 0) tray.appendChild(el('span', { class: 'adv', text: '+' + adv }));
      }
      fill(this.trayTop, oppSide); fill(this.trayBot, mySide);
    },
    paintMoves: function (hist) {
      var box = document.getElementById('ch-moves'); if (!box) return;
      box.replaceChildren();
      if (!hist.length) { box.appendChild(el('span', { class: 'n', style: 'grid-column:1/-1', text: 'No moves yet' })); return; }
      for (var i = 0; i < hist.length; i += 2) {
        box.appendChild(el('span', { class: 'n', text: (i / 2 + 1) + '.' }));
        box.appendChild(el('span', { class: 'm' + (i === hist.length - 1 ? ' cur' : ''), text: hist[i].san }));
        box.appendChild(el('span', { class: 'm' + (i + 1 === hist.length - 1 ? ' cur' : ''), text: hist[i + 1] ? hist[i + 1].san : '' }));
      }
      box.scrollTop = box.scrollHeight;
    },

    /* ----- selecting and moving ----- */
    canMove: function () { var p = this.p; return p && p.active && this.ctx.isMyTurn(p) && !this.ctx.busy() && !this.pending; },
    paintSel: function () {
      var self = this, p = this.p, board = parseBoard(p.state.fen);
      this.cells.forEach(function (sq) { sq.classList.remove('sel', 'dot', 'cap'); });
      if (!this.sel || !p.legal || !p.legal[this.sel]) return;
      this.cellAt(this.sel).classList.add('sel');
      var fromCode = board[idxOf(this.sel)];
      p.legal[this.sel].forEach(function (to) {
        var ep = fromCode[1] === 'p' && to[0] !== self.sel[0] && !board[idxOf(to)];
        self.cellAt(to).classList.add(board[idxOf(to)] || ep ? 'cap' : 'dot');
      });
    },
    clearSel: function () { this.sel = null; if (this.p) this.paintSel(); },
    mine: function (name) {
      var code = parseBoard(this.p.state.fen)[idxOf(name)];
      return !!code && code[0] === this.p.you_side;
    },
    down: function (e) {
      if (e.button > 0 || !this.canMove()) return;
      var name = this.nameAtPoint(e.clientX, e.clientY); if (!name) return;
      if (this.promo) { if (!e.target.closest('.ch-promo')) this.closePromo(); else return; }
      var legal = this.p.legal || {};
      if (this.sel && legal[this.sel] && legal[this.sel].indexOf(name) >= 0) { this.attempt(this.sel, name); e.preventDefault(); return; }
      if (this.mine(name) && legal[name]) {
        this.sel = name; this.paintSel();
        var pc = this.cellAt(name).querySelector('.ch-pc');
        this.drag = { from: name, pc: pc, x: e.clientX, y: e.clientY, active: false, id: e.pointerId };
        e.preventDefault();
      } else { this.clearSel(); }
    },
    move: function (e) {
      var d = this.drag; if (!d) return;
      if (!d.active) {
        if (Math.abs(e.clientX - d.x) + Math.abs(e.clientY - d.y) < 8) return;
        d.active = true;
        var ghost = d.pc.cloneNode(true); ghost.className = 'ch-pc drag'; document.body.appendChild(ghost); d.ghost = ghost;
        d.pc.classList.add('ghost');
      }
      d.ghost.style.left = e.clientX + 'px'; d.ghost.style.top = e.clientY + 'px';
      var over = this.nameAtPoint(e.clientX, e.clientY);
      this.cells.forEach(function (sq) { sq.classList.toggle('over', sq.dataset.sq === over && over !== d.from); });
    },
    up: function (e) {
      var d = this.drag; if (!d) return;
      var wasDrag = d.active, to = wasDrag ? this.nameAtPoint(e.clientX, e.clientY) : null, from = d.from;
      this.endDrag();
      if (wasDrag) {
        if (to && to !== from && (this.p.legal[from] || []).indexOf(to) >= 0) this.attempt(from, to, true);
        else this.paintSel();
      }
    },
    endDrag: function () {
      var d = this.drag; this.drag = null;
      if (d) { if (d.ghost) d.ghost.remove(); if (d.pc) d.pc.classList.remove('ghost'); }
      if (this.cells) this.cells.forEach(function (sq) { sq.classList.remove('over'); });
    },
    attempt: function (from, to, dragged) {
      var code = parseBoard(this.p.state.fen)[idxOf(from)];
      if (code[1] === 'p' && (to[1] === '8' || to[1] === '1')) { this.openPromo(from, to, code[0]); return; }
      this.commit(from, to, '', dragged);
    },
    commit: function (from, to, promo, dragged) {
      var self = this, ctx = this.ctx, board = parseBoard(this.p.state.fen), code = board[idxOf(from)], captured = !!board[idxOf(to)];
      this.pending = { from: from, to: to };
      // show the move straight away; the server's answer redraws everything (en passant, castling, …)
      var src = this.cellAt(from).querySelector('.ch-pc'), dst = this.cellAt(to);
      var old = dst.querySelector('.ch-pc'); if (old) old.remove();
      if (src) { dst.appendChild(src); src.classList.remove('ghost'); if (!dragged) this.slide(src, from, to); }
      if (promo && src) { src.replaceChildren(pieceSVG(code[0], promo)); }
      this.cells.forEach(function (sq) { sq.classList.remove('sel', 'dot', 'cap', 'last'); });
      this.cellAt(from).classList.add('last'); dst.classList.add('last');
      this.sel = null;
      ctx.sfx(captured ? 'capture' : 'move'); ctx.buzz(12);
      this.soundedLen = (this.p.state.history || []).length + 1;
      ctx.send('move', from + to + promo).then(function (j) {
        self.pending = null;
        if (j && j.error && self.p) self.update(self.p, null, ctx);       // put the piece back
      });
    },

    /* ----- pawn promotion ----- */
    openPromo: function (from, to, color) {
      var self = this, f = FILES.indexOf(to[0]), c = this.flip ? 7 - f : f, atTop = this.cellAt(to).parentNode && this.cells.indexOf(this.cellAt(to)) < 8;
      var box = el('div', { class: 'ch-promo' + (atTop ? '' : ' up'), style: 'left:' + (c * 12.5) + '%;' + (atTop ? 'top:0' : 'bottom:0') });
      ['q', 'n', 'r', 'b'].forEach(function (t) {
        var b = el('button', { type: 'button', 'aria-label': { q: 'Queen', n: 'Knight', r: 'Rook', b: 'Bishop' }[t] }, [pieceSVG(color, t)]);
        b.onclick = function (ev) { ev.stopPropagation(); self.closePromo(); self.commit(from, to, t, false); };
        box.appendChild(b);
      });
      this.board.appendChild(box); this.promo = box;
    },
    closePromo: function () { if (this.promo) { this.promo.remove(); this.promo = null; } },

    /* ----- the shell's hooks ----- */
    decorate: function (pl, p) {
      var side = p.state.colors.w === pl.id ? 'w' : 'b', out = { chip: side === 'w' ? '#f4f6fa' : '#2b3345', sub: side === 'w' ? 'White' : 'Black' };
      if (p.clock) out.clock = side;
      if (p.active && p.state.draw_offer === side) out.sub += ' · offers a draw';
      return out;
    },
    status: function (p, ctx) {
      if (!p.active) return null;
      var mine = ctx.isMyTurn(p), chk = p.state.check ? 'Check! ' : '', t = ctx.player(p.turn);
      return mine ? { text: chk + 'Your move', tone: 'you' } : { text: chk + 'Waiting for ' + (t ? t.name : 'your neighbour') + '…', tone: '' };
    },
    help: function (p) {
      if (!p.active) return '';
      return 'Tap a piece, then a highlighted square — or drag it there. ' + (p.clock && !p.clock.running ? 'Clocks start after both sides have moved.' : '');
    },
    actions: function (p, ctx, box) {
      var stale = document.getElementById('ch-draw-banner'); if (stale) stale.remove();
      if (!p.active) return;
      var offer = p.state.draw_offer, me = p.you_side, other = me === 'w' ? 'b' : 'w';
      if (offer === other) {
        var yes = el('button', { class: 'gm-btn', type: 'button', style: 'background:#22a35a;color:#fff;border-color:#22a35a' }, ['Accept']);
        var no = el('button', { class: 'gm-btn', type: 'button' }, ['Decline']);
        yes.onclick = function () { ctx.send('draw_accept'); }; no.onclick = function () { ctx.send('draw_decline'); };
        var ban = el('div', { class: 'ch-draw' }, [el('span', { text: '🤝 Your opponent offers a draw' }), yes, no]);
        ban.id = 'ch-draw-banner'; box.parentNode.insertBefore(ban, box.nextSibling);      // sits under the buttons
      } else {
        var b = el('button', { class: 'gm-btn', type: 'button' }, [el('i', { class: 'bi bi-hand-thumbs-up' }), offer === me ? 'Draw offered' : 'Offer draw']);
        if (offer === me) b.disabled = true;
        b.onclick = function () { ctx.send('draw_offer').then(function (j) { if (j && !j.error) ctx.toast('Draw offered'); }); };
        box.appendChild(b);
      }
    }
  });
})();
