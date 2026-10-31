"""Teen Patti (Flash): the rules, with no Django in it.

A table is a series of hands. Money is counted in tokens (1 token = 10 rupees of pretend money).
There is no wallet: every table starts fresh. Each player picks their own buy-in when the table opens, and can add
more (ADDON_TOKENS at a time, as often as they like) whenever they run low. We keep track of what each player has put
in ("invested"), so when they leave or the table closes we can show tokens, invested and net.
Everything the server knows (every hand, the deck) lives in `state`; `public_view()` is the only thing that
goes to a browser, and it only contains the viewer's own cards.
"""
import random

SUITS = "SHDC"
TURN_MS = 45_000        # time to act before you are packed automatically
SIDE_MS = 30_000        # time to answer a side show
BETWEEN_MS = 9_000      # pause after a hand before the next one is dealt
MAX_STAKE_MULT = 16     # the blind stake can be raised (doubled) up to boot x 16
MIN_BUYIN_HANDS = 10    # you need at least 10 x boot to sit down
TOKEN_RUPEES = 10       # 1 token = 10 rupees of pretend money
ADDON_TOKENS = 50       # one top-up = 50 tokens = 500 rupees
BUYIN_PRESETS = (50, 100, 150, 200, 300, 500)   # tokens: 500, 1000, 1500, 2000, 3000, 5000 rupees
MIN_BUYIN_TOKENS = 50   # 500 rupees
MAX_BUYIN_TOKENS = 10_000
BUYIN_STEP = 10         # custom buy-ins go up in steps of 10 tokens (100 rupees)
BUYIN_MS = 180_000      # time everyone has to choose their buy-in when the table opens
REBUY_MS = 90_000       # when fewer than two people can pay the boot, how long the table waits for a top-up
MAX_TIMEOUTS = 3        # this many missed turns in a row and you are taken off the table
LOG_KEEP = 14
_rng = random.SystemRandom()

RANK_NAMES = {14: "Ace", 13: "King", 12: "Queen", 11: "Jack", 10: "10", 9: "9", 8: "8", 7: "7", 6: "6", 5: "5", 4: "4", 3: "3", 2: "2"}
RANK_SHORT = {14: "A", 13: "K", 12: "Q", 11: "J"}
CATEGORY = {6: "Trail", 5: "Pure sequence", 4: "Sequence", 3: "Colour", 2: "Pair", 1: "High card"}


# ---------------------------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------------------------
def new_deck():
    deck = [[r, s] for r in range(2, 15) for s in SUITS]
    _rng.shuffle(deck)
    return deck


def _seq_value(ranks):
    """Highest-ranking run is A-K-Q, then A-2-3, then K-Q-J ... down to 4-3-2. None if not a run."""
    r = sorted(ranks, reverse=True)
    if r == [14, 3, 2]:
        return 13.5
    if r[0] - r[1] == 1 and r[1] - r[2] == 1:
        return r[0]
    return None


def score(cards):
    """A comparable tuple: bigger is a better hand."""
    ranks = sorted((c[0] for c in cards), reverse=True)
    flush = len({c[1] for c in cards}) == 1
    if ranks[0] == ranks[2]:
        return (6, ranks[0])
    run = _seq_value(ranks)
    if run is not None:
        return (5 if flush else 4, run)
    if flush:
        return (3, *ranks)
    if ranks[0] == ranks[1] or ranks[1] == ranks[2]:
        pair = ranks[1]
        kicker = ranks[2] if ranks[0] == ranks[1] else ranks[0]
        return (2, pair, kicker)
    return (1, *ranks)


def hand_name(cards):
    s = score(cards)
    ranks = sorted((c[0] for c in cards), reverse=True)
    cat = s[0]
    if cat == 6:
        return f"Trail of {RANK_NAMES[s[1]]}s" if s[1] != 14 else "Trail of Aces"
    if cat in (5, 4):
        label = "A-2-3" if s[1] == 13.5 else "-".join(RANK_SHORT.get(r, str(r)) for r in ranks)
        return ("Pure sequence " if cat == 5 else "Sequence ") + f"({label})"
    if cat == 3:
        return f"Colour ({RANK_NAMES[ranks[0]]} high)"
    if cat == 2:
        return f"Pair of {RANK_NAMES[s[1]]}s"
    return f"{RANK_NAMES[ranks[0]]} high"


