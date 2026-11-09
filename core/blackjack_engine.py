"""Blackjack: the rules, with no Django in it (so they can be tested on their own).

THE TABLE
  * Two to seven neighbours. One of them is the dealer. The first dealer is picked at random, then the deal moves
    to the next seat every ROUNDS_PER_DEALER (5) rounds. When every player has dealt 5 rounds, that is ONE GAME.
    The host chooses how many games to play before the table opens.
  * The dealer is a person: they press "Deal cards" to start each round, and press "Draw card" when the rules
    make them take one. The dealer plays against every other player, and plays last.
  * Play is for CHIPS (play money). Every game starts with everyone holding START_CHIPS. Before each deal every
    player (not the dealer) bets any even number of chips from MIN_BET up to the table limit, or sits the round
    out. The dealer is the BANK for their rounds: they collect the losing bets and pay the winning ones, so chips
    only move between players. Holdings are settled after every round, and the table limit (a share of the bank)
    plus the "bank must cover it" check on double / split keep the bank from ever owing more than it has.
  * Whoever holds the most chips at the end of a game wins that game. Chips start fresh in every game.

THE CARDS
  * 6 decks in a shoe, reshuffled when a quarter is left (the "cut card"), checked at the start of a round.
  * 2-10 count as their number, J Q K count 10, an Ace counts 1 or 11, whichever is better for the hand
    (a hand with an Ace counted as 11 is "soft"). You choose how to play it by hitting or standing.

THE ROUND
  0. Bets: every player bets (or sits out); the dealer presses Deal once at least one bet is in.
  1. Deal: every player who bet gets 2 cards face up, the dealer 1 face up and 1 face down (the hole card), dealt one
     card at a time starting left of the dealer.
  2. If the dealer shows an Ace, every player may buy INSURANCE (half their bet). It pays 2 to 1 if the dealer has
     blackjack. Then, when the dealer shows an Ace or a ten-value card, the dealer checks the hole card ("peek").
     A dealer blackjack ends the round on the spot: players lose their bet, except a player with blackjack, who
     pushes (gets the bet back).
  3. A player with blackjack (an Ace plus a ten-value card as the first two cards) is paid 3 to 2 and does not play.
     Payouts: win 1 to 1, blackjack 3 to 2, insurance 2 to 1, push returns the bet, a double or a split hand pays
     on its own (larger) bet.
  4. Everyone else plays in turn, from the seat left of the dealer round to the dealer's right:
       Hit      take a card. Over 21 is a bust and the hand loses at once. On 21 the hand stands by itself.
       Stand    keep what you have.
       Double   (first two cards of a hand only) double the bet, take exactly one card, then stand.
       Split    (first two cards of a hand, equal in value; ten, Jack, Queen, King count as equal) make two hands,
                each with a bet equal to the first. Up to 4 hands per player. You may double after splitting.
                Split Aces get one card each and then stand, and cannot be split again. A 21 after a split is
                just 21, not blackjack.
  5. The dealer turns the hole card up and must draw on 16 or less and stand on every 17 or more (soft 17 too).
     If every player has busted or has blackjack, the dealer does not draw.
  6. Settle: a hand beats the dealer by a higher total not over 21, or when the dealer busts. Equal totals push.
     Blackjack beats any other 21. A player who busts loses even if the dealer busts afterwards.

Everything the server knows (the shoe, the hole card) lives in `state`; `public_view()` is the only thing that goes
to a browser and it never contains the shoe, nor the hole card (except for the dealer's own eyes).
"""
import random

SUITS = "SHDC"
DECKS = 6
CUT_AT = DECKS * 52 // 4         # reshuffle at the start of a round when fewer cards than this are left
ROUNDS_PER_DEALER = 5
MAX_HANDS = 4                    # a player can split up to 4 hands
START_CHIPS = 1000               # what everybody holds at the start of every game
MIN_BET = 10
DEAL_MS = 45_000                 # betting, then the dealer's time to press Deal
INSURANCE_MS = 25_000
TURN_MS = 40_000                 # time to hit, stand ...
DRAW_MS = 40_000                 # the dealer's time to draw a forced card
RESULT_MS = 10_000               # how long a result stays up before the next round opens
MAX_TIMEOUTS = 3                 # this many missed turns in a row and you are taken off the table
LOG_KEEP = 16
_rng = random.SystemRandom()


# ---------------------------------------------------------------------------------------------
# Cards. A card is a number 0..51: suit = n // 13 (S H D C), rank = n % 13 + 1 (1 = Ace ... 13 = King).
# ---------------------------------------------------------------------------------------------
def cid(rank, suit):
    return SUITS.index(suit) * 13 + rank - 1


