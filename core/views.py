import logging
import re
import json
from decimal import Decimal

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, Exists, IntegerField, Max, OuterRef, Prefetch, Q, Subquery
from django.db.models.functions import Coalesce
from django.http import JsonResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from . import live, push
from .navigation import nav_context
from .models import (Announcement, AnnouncementResponse, ChatGroup, Comment, Community, CommunityEvent, EventPoke,
                     CommunityMember, CommunityPost, GameChallenge, GameChatMessage, GameParticipant, GameSession, GroupMember, Message, Notification, Payment, PaymentRequest, Post,
                     TypingIndicator,
                     PushSubscription, ResidentProfile)

User = get_user_model()
logger = logging.getLogger(__name__)


def group_memberships(user):
    """My group memberships, each annotated with .unread (messages from others newer than last_read)."""
    unread = (
        Message.objects.filter(group=OuterRef("group_id"), id__gt=OuterRef("last_read"))
        .exclude(sender=user).order_by().values("group").annotate(c=Count("id")).values("c")
    )
    return (
        GroupMember.objects.filter(user=user).select_related("group")
        .annotate(unread=Coalesce(Subquery(unread, output_field=IntegerField()), 0))
    )


# --------------------------------------------------------------------------
# Context processor (registered in settings): runs for every template
# --------------------------------------------------------------------------
def nav(request):
    ctx = {"SOCIETY_NAME": settings.SOCIETY_NAME, "VAPID_PUBLIC_KEY": settings.VAPID_PUBLIC_KEY,
           "PUSH_CONFIGURED": bool(settings.VAPID_PUBLIC_KEY and settings.VAPID_PRIVATE_KEY)}
    if request.user.is_authenticated:
        dms = Message.objects.filter(recipient=request.user, is_read=False).count()
        ctx["unread_dms"] = dms + sum(m.unread for m in group_memberships(request.user))
        ctx["unread_notifications"] = Notification.objects.filter(recipient=request.user, is_read=False).count()
        ctx["latest_notification_id"] = Notification.objects.filter(recipient=request.user).aggregate(m=Max("id"))["m"] or 0
        ctx.update(nav_context(request.resolver_match.url_name if request.resolver_match else ""))
    return ctx


def notify(user, title, body="", url="", kind=Notification.Kind.SYSTEM, actor=None):
    """Create the in-app notification (it appears live in any open tab) and push it to the user's phones."""
    if user and user != actor:
        n = Notification.objects.create(recipient=user, actor=actor, kind=kind, title=title, body=body, url=url)
        chat = kind == Notification.Kind.MESSAGE
        # messages from one chat share a tag, so the phone shows "Rahul (3 new messages)" instead of a pile
        push.send([user.pk], title, body, url or reverse("notifications"),
                  tag=f"chat:{url}" if chat and url else f"n{n.pk}", collapse=chat, kind=kind)


def notify_mentions(text, actor, url, title="You were mentioned", only_user_ids=None):
    """Notify everyone tagged with @Name. `only_user_ids` limits it to the people who can see the place (a group, a community)."""
    from .social import resolve_mentions
    for user in resolve_mentions(text, only_user_ids):
        notify(user, title, f"{actor.display_name} mentioned you.", url, Notification.Kind.MENTION, actor)


# --------------------------------------------------------------------------
# Forms
# --------------------------------------------------------------------------
def style(form):
    """Give every widget Bootstrap's form-control class."""
    for f in form.fields.values():
        if not isinstance(f.widget, forms.CheckboxInput):
            f.widget.attrs.setdefault("class", "form-control")
    return form


def check_image(f):
    if f and f.size > settings.MAX_UPLOAD_MB * 1024 * 1024:
        raise ValidationError(f"Image is too large (max {settings.MAX_UPLOAD_MB} MB).")
    return f