# ---------------------------------------------------------------------------------------------
# Setting up
# ---------------------------------------------------------------------------------------------
def new_table(players, boot, now):
    """players: ordered user ids. Returns the state. The table opens in the 'buyin' phase: everybody chooses how many
    tokens to sit down with, and the first hand is dealt once all of them have chosen."""
    zero = {str(u): 0 for u in players}
    state = {"players": list(players), "boot": int(boot), "stacks": dict(zero), "initial": dict(zero),
             "invested": dict(zero), "cashed": dict(zero), "chosen": [],
             "left": [], "hand_no": 0, "dealer": -1, "phase": "buyin", "hand": None, "last": None,
             "log": [], "n": 0, "deadline": now + BUYIN_MS, "timeouts": {}, "summary": None}
    return state


def _invested(state):
    """What each player has put in so far (older tables only have 'initial')."""
    return state.get("invested") or state["initial"]


def min_buyin(boot):
    return max(MIN_BUYIN_TOKENS, int(boot) * MIN_BUYIN_HANDS)


def _log(state, uid, act, amount=0, other=None):
    state["n"] += 1
    entry = {"n": state["n"], "u": uid, "a": act}
    if amount:
        entry["x"] = amount
    if other is not None:
        entry["v"] = other
    state["log"].append(entry)
    del state["log"][:-LOG_KEEP]


def _ready(state):
    """Players who can be dealt in: still at the table and able to pay the boot."""
    return [u for u in state["players"] if u not in state["left"] and state["stacks"][str(u)] >= state["boot"]]


def _active(state):
    return [u for u in state["players"] if u not in state["left"]]


def _wait(state, now):
    """Fewer than two people can pay the boot: pause the table so somebody can add tokens."""
    if state["phase"] != "waiting":
        _log(state, None, "wait")
    state["phase"] = "waiting"
    state["deadline"] = now + REBUY_MS


def _next_hand(state, now):
    """Deal a new hand, wait for top-ups if too few can pay the boot, or close the table if too few people remain."""
    if len(_active(state)) < 2:
        _close(state, now)
        return
    ready = _ready(state)
    if len(ready) < 2:
        _wait(state, now)
        return
    players = state["players"]
    d = state["dealer"]
    for _ in range(len(players)):                       # the dealer button moves to the next player who is playing
        d = (d + 1) % len(players)
        if players[d] in ready:
            break
    state["dealer"] = d
    state["hand_no"] += 1
    deck = new_deck()
    cards, seen, status, put = {}, {}, {}, {}
    pot = 0
    for u in players:
        if u in ready:
            cards[str(u)] = [deck.pop(), deck.pop(), deck.pop()]
            seen[str(u)] = False
            status[str(u)] = "in"
            state["stacks"][str(u)] -= state["boot"]
            put[str(u)] = state["boot"]
            pot += state["boot"]
        else:
            status[str(u)] = "out"
            put[str(u)] = 0
    state["hand"] = {"cards": cards, "seen": seen, "status": status, "put": put, "pot": pot, "stake": state["boot"],
                     "turn": None, "side": None, "side_result": None}
    state["hand"]["turn"] = _after(state, players[d])
    state["phase"] = "bet"
    state["deadline"] = now + TURN_MS
    _log(state, None, "deal")


def _cash_out(state, uid):
    """The player's tokens leave the table (they are shown in the result; nothing is stored anywhere)."""
    amount = state["stacks"][str(uid)]
    if amount:
        state["cashed"][str(uid)] += amount
        state["stacks"][str(uid)] = 0


def _close(state, now):
    for u in state["players"]:
        _cash_out(state, u)
    inv = _invested(state)
    state["phase"] = "over"
    state["summary"] = [{"id": u, "net": state["cashed"][str(u)] - inv[str(u)], "initial": inv[str(u)],
                         "invested": inv[str(u)], "cashed": state["cashed"][str(u)]} for u in state["players"]]
    state["deadline"] = 0