def rank(c):
    return c % 13 + 1


def suit(c):
    return SUITS[c // 13]


def value(c):
    """What the card is worth with an Ace as 1."""
    return min(rank(c), 10)


def pair(c):
    """How a card is sent to the browser: [rank, suit]."""
    return [rank(c), suit(c)]


def new_shoe():
    shoe = list(range(52)) * DECKS
    _rng.shuffle(shoe)
    return shoe


def count(cards):
    """(best total, soft?). An Ace is 11 when that does not bust the hand (then the hand is 'soft')."""
    hard = sum(value(c) for c in cards)
    if any(rank(c) == 1 for c in cards) and hard + 10 <= 21:
        return hard + 10, True
    return hard, False


def total_text(cards):
    """'17', or '7/17' for a soft hand that could still be played either way, or 'Bust'."""
    best, soft = count(cards)
    if best > 21:
        return f"Bust {best}"
    if soft and best < 21:
        return f"{best - 10}/{best}"
    return str(best)


def is_natural(cards):
    return len(cards) == 2 and count(cards)[0] == 21


# ---------------------------------------------------------------------------------------------
# Hands
# ---------------------------------------------------------------------------------------------
def _new_hand(stake):
    return {"cards": [], "stake": stake, "done": False, "doubled": False, "split": False, "ace": False}


def hand_natural(h):
    """Blackjack: the first two cards of a hand that was not made by splitting."""
    return not h["split"] and is_natural(h["cards"])


def hand_bust(h):
    return count(h["cards"])[0] > 21


# ---------------------------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------------------------
def _log(state, uid, act, extra=None):
    state["n"] += 1
    entry = {"n": state["n"], "u": uid, "a": act}
    if extra is not None:
        entry["x"] = extra
    state["log"].append(entry)
    del state["log"][:-LOG_KEEP]


def _draw(state):
    if not state["shoe"]:                       # never happens in practice: a long round can use up a whole shoe
        state["shoe"] = new_shoe()
        state["reshuffles"] += 1
        _log(state, None, "shuffle")
    return state["shoe"].pop()


def _active(state):
    return [u for u in state["players"] if u not in state["left"]]


def _seat_order(state, dealer):
    """Everybody except the dealer, starting with the seat left of the dealer (they act first)."""
    seats = state["players"]
    i = seats.index(dealer)
    return [u for u in seats[i + 1:] + seats[:i] if u not in state["left"]]


def game_no(state):
    return state["block"] // state["n0"] + 1


# ---------------------------------------------------------------------------------------------
# Chips. A player's chips are taken out of their stack the moment they are committed (bet, double, split,
# insurance) and paid back, with winnings, when the round settles. "Holdings" = stack + chips in play.
# ---------------------------------------------------------------------------------------------
def _even_down(n):
    n = max(0, int(n))
    return n - n % 2


def table_max(state):
    """The most one player may bet this round: a fair share of the bank, so that every player could bet it, win
    (blackjack pays 3 to 2) or double, and the bank could still pay everybody."""
    rnd = state["rnd"]
    return _even_down(state["chips"][str(rnd["dealer"])] // (2 * max(1, len(rnd["order"]))))


def bet_cap(state, uid):
    rnd, k = state["rnd"], str(uid)
    return _even_down(min(state["chips"][k] + rnd["bets"].get(k, 0), table_max(state)))


def _eligible(state):
    """Players who could bet this round (they have the chips, and the bank is big enough)."""
    rnd = state["rnd"]
    return [u for u in rnd["order"] if u not in state["left"] and bet_cap(state, u) >= MIN_BET]


def _undecided(state):
    rnd = state["rnd"]
    return [u for u in _eligible(state) if str(u) not in rnd["bets"] and u not in rnd["sit"]]


def _exposure(state):
    """The most the bank could have to pay out on the table right now (a blackjack pays 3 to 2; before the cards
    are dealt every bet counts as if it might be one)."""
    rnd = state["rnd"]
    if rnd["hands"]:
        return sum(h["stake"] * (3 if hand_natural(h) else 2) // 2
                   for hs in rnd["hands"].values() for h in hs if not hand_bust(h))
    return sum(b * 3 // 2 for b in rnd["bets"].values())


def _free_bank(state):
    return state["chips"][str(state["rnd"]["dealer"])] - _exposure(state)


def in_play(state, uid):
    """Chips of this player that are on the table (bet, doubles, splits, insurance) and not yet settled."""
    rnd, k = state["rnd"], str(uid)
    if not rnd or rnd.get("done"):
        return 0
    if rnd["hands"]:
        return sum(h["stake"] for h in rnd["hands"].get(k, [])) + rnd["ins_amt"].get(k, 0)
    return rnd["bets"].get(k, 0)


def holdings(state, uid):
    return state["chips"][str(uid)] + in_play(state, uid)


def _void_round(state):
    """Give every chip on the table back (used when the table closes in the middle of a round)."""
    rnd = state["rnd"]
    if not rnd or rnd.get("done"):
        return
    for k in state["chips"]:
        state["chips"][k] += in_play(state, int(k))
    rnd["done"] = True


# ---------------------------------------------------------------------------------------------
# Setting up
# ---------------------------------------------------------------------------------------------
def new_table(players, games, now):
    """players: user ids. Seats are shuffled; the first seat deals first (so the first dealer is random)."""
    seats = list(players)
    _rng.shuffle(seats)
    n = len(seats)
    games = max(1, int(games))
    zero = {str(u): 0 for u in seats}
    start = {str(u): START_CHIPS for u in seats}
    state = {"players": seats, "n0": n, "games": games, "rpd": ROUNDS_PER_DEALER,
             "sched": [seats[i % n] for i in range(games * n)],       # who deals each block of 5 rounds
             "block": 0, "rb": 0, "round_no": 0, "left": [], "timeouts": {},
             "chips": start, "scores": dict(zero), "lastbet": {}, "gdone": [],
             "shoe": new_shoe(), "reshuffles": 0, "phase": "deal", "rnd": None, "last": None,
             "log": [], "n": 0, "deadline": 0, "summary": None, "winner": None, "reason": ""}
    _log(state, seats[0], "start")
    _next_round(state, now)
    _pump(state, now)
    return state


def _next_round(state, now):
    """Open the next round: same dealer for 5 rounds, then on to the next seat. Closes finished games."""
    if len(_active(state)) < 2:
        return _finish(state, now, "Everyone else left the table", early=True)
    state["rb"] += 1
    sched, n0 = state["sched"], state["n0"]
    over = state["rb"] > state["rpd"] or sched[state["block"]] in state["left"]
    if over:
        b = state["block"] + 1
        while b < len(sched) and sched[b] in state["left"]:
            b += 1                                  # a player who left does not deal any more
        reach = state["games"] if b >= len(sched) else b // n0
        for _ in range(state["block"] // n0, reach):
            _close_game(state)
        if b >= len(sched):
            return _finish(state, now, f"All {state['games']} game{'s' if state['games'] != 1 else ''} played")
        state["block"], state["rb"] = b, 1
    _begin_round(state, now)


def _begin_round(state, now):
    state["round_no"] += 1
    shuffled = False
    if len(state["shoe"]) < CUT_AT:
        state["shoe"] = new_shoe()
        state["reshuffles"] += 1
        shuffled = True
        _log(state, None, "shuffle")
    dealer = state["sched"][state["block"]]
    state["rnd"] = {"no": state["round_no"], "dealer": dealer, "order": _seat_order(state, dealer), "hands": {},
                    "dealer_cards": [], "hole_up": False, "ins": {}, "ins_amt": {}, "bets": {}, "sit": [],
                    "turn": None, "dbj": False, "shuffled": shuffled, "done": False}
    state["phase"] = "deal"                             # "deal" = bets, then the dealer presses Deal
    state["deadline"] = now + DEAL_MS
    _log(state, dealer, "round", state["rb"])


def _close_game(state):
    """A game ends when every player has dealt 5 rounds. Whoever holds the most chips wins that game; then
    everybody gets a fresh stack for the next one."""
    live = [u for u in state["players"] if u not in state["left"]]
    net = {k: v - START_CHIPS for k, v in state["chips"].items()}
    best = max((net[str(u)] for u in live), default=0)
    top = [u for u in live if net[str(u)] == best]
    state["gdone"].append({"no": len(state["gdone"]) + 1, "net": net, "winner": top[0] if len(top) == 1 else None})
    for k, v in net.items():
        state["scores"][k] += v
    state["chips"] = {k: START_CHIPS for k in state["chips"]}


def _finish(state, now, reason, early=False):
    _void_round(state)
    state["phase"] = "over"
    state["deadline"] = 0
    live = _active(state)
    rows = []
    for u in state["players"]:
        k = str(u)
        rows.append({"id": u, "score": state["scores"][k] + state["chips"][k] - START_CHIPS, "wins": sum(1 for g in state["gdone"] if g["winner"] == u),
                     "left": u in state["left"]})
    pool = [r for r in rows if not r["left"]]
    pool.sort(key=lambda r: (r["wins"], r["score"]), reverse=True)
    winner = None
    if len(live) == 1:
        winner = live[0]                          # everybody else walked away
    elif pool and (len(pool) == 1 or (pool[0]["wins"], pool[0]["score"]) != (pool[1]["wins"], pool[1]["score"])):
        winner = pool[0]["id"]
    rows.sort(key=lambda r: (r["left"], -r["wins"], -r["score"]))
    state["summary"] = rows
    state["winner"] = winner
    state["reason"] = reason
    _log(state, None, "over")


# ---------------------------------------------------------------------------------------------
# Dealing and the round
# ---------------------------------------------------------------------------------------------
def _deal(state, now):
    """Deal to everybody who bet. Anybody who has not bet sits this round out."""
    rnd = state["rnd"]
    order = [u for u in rnd["order"] if rnd["bets"].get(str(u), 0) > 0]
    rnd["in"] = order
    for u in order:
        rnd["hands"][str(u)] = [_new_hand(rnd["bets"][str(u)])]
    for _ in range(2):                                  # one card at a time, round the table, dealer last
        for u in order:
            rnd["hands"][str(u)][0]["cards"].append(_draw(state))
        rnd["dealer_cards"].append(_draw(state))
    _log(state, rnd["dealer"], "deal")
    up = rnd["dealer_cards"][0]
    if rank(up) == 1:                                   # an Ace: offer insurance, then peek
        rnd["ins"] = {str(u): (False if u in state["left"] else None) for u in order}
        state["phase"] = "insurance"
        state["deadline"] = now + INSURANCE_MS
        _check_insurance(state, now)
    elif value(up) == 10:
        _peek(state, now)
    else:
        _start_play(state, now)


def _check_insurance(state, now):
    rnd = state["rnd"]
    if all(v is not None for v in rnd["ins"].values()):
        _peek(state, now)


def _peek(state, now):
    """The dealer checks the hole card when showing an Ace or a ten-value card."""
    rnd = state["rnd"]
    if is_natural(rnd["dealer_cards"]):
        rnd["dbj"] = True
        rnd["hole_up"] = True
        _log(state, rnd["dealer"], "dealer_bj")
        return _settle_round(state, now)
    if any(rnd["ins"].values()):
        _log(state, rnd["dealer"], "no_bj")
    _start_play(state, now)


def _start_play(state, now):
    rnd = state["rnd"]
    for hs in rnd["hands"].values():
        for h in hs:
            if hand_natural(h):
                h["done"] = True                        # blackjack: paid 3 to 2, no decisions to make
    _next_actor(state, now)


def _next_actor(state, now):
    """Find whose decision is next, in seat order and hand order; deal the second card to a hand that waits for one."""
    rnd = state["rnd"]
    for u in rnd["order"]:
        hs = rnd["hands"].get(str(u), [])
        for i, h in enumerate(hs):
            if h["done"]:
                continue
            if len(h["cards"]) == 1:                    # the later hand of a split gets its card only now
                h["cards"].append(_draw(state))
                if h["ace"] or count(h["cards"])[0] >= 21:
                    h["done"] = True
                    continue
            if u in state["left"]:
                h["done"] = True                        # somebody who left stands on what they have
                continue
            rnd["turn"] = {"u": u, "h": i}
            state["phase"] = "play"
            state["deadline"] = now + TURN_MS
            return
    rnd["turn"] = None
    _dealer_phase(state, now)


def _cur(state):
    t = state["rnd"]["turn"]
    return state["rnd"]["hands"][str(t["u"])][t["h"]]


def _dealer_phase(state, now):
    rnd = state["rnd"]
    rnd["hole_up"] = True
    live = [h for hs in rnd["hands"].values() for h in hs if not hand_bust(h) and not hand_natural(h)]
    if not live:                                        # nobody left to beat: the dealer does not draw
        return _settle_round(state, now)
    _dealer_step(state, now)


def _dealer_step(state, now):
    rnd = state["rnd"]
    if count(rnd["dealer_cards"])[0] >= 17:             # stands on every 17, soft or hard
        return _settle_round(state, now)
    state["phase"] = "dealer"
    state["deadline"] = now + DRAW_MS


def _dealer_draw(state, now):
    rnd = state["rnd"]
    rnd["dealer_cards"].append(_draw(state))
    _log(state, rnd["dealer"], "draw")
    _dealer_step(state, now)


# ---------------------------------------------------------------------------------------------
# Settling
# ---------------------------------------------------------------------------------------------
def hand_net(h, dealer_best, dealer_bust, dealer_bj):
    """Chips won (+) or lost (-) on one hand, and a word for it. A blackjack pays 3 to 2 (stakes are even)."""
    best = count(h["cards"])[0]
    stake = h["stake"]
    if best > 21:
        return -stake, "bust"
    natural = hand_natural(h)
    if dealer_bj:
        return (0, "push") if natural else (-stake, "lose")
    if natural:
        return stake * 3 // 2, "blackjack"
    if dealer_bust or best > dealer_best:
        return stake, "win"
    if best < dealer_best:
        return -stake, "lose"
    return 0, "push"


def _settle_round(state, now):
    """Pay the table. Each hand's stake goes back to its owner with the winnings (or is kept by the bank when the
    hand lost); the bank's holdings change by the opposite of what the players made."""
    rnd = state["rnd"]
    dcards = rnd["dealer_cards"]
    dbest = count(dcards)[0]
    dbust, dbj = dbest > 21, rnd["dbj"]
    rows, total = [], 0
    for u in rnd["in"]:
        k = str(u)
        hands, net = [], 0
        for h in rnd["hands"][k]:
            n, tag = hand_net(h, dbest, dbust, dbj)
            net += n
            state["chips"][k] += h["stake"] + n                 # stake back, plus the win (a loss leaves nothing)
            hands.append({"cards": [pair(c) for c in h["cards"]], "text": total_text(h["cards"]), "stake": h["stake"],
                          "net": n, "tag": tag, "doubled": h["doubled"]})
        ins = None
        if rnd["ins_amt"].get(k):
            amt = rnd["ins_amt"][k]
            ins = amt * 2 if dbj else -amt                      # insurance pays 2 to 1
            if dbj:
                state["chips"][k] += amt * 3
            net += ins
        rows.append({"id": u, "hands": hands, "ins": ins, "net": net})
        total += net
    dealer_net = -total
    state["chips"][str(rnd["dealer"])] += dealer_net
    rnd["done"] = True
    state["last"] = {"round": rnd["no"], "dealer": rnd["dealer"], "dealer_cards": [pair(c) for c in dcards],
                     "dealer_text": total_text(dcards), "dealer_bj": dbj, "dealer_bust": dbust, "rows": rows,
                     "dealer_net": dealer_net}
    state["phase"] = "result"
    state["deadline"] = now + RESULT_MS
    _log(state, rnd["dealer"], "settle", dealer_net)


# ---------------------------------------------------------------------------------------------
# Players leaving, and things that happen by themselves
# ---------------------------------------------------------------------------------------------
def _leave(state, uid, now):
    if uid in state["left"] or state["phase"] == "over" or uid not in state["players"]:
        return
    state["left"].append(uid)
    _log(state, uid, "leave")
    if len(_active(state)) < 2:
        return _finish(state, now, "Everyone else left the table", early=True)
    _pump(state, now)


def _skip_round(state, now):
    """Nobody can or wants to bet: the round is not played (it still counts towards the dealer's five)."""
    _log(state, state["rnd"]["dealer"], "no_bets")
    state["rnd"]["done"] = True
    _next_round(state, now)


def _pump(state, now):
    """Play on for anybody who has left the table: they sit out, stand, decline insurance, and a dealer who left
    deals and draws without waiting. Also skips a round in which nobody is betting."""
    for _ in range(400):
        ph = state["phase"]
        if ph == "over":
            return
        rnd = state["rnd"]
        left = state["left"]
        if ph == "deal":
            for u in _undecided(state):
                if u in left:
                    rnd["sit"].append(u)
            if _undecided(state):
                return                                  # still waiting for somebody's bet
            if not rnd["bets"]:
                _skip_round(state, now)
            elif rnd["dealer"] in left:
                _deal(state, now)
            else:
                return                                  # the dealer presses Deal
        elif ph == "insurance":
            gone = [k for k, v in rnd["ins"].items() if v is None and int(k) in left]
            if not gone:
                return
            for k in gone:
                rnd["ins"][k] = False
            _check_insurance(state, now)
        elif ph == "play" and rnd["turn"]["u"] in left:
            _cur(state)["done"] = True
            _next_actor(state, now)
        elif ph == "dealer" and rnd["dealer"] in left:
            _dealer_draw(state, now)
        else:
            return


def _miss(state, uid, now):
    """A turn timer ran out. Returns True when this player has now been taken off the table."""
    k = str(uid)
    state["timeouts"][k] = state["timeouts"].get(k, 0) + 1
    _log(state, uid, "timeout")
    return state["timeouts"][k] >= MAX_TIMEOUTS


def settle(state, now):
    """Deal with a timer that has run out. Returns True if the state changed."""
    changed = False
    for _ in range(40):
        if state["phase"] == "over" or not state["deadline"] or now < state["deadline"]:
            break
        changed = True
        ph, rnd = state["phase"], state["rnd"]
        if ph == "result":
            _next_round(state, now)
            _pump(state, now)
            continue
        if ph == "insurance":
            for k, v in rnd["ins"].items():
                if v is None:
                    rnd["ins"][k] = False
            _check_insurance(state, now)
            _pump(state, now)
            continue
        if ph == "deal":
            # Time is up for betting: whoever has not decided sits out (and misses a turn); then the cards go out.
            und = _undecided(state)
            gone = []
            for u in und:
                rnd["sit"].append(u)
                if _miss(state, u, now):
                    gone.append(u)
            if rnd["bets"]:
                if not und and rnd["dealer"] not in state["left"] and _miss(state, rnd["dealer"], now):
                    gone.append(rnd["dealer"])          # everyone was ready and the dealer sat on the button
                _deal(state, now)
            else:
                _skip_round(state, now)
            for u in gone:
                _leave(state, u, now)
            _pump(state, now)
            continue
        elif ph == "play":
            who = rnd["turn"]["u"]
            gone = _miss(state, who, now)
            _cur(state)["done"] = True
            _log(state, who, "stand")
            _next_actor(state, now)
        else:                                           # the dealer must draw
            who = rnd["dealer"]
            gone = _miss(state, who, now)
            _dealer_draw(state, now)
        if gone:
            _leave(state, who, now)
        _pump(state, now)
    return changed


# ---------------------------------------------------------------------------------------------
# What a player may do
# ---------------------------------------------------------------------------------------------
def can_double(h):
    return len(h["cards"]) == 2 and not h["done"]


def can_split(state, uid, h):
    hands = state["rnd"]["hands"][str(uid)]
    return (len(h["cards"]) == 2 and not h["done"] and len(hands) < MAX_HANDS
            and value(h["cards"][0]) == value(h["cards"][1]) and not h["ace"])


def _afford(state, uid, extra):
    """None if the player can put `extra` more chips on the table and the bank can cover the bigger hand,
    otherwise the reason why not."""
    if state["chips"][str(uid)] < extra:
        return "Not enough chips"
    if _free_bank(state) < extra:
        return "The bank cannot cover it"
    return None


def insurance_cost(state, uid):
    return state["rnd"]["hands"][str(uid)][0]["stake"] // 2


def options(state, uid):
    ph, rnd = state["phase"], state["rnd"]
    if ph == "over" or uid in state["left"] or uid not in state["players"]:
        return []
    k = str(uid)
    out = []
    if ph == "deal":
        if rnd["dealer"] == uid:
            if rnd["bets"]:
                out.append({"a": "deal"})
        elif uid in _eligible(state):
            out += [{"a": "bet", "min": MIN_BET, "max": bet_cap(state, uid), "cur": rnd["bets"].get(k, 0),
                     "last": state["lastbet"].get(k, 0)}, {"a": "sit"}]
    elif ph == "insurance" and rnd["ins"].get(k, True) is None:
        cost = insurance_cost(state, uid)
        yes = {"a": "ins:yes", "cost": cost}
        if state["chips"][k] < cost:
            yes["off"] = "Not enough chips"
        out += [yes, {"a": "ins:no"}]
    elif ph == "play" and rnd["turn"]["u"] == uid:
        h = _cur(state)
        out += [{"a": "hit"}, {"a": "stand"}]
        if can_double(h):
            d = {"a": "double", "cost": h["stake"]}
            why = _afford(state, uid, h["stake"])
            if why:
                d["off"] = why
            out.append(d)
        if can_split(state, uid, h):
            sp = {"a": "split", "cost": h["stake"]}
            why = _afford(state, uid, h["stake"])
            if why:
                sp["off"] = why
            out.append(sp)
    elif ph == "dealer" and rnd["dealer"] == uid:
        out.append({"a": "draw"})
    elif ph == "result" and rnd["dealer"] == uid:
        out.append({"a": "next"})
    return out


def act(state, uid, action, now):
    """Do something. Returns an error sentence, or None when it worked."""
    ph = state["phase"]
    if ph == "over":
        return "This table is closed."
    if uid not in state["players"] or uid in state["left"]:
        return "You are not at this table."
    if action == "leave":
        _leave(state, uid, now)
        return None
    rnd = state["rnd"]
    k = str(uid)
    if action == "deal":
        if rnd["dealer"] != uid:
            return "Only the dealer deals."
        if ph != "deal":
            return "The cards are already dealt."
        if not rnd["bets"]:
            return "Wait for at least one bet before dealing."
        _deal(state, now)
    elif action.startswith("bet:") or action == "sit":
        if ph != "deal":
            return "Bets are closed for this round."
        if rnd["dealer"] == uid:
            return "The dealer is the bank and does not bet."
        if uid not in _eligible(state):
            return "You cannot bet this round (not enough chips, or the bank is too small)."
        old = rnd["bets"].pop(k, 0)
        state["chips"][k] += old                        # take the old bet back before deciding
        if action == "sit":
            if uid not in rnd["sit"]:
                rnd["sit"].append(uid)
            _log(state, uid, "sit")
        else:
            try:
                amt = int(action[4:])
            except ValueError:
                amt = -1
            cap = bet_cap(state, uid)
            if amt < MIN_BET or amt % 2 or amt > cap:
                if old:
                    state["chips"][k] -= old            # keep the old bet
                    rnd["bets"][k] = old
                return f"Bet an even number of chips from {MIN_BET} to {cap}."
            state["chips"][k] -= amt
            rnd["bets"][k] = amt
            state["lastbet"][k] = amt
            if uid in rnd["sit"]:
                rnd["sit"].remove(uid)
            _log(state, uid, "bet", amt)
    elif action in ("ins:yes", "ins:no"):
        if ph != "insurance" or rnd["ins"].get(k, True) is not None:
            return "Insurance is not on offer."
        if action == "ins:yes":
            cost = insurance_cost(state, uid)
            if state["chips"][k] < cost:
                return "You do not have enough chips for insurance."
            state["chips"][k] -= cost
            rnd["ins_amt"][k] = cost
        rnd["ins"][k] = action == "ins:yes"
        _log(state, uid, "insure" if rnd["ins"][k] else "no_insure")
        _check_insurance(state, now)
    elif action in ("hit", "stand", "double", "split"):
        if ph != "play" or rnd["turn"]["u"] != uid:
            return "Wait for your turn."
        h = _cur(state)
        if action == "stand":
            h["done"] = True
            _log(state, uid, "stand")
            _next_actor(state, now)
        elif action == "hit":
            h["cards"].append(_draw(state))
            best = count(h["cards"])[0]
            _log(state, uid, "bust" if best > 21 else "hit")
            if best >= 21:
                h["done"] = True                        # a bust ends the hand; 21 stands by itself
                _next_actor(state, now)
            else:
                state["deadline"] = now + TURN_MS
        elif action == "double":
            if not can_double(h):
                return "You can only double on your first two cards."
            why = _afford(state, uid, h["stake"])
            if why:
                return why + " to double."
            state["chips"][k] -= h["stake"]
            h["stake"] *= 2
            h["doubled"] = True
            h["cards"].append(_draw(state))
            h["done"] = True
            _log(state, uid, "double")
            _next_actor(state, now)
        else:
            if not can_split(state, uid, h):
                return "You can only split two cards of equal value (up to %d hands)." % MAX_HANDS
            why = _afford(state, uid, h["stake"])
            if why:
                return why + " to split."
            state["chips"][k] -= h["stake"]
            ace = rank(h["cards"][0]) == 1
            second = _new_hand(h["stake"])
            second["cards"] = [h["cards"][1]]
            second["split"] = h["split"] = True
            second["ace"] = h["ace"] = ace
            h["cards"] = [h["cards"][0]]
            h["cards"].append(_draw(state))
            if ace or count(h["cards"])[0] >= 21:       # split Aces get one card each; 21 stands
                h["done"] = True
            state["rnd"]["hands"][k].insert(rnd["turn"]["h"] + 1, second)
            _log(state, uid, "split")
            _next_actor(state, now)
    elif action == "draw":
        if rnd["dealer"] != uid:
            return "Only the dealer draws."
        if ph != "dealer":
            return "The dealer does not need a card."
        _dealer_draw(state, now)
    elif action == "next":
        if rnd["dealer"] != uid:
            return "Only the dealer opens the next round."
        if ph != "result":
            return "The round is not over yet."
        _next_round(state, now)
    else:
        return "Unknown action."
    state["timeouts"][k] = 0
    _pump(state, now)
    return None


# ---------------------------------------------------------------------------------------------
# What leaves the server
# ---------------------------------------------------------------------------------------------
def info(state):
    """The same shape the other games return: who is up next, and whether the table is over."""
    ph, rnd = state["phase"], state["rnd"]
    if ph == "over":
        nxt = state["winner"] or state["players"][0]
    elif ph == "play":
        nxt = rnd["turn"]["u"]
    elif ph == "insurance":
        nxt = next((int(k) for k, v in rnd["ins"].items() if v is None), rnd["dealer"])
    else:
        nxt = rnd["dealer"]
    return {"next": nxt, "winner": state["winner"], "finished": ph == "over", "reason": state["reason"],
            "nudge": ph in ("deal", "play", "dealer", "insurance")}


def owes_bet(state, uid):
    """True while this player still has to bet or sit out the round."""
    return state["phase"] == "deal" and uid in _undecided(state)


def _hand_view(state, u, i, h):
    rnd = state["rnd"]
    best, soft = count(h["cards"])
    t = rnd["turn"]
    on = state["phase"] == "play" and t and t["u"] == u and t["h"] == i
    if hand_bust(h):
        st = "bust"
    elif hand_natural(h):
        st = "bj"
    elif on:
        st = "turn"
    elif h["done"]:
        st = "21" if best == 21 else "stand"
    else:
        st = "wait"
    return {"cards": [pair(c) for c in h["cards"]], "best": best, "text": total_text(h["cards"]), "st": st,
            "stake": h["stake"], "doubled": h["doubled"], "split": h["split"]}


def public_view(state, uid, now):
    rnd, ph = state["rnd"], state["phase"]
    players = []
    for u in state["players"]:
        k = str(u)
        hold = holdings(state, u)
        row = {"id": u, "left": u in state["left"], "chips": state["chips"][k], "in_play": in_play(state, u),
               "hold": hold, "gnet": hold - START_CHIPS, "score": state["scores"][k] + hold - START_CHIPS,
               "wins": sum(1 for g in state["gdone"] if g["winner"] == u), "dealer": bool(rnd) and rnd["dealer"] == u,
               "ins_amt": rnd["ins_amt"].get(k, 0) if rnd and not rnd.get("done") else 0}
        if rnd and rnd["dealer"] != u and ph != "over":
            if k in rnd["hands"]:
                row["bst"] = "in"
            elif ph == "deal":
                row["bst"] = ("bet" if k in rnd["bets"] else "sit" if u in rnd["sit"]
                              else "wait" if u in _eligible(state) else "out")
            else:
                row["bst"] = "sit"
            row["bet"] = sum(h["stake"] for h in rnd["hands"][k]) if k in rnd["hands"] else rnd["bets"].get(k, 0)
        if rnd and k in rnd["hands"]:
            row["hands"] = [_hand_view(state, u, i, h) for i, h in enumerate(rnd["hands"][k])]
        if rnd and k in rnd["ins"]:
            v = rnd["ins"][k]
            row["ins"] = ("pending" if v is None else "answered") if ph == "insurance" else ("yes" if v else "no")
        players.append(row)
    dealer = None
    if rnd:
        dc = rnd["dealer_cards"]
        mine = rnd["dealer"] == uid
        shown = dc if (rnd["hole_up"] or mine) else dc[:1]
        cards = [pair(c) for c in dc[:1]]
        if len(dc) > 1:
            cards.append(pair(dc[1]) if (rnd["hole_up"] or mine) else None)
        cards += [pair(c) for c in dc[2:]]
        dealer = {"id": rnd["dealer"], "cards": cards, "text": total_text(shown) if shown else "",
                  "hole_hidden": len(dc) > 1 and not rnd["hole_up"], "peek": bool(len(dc) > 1 and mine and not rnd["hole_up"]),
                  "bj": bool(rnd["hole_up"] and is_natural(dc))}
    total_ms = {"deal": DEAL_MS, "insurance": INSURANCE_MS, "play": TURN_MS, "dealer": DRAW_MS, "result": RESULT_MS}.get(ph, 0)
    cur = None
    if rnd and ph == "play" and rnd["turn"]:
        cur = rnd["turn"]
    out = {"phase": ph, "games": state["games"], "game": min(game_no(state), state["games"]), "rpd": state["rpd"],
           "rb": state["rb"], "round_no": state["round_no"], "dealer": dealer, "dealer_id": rnd["dealer"] if rnd else None,
           "players": players, "order": list(rnd["order"]) if rnd else [], "options": options(state, uid), "turn": cur,
           "deadline_in": max(0, state["deadline"] - now) if state["deadline"] else 0, "total_ms": total_ms,
           "log": state["log"][-10:], "last": state["last"], "gdone": state["gdone"], "summary": state["summary"],
           "shoe": len(state["shoe"]), "shuffled": bool(rnd and rnd.get("shuffled")), "n": state["n"],
           "start": START_CHIPS, "min_bet": MIN_BET, "max_hands": MAX_HANDS, "winner": state["winner"],
           "bank": state["chips"][str(rnd["dealer"])] if rnd else 0, "table_max": table_max(state) if rnd else 0,
           "round_done": bool(rnd and rnd.get("done"))}
    return out