class WardLoginForm(AuthenticationForm):
    """Login by mobile number; shows a friendly message to not-yet-approved residents."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].label = "Mobile number"
        self.fields["username"].widget.attrs.update(
            {"inputmode": "numeric", "autocomplete": "username", "placeholder": "10-digit mobile number"}
        )
        style(self)

    def clean(self):
        username = self.cleaned_data.get("username")
        password = self.cleaned_data.get("password")
        if username and password:
            user = User.objects.filter(username=username).first()
            if user and not user.is_active and user.check_password(password):
                raise ValidationError(
                    "Your registration is waiting for approval by the committee. "
                    "You'll be able to log in once it is verified.",
                    code="pending",
                )
        return super().clean()


class RegisterForm(forms.Form):
    full_name = forms.CharField(max_length=60, widget=forms.TextInput(attrs={"placeholder": "e.g. Ramesh Sharma"}))
    house_number = forms.CharField(max_length=10, widget=forms.TextInput(attrs={"placeholder": "e.g. A-42"}))
    mobile = forms.CharField(
        label="Mobile number", max_length=10,
        widget=forms.TextInput(attrs={"inputmode": "numeric", "placeholder": "10-digit number (your login ID)"}),
    )
    password1 = forms.CharField(label="Password", widget=forms.PasswordInput)
    password2 = forms.CharField(label="Confirm password", widget=forms.PasswordInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        style(self)

    def clean_full_name(self):
        return " ".join(self.cleaned_data["full_name"].split())

    def clean_house_number(self):
        return self.cleaned_data["house_number"].strip().upper()

    def clean_mobile(self):
        m = self.cleaned_data["mobile"].strip()
        if not re.fullmatch(r"[6-9]\d{9}", m):
            raise ValidationError("Enter a valid 10-digit Indian mobile number.")
        if User.objects.filter(username=m).exists():
            raise ValidationError("This number is already registered. Try logging in.")
        return m

    def clean(self):
        data = super().clean()
        p1, p2 = data.get("password1"), data.get("password2")
        if p1 and p2:
            if p1 != p2:
                self.add_error("password2", "Passwords don't match.")
            else:
                try:
                    validate_password(p1)
                except ValidationError as e:
                    self.add_error("password1", e)
        return data


class PostForm(forms.ModelForm):
    class Meta:
        model = Post
        fields = ["body", "image"]
        widgets = {
            "body": forms.Textarea(attrs={"rows": 2, "placeholder": "Share something with your neighbours…",
                                          "class": "form-control", "data-mention": "all"}),
            "image": forms.FileInput(attrs={"accept": "image/*", "class": "d-none", "id": "post-image"}),
        }

    def clean_image(self):
        return check_image(self.cleaned_data.get("image"))

    def clean_body(self):
        body = self.cleaned_data["body"].strip()
        if not body:
            raise ValidationError("Write something first.")
        return body


class ProfileForm(forms.ModelForm):
    first_name = forms.CharField(max_length=60)
    last_name = forms.CharField(max_length=60, required=False)

    class Meta:
        model = ResidentProfile
        fields = ["bio", "gender", "date_of_birth", "pronouns", "occupation", "hometown", "interests", "avatar"]
        widgets = {
            "avatar": forms.FileInput(attrs={"accept": "image/*"}),
            "bio": forms.TextInput(attrs={"placeholder": "A line about you (optional)"}),
            "date_of_birth": forms.DateInput(attrs={"type": "date"}),
            "pronouns": forms.TextInput(attrs={"placeholder": "e.g. she/her (optional)"}),
            "occupation": forms.TextInput(attrs={"placeholder": "What do you do?"}),
            "hometown": forms.TextInput(attrs={"placeholder": "Where are you from?"}),
            "interests": forms.TextInput(attrs={"placeholder": "e.g. cricket, cooking, books"}),
        }

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields["first_name"].initial = user.first_name
        self.fields["last_name"].initial = user.last_name
        self.order_fields(["first_name", "last_name", "bio", "gender", "date_of_birth", "pronouns",
                           "occupation", "hometown", "interests", "avatar"])
        style(self)

    def clean_date_of_birth(self):
        value = self.cleaned_data.get("date_of_birth")
        if value and value > timezone.localdate():
            raise ValidationError("Date of birth cannot be in the future.")
        return value

    def clean_avatar(self):
        return check_image(self.cleaned_data.get("avatar"))

    def save(self, commit=True):
        self.user.first_name = self.cleaned_data["first_name"]
        self.user.last_name = self.cleaned_data["last_name"]
        self.user.save(update_fields=["first_name", "last_name"])
        return super().save(commit)


class PaymentForm(forms.Form):
    txn_id = forms.CharField(max_length=40)
    amount = forms.DecimalField(max_digits=9, decimal_places=2, min_value=Decimal("1"), required=False)

    def __init__(self, *args, fixed_amount=None, **kwargs):
        self.fixed_amount = fixed_amount
        super().__init__(*args, **kwargs)

    def clean_txn_id(self):
        t = re.sub(r"\s+", "", self.cleaned_data["txn_id"]).upper()
        if not re.fullmatch(r"[A-Z0-9]{8,35}", t):
            raise ValidationError("Enter the transaction ID (UTR) exactly as your UPI app shows it: letters and digits only.")
        if Payment.objects.filter(txn_id=t).exists():
            raise ValidationError("That transaction ID has already been submitted.")
        return t

    def clean(self):
        data = super().clean()
        if self.fixed_amount is None and not data.get("amount"):
            self.add_error("amount", "Enter the amount you paid.")
        return data


AUDIO_EXTS = {"webm", "ogg", "oga", "mp3", "m4a", "mp4", "wav", "aac"}


class MessageForm(forms.Form):
    """A chat message: text, or one photo, or one voice note (text may caption a photo)."""
    body = forms.CharField(max_length=1000, required=False)
    image = forms.ImageField(required=False)
    document = forms.FileField(required=False)
    audio = forms.FileField(required=False)

    def clean_body(self):
        return self.cleaned_data["body"].strip()

    def clean_image(self):
        return check_image(self.cleaned_data.get("image"))

    def clean_audio(self):
        f = self.cleaned_data.get("audio")
        if f:
            ext = f.name.rsplit(".", 1)[-1].lower() if "." in f.name else ""
            ctype = (f.content_type or "").lower()
            if ext not in AUDIO_EXTS or not ctype.startswith(("audio/", "video/webm", "video/mp4")):
                raise ValidationError("That voice note format isn't supported.")
            check_image(f)  # same size cap
        return f

    def clean(self):
        d = super().clean()
        if sum(bool(d.get(k)) for k in ("image", "document", "audio")) > 1:
            raise ValidationError("Send one attachment at a time.")
        if not self.errors and not (d.get("body") or d.get("image") or d.get("document") or d.get("audio")):
            raise ValidationError("Type a message or attach something.")
        return d


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def back(request, anchor=""):
    """Redirect to the page the form was submitted from (safe), jumping to an anchor.
    Requests made by the page's own JavaScript (header X-Requested-With: fetch) just get a small OK instead."""
    if request.headers.get("x-requested-with") == "fetch":
        return JsonResponse({"ok": True})
    nxt = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        nxt = reverse("feed")
    return redirect(f"{nxt}#{anchor}" if anchor else nxt)