# ---------------------------------------------------------------------------------------------
# Buy-in and top-ups
# ---------------------------------------------------------------------------------------------
def _buyin(state, uid, raw, now):
    """A player picks how many tokens they sit down with. Returns an error string ('' on success)."""
    if state["phase"] != "buyin":
        return "The table has already started."
    key = str(uid)
    if uid in state.get("chosen", []):
        return "You have already chosen your buy-in."
    try:
        tokens = int(raw)
    except (TypeError, ValueError):
        return "Choose how much to bring."
    low = min_buyin(state["boot"])
    if tokens < low:
        return f"The smallest buy-in for this table is {low} tokens ({low * TOKEN_RUPEES} rupees)."
    if tokens > MAX_BUYIN_TOKENS:
        return f"The biggest buy-in is {MAX_BUYIN_TOKENS} tokens."
    if tokens % BUYIN_STEP:
        return f"Buy-ins go up in steps of {BUYIN_STEP * TOKEN_RUPEES} rupees."
    state["stacks"][key] = tokens
    state["initial"][key] = tokens
    state["invested"][key] = tokens
    state["chosen"].append(uid)
    _log(state, uid, "buyin", tokens)
    _buyin_progress(state, now)
    return ""


def _buyin_progress(state, now):
    """Deal the first hand once everyone still at the table has chosen; close the table if too few remain."""
    active = _active(state)
    if len(active) < 2:
        _close(state, now)
    elif all(u in state["chosen"] for u in active):
        _next_hand(state, now)


def _buyin_timeout(state, now):
    """Anyone who did not choose in time is taken off the table; the rest start (if there are at least two)."""
    for u in state["players"]:
        if u not in state["left"] and u not in state["chosen"]:
            state["left"].append(u)
            _log(state, u, "nobuyin")
    _buyin_progress(state, now)


def _addon(state, uid, now):
    """Add ADDON_TOKENS (500 rupees) to a player's tokens. No limit on how often."""
    key = str(uid)
    inv = state.setdefault("invested", dict(state["initial"]))
    state["stacks"][key] += ADDON_TOKENS
    inv[key] = inv.get(key, 0) + ADDON_TOKENS
    _log(state, uid, "addon", ADDON_TOKENS)
    if state["phase"] == "waiting" and len(_ready(state)) >= 2:
        _next_hand(state, now)


# ---------------------------------------------------------------------------------------------
# Turn order helpers
# ---------------------------------------------------------------------------------------------
def _in(state):
    h = state["hand"]
    return [u for u in state["players"] if h["status"].get(str(u)) == "in"]


def _after(state, uid):
    """The next player still in the hand, going round the table after `uid`."""
    players = state["players"]
    h = state["hand"]
    i = players.index(uid)
    for k in range(1, len(players) + 1):
        cand = players[(i + k) % len(players)]
        if h["status"].get(str(cand)) == "in":
            return cand
    return None


def _before(state, uid):
    players = state["players"]
    h = state["hand"]
    i = players.index(uid)
    for k in range(1, len(players) + 1):
        cand = players[(i - k) % len(players)]
        if h["status"].get(str(cand)) == "in":
            return cand
    return None


def bet_cost(state, uid):
    h = state["hand"]
    return h["stake"] * (2 if h["seen"][str(uid)] else 1)


def raise_cost(state, uid):
    h = state["hand"]
    new = h["stake"] * 2
    return new * (2 if h["seen"][str(uid)] else 1)


def _pay(state, uid, amount):
    h = state["hand"]
    amount = min(amount, state["stacks"][str(uid)])
    state["stacks"][str(uid)] -= amount
    h["put"][str(uid)] += amount
    h["pot"] += amount
    return amount


# ---------------------------------------------------------------------------------------------
# What a player may do right now
# ---------------------------------------------------------------------------------------------
def options(state, uid):
    """List of {"a": action, "cost": tokens} the player could take now."""
    out = _play_options(state, uid)
    if state["phase"] in ("between", "bet", "sideshow", "waiting") and uid in state["players"] and uid not in state["left"]:
        out.append({"a": "addon", "tokens": ADDON_TOKENS})          # always possible, as often as you like
    return out


