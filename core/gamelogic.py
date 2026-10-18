"""Rules for the board games. Pure Python (no Django), so it can be tested on its own.

Every game keeps its whole situation in one JSON-friendly `state` dict. The functions here change that dict
and return a small `info` dict:  {"error": str}  when the move is refused, otherwise
{"next": user_id_whose_turn_it_is, "winner": user_id or None, "finished": bool, "reason": str}.
"""
import secrets

from . import chess_engine as chess

GAMES = ("tic_tac_toe", "connect_four", "snakes_ladders", "ludo", "chess")
TWO_PLAYER = ("tic_tac_toe", "connect_four", "chess")
PARTY = ("snakes_ladders", "ludo")          # 2-4 players


def roll_die():
    return secrets.randbelow(6) + 1


def _ok(next_id, winner=None, finished=False, reason=""):
    return {"next": next_id, "winner": winner, "finished": finished, "reason": reason}


def _err(text):
    return {"error": text}


def next_in_order(players, user_id):
    return players[(players.index(user_id) + 1) % len(players)]


# =============================================================================================
# Tic-tac-toe
# =============================================================================================
TTT_LINES = ((0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6), (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6))


def ttt_new(players):
    return {"board": [""] * 9, "symbols": {str(players[0]): "X", str(players[1]): "O"}, "win": [], "last": None}


def ttt_winner(board):
    """-> (symbol or "draw" or "", winning cells)"""
    for line in TTT_LINES:
        a, b, c = line
        if board[a] and board[a] == board[b] == board[c]:
            return board[a], list(line)
    return ("draw", []) if all(board) else ("", [])


def ttt_move(state, uid, cell):
    try:
        cell = int(cell)
    except (TypeError, ValueError):
        return _err("Tap a square.")
    if not 0 <= cell < 9 or state["board"][cell]:
        return _err("That square is taken.")
    symbol = state["symbols"][str(uid)]
    state["board"][cell] = symbol
    state["last"] = cell
    result, line = ttt_winner(state["board"])
    state["win"] = line
    if result == "draw":
        return _ok(uid, None, True, "draw")
    if result:
        return _ok(uid, uid, True, "three in a row")
    return _ok(next_in_order(state["players"], uid))


# =============================================================================================
# Connect Four
# =============================================================================================
def c4_new(players):
    return {"board": [[""] * 7 for _ in range(6)], "symbols": {str(players[0]): "R", str(players[1]): "B"},
            "win": [], "last": None}


def c4_find_win(board, row, col):
    token = board[row][col]
    for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
        cells = [[row, col]]
        for direction in (-1, 1):
            r, c = row + dr * direction, col + dc * direction
            while 0 <= r < 6 and 0 <= c < 7 and board[r][c] == token:
                cells.append([r, c]); r += dr * direction; c += dc * direction
        if len(cells) >= 4:
            return cells
    return []


def c4_move(state, uid, column):
    try:
        column = int(column)
    except (TypeError, ValueError):
        return _err("Choose a column.")
    if not 0 <= column < 7:
        return _err("Choose a column.")
    row = next((r for r in range(5, -1, -1) if not state["board"][r][column]), None)
    if row is None:
        return _err("That column is full.")
    state["board"][row][column] = state["symbols"][str(uid)]
    state["last"] = [row, column]
    win = c4_find_win(state["board"], row, column)
    state["win"] = win
    if win:
        return _ok(uid, uid, True, "four in a row")
    if all(state["board"][0]):
        return _ok(uid, None, True, "draw")
    return _ok(next_in_order(state["players"], uid))


# =============================================================================================
# Snakes and Ladders (2-4 players)
# =============================================================================================
SNAKES = {"99": 78, "95": 75, "92": 88, "64": 60, "62": 19, "54": 34, "17": 7}
LADDERS = {"4": 14, "9": 31, "20": 38, "28": 84, "40": 59, "51": 67, "63": 81, "71": 91}


def snakes_new(players):
    return {"positions": {str(p): 0 for p in players}, "snakes": dict(SNAKES), "ladders": dict(LADDERS),
            "symbols": {str(p): str(i) for i, p in enumerate(players)}, "last": None}


def snakes_roll(state, uid, rng=roll_die):
    roll = rng()
    old = state["positions"][str(uid)]
    target = old + roll
    bounced = target > 100                      # you need the exact number to land on 100
    landed = old if bounced else target
    kind = "ladder" if str(landed) in state["ladders"] else "snake" if str(landed) in state["snakes"] else ""
    final = state["ladders"].get(str(landed), state["snakes"].get(str(landed), landed))
    state["positions"][str(uid)] = final
    state["last"] = {"uid": uid, "roll": roll, "from": old, "landed": landed, "to": final, "type": kind,
                     "bounced": bounced}
    if final == 100:
        return _ok(uid, uid, True, "reached 100")
    if roll == 6:
        return _ok(uid)                         # a six earns another roll
    return _ok(next_in_order(state["players"], uid))