def fmt_time(dt):
    dt = timezone.localtime(dt)
    return f"{dt.day} {dt:%b}, {dt.hour % 12 or 12}:{dt:%M} {dt:%p}"


def resident_or_404(user_id):
    return get_object_or_404(User, pk=user_id, is_active=True, profile__isnull=False)


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------
def register(request):
    if request.user.is_authenticated:
        return redirect("feed")
    form = RegisterForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        first, _, last = d["full_name"].partition(" ")
        with transaction.atomic():
            user = User(username=d["mobile"], first_name=first, last_name=last, is_active=False)
            user.set_password(d["password1"])
            user.save()
            ResidentProfile.objects.create(user=user, house_number=d["house_number"])
        messages.success(
            request,
            "Registration received! The committee will verify your house and activate your account. "
            "Then you can log in here.",
        )
        return redirect("login")
    return render(request, "auth.html", {"form": form, "mode": "register"})


# --------------------------------------------------------------------------
# Town Square
# --------------------------------------------------------------------------
@login_required
def feed(request):
    if request.method == "POST":
        form = PostForm(request.POST, request.FILES)
        if form.is_valid():
            post = form.save(commit=False)
            post.author = request.user
            post.save()
            notify_mentions(post.body, request.user, reverse("feed") + f"#post-{post.pk}", "You were mentioned on Town Square")
            messages.success(request, "Posted to the Town Square.")
            return redirect("feed")
    else:
        form = PostForm()

    page = Paginator(feed_posts(request.user), 10).get_page(request.GET.get("page"))
    return render(request, "feed.html", {
        "form": form,
        "page": page,
        "next_url": request.get_full_path(),
        "latest_notice": Announcement.objects.first(),  # pinned first, then newest
    })


def feed_posts(user):
    return (
        Post.objects.filter(is_hidden=False)
        .select_related("author__profile")
        .prefetch_related(
            Prefetch("comments", queryset=Comment.objects.filter(is_hidden=False).select_related("author__profile"))
        )
        .annotate(
            like_count=Count("likes", distinct=True),
            liked=Exists(Post.likes.through.objects.filter(post_id=OuterRef("pk"), user_id=user.pk)),
        )
        .order_by("-created_at")  # annotate(Count) drops Meta.ordering
    )


@login_required
def feed_live(request):
    """What changed in the Town Square since this tab last looked.
    ?after=<newest post id on screen> (first page only) -> new posts;
    ?st=<id>:<comments>:<likes>:<liked>,... for the posts on screen -> the ones whose numbers changed or that were hidden."""
    nxt = request.GET.get("next", "")
    if not url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        nxt = reverse("feed")

    def html(post):
        return render_to_string("_post.html", {"post": post, "next_url": nxt}, request=request)

    posts = feed_posts(request.user)
    out = {"new": [], "upd": [], "gone": []}
    after = request.GET.get("after", "")
    if after.isdigit():
        newest = list(posts.filter(id__gt=int(after))[:20])
        out["new"] = [{"id": p.id, "html": html(p)} for p in reversed(newest)]  # oldest first, so the newest lands on top
    state = {}
    for part in request.GET.get("st", "").split(",")[:40]:
        bits = part.split(":")
        if len(bits) == 4 and all(b.isdigit() for b in bits):
            state[int(bits[0])] = tuple(int(b) for b in bits[1:])
    if state:
        seen = set()
        for p in posts.filter(id__in=list(state)):
            seen.add(p.id)
            if (len(p.comments.all()), p.like_count, int(p.liked)) != state[p.id]:
                out["upd"].append({"id": p.id, "html": html(p)})
        out["gone"] = [i for i in state if i not in seen]
    return JsonResponse(out)


@login_required
@require_POST
def like(request, pk):
    post = get_object_or_404(Post, pk=pk, is_hidden=False)
    if post.likes.filter(pk=request.user.pk).exists():
        post.likes.remove(request.user)
    else:
        post.likes.add(request.user)
    return back(request, f"post-{pk}")


@login_required
@require_POST
def comment(request, pk):
    post = get_object_or_404(Post, pk=pk, is_hidden=False)
    body = request.POST.get("body", "").strip()[:500]
    if body:
        Comment.objects.create(post=post, author=request.user, body=body)
        if post.author_id != request.user.id:
            notify(post.author, f"{request.user.display_name} commented on your post", body[:120],
                   reverse("feed") + f"#post-{pk}", Notification.Kind.TOWN_SQUARE, request.user)
        notify_mentions(body, request.user, reverse("feed") + f"#post-{pk}")
    return back(request, f"post-{pk}")


