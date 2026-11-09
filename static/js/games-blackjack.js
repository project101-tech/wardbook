/* Blackjack: the table. The dealer (a neighbour) is the bank and presses the buttons to deal and draw. Everybody sees
   everybody's cards with the name underneath; YOUR seat is always at the bottom of the table, right in front of you.
   Before each deal every player bets (any even number of chips, or sits out). The rules live on the server
   (core/blackjack_engine.py); this file only draws what the server sends and sends back what the player pressed.
   Chips are play money. */
(function () {
  'use strict';
  var G = window.WBGame;
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  var SUIT = { S: '♠︎', H: '♥︎', D: '♦︎', C: '♣︎' };
  var RANK = { 1: 'A', 11: 'J', 12: 'Q', 13: 'K' };
  function rk(r) { return RANK[r] || String(r); }
  function n(v) { return Number(v || 0).toLocaleString('en-US'); }
  function sgn(v) { return !v ? '0' : (v > 0 ? '+' : '−') + n(Math.abs(v)); }          // +1,500  −20  0
  function tone(v) { return v > 0 ? 'up' : v < 0 ? 'down' : ''; }

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
  function overlap(k) { return k <= 2 ? 'ov0' : k === 3 ? 'ov1' : k === 4 ? 'ov2' : k === 5 ? 'ov3' : 'ov4'; }

  G.register('blackjack', {
    seen: {}, lastN: -1, lastRound: -1, fired: -1, betRound: -1,

    init: function (ctx, p) {
      var self = this; this.ctx = ctx;
      var root = el('div', 'bj');
      this.bar = el('div', 'bj-bar');
      this.felt = el('div', 'bj-felt');
      this.dealerBox = el('div', 'bj-dealer');
      this.banner = el('div', 'bj-banner');
      this.others = el('div', 'bj-seats');
      this.mineBox = el('div', 'bj-mine');
      this.wallet = el('div', 'bj-wallet');
      this.betPanel = this.buildBet();
      this.acts = el('div', 'bj-acts');
      this.result = el('div', 'bj-result');
      this.score = el('div', 'bj-score');
      this.logBox = el('div', 'bj-log');
      this.rules = this.buildRules();
      [this.bar, this.felt, this.wallet, this.betPanel, this.acts, this.result, this.score, this.logBox, this.rules].forEach(function (x) { root.appendChild(x); });
      ctx.stage.appendChild(root); ctx.stage.classList.add('bj-stage');
      setInterval(function () { self.tick(); }, 250);
    },

    name: function (id) { var pl = this.ctx.player(id); return pl ? pl.name : 'Someone'; },
    who: function (id) { return id === this.ctx.me ? 'You' : this.name(id); },
    seat: function (b, id) { return b.players.filter(function (x) { return x.id === id; })[0] || {}; },

    do: function (action) {
      var self = this, ctx = this.ctx;
      if (this.working) return; this.working = true;
      Array.prototype.forEach.call(this.acts.querySelectorAll('button'), function (x) { x.disabled = true; });
      Array.prototype.forEach.call(this.betPanel.querySelectorAll('button'), function (x) { x.disabled = true; });
      if (action !== 'leave') { ctx.sfx('hop'); ctx.buzz(15); }
      ctx.send('bj', action).then(function () { self.working = false; if (self.p) self.render(self.p); });
    },

    /* ---------- a new payload from the server ---------- */
    update: function (p, prev, ctx) {
      this.p = p; this.working = false;
      var b = p.bj;
      if (b.phase === 'over' && b.summary) {
        var mine = b.summary.filter(function (r) { return r.id === p.you; })[0];
        if (mine) p.reason = (p.reason ? p.reason + ' · ' : '') + 'you finished ' + sgn(mine.score) + ' chips and won ' + mine.wins + ' game' + (mine.wins === 1 ? '' : 's');
      }
      this.render(p);
      var self = this, fresh = (b.log || []).filter(function (e) { return e.n > self.lastN; });
      if (this.lastN >= 0) {
        fresh.forEach(function (e) {
          if (e.a === 'deal') ctx.sfx('move');
          else if (e.a === 'bet') { if (e.u !== p.you) ctx.sfx('tick'); }
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
      if (by.deal) return { text: 'Bets are in: press Deal cards', tone: 'you' };
      if (by.bet && !by.bet.cur) return { text: 'Place your bet', tone: 'you' };
      if (by['ins:yes']) return { text: 'Insurance?', tone: 'you' };
      if (by.hit) return { text: 'Your turn: hit or stand', tone: 'you' };
      if (by.draw) return { text: 'Draw a card', tone: 'you' };
      if (by.next) return { text: 'Round over', tone: '' };
      var d = this.name(b.dealer_id);
      if (b.phase === 'deal') return { text: b.dealer_id === p.you ? 'Waiting for bets…' : 'Waiting for ' + d + ' to deal…', tone: '' };
      if (b.phase === 'insurance') return { text: 'Insurance…', tone: '' };
      if (b.phase === 'play' && b.turn) return { text: this.name(b.turn.u) + ' is playing…', tone: '' };
      if (b.phase === 'dealer') return { text: d + ' is drawing…', tone: '' };
      return { text: 'Round ' + b.round_no, tone: '' };
    },
    help: function (p) {
      var b = p.bj, by = this.optMap(b);
      if (by.bet) return 'Bet any even number of chips. Change it as often as you like until the dealer deals.';
      if (by.deal) return 'Everyone who bet gets two cards, you get one face up and one face down.';
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
      this.renderBar(b); this.renderTable(b); this.renderWallet(p); this.renderBet(p); this.renderActs(p);
      this.renderResult(p); this.renderScore(p); this.renderLog(p);
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
      var x = card(c, { fresh: fresh, delay: this.delay, peek: extra && extra.peek });
      if (fresh) this.delay += 130;
      return x;
    },

    /* The felt. Whoever is looking sits at the BOTTOM: a player sees the dealer at the top and the other players
       in between (in turn order, clockwise); the dealer sees their own cards at the bottom and the players above. */
    renderTable: function (b) {
      var self = this, me = this.p.you, dealerMe = b.dealer_id === me, order = (b.order || []).slice();
      this.renderDealer(b); this.renderBanner(b);
      this.others.replaceChildren(); this.mineBox.replaceChildren();
      var ids, mine = null;
      if (dealerMe) ids = order;                                  // players face the dealer: first to act on the dealer's left
      else { mine = order.indexOf(me) >= 0 ? me : null; ids = order.filter(function (id) { return id !== me; }).reverse(); }
      this.others.className = 'bj-seats n' + Math.min(Math.max(ids.length, 1), 6);
      ids.forEach(function (id) { self.others.appendChild(self.seatNode(b, self.seat(b, id), false)); });
      if (mine != null) this.mineBox.appendChild(this.seatNode(b, this.seat(b, mine), true));
      this.felt.replaceChildren();
      this.felt.className = 'bj-felt' + (dealerMe ? ' dealer-view' : '');
      if (dealerMe) [this.others, this.banner, this.dealerBox].forEach(function (x) { self.felt.appendChild(x); });
      else [this.dealerBox, this.banner, this.others, this.mineBox].forEach(function (x) { self.felt.appendChild(x); });
    },

    renderDealer: function (b) {
      var box = this.dealerBox, d = b.dealer, self = this;
      box.replaceChildren();
      if (!d) return;
      var isMe = d.id === this.p.you, pl = this.seat(b, d.id);
      box.className = 'bj-dealer' + (isMe ? ' mine' : '');
      box.appendChild(el('div', 'bj-role', 'DEALER · THE BANK'));
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
      box.appendChild(this.holdLine(pl, 'Bank'));
    },

    holdLine: function (pl, label) {
      var m = el('div', 'bj-meta');
      m.appendChild(el('span', 'bj-hold', (label || 'Chips') + ' ' + n(pl.hold)));
      if (pl.gnet) m.appendChild(el('span', 'bj-pts ' + tone(pl.gnet), sgn(pl.gnet)));
      return m;
    },

    renderBanner: function (b) {
      var t = '', me = this.p.you, by = this.optMap(b), d = this.name(b.dealer_id), tone = '', self = this;
      if (b.phase === 'deal') {
        var waiting = b.players.filter(function (x) { return x.bst === 'wait'; }).map(function (x) { return x.id === me ? 'you' : self.name(x.id); });
        var betting = b.players.filter(function (x) { return x.bst === 'bet'; }).length;
        if (b.table_max < b.min_bet) t = d + '’s bank is too small to cover bets this round.';
        else if (by.deal) t = 'Your deal. ' + betting + ' bet' + (betting === 1 ? '' : 's') + ' in' + (waiting.length ? ' (still waiting for ' + waiting.join(', ') + ')' : '') + '. Press “Deal cards” when you are ready.';
        else if (by.bet) t = by.bet.cur ? 'Your bet is in. Waiting for ' + d + ' to deal.' : 'Place your bet, or sit this round out.';
        else t = betting ? 'Waiting for ' + d + ' to deal the cards.' : 'Waiting for bets.';
        if (b.round_no === 1 && b.rb === 1 && b.game === 1) t += ' ' + d + ' was picked at random to deal first.';
      } else if (b.phase === 'insurance') {
        var pend = b.players.filter(function (x) { return x.ins === 'pending'; }).map(function (x) { return x.id === me ? 'you' : self.name(x.id); });
        t = d + ' shows an Ace. Insurance costs half your bet and pays 2 to 1 if the dealer has blackjack.' + (pend.length ? ' Waiting for ' + pend.join(', ') + '.' : '');
        tone = 'on';
      } else if (b.phase === 'play' && b.turn) {
        var seat = this.seat(b, b.turn.u), many = seat.hands && seat.hands.length > 1;
        t = (b.turn.u === me ? 'Your turn' : this.name(b.turn.u) + '’s turn') + (many ? ' (hand ' + (b.turn.h + 1) + ' of ' + seat.hands.length + ')' : '') + '.';
      } else if (b.phase === 'dealer') {
        t = by.draw ? 'You must draw: the dealer draws on 16 or less.' : d + ' must draw a card (16 or less).';
        tone = 'on';
      } else if (b.phase === 'result') {
        t = (b.last && b.last.dealer_bj) ? d + ' has blackjack.' : (b.last && b.last.dealer_bust) ? d + ' busts with ' + b.last.dealer_text.replace('Bust ', '') + '.' : d + ' finishes on ' + (b.last ? b.last.dealer_text : '') + '.';
        t += ' Chips are paid out.';
        if (b.shuffled) t += ' New shoe shuffled.';
        tone = 'on';
      } else if (b.phase === 'over') {
        t = 'Match over.'; tone = 'on';
      }
      this.banner.className = 'bj-banner' + (tone ? ' ' + tone : '');
      this.banner.textContent = t;
    },

    seatNode: function (b, pl, big) {
      var self = this, me = this.p.you;
      var seat = el('div', 'bj-seat' + (pl.id === me ? ' mine' : '') + (big ? ' big' : '') + (pl.left ? ' left' : '') + (b.turn && b.turn.u === pl.id ? ' turn' : ''));
      var hands = el('div', 'bj-hands' + ((pl.hands || []).length > 1 ? ' split' : ''));
      if (!pl.hands) {
        var empty = el('div', 'bj-cards ' + overlap(0));
        var note = pl.left ? 'Left the table' : b.phase === 'deal' ? ({ bet: '', sit: 'Sitting out', wait: 'Betting…', out: 'Cannot bet' })[pl.bst] : (pl.bst === 'sit' ? 'Sat out' : '');
        if (pl.bst === 'bet' && b.phase === 'deal') empty.appendChild(el('span', 'bj-coin big', n(pl.bet)));
        else empty.appendChild(el('div', 'bj-empty', note || ''));
        hands.appendChild(empty);
      }
      (pl.hands || []).forEach(function (h, hi) {
        var hn = el('div', 'bj-hand st-' + h.st);
        var cards = el('div', 'bj-cards ' + overlap(h.cards.length));
        h.cards.forEach(function (c, ci) { cards.appendChild(self.cardNode(pl.id + ':' + hi + ':' + ci, c)); });
        hn.appendChild(cards);
        var tot = el('div', 'bj-chips');
        var label = h.st === 'bj' ? 'Blackjack!' : h.text;
        tot.appendChild(el('span', 'bj-tot' + (h.st === 'bust' ? ' bust' : h.st === 'bj' ? ' bj' : h.st === '21' ? ' tw' : ''), label));
        tot.appendChild(el('span', 'bj-coin', n(h.stake)));
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
      var meta = self.holdLine(pl, 'Chips');
      if (pl.ins === 'pending') meta.appendChild(el('span', 'bj-state', 'Insurance?'));
      else if (pl.ins === 'answered') meta.appendChild(el('span', 'bj-state', 'Answered'));
      else if (pl.ins === 'yes') meta.appendChild(el('span', 'bj-state ins', 'Insured'));
      seat.appendChild(meta);
      return seat;
    },

    /* ---------- "my holdings" strip: always there, for players and the dealer alike ---------- */
    renderWallet: function (p) {
      var b = p.bj, box = this.wallet, pl = this.seat(b, p.you), isDealer = b.dealer_id === p.you;
      box.replaceChildren();
      if (!p.active || !pl.id) { box.className = 'bj-wallet'; return; }
      box.className = 'bj-wallet show';
      function cell(label, val, cls) { var c = el('div', 'wc'); c.appendChild(el('span', 'wl', label)); c.appendChild(el('b', cls || '', val)); box.appendChild(c); }
      cell(isDealer ? 'Your bank' : 'Your holdings', n(pl.hold));
      cell(isDealer ? 'Table limit' : 'On the table', isDealer ? n(b.table_max) + ' each' : n(pl.in_play));
      cell('This game', sgn(pl.gnet), tone(pl.gnet));
    },

    /* ---------- the bet box (built once, so what you type is not lost when the table refreshes) ---------- */
    buildBet: function () {
      var self = this, box = el('div', 'bj-bet');
      this.betHead = el('div', 'bt-head');
      this.betInfo = el('div', 'bt-info');
      var row = el('div', 'bt-row');
      this.betInput = el('input', 'bt-in'); this.betInput.type = 'number'; this.betInput.inputMode = 'numeric'; this.betInput.step = '2'; this.betInput.min = '10';
      this.betInput.setAttribute('aria-label', 'Your bet in chips');
      this.betInput.addEventListener('input', function () { self.betCheck(); });
      this.betInput.addEventListener('keydown', function (e) { if (e.key === 'Enter') self.betPlace(); });
      row.appendChild(this.betInput);
      this.betQuick = el('div', 'bt-quick');
      [10, 50, 100, 500].forEach(function (v) { self.quick('+' + v, function () { self.betSet((parseInt(self.betInput.value, 10) || 0) + v); }); });
      this.quickLast = this.quick('Last', function () { self.betSet(self.lastBet); });
      this.quick('Max', function () { self.betSet(self.betMax); });
      this.quick('Clear', function () { self.betSet(''); });
      row.appendChild(this.betQuick);
      this.betGo = el('button', 'bj-btn deal bt-go'); this.betGo.type = 'button'; this.betGo.onclick = function () { self.betPlace(); };
      this.betSit = el('button', 'bj-btn ghost bt-sit'); this.betSit.type = 'button'; this.betSit.textContent = 'Sit this round out'; this.betSit.onclick = function () { self.do('sit'); };
      this.betMsg = el('div', 'bt-msg');
      var btns = el('div', 'bt-btns'); btns.appendChild(this.betGo); btns.appendChild(this.betSit);
      [this.betHead, this.betInfo, row, this.betMsg, btns].forEach(function (x) { box.appendChild(x); });
      return box;
    },
    quick: function (label, fn) { var q = el('button', 'bt-q', label); q.type = 'button'; q.onclick = fn; this.betQuick.appendChild(q); return q; },
    betSet: function (v) {
      if (v !== '') v = Math.max(0, Math.min(this.betMax || 0, v));
      this.betInput.value = v === '' ? '' : String(v);
      this.betCheck();
    },
    betValid: function () {
      var v = Number(this.betInput.value);
      return this.betInput.value !== '' && Number.isInteger(v) && v >= this.betMin && v <= this.betMax && v % 2 === 0;
    },
    betCheck: function () {
      var v = parseInt(this.betInput.value, 10), ok = this.betValid();
      this.betGo.disabled = !ok || !!this.working;
      this.betGo.replaceChildren();
      this.betGo.appendChild(document.createTextNode(this.betCur ? 'Change bet' + (ok ? ' to ' + n(v) : '') : 'Place bet' + (ok ? ' ' + n(v) : '')));
      this.betMsg.textContent = ok || this.betInput.value === '' ? '' : 'Bet an even number of chips from ' + n(this.betMin) + ' to ' + n(this.betMax) + '.';
    },
    betPlace: function () { if (this.betValid()) this.do('bet:' + parseInt(this.betInput.value, 10)); },

    renderBet: function (p) {
      var b = p.bj, by = this.optMap(b), box = this.betPanel, pl = this.seat(b, p.you), isDealer = b.dealer_id === p.you;
      var show = p.active && b.phase === 'deal' && pl.id;
      box.className = 'bj-bet' + (show ? ' show' : '');
      if (!show) { this.betRound = -1; return; }
      this.betHead.textContent = isDealer ? 'You are the bank this round' : 'Your bet';
      if (isDealer) {
        this.betInfo.textContent = 'You pay the winning bets and collect the losing ones. Each player may bet up to ' + n(b.table_max) + ' chips (a share of your ' + n(pl.hold) + '-chip bank, so you can always pay).';
        this.betInput.style.display = this.betQuick.style.display = this.betGo.style.display = this.betSit.style.display = this.betMsg.style.display = 'none';
        return;
      }
      this.betInput.style.display = this.betQuick.style.display = this.betGo.style.display = this.betSit.style.display = this.betMsg.style.display = '';
      if (!by.bet) {
        this.betInput.style.display = this.betQuick.style.display = this.betGo.style.display = this.betSit.style.display = this.betMsg.style.display = 'none';
        this.betInfo.textContent = pl.hold < b.min_bet ? 'You are out of chips for this game. You sit out until the next game starts with a fresh stack.' : 'You cannot bet this round: the bank is too small to cover it.';
        return;
      }
      var o = by.bet;
      this.betMin = o.min; this.betMax = o.max; this.betCur = o.cur; this.lastBet = o.last || 0;
      this.betInput.min = String(o.min); this.betInput.max = String(o.max);
      this.quickLast.style.display = this.lastBet && this.lastBet <= o.max ? '' : 'none';
      this.betInfo.textContent = (o.cur ? 'Your bet is in: ' + n(o.cur) + '. You can change it until the dealer deals. ' : 'Any even number from ' + n(o.min) + ' up to ' + n(o.max) + '. ') +
        'The limit is ' + n(b.table_max) + ' (a share of the bank, ' + n(b.bank) + ').';
      this.betSit.style.display = pl.bst === 'sit' ? 'none' : '';
      if (pl.bst === 'sit') this.betInfo.textContent = 'You are sitting this round out. Place a bet to join in.';
      if (this.betRound !== b.round_no) {                         // a new round: start from the last bet (or what is in)
        this.betRound = b.round_no;
        this.betInput.value = String(o.cur || Math.min(this.lastBet || o.min, o.max));
      }
      this.betCheck();
    },

    renderActs: function (p) {
      var self = this, b = p.bj, box = this.acts, by = this.optMap(b), me = p.you;
      box.replaceChildren();
      if (!p.active) return;
      function btn(cls, nodes, action, sub, off) {
        var x = el('button', 'bj-btn ' + cls); x.type = 'button';
        nodes.forEach(function (c) { x.appendChild(typeof c === 'string' ? document.createTextNode(c) : c); });
        if (sub) x.appendChild(el('small', '', off || sub));
        if (off) x.disabled = true; else x.onclick = function () { self.do(action); };
        box.appendChild(x); return x;
      }
      if (by.deal) btn('deal', [el('i', 'bi bi-suit-spade-fill'), 'Deal cards'], 'deal');
      if (by['ins:yes']) { btn('ins', ['Insurance'], 'ins:yes', n(by['ins:yes'].cost) + ' chips', by['ins:yes'].off); btn('ghost', ['No insurance'], 'ins:no'); }
      if (by.hit) btn('hit', ['Hit'], 'hit', 'take a card');
      if (by.stand) btn('stand', ['Stand'], 'stand', 'keep your hand');
      if (by.double) btn('dbl', ['Double'], 'double', '+' + n(by.double.cost) + ' chips, one card', by.double.off);
      if (by.split) btn('split', ['Split'], 'split', '+' + n(by.split.cost) + ' chips, two hands', by.split.off);
      if (by.draw) btn('deal', ['Draw a card'], 'draw', 'dealer’s forced card');
      if (by.next) btn('stand', ['Next round'], 'next');
      var mine = this.seat(b, me);
      if (!box.children.length && !(b.phase === 'deal' && by.bet)) {
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
      var word = net > 0 ? 'won ' + n(net) : net < 0 ? 'lost ' + n(-net) : mineRow ? 'pushed' : 'broke even';
      box.appendChild(el('h3', '', 'Round ' + last.round + ': ' + (mineRow ? 'you ' + word : last.dealer === me ? 'as the bank you ' + word : 'you sat out')));
      last.rows.forEach(function (r) {
        var row = el('div', 'rrow' + (r.id === me ? ' me' : ''));
        row.appendChild(el('span', 'nm', r.id === me ? 'You' : self.name(r.id)));
        var hs = el('span', 'hs');
        r.hands.forEach(function (h) {
          var one = el('span', 'one ' + h.tag);
          one.appendChild(document.createTextNode(h.text + ' · bet ' + n(h.stake) + ' · '));
          one.appendChild(el('b', '', ({ blackjack: 'Blackjack ', win: 'Win ', lose: 'Lose ', push: 'Push ', bust: 'Bust ' })[h.tag] + (h.net ? sgn(h.net) : '')));
          hs.appendChild(one);
        });
        if (r.ins != null) hs.appendChild(el('span', 'one ins', 'Insurance ' + sgn(r.ins)));
        row.appendChild(hs); row.appendChild(el('span', 'net ' + tone(r.net), sgn(r.net)));
        box.appendChild(row);
      });
      var drow = el('div', 'rrow dealer');
      drow.appendChild(el('span', 'nm', (last.dealer === me ? 'You' : this.name(last.dealer)) + ' (bank)'));
      drow.appendChild(el('span', 'hs', last.dealer_bj ? 'Blackjack' : last.dealer_text));
      drow.appendChild(el('span', 'net ' + tone(last.dealer_net), sgn(last.dealer_net)));
      box.appendChild(drow);
    },

    /* ---------- everybody's chips ---------- */
    renderScore: function (p) {
      var b = p.bj, self = this, box = this.score, me = p.you, over = b.phase === 'over' && b.summary;
      box.replaceChildren();
      box.className = 'bj-score' + (over ? ' final' : '');
      box.appendChild(el('h3', '', over ? 'Final result' : 'Holdings'));
      var rows = over ? b.summary.map(function (r) { return { id: r.id, score: r.score, wins: r.wins, left: r.left }; })
        : b.players.slice().sort(function (a, c) { return c.hold - a.hold || c.wins - a.wins; });
      var head = el('div', 'srow head');
      (over ? ['Player', 'Games won', 'Total chips'] : ['Player', 'Chips', 'This game', 'Games won']).forEach(function (t, i) { head.appendChild(el('span', i ? '' : 'nm', t)); });
      box.appendChild(head);
      rows.forEach(function (r) {
        var row = el('div', 'srow' + (r.id === me ? ' me' : '') + (r.left ? ' gone' : ''));
        var nm = el('span', 'nm'); nm.appendChild(el('span', 't', r.id === me ? 'You' : self.name(r.id)));
        if (r.id === b.dealer_id && !over) nm.appendChild(el('em', 'dl', 'bank'));
        if (r.left) nm.appendChild(el('em', 'dl', 'left'));
        row.appendChild(nm);
        if (over) {
          row.appendChild(el('span', '', String(r.wins)));
          row.appendChild(el('span', 'tot ' + tone(r.score), sgn(r.score)));
        } else {
          row.appendChild(el('span', 'tot', n(r.hold)));
          row.appendChild(el('span', tone(r.gnet), sgn(r.gnet)));
          row.appendChild(el('span', '', String(r.wins)));
        }
        box.appendChild(row);
      });
      if ((b.gdone || []).length) {
        var gl = el('div', 'games-done');
        b.gdone.forEach(function (g) { gl.appendChild(el('span', 'gd', 'Game ' + g.no + ': ' + (g.winner ? (g.winner === me ? 'you' : self.name(g.winner)) + ' won' : 'a draw'))); });
        box.appendChild(gl);
      }
      box.appendChild(el('div', 'bj-shoe', 'Everyone starts each game with ' + n(b.start) + ' chips. Shoe: ' + b.shoe + ' cards left · 6 decks'));
    },

    renderLog: function (p) {
      var b = p.bj, self = this, box = this.logBox, me = p.you;
      box.replaceChildren();
      var text = {
        start: function (nm) { return nm + (nm === 'You' ? ' were' : ' was') + ' picked to deal first'; }, deal: function (nm) { return nm + ' dealt the cards'; },
        bet: function (nm, e) { return nm + ' bet ' + n(e.x); }, sit: function (nm) { return nm + ' sat the round out'; },
        no_bets: function () { return 'Nobody could bet, round skipped'; },
        hit: function (nm) { return nm + ' hit'; }, stand: function (nm) { return nm + ' stood'; }, double: function (nm) { return nm + ' doubled down'; },
        split: function (nm) { return nm + ' split'; }, bust: function (nm) { return nm + ' busted'; }, draw: function (nm) { return nm + ' drew a card as dealer'; },
        insure: function (nm) { return nm + ' took insurance'; }, no_insure: function (nm) { return nm + ' declined insurance'; },
        dealer_bj: function (nm) { return nm + (nm === 'You' ? ' have' : ' has') + ' blackjack'; }, no_bj: function () { return 'No dealer blackjack, insurance is lost'; },
        settle: function () { return 'Round settled, chips paid out'; }, timeout: function (nm) { return nm + ' ran out of time'; },
        leave: function (nm) { return nm + ' left the table'; }, shuffle: function () { return 'The shoe was shuffled'; }, over: function () { return 'The match is over'; }
      };
      (b.log || []).slice().reverse().slice(0, 8).forEach(function (e) {
        var f = text[e.a]; if (!f) return;
        box.appendChild(el('div', 'ln', f(e.u === me ? 'You' : (e.u ? self.name(e.u) : ''), e)));
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
        'Everyone starts every game with 1,000 play chips (no real money). Before each deal you bet any even number of chips (at least 10, up to the table limit) or sit the round out. You can change your bet until the dealer deals.',
        'The dealer is the bank for their five rounds: they collect losing bets and pay winning ones, and do not bet. The table limit is a share of the bank, so the bank can always pay. If a double or split would be more than the bank can cover, it is not offered.',
        'Get closer to 21 than the dealer without going over. Cards 2 to 10 count as their number, Jack, Queen and King count 10.',
        'An Ace counts 11 unless that would bust you, then it counts 1 (a hand like 7/17 is a “soft” hand: you can hit it safely).',
        'Blackjack is an Ace plus a ten-value card as your first two cards. It pays 3 to 2 and beats any other 21. If the dealer also has it, it is a push (you get your bet back).',
        'A normal win pays 1 to 1. A push returns your bet. A loss costs your bet. Chips are paid out after every round.',
        'Hit takes a card. Over 21 you bust and lose at once, even if the dealer busts later. Reach 21 and you stand automatically.',
        'Double (on your first two cards): add the same amount to your bet, take exactly one more card, then stand.',
        'Split (first two cards of equal value, ten, Jack, Queen and King count as equal): two hands, each with a full bet, up to 4 hands. Split Aces get one card each. A 21 after splitting is 21, not blackjack.',
        'Insurance: when the dealer shows an Ace you can pay half your bet. It pays 2 to 1 if the dealer has blackjack. The dealer checks the face-down card when showing an Ace or a ten.',
        'The dealer plays last and must draw on 16 or less and stand on 17 or more (including soft 17). The dealer does not draw if everyone has busted or has blackjack.',
        'The first dealer is picked at random. Each dealer deals 5 rounds, then the deal moves to the next seat. When everyone has dealt 5 rounds that is one game, and everybody gets a fresh 1,000 chips. Most chips at the end of a game wins the game; most games wins the match.',
        'Out of time? Players who have not bet sit the round out, the dealer is dealt for, players stand. Miss three turns in a row and you are taken off the table.'
      ].forEach(function (t) { ul.appendChild(el('li', '', t)); });
      d.appendChild(ul);
      return d;
    }
  });
})();
