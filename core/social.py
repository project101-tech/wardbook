"""@mentions (search + resolving who was tagged) and event pokes.

Kept in its own file so the big views.py stays as it is. notify_mentions() in views.py calls resolve_mentions().
"""
import re
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .environments import my_env_ids, users_in_envs, visible_announcements, visible_users
from .models import Announcement, AnnouncementResponse, ChatGroup, Community, EventPoke, GroupMember, Notification

User = get_user_model()
POKE_COOLDOWN = timedelta(hours=12)
POKES_PER_DAY = 25


# --------------------------------------------------------------------------
# Mentions
# --------------------------------------------------------------------------
def _full_name(u):
    return f"{u.first_name} {u.last_name}".strip()


def _residents(viewer=None):
    """Everyone who can be mentioned: (user, full name, house, name_is_shared). Phone numbers are never used.
    With a `viewer`, only people in a network they are in."""
    base = visible_users(viewer) if viewer is not None else User.objects.filter(is_active=True, profile__isnull=False)
    users = list(base.select_related("profile"))
    names = [_full_name(u) for u in users]
    out = []
    for u, name in zip(users, names):
        if not name:
            continue
        out.append((u, name, u.profile.house_number or "", sum(1 for n in names if n.lower() == name.lower()) > 1))
    return out


def label_for(name, house, shared):
    """What gets typed into the text box. Two neighbours with the same name get their house number added."""
    return f"{name} ({house})" if shared and house else name


def resolve_mentions(text, only_user_ids=None, viewer=None):
    """Users tagged in `text` with @Full Name (or @Full Name (house) when two neighbours share a name)."""
    if not text or "@" not in text:
        return []
    low = text.lower()
    chars = list(low)
    found = []
    entries = []
    for u, name, house, shared in _residents(viewer):
        if only_user_ids is not None and u.pk not in only_user_ids:
            continue
        if shared and not house:
            continue
        entries.append((u, label_for(name, house, shared).lower()))
    entries.sort(key=lambda e: -len(e[1]))                    # longest names first, so "@Ann Lee" is not read as "@Ann"
    for u, label in entries:
        needle = "@" + label
        start = 0
        while True:
            i = "".join(chars).find(needle, start)
            if i < 0:
                break
            end = i + len(needle)
            before_ok = i == 0 or not (low[i - 1].isalnum())
            after_ok = end >= len(low) or not (low[end].isalnum() or low[end] == "_")
            if before_ok and after_ok:
                if u not in found:
                    found.append(u)
                chars[i:end] = " " * (end - i)               # used up
            start = end
    return found


def scope_user_ids(user, scope):
    """None = every resident; a set = only those people. Returns False when the person may not use the scope."""
    kind, _, value = (scope or "").partition(":")
    if kind == "group" and value.isdigit():
        if not GroupMember.objects.filter(group_id=int(value), user=user).exists():
            return False
        return set(GroupMember.objects.filter(group_id=int(value)).values_list("user_id", flat=True))
    if kind == "community" and value.isdigit():
        c = Community.objects.filter(pk=int(value), members=user).first()
        if not c:
            return False
        return set(c.members.values_list("pk", flat=True))
    return None


@login_required
def mention_search(request):
    q = request.GET.get("q", "").strip().lower()[:40]
    allowed = scope_user_ids(request.user, request.GET.get("scope", ""))
    if allowed is False:
        return JsonResponse({"people": []})
    rows = []
    for u, name, house, shared in _residents(request.user):
        if u.pk == request.user.pk or (allowed is not None and u.pk not in allowed):
            continue
        hay = f"{name} {house}".lower()
        if q and q not in hay:
            continue
        starts = name.lower().startswith(q) or any(w.startswith(q) for w in name.lower().split())
        prof = u.profile
        try:
            avatar = prof.avatar.url if prof.avatar else ""
        except Exception:
            avatar = ""
        rows.append((0 if starts else 1, name.lower(), {
            "id": u.pk, "name": name, "house": house, "unit": u.house_label, "label": label_for(name, house, shared),
            "initial": (name[:1] or "?").upper(), "avatar": avatar, "online": prof.is_online}))
    rows.sort(key=lambda r: (r[0], r[1]))
    return JsonResponse({"people": [r[2] for r in rows[:8]]})