@login_required
@require_POST
def delete_post(request, pk):
    post = get_object_or_404(Post, pk=pk)
    if post.author_id == request.user.pk or request.user.is_staff:
        post.delete()
        messages.success(request, "Post deleted.")
    return back(request)


# --------------------------------------------------------------------------
# Notices, directory, profile
# --------------------------------------------------------------------------
@login_required
def announcements(request):
    A = AnnouncementResponse.Answer
    notices = list(
        Announcement.objects.annotate(
            n_going=Count("responses", filter=Q(responses__answer=A.GOING)),
            n_no=Count("responses", filter=Q(responses__answer=A.NOT_GOING)),
            n_ack=Count("responses", filter=Q(responses__answer=A.ACK)),
        )
        .prefetch_related(
            Prefetch("responses", to_attr="going_list",
                     queryset=AnnouncementResponse.objects.filter(answer=A.GOING).select_related("user__profile")),
            Prefetch("payment_requests", queryset=PaymentRequest.objects.filter(is_open=True)),
        )
        .order_by("-is_pinned", "-created_at")
    )
    mine = dict(AnnouncementResponse.objects.filter(user=request.user).values_list("announcement_id", "answer"))
    pokers = {}
    for pk_, sender in EventPoke.objects.filter(target=request.user).select_related("sender").order_by("poked_at").values_list("announcement_id", "sender__first_name"):
        pokers.setdefault(pk_, []).append(sender)
    for n in notices:
        n.my = mine.get(n.id)  # what I answered, or None
        n.pokers = [] if n.my else pokers.get(n.id, [])             # neighbours who poked me and are still waiting for my answer
        n.can_poke = n.category == Announcement.Category.EVENT and n.is_upcoming and n.my == A.GOING
    return render(request, "announcements.html", {"notices": notices})


@login_required
@require_POST
def respond(request, pk):
    """Event RSVP (going / can't make it) or rule acknowledgement. Pressing the same button again reverts it."""
    A = AnnouncementResponse.Answer
    n = get_object_or_404(Announcement, pk=pk)
    allowed = {Announcement.Category.EVENT: {A.GOING, A.NOT_GOING}, Announcement.Category.RULE: {A.ACK}}.get(n.category, set())
    answer = request.POST.get("answer")
    if answer not in allowed:
        return back(request, f"notice-{pk}")
    if not n.is_upcoming:
        messages.warning(request, "This event has already started, so RSVPs are closed.")
        return back(request, f"notice-{pk}")
    existing = AnnouncementResponse.objects.filter(announcement=n, user=request.user).first()
    if existing and existing.answer == answer:
        existing.delete()  # the revert
    else:
        AnnouncementResponse.objects.update_or_create(announcement=n, user=request.user, defaults={"answer": answer})
    return back(request, f"notice-{pk}")


# --------------------------------------------------------------------------
# Payments (manual UPI: pay in your app, submit the transaction ID, admin approves)
# --------------------------------------------------------------------------
@login_required
def payments(request):
    mine = list(Payment.objects.filter(payer=request.user).select_related("request"))
    by_request = {}
    for p in mine:
        by_request.setdefault(p.request_id, []).append(p)
    collections = list(PaymentRequest.objects.filter(is_open=True).select_related("announcement"))
    for c in collections:
        c.mine = by_request.get(c.id, [])
        c.settled = c.amount is not None and any(p.status == Payment.Status.APPROVED for p in c.mine)
        c.awaiting = any(p.status == Payment.Status.PENDING for p in c.mine)
    return render(request, "payments.html", {"collections": collections, "history": mine})


@login_required
@require_POST
def pay(request, pk):
    pr = get_object_or_404(PaymentRequest, pk=pk, is_open=True)
    if pr.amount is not None and Payment.objects.filter(
            request=pr, payer=request.user, status=Payment.Status.APPROVED).exists():
        messages.info(request, "You've already paid this one. Thank you!")
        return redirect("payments")
    form = PaymentForm(request.POST, fixed_amount=pr.amount)
    if form.is_valid():
        try:
            Payment.objects.create(
                request=pr, payer=request.user, txn_id=form.cleaned_data["txn_id"],
                amount=pr.amount if pr.amount is not None else form.cleaned_data["amount"],
            )
        except IntegrityError:
            messages.error(request, "That transaction ID has already been submitted.")
        else:
            messages.success(request, "Thanks! Your payment is submitted. The committee will verify it against the "
                                      "bank statement and approve it soon.")
    else:
        messages.error(request, " ".join(e for errs in form.errors.values() for e in errs))
    return redirect("payments")


@login_required
def directory(request):
    q = request.GET.get("q", "").strip()
    residents = (
        User.objects.filter(is_active=True, profile__isnull=False)
        .select_related("profile")
        .order_by("profile__house_number", "first_name")
    )
    if q:
        residents = residents.filter(
            Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(profile__house_number__icontains=q)
        )
    return render(request, "directory.html", {"residents": residents, "q": q})


