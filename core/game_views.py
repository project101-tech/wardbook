"""Games: the lobby, invitations, playing, and one move endpoint for all five games.

The rules themselves live in gamelogic.py / chess_engine.py (no Django there). This file only handles
who may do what, saving, notifications and the JSON the game screen needs.
"""
import re
import time

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from . import chess_engine as chess
from . import gamelogic as logic
from . import teenpatti_engine as tpe
from .models import GameChallenge, GameChatMessage, GameParticipant, GameSession, Notification
from .environments import visible_users
from .views import fmt_time, notify, residents_except

User = get_user_model()
Game = GameChallenge.Game

# What the lobby shows for each game (order = order on the page).
GAME_CARDS = [
    {"key": Game.TEEN_PATTI, "label": "Teen Patti", "blurb": "Flash with play money: blind or seen, side shows, a big pot. Up to 9 at one table.",
     "who": "2 to 9 players", "party": True, "max": 8, "glyph": "♠", "theme": "tp"},
    {"key": Game.CHESS, "label": "Chess", "blurb": "The full game with clocks, castling, en passant and draw offers.",
     "who": "2 players", "party": False, "max": 1, "glyph": "♞", "theme": "chess"},
    {"key": Game.LUDO, "label": "Ludo", "blurb": "Roll a six to start. Race four tokens home and send rivals back to the yard.",
     "who": "2 to 4 players", "party": True, "max": 3, "glyph": "🎲", "theme": "ludo"},
    {"key": Game.SNAKES_LADDERS, "label": "Snakes & Ladders", "blurb": "Climb the ladders, dodge the snakes, reach 100 on an exact roll.",
     "who": "2 to 4 players", "party": True, "max": 3, "glyph": "🐍", "theme": "snakes"},
    {"key": Game.CONNECT_FOUR, "label": "Connect Four", "blurb": "Drop discs and line up four before your neighbour does.",
     "who": "2 players", "party": False, "max": 1, "glyph": "●", "theme": "c4"},
    {"key": Game.TIC_TAC_TOE, "label": "Tic-tac-toe", "blurb": "Quick three-in-a-row. Perfect for a spare minute.",
     "who": "2 players", "party": False, "max": 1, "glyph": "✕", "theme": "ttt"},
]
CARD_BY_KEY = {c["key"]: c for c in GAME_CARDS}
CHESS_CLOCKS = {"3": "3 min", "5": "5 min", "10": "10 min", "15": "15 min", "30": "30 min"}
TP_BOOTS = {"10": "₹10 boot", "20": "₹20 boot", "50": "₹50 boot"}      # rupees of pretend money; 10 rupees = 1 token
CHESS_REASONS = {
    "checkmate": "Checkmate", "stalemate": "Stalemate", "insufficient": "Not enough pieces to checkmate",
    "repetition": "Same position three times", "fifty": "50 moves without a capture or pawn move",
    "agreement": "Draw agreed", "resignation": "Resignation", "timeout": "Time ran out",
    "timeout-draw": "Time ran out, but there is not enough material to win",
}


def now_ms():
    return int(time.time() * 1000)


