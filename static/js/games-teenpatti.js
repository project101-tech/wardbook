/* Teen Patti: the table, your three cards, the pot, the betting buttons and the log. Money is play money: 1 token = ₹10.
   There is no wallet: everybody picks a buy-in when the table opens and can add ₹500 (50 tokens) whenever they like. */
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
  function rupees(n) { return '₹' + (n * 10).toLocaleString('en-IN'); }
  function signed(n) { return (n > 0 ? '+' : n < 0 ? '−' : '') + rupees(Math.abs(n)); }

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
        if (mine) p.reason = 'Table closed · you took out ' + mine.cashed + ' tokens (' + rupees(mine.cashed) + ') after putting in ' + rupees(mine.invested != null ? mine.invested : mine.initial) + ' · net ' + signed(mine.net);
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
      if (t.phase === 'buyin') {
        this.potBox.replaceChildren(el('div', 'lbl', 'NEW TABLE'), el('div', 'sub', 'Boot ' + rupees(t.boot) + ' per hand · everyone picks their own starting amount'));
      } else {
        this.potBox.replaceChildren(
          el('div', 'lbl', 'POT'), tk(t.pot, true),
          el('div', 'sub', rupees(t.pot) + ' · hand ' + t.hand_no + ' · blind stake ' + t.stake));
      }
      this.me.style.display = t.phase === 'buyin' ? 'none' : '';
      this.banner.replaceChildren(); this.banner.className = 'tp-banner';
      if (t.phase === 'waiting') {
        this.banner.className = 'tp-banner on';
        this.banner.textContent = 'Not enough players can pay the boot (' + t.boot + ' token' + (t.boot === 1 ? '' : 's') + '). Add ' + rupees(t.addon) + ' to keep playing, or the table closes.';
      }
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
      else if (t.phase === 'waiting' && !me.left) label = me.stack < t.boot ? 'Out of tokens: add some to keep playing' : 'Waiting for a top-up';
      else if (me.left) label = 'You left the table';
      else if (me.status === 'out') label = me.stack < t.boot ? 'Out of tokens: add some to join the next hand' : 'Sitting this hand out';
      else if (me.status === 'packed') label = 'You packed';
      else if (t.my_cards) label = t.my_name;
      else label = 'Playing blind · tap your cards to look';
      this.handName.textContent = label;
      this.handName.className = 'tp-handname' + (t.my_cards && inHand ? ' seen' : '');
      var mineNow = (me.left || t.phase === 'over') ? (me.cashed || 0) : (me.stack || 0);
      this.myStack.replaceChildren(document.createTextNode(me.left || t.phase === 'over' ? 'You took out ' : 'Your tokens '), tk(mineNow),
        el('small', '', ' ' + rupees(mineNow) + ' · put in ' + rupees(me.invested || 0)));

      /* what I can do */
      this.renderActs(p);
      this.renderLog(p);
      this.tick();
    },

    /* everybody's tokens, what they put in, and how they stand: the way to compare */
    standings: function (p, note) {
      var self = this, t = p.tp, wrap = el('div', 'tp-stand');
      var rows = t.players.filter(function (r) { return (r.invested || 0) > 0; }).map(function (r) {
        var tokens = (r.stack || 0) + (r.cashed || 0), inv = r.invested || 0;
        return { id: r.id, tokens: tokens, inv: inv, net: tokens - inv, left: r.left };
      }).sort(function (a, b) { return b.net - a.net; });
      var head = el('div', 'srow head');
      ['Player', 'Tokens', 'Put in', 'Net'].forEach(function (h) { head.appendChild(el('span', '', h)); });
      wrap.appendChild(head);
      rows.forEach(function (r) {
        var row = el('div', 'srow' + (r.id === p.you ? ' me' : ''));
        var nm = el('span', 'nm', (r.id === p.you ? 'You' : self.name(r.id))); if (r.left && t.phase !== 'over') nm.appendChild(el('small', '', ' left'));
        row.appendChild(nm);
        var tc = el('span', 'tc'); tc.appendChild(document.createTextNode(r.tokens + ' ')); tc.appendChild(el('small', '', rupees(r.tokens))); row.appendChild(tc);
        row.appendChild(el('span', '', rupees(r.inv)));
        row.appendChild(el('span', 'net ' + (r.net > 0 ? 'up' : r.net < 0 ? 'down' : ''), signed(r.net)));
        wrap.appendChild(row);
      });
      if (note) wrap.appendChild(el('div', 'note', note));
      return wrap;
    },

    buyin: function (tokens) {
      var self = this;
      if (this.working) return; this.working = true;
      this.ctx.send('tp', 'buyin:' + tokens).then(function () { self.working = false; });
    },

    renderBuyin: function (p) {
      var t = p.tp, self = this, box = this.reveal, b = t.buyin || {}, me = t.players.filter(function (x) { return x.id === p.you; })[0] || {};
      var key = JSON.stringify([t.phase, !!b.mine, t.players.map(function (x) { return [x.id, !!x.chosen, !!x.left]; })]);
      if (key === this.buyKey) return;                          // nothing changed: leave the amount box alone while someone is typing
      this.buyKey = key;
      box.replaceChildren(); box.className = 'tp-reveal show buyin';
      if (me.left) { box.appendChild(el('h3', '', 'You are not at this table')); return; }
      if (!b.mine) {
        box.appendChild(el('h3', '', 'How much do you want to play with?'));
        box.appendChild(el('div', 'note', 'Only for this table. Nothing is carried over from earlier games. 1 token = ₹10. Run low later? Add ' + rupees(t.addon) + ' any time.'));
        var chips = el('div', 'tp-chips');
        (b.choices || []).forEach(function (c) {
          var bt = el('button', 'tp-chip', rupees(c)); bt.type = 'button'; bt.onclick = function () { self.buyin(c); };
          bt.appendChild(el('small', '', c + ' tokens')); chips.appendChild(bt);
        });
        box.appendChild(chips);
        var custom = el('div', 'tp-custom');
        var inp = el('input'); inp.type = 'number'; inp.inputMode = 'numeric'; inp.placeholder = 'Other amount in ₹ (min ' + rupees(b.min) + ')';
        inp.min = b.min * 10; inp.step = b.step * 10;
        var go = el('button', 'tp-btn bet', 'Sit down'); go.type = 'button';
        go.onclick = function () {
          var v = parseInt(inp.value, 10);
          if (!v || v % (b.step * 10) || v < b.min * 10 || v > b.max * 10) { self.ctx.toast('Enter ' + rupees(b.min) + ' or more, in steps of ' + rupees(b.step) + '.'); return; }
          self.buyin(v / 10);
        };
        custom.appendChild(inp); custom.appendChild(go); box.appendChild(custom);
      } else {
        box.appendChild(el('h3', '', 'You are in with ' + rupees(me.invested || 0) + ' (' + (me.stack || 0) + ' tokens)'));
      }
      var waiting = t.players.filter(function (x) { return !x.chosen && !x.left; }).map(function (x) { return x.id === p.you ? 'you' : self.name(x.id); });
      box.appendChild(el('div', 'note', waiting.length ? 'Waiting for ' + waiting.join(', ') + '. The first hand is dealt when everyone has chosen.' : 'Dealing…'));
    },

    renderReveal: function (p) {
      var t = p.tp, last = t.last, box = this.reveal, self = this, me0 = t.players.filter(function (x) { return x.id === p.you; })[0] || {};
      if (t.phase === 'buyin') { this.renderBuyin(p); return; }
      this.buyKey = null;
      box.replaceChildren(); box.className = 'tp-reveal';
      if (t.phase === 'over' && t.summary) {
        box.classList.add('show', 'over');
        box.appendChild(el('h3', '', 'Final result'));
        box.appendChild(this.standings(p, 'Tokens are what each player left with. Net = tokens − what they put in (₹10 per token).'));
        return;
      }
      if (me0.left) {
        box.classList.add('show', 'over');
        var mine = (me0.cashed || 0), inv = me0.invested || 0;
        box.appendChild(el('h3', '', 'You left the table'));
        box.appendChild(el('div', 'tp-mine', 'You took out ' + mine + ' tokens (' + rupees(mine) + ') after putting in ' + rupees(inv) + ': ' + signed(mine - inv)));
        box.appendChild(this.standings(p, 'The table right now.' + (t.phase === 'bet' || t.phase === 'sideshow' ? ' Chips in the hand being played are not counted yet.' : '')));
        if (!this.leftShown) { this.leftShown = true; setTimeout(function () { if (box.scrollIntoView) box.scrollIntoView({ behavior: 'smooth', block: 'center' }); }, 60); }
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
      if (by.addon) {
        var hot = (me.stack || 0) < t.boot, armed = this.addArmed && performance.now() - this.addArmed < 3500;
        var ab = btn('addon' + (hot ? ' hot' : ''), armed ? ['Tap again to add ' + rupees(by.addon.tokens)] : ['+ Add ' + rupees(by.addon.tokens) + ' · ', tk(by.addon.tokens)], 'addon');
        ab.onclick = function () {
          if (self.addArmed && performance.now() - self.addArmed < 3500) { self.addArmed = 0; self.do('addon'); return; }
          self.addArmed = performance.now(); self.renderActs(self.p);
          setTimeout(function () { if (self.p) self.renderActs(self.p); }, 3600);
        };
      }
      if (t.phase === 'bet' && t.turn === p.you && !by.bet && !by.show && me.status === 'in') {
        box.appendChild(el('div', 'tp-note', 'You do not have enough tokens to bet. Pack, or wait for a show.'));
      }
      if (t.phase === 'buyin') box.appendChild(el('div', 'tp-note', t.buyin && t.buyin.mine ? 'Waiting for the others to choose…' : 'Pick your starting amount above.'));
      else if (t.phase === 'waiting' && (me.stack || 0) < t.boot) box.appendChild(el('div', 'tp-note', 'You are out of tokens. Add ' + rupees(t.addon) + ' (' + t.addon + ' tokens) to keep playing.'));
      else if (t.phase === 'bet' && me.status === 'out' && (me.stack || 0) < t.boot) box.appendChild(el('div', 'tp-note', 'Add ' + rupees(t.addon) + ' to join the next hand.'));
      if (!box.children.length) box.appendChild(el('div', 'tp-note', me.left ? 'You have left the table. Your result is above.' : 'Waiting…'));
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
          case 'buyin': txt = n + ' sat down with ' + rupees(e.x) + ' (' + e.x + ' tokens)'; break;
          case 'addon': txt = n + ' added ' + rupees(e.x) + ' (' + e.x + ' tokens)'; break;
          case 'nobuyin': txt = n + ' did not choose a buy-in in time and left'; break;
          case 'wait': txt = 'Not enough players can pay the boot. The table waits for a top-up'; break;
          default: txt = '';
        }
        if (txt) box.appendChild(el('div', 'ln' + (e.a === 'win' ? ' win' : ''), txt));
      });
    },

    tick: function () {
      var p = this.p; if (!p) return;
      var t = p.tp, left = t.deadline_in - (performance.now() - (p._at || 0)), total = t.total_ms || 45000;
      var show = p.active && t.deadline_in > 0;
      this.timer.style.visibility = show ? 'visible' : 'hidden';
      if (!show) return;
      var secs = Math.max(0, Math.ceil(left / 1000));
      this.timerBar.style.width = Math.max(0, Math.min(100, left / total * 100)) + '%';
      this.timerTxt.textContent = t.phase === 'buyin' ? 'Choose your buy-in · ' + secs + 's' : t.phase === 'waiting' ? 'Table closes in ' + secs + 's unless someone adds tokens' : t.phase === 'between' ? 'Next hand in ' + secs + 's' : (t.turn === p.you || (t.side && t.side.to === p.you) ? 'Your time: ' : this.name(t.phase === 'sideshow' && t.side ? t.side.to : t.turn) + ': ') + secs + 's';
      this.timer.classList.toggle('low', left < 10000 && t.phase !== 'between');
      if (left < -400 && performance.now() - this.fired > 2500) { this.fired = performance.now(); this.ctx.fetchState(); }
    },

    /* ----- hooks used by the shared game screen ----- */
    decorate: function (pl, p) {
      var t = p.tp, row = t.players.filter(function (x) { return x.id === pl.id; })[0] || {};
      var sub, chip = '#22a35a', done = row.left || t.phase === 'over';
      if (t.phase === 'buyin' && !row.left) { sub = row.chosen ? 'Ready · ' + rupees(row.invested || 0) : 'Choosing buy-in…'; chip = row.chosen ? '#22a35a' : '#b8bec9'; }
      else if (t.phase === 'over') { var rn = (row.cashed || 0) - (row.invested || 0); sub = 'Finished · ' + (rn > 0 ? '+' : rn < 0 ? '−' : '') + rupees(Math.abs(rn)); chip = '#b8bec9'; }
      else if (row.left) { sub = 'Left the table'; chip = '#b8bec9'; }
      else if (row.status === 'packed') { sub = 'Packed'; chip = '#b8bec9'; }
      else if (row.status === 'out') { sub = 'Sitting out'; chip = '#b8bec9'; }
      else sub = (row.seen ? 'Seen' : 'Blind') + (row.put ? ' · in ' + row.put : '');
      if (row.dealer && !row.left && t.phase !== 'buyin') sub += ' · Dealer';
      var stack = el('div', 'tp-stack'); stack.appendChild(tk(done ? (row.cashed || 0) : (row.stack || 0)));
      return { chip: chip, sub: sub, extra: stack };
    },
    status: function (p, ctx) {
      var t = p.tp;
      if (!p.active) return null;
      var meRow = t.players.filter(function (x) { return x.id === p.you; })[0] || {};
      if (t.phase === 'buyin') return t.buyin && t.buyin.mine ? { text: 'Waiting for the others…', tone: '' } : { text: 'Pick your buy-in', tone: 'you' };
      if (t.phase === 'waiting') return (meRow.stack || 0) < t.boot && !meRow.left ? { text: 'Add tokens to play', tone: 'you' } : { text: 'Waiting for a top-up…', tone: '' };
      if (t.phase === 'between') return { text: 'Hand over', tone: '' };
      if (t.phase === 'sideshow' && t.side) return t.side.to === p.you ? { text: 'Side show: your answer', tone: 'you' } : { text: 'Side show…', tone: '' };
      if (t.turn === p.you) return { text: 'Your turn', tone: 'you' };
      var pl = ctx.player(t.turn); return { text: 'Waiting for ' + (pl ? pl.name : 'a neighbour') + '…', tone: '' };
    },
    help: function (p) {
      if (!p.active) return '';
      return 'Everyone picks their own starting amount and can add ' + rupees(p.tp.addon) + ' whenever they run low. Best hand: Trail (three of a kind), Pure sequence, Sequence, Colour, Pair, High card. Blind players bet the stake, players who have seen their cards pay double. With two left, ask for a Show; a tie goes against whoever asked.';
    },
    actions: function (p, ctx, box) {
      var row = p.tp.players.filter(function (x) { return x.id === p.you; })[0];
      if (!p.active || !row || row.left) return;
      var b = el('button', 'gm-btn danger', 'Leave table'); b.type = 'button';
      var armed = false, timer;
      b.onclick = function () {
        if (!armed) { armed = true; b.classList.add('sure'); b.textContent = 'Tap again: leave and see my result'; timer = setTimeout(function () { armed = false; b.classList.remove('sure'); b.textContent = 'Leave table'; }, 3500); return; }
        clearTimeout(timer);
        ctx.send('tp', 'leave').then(function (j) {                    // pop up your result straight away
          var g = j && j.game, r = g && g.tp && g.tp.players.filter(function (x) { return x.id === g.you; })[0];
          if (r && r.left) { var net = (r.cashed || 0) - (r.invested || 0); ctx.toast('You left with ' + r.cashed + ' tokens (' + rupees(r.cashed) + ') · net ' + (net > 0 ? '+' : net < 0 ? '−' : '') + rupees(Math.abs(net))); }
        });
      };
      box.appendChild(b);
    }
  });
})();
