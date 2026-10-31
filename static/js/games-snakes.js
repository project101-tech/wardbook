/* Snakes & Ladders: a drawn board with wooden ladders and winding snakes, hopping tokens, a tap-to-roll die. */
(function () {
  'use strict';
  var G = window.WBGame, el = G.el, svg = G.svg;
  var COLORS = ['#e63946', '#1877f2', '#22a35a', '#f2b01e'], NAMES = ['Red', 'Blue', 'Green', 'Yellow'];
  var SNAKE_COLORS = [['#2fa35f', '#bff0cf'], ['#8e5bd6', '#ddc9ff'], ['#e8793a', '#ffd9bd'], ['#1fa3b8', '#bdeff7'], ['#d94a7a', '#ffc9dc'], ['#6c8f2a', '#dff0b0'], ['#c9a227', '#fff1b3']];

  function center(n) {                               // square number -> middle of the square (board is 100 x 100)
    var i = n - 1, row = Math.floor(i / 10), c = i % 10, col = row % 2 ? 9 - c : c;
    return [col * 10 + 5, (9 - row) * 10 + 5];
  }

  function drawBoard(st) {
    var s = svg('svg', { class: 'sl-svg', viewBox: '0 0 100 112' });
    var defs = svg('defs'); s.appendChild(defs);
    var tint = ['#fff6d8', '#ffe9b0', '#e3f4ff', '#d4ecff'];
    for (var n = 1; n <= 100; n++) {
      var c = center(n), x = c[0] - 5, y = c[1] - 5, row = Math.floor((n - 1) / 10), col = (n - 1) % 10, odd = (row + col) % 2;
      s.appendChild(svg('rect', { x: x, y: y, width: 10, height: 10, fill: n === 100 ? '#ffe27a' : n === 1 ? '#c8f0d6' : tint[odd + (row % 2 ? 2 : 0)], stroke: '#fff', 'stroke-width': .3 }));
      var t = svg('text', { x: x + 1, y: y + 3.2, 'font-size': 2.6, fill: '#8a7a4a', 'font-weight': 700, opacity: .85, 'font-family': 'inherit' }); t.textContent = n === 100 ? '100 ★' : n;
      s.appendChild(t);
    }
    // ladders
    Object.keys(st.ladders).forEach(function (k) {
      var a = center(+k), b = center(st.ladders[k]), dx = b[0] - a[0], dy = b[1] - a[1], L = Math.hypot(dx, dy), nx = -dy / L, ny = dx / L, off = 1.7;
      var g = svg('g');
      [-1, 1].forEach(function (side) { g.appendChild(svg('line', { x1: a[0] + nx * off * side, y1: a[1] + ny * off * side, x2: b[0] + nx * off * side, y2: b[1] + ny * off * side, stroke: '#7a4310', 'stroke-width': 1.5, 'stroke-linecap': 'round', opacity: .35, transform: 'translate(.5 .7)' })); });
      for (var r = 1.2; r < L - 1; r += 3.6) {
        var px = a[0] + dx * r / L, py = a[1] + dy * r / L;
        g.appendChild(svg('line', { x1: px - nx * off, y1: py - ny * off, x2: px + nx * off, y2: py + ny * off, stroke: '#c98a3c', 'stroke-width': 1, 'stroke-linecap': 'round' }));
      }
      [-1, 1].forEach(function (side) { g.appendChild(svg('line', { x1: a[0] + nx * off * side, y1: a[1] + ny * off * side, x2: b[0] + nx * off * side, y2: b[1] + ny * off * side, stroke: '#a8651d', 'stroke-width': 1.3, 'stroke-linecap': 'round' })); });
      s.appendChild(g);
    });
    // snakes: a wavy tapering body from head to tail
    Object.keys(st.snakes).forEach(function (k, idx) {
      var h = center(+k), tl = center(st.snakes[k]), dx = tl[0] - h[0], dy = tl[1] - h[1], L = Math.hypot(dx, dy), ux = dx / L, uy = dy / L, nx = -uy, ny = ux;
      var col = SNAKE_COLORS[idx % SNAKE_COLORS.length], N = 44, waves = Math.max(1.5, Math.round(L / 13) + .5), amp = 2.6, pts = [];
      for (var i = 0; i <= N; i++) { var t = i / N, w = Math.sin(t * Math.PI * waves) * amp * Math.sin(Math.min(1, t * 6) * Math.PI / 2); pts.push([h[0] + dx * t + nx * w, h[1] + dy * t + ny * w]); }
      var left = [], right = [];
      for (var j = 0; j <= N; j++) {
        var p0 = pts[Math.max(0, j - 1)], p1 = pts[Math.min(N, j + 1)], tx = p1[0] - p0[0], ty = p1[1] - p0[1], tl2 = Math.hypot(tx, ty) || 1, qx = -ty / tl2, qy = tx / tl2, wd = 2.5 - 2.0 * Math.pow(j / N, .8);
        left.push([pts[j][0] + qx * wd, pts[j][1] + qy * wd]); right.push([pts[j][0] - qx * wd, pts[j][1] - qy * wd]);
      }
      var d = 'M' + left.map(function (q) { return q[0].toFixed(2) + ' ' + q[1].toFixed(2); }).join(' L') + ' L' + right.reverse().map(function (q) { return q[0].toFixed(2) + ' ' + q[1].toFixed(2); }).join(' L') + ' Z';
      var g = svg('g');
      g.appendChild(svg('path', { d: d, fill: '#000', opacity: .18, transform: 'translate(.6 .8)' }));
      g.appendChild(svg('path', { d: d, fill: col[0], stroke: 'rgba(0,0,0,.25)', 'stroke-width': .3, 'stroke-linejoin': 'round' }));
      g.appendChild(svg('polyline', { points: pts.map(function (q) { return q[0].toFixed(2) + ',' + q[1].toFixed(2); }).join(' '), fill: 'none', stroke: col[1], 'stroke-width': .9, 'stroke-dasharray': '.1 2.1', 'stroke-linecap': 'round', opacity: .95 }));
      var ang = Math.atan2(-uy, -ux) * 180 / Math.PI;           // the head looks away from the tail
      var head = svg('g', { transform: 'translate(' + h[0] + ' ' + h[1] + ') rotate(' + ang + ')' });
      head.appendChild(svg('path', { d: 'M3.2 0 L6.6 -.8 M3.2 0 L6.6 .8', stroke: '#e63946', 'stroke-width': .5, 'stroke-linecap': 'round', fill: 'none' }));
      head.appendChild(svg('ellipse', { cx: 0, cy: 0, rx: 3.4, ry: 2.9, fill: col[0], stroke: 'rgba(0,0,0,.3)', 'stroke-width': .3 }));
      [-1.4, 1.4].forEach(function (yy) { head.appendChild(svg('circle', { cx: 1.1, cy: yy, r: .95, fill: '#fff' })); head.appendChild(svg('circle', { cx: 1.35, cy: yy, r: .45, fill: '#111' })); });
      g.appendChild(head); s.appendChild(g);
    });
    // start tray under the board
    s.appendChild(svg('rect', { x: 1, y: 101.5, width: 98, height: 9.5, rx: 3, fill: '#e9f6ee', stroke: '#b9dcc6', 'stroke-width': .3 }));
    var lbl = svg('text', { x: 97, y: 107.6, 'font-size': 2.8, 'text-anchor': 'end', fill: '#4f8a68', 'font-weight': 800 }); lbl.textContent = 'START'; s.appendChild(lbl);
    return s;
  }

  G.register('snakes_ladders', {
    init: function (ctx, p) {
      var self = this, st = p.state;
      this.wrap = el('div', { class: 'sl-wrap' }); this.board = drawBoard(st); this.wrap.appendChild(this.board);
      this.tokLayer = svg('g'); this.board.appendChild(this.tokLayer);
      this.toks = {}; this.pos = {}; this.q = Promise.resolve(); this.seq = st.seq || 0; this.busyAnim = false;
      (p.players || []).forEach(function (pl) {
        var col = COLORS[+st.symbols[String(pl.id)] % 4];
        var g = svg('g', { class: 'sl-tok' }, [svg('circle', { r: 2.9, cx: 0, cy: .5, fill: 'rgba(0,0,0,.25)' }), svg('circle', { r: 2.7, fill: col, stroke: '#fff', 'stroke-width': .7 }), svg('circle', { r: 1.1, cx: -.8, cy: -.9, fill: 'rgba(255,255,255,.55)' })]);
        self.toks[pl.id] = g; self.tokLayer.appendChild(g);
      });
      var dieBox = el('div', { class: 'die-wrap' }); this.die = G.makeDie(); this.dieLabel = el('div', { class: 'die-label' });
      dieBox.appendChild(this.die); dieBox.appendChild(this.dieLabel);
      ctx.stage.appendChild(this.wrap); ctx.stage.appendChild(dieBox);
      this.die.onclick = function () {
        if (!self.die.classList.contains('can') || ctx.busy() || self.busyAnim) return;
        self.die.classList.remove('can'); G.buzz(15); G.tumble(self.die, 6000, 0);            // keeps shaking until the server answers
        ctx.send('roll');
      };
      G.showDie(this.die, 1); this.ctx = ctx;
    },
    pos2xy: function (uid, n, group) {
      if (n === 0) { var seat = this.seatOf(uid); return [8 + seat * 8, 106.3]; }
      var c = center(n), k = group.length, idx = group.indexOf(uid), off = [[0, 0], [[-1.9, 0], [1.9, 0]], [[-1.9, -1.5], [1.9, -1.5], [0, 1.9]], [[-1.9, -1.9], [1.9, -1.9], [-1.9, 1.9], [1.9, 1.9]]];
      var o = k === 1 ? off[0] : off[Math.min(3, k - 1)][idx]; return [c[0] + o[0], c[1] + o[1] + 1];
    },
    seatOf: function (uid) { return this.seats[uid] || 0; },
    place: function () {
      var self = this, byPos = {};
      Object.keys(this.pos).forEach(function (u) { (byPos[self.pos[u]] = byPos[self.pos[u]] || []).push(+u); });
      Object.keys(this.pos).forEach(function (u) {
        var n = self.pos[u], xy = self.pos2xy(+u, n, byPos[n]); self.toks[u].style.transform = 'translate(' + xy[0] + 'px,' + xy[1] + 'px)';
      });
    },
    update: function (p, prev, ctx) {
      var self = this; this.seats = {}; p.players.forEach(function (pl) { self.seats[pl.id] = pl.seat; });
      var st = p.state;
      this.q = this.q.then(function () { return self.run(p, prev, ctx); });
    },
    run: function (p, prev, ctx) {
      var self = this, st = p.state, last = st.last, fresh = prev && last && (st.seq || 0) !== this.seq;
      var done = function () {
        self.pos = {}; Object.keys(st.positions).forEach(function (u) { self.pos[u] = st.positions[u]; }); self.place();
        self.seq = st.seq || 0; self.busyAnim = false; self.paintDie(p, ctx);
      };
      if (!fresh) { if (!prev) { Object.keys(st.positions).forEach(function (u) { self.pos[u] = st.positions[u]; }); self.place(); if (last) G.showDie(self.die, last.roll); } return Promise.resolve(done()); }
      self.busyAnim = true; var uid = String(last.uid), mine = last.uid === p.you;
      var start = Promise.resolve();
      if (!mine) start = G.tumble(self.die, 550, last.roll);
      else { start = G.sleep(250).then(function () { G.stopTumble(self.die, last.roll); }); }
      return start.then(function () {
        self.dieLabel.textContent = (mine ? 'You' : ((ctx.player(last.uid) || {}).name || 'Neighbour')) + ' rolled ' + last.roll;
        var hops = [], from = last.from; for (var c = from + 1; c <= last.landed; c++) hops.push(c);
        var chain = Promise.resolve();
        if (last.bounced) chain = chain.then(function () { ctx.toast('Need exactly ' + (100 - last.from) + ' to finish'); return G.sleep(400); });
        hops.forEach(function (c) { chain = chain.then(function () { self.pos[uid] = c; self.place(); G.sfx('hop'); return G.sleep(140); }); });
        if (last.type) chain = chain.then(function () { return G.sleep(260); }).then(function () {
          ctx.toast(last.type === 'ladder' ? '🪜 Ladder! Up to ' + last.to : '🐍 Snake! Down to ' + last.to);
          G.sfx(last.type === 'ladder' ? 'turn' : 'capture'); self.toks[uid].style.transitionDuration = '.7s'; self.pos[uid] = last.to; self.place(); return G.sleep(750);
        }).then(function () { self.toks[uid].style.transitionDuration = ''; });
        return chain;
      }).then(done);
    },
    paintDie: function (p, ctx) {
      var can = ctx.isMyTurn(p);
      this.die.classList.toggle('can', can); this.die.disabled = !can;
      if (can) this.dieLabel.textContent = (p.state.last && p.state.last.uid === p.you && p.state.last.roll === 6 ? 'A six! Roll again' : 'Your turn — tap the dice');
      else if (!p.active) this.dieLabel.textContent = '';
      else { var t = ctx.player(p.turn); this.dieLabel.textContent = (t ? t.name : 'Neighbour') + ' is rolling…'; if (p.state.last) this.dieLabel.textContent = this.dieLabel.textContent; }
    },
    decorate: function (pl, p) {
      var i = +p.state.symbols[String(pl.id)] % 4;
      var pos = p.state.positions[String(pl.id)];
      return { chip: COLORS[i], sub: pos ? 'Square ' + pos : 'At the start' };
    },
    help: function (p) { return p.active ? 'Ladders lift you up, snakes slide you down. You need the exact number to land on 100. A six gives another roll.' : ''; }
  });
})();
