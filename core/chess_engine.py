"""A small, complete chess rules engine (no dependencies).

Squares are numbered 0..63: a1=0, b1=1 ... h1=7, a2=8 ... h8=63.
Pieces are letters: PNBRQK for white, pnbrqk for black, "" for an empty square.
A move is a tuple (from_square, to_square, promotion) where promotion is "" or one of "qrbn".

Implements every rule that matters for an online game: castling, en passant, promotion, check,
checkmate, stalemate, insufficient material, the 50-move rule and threefold repetition.
"""

FILES = "abcdefgh"
START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"

KNIGHT_STEPS = ((1, 2), (2, 1), (2, -1), (1, -2), (-1, -2), (-2, -1), (-2, 1), (-1, 2))
KING_STEPS = ((1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1))
BISHOP_DIRS = ((1, 1), (1, -1), (-1, 1), (-1, -1))
ROOK_DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1))


def sq_name(s):
    return FILES[s % 8] + str(s // 8 + 1)


def name_sq(n):
    if len(n) != 2 or n[0] not in FILES or n[1] not in "12345678":
        raise ValueError("bad square")
    return (int(n[1]) - 1) * 8 + FILES.index(n[0])


def is_white(piece):
    return piece.isupper()


class Pos:
    __slots__ = ("b", "turn", "castle", "ep", "half", "full")

    def __init__(self, b, turn, castle, ep, half, full):
        self.b, self.turn, self.castle, self.ep, self.half, self.full = b, turn, castle, ep, half, full

    def copy(self):
        return Pos(self.b[:], self.turn, self.castle, self.ep, self.half, self.full)

    def fen(self):
        rows = []
        for rank in range(7, -1, -1):
            row, empty = "", 0
            for f in range(8):
                p = self.b[rank * 8 + f]
                if p:
                    if empty:
                        row += str(empty); empty = 0
                    row += p
                else:
                    empty += 1
            if empty:
                row += str(empty)
            rows.append(row)
        ep = sq_name(self.ep) if self.ep >= 0 else "-"
        return f"{'/'.join(rows)} {self.turn} {self.castle or '-'} {ep} {self.half} {self.full}"

    def key(self):
        """What makes two positions 'the same' for repetition."""
        return " ".join(self.fen().split(" ")[:4])


def parse_fen(fen):
    parts = fen.split()
    board = [""] * 64
    rank = 7
    for row in parts[0].split("/"):
        f = 0
        for ch in row:
            if ch.isdigit():
                f += int(ch)
            else:
                board[rank * 8 + f] = ch; f += 1
        rank -= 1
    castle = "" if parts[2] == "-" else parts[2]
    ep = -1 if parts[3] == "-" else name_sq(parts[3])
    return Pos(board, parts[1], castle, ep, int(parts[4]), int(parts[5]))


def start_pos():
    return parse_fen(START_FEN)


# ---- attacks ----------------------------------------------------------------------------------
def attacked(b, sq, by_white):
    """Is `sq` attacked by any piece of the given colour?"""
    f, r = sq % 8, sq // 8
    pawn = "P" if by_white else "p"
    pr = r - 1 if by_white else r + 1          # a white pawn attacks upward, so it sits one rank below sq
    if 0 <= pr < 8:
        for pf in (f - 1, f + 1):
            if 0 <= pf < 8 and b[pr * 8 + pf] == pawn:
                return True
    knight = "N" if by_white else "n"
    for df, dr in KNIGHT_STEPS:
        nf, nr = f + df, r + dr
        if 0 <= nf < 8 and 0 <= nr < 8 and b[nr * 8 + nf] == knight:
            return True
    king = "K" if by_white else "k"
    for df, dr in KING_STEPS:
        nf, nr = f + df, r + dr
        if 0 <= nf < 8 and 0 <= nr < 8 and b[nr * 8 + nf] == king:
            return True
    bq = ("B", "Q") if by_white else ("b", "q")
    for df, dr in BISHOP_DIRS:
        nf, nr = f + df, r + dr
        while 0 <= nf < 8 and 0 <= nr < 8:
            p = b[nr * 8 + nf]
            if p:
                if p in bq:
                    return True
                break
            nf += df; nr += dr
    rq = ("R", "Q") if by_white else ("r", "q")
    for df, dr in ROOK_DIRS:
        nf, nr = f + df, r + dr
        while 0 <= nf < 8 and 0 <= nr < 8:
            p = b[nr * 8 + nf]
            if p:
                if p in rq:
                    return True
                break
            nf += df; nr += dr
    return False


def king_square(b, white):
    k = "K" if white else "k"
    return b.index(k) if k in b else -1


def in_check(pos, white=None):
    white = (pos.turn == "w") if white is None else white
    ks = king_square(pos.b, white)
    return ks >= 0 and attacked(pos.b, ks, not white)


# ---- move generation ----------------------------------------------------------------------------
def pseudo_moves(pos):
    b, white = pos.b, pos.turn == "w"
    out = []
    for frm in range(64):
        p = b[frm]
        if not p or is_white(p) != white:
            continue
        f, r = frm % 8, frm // 8
        kind = p.upper()
        if kind == "P":
            d = 1 if white else -1
            last_rank = 7 if white else 0
            start_rank = 1 if white else 6
            nr = r + d
            if 0 <= nr < 8:
                to = nr * 8 + f
                if not b[to]:
                    if nr == last_rank:
                        out += [(frm, to, c) for c in "qrbn"]
                    else:
                        out.append((frm, to, ""))
                        if r == start_rank and not b[(r + 2 * d) * 8 + f]:
                            out.append((frm, (r + 2 * d) * 8 + f, ""))
                for nf in (f - 1, f + 1):
                    if 0 <= nf < 8:
                        to = nr * 8 + nf
                        target = b[to]
                        if (target and is_white(target) != white) or to == pos.ep:
                            if nr == last_rank:
                                out += [(frm, to, c) for c in "qrbn"]
                            else:
                                out.append((frm, to, ""))
        elif kind in "NK":
            steps = KNIGHT_STEPS if kind == "N" else KING_STEPS
            for df, dr in steps:
                nf, nr = f + df, r + dr
                if 0 <= nf < 8 and 0 <= nr < 8:
                    to = nr * 8 + nf
                    if not b[to] or is_white(b[to]) != white:
                        out.append((frm, to, ""))
        else:
            dirs = BISHOP_DIRS if kind == "B" else ROOK_DIRS if kind == "R" else BISHOP_DIRS + ROOK_DIRS
            for df, dr in dirs:
                nf, nr = f + df, r + dr
                while 0 <= nf < 8 and 0 <= nr < 8:
                    to = nr * 8 + nf
                    if b[to]:
                        if is_white(b[to]) != white:
                            out.append((frm, to, ""))
                        break
                    out.append((frm, to, ""))
                    nf += df; nr += dr
    # castling
    base = 0 if white else 56
    king = "K" if white else "k"
    rook = "R" if white else "r"
    if b[base + 4] == king and not attacked(b, base + 4, not white):
        if ("K" if white else "k") in pos.castle and b[base + 7] == rook and not b[base + 5] and not b[base + 6] \
                and not attacked(b, base + 5, not white) and not attacked(b, base + 6, not white):
            out.append((base + 4, base + 6, ""))
        if ("Q" if white else "q") in pos.castle and b[base] == rook and not b[base + 1] and not b[base + 2] \
                and not b[base + 3] and not attacked(b, base + 3, not white) and not attacked(b, base + 2, not white):
            out.append((base + 4, base + 2, ""))
    return out


def make_move(pos, mv):
    """Return the position after `mv` (assumed pseudo-legal)."""
    frm, to, promo = mv
    n = pos.copy()
    b = n.b
    piece, captured = b[frm], b[to]
    white = is_white(piece)
    kind = piece.upper()
    b[to] = piece
    b[frm] = ""
    if kind == "P" and to == pos.ep and frm % 8 != to % 8 and not captured:
        b[to - 8 if white else to + 8] = ""                       # en passant
        captured = "p"
    if promo:
        b[to] = promo.upper() if white else promo.lower()
    if kind == "K" and abs(to - frm) == 2:                          # castling: bring the rook across
        base = 0 if white else 56
        if to > frm:
            b[base + 5], b[base + 7] = b[base + 7], ""
        else:
            b[base + 3], b[base] = b[base], ""
    c = n.castle
    if kind == "K":
        c = c.replace("K" if white else "k", "").replace("Q" if white else "q", "")
    for sq, right in ((0, "Q"), (7, "K"), (56, "q"), (63, "k")):
        if frm == sq or to == sq:
            c = c.replace(right, "")
    n.castle = c
    n.ep = -1
    if kind == "P" and abs(to - frm) == 16:
        mid, enemy = (frm + to) // 2, "p" if white else "P"
        for nf in (to % 8 - 1, to % 8 + 1):
            if 0 <= nf < 8 and b[(to // 8) * 8 + nf] == enemy:
                n.ep = mid
    n.half = 0 if (kind == "P" or captured) else pos.half + 1
    n.full = pos.full + (1 if pos.turn == "b" else 0)
    n.turn = "b" if pos.turn == "w" else "w"
    return n


def legal_moves(pos):
    white = pos.turn == "w"
    out = []
    for mv in pseudo_moves(pos):
        nxt = make_move(pos, mv)
        ks = king_square(nxt.b, white)
        if ks >= 0 and not attacked(nxt.b, ks, not white):
            out.append(mv)
    return out


def uci(mv):
    return sq_name(mv[0]) + sq_name(mv[1]) + mv[2]


def parse_uci(text):
    if not isinstance(text, str) or len(text) not in (4, 5):
        raise ValueError("bad move")
    promo = text[4] if len(text) == 5 else ""
    if promo and promo not in "qrbn":
        raise ValueError("bad promotion")
    return (name_sq(text[:2]), name_sq(text[2:4]), promo)


def san(pos, mv, legal=None):
    legal = legal if legal is not None else legal_moves(pos)
    frm, to, promo = mv
    piece = pos.b[frm]
    kind = piece.upper()
    if kind == "K" and abs(to - frm) == 2:
        s = "O-O" if to > frm else "O-O-O"
    else:
        capture = bool(pos.b[to]) or (kind == "P" and to == pos.ep and frm % 8 != to % 8)
        if kind == "P":
            s = (FILES[frm % 8] + "x" if capture else "") + sq_name(to) + ("=" + promo.upper() if promo else "")
        else:
            s = kind
            others = [m for m in legal if m != mv and pos.b[m[0]] == piece and m[1] == to]
            if others:
                if not any(m[0] % 8 == frm % 8 for m in others):
                    s += FILES[frm % 8]
                elif not any(m[0] // 8 == frm // 8 for m in others):
                    s += str(frm // 8 + 1)
                else:
                    s += sq_name(frm)
            s += ("x" if capture else "") + sq_name(to)
    nxt = make_move(pos, mv)
    if in_check(nxt):
        s += "#" if not legal_moves(nxt) else "+"
    return s


def insufficient_material(b):
    others = [(i, p) for i, p in enumerate(b) if p and p.upper() != "K"]
    if not others:
        return True
    if any(p.upper() in "PRQ" for _, p in others):
        return False
    minors = [(i, p.upper()) for i, p in others]
    if len(minors) == 1:
        return True                                                # K+B vs K, K+N vs K
    if all(k == "B" for _, k in minors):                            # only bishops, all on one colour
        return len({((i % 8) + (i // 8)) % 2 for i, _ in minors}) == 1
    return False


def position_status(pos, repeats=1):
    """-> "" while the game goes on, else the reason it ended ('checkmate' means the side to move lost)."""
    if not legal_moves(pos):
        return "checkmate" if in_check(pos) else "stalemate"
    if insufficient_material(pos.b):
        return "insufficient"
    if repeats >= 3:
        return "repetition"
    if pos.half >= 100:
        return "fifty"
    return ""


def perft(pos, depth):
    if depth == 0:
        return 1
    total = 0
    for mv in legal_moves(pos):
        total += 1 if depth == 1 else perft(make_move(pos, mv), depth - 1)
    return total


# ---- game state used by the site (JSON friendly) -----------------------------------------------
def new_game(white_id, black_id, minutes=0, increment=0):
    pos = start_pos()
    clock = None
    if minutes:
        ms = int(minutes * 60 * 1000)
        clock = {"w": ms, "b": ms, "inc": int(increment * 1000), "since": None}
    return {"fen": pos.fen(), "history": [], "positions": {pos.key(): 1}, "last": None, "result": None,
            "draw_offer": None, "colors": {"w": white_id, "b": black_id}, "clock": clock}


def side_of(state, user_id):
    for c, uid in state["colors"].items():
        if uid == user_id:
            return c
    return ""


def clock_view(state, now_ms):
    """Remaining milliseconds for each side right now, and whether a clock is running."""
    clock = state.get("clock")
    if not clock:
        return None
    left = {"w": clock["w"], "b": clock["b"]}
    running = bool(clock["since"]) and not state["result"]
    if running:
        side = parse_fen(state["fen"]).turn
        left[side] = max(0, left[side] - max(0, now_ms - clock["since"]))
    return {"w": left["w"], "b": left["b"], "running": running, "turn": parse_fen(state["fen"]).turn, "inc": clock["inc"]}


def finish(state, winner_side, reason):
    state["result"] = {"winner": winner_side or None, "reason": reason}
    state["draw_offer"] = None


def check_timeout(state, now_ms):
    """If the side to move has run out of time, end the game. Returns True if it did."""
    if state["result"] or not state.get("clock"):
        return False
    view = clock_view(state, now_ms)
    if view["running"] and view[view["turn"]] <= 0:
        state["clock"][view["turn"]] = 0
        loser = view["turn"]
        pos = parse_fen(state["fen"])
        # flagged, but the other side cannot ever mate -> draw (rare: lone king / lone minor piece)
        other_has_force = any(p and (p.isupper() != (loser == "w")) and p.upper() in "PRQ" for p in pos.b) or \
            sum(1 for p in pos.b if p and (p.isupper() != (loser == "w")) and p.upper() in "NB") >= 2
        if other_has_force:
            finish(state, "b" if loser == "w" else "w", "timeout")
        else:
            finish(state, None, "timeout-draw")
        return True
    return False


def play(state, user_id, text, now_ms):
    """Apply a move given in UCI form. Returns an error string, or "" on success. Mutates `state`."""
    if state["result"]:
        return "This game is over."
    pos = parse_fen(state["fen"])
    side = side_of(state, user_id)
    if side != pos.turn:
        return "It is not your move."
    if check_timeout(state, now_ms):
        return "Your time has run out."
    try:
        mv = parse_uci(text)
    except ValueError:
        return "That move is not valid."
    legal = legal_moves(pos)
    if mv not in legal:
        return "That move is not legal."
    clock = state.get("clock")
    if clock:
        if len(state["history"]) >= 2 and clock["since"]:       # clocks only start once both sides have moved
            clock[side] = max(1, clock[side] - max(0, now_ms - clock["since"])) + clock["inc"]
        clock["since"] = now_ms if len(state["history"]) + 1 >= 2 else None
    text_san = san(pos, mv, legal)
    nxt = make_move(pos, mv)
    key = nxt.key()
    state["positions"][key] = state["positions"].get(key, 0) + 1
    state["fen"] = nxt.fen()
    state["history"].append({"uci": uci(mv), "san": text_san})
    state["last"] = [sq_name(mv[0]), sq_name(mv[1])]
    state["draw_offer"] = None
    reason = position_status(nxt, state["positions"][key])
    if reason == "checkmate":
        finish(state, side, "checkmate")
    elif reason:
        finish(state, None, reason)
    return ""


def resign(state, user_id):
    side = side_of(state, user_id)
    if state["result"] or not side:
        return "This game is over."
    finish(state, "b" if side == "w" else "w", "resignation")
    return ""


def offer_draw(state, user_id):
    side = side_of(state, user_id)
    if state["result"] or not side:
        return "This game is over."
    if state["draw_offer"] == side:
        return "You already offered a draw."
    if state["draw_offer"]:                                          # the other side had offered: that is an accept
        finish(state, None, "agreement")
    else:
        state["draw_offer"] = side
    return ""


def answer_draw(state, user_id, accept):
    side = side_of(state, user_id)
    if state["result"] or not side or not state["draw_offer"] or state["draw_offer"] == side:
        return "There is no draw offer to answer."
    if accept:
        finish(state, None, "agreement")
    else:
        state["draw_offer"] = None
    return ""


def legal_map(state):
    """{"e2": ["e3", "e4"], ...} for the side to move (promotions listed once)."""
    if state["result"]:
        return {}
    out = {}
    for frm, to, _ in legal_moves(parse_fen(state["fen"])):
        lst = out.setdefault(sq_name(frm), [])
        if sq_name(to) not in lst:
            lst.append(sq_name(to))
    return out


def public_state(state):
    out = {k: v for k, v in state.items() if k != "positions"}
    pos = parse_fen(state["fen"])
    out["turn_side"] = pos.turn
    out["check"] = (in_check(pos) and not state["result"])
    return out