@login_required
def profile(request):
    prof, _ = ResidentProfile.objects.get_or_create(user=request.user, defaults={"house_number": "Admin"})
    form = ProfileForm(request.POST or None, request.FILES or None, instance=prof, user=request.user)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                form.save()
        except Exception:
            logger.exception("Profile update failed for user %s", request.user.pk)
            form.add_error("avatar", "We couldn't upload that image right now. Check the image format and try again.")
            messages.error(request, "Profile saved except for the image. Please try the upload again.")
        else:
            messages.success(request, "Profile updated.")
            return redirect("profile")
    return render(request, "profile.html", {
        "form": form,
        "my_posts": Post.objects.filter(author=request.user).count(),
    })


def _music_count(resident):
    """How many playlists this resident has chosen to share with neighbours (0 when they have not opted in)."""
    from .music import _shared_playlists
    return _shared_playlists(resident)[1].count()


@login_required
def public_profile(request, user_id):
    resident = resident_or_404(user_id)
    profile = resident.profile
    back_url = request.GET.get("next", "")
    if not url_has_allowed_host_and_scheme(back_url, allowed_hosts={request.get_host()}):
        back_url = reverse("directory")
    return render(request, "public_profile.html", {
        "resident": resident,
        "profile": profile,
        "back_url": back_url,
        "post_count": Post.objects.filter(author=resident, is_hidden=False).count(),
        "recent_posts": Post.objects.filter(author=resident, is_hidden=False).order_by("-created_at")[:5],
        "music_count": _music_count(resident),
    })


# --------------------------------------------------------------------------
# Chat: direct messages + groups (database-backed; the page polls every few seconds)
# --------------------------------------------------------------------------
def residents_except(me):
    return (User.objects.filter(is_active=True, profile__isnull=False).exclude(pk=me.pk)
            .select_related("profile").order_by("first_name"))


def preview_text(m, me, is_group=False):
    if not m:
        return "No messages yet"
    if m.sender_id == me.id:
        who = "You: "
    elif is_group:
        who = f"{m.sender.first_name or m.sender.username}: "
    else:
        who = ""
    return who + m.preview


def threads(me):
    """Everything in my chat list (people and groups), newest activity first."""
    items = []
    msgs = (
        Message.objects.filter(group__isnull=True)
        .filter(Q(sender=me) | Q(recipient=me))
        .select_related("sender__profile", "recipient__profile")
        .order_by("-id")[:500]
    )
    seen = {}
    for m in msgs:
        other = m.recipient if m.sender_id == me.id else m.sender
        t = seen.setdefault(other.id, {"user": other, "last": m, "unread": 0})
        if m.recipient_id == me.id and not m.is_read:
            t["unread"] += 1
    for t in seen.values():
        u = t["user"]
        items.append({"key": f"dm-{u.pk}", "url": reverse("conversation", args=[u.pk]), "title": u.display_name,
                      "chip": u.house, "avatar": u.avatar_html, "unread": t["unread"], "sort": t["last"].id,
                      "preview": preview_text(t["last"], me), "online": u.profile.is_online})

    memberships = list(group_memberships(me))
    last_by_group = {}
    if memberships:
        recent = (Message.objects.filter(group_id__in=[ms.group_id for ms in memberships])
                  .select_related("sender").order_by("-id")[:500])
        for m in recent:
            last_by_group.setdefault(m.group_id, m)
    for ms in memberships:
        g, last = ms.group, last_by_group.get(ms.group_id)
        online_count = sum(member.user.profile.is_online for member in g.memberships.select_related("user__profile"))
        items.append({"key": f"g-{g.pk}", "url": reverse("group", args=[g.pk]), "title": g.name, "chip": "",
                      "avatar": g.avatar_html, "unread": ms.unread, "is_group": True,
                      "sort": last.id if last else 10 ** 12,  # brand-new empty groups float to the top
                      "preview": preview_text(last, me, is_group=True), "online_count": online_count})
    items.sort(key=lambda t: t["sort"], reverse=True)
    return items


def chat_base(me):
    # peer/group default to None so messages.html can always test them; the open chat's view overrides one of them
    return {"threads": threads(me), "residents": residents_except(me), "peer": None, "group": None}


def thread_qs(me, other):
    return Message.objects.filter(group__isnull=True).filter(
        Q(sender=me, recipient=other) | Q(sender=other, recipient=me))


def typing_users(thread_key, me):
    cutoff = timezone.now() - timezone.timedelta(seconds=5)
    return list(TypingIndicator.objects.filter(thread_key=thread_key, updated_at__gte=cutoff)
                .exclude(user=me).select_related("user"))


def typing_payload(thread_key, me):
    return [{"name": indicator.user.display_name} for indicator in typing_users(thread_key, me)]


def thread_key_for_dm(me, other):
    return f"dm-{min(me.pk, other.pk)}-{max(me.pk, other.pk)}"


def short_time(dt):
    dt = timezone.localtime(dt)
    t = f"{dt.hour % 12 or 12}:{dt:%M} {dt:%p}"
    return t if dt.date() == timezone.localdate() else f"{dt.day} {dt:%b}, {t}"


def clear_chat_alerts(me, url):
    """Opening a chat also clears its 'new message' alerts, so the bell badge matches what you've actually read."""
    if Notification.objects.filter(recipient=me, kind=Notification.Kind.MESSAGE, url=url, is_read=False).update(is_read=True):
        live.bump(live.user_key(me.pk, "n"))