# =============================================================================================
# Ludo (2-4 players)
#
# Every token has a "progress" number:  -1 = still in the yard, 0..50 = on the shared 52-square track
# (0 is your own start square), 51..55 = your private home column, 56 = home (finished).
# =============================================================================================
LUDO_START = (0, 13, 26, 39)                  # where each colour enters the 52-square loop
LUDO_SAFE = {0, 13, 26, 39, 8, 21, 34, 47}    # start squares and the star squares
LUDO_COLOR_NAMES = ("red", "green", "yellow", "blue")
LUDO_SEATS = {2: (0, 2), 3: (0, 1, 2), 4: (0, 1, 2, 3)}   # two players sit opposite each other
LUDO_HOME = 56


def ludo_new(players):
    seats = LUDO_SEATS[len(players)]
    return {"colors": {str(p): seats[i] for i, p in enumerate(players)},
            "tokens": {str(p): [-1, -1, -1, -1] for p in players},
            "dice": None, "sixes": 0, "last": None, "symbols": {str(p): LUDO_COLOR_NAMES[seats[i]] for i, p in enumerate(players)}}


def ludo_square(color, progress):
    """Absolute square (0..51) on the shared loop for progress 0..50, else None."""
    if 0 <= progress <= 50:
        return (LUDO_START[color] + progress) % 52
    return None


def ludo_movable(state, uid, dice):
    out = []
    for i, p in enumerate(state["tokens"][str(uid)]):
        if p == -1:
            if dice == 6:
                out.append(i)
        elif p < LUDO_HOME and p + dice <= LUDO_HOME:
            out.append(i)
    return out


def ludo_apply(state, uid, token, dice):
    """Move one token. Returns (captured list, finished_token_bool)."""
    toks = state["tokens"][str(uid)]
    frm = toks[token]
    to = 0 if frm == -1 else frm + dice
    toks[token] = to
    captured = []
    sq = ludo_square(state["colors"][str(uid)], to)
    if sq is not None and sq not in LUDO_SAFE:
        for other, otoks in state["tokens"].items():
            if other == str(uid):
                continue
            ocolor = state["colors"][other]
            for j, op in enumerate(otoks):
                if ludo_square(ocolor, op) == sq:
                    otoks[j] = -1
                    captured.append({"uid": int(other), "token": j})
    state["last"] = {"uid": uid, "roll": dice, "token": token, "from": frm, "to": to, "captured": captured}
    return captured, to == LUDO_HOME


def ludo_after_move(state, uid, dice, captured, finished_token):
    toks = state["tokens"][str(uid)]
    state["dice"] = None
    if all(p == LUDO_HOME for p in toks):
        state["sixes"] = 0
        return _ok(uid, uid, True, "all four tokens home")
    if dice == 6 or captured or finished_token:       # six, capture or arrival = roll again
        if dice != 6:
            state["sixes"] = 0
        return _ok(uid)
    state["sixes"] = 0
    return _ok(next_in_order(state["players"], uid))


def ludo_roll(state, uid, rng=roll_die):
    if state["dice"] is not None:
        return _err("Move a token first.")
    dice = rng()
    state["sixes"] = state["sixes"] + 1 if dice == 6 else 0
    if state["sixes"] >= 3:                          # three sixes in a row: the turn is lost
        state["sixes"] = 0
        state["last"] = {"uid": uid, "roll": 6, "token": None, "from": None, "to": None, "captured": [], "note": "three sixes"}
        return _ok(next_in_order(state["players"], uid))
    movable = ludo_movable(state, uid, dice)
    if not movable:
        state["sixes"] = 0
        state["last"] = {"uid": uid, "roll": dice, "token": None, "from": None, "to": None, "captured": [], "note": "no move"}
        return _ok(next_in_order(state["players"], uid))
    distinct = {}                                     # tokens on the same spot are the same choice
    for i in movable:
        distinct.setdefault(state["tokens"][str(uid)][i], i)
    if len(distinct) == 1:                            # only one real choice: just play it
        captured, fin = ludo_apply(state, uid, next(iter(distinct.values())), dice)
        return ludo_after_move(state, uid, dice, captured, fin)
    state["dice"] = dice
    state["last"] = {"uid": uid, "roll": dice, "token": None, "from": None, "to": None, "captured": [], "note": "choose"}
    return _ok(uid)


def ludo_move(state, uid, token):
    dice = state["dice"]
    if dice is None:
        return _err("Roll the dice first.")
    try:
        token = int(token)
    except (TypeError, ValueError):
        return _err("Tap one of your glowing tokens.")
    if token not in ludo_movable(state, uid, dice):
        return _err("That token cannot move.")
    captured, fin = ludo_apply(state, uid, token, dice)
    return ludo_after_move(state, uid, dice, captured, fin)


# =============================================================================================
# Starting a game
# =============================================================================================
def new_state(game, players, time_control_minutes=0):
    """`players` is the ordered list of user ids. Returns (state, first_player_id)."""
    if game == "tic_tac_toe":
        state = ttt_new(players)
    elif game == "connect_four":
        state = c4_new(players)
    elif game == "snakes_ladders":
        state = snakes_new(players)
    elif game == "ludo":
        state = ludo_new(players)
    elif game == "chess":
        order = list(players)
        if secrets.randbelow(2):
            order.reverse()
        state = chess.new_game(order[0], order[1], time_control_minutes, 0)
        state["symbols"] = {str(order[0]): "white", str(order[1]): "black"}
        state["players"] = list(players)
        return state, order[0]
    else:
        raise ValueError("unknown game")
    state["players"] = list(players)
    return state, players[0]
