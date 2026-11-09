/* Blackjack: the table. The dealer (a neighbour) sits at the top and presses the buttons to deal and draw; everybody else
   sits below with their cards face up and their name under them. The rules live on the server (core/blackjack_engine.py);
   this file only draws what the server sends and sends back what the player pressed.
   Points are play points, not money. The server counts in half points, so we halve them for the screen. */
(function () {
  'use strict';
  var G = window.WBGame;
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  var SUIT = { S: '♠︎', H: '♥︎', D: '♦︎', C: '♣︎' };
  var RANK = { 1: 'A', 11: 'J', 12: 'Q', 13: 'K' };
  function rk(r) { return RANK[r] || String(r); }
  function pts(n) {                                        // half points -> "+1.5", "−1", "0"
    if (!n) return '0';
    var v = Math.abs(n) / 2;
    return (n > 0 ? '+' : '−') + (v % 1 ? v.toFixed(1) : String(v));
  }
  function plain(n) { var v = Math.abs(n) / 2; return (n < 0 ? '−' : '') + (v % 1 ? v.toFixed(1) : String(v)); }

  function card(c, o) {
    o = o || {};
    var d;
    if (!c) {
      d = el('div', 'bj-card back');
    } else {
      var red = c[1] === 'H' || c[1] === 'D';
      d = el('div', 'bj-card face' + (red ? ' red' : ''));
      d.appendChild(el('span', 'cn', rk(c[0]) + SUIT[c[1]]));
      d.appendChild(el('span', 'cs', SUIT[c[1]]));
    }
    if (o.peek) d.classList.add('peek');
    if (o.fresh) { d.classList.add('deal'); d.style.animationDelay = (o.delay || 0) + 'ms'; }
    return d;
  }
  function overlap(n) { return n <= 2 ? 'ov0' : n === 3 ? 'ov1' : n === 4 ? 'ov2' : n === 5 ? 'ov3' : 'ov4'; }

  G.register('blackjack', {
    seen: {}, lastN: -1, lastRound: -1, fired: -1,

    init: function (ctx, p) {
      var self = this; this.ctx = ctx;
      var root = el('div', 'bj');
      this.bar = el('div', 'bj-bar');
      this.felt = el('div', 'bj-felt');
      this.dealerBox = el('div', 'bj-dealer');
      this.banner = el('div', 'bj-banner');
      this.seats = el('div', 'bj-seats');
      this.felt.appendChild(this.dealerBox); this.felt.appendChild(this.banner); this.felt.appendChild(this.seats);
      this.acts = el('div', 'bj-acts');
      this.result = el('div', 'bj-result');
      this.score = el('div', 'bj-score');
      this.logBox = el('div', 'bj-log');
      this.rules = this.buildRules();
      [this.bar, this.felt, this.acts, this.result, this.score, this.logBox, this.rules].forEach(function (n) { root.appendChild(n); });
      ctx.stage.appendChild(root); ctx.stage.classList.add('bj-stage');
      setInterval(function () { self.tick(); }, 250);
    },

    name: function (id) { var pl = this.ctx.player(id); return pl ? pl.name : 'Someone'; },
    who: function (id) { return id === this.ctx.me ? 'You' : this.name(id); },

    do: function (action) {
      var self = this, ctx = this.ctx;
      if (this.working) return; this.working = true;
      Array.prototype.forEach.call(this.acts.querySelectorAll('button'), function (b) { b.disabled = true; });
      if (action !== 'leave') { ctx.sfx('hop'); ctx.buzz(15); }
      ctx.send('bj', action).then(function () { self.working = false; if (self.p) self.render(self.p); });
    },

    /* ---------- a new payload from the server ---------- */
    update: function (p, prev, ctx) {
      this.p = p; this.working = false;
      var b = p.bj;
      if (b.phase === 'over' && b.summary) {
        var mine = b.summary.filter(function (r) { return r.id === p.you; })[0];
        if (mine) p.reason = (p.reason ? p.reason + ' · ' : '') + 'you finished on ' + plain(mine.score) + ' point' + (Math.abs(mine.score) === 2 ? '' : 's') + ' and won ' + mine.wins + ' game' + (mine.wins === 1 ? '' : 's');
      }
      this.render(p);
      var self = this, fresh = (b.log || []).filter(function (e) { return e.n > self.lastN; });
      if (this.lastN >= 0) {
        fresh.forEach(function (e) {
          if (e.a === 'deal') ctx.sfx('move');
          else if (e.a === 'hit' || e.a === 'draw' || e.a === 'double' || e.a === 'split') { if (e.u !== p.you) ctx.sfx('hop'); }
          else if (e.a === 'bust' && e.u === p.you) ctx.sfx('error');
          else if (e.a === 'shuffle') ctx.sfx('dice');
          else if (e.a === 'settle') self.settleSound(p);
        });
      }
      if (b.log && b.log.length) this.lastN = b.log[b.log.length - 1].n;
    },
    settleSound: function (p) {
      var row = ((p.bj.last || {}).rows || []).filter(function (r) { return r.id === p.you; })[0], net = row ? row.net : (p.bj.last || {}).dealer_net;
      if (net > 0) { this.ctx.sfx('win'); this.ctx.buzz(20); } else if (net < 0) this.ctx.sfx('lose'); else this.ctx.sfx('tick');
    },

    status: function (p) {
      var b = p.bj, by = this.optMap(b);
      if (!p.active) return null;
      if (by.deal) return { text: 'Your deal: press Deal cards', tone: 'you' };
      if (by['ins:yes']) return { text: 'Insurance?', tone: 'you' };
      if (by.hit) return { text: 'Your turn: hit or stand', tone: 'you' };
      if (by.draw) return { text: 'Draw a card', tone: 'you' };
      if (by.next) return { text: 'Round over', tone: '' };
      var d = this.name(b.dealer_id);
      if (b.phase === 'deal') return { text: 'Waiting for ' + d + ' to deal…', tone: '' };
      if (b.phase === 'insurance') return { text: 'Insurance…', tone: '' };
      if (b.phase === 'play' && b.turn) return { text: this.name(b.turn.u) + ' is playing…', tone: '' };
      if (b.phase === 'dealer') return { text: d + ' is drawing…', tone: '' };
      return { text: 'Round ' + b.round_no, tone: '' };
    },
    help: function (p) {
      var b = p.bj, by = this.optMap(b);
      if (by.deal) return 'Everyone gets two cards, you get one face up and one face down.';
      if (by.draw) return 'The dealer must draw on 16 or less and stand on 17 or more.';
      if (by.hit) return 'An Ace counts 11 until that would bust you, then it counts 1.';
      return '';
    },
    optMap: function (b) { var m = {}; (b.options || []).forEach(function (o) { m[o.a] = o; }); return m; },

    /* ---------- drawing ---------- */
    render: function (p) {
      var b = p.bj;
      if (this.lastRound !== b.round_no) { this.seen = {}; this.lastRound = b.round_no; }
      this.delay = 0;
      this.renderBar(b); this.renderDealer(b); this.renderBanner(b); this.renderSeats(b);
      this.renderActs(p); this.renderResult(p); this.renderScore(p); this.renderLog(p);
      this.tick();
    },

    renderBar: function (b) {
      this.bar.replaceChildren();
      var left = el('div', 'bj-where');
      left.appendChild(el('b', '', 'Game ' + b.game + ' of ' + b.games));
      left.appendChild(el('span', '', b.phase === 'over' ? 'Finished' : 'Round ' + Math.min(b.rb, b.rpd) + ' of ' + b.rpd + ' with ' + this.name(b.dealer_id) + ' dealing'));
      this.bar.appendChild(left);
      this.timerBox = el('div', 'bj-timer'); this.timerBar = el('i'); this.timerTxt = el('span');
      this.timerBox.appendChild(this.timerBar); this.timerBox.appendChild(this.timerTxt);
      this.bar.appendChild(this.timerBox);
    },

    cardNode: function (key, c, extra) {
      var fresh = !this.seen[key] && !!c;
      if (c) this.seen[key] = 1;
      var n = card(c, { fresh: fresh, delay: this.delay, peek: extra && extra.peek });
      if (fresh) this.delay += 130;
      return n;
    },

    renderDealer: function (b) {
      var box = this.dealerBox, d = b.dealer, self = this;
      box.replaceChildren();
      if (!d) return;
      var isMe = d.id === this.p.you;
      box.appendChild(el('div', 'bj-role', 'DEALER'));
      var cards = el('div', 'bj-cards ' + overlap(d.cards.length));
      if (!d.cards.length) {
        cards.appendChild(el('div', 'bj-empty', b.phase === 'deal' ? (isMe ? 'Your cards will appear here' : 'No cards yet') : ''));
      }
      d.cards.forEach(function (c, i) { cards.appendChild(self.cardNode('d:' + i, c, { peek: d.peek && i === 1 })); });
      box.appendChild(cards);
      var nm = el('div', 'bj-name');
      nm.appendChild(el('span', '', this.name(d.id)));
      if (isMe) nm.appendChild(el('em', 'me-tag', 'YOU'));
      box.appendChild(nm);
      var chips = el('div', 'bj-chips');
      if (d.text) chips.appendChild(el('span', 'bj-tot' + (d.bj ? ' bj' : /Bust/.test(d.text) ? ' bust' : ''), d.bj ? 'Blackjack!' : d.text));
      if (d.peek) chips.appendChild(el('span', 'bj-hint', 'Only you can see your face-down card'));
      else if (d.hole_hidden) chips.appendChild(el('span', 'bj-hint', 'One card face down'));
      box.appendChild(chips);
    },

    renderBanner: function (b) {
      var t = '', me = this.p.you, by = this.optMap(b), d = this.name(b.dealer_id), tone = '';
      if (b.phase === 'deal') {
        t = by.deal ? 'Your deal. Press “Deal cards” when everybody is ready.' : 'Waiting for ' + d + ' to deal the cards.';
        if (b.round_no === 1) t += ' ' + d + ' was picked at random to deal first.';
      } else if (b.phase === 'insurance') {
        var waiting = b.players.filter(function (x) { return x.ins === 'pending'; }).map(function (x) { return x.id === me ? 'you' : this.name(x.id); }, this);
        t = d + ' shows an Ace. Insurance costs half a point and pays 2 to 1 if the dealer has blackjack.' + (waiting.length ? ' Waiting for ' + waiting.join(', ') + '.' : '');
        tone = 'on';
      } else if (b.phase === 'play' && b.turn) {
        var seat = b.players.filter(function (x) { return x.id === b.turn.u; })[0], many = seat && seat.hands && seat.hands.length > 1;
        t = (b.turn.u === me ? 'Your turn' : this.name(b.turn.u) + '’s turn') + (many ? ' (hand ' + (b.turn.h + 1) + ' of ' + seat.hands.length + ')' : '') + '.';
      } else if (b.phase === 'dealer') {
        t = by.draw ? 'You must draw: the dealer draws on 16 or less.' : d + ' must draw a card (16 or less).';
        tone = 'on';
      } else if (b.phase === 'result') {
        t = (b.last && b.last.dealer_bj) ? d + ' has blackjack.' : (b.last && b.last.dealer_bust) ? d + ' busts with ' + b.last.dealer_text.replace('Bust ', '') + '.' : d + ' finishes on ' + (b.last ? b.last.dealer_text : '') + '.';
        if (b.shuffled) t += ' New shoe shuffled.';
        tone = 'on';
      } else if (b.phase === 'over') {
        t = 'Match over.'; tone = 'on';
      }
      this.banner.className = 'bj-banner' + (tone ? ' ' + tone : '');
      this.banner.textContent = t;
    },

    renderSeats: function (b) {
      var self = this, me = this.p.you, active = this.p.active;
      this.seats.replaceChildren();
      var list = b.players.filter(function (x) { return !x.dealer; });
      this.seats.className = 'bj-seats n' + Math.min(list.length, 6);
      list.forEach(function (pl) {
        var seat = el('div', 'bj-seat' + (pl.id === me ? ' mine' : '') + (pl.left ? ' left' : '') + (b.turn && b.turn.u === pl.id ? ' turn' : ''));
        var hands = el('div', 'bj-hands' + ((pl.hands || []).length > 1 ? ' split' : ''));
        if (!pl.hands) hands.appendChild(el('div', 'bj-cards ' + overlap(0), '')), hands.lastChild.appendChild(el('div', 'bj-empty', pl.left ? 'Left the table' : ''));
        (pl.hands || []).forEach(function (h, hi) {
          var hn = el('div', 'bj-hand st-' + h.st);
          var cards = el('div', 'bj-cards ' + overlap(h.cards.length));
          h.cards.forEach(function (c, ci) { cards.appendChild(self.cardNode(pl.id + ':' + hi + ':' + ci, c)); });
          hn.appendChild(cards);
          var tot = el('div', 'bj-chips');
          var label = h.st === 'bj' ? 'Blackjack!' : h.text;
          tot.appendChild(el('span', 'bj-tot' + (h.st === 'bust' ? ' bust' : h.st === 'bj' ? ' bj' : h.st === '21' ? ' tw' : ''), label));
          if (h.doubled) tot.appendChild(el('span', 'bj-x2', '×2'));
          if (h.st === 'stand') tot.appendChild(el('span', 'bj-state', 'Stands'));
          if (h.st === 'turn') tot.appendChild(el('span', 'bj-state go', 'Playing'));
          hn.appendChild(tot);
          hands.appendChild(hn);
        });
        seat.appendChild(hands);
        var nm = el('div', 'bj-name');
        nm.appendChild(el('span', '', self.name(pl.id)));
        if (pl.id === me) nm.appendChild(el('em', 'me-tag', 'YOU'));
        seat.appendChild(nm);
        var meta = el('div', 'bj-meta');
        meta.appendChild(el('span', 'bj-pts' + (pl.score > 0 ? ' up' : pl.score < 0 ? ' down' : ''), pts(pl.score) + ' pts'));
        if (pl.ins === 'pending') meta.appendChild(el('span', 'bj-state', 'Insurance?'));
        else if (pl.ins === 'answered') meta.appendChild(el('span', 'bj-state', 'Answered'));
        else if (pl.ins === 'yes') meta.appendChild(el('span', 'bj-state ins', 'Insured'));
        seat.appendChild(meta);
        self.seats.appendChild(seat);
      });
    },

    renderActs: function (p) {
      var self = this, b = p.bj, box = this.acts, by = this.optMap(b), me = p.you;
      box.replaceChildren();
      if (!p.active) return;
      function btn(cls, nodes, action, sub) {
        var x = el('button', 'bj-btn ' + cls); x.type = 'button';
        nodes.forEach(function (n) { x.appendChild(typeof n === 'string' ? document.createTextNode(n) : n); });
        if (sub) x.appendChild(el('small', '', sub));
        x.onclick = function () { self.do(action); };
        box.appendChild(x); return x;
      }
      if (by.deal) btn('deal', [el('i', 'bi bi-suit-spade-fill'), 'Deal cards'], 'deal');
      if (by['ins:yes']) { btn('ins', ['Insurance'], 'ins:yes', '½ point'); btn('ghost', ['No insurance'], 'ins:no'); }
      if (by.hit) btn('hit', ['Hit'], 'hit', 'take a card');
      if (by.stand) btn('stand', ['Stand'], 'stand', 'keep your hand');
      if (by.double) btn('dbl', ['Double'], 'double', 'bet ×2, one card');
      if (by.split) btn('split', ['Split'], 'split', 'two hands');
      if (by.draw) btn('deal', ['Draw a card'], 'draw', 'dealer’s forced card');
      if (by.next) btn('stand', ['Next round'], 'next');
      var mine = b.players.filter(function (x) { return x.id === me; })[0] || {};
      if (!box.children.length) {
        var t = mine.left ? 'You have left the table.' : b.phase === 'result' ? 'Next round starts in a moment…' : '';
        if (t) box.appendChild(el('div', 'bj-note', t));
      }
      if (!mine.left) {
        var leave = el('button', 'bj-btn leave'); leave.type = 'button'; leave.textContent = 'Leave table';
        var armed = false, timer;
        leave.onclick = function () {
          if (!armed) { armed = true; leave.classList.add('sure'); leave.textContent = 'Tap again to leave'; timer = setTimeout(function () { armed = false; leave.classList.remove('sure'); leave.textContent = 'Leave table'; }, 3500); return; }
          clearTimeout(timer); self.do('leave');
        };
        box.appendChild(leave);
      }
    },

    /* ---------- the round that just ended ---------- */
    renderResult: function (p) {
      var b = p.bj, last = b.last, box = this.result, self = this, me = p.you;
      box.replaceChildren();
      if (!last || (b.phase !== 'result' && b.phase !== 'over')) { box.className = 'bj-result'; return; }
      box.className = 'bj-result show';
      var mineRow = last.rows.filter(function (r) { return r.id === me; })[0];
      var net = mineRow ? mineRow.net : last.dealer_net;
      box.classList.add(net > 0 ? 'good' : net < 0 ? 'bad' : 'even');
      box.appendChild(el('h3', '', 'Round ' + last.round + ': ' + (mineRow ? (net > 0 ? 'you won ' + pts(net) : net < 0 ? 'you lost ' + plain(net) : 'you pushed') : 'you dealt, ' + (net > 0 ? 'you won ' + pts(net) : net < 0 ? 'you lost ' + plain(net) : 'level'))));
      last.rows.forEach(function (r) {
        var row = el('div', 'rrow' + (r.id === me ? ' me' : ''));
        row.appendChild(el('span', 'nm', r.id === me ? 'You' : self.name(r.id)));
        var hs = el('span', 'hs');
        r.hands.forEach(function (h) {
          var one = el('span', 'one ' + h.tag);
          one.appendChild(document.createTextNode(h.text + ' '));
          one.appendChild(el('b', '', ({ blackjack: 'Blackjack ', win: 'Win ', lose: 'Lose ', push: 'Push ', bust: 'Bust ' })[h.tag] + (h.net ? pts(h.net) : '')));
          if (h.doubled) one.appendChild(el('small', '', ' doubled'));
          hs.appendChild(one);
        });
        if (r.ins != null) hs.appendChild(el('span', 'one ins', 'Insurance ' + pts(r.ins)));
        row.appendChild(hs); row.appendChild(el('span', 'net ' + (r.net > 0 ? 'up' : r.net < 0 ? 'down' : ''), pts(r.net)));
        box.appendChild(row);
      });
      var drow = el('div', 'rrow dealer');
      drow.appendChild(el('span', 'nm', (last.dealer === me ? 'You' : this.name(last.dealer)) + ' (dealer)'));
      drow.appendChild(el('span', 'hs', last.dealer_bj ? 'Blackjack' : last.dealer_text));
      drow.appendChild(el('span', 'net ' + (last.dealer_net > 0 ? 'up' : last.dealer_net < 0 ? 'down' : ''), pts(last.dealer_net)));
      box.appendChild(drow);
    },

    /* ---------- points ---------- */
    renderScore: function (p) {
      var b = p.bj, self = this, box = this.score, me = p.you;
      box.replaceChildren();
      box.appendChild(el('h3', '', b.phase === 'over' ? 'Final result' : 'Points'));
      var rows = b.phase === 'over' && b.summary ? b.summary.map(function (r) { var pl = b.players.filter(function (x) { return x.id === r.id; })[0] || {}; return { id: r.id, score: r.score, wins: r.wins, left: r.left, gnet: pl.gnet || 0 }; })
        : b.players.slice().sort(function (a, c) { return c.wins - a.wins || c.score - a.score; });
      var head = el('div', 'srow head');
      ['Player', 'Games won', 'This game', 'Points'].forEach(function (t, i) { head.appendChild(el('span', i ? '' : 'nm', t)); });
      box.appendChild(head);
      rows.forEach(function (r) {
        var row = el('div', 'srow' + (r.id === me ? ' me' : '') + (r.left ? ' gone' : ''));
        var nm = el('span', 'nm'); nm.appendChild(el('span', 't', r.id === me ? 'You' : self.name(r.id)));
        if (r.id === b.dealer_id && b.phase !== 'over') nm.appendChild(el('em', 'dl', 'deals'));
        if (r.left) nm.appendChild(el('em', 'dl', 'left'));
        row.appendChild(nm); row.appendChild(el('span', '', String(r.wins)));
        row.appendChild(el('span', r.gnet > 0 ? 'up' : r.gnet < 0 ? 'down' : '', pts(r.gnet)));
        row.appendChild(el('span', 'tot ' + (r.score > 0 ? 'up' : r.score < 0 ? 'down' : ''), pts(r.score)));
        box.appendChild(row);
      });
      if ((b.gdone || []).length) {
        var gl = el('div', 'games-done');
        b.gdone.forEach(function (g) { gl.appendChild(el('span', 'gd', 'Game ' + g.no + ': ' + (g.winner ? (g.winner === me ? 'you' : self.name(g.winner)) + ' won' : 'a draw'))); });
        box.appendChild(gl);
      }
      box.appendChild(el('div', 'bj-shoe', 'Shoe: ' + b.shoe + ' cards left · 6 decks'));
    },

    renderLog: function (p) {
      var b = p.bj, self = this, box = this.logBox, me = p.you;
      box.replaceChildren();
      var text = {
        start: function (n) { return n + (n === 'You' ? ' were' : ' was') + ' picked to deal first'; }, deal: function (n) { return n + ' dealt the cards'; },
        hit: function (n) { return n + ' hit'; }, stand: function (n) { return n + ' stood'; }, double: function (n) { return n + ' doubled down'; },
        split: function (n) { return n + ' split'; }, bust: function (n) { return n + ' busted'; }, draw: function (n) { return n + ' drew a card as dealer'; },
        insure: function (n) { return n + ' took insurance'; }, no_insure: function (n) { return n + ' declined insurance'; },
        dealer_bj: function (n) { return n + (n === 'You' ? ' have' : ' has') + ' blackjack'; }, no_bj: function () { return 'No dealer blackjack, insurance is lost'; },
        settle: function () { return 'Round settled'; }, timeout: function (n) { return n + ' ran out of time'; },
        leave: function (n) { return n + ' left the table'; }, shuffle: function () { return 'The shoe was shuffled'; }, over: function () { return 'The match is over'; }
      };
      (b.log || []).slice().reverse().slice(0, 8).forEach(function (e, i) {
        var f = text[e.a]; if (!f) return;
        box.appendChild(el('div', 'ln', f(e.u === me ? 'You' : (e.u ? self.name(e.u) : ''))));
      });
    },

    /* ---------- the countdown ---------- */
    tick: function () {
      var p = this.p; if (!p || !this.timerBar) return;
      var b = p.bj, total = b.total_ms, show = p.active && total > 0 && b.deadline_in > 0;
      this.timerBox.style.visibility = show ? 'visible' : 'hidden';
      if (!show) return;
      var left = Math.max(0, b.deadline_in - (performance.now() - (p._at || performance.now())));
      this.timerBar.style.width = (100 * left / total).toFixed(1) + '%';
      this.timerTxt.textContent = Math.ceil(left / 1000) + 's';
      this.timerBox.classList.toggle('low', left < 10000);
      if (left <= 0 && this.fired !== b.n) { this.fired = b.n; this.ctx.fetchState(); }       // let the server move things along
    },

    buildRules: function () {
      var d = el('details', 'bj-rules');
      d.appendChild(el('summary', '', 'How blackjack works here'));
      var ul = el('ul');
      [
        'Get closer to 21 than the dealer without going over. Cards 2 to 10 count as their number, Jack, Queen and King count 10.',
        'An Ace counts 11 unless that would bust you, then it counts 1 (a hand like 7/17 is a “soft” hand: you can hit it safely).',
        'Blackjack is an Ace plus a ten-value card as your first two cards. It pays 3 to 2 and beats any other 21. If the dealer also has it, it is a push.',
        'Hit takes a card. Over 21 you bust and lose at once, even if the dealer busts later. Reach 21 and you stand automatically.',
        'Double (on your first two cards): bet twice as much, take exactly one more card, then stand.',
        'Split (first two cards of equal value, ten, Jack, Queen and King count as equal): two hands, each with a full bet, up to 4 hands. Split Aces get one card each. A 21 after splitting is 21, not blackjack.',
        'Insurance: when the dealer shows an Ace you can pay half a point. It pays 2 to 1 if the dealer has blackjack. The dealer checks the face-down card when showing an Ace or a ten.',
        'The dealer plays last and must draw on 16 or less and stand on 17 or more (including soft 17). The dealer does not draw if everyone has busted or has blackjack.',
        'A win pays 1 point, a push gives your bet back, a loss costs 1 point. The dealer plays against every player and wins or loses the opposite of what the players do.',
        'The first dealer is picked at random. Each dealer deals 5 rounds, then the deal moves to the next seat. When everyone has dealt 5 rounds that is one game. Most points in a game wins the game; most games wins the match.',
        'Out of time? The dealer is dealt for, players stand. Miss three turns in a row and you are taken off the table.'
      ].forEach(function (t) { ul.appendChild(el('li', '', t)); });
      d.appendChild(ul);
      return d;
    }
  });
})();