def mark_dm_read(me, other):
    """I'm looking at my chat with `other`: mark their messages as seen (with the time) and tell both sides' tabs."""
    n = Message.objects.filter(group__isnull=True, sender=other, recipient=me, is_read=False).update(
        is_read=True, read_at=timezone.now())
    clear_chat_alerts(me, reverse("conversation", args=[other.pk]))
    if n:
        live.bump(live.user_key(other.pk, "c"), live.user_key(me.pk, "c"))  # "Seen" for them, badges for my other tabs
    return n


def dm_receipt(me, other):
    """Under my newest message: Sent / Seen. Only when that message is the last one in the chat (like Instagram)."""
    last = thread_qs(me, other).order_by("-id").only("id", "sender_id", "is_read", "read_at").first()
    if not last or last.sender_id != me.id:
        return None
    return {"id": last.id, "seen": last.is_read, "at": short_time(last.read_at) if last.read_at else ""}


def group_receipt(me, group):
    last = Message.objects.filter(group=group).order_by("-id").only("id", "sender_id").first()
    if not last or last.sender_id != me.id:
        return None
    others = GroupMember.objects.filter(group=group).exclude(user=me)
    seen = list(others.filter(last_read__gte=last.id).select_related("user").order_by("user__first_name"))
    return {"id": last.id, "seen_by": [m.user.first_name or m.user.username for m in seen], "total": others.count()}


def bump_group(group_id):
    live.bump(*[live.user_key(uid, "c") for uid in
                GroupMember.objects.filter(group_id=group_id).values_list("user_id", flat=True)])


def as_json(m, me):
    return {
        "id": m.id, "mine": m.sender_id == me.id, "body": m.body, "time": fmt_time(m.created_at),
        "sender": m.sender.first_name or m.sender.username,
        "image": m.image.url if m.image else "", "document": m.document.url if m.document else "",
        "audio": m.audio.url if m.audio else "",
    }


def post_message(request, **target):
    """Shared by DM and group send. Returns a JsonResponse."""
    form = MessageForm(request.POST, request.FILES)
    if not form.is_valid():
        err = " ".join(e for errs in form.errors.values() for e in errs)
        return JsonResponse({"error": err}, status=400)
    d = form.cleaned_data
    if target.get("recipient"):
        TypingIndicator.objects.filter(thread_key=thread_key_for_dm(request.user, target["recipient"]), user=request.user).delete()
    elif target.get("group"):
        TypingIndicator.objects.filter(thread_key=f"group-{target['group'].pk}", user=request.user).delete()
    m = Message.objects.create(sender=request.user, body=d["body"], image=d["image"] or "",
                               document=d["document"] or "", audio=d["audio"] or "", **target)
    if m.recipient_id:
        notify(m.recipient, f"New message from {request.user.display_name}", m.preview,
               reverse("conversation", args=[request.user.pk]), Notification.Kind.MESSAGE, request.user)
    else:
        for member in m.group.members.exclude(pk=request.user.pk):
            notify(member, f"New message in {m.group.name}", m.preview,
                   reverse("group", args=[m.group_id]), Notification.Kind.MESSAGE, request.user)
    if m.group_id:
        notify_mentions(m.body, request.user, reverse("group", args=[m.group_id]), f"You were mentioned in {m.group.name}",
                        set(m.group.members.values_list("pk", flat=True)))
    receipt = dm_receipt(request.user, m.recipient) if m.recipient_id else group_receipt(request.user, m.group)
    return JsonResponse({"messages": [as_json(m, request.user)], "receipt": receipt})


def after_param(request):
    try:
        return int(request.GET.get("after", 0))
    except ValueError:
        return 0


@login_required
def inbox(request):
    return render(request, "messages.html", {**chat_base(request.user), "active_key": None})


@login_required
def threads_partial(request):
    """Just the conversation list, so an open chat page can refresh it the moment something arrives."""
    return render(request, "_threads.html", {"threads": threads(request.user), "active_key": request.GET.get("active", "")})


# ---- 1-on-1 ----
@login_required
def conversation(request, user_id):
    me = request.user
    other = resident_or_404(user_id)
    if other.pk == me.pk:
        return redirect("inbox")
    mark_dm_read(me, other)
    chat = list(thread_qs(me, other).select_related("sender").order_by("-id")[:100])[::-1]
    return render(request, "messages.html", {
        **chat_base(me), "active_key": f"dm-{other.pk}", "peer": other, "chat": chat,
        "last_id": chat[-1].id if chat else 0, "api": reverse("conversation_api", args=[other.pk]),
        "receipt": dm_receipt(me, other),
    })


