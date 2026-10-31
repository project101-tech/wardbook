"""Music Taste: discover neighbours' shared Spotify playlists, listen with Spotify's embedded player, manage your own.

Privacy rules, in one place:
  * Nothing is visible until the resident switches "Share my music" on (MusicProfile.is_shared, off by default).
  * Even then, only playlists marked visible are shown. Hidden ones are never sent to anyone else's browser.
  * Every change is a POST that looks the playlist up with user=request.user, so other people's rows are simply "not found".
"""
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, Max, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme, urlencode
from django.views.decorators.http import require_POST

from . import spotify
from .models import MusicPlaylist, MusicProfile

User = get_user_model()
from .environments import visible_users  # noqa: E402
PAGE = 24


def _max_playlists():
    return getattr(settings, "MUSIC_MAX_PLAYLISTS", 20)


def _sandbox():
    v = getattr(settings, "SPOTIFY_EMBED_SANDBOX", "")
    return "" if v.strip() == "-" else v


def _residents(viewer):
    """Neighbours the viewer may see (people in a network they are in)."""
    return visible_users(viewer)


def _shared_playlists(user):
    """The playlists neighbours may see: the master switch is on AND the playlist itself is visible."""
    mp = MusicProfile.objects.filter(user=user).first()
    if not mp or not mp.is_shared:
        return mp, MusicPlaylist.objects.none()
    return mp, MusicPlaylist.objects.filter(user=user, is_visible=True)


# --------------------------------------------------------------------------
# Discover
# --------------------------------------------------------------------------
@login_required
def music_home(request):
    q = request.GET.get("q", "").strip()[:60]
    people = (_residents(request.user).filter(music_profile__is_shared=True).exclude(pk=request.user.pk).select_related("profile")
              .annotate(shared_count=Count("music_playlists", filter=Q(music_playlists__is_visible=True)))
              .filter(shared_count__gt=0))
    for term in q.split():
        people = people.filter(Q(first_name__icontains=term) | Q(last_name__icontains=term)
                               | Q(profile__house_number__icontains=term))
    page = Paginator(people.order_by("first_name", "last_name", "pk"), PAGE).get_page(request.GET.get("page"))
    ids = [u.pk for u in page.object_list]
    covers = {}
    for pl in MusicPlaylist.objects.filter(user_id__in=ids, is_visible=True).exclude(cover_url="").order_by("user_id", "position", "id"):
        if len(covers.setdefault(pl.user_id, [])) < 3:
            covers[pl.user_id].append(pl.cover_url)
    for u in page.object_list:
        u.covers = covers.get(u.pk, [])
    mine = MusicProfile.objects.filter(user=request.user).first()
    back_to = reverse("music") + ("?" + urlencode({k: v for k, v in (("q", q), ("page", page.number if page.number > 1 else "")) if v}) if q or page.number > 1 else "")
    ctx = {"page": page, "q": q, "back_to": back_to, "total_sharing": page.paginator.count,
           "my_count": MusicPlaylist.objects.filter(user=request.user).count(), "my_shared": bool(mine and mine.is_shared)}
    if request.GET.get("partial"):
        return render(request, "_music_results.html", ctx)
    return render(request, "music.html", ctx)


# --------------------------------------------------------------------------
# A resident's music profile
# --------------------------------------------------------------------------
@login_required
def music_profile(request, user_id):
    resident = get_object_or_404(_residents(request.user).select_related("profile"), pk=user_id)
    mp, playlists = _shared_playlists(resident)
    playlists = list(playlists)
    is_me = resident.pk == request.user.pk
    back_url = request.GET.get("next", "")
    if not back_url.startswith("/") or back_url.startswith("//") or not url_has_allowed_host_and_scheme(back_url, allowed_hosts={request.get_host()}):
        back_url = ""                      # only a plain path on this site, never a relative or outside address
    return render(request, "music_profile.html", {
        "resident": resident, "mp": mp if playlists or is_me else None, "playlists": playlists, "is_me": is_me,
        "my_hidden": is_me and not (mp and mp.is_shared), "back_url": back_url, "sandbox": _sandbox(),
    })


