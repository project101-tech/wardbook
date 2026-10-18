/* Tic-tac-toe and Connect Four */
(function () {
  'use strict';
  var G = window.WBGame, el = G.el, svg = G.svg;

  /* ============================== Tic-tac-toe ============================== */
  function mark(sym) {
    var s = svg('svg', { viewBox: '0 0 100 100' });
    if (sym === 'X') {
      s.appendChild(svg('path', { class: 'mark x', d: 'M22 22 L78 78 M78 22 L22 78', pathLength: 100 }));
    } else {
      s.appendChild(svg('circle', { class: 'mark o', cx: 50, cy: 50, r: 30, pathLength: 100 }));
    }
    return s;
  }
  G.register('tic_tac_toe', {
    cells: [],
    init: function (ctx, p) {
      var self = this, board = el('div', { class: 'ttt-board' });
      this.cells = [];
      for (var i = 0; i < 9; i++) {
        (function (i) {
          var c = el('button', { class: 'ttt-cell', type: 'button', 'aria-label': 'Square ' + (i + 1) });
          c.onclick = function () {
            if (!c.classList.contains('can') || ctx.busy()) return;
            var sym = self.me;                          // show my mark at once; the server confirms
            c.dataset.v = sym; c.replaceChildren(mark(sym)); c.classList.add('new'); c.classList.remove('can');
            ctx.sfx('move'); ctx.buzz(12); ctx.send('move', i);
          };
          self.cells.push(c); board.appendChild(c);
        })(i);
      }
      this.line = svg('svg', { viewBox: '0 0 300 300', style: 'position:absolute;inset:0;width:100%;height:100%;pointer-events:none' });
      board.appendChild(this.line);
      ctx.stage.appendChild(board);
    },
    update: function (p, prev, ctx) {
      var st = p.state, mine = ctx.isMyTurn(p), me = st.symbols[String(p.you)];
      this.me = me;
      var placed = false;
      for (var i = 0; i < 9; i++) {
        var c = this.cells[i], v = st.board[i] || '';
        if ((c.dataset.v || '') !== v) {
          c.dataset.v = v; c.replaceChildren(); c.classList.remove('new');
          if (v) { c.appendChild(mark(v)); if (!prev || (prev.state.board[i] || '') !== v) { c.classList.add('new'); placed = true; } }
        }
        c.dataset.ghost = me || '';
        c.classList.toggle('can', mine && !v);
        c.disabled = !(mine && !v);
        c.classList.toggle('last', st.last === i && p.active);
        c.classList.toggle('win', (st.win || []).indexOf(i) >= 0);
      }
      if (placed && prev && prev.turn !== p.you) ctx.sfx('move');
      this.line.replaceChildren();
      if (st.win && st.win.length === 3) {                   // strike through the winning three
        var a = st.win[0], b = st.win[2], pt = function (n) { return [(n % 3) * 100 + 50, Math.floor(n / 3) * 100 + 50]; };
        var A = pt(a), B = pt(b);
        this.line.appendChild(svg('line', { x1: A[0], y1: A[1], x2: B[0], y2: B[1], stroke: '#1c1e21', 'stroke-width': 9, 'stroke-linecap': 'round', opacity: .8, 'stroke-dasharray': 400, 'stroke-dashoffset': 400, style: 'transition:stroke-dashoffset .45s ease-out' }));
        var ln = this.line.firstChild; requestAnimationFrame(function () { requestAnimationFrame(function () { ln.setAttribute('stroke-dashoffset', 0); }); });
      }
    },
    decorate: function (pl, p) {
      var sym = p.state.symbols[String(pl.id)];
      return { chip: sym === 'X' ? '#1877f2' : '#f2455f', sub: 'Playing ' + sym };
    },
    help: function (p) { return p.active ? 'Get three in a row — across, down or diagonally.' : ''; }
  });

  /* ============================== Connect Four ============================== */
  G.register('connect_four', {
    init: function (ctx, p) {
      var self = this, wrap = el('div', { class: 'c4-wrap' }), board = el('div', { class: 'c4-board' });
      this.cols = []; this.holes = [];
      for (var c = 0; c < 7; c++) {
        (function (c) {
          var col = el('button', { class: 'c4-col', type: 'button', 'aria-label': 'Column ' + (c + 1) }), holes = [];
          for (var r = 0; r < 6; r++) { var h = el('div', { class: 'c4-hole' }); holes.push(h); col.appendChild(h); }
          col.onclick = function () {
            if (!col.classList.contains('can') || ctx.busy()) return;
            var row = -1; for (var r = 5; r >= 0; r--) if (!holes[r].firstElementChild) { row = r; break; }
            if (row < 0) return;
            self.put(holes[row], self.mySym, row, true);      // drop my disc at once
            ctx.sfx('move'); ctx.buzz(12); ctx.send('move', c);
          };
          self.cols.push(col); self.holes.push(holes); board.appendChild(col);
        })(c);
      }
      wrap.appendChild(board); ctx.stage.appendChild(wrap);
    },
    put: function (hole, sym, row, animate) {
      var d = el('div', { class: 'c4-disc ' + sym + (animate ? ' drop' : ''), 'data-v': sym });
      if (animate) d.style.cssText = '--from:' + (-(row + 1.4) * 100) + '%;--drop:' + (0.28 + row * 0.05).toFixed(2) + 's';
      hole.appendChild(d); return d;
    },
    update: function (p, prev, ctx) {
      var st = p.state, mine = ctx.isMyTurn(p), won = {}, newDisc = false;
      this.mySym = st.symbols[String(p.you)];
      (st.win || []).forEach(function (w) { won[w[0] + ',' + w[1]] = 1; });
      for (var c = 0; c < 7; c++) {
        var full = !!st.board[0][c];
        this.cols[c].classList.toggle('can', mine && !full); this.cols[c].disabled = !(mine && !full);
        for (var r = 0; r < 6; r++) {
          var hole = this.holes[c][r], v = st.board[r][c] || '', disc = hole.firstElementChild;
          if ((disc ? disc.dataset.v : '') !== v) {
            hole.replaceChildren();
            if (v) {
              var isNew = prev && !(prev.state.board[r][c]);
              this.put(hole, v, r, !!isNew); if (isNew) newDisc = true;
            }
            disc = hole.firstElementChild;
          }
          if (disc) {
            disc.classList.toggle('win', !!won[r + ',' + c]);
            disc.classList.toggle('last', !!(st.last && st.last[0] === r && st.last[1] === c) && p.active);
          }
        }
      }
      if (newDisc && prev && prev.turn !== p.you) setTimeout(function () { ctx.sfx('move'); }, 250);
    },
    decorate: function (pl, p) {
      var sym = p.state.symbols[String(pl.id)];
      return { chip: sym === 'R' ? '#e63946' : '#f2b01e', sub: sym === 'R' ? 'Red discs' : 'Yellow discs' };
    },
    help: function (p) { return p.active ? 'Tap a column to drop a disc. Line up four to win.' : ''; }
  });
})();