@login_required
def conversation_api(request, user_id):
    me = request.user
    other = resident_or_404(user_id)
    if other.pk == me.pk:
        return JsonResponse({"error": "no"}, status=400)
    if request.method == "POST":
        if "typing" in request.POST:
            key = thread_key_for_dm(me, other)
            if request.POST.get("typing") == "1":
                TypingIndicator.objects.update_or_create(thread_key=key, user=me)
            else:
                TypingIndicator.objects.filter(thread_key=key, user=me).delete()
            live.bump(live.user_key(other.pk, "c"))  # their tab shows / clears "typing…" right away
            return JsonResponse({"typing": typing_payload(key, me), "online": other.profile.is_online})
        return post_message(request, recipient=other)
    new = list(thread_qs(me, other).filter(id__gt=after_param(request)).select_related("sender").order_by("id"))
    mark_dm_read(me, other)
    key = thread_key_for_dm(me, other)
    return JsonResponse({"messages": [as_json(m, me) for m in new], "typing": typing_payload(key, me),
                         "online": other.profile.is_online, "receipt": dm_receipt(me, other)})


# ---- groups ----
def my_group_or_404(user, pk):
    return get_object_or_404(ChatGroup, pk=pk, memberships__user=user)


def newest_id(group):
    last = Message.objects.filter(group=group).order_by("-id").values_list("id", flat=True).first()
    return last or 0


@login_required
@require_POST
def group_new(request):
    me = request.user
    name = " ".join(request.POST.get("name", "").split())[:60]
    ids = [int(i) for i in request.POST.getlist("members") if i.isdigit()]
    people = list(residents_except(me).filter(pk__in=ids))
    if not name or not people:
        messages.error(request, "Give the group a name and pick at least one neighbour.")
        return redirect("inbox")
    with transaction.atomic():
        group = ChatGroup.objects.create(name=name, created_by=me)
        GroupMember.objects.bulk_create([GroupMember(group=group, user=u) for u in [me, *people]])
    return redirect("group", pk=group.pk)


@login_required
def group_chat(request, pk):
    me = request.user
    group = my_group_or_404(me, pk)
    chat = list(Message.objects.filter(group=group).select_related("sender__profile").order_by("-id")[:100])[::-1]
    last_id = chat[-1].id if chat else 0
    if GroupMember.objects.filter(group=group, user=me, last_read__lt=last_id).update(last_read=last_id):
        bump_group(group.pk)  # "Seen by …" for the senders, badges for my other tabs
    clear_chat_alerts(me, reverse("group", args=[group.pk]))
    can_manage = group.created_by_id == me.id
    return render(request, "messages.html", {
        **chat_base(me), "active_key": f"g-{group.pk}", "group": group, "chat": chat, "last_id": last_id,
        "api": reverse("group_api", args=[group.pk]), "receipt": group_receipt(me, group),
        "members": group.memberships.select_related("user__profile").order_by("user__first_name"),
        "online_count": sum(member.user.profile.is_online for member in group.memberships.select_related("user__profile")),
        "can_manage": can_manage,
        "addable": residents_except(me).exclude(chat_groups=group) if can_manage else [],
    })


@login_required
def group_api(request, pk):
    me = request.user
    group = my_group_or_404(me, pk)
    if request.method == "POST":
        if "typing" in request.POST:
            key = f"group-{group.pk}"
            if request.POST.get("typing") == "1":
                TypingIndicator.objects.update_or_create(thread_key=key, user=me)
            else:
                TypingIndicator.objects.filter(thread_key=key, user=me).delete()
            live.bump(*[live.user_key(uid, "c") for uid in group.memberships.exclude(user=me).values_list("user_id", flat=True)])
            return JsonResponse({"typing": typing_payload(key, me)})
        return post_message(request, group=group)
    new = list(Message.objects.filter(group=group, id__gt=after_param(request)).select_related("sender").order_by("id"))
    if new and GroupMember.objects.filter(group=group, user=me, last_read__lt=new[-1].id).update(last_read=new[-1].id):
        bump_group(group.pk)
    if new:
        clear_chat_alerts(me, reverse("group", args=[group.pk]))
    key = f"group-{group.pk}"
    return JsonResponse({"messages": [as_json(m, me) for m in new], "typing": typing_payload(key, me),
                         "receipt": group_receipt(me, group)})


@login_required
@require_POST
def group_add(request, pk):
    group = my_group_or_404(request.user, pk)
    if group.created_by_id != request.user.id:
        messages.error(request, "Only the person who made the group can add people.")
        return redirect("group", pk=pk)
    ids = [int(i) for i in request.POST.getlist("members") if i.isdigit()]
    start = newest_id(group)  # newcomers don't get a pile of "unread" for old chatter
    for u in residents_except(request.user).filter(pk__in=ids):
        GroupMember.objects.get_or_create(group=group, user=u, defaults={"last_read": start})
    return redirect("group", pk=pk)


@login_required
@require_POST
def group_leave(request, pk):
    group = my_group_or_404(request.user, pk)
    GroupMember.objects.filter(group=group, user=request.user).delete()
    if not group.memberships.exists():
        group.delete()
    messages.success(request, f"You left {group.name}.")
    return redirect("inbox")


# --------------------------------------------------------------------------
# Notifications, communities, games, and browser push registration
# --------------------------------------------------------------------------
@login_required
def notifications(request):
    items = list(Notification.objects.filter(recipient=request.user).select_related("actor")[:100])
    if Notification.objects.filter(recipient=request.user, is_read=False).update(is_read=True):
        live.bump(live.user_key(request.user.pk, "n"))  # badges in my other tabs
    keys_ok, keys_message = push.check_keys()
    return render(request, "notifications.html", {"notifications": items, "push_keys_ok": keys_ok,
                                                   "push_keys_message": keys_message})