# --------------------------------------------------------------------------
# Manage my own music
# --------------------------------------------------------------------------
class SettingsForm(forms.ModelForm):
    class Meta:
        model = MusicProfile
        fields = ["is_shared", "about", "favourites"]
        widgets = {
            "about": forms.TextInput(attrs={"class": "form-control", "maxlength": 280, "placeholder": "e.g. Old Bollywood on Sundays, lo-fi when I work"}),
            "favourites": forms.TextInput(attrs={"class": "form-control", "maxlength": 250, "placeholder": "Favourite artists or tracks"}),
        }


class AddForm(forms.Form):
    url = forms.CharField(max_length=300, widget=forms.TextInput(attrs={
        "id": "mm-url", "class": "form-control", "placeholder": "https://open.spotify.com/playlist/…", "autocomplete": "off", "inputmode": "url"}))
    description = forms.CharField(max_length=300, required=False, widget=forms.Textarea(attrs={
        "class": "form-control", "rows": 2, "maxlength": 300, "placeholder": "Why you love it (optional)"}))
    is_visible = forms.BooleanField(required=False, initial=True)

    def clean_url(self):
        try:
            self.spotify_id = spotify.parse_playlist(self.cleaned_data["url"])
        except spotify.LinkError as e:
            raise forms.ValidationError(str(e))
        return self.cleaned_data["url"]


class EditForm(forms.Form):
    title = forms.CharField(max_length=200, required=False)
    description = forms.CharField(max_length=300, required=False)
    is_visible = forms.BooleanField(required=False)


def _renumber(user):
    rows = list(MusicPlaylist.objects.filter(user=user).order_by("position", "id"))
    changed = []
    for i, r in enumerate(rows):
        if r.position != i:
            r.position = i
            changed.append(r)
    if changed:
        MusicPlaylist.objects.bulk_update(changed, ["position"])
    return rows


def _manage_ctx(request, add_form=None):
    mp, _ = MusicProfile.objects.get_or_create(user=request.user)
    playlists = list(MusicPlaylist.objects.filter(user=request.user))
    return {"mp": mp, "settings_form": SettingsForm(instance=mp), "add_form": add_form or AddForm(),
            "playlists": playlists, "visible_count": sum(1 for p in playlists if p.is_visible),
            "limit": _max_playlists(), "full": len(playlists) >= _max_playlists()}


@login_required
def music_manage(request):
    return render(request, "music_manage.html", _manage_ctx(request))


@login_required
@require_POST
def music_settings(request):
    mp, _ = MusicProfile.objects.get_or_create(user=request.user)
    form = SettingsForm(request.POST, instance=mp)
    if form.is_valid():
        form.save()
        messages.success(request, "Your music is now shown to neighbours." if mp.is_shared
                         else "Saved. Your music is private: neighbours can't see it.")
    else:
        messages.error(request, "Couldn't save those settings. Check the text lengths and try again.")
    return redirect("music_manage")


def _apply_meta(pl, meta):
    pl.meta_status = meta["status"]
    pl.checked_at = timezone.now()
    if meta["title"] and not pl.title:
        pl.title = meta["title"]
    if meta["cover_url"]:
        pl.cover_url = meta["cover_url"]


@login_required
@require_POST
def music_add(request):
    form = AddForm(request.POST)
    if not form.is_valid():
        return render(request, "music_manage.html", _manage_ctx(request, form), status=400)
    ctx_full = MusicPlaylist.objects.filter(user=request.user).count() >= _max_playlists()
    if ctx_full:
        form.add_error("url", f"You can share up to {_max_playlists()} playlists. Remove one to add another.")
        return render(request, "music_manage.html", _manage_ctx(request, form), status=400)
    if MusicPlaylist.objects.filter(user=request.user, spotify_id=form.spotify_id).exists():
        form.add_error("url", "You've already added that playlist.")
        return render(request, "music_manage.html", _manage_ctx(request, form), status=400)
    MusicProfile.objects.get_or_create(user=request.user)
    pl = MusicPlaylist(user=request.user, spotify_id=form.spotify_id, description=form.cleaned_data["description"].strip(),
                       is_visible=form.cleaned_data["is_visible"])
    _apply_meta(pl, spotify.fetch_meta(form.spotify_id))
    try:
        with transaction.atomic():
            pl.position = (MusicPlaylist.objects.filter(user=request.user).aggregate(m=Max("position"))["m"] or -1) + 1
            pl.save()
    except IntegrityError:
        form.add_error("url", "You've already added that playlist.")
        return render(request, "music_manage.html", _manage_ctx(request, form), status=400)
    if pl.is_unavailable:
        messages.warning(request, "Added, but Spotify couldn't find that playlist. It may be private or deleted: "
                                  "neighbours won't be able to play it until it's public.")
    else:
        messages.success(request, "Playlist added.")
    return redirect("music_manage")