def _play_options(state, uid):
    out = []
    if state["phase"] == "buyin":
        if uid in state["players"] and uid not in state["left"] and uid not in state.get("chosen", []):
            out.append({"a": "buyin"})
        return out
    if state["phase"] in ("over", "waiting") or state["hand"] is None:
        return out
    h = state["hand"]
    key = str(uid)
    if state["phase"] == "between":
        if uid not in state["left"]:
            out.append({"a": "deal"})
        return out
    if h["status"].get(key) != "in":
        return out
    if not h["seen"][key] and state["phase"] in ("bet", "sideshow"):
        out.append({"a": "see"})
    if state["phase"] == "sideshow":
        if h["side"]["to"] == uid:
            out.append({"a": "side_accept"})
            out.append({"a": "side_decline"})
        return out
    out.append({"a": "pack"})
    if h["turn"] != uid:
        return out
    stack = state["stacks"][key]
    cost = bet_cost(state, uid)
    people = _in(state)
    if len(people) == 2:
        out.append({"a": "show", "cost": min(cost, stack)})
    if stack >= cost:
        out.append({"a": "bet", "cost": cost})
        if h["stake"] * 2 <= state["boot"] * MAX_STAKE_MULT and stack >= raise_cost(state, uid):
            out.append({"a": "raise", "cost": raise_cost(state, uid)})
        prev = _before(state, uid)
        if len(people) >= 3 and h["seen"][key] and prev is not None and h["seen"][str(prev)]:
            out.append({"a": "side", "cost": cost, "to": prev})
    return out


# ---------------------------------------------------------------------------------------------
# Acting
# ---------------------------------------------------------------------------------------------
def act(state, uid, action, now):
    """Apply one action. Returns an error string ('' on success)."""
    if state["phase"] == "over":
        return "This table is closed."
    if uid not in state["players"] or uid in state["left"]:
        return "You are not at this table."
    if action == "leave":
        _leave(state, uid, now)
        return ""
    if action.startswith("buyin:"):
        return _buyin(state, uid, action[6:], now)
    allowed = {o["a"]: o for o in options(state, uid)}
    if action not in allowed:
        return "You cannot do that right now."
    h = state["hand"]
    state["timeouts"][str(uid)] = 0
    if action == "addon":
        _addon(state, uid, now)
        return ""
    if action == "deal":
        if state["phase"] == "between":
            _next_hand(state, now)
        return ""
    if action == "see":
        h["seen"][str(uid)] = True
        _log(state, uid, "see")
        return ""
    if action == "pack":
        _pack(state, uid, now)
        return ""
    if action == "bet":
        paid = _pay(state, uid, allowed["bet"]["cost"])
        _log(state, uid, "chaal" if h["seen"][str(uid)] else "blind", paid)
        _advance(state, uid, now)
        return ""
    if action == "raise":
        paid = _pay(state, uid, allowed["raise"]["cost"])
        h["stake"] *= 2
        _log(state, uid, "raise", paid)
        _advance(state, uid, now)
        return ""
    if action == "show":
        paid = _pay(state, uid, allowed["show"]["cost"])
        _log(state, uid, "show", paid)
        other = [u for u in _in(state) if u != uid][0]
        _showdown(state, uid, other, now)
        return ""
    if action == "side":
        target = allowed["side"]["to"]
        paid = _pay(state, uid, allowed["side"]["cost"])
        h["side"] = {"from": uid, "to": target}
        h["side_result"] = None
        state["phase"] = "sideshow"
        state["deadline"] = now + SIDE_MS
        _log(state, uid, "side", paid, target)
        return ""
    if action in ("side_accept", "side_decline"):
        _answer_side(state, action == "side_accept", now)
        return ""
    return "Unknown action."


def _advance(state, uid, now):
    h = state["hand"]
    nxt = _after(state, uid)
    h["turn"] = nxt
    state["phase"] = "bet"
    state["deadline"] = now + TURN_MS