@login_required
@require_POST
def notifications_read_all(request):
    if Notification.objects.filter(recipient=request.user, is_read=False).update(is_read=True):
        live.bump(live.user_key(request.user.pk, "n"))
    return JsonResponse({"ok": True})


@login_required
@require_POST
def notification_read(request, pk):
    if Notification.objects.filter(pk=pk, recipient=request.user, is_read=False).update(is_read=True):
        live.bump(live.user_key(request.user.pk, "n"))
    return redirect(request.POST.get("next") or "notifications")


@login_required
def communities(request):
    mine = Community.objects.filter(members=request.user).prefetch_related("memberships__user")
    discover = Community.objects.exclude(members=request.user).order_by("-created_at")[:50]
    return render(request, "communities.html", {"mine": mine, "discover": discover})


@login_required
@require_POST
def community_new(request):
    name = " ".join(request.POST.get("name", "").split())[:80]
    if not name:
        messages.error(request, "Give your community a name.")
        return redirect("communities")
    community = Community.objects.create(name=name, description=request.POST.get("description", "").strip()[:500],
                                         created_by=request.user)
    CommunityMember.objects.create(community=community, user=request.user)
    return redirect("communities")


@login_required
@require_POST
def community_join(request, pk):
    community = get_object_or_404(Community, pk=pk)
    CommunityMember.objects.get_or_create(community=community, user=request.user)
    notify(community.created_by, f"{request.user.display_name} joined {community.name}",
           url=reverse("communities"), kind=Notification.Kind.COMMUNITY, actor=request.user)
    return redirect("communities")


@login_required
def community_detail(request, pk):
    community = get_object_or_404(Community, pk=pk, members=request.user)
    return render(request, "community.html", {"community": community, "posts": community.posts.select_related("author")[:50],
                                                "events": community.events.select_related("created_by")[:20]})


@login_required
@require_POST
def community_post(request, pk):
    community = get_object_or_404(Community, pk=pk, members=request.user)
    body = request.POST.get("body", "").strip()[:2000]
    if body:
        post = CommunityPost.objects.create(community=community, author=request.user, body=body)
        notify_mentions(body, request.user, reverse("community_detail", args=[pk]), f"You were mentioned in {community.name}",
                        set(community.members.values_list("pk", flat=True)))
        for member in community.members.exclude(pk=request.user.pk):
            notify(member, f"New post in {community.name}", body[:120], reverse("community_detail", args=[pk]),
                   Notification.Kind.COMMUNITY, request.user)
    return redirect("community_detail", pk=pk)


@login_required
@require_POST
def community_event(request, pk):
    community = get_object_or_404(Community, pk=pk, members=request.user)
    try:
        when = timezone.datetime.fromisoformat(request.POST.get("event_date", "")).replace(tzinfo=timezone.get_current_timezone())
    except (TypeError, ValueError):
        messages.error(request, "Enter a valid event date and time.")
        return redirect("community_detail", pk=pk)
    CommunityEvent.objects.create(community=community, created_by=request.user,
                                  title=request.POST.get("title", "").strip()[:150],
                                  details=request.POST.get("details", "").strip(), event_date=when)
    return redirect("community_detail", pk=pk)


@login_required
@require_POST
def presence(request):
    ResidentProfile.objects.filter(user=request.user).update(last_seen_at=timezone.now())
    return JsonResponse({"online": True})


def json_body(request):
    try:
        return json.loads(request.body or b"{}")
    except ValueError:
        return {}


@login_required
@require_POST
def push_subscribe(request):
    """Save (or re-assign) this device. If the phone was last used by someone else, it now belongs to whoever is logged in."""
    if not push.enabled():
        return JsonResponse({"ok": False, "error": "Push notifications are not configured on the server."}, status=503)
    data = json_body(request) if request.content_type == "application/json" else request.POST
    endpoint = data.get("endpoint") or ""
    keys = data.get("keys") or {}
    if not endpoint.startswith("https://") or len(endpoint) > 500 or not keys.get("p256dh") or not keys.get("auth"):
        return JsonResponse({"ok": False, "error": "Incomplete push subscription."}, status=400)
    subscription, _ = PushSubscription.objects.update_or_create(endpoint=endpoint, defaults={
        "user": request.user, "p256dh": keys["p256dh"][:200], "auth": keys["auth"][:200]})
    return JsonResponse({"ok": True, "subscription_id": subscription.pk})


@login_required
@require_POST
def push_unsubscribe(request):
    """Detach this device from my account (called on log out, so my alerts don't keep popping up on a shared phone)."""
    PushSubscription.objects.filter(user=request.user, endpoint=json_body(request).get("endpoint", "")).delete()
    return JsonResponse({"ok": True})


@login_required
@require_POST
def push_test(request):
    return JsonResponse(push.send_test(request.user))


def service_worker(request):
    """Served from the site root so it can receive pushes for every page."""
    resp = render(request, "sw.js", content_type="application/javascript")
    resp["Service-Worker-Allowed"] = "/"
    resp["Cache-Control"] = "no-cache"
    return resp