def boot_of(challenge):
    """Teen Patti: the boot (entry stake) in tokens, kept as text like "₹20 boot" on the challenge."""
    m = re.search(r"(\d+)", challenge.time_control or "")
    return max(1, int(m.group(1)) // 10) if m else 1


class TableError(Exception):
    pass


def minutes_of(challenge):
    if challenge.game != Game.CHESS:
        return 0
    m = re.match(r"\s*(\d+)", challenge.time_control or "")
    return int(m.group(1)) if m else 0


# --------------------------------------------------------------------------------------------
# Lobby
# --------------------------------------------------------------------------------------------
def _names(user_ids):
    return {u.pk: u for u in User.objects.filter(pk__in=set(user_ids)).select_related("profile")}


@login_required
def games(request):
    me = request.user
    mine_qs = GameChallenge.objects.filter(participantships__user=me).distinct()

    received = (GameChallenge.objects.filter(status=GameChallenge.Status.PENDING, participantships__user=me,
                                             participantships__response=GameParticipant.Response.PENDING)
                .select_related("challenger").prefetch_related("participantships__user"))
    waiting = (GameChallenge.objects.filter(status=GameChallenge.Status.PENDING, challenger=me)
               .prefetch_related("participantships__user"))
    sessions = (GameSession.objects.filter(is_active=True, challenge__participantships__user=me,
                                           challenge__participantships__response=GameParticipant.Response.ACCEPTED)
                .select_related("challenge").distinct().order_by("-updated_at")[:20])
    recent = (mine_qs.filter(status=GameChallenge.Status.FINISHED).select_related("challenger", "opponent", "winner")
              .prefetch_related("participantships__user")[:8])

    active_rows = []
    people = _names(uid for s in sessions for uid in s.state.get("players", []))
    for s in sessions:
        others = [people[i].display_name for i in s.state.get("players", []) if i != me.pk and i in people]
        active_rows.append({"pk": s.pk, "label": s.challenge.get_game_display(), "key": s.challenge.game,
                            "others": ", ".join(others), "your_turn": s.current_turn_id == me.pk,
                            "when": fmt_time(s.updated_at)})
    active_rows.sort(key=lambda r: not r["your_turn"])

    def waiting_row(c):
        parts = list(c.participantships.all())
        return {"pk": c.pk, "label": c.get_game_display(), "key": c.game, "party": c.game in logic.PARTY,
                "time_control": c.time_control,
                "joined": [p.user.display_name for p in parts if p.response == "accepted" and p.user_id != me.pk],
                "waiting_on": [p.user.display_name for p in parts if p.response == "pending"],
                "declined": [p.user.display_name for p in parts if p.response == "declined"],
                "can_start": c.game in logic.PARTY and any(p.response == "accepted" and p.user_id != me.pk for p in parts)}

    def received_row(c):
        parts = list(c.participantships.all())
        return {"pk": c.pk, "label": c.get_game_display(), "key": c.game, "from": c.challenger.display_name,
                "time_control": c.time_control,
                "with": [p.user.display_name for p in parts if p.user_id not in (me.pk, c.challenger_id)]}

    def recent_row(c):
        parts = [p.user.display_name for p in c.participantships.all() if p.user_id != me.pk and p.response == "accepted"]
        result = "draw" if not c.winner_id else "won" if c.winner_id == me.pk else "lost"
        return {"label": c.get_game_display(), "key": c.game, "others": ", ".join(parts), "result": result,
                "when": fmt_time(c.created_at)}

    finished = GameChallenge.objects.filter(status=GameChallenge.Status.FINISHED, participantships__user=me,
                                            participantships__response=GameParticipant.Response.ACCEPTED).distinct()
    wins = finished.filter(winner=me).count()
    total = finished.count()
    draws = finished.filter(winner__isnull=True).count()
    top = (GameChallenge.objects.filter(status=GameChallenge.Status.FINISHED, winner__isnull=False, winner__in=visible_users(me).values("pk"))
           .values("winner").annotate(w=Count("id")).order_by("-w")[:5])
    top_people = _names(r["winner"] for r in top)
    leaderboard = [{"name": top_people[r["winner"]].display_name, "wins": r["w"], "me": r["winner"] == me.pk}
                   for r in top if r["winner"] in top_people]

    residents = [{"pk": r.pk, "name": r.display_name, "house": r.house_label, "online": r.profile.is_online,
                  "initial": (r.display_name[:1] or "?").upper()} for r in residents_except(me)]
    residents.sort(key=lambda r: (not r["online"], r["name"].lower()))
    return render(request, "games.html", {
        "cards": GAME_CARDS, "clocks": CHESS_CLOCKS, "residents": residents,
        "received": [received_row(c) for c in received], "waiting": [waiting_row(c) for c in waiting],
        "active": active_rows, "recent": [recent_row(c) for c in recent],
        "stats": {"wins": wins, "losses": total - wins - draws, "draws": draws, "played": total},
        "leaderboard": leaderboard,
        "boots": [{"k": k, "label": v, "tokens": int(k) // 10} for k, v in TP_BOOTS.items()],
    })


@login_required
@require_POST
def game_challenge(request):
    game = request.POST.get("game")
    if game not in Game.values:
        messages.error(request, "Choose a game.")
        return redirect("games")
    ids = []
    for value in request.POST.getlist("opponents") + [request.POST.get("opponent", "")]:
        if value.isdigit() and int(value) not in ids and int(value) != request.user.pk:
            ids.append(int(value))
    party = game in logic.PARTY
    most = CARD_BY_KEY[game]["max"]
    if len(ids) < 1 or len(ids) > most:
        messages.error(request, f"Pick 1 to {most} neighbours." if party else "Pick one neighbour to play against.")
        return redirect("games")
    people = {r.pk: r for r in residents_except(request.user).filter(pk__in=ids)}
    if len(people) != len(ids):
        messages.error(request, "One of those neighbours cannot be invited.")
        return redirect("games")
    if game == Game.TEEN_PATTI:
        clock = TP_BOOTS.get(request.POST.get("boot", ""), TP_BOOTS["10"])         # only the boot is chosen here; everybody picks their own buy-in at the table
    else:
        clock = request.POST.get("time_control", "") if game == Game.CHESS else ""
        clock = CHESS_CLOCKS.get(clock, "")
    challenge = GameChallenge.objects.create(game=game, challenger=request.user, opponent=people[ids[0]], time_control=clock)
    GameParticipant.objects.create(challenge=challenge, user=request.user, seat=0, response=GameParticipant.Response.ACCEPTED)
    for seat, uid in enumerate(ids, start=1):
        GameParticipant.objects.create(challenge=challenge, user=people[uid], seat=seat, response=GameParticipant.Response.PENDING)
        notify(people[uid], f"{request.user.display_name} invited you to play {challenge.get_game_display()}",
               "Open Games to accept the invitation.", reverse("games"), Notification.Kind.GAME, request.user)
    messages.success(request, "Invitation sent.")
    return redirect("games")


def session_for_challenge(challenge):
    """The running game for an accepted challenge (created on first use)."""
    existing = GameSession.objects.filter(challenge=challenge).first()
    if existing:
        return existing
    parts = [p for p in challenge.participantships.order_by("seat") if p.response == GameParticipant.Response.ACCEPTED]
    players = [p.user_id for p in parts] or [challenge.challenger_id, challenge.opponent_id]
    if not parts:
        GameParticipant.objects.get_or_create(challenge=challenge, user_id=challenge.challenger_id, defaults={"seat": 0})
        GameParticipant.objects.get_or_create(challenge=challenge, user_id=challenge.opponent_id, defaults={"seat": 1})
    if challenge.game == Game.TEEN_PATTI:
        state, first = _open_table(challenge, players)
    else:
        state, first = logic.new_state(challenge.game, players, minutes_of(challenge))
    return GameSession.objects.create(challenge=challenge, state=state, current_turn_id=first)


def _open_table(challenge, players):
    """Open the table. There is no wallet: everybody picks how much to sit down with on the table screen itself."""
    import random
    seated = list(players)
    random.SystemRandom().shuffle(seated)                       # random seats
    state = tpe.new_table(seated, boot_of(challenge), now_ms())
    return state, tpe.info(state)["next"]


def _try_start(request, challenge):
    """Start a game; if a Teen Patti table cannot be opened, tell everyone and call the invitation off."""
    try:
        return _start(challenge)
    except TableError as e:
        challenge.status = GameChallenge.Status.DECLINED
        challenge.save(update_fields=["status"])
        messages.error(request, str(e))
        for p in challenge.participantships.exclude(user=request.user).select_related("user"):
            if p.response == GameParticipant.Response.ACCEPTED:
                notify(p.user, f"Your {challenge.get_game_display()} table was cancelled", str(e), reverse("games"),
                       Notification.Kind.GAME, request.user)
        return None


def _start(challenge):
    challenge.status = GameChallenge.Status.ACCEPTED
    challenge.save(update_fields=["status"])
    return session_for_challenge(challenge)


@login_required
@require_POST
def game_update(request, pk):
    """An invited neighbour accepts or declines."""
    answer = request.POST.get("answer")
    if answer not in (GameParticipant.Response.ACCEPTED, GameParticipant.Response.DECLINED):
        return redirect("games")
    with transaction.atomic():
        challenge = get_object_or_404(GameChallenge.objects.select_for_update(), pk=pk, status=GameChallenge.Status.PENDING,
                                      participantships__user=request.user,
                                      participantships__response=GameParticipant.Response.PENDING)
        GameParticipant.objects.filter(challenge=challenge, user=request.user).update(response=answer)
        party = challenge.game in logic.PARTY
        parts = list(challenge.participantships.all())
        waiting = [p for p in parts if p.response == GameParticipant.Response.PENDING]
        joined = [p for p in parts if p.response == GameParticipant.Response.ACCEPTED]
        game_name = challenge.get_game_display()
        if not party:
            if answer == GameParticipant.Response.ACCEPTED:
                session = _start(challenge)
                notify(challenge.challenger, f"{request.user.display_name} accepted your {game_name} invitation",
                       "The game is ready. Tap to play.", reverse("game_play", args=[session.pk]),
                       Notification.Kind.GAME, request.user)
                return redirect("game_play", pk=session.pk)
            challenge.status = GameChallenge.Status.DECLINED
            challenge.save(update_fields=["status"])
            notify(challenge.challenger, f"{request.user.display_name} declined your {game_name} invitation", "",
                   reverse("games"), Notification.Kind.GAME, request.user)
            return redirect("games")
        # Ludo / Snakes: wait for everyone to answer, then start with whoever said yes
        verb = "joined" if answer == GameParticipant.Response.ACCEPTED else "declined"
        notify(challenge.challenger, f"{request.user.display_name} {verb} your {game_name} game", "",
               reverse("games"), Notification.Kind.GAME, request.user)
        if not waiting:
            if len(joined) >= 2:
                session = _try_start(request, challenge)
                if session is None:
                    return redirect("games")
                for p in joined:
                    notify(p.user, f"Your {game_name} game is starting", "Everyone has answered. Tap to play.",
                           reverse("game_play", args=[session.pk]), Notification.Kind.GAME, request.user)
                return redirect("game_play", pk=session.pk)
            challenge.status = GameChallenge.Status.DECLINED
            challenge.save(update_fields=["status"])
    return redirect("games")


@login_required
@require_POST
def game_start(request, pk):
    """The host of a Ludo / Snakes game starts it without waiting for everyone."""
    with transaction.atomic():
        challenge = get_object_or_404(GameChallenge.objects.select_for_update(), pk=pk, challenger=request.user,
                                      status=GameChallenge.Status.PENDING, game__in=logic.PARTY)
        joined = challenge.participantships.filter(response=GameParticipant.Response.ACCEPTED)
        if joined.count() < 2:
            messages.error(request, "Wait until at least one neighbour has joined.")
            return redirect("games")
        challenge.participantships.filter(response=GameParticipant.Response.PENDING).update(response=GameParticipant.Response.DECLINED)
        session = _try_start(request, challenge)
        if session is None:
            return redirect("games")
        for p in joined.exclude(user=request.user).select_related("user"):
            notify(p.user, f"Your {challenge.get_game_display()} game is starting", "Tap to play.",
                   reverse("game_play", args=[session.pk]), Notification.Kind.GAME, request.user)
    return redirect("game_play", pk=session.pk)


@login_required
@require_POST
def game_cancel(request, pk):
    challenge = get_object_or_404(GameChallenge, pk=pk, challenger=request.user, status=GameChallenge.Status.PENDING)
    challenge.status = GameChallenge.Status.DECLINED
    challenge.save(update_fields=["status"])
    return redirect("games")


# --------------------------------------------------------------------------------------------
# Playing
# --------------------------------------------------------------------------------------------
def game_access_or_404(user, pk):
    session = get_object_or_404(GameSession.objects.select_related("challenge"), pk=pk)
    players = session.state.get("players") or [session.challenge.challenger_id, session.challenge.opponent_id]
    if user.id not in players:
        raise PermissionDenied
    return session


def _avatar(u):
    prof = getattr(u, "profile", None)
    try:
        return prof.avatar.url if prof and prof.avatar else ""
    except Exception:
        return ""


def game_payload(session, user):
    ch, state = session.challenge, session.state
    ids = state.get("players") or [ch.challenger_id, ch.opponent_id]
    users = _names(ids)
    players = []
    for seat, pid in enumerate(ids):
        u = users.get(pid)
        if not u:
            continue
        players.append({"id": pid, "name": u.display_name, "house": u.house_label, "seat": seat,
                        "online": u.profile.is_online, "initial": (u.display_name[:1] or "?").upper(),
                        "avatar": _avatar(u), "symbol": state.get("symbols", {}).get(str(pid), "")})
    if ch.game == Game.CHESS:
        clean = chess.public_state(state)
    elif ch.game == Game.TEEN_PATTI:
        clean = {}                                   # the table's own state holds everyone's cards: never sent
    else:
        clean = dict(state)
    turn_is_me = session.is_active and session.current_turn_id == user.id
    out = {"game": ch.game, "label": ch.get_game_display(), "active": session.is_active, "state": clean,
           "turn": session.current_turn_id, "you": user.id, "players": players, "winner": session.winner_id,
           "reason": state.get("reason", ""), "time_control": ch.time_control, "server_now": now_ms(),
           "rev": int(session.updated_at.timestamp() * 1000),
           "rematch_url": reverse("game_play", args=[session.rematch_session_id]) if session.rematch_session_id else "",
           "can_resign": session.is_active and ch.game in logic.TWO_PLAYER,
           "opponent_online": any(p["online"] for p in players if p["id"] != user.id)}
    if ch.game == Game.CHESS:
        out["legal"] = chess.legal_map(state) if turn_is_me else {}
        out["clock"] = chess.clock_view(state, out["server_now"])
        out["you_side"] = chess.side_of(state, user.id)
    elif ch.game == Game.TEEN_PATTI:
        pv = tpe.public_view(state, user.id, out["server_now"])
        out["tp"] = pv
        out["turn"] = pv["turn"] if pv["phase"] == "bet" else (pv["side"]["to"] if pv["phase"] == "sideshow" else None)
    elif ch.game == Game.LUDO:
        dice = state.get("dice")
        out["movable"] = logic.ludo_movable(state, user.id, dice) if (dice and turn_is_me) else []
    return out


@login_required
def game_play(request, pk):
    session = game_access_or_404(request.user, pk)
    return render(request, "game_play.html", {"session": session, "payload": game_payload(session, request.user),
                                              "card": CARD_BY_KEY.get(session.challenge.game, {})})


def chess_info(state):
    side = chess.parse_fen(state["fen"]).turn
    nxt = state["colors"][side]
    res = state["result"]
    if not res:
        return {"next": nxt, "winner": None, "finished": False, "reason": ""}
    winner = state["colors"][res["winner"]] if res["winner"] else None
    return {"next": nxt, "winner": winner, "finished": True, "reason": CHESS_REASONS.get(res["reason"], res["reason"])}


def _save(session, info):
    """Write the outcome of a move back to the database."""
    challenge, state = session.challenge, session.state
    session.current_turn_id = info["next"]
    state["seq"] = state.get("seq", 0) + 1          # lets the screen tell a new move from a re-sent one
    if info["finished"]:
        state["reason"] = info["reason"]
        session.is_active = False
        session.winner_id = info["winner"]
        challenge.winner_id = info["winner"]
        challenge.status = GameChallenge.Status.FINISHED
        challenge.save(update_fields=["winner", "status"])
    session.state = state
    session.save(update_fields=["state", "current_turn", "is_active", "winner", "updated_at"])


def _nudge(session, user_id, actor):
    """Tell a neighbour who is not on the site that it is their move (once, not on every move)."""
    if user_id == actor.id or not session.is_active:
        return
    target = User.objects.filter(pk=user_id).select_related("profile").first()
    if not target or target.profile.is_online:
        return
    url = reverse("game_play", args=[session.pk])
    if Notification.objects.filter(recipient=target, kind=Notification.Kind.GAME, url=url, is_read=False).exists():
        return
    notify(target, f"Your move in {session.challenge.get_game_display()}",
           f"{actor.display_name} has played. Tap to take your turn.", url, Notification.Kind.GAME, actor)


def _settle_clock(session):
    """If a chess clock has run out, end the game. Returns True if it changed anything."""
    state = session.state
    if session.challenge.game != Game.CHESS or not session.is_active or not state.get("clock"):
        return False
    if chess.check_timeout(state, now_ms()):
        _save(session, chess_info(state))
        return True
    return False


def _settle_table(session):
    """Teen Patti: a turn timer ran out (auto-pack), or the pause between hands is over. True if anything changed."""
    if session.challenge.game != Game.TEEN_PATTI or not session.is_active:
        return False
    if tpe.settle(session.state, now_ms()):
        _save(session, tpe.info(session.state))
        return True
    return False


@login_required
@require_POST
def game_move(request, pk):
    game_access_or_404(request.user, pk)
    me = request.user
    with transaction.atomic():
        session = GameSession.objects.select_for_update().select_related("challenge").get(pk=pk)

        def reply(error="", status=200):
            body = {"game": game_payload(session, me)}
            if error:
                body["error"] = error
            return JsonResponse(body, status=status)

        _settle_table(session)
        if not session.is_active:
            return reply("This game is over.", 400)
        if _settle_clock(session):
            return reply("Time ran out.", 409)
        game, state = session.challenge.game, session.state
        action = request.POST.get("action", "move")
        value = request.POST.get("move", "")
        my_turn = session.current_turn_id == me.id

        nudge = True
        if game == Game.TEEN_PATTI:
            if action != "tp":
                return reply("Unknown action.", 400)
            err = tpe.act(state, me.id, value, now_ms())
            if err:
                return reply(err, 400)
            info = tpe.info(state)
            nudge = info.get("nudge", False)
        elif action == "resign":
            if game not in logic.TWO_PLAYER:
                return reply("You cannot resign this game.", 400)
            if game == Game.CHESS:
                chess.resign(state, me.id)
                info = chess_info(state)
            else:
                other = logic.next_in_order(state["players"], me.id)
                info = {"next": me.id, "winner": other, "finished": True, "reason": "Resignation"}
        elif action in ("draw_offer", "draw_accept", "draw_decline"):
            if game != Game.CHESS:
                return reply("Draws can only be offered in chess.", 400)
            if action == "draw_offer":
                err = chess.offer_draw(state, me.id)
            else:
                err = chess.answer_draw(state, me.id, action == "draw_accept")
            if err:
                return reply(err, 400)
            info = chess_info(state)
        elif action in ("move", "roll"):
            if not my_turn:
                return reply("Wait for your turn.", 409)
            if game == Game.CHESS:
                err = chess.play(state, me.id, value, now_ms())
                if err:
                    return reply(err, 400)
                info = chess_info(state)
            elif game == Game.TIC_TAC_TOE:
                info = logic.ttt_move(state, me.id, value)
            elif game == Game.CONNECT_FOUR:
                info = logic.c4_move(state, me.id, value)
            elif game == Game.SNAKES_LADDERS:
                info = logic.snakes_roll(state, me.id)
            elif game == Game.LUDO:
                info = logic.ludo_roll(state, me.id) if action == "roll" else logic.ludo_move(state, me.id, value)
            else:
                return reply("Unknown game.", 400)
            if "error" in info:
                return reply(info["error"], 400)
        else:
            return reply("Unknown action.", 400)

        _save(session, info)
        if nudge and action in ("move", "roll", "tp") and not info["finished"] and info["next"] != me.id:
            _nudge(session, info["next"], me)
        return reply()


@login_required
def game_state(request, pk):
    session = game_access_or_404(request.user, pk)
    game = session.challenge.game
    if session.is_active and (game == Game.TEEN_PATTI or (game == Game.CHESS and session.state.get("clock"))):
        with transaction.atomic():
            locked = GameSession.objects.select_for_update().select_related("challenge").get(pk=pk)
            if _settle_clock(locked) or _settle_table(locked):
                session = locked
    return JsonResponse({"game": game_payload(session, request.user)})


@login_required
def game_chat(request, pk):
    session = game_access_or_404(request.user, pk)
    if request.method == "POST":
        body = request.POST.get("body", "").strip()[:500]
        if not body:
            return JsonResponse({"error": "Write a message first."}, status=400)
        GameChatMessage.objects.create(session=session, sender=request.user, body=body)
    after = request.GET.get("after", "0")
    try:
        after = int(after)
    except ValueError:
        after = 0
    msgs = session.chat_messages.filter(id__gt=after).select_related("sender")
    return JsonResponse({"messages": [{"id": m.id, "body": m.body, "mine": m.sender_id == request.user.id,
                                        "sender": m.sender.display_name, "time": fmt_time(m.created_at)} for m in msgs]})


@login_required
@require_POST
def game_rematch(request, pk):
    old = game_access_or_404(request.user, pk)
    with transaction.atomic():
        old = GameSession.objects.select_for_update().select_related("challenge").get(pk=old.pk)
        if old.is_active:
            return redirect("game_play", pk=old.pk)
        if old.challenge.game == Game.TEEN_PATTI:
            # a new table is an invitation everyone answers (and everybody picks a fresh buy-in), not an automatic rematch
            challenge = old.challenge
            others = [uid for uid in old.state.get("players", []) if uid != request.user.id]
            if not others:
                return redirect("games")
            fresh = GameChallenge.objects.create(game=challenge.game, challenger=request.user, opponent_id=others[0],
                                                 time_control=challenge.time_control)
            GameParticipant.objects.create(challenge=fresh, user=request.user, seat=0, response=GameParticipant.Response.ACCEPTED)
            for seat, uid in enumerate(others, start=1):
                GameParticipant.objects.create(challenge=fresh, user_id=uid, seat=seat, response=GameParticipant.Response.PENDING)
                target = User.objects.filter(pk=uid).first()
                if target:
                    notify(target, f"{request.user.display_name} invited you to a new Teen Patti table",
                           "Open Games to take a seat.", reverse("games"), Notification.Kind.GAME, request.user)
            messages.success(request, "New table opened. It starts when everyone has answered.")
            return redirect("games")
        if old.rematch_session_id:
            session = old.rematch_session
        else:
            challenge = old.challenge
            players = old.state.get("players", [])
            new_challenge = GameChallenge.objects.create(
                game=challenge.game, challenger=challenge.challenger, opponent=challenge.opponent,
                time_control=challenge.time_control, status=GameChallenge.Status.ACCEPTED)
            seat = 0
            for uid in players:
                GameParticipant.objects.create(challenge=new_challenge, user_id=uid, seat=seat,
                                               response=GameParticipant.Response.ACCEPTED)
                seat += 1
            session = session_for_challenge(new_challenge)
            old.rematch_session = session
            old.save(update_fields=["rematch_session"])
    for uid in session.state.get("players", []):
        if uid != request.user.id:
            target = User.objects.filter(pk=uid).first()
            if target:
                notify(target, f"{request.user.display_name} wants a rematch in {session.challenge.get_game_display()}",
                       "Tap to play again.", reverse("game_play", args=[session.pk]), Notification.Kind.GAME, request.user)
    return redirect("game_play", pk=session.pk)
