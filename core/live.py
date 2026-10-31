"""Instant updates without WebSockets or extra services.

Every open Wardbook tab keeps one request parked on /live/ (a "long poll"). When anything the tab
cares about changes (a message arrives, a notification is created, a post is liked, a game move is
played ...) a Django signal bumps a version number here, the parked request wakes up in milliseconds,
and the page fetches just what changed. If nothing happens the request ends after WAIT seconds and
the tab simply asks again.

This relies on all requests being served by ONE gunicorn process (see render.yaml: --workers 1 with
many threads), because the version numbers live in this process's memory. If you ever run several
processes, nothing breaks, it just gets slower: badges and "new notification" pop-ups still arrive
(they are read from the database at the end of every wait, <= WAIT seconds), while chat, feed and game
pages fall back to their own slow safety-net refresh (30-60 s) for events that happened in another process.
"""
import logging
import os
import threading
import time

from django.db import transaction
from django.db.models import Max, Q
from django.db.models.signals import m2m_changed, post_delete, post_save
from django.dispatch import receiver
from django.http import JsonResponse

from .models import (Comment, GameChallenge, GameChatMessage, GameSession, GroupMember, Message, Notification, Post)

log = logging.getLogger(__name__)

WAIT = 20  # seconds a parked request waits before answering "nothing new"
MAX_STREAMS = int(os.environ.get("LIVE_MAX_STREAMS", "32"))  # keep a few threads free for ordinary pages
RETRY_BUSY_MS = 4000

_cond = threading.Condition()
_versions = {}
_seq = 0
_slots = threading.BoundedSemaphore(MAX_STREAMS)


def bump(*keys):
    """Mark channels as changed and wake every parked request."""
    global _seq
    with _cond:
        _seq += 1
        for k in keys:
            _versions[k] = _seq
        _cond.notify_all()


def bump_on_commit(*keys):
    """Same, but only once the database change is really visible to other requests."""
    transaction.on_commit(lambda: bump(*keys))


def user_key(user_id, channel):
    return ("u", user_id, channel)


# ---- what changes which channel ------------------------------------------------------------------
@receiver(post_save, sender=Notification)
def _notification_saved(sender, instance, created, **kw):
    if created:
        bump_on_commit(user_key(instance.recipient_id, "n"))


@receiver(post_save, sender=Message)
def _message_saved(sender, instance, created, **kw):
    if not created:
        return
    ids = {instance.sender_id}
    if instance.recipient_id:
        ids.add(instance.recipient_id)
    else:
        ids.update(GroupMember.objects.filter(group_id=instance.group_id).values_list("user_id", flat=True))
    bump_on_commit(*[user_key(i, "c") for i in ids])


@receiver(post_save, sender=Post)
@receiver(post_delete, sender=Post)
@receiver(post_save, sender=Comment)
@receiver(post_delete, sender=Comment)
def _feed_changed(sender, **kw):
    bump_on_commit(("f",))


@receiver(m2m_changed, sender=Post.likes.through)
def _likes_changed(sender, action, **kw):
    if action in ("post_add", "post_remove", "post_clear"):
        bump_on_commit(("f",))


@receiver(post_save, sender=GameSession)
def _game_saved(sender, instance, **kw):
    bump_on_commit(("g", instance.pk))


@receiver(post_save, sender=GameChatMessage)
def _game_chat_saved(sender, instance, created, **kw):
    if created:
        bump_on_commit(("g", instance.session_id))


# ---- the parked request ------------------------------------------------------------------------
def _key_for(user, name):
    if name in ("n", "c"):
        return user_key(user.pk, name)
    if name == "f":
        return ("f",)
    return ("g", int(name[1:]))


def _parse(request, user):
    """?s=n:12,c:-,g5:3  ->  ordered channel names and the version the tab last saw (None = never)."""
    names, known = [], {}
    for part in request.GET.get("s", "").split(",")[:8]:
        name, _, ver = part.partition(":")
        if name in names:
            continue
        if name in ("n", "c", "f"):
            pass
        elif name[:1] == "g" and name[1:].isdigit():
            gid = int(name[1:])
            allowed = GameChallenge.objects.filter(session__pk=gid).filter(
                Q(challenger=user) | Q(opponent=user) | Q(participants=user)).exists()
            if not allowed:
                continue
        else:
            continue
        names.append(name)
        known[name] = int(ver) if ver.isdigit() else None
    return names, known


def counts_for(user):
    """Numbers shown on the nav badges."""
    from .views import group_memberships  # imported here: views imports this module

    dms = Message.objects.filter(recipient=user, is_read=False).count()
    return {
        "dms": dms + sum(m.unread for m in group_memberships(user)),
        "notifs": Notification.objects.filter(recipient=user, is_read=False).count(),
    }


def stream(request):
    user = request.user
    if not user.is_authenticated:
        return JsonResponse({"auth": False}, status=401)
    names, known = _parse(request, user)
    keys = {n: _key_for(user, n) for n in names}

    got_slot = _slots.acquire(blocking=False)  # a full house degrades to quick polling instead of hanging the site
    deadline = time.monotonic() + WAIT
    try:
        with _cond:
            while True:
                current = {n: _versions.get(keys[n], 0) for n in names}
                changed = [n for n in names if known[n] is None or current[n] != known[n]]
                if changed or not got_slot:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                _cond.wait(remaining)
    finally:
        if got_slot:
            _slots.release()

    out = {"v": current, "changed": changed, "counts": counts_for(user)}
    if not got_slot and not changed:
        out["retry"] = RETRY_BUSY_MS

    # Notifications created since the id the tab last saw, so it can show them instantly (and not twice).
    mine = Notification.objects.filter(recipient=user)
    out["nid"] = mine.aggregate(m=Max("id"))["m"] or 0
    nid = request.GET.get("nid", "")
    if nid.isdigit():
        out["notifs"] = [
            {"id": n.id, "title": n.title, "body": n.body, "url": n.url, "kind": n.kind}
            for n in mine.filter(id__gt=int(nid), is_read=False).order_by("id")[:5]
        ]
    resp = JsonResponse(out)
    resp["Cache-Control"] = "no-store"
    return resp
