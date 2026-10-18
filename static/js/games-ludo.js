/* Ludo: a 15 x 15 board drawn in SVG, tokens that hop square by square, captures, a tap-to-roll die. */
(function () {
  'use strict';
  var G = window.WBGame, el = G.el, svg = G.svg;
  var COLORS = ['#e63946', '#22a35a', '#f2b01e', '#1877f2'], SOFT = ['#fbd3d6', '#c9ecd8', '#fbe8b5', '#c8dcfc'], NAMES = ['Red', 'Green', 'Yellow', 'Blue'];
  var START = [0, 13, 26, 39], SAFE = [0, 13, 26, 39, 8, 21, 34, 47];
  var TRACK = (function () {                       // the 52 squares of the shared loop, clockwise from Red's start square
    var t = [];
    function run(x, y, dx, dy, n) { for (var i = 0; i < n; i++) t.push([x + dx * i, y + dy * i]); }
    run(1, 6, 1, 0, 5); run(6, 5, 0, -1, 6); run(7, 0, 1, 0, 2); run(8, 1, 0, 1, 5); run(9, 6, 1, 0, 6); run(14, 7, 0, 1, 2);
    run(13, 8, -1, 0, 5); run(8, 9, 0, 1, 6); run(7, 14, -1, 0, 2); run(6, 13, 0, -1, 5); run(5, 8, -1, 0, 6); run(0, 7, 0, -1, 2);
    return t;
  })();
  var HOME = [[[1, 7], [2, 7], [3, 7], [4, 7], [5, 7]], [[7, 1], [7, 2], [7, 3], [7, 4], [7, 5]], [[13, 7], [12, 7], [11, 7], [10, 7], [9, 7]], [[7, 13], [7, 12], [7, 11], [7, 10], [7, 9]]];
  var CORNER = [[0, 0], [9, 0], [9, 9], [0, 9]];
  var WELLS = [[2, 2], [4, 2], [2, 4], [4, 4]];
  var PILE = [[6.8, 7.5], [7.5, 6.8], [8.2, 7.5], [7.5, 8.2]];                      // where finished tokens rest, in each colour's triangle

  function xy(color, prog, i) {
    if (prog === -1) return [CORNER[color][0] + WELLS[i][0] + .0, CORNER[color][1] + WELLS[i][1] + .0];
    if (prog <= 50) { var c = TRACK[(START[color] + prog) % 52]; return [c[0] + .5, c[1] + .5]; }
    if (prog <= 55) { var h = HOME[color][prog - 51]; return [h[0] + .5, h[1] + .5]; }
    return [PILE[color][0] + (i % 2 ? .22 : -.22) * (color % 2 ? 0 : 1), PILE[color][1] + (i % 2 ? .22 : -.22) * (color % 2 ? 1 : 0)];
  }
  function star(cx, cy, r, fill) {
    var pts = []; for (var k = 0; k < 10; k++) { var a = -Math.PI / 2 + k * Math.PI / 5, rr = k % 2 ? r * .42 : r; pts.push((cx + Math.cos(a) * rr).toFixed(3) + ',' + (cy + Math.sin(a) * rr).toFixed(3)); }
    return svg('polygon', { points: pts.join(' '), fill: fill });
  }

  function drawBoard(active) {
    var s = svg('svg', { class: 'ludo-svg', viewBox: '-.2 -.2 15.4 15.4' });
    s.appendChild(svg('rect', { x: -.2, y: -.2, width: 15.4, height: 15.4, fill: '#f6f1e4' }));
    for (var c = 0; c < 4; c++) {                                              // yards
      var o = CORNER[c], on = active.indexOf(c) >= 0;
      s.appendChild(svg('rect', { x: o[0], y: o[1], width: 6, height: 6, rx: .5, fill: COLORS[c], opacity: on ? 1 : .4 }));
      s.appendChild(svg('rect', { x: o[0] + .9, y: o[1] + .9, width: 4.2, height: 4.2, rx: .6, fill: '#fff', opacity: on ? 1 : .8 }));
      WELLS.forEach(function (w) { s.appendChild(svg('circle', { cx: o[0] + w[0], cy: o[1] + w[1], r: .62, fill: SOFT[c], stroke: COLORS[c], 'stroke-width': .06, opacity: on ? 1 : .6 })); });
    }
    TRACK.forEach(function (t, i) {                                            // the loop
      var startOf = START.indexOf(i);
      s.appendChild(svg('rect', { x: t[0], y: t[1], width: 1, height: 1, fill: startOf >= 0 ? COLORS[startOf] : '#fff', stroke: '#c9d0de', 'stroke-width': .05 }));
      if (SAFE.indexOf(i) >= 0) s.appendChild(star(t[0] + .5, t[1] + .52, .32, startOf >= 0 ? '#fff' : '#a3adc0'));
    });
    HOME.forEach(function (col, c) { col.forEach(function (h) { s.appendChild(svg('rect', { x: h[0], y: h[1], width: 1, height: 1, fill: COLORS[c], stroke: 'rgba(255,255,255,.7)', 'stroke-width': .05, opacity: active.indexOf(c) >= 0 ? .92 : .45 })); }); });
    [[[6, 6], [6, 9], [7.5, 7.5]], [[6, 6], [9, 6], [7.5, 7.5]], [[9, 6], [9, 9], [7.5, 7.5]], [[6, 9], [9, 9], [7.5, 7.5]]].forEach(function (tri, c) {
      s.appendChild(svg('polygon', { points: tri.map(function (q) { return q.join(','); }).join(' '), fill: COLORS[c], stroke: '#fff', 'stroke-width': .06, opacity: active.indexOf(c) >= 0 ? 1 : .45 }));
    });
    s.appendChild(svg('rect', { x: 0, y: 0, width: 15, height: 15, fill: 'none', stroke: '#2b2f4a', 'stroke-width': .12 }));
    return s;
  }

  G.register('ludo', {
    init: function (ctx, p) {
      var self = this, st = p.state;
      this.colorOf = {}; Object.keys(st.colors).forEach(function (u) { self.colorOf[u] = st.colors[u]; });
      this.wrap = el('div', { class: 'ludo-wrap' }); this.board = drawBoard(Object.keys(st.colors).map(function (u) { return st.colors[u]; })); this.wrap.appendChild(this.board);
      this.layer = svg('g'); this.board.appendChild(this.layer);
      this.toks = {}; this.prog = {}; this.q = Promise.resolve(); this.seq = st.seq || 0; this.busyAnim = false; this.ctx = ctx;
      Object.keys(st.tokens).forEach(function (u) {
        var color = self.colorOf[u]; self.toks[u] = []; self.prog[u] = st.tokens[u].slice();
        for (var i = 0; i < 4; i++) {
          (function (i) {
            var g = svg('g', { class: 'ludo-tok', 'data-u': u }, [
              svg('circle', { class: 'ring', r: .6, cx: 0, cy: 0 }),
              svg('circle', { r: .47, cx: 0, cy: .06, fill: 'rgba(0,0,0,.28)' }),
              svg('circle', { r: .45, fill: COLORS[color], stroke: '#fff', 'stroke-width': .09 }),
              svg('circle', { r: .24, fill: 'rgba(255,255,255,.28)' }),
              svg('circle', { r: .11, cx: -.13, cy: -.15, fill: 'rgba(255,255,255,.75)' })]);
            g.addEventListener('click', function () { if (g.classList.contains('can') && !ctx.busy() && !self.busyAnim) { G.buzz(12); ctx.send('move', i); } });
            self.toks[u].push(g); self.layer.appendChild(g);
          })(i);
        }
      });
      var dieBox = el('div', { class: 'die-wrap' }); this.die = G.makeDie(); this.dieLabel = el('div', { class: 'die-label' });
      dieBox.appendChild(this.die); dieBox.appendChild(this.dieLabel);
      ctx.stage.appendChild(this.wrap); ctx.stage.appendChild(dieBox);
      this.die.onclick = function () {
        if (!self.die.classList.contains('can') || ctx.busy() || self.busyAnim) return;
        self.die.classList.remove('can'); G.buzz(15); G.tumble(self.die, 6000, 0); ctx.send('roll');
      };
      G.showDie(this.die, 1);
    },
    place: function () {
      var self = this, buckets = {};
      Object.keys(this.prog).forEach(function (u) { self.prog[u].forEach(function (pr, i) { var c = self.colorOf[u], pt = xy(c, pr, i), k = pr === -1 || pr === 56 ? 'x' + u + i : pt[0] + ',' + pt[1]; (buckets[k] = buckets[k] || []).push([u, i, pt]); }); });
      var off = [[[0, 0]], [[-.2, 0], [.2, 0]], [[-.2, -.15], [.2, -.15], [0, .2]], [[-.2, -.2], [.2, -.2], [-.2, .2], [.2, .2]]];
      Object.keys(buckets).forEach(function (k) {
        var list = buckets[k], n = list.length;
        list.forEach(function (e, idx) {
          var o = off[Math.min(4, n) - 1][idx % 4], scale = n > 1 ? .72 : 1, g = self.toks[e[0]][e[1]];
          g.style.transform = 'translate(' + (e[2][0] + o[0]) + 'px,' + (e[2][1] + o[1]) + 'px) scale(' + scale + ')';
        });
      });
    },
    update: function (p, prev, ctx) {
      var self = this; this.q = this.q.then(function () { return self.run(p, prev, ctx); });
    },
    sync: function (st) { var self = this; Object.keys(st.tokens).forEach(function (u) { self.prog[u] = st.tokens[u].slice(); }); this.place(); },
    run: function (p, prev, ctx) {
      var self = this, st = p.state, last = st.last, fresh = prev && last && (st.seq || 0) !== this.seq;
      var done = function () { self.sync(st); self.seq = st.seq || 0; self.busyAnim = false; self.paint(p, ctx); };
      if (!fresh) { if (!prev && last) G.showDie(self.die, last.roll); return Promise.resolve(done()); }
      self.busyAnim = true; var u = String(last.uid), mine = last.uid === p.you, who = mine ? 'You' : ((ctx.player(last.uid) || {}).name || 'Neighbour');
      var start = mine ? G.sleep(250).then(function () { G.stopTumble(self.die, last.roll); }) : G.tumble(self.die, 550, last.roll);
      return start.then(function () {
        self.dieLabel.textContent = who + ' rolled ' + last.roll;
        if (last.token === null || last.token === undefined) {
          if (last.note === 'no move') ctx.toast(who + (mine ? ' have' : ' has') + ' no move with a ' + last.roll);
          else if (last.note === 'three sixes') ctx.toast('Three sixes in a row — turn lost!');
          return G.sleep(450);
        }
        var chain = Promise.resolve(), i = last.token, from = last.from, to = last.to;
        if (from === -1) chain = chain.then(function () { self.prog[u][i] = 0; self.place(); G.sfx('hop'); return G.sleep(260); });
        else for (var q = from + 1; q <= to; q++) (function (q) { chain = chain.then(function () { self.prog[u][i] = q; self.place(); G.sfx('hop'); return G.sleep(135); }); })(q);
        chain = chain.then(function () {
          if (last.captured && last.captured.length) {
            G.sfx('capture'); G.buzz(30);
            last.captured.forEach(function (cp) { self.prog[String(cp.uid)][cp.token] = -1; });
            var names = last.captured.map(function (cp) { return cp.uid === p.you ? 'your' : ((ctx.player(cp.uid) || {}).name || 'a neighbour') + "'s"; });
            ctx.toast(who + (mine ? ' sent ' : ' sent ') + names.join(' and ') + ' token' + (last.captured.length > 1 ? 's' : '') + ' back to the yard!');
            self.place(); return G.sleep(700);
          }
          if (to === 56) { G.sfx('win'); ctx.toast(who + (mine ? ' got' : ' got') + ' a token home!'); return G.sleep(600); }
        });
        return chain;
      }).then(done);
    },
    paint: function (p, ctx) {
      var self = this, st = p.state, mine = ctx.isMyTurn(p), pending = st.dice;
      var movable = mine && pending ? p.movable : [];
      Object.keys(this.toks).forEach(function (u) { self.toks[u].forEach(function (g, i) { g.classList.toggle('can', u === String(p.you) && movable.indexOf(i) >= 0); }); });
      var canRoll = mine && !pending; this.die.classList.toggle('can', canRoll); this.die.disabled = !canRoll;
      if (mine && pending) { G.showDie(this.die, pending); this.dieLabel.textContent = 'Tap a glowing token to move ' + pending; }
      else if (canRoll) { var again = st.last && st.last.uid === p.you && (st.last.roll === 6 || (st.last.captured || []).length); this.dieLabel.textContent = again ? 'Bonus roll!' : 'Your turn — tap the dice'; }
      else if (!p.active) this.dieLabel.textContent = '';
      else { var t = ctx.player(p.turn); this.dieLabel.textContent = (t ? t.name : 'Neighbour') + (pending ? ' is choosing a token…' : ' is rolling…'); if (pending && !mine) G.showDie(this.die, pending); }
    },
    status: function (p, ctx) {
      if (p.active && ctx.isMyTurn(p) && p.state.dice) return { text: 'Pick a token', tone: 'you' };
      return null;
    },
    decorate: function (pl, p) {
      var c = p.state.colors[String(pl.id)], toks = p.state.tokens[String(pl.id)] || [], home = toks.filter(function (x) { return x === 56; }).length;
      return { chip: COLORS[c], sub: NAMES[c] + ' · ' + home + '/4 home' };
    },
    help: function (p) { return p.active ? 'Roll a six to bring a token out. Land on a rival to send them back (stars are safe). A six, a capture or getting home gives a bonus roll. Home needs an exact roll.' : ''; }
  });
})();