def _mine_or_404(request, pk):
    return get_object_or_404(MusicPlaylist, pk=pk, user=request.user)


@login_required
@require_POST
def music_edit(request, pk):
    pl = _mine_or_404(request, pk)
    form = EditForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Couldn't save that playlist. Titles can be 200 characters, notes 300.")
        return redirect("music_manage")
    pl.title = form.cleaned_data["title"].strip()
    pl.description = form.cleaned_data["description"].strip()
    pl.is_visible = form.cleaned_data["is_visible"]
    pl.save()
    messages.success(request, "Playlist updated.")
    return redirect("music_manage")


@login_required
@require_POST
def music_toggle(request, pk):
    pl = _mine_or_404(request, pk)
    pl.is_visible = not pl.is_visible
    pl.save(update_fields=["is_visible", "updated_at"])
    messages.success(request, "Playlist is now visible to neighbours." if pl.is_visible else "Playlist hidden from neighbours.")
    return redirect("music_manage")


@login_required
@require_POST
def music_delete(request, pk):
    pl = _mine_or_404(request, pk)
    pl.delete()
    _renumber(request.user)
    messages.success(request, "Playlist removed.")
    return redirect("music_manage")


@login_required
@require_POST
def music_move(request, pk):
    pl = _mine_or_404(request, pk)
    direction = request.POST.get("dir")
    if direction not in ("up", "down"):
        raise Http404
    with transaction.atomic():
        rows = _renumber(request.user)
        i = next(n for n, r in enumerate(rows) if r.pk == pl.pk)
        j = i - 1 if direction == "up" else i + 1
        if 0 <= j < len(rows):
            rows[i].position, rows[j].position = rows[j].position, rows[i].position
            MusicPlaylist.objects.bulk_update([rows[i], rows[j]], ["position"])
    return redirect("music_manage")


@login_required
@require_POST
def music_refresh(request, pk):
    pl = _mine_or_404(request, pk)
    meta = spotify.fetch_meta(pl.spotify_id)
    _apply_meta(pl, meta)
    pl.save()
    if meta["status"] == "ok":
        messages.success(request, "Playlist details refreshed from Spotify.")
    elif meta["status"] == "unavailable":
        messages.warning(request, "Spotify still can't find that playlist. It may be private or deleted.")
    else:
        messages.info(request, "Couldn't reach Spotify just now. Try again in a minute.")
    return redirect("music_manage")


@login_required
def music_preview(request):
    """Preview a pasted link before saving. Spotify is asked about the validated playlist id only, never the raw text."""
    key = f"music-preview:{request.user.pk}"
    n = cache.get(key, 0)
    if n >= 30:
        return JsonResponse({"ok": False, "message": "Slow down a little, then try again in a minute."}, status=429)
    cache.set(key, n + 1, 60)
    try:
        sid = spotify.parse_playlist(request.GET.get("url", ""))
    except spotify.LinkError as e:
        return JsonResponse({"ok": False, "message": str(e)}, status=400)
    meta = spotify.fetch_meta(sid)
    dup = MusicPlaylist.objects.filter(user=request.user, spotify_id=sid).exists()
    return JsonResponse({"ok": True, "id": sid, "url": spotify.canonical_url(sid), "embed": spotify.embed_url(sid),
                         "title": meta["title"], "cover": meta["cover_url"], "status": meta["status"], "duplicate": dup})