@login_required
def mention_names(request):
    """Every taggable name, so the page can highlight @mentions in posts and chats."""
    def build():
        labels = []
        for _u, name, house, shared in _residents(request.user):
            labels.append(label_for(name, house, shared))
        return labels
    key = "mention-names-" + ",".join(str(i) for i in sorted(my_env_ids(request.user)))     # one list per set of networks
    return JsonResponse({"names": cache.get_or_set(key, build, 60), "me": _full_name(request.user)})


# --------------------------------------------------------------------------
# Event pokes
# --------------------------------------------------------------------------
def _poke_event_or_error(user, pk):
    """(announcement, error). Only someone who is going to an upcoming event can poke."""
    n = get_object_or_404(visible_announcements(user), pk=pk, category=Announcement.Category.EVENT)
    if not n.is_upcoming:
        return n, "This event has already started."
    mine = AnnouncementResponse.objects.filter(announcement=n, user=user).first()
    if not mine or mine.answer != AnnouncementResponse.Answer.GOING:
        return n, "Tap “I'll come” first, then you can poke neighbours."
    return n, ""


@login_required
def poke_candidates(request, pk):
    n, error = _poke_event_or_error(request.user, pk)
    if error:
        return JsonResponse({"error": error}, status=403)
    q = request.GET.get("q", "").strip()[:40]
    answered = AnnouncementResponse.objects.filter(announcement=n).values_list("user_id", flat=True)
    people = (users_in_envs(n.environments.values_list("pk", flat=True)).filter(pk__in=visible_users(request.user).values("pk"))
              .exclude(pk=request.user.pk).exclude(pk__in=answered).select_related("profile").order_by("first_name", "last_name"))
    if q:
        people = people.filter(Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(profile__house_number__icontains=q))
    recent = set(EventPoke.objects.filter(announcement=n, sender=request.user, poked_at__gte=timezone.now() - POKE_COOLDOWN)
                 .values_list("target_id", flat=True))
    rows = []
    for u in people[:40]:
        if not _full_name(u):
            continue
        rows.append({"id": u.pk, "name": _full_name(u), "house": u.profile.house_number or "", "unit": u.house_label, "poked": u.pk in recent})
    return JsonResponse({"people": rows, "total": people.count()})


@login_required
@require_POST
def poke(request, pk):
    from .views import notify                              # imported here: views.py imports this module too
    n, error = _poke_event_or_error(request.user, pk)
    if error:
        return JsonResponse({"error": error}, status=403)
    tid = request.POST.get("target", "")
    target = (users_in_envs(n.environments.values_list("pk", flat=True)).filter(pk=int(tid), pk__in=visible_users(request.user).values("pk")).first()
              if tid.isdigit() else None)
    if not target or target.pk == request.user.pk:
        return JsonResponse({"error": "Pick a neighbour to poke."}, status=400)
    if AnnouncementResponse.objects.filter(announcement=n, user=target).exists():
        return JsonResponse({"error": f"{_full_name(target) or 'They'} already replied."}, status=400)
    now = timezone.now()
    if EventPoke.objects.filter(sender=request.user, poked_at__gte=now - timedelta(days=1)).count() >= POKES_PER_DAY:
        return JsonResponse({"error": "That's a lot of pokes for today. Try again tomorrow."}, status=429)
    existing = EventPoke.objects.filter(announcement=n, sender=request.user, target=target).first()
    if existing and existing.poked_at > now - POKE_COOLDOWN:
        return JsonResponse({"error": f"You already poked {_full_name(target)}. Give them a little time."}, status=400)
    if existing:
        existing.poked_at = now
        existing.save(update_fields=["poked_at"])
    else:
        EventPoke.objects.create(announcement=n, sender=request.user, target=target, poked_at=now)
    me = request.user.display_name
    notify(target, f"{me} poked you \U0001F449", f"{me} is going to “{n.title}”. Are you coming too?",
           reverse("announcements") + f"#notice-{n.pk}", Notification.Kind.POKE, request.user)
    return JsonResponse({"ok": True, "message": f"Poked {_full_name(target)}"})
