/* Teen Patti: the table, your three cards, the pot, the betting buttons and the log. Money is play money: 1 token = ₹10. */
(function () {
  'use strict';
  var G = window.WBGame;
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  var SUIT = { S: '♠︎', H: '♥︎', D: '♦︎', C: '♣︎' };
  var RANK = { 11: 'J', 12: 'Q', 13: 'K', 14: 'A' };
  function rk(r) { return RANK[r] || String(r); }

  function card(c, cls) {
    if (!c) {
      return el('div', 'tp-card back' + (cls ? ' ' + cls : ''));            // face down
    }
    var red = c[1] === 'H' || c[1] === 'D';
    var d = el('div', 'tp-card face' + (red ? ' red' : '') + (cls ? ' ' + cls : ''));
    d.appendChild(el('span', 'cn tl', rk(c[0]) + SUIT[c[1]]));
    d.appendChild(el('span', 'cs', SUIT[c[1]]));
    d.appendChild(el('span', 'cn br', rk(c[0]) + SUIT[c[1]]));
    return d;
  }
  function tk(n, big) {                                    // a gold token with a number
    var s = el('span', 'tk' + (big ? ' big' : ''));
    s.appendChild(el('i')); s.appendChild(document.createTextNode(String(n)));
    return s;
  }
  function rupees(n) { return '₹' + (n * 10); }

  G.register('teen_patti', {
    lastN: -1, lastHand: -1, stage: null,

    init: function (ctx, p) {
      var self = this; this.ctx = ctx;
      var root = el('div', 'tp');
      this.felt = el('div', 'tp-felt');
      this.potBox = el('div', 'tp-pot'); this.banner = el('div', 'tp-banner'); this.timer = el('div', 'tp-timer');
      this.timerBar = el('i'); this.timerTxt = el('span'); this.timer.appendChild(this.timerBar); this.timer.appendChild(this.timerTxt);
      this.felt.appendChild(this.potBox); this.felt.appendChild(this.banner); this.felt.appendChild(this.timer);
      this.reveal = el('div', 'tp-reveal');
      this.me = el('div', 'tp-me');
      this.cards = el('div', 'tp-cards'); this.handName = el('div', 'tp-handname'); this.myStack = el('div', 'tp-mystack');
      this.me.appendChild(this.cards); this.me.appendChild(this.handName); this.me.appendChild(this.myStack);
      this.acts = el('div', 'tp-acts');
      this.log = el('div', 'tp-log');
      [this.felt, this.reveal, this.me, this.acts, this.log].forEach(function (n) { root.appendChild(n); });
      ctx.stage.appendChild(root); ctx.stage.classList.add('tp-stage');
      this.cards.addEventListener('click', function () {                         // tap your cards to look at them
        var o = (self.p && self.p.tp.options || []).filter(function (x) { return x.a === 'see'; })[0];
        if (o) self.do('see');
      });
      this.fired = 0;
      setInterval(function () { self.tick(); }, 250);
    },

    do: function (action) {
      var self = this, ctx = this.ctx;
      if (this.working) return; this.working = true;
      Array.prototype.forEach.call(this.acts.querySelectorAll('button'), function (b) { b.disabled = true; });
      if (action === 'bet' || action === 'raise' || action === 'show' || action === 'side') { ctx.sfx('hop'); ctx.buzz(15); }
      ctx.send('tp', action).then(function () { self.working = false; if (self.p) self.render(self.p); });
    },

    name: function (id) { var pl = this.ctx.player(id); return pl ? pl.name : 'Someone'; },

    update: function (p, prev, ctx) {
      this.p = p; this.working = false;
      var t = p.tp;
      if (t.phase === 'over' && p.tp.summary) {
        var mine = p.tp.summary.filter(function (r) { return r.id === p.you; })[0];
        if (mine) p.reason = 'Table closed · you finished ' + (mine.net >= 0 ? '+' : '−') + Math.abs(mine.net) + ' token' + (Math.abs(mine.net) === 1 ? '' : 's') + ' (' + (mine.net >= 0 ? '+' : '−') + rupees(Math.abs(mine.net)) + ')';
      }
      this.render(p);
      // sounds for whatever just happened
      var self = this, fresh = (t.log || []).filter(function (e) { return e.n > self.lastN; });
      if (this.lastN >= 0) {
        fresh.forEach(function (e) {
          if (e.u === p.you) return;
          if (e.a === 'deal') ctx.sfx('move'); else if (['blind', 'chaal', 'raise', 'show', 'side'].indexOf(e.a) >= 0) ctx.sfx('hop');
          else if (e.a === 'pack') ctx.sfx('tick'); else if (e.a === 'win' || e.a === 'side_win') ctx.sfx('capture');
        });
        if (fresh.some(function (e) { return e.a === 'win'; })) ctx.buzz(20);
      }
      if (t.log && t.log.length) this.lastN = t.log[t.log.length - 1].n;
    },

    render: function (p) {
      var t = p.tp, ctx = this.ctx, me = t.players.filter(function (x) { return x.id === p.you; })[0] || {};
      var inHand = me.status === 'in', cardsDelta = this.lastHand !== t.hand_no;

      /* the pot */
      this.potBox.replaceChildren(
        el('div', 'lbl', 'POT'), tk(t.pot, true),
        el('div', 'sub', rupees(t.pot) + ' · hand ' + t.hand_no + ' · blind stake ' + t.stake));
      this.banner.replaceChildren(); this.banner.className = 'tp-banner';
      if (t.phase === 'sideshow' && t.side) {
        var ask = this.name(t.side.from), to = this.name(t.side.to);
        this.banner.className = 'tp-banner on';
        this.banner.textContent = t.side.to === p.you ? ask + ' asks for a side show with you. The lower hand packs.' : ask + ' asked ' + to + ' for a side show…';
      }

      /* the last hand */
      this.renderReveal(p);

      /* my cards */
      this.cards.replaceChildren();
      var dim = me.status === 'packed' || me.status === 'out' || me.left;
      for (var i = 0; i < 3; i++) {
        var c = t.my_cards ? t.my_cards[i] : null, node = card(c, (cardsDelta ? 'deal d' + i : '') + (c ? ' flipped' : '') + (dim ? ' dim' : ''));
        this.cards.appendChild(node);
      }
      this.cards.classList.toggle('can-see', !!(t.options || []).filter(function (o) { return o.a === 'see'; }).length);
      this.lastHand = t.hand_no;
      var label = '';
      if (t.phase === 'over') label = 'Table closed';
      else if (me.left) label = 'You left the table';
      else if (me.status === 'out') label = 'Sitting this hand out';
      else if (me.status === 'packed') label = 'You packed';
      else if (t.my_cards) label = t.my_name;
      else label = 'Playing blind · tap your cards to look';
      this.handName.textContent = label;
      this.handName.className = 'tp-handname' + (t.my_cards && inHand ? ' seen' : '');
      this.myStack.replaceChildren(document.createTextNode('Your tokens '), tk(me.stack || 0), el('small', '', ' ' + rupees(me.stack || 0)));

      /* what I can do */
      this.renderActs(p);
      this.renderLog(p);
      this.tick();
    },

    renderReveal: function (p) {
      var t = p.tp, last = t.last, box = this.reveal, self = this;
      box.replaceChildren(); box.className = 'tp-reveal';
      if (t.phase === 'over' && t.summary) {
        box.classList.add('show', 'over');
        box.appendChild(el('h3', '', 'Final result'));
        t.summary.slice().sort(function (a, b) { return b.net - a.net; }).forEach(function (r) {
          var row = el('div', 'rrow' + (r.id === p.you ? ' me' : ''));
          row.appendChild(el('span', 'nm', self.name(r.id)));
          row.appendChild(el('span', 'net ' + (r.net > 0 ? 'up' : r.net < 0 ? 'down' : ''), (r.net > 0 ? '+' : r.net < 0 ? '−' : '') + Math.abs(r.net) + ' tokens (' + (r.net < 0 ? '−' : r.net > 0 ? '+' : '') + rupees(Math.abs(r.net)) + ')'));
          box.appendChild(row);
        });
        return;
      }
      var sr = t.side_result;
      if (sr && t.phase === 'bet') {
        box.classList.add('show', 'side');
        box.appendChild(el('h3', '', 'Side show: ' + (sr.winner === p.you ? 'you won' : 'you lost')));
        [sr.a, sr.b].forEach(function (id) {
          var line = el('div', 'hand' + (id === sr.winner ? ' win' : '')); line.appendChild(el('span', 'nm', id === p.you ? 'You' : self.name(id)));
          var cs = el('span', 'cs'); (sr.cards[id] || []).forEach(function (c) { cs.appendChild(card(c, 'mini')); }); line.appendChild(cs);
          box.appendChild(line);
        });
        box.appendChild(el('div', 'note', (sr.loser === p.you ? 'You are' : self.name(sr.loser) + ' is') + ' packed.'));
        return;
      }
      if (!last) return;
      if (t.phase === 'between') {
        box.classList.add('show');
        var w = last.winner, won = w === p.you;
        box.classList.toggle('mine', won);
        box.appendChild(el('h3', '', (won ? 'You win ' : self.name(w) + ' wins ') + last.amount + ' tokens (' + rupees(last.amount) + ')'));
        var ids = Object.keys(last.reveal || {});
        if (ids.length) {
          ids.forEach(function (k) {
            var id = +k, line = el('div', 'hand' + (id === w ? ' win' : ''));
            line.appendChild(el('span', 'nm', id === p.you ? 'You' : self.name(id)));
            var cs = el('span', 'cs'); last.reveal[k].forEach(function (c) { cs.appendChild(card(c, 'mini')); }); line.appendChild(cs);
            line.appendChild(el('span', 'hn', (last.names || {})[k] || ''));
            box.appendChild(line);
          });
        } else box.appendChild(el('div', 'note', 'Everyone else packed, so the cards stay hidden.'));
      }
    },

    renderActs: function (p) {
      var t = p.tp, self = this, box = this.acts, opts = t.options || [], me = t.players.filter(function (x) { return x.id === p.you; })[0] || {};
      box.replaceChildren();
      if (!p.active) return;
      function btn(cls, labelNodes, action) {
        var b = el('button', 'tp-btn ' + cls); b.type = 'button';
        labelNodes.forEach(function (n) { b.appendChild(typeof n === 'string' ? document.createTextNode(n) : n); });
        b.onclick = function () { self.do(action); };
        box.appendChild(b); return b;
      }
      var by = {}; opts.forEach(function (o) { by[o.a] = o; });
      var seen = !!t.my_cards;
      if (by.bet) {
        btn('bet', [seen ? 'Chaal ' : 'Blind ', tk(by.bet.cost)], 'bet');
      }
      if (by.raise) btn('raise', ['Raise ', tk(by.raise.cost)], 'raise');
      if (by.show) btn('show', ['Show ', tk(by.show.cost)], 'show');
      if (by.side) btn('side', ['Side show ', tk(by.side.cost), ' → ' + this.name(by.side.to)], 'side');
      if (by.side_accept) btn('bet', ['Accept side show'], 'side_accept');
      if (by.side_decline) btn('ghost', ['Decline'], 'side_decline');
      if (by.see) btn('see', [el('i', 'bi bi-eye'), ' See cards'], 'see');
      if (by.pack) btn('pack', ['Pack'], 'pack');
      if (by.deal) btn('bet', ['Deal next hand'], 'deal');
      if (t.phase === 'bet' && t.turn === p.you && !by.bet && !by.show && me.status === 'in') {
        box.appendChild(el('div', 'tp-note', 'You do not have enough tokens to bet. Pack, or wait for a show.'));
      }
      if (!box.children.length) box.appendChild(el('div', 'tp-note', me.left ? 'You have left. Your tokens are back in your wallet.' : 'Waiting…'));
    },

    renderLog: function (p) {
      var self = this, t = p.tp, box = this.log; box.replaceChildren();
      (t.log || []).slice().reverse().forEach(function (e) {
        var n = e.u != null ? self.name(e.u) : '', other = e.v != null ? self.name(e.v) : '', txt = '';
        switch (e.a) {
          case 'deal': txt = 'New hand: everyone puts in the boot (' + t.boot + ')'; break;
          case 'blind': txt = n + ' played blind for ' + e.x; break;
          case 'chaal': txt = n + ' chaal ' + e.x; break;
          case 'raise': txt = n + ' raised, paying ' + e.x; break;
          case 'pack': txt = n + ' packed'; break;
          case 'see': txt = n + ' looked at their cards'; break;
          case 'show': txt = n + ' asked for a show' + (e.x ? ' (' + e.x + ')' : ''); break;
          case 'side': txt = n + ' asked ' + other + ' for a side show (' + e.x + ')'; break;
          case 'side_no': txt = n + ' refused the side show'; break;
          case 'side_win': txt = n + ' won the side show; ' + other + ' packed'; break;
          case 'win': txt = n + ' won ' + e.x; break;
          case 'timeout': txt = n + ' ran out of time'; break;
          case 'left': txt = n + ' left the table'; break;
          case 'broke': txt = n + ' is out of tokens'; break;
          default: txt = '';
        }
        if (txt) box.appendChild(el('div', 'ln' + (e.a === 'win' ? ' win' : ''), txt));
      });
    },

    tick: function () {
      var p = this.p; if (!p) return;
      var t = p.tp, left = t.deadline_in - (performance.now() - (p._at || 0)), total = t.phase === 'between' ? 9000 : (t.phase === 'sideshow' ? 30000 : 45000);
      var show = p.active && t.deadline_in > 0;
      this.timer.style.visibility = show ? 'visible' : 'hidden';
      if (!show) return;
      var secs = Math.max(0, Math.ceil(left / 1000));
      this.timerBar.style.width = Math.max(0, Math.min(100, left / total * 100)) + '%';
      this.timerTxt.textContent = t.phase === 'between' ? 'Next hand in ' + secs + 's' : (t.turn === p.you || (t.side && t.side.to === p.you) ? 'Your time: ' : this.name(t.phase === 'sideshow' && t.side ? t.side.to : t.turn) + ': ') + secs + 's';
      this.timer.classList.toggle('low', left < 10000 && t.phase !== 'between');
      if (left < -400 && performance.now() - this.fired > 2500) { this.fired = performance.now(); this.ctx.fetchState(); }
    },

    /* ----- hooks used by the shared game screen ----- */
    decorate: function (pl, p) {
      var t = p.tp, row = t.players.filter(function (x) { return x.id === pl.id; })[0] || {};
      var sub, chip = '#22a35a';
      if (row.left) { sub = 'Left the table'; chip = '#b8bec9'; }
      else if (row.status === 'packed') { sub = 'Packed'; chip = '#b8bec9'; }
      else if (row.status === 'out') { sub = 'Sitting out'; chip = '#b8bec9'; }
      else sub = (row.seen ? 'Seen' : 'Blind') + (row.put ? ' · in ' + row.put : '');
      if (row.dealer && !row.left) sub += ' · Dealer';
      var stack = el('div', 'tp-stack'); stack.appendChild(tk(row.stack || 0));
      return { chip: chip, sub: sub, extra: stack };
    },
    status: function (p, ctx) {
      var t = p.tp;
      if (!p.active) return null;
      if (t.phase === 'between') return { text: 'Hand over', tone: '' };
      if (t.phase === 'sideshow' && t.side) return t.side.to === p.you ? { text: 'Side show: your answer', tone: 'you' } : { text: 'Side show…', tone: '' };
      if (t.turn === p.you) return { text: 'Your turn', tone: 'you' };
      var pl = ctx.player(t.turn); return { text: 'Waiting for ' + (pl ? pl.name : 'a neighbour') + '…', tone: '' };
    },
    help: function (p) {
      if (!p.active) return '';
      return 'Best hand: Trail (three of a kind), Pure sequence, Sequence, Colour, Pair, High card. Blind players bet the stake, players who have seen their cards pay double. With two left, ask for a Show; a tie goes against whoever asked.';
    },
    actions: function (p, ctx, box) {
      var row = p.tp.players.filter(function (x) { return x.id === p.you; })[0];
      if (!p.active || !row || row.left) return;
      var b = el('button', 'gm-btn danger', 'Leave table'); b.type = 'button';
      var armed = false, timer;
      b.onclick = function () {
        if (!armed) { armed = true; b.classList.add('sure'); b.textContent = 'Tap again: pack and cash out'; timer = setTimeout(function () { armed = false; b.classList.remove('sure'); b.textContent = 'Leave table'; }, 3500); return; }
        clearTimeout(timer); ctx.send('tp', 'leave');
      };
      box.appendChild(b);
    }
  });
})();