def _pack(state, uid, now):
    h = state["hand"]
    h["status"][str(uid)] = "packed"
    _log(state, uid, "pack")
    people = _in(state)
    if len(people) == 1:
        _finish_hand(state, people[0], "pack", {}, now)
        return
    if h["turn"] == uid:
        _advance(state, uid, now)


def _answer_side(state, accept, now):
    h = state["hand"]
    asker, target = h["side"]["from"], h["side"]["to"]
    h["side"] = None
    if not accept:
        _log(state, target, "side_no", 0, asker)
        _advance(state, asker, now)
        return
    sa, sb = score(h["cards"][str(asker)]), score(h["cards"][str(target)])
    loser = target if sa > sb else asker                 # a tie goes against the player who asked
    winner = asker if loser == target else target
    h["status"][str(loser)] = "packed"
    h["side_result"] = {"a": asker, "b": target, "winner": winner, "loser": loser,
                        "cards": {str(asker): h["cards"][str(asker)], str(target): h["cards"][str(target)]}}
    _log(state, winner, "side_win", 0, loser)
    people = _in(state)
    if len(people) == 1:                                  # someone else left during the side show: the winner takes the pot
        _finish_hand(state, people[0], "pack", {}, now)
        return
    _advance(state, asker, now)


def _showdown(state, asker, other, now):
    h = state["hand"]
    sa, sb = score(h["cards"][str(asker)]), score(h["cards"][str(other)])
    winner = asker if sa > sb else other                  # a tie goes against the player who asked for the show
    reveal = {str(asker): h["cards"][str(asker)], str(other): h["cards"][str(other)]}
    _finish_hand(state, winner, "show", reveal, now)


def _finish_hand(state, winner, kind, reveal, now):
    h = state["hand"]
    pot = h["pot"]
    state["stacks"][str(winner)] += pot
    state["last"] = {"n": state["hand_no"], "winner": winner, "amount": pot, "kind": kind,
                     "put": dict(h["put"]), "reveal": reveal,
                     "names": {u: hand_name(c) for u, c in reveal.items()}}
    h["pot"] = 0
    h["turn"] = None
    h["side"] = None
    state["phase"] = "between"
    state["deadline"] = now + BETWEEN_MS
    _log(state, winner, "win", pot)
    _settle_leavers(state, now)


def _settle_leavers(state, now):
    """Somebody has left between hands: close the table if too few remain, or pause it if too few can pay the boot."""
    if len(_active(state)) < 2:
        _close(state, now)
    elif len(_ready(state)) < 2:
        _wait(state, now)


def _leave(state, uid, now):
    """Leave the table: your hand (if any) is packed and your tokens are cashed out (shown in your result)."""
    h = state["hand"]
    if state["phase"] in ("bet", "sideshow") and h and h["status"].get(str(uid)) == "in":
        if state["phase"] == "sideshow":
            side = h["side"]
            if side["to"] == uid:                          # leaving counts as declining
                _answer_side(state, False, now)
            elif side["from"] == uid:                      # the side show is called off
                h["side"] = None
                state["phase"] = "bet"
                h["turn"] = _after(state, uid)
                state["deadline"] = now + TURN_MS
        h["status"][str(uid)] = "packed"
        _log(state, uid, "pack")
        people = _in(state)
        _cash_out(state, uid)
        state["left"].append(uid)
        _log(state, uid, "left")
        if len(people) == 1:
            _finish_hand(state, people[0], "pack", {}, now)
        elif state["phase"] == "bet" and h["turn"] == uid:
            _advance(state, uid, now)
        return
    if uid not in state["left"]:
        _cash_out(state, uid)
        state["left"].append(uid)
        _log(state, uid, "left")
    if state["phase"] == "buyin":
        _buyin_progress(state, now)
    elif state["phase"] in ("between", "waiting"):
        _settle_leavers(state, now)


def settle(state, now):
    """Deal with a turn timer that has run out. Returns True if the state changed."""
    changed = False
    for _ in range(6):
        if state["phase"] == "over" or not state["deadline"] or now < state["deadline"]:
            break
        changed = True
        h = state["hand"]
        if state["phase"] == "buyin":
            _buyin_timeout(state, now)
        elif state["phase"] == "waiting":
            _close(state, now)                            # nobody topped up in time
        elif state["phase"] == "between":
            _next_hand(state, now)
        elif state["phase"] == "sideshow":
            _answer_side(state, False, now)
        else:
            uid = h["turn"]
            n = state["timeouts"].get(str(uid), 0) + 1
            state["timeouts"][str(uid)] = n
            _log(state, uid, "timeout")
            if n >= MAX_TIMEOUTS:
                _leave(state, uid, now)
            else:
                _pack(state, uid, now)
    return changed


# ---------------------------------------------------------------------------------------------
# What leaves the server
# ---------------------------------------------------------------------------------------------
def total_tokens(state):
    """Sanity check: tokens on the table + in the pot + already cashed out. Always equals what everyone has put in."""
    pot = state["hand"]["pot"] if state["hand"] else 0
    return sum(state["stacks"].values()) + pot + sum(state["cashed"].values())


def info(state):
    """The same shape the other games return: who moves next, and whether the table is over."""
    h = state["hand"]
    finished = state["phase"] == "over"
    if state["phase"] == "bet":
        nxt = h["turn"]
    elif state["phase"] == "sideshow":
        nxt = h["side"]["to"]
    else:
        nxt = (state["last"] or {}).get("winner") or state["players"][0]
    out = {"next": nxt, "winner": None, "finished": finished, "reason": "", "nudge": state["phase"] in ("bet", "sideshow")}
    if finished:
        best = max(state["summary"], key=lambda r: r["net"])
        ties = [r for r in state["summary"] if r["net"] == best["net"]]
        out["winner"] = best["id"] if best["net"] > 0 and len(ties) == 1 else None
        out["reason"] = "Table closed"
    return out


def public_view(state, uid, now):
    h = state["hand"]
    inv = _invested(state)
    chosen = state.get("chosen", [])
    players = []
    for u in state["players"]:
        k = str(u)
        row = {"id": u, "stack": state["stacks"][k], "left": u in state["left"],
               "dealer": state["dealer"] >= 0 and state["players"][state["dealer"]] == u,
               "initial": state["initial"][k], "invested": inv.get(k, 0), "cashed": state["cashed"][k],
               "chosen": u in chosen}
        if h:
            row.update({"status": h["status"].get(k, "out"), "seen": bool(h["seen"].get(k)), "put": h["put"].get(k, 0)})
        players.append(row)
    me = None
    if h and str(uid) in h["cards"] and h["seen"].get(str(uid)):
        me = h["cards"][str(uid)]
    phase = state["phase"]
    total_ms = {"buyin": BUYIN_MS, "waiting": REBUY_MS, "between": BETWEEN_MS, "sideshow": SIDE_MS}.get(phase, TURN_MS)
    out = {"boot": state["boot"], "phase": phase, "hand_no": state["hand_no"], "players": players,
           "pot": h["pot"] if h else 0, "stake": h["stake"] if h else state["boot"], "turn": h["turn"] if h else None,
           "my_cards": me, "my_name": hand_name(me) if me else "", "options": options(state, uid),
           "deadline_in": max(0, state["deadline"] - now) if state["deadline"] else 0, "total_ms": total_ms,
           "log": state["log"][-8:],
           "last": state["last"], "summary": state["summary"], "side": h["side"] if h else None,
           "max_stake": state["boot"] * MAX_STAKE_MULT, "n": state["n"],
           "addon": ADDON_TOKENS, "token_rupees": TOKEN_RUPEES}
    if phase == "buyin":
        low = min_buyin(state["boot"])
        out["buyin"] = {"choices": [c for c in BUYIN_PRESETS if c >= low], "min": low, "max": MAX_BUYIN_TOKENS,
                        "step": BUYIN_STEP, "mine": uid in chosen}
    if h and h.get("side_result") and uid in (h["side_result"]["a"], h["side_result"]["b"]):
        out["side_result"] = h["side_result"]
    return out
