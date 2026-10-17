import logging
import re
import json
from decimal import Decimal
from .realtime import send_user_event
from .realtime import send_user_event

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
from django.db.models import Count, Exists, IntegerField, OuterRef, Prefetch, Q, Subquery
from django.db.models.functions import Coalesce
from django.http import JsonResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .models import (Announcement, AnnouncementResponse, ChatGroup, Comment, Community, CommunityEvent,
                     CommunityMember, CommunityPost, GameChallenge, GameChatMessage, GameParticipant, GameSession, GroupMember, Message, Notification, Payment, PaymentRequest, Post,
                     TypingIndicator,
                     PushSubscription, ResidentProfile)

User = get_user_model()
logger = logging.getLogger(__name__)
AVAILABLE_GAME_VALUES = {
    GameChallenge.Game.TIC_TAC_TOE,
    GameChallenge.Game.CONNECT_FOUR,
    GameChallenge.Game.SNAKES_LADDERS,
}


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
    return ctx


def notify(user, title, body="", url="", kind=Notification.Kind.SYSTEM, actor=None):
    if user and user != actor:
        Notification.objects.create(recipient=user, actor=actor, kind=kind, title=title, body=body, url=url)
        if settings.VAPID_PRIVATE_KEY and settings.VAPID_PUBLIC_KEY:
            from pywebpush import WebPushException, webpush
            for subscription in user.push_subscriptions.all():
                try:
                    webpush({"endpoint": subscription.endpoint, "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth}},
                            json.dumps({"title": title, "body": body, "url": url}),
                            vapid_private_key=settings.VAPID_PRIVATE_KEY,
                            vapid_claims={"sub": settings.VAPID_CLAIMS_EMAIL}, ttl=86400, urgency="high")
                except WebPushException as exc:
                    response = getattr(exc, "response", None)
                    if response is not None and response.status_code in (404, 410):
                        subscription.delete()
                    logger.exception("Web Push delivery failed for user %s", user.pk)
                except Exception:
                    logger.exception("Unexpected Web Push failure for user %s", user.pk)


def notify_mentions(text, actor, url, title="You were mentioned"):
    handles = set(re.findall(r"@([A-Za-z0-9_.-]+)", text or ""))
    for user in User.objects.filter(username__in=handles, is_active=True):
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
                                          "class": "form-control"}),
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
    """Redirect to the page the form was submitted from (safe), jumping to an anchor."""
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

    posts = (
        Post.objects.filter(is_hidden=False)
        .select_related("author__profile")
        .prefetch_related(
            Prefetch("comments", queryset=Comment.objects.filter(is_hidden=False).select_related("author__profile"))
        )
        .annotate(
            like_count=Count("likes", distinct=True),
            liked=Exists(Post.likes.through.objects.filter(post_id=OuterRef("pk"), user_id=request.user.pk)),
        )
        .order_by("-created_at")  # annotate(Count) drops Meta.ordering
    )
    page = Paginator(posts, 10).get_page(request.GET.get("page"))
    return render(request, "feed.html", {
        "form": form,
        "page": page,
        "latest_notice": Announcement.objects.first(),  # pinned first, then newest
    })


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
    for n in notices:
        n.my = mine.get(n.id)  # what I answered, or None
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
    
    m = Message.objects.create(
        sender=request.user,
        body=d["body"],
        image=d["image"] or "",
        document=d["document"] or "",
        audio=d["audio"] or "",
        **target,
    )

    # Notify the recipient(s) after the database commit.
    payload = {
        "type": "new_message",
        "id": m.id,
        "sender_id": request.user.id,
        "recipient_id": m.recipient_id,
        "group_id": m.group_id,
        "body": m.body,
        "preview": m.preview,
        "time": fmt_time(m.created_at),
        "image": m.image.url if m.image else "",
        "document": m.document.url if m.document else "",
        "audio": m.audio.url if m.audio else "",
        "sender": m.sender.display_name
    }

    if m.recipient_id:
        recipient_ids = [m.recipient_id]
    else:
        recipient_ids = list(
            m.group.members.exclude(pk=request.user.pk)
            .values_list("pk", flat=True)
        )

    def publish_message():
        for user_id in set(recipient_ids):
            send_user_event(user_id, payload)

    transaction.on_commit(publish_message)

    if m.recipient_id:
        notify(
            m.recipient,
            f"New message from {request.user.display_name}",
            m.preview,
            reverse("conversation", args=[request.user.pk]),
            Notification.Kind.MESSAGE,
            request.user,
        )
    else:
        for member in m.group.members.exclude(pk=request.user.pk):
            notify(
                member,
                f"New message in {m.group.name}",
                m.preview,
                reverse("group", args=[m.group_id]),
                Notification.Kind.MESSAGE,
                request.user,
            )

    notify_mentions(
        m.body,
        request.user,
        reverse("conversation", args=[m.recipient_id])
        if m.recipient_id else reverse("group", args=[m.group_id]),
    )

    return JsonResponse({"messages": [as_json(m, request.user)]})



def after_param(request):
    try:
        return int(request.GET.get("after", 0))
    except ValueError:
        return 0


@login_required
def inbox(request):
    return render(request, "messages.html", {**chat_base(request.user), "active_key": None})


# ---- 1-on-1 ----
@login_required
def conversation(request, user_id):
    me = request.user
    other = resident_or_404(user_id)
    if other.pk == me.pk:
        return redirect("inbox")
    Message.objects.filter(group__isnull=True, sender=other, recipient=me, is_read=False).update(is_read=True)
    chat = list(thread_qs(me, other).select_related("sender").order_by("-id")[:100])[::-1]
    return render(request, "messages.html", {
        **chat_base(me), "active_key": f"dm-{other.pk}", "peer": other, "chat": chat,
        "last_id": chat[-1].id if chat else 0, "api": reverse("conversation_api", args=[other.pk]),
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
            return JsonResponse({"typing": typing_payload(key, me), "online": other.profile.is_online})
        return post_message(request, recipient=other)
    new = list(thread_qs(me, other).filter(id__gt=after_param(request)).select_related("sender").order_by("id"))
    Message.objects.filter(group__isnull=True, sender=other, recipient=me, is_read=False).update(is_read=True)
    key = thread_key_for_dm(me, other)
    return JsonResponse({"messages": [as_json(m, me) for m in new], "typing": typing_payload(key, me),
                         "online": other.profile.is_online})


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
    GroupMember.objects.filter(group=group, user=me, last_read__lt=last_id).update(last_read=last_id)
    can_manage = group.created_by_id == me.id
    return render(request, "messages.html", {
        **chat_base(me), "active_key": f"g-{group.pk}", "group": group, "chat": chat, "last_id": last_id,
        "api": reverse("group_api", args=[group.pk]),
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
            return JsonResponse({"typing": typing_payload(key, me)})
        return post_message(request, group=group)
    new = list(Message.objects.filter(group=group, id__gt=after_param(request)).select_related("sender").order_by("id"))
    if new:
        GroupMember.objects.filter(group=group, user=me, last_read__lt=new[-1].id).update(last_read=new[-1].id)
    key = f"group-{group.pk}"
    return JsonResponse({"messages": [as_json(m, me) for m in new], "typing": typing_payload(key, me)})


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
    items = Notification.objects.filter(recipient=request.user).select_related("actor")[:100]
    Notification.objects.filter(recipient=request.user, is_read=False).update(is_read=True)
    return render(request, "notifications.html", {"notifications": items})


@login_required
@require_POST
def notification_read(request, pk):
    Notification.objects.filter(pk=pk, recipient=request.user).update(is_read=True)
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
        notify_mentions(body, request.user, reverse("community_detail", args=[pk]), f"You were mentioned in {community.name}")
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
def games(request):
    received = GameChallenge.objects.filter(opponent=request.user, status=GameChallenge.Status.PENDING).select_related("challenger")
    received = received.filter(game__in=AVAILABLE_GAME_VALUES)
    mine = GameChallenge.objects.filter(Q(challenger=request.user) | Q(opponent=request.user),
                                         game__in=AVAILABLE_GAME_VALUES).select_related("challenger", "opponent")[:50]
    for challenge in mine:
        challenge.play_url = reverse("game_play", args=[challenge.session.pk]) if hasattr(challenge, "session") else ""
    return render(request, "games.html", {"received": received, "games": mine, "residents": residents_except(request.user)})


def initial_game_state(game):
    if game == GameChallenge.Game.TIC_TAC_TOE:
        return {"board": [""] * 9, "symbols": {}}
    if game == GameChallenge.Game.CONNECT_FOUR:
        return {"board": [[""] * 7 for _ in range(6)], "symbols": {}}
    if game == GameChallenge.Game.SNAKES_LADDERS:
        return {"positions": {}, "dice": None, "last_roll": None, "last_jump": None, "symbols": {}, "players": [],
                "snakes": {"99": 78, "95": 75, "92": 88, "64": 60, "62": 19, "54": 34, "17": 7},
                "ladders": {"4": 14, "9": 31, "20": 38, "28": 84, "40": 59, "51": 67, "63": 81, "71": 91}}
    return {"pieces": {"challenger": 9, "opponent": 9}, "struck": [], "symbols": {}}


def session_for_challenge(challenge):
    session, created = GameSession.objects.get_or_create(
        challenge=challenge,
        defaults={"state": initial_game_state(challenge.game), "current_turn": challenge.challenger},
    )
    if created:
        participants = list(challenge.participantships.select_related("user").order_by("seat"))
        if not participants:
            participants = [GameParticipant.objects.create(challenge=challenge, user_id=challenge.challenger_id, seat=0),
                            GameParticipant.objects.create(challenge=challenge, user_id=challenge.opponent_id, seat=1)]
        players = [p.user_id for p in participants]
        session.state["players"] = players
        marks = "XO" if challenge.game == GameChallenge.Game.TIC_TAC_TOE else "RB"
        session.state["symbols"] = {str(user_id): marks[index] for index, user_id in enumerate(players)}
        if challenge.game == GameChallenge.Game.SNAKES_LADDERS:
            session.state["positions"] = {str(user_id): 0 for user_id in players}
        session.save(update_fields=["state"])
    return session


@login_required
@require_POST
def game_challenge(request):
    opponent_ids = [value for value in request.POST.getlist("opponents") if value.isdigit()]
    if not opponent_ids and request.POST.get("opponent", "").isdigit():
        opponent_ids = [request.POST["opponent"]]
    opponent = get_object_or_404(User, pk=opponent_ids[0] if opponent_ids else 0, is_active=True)
    game = request.POST.get("game")
    all_opponents = list(residents_except(request.user).filter(pk__in=opponent_ids))
    if opponent == request.user or len(all_opponents) != len(set(opponent_ids)) or game not in GameChallenge.Game.values:
        messages.error(request, "Choose a neighbour and a valid game.")
        return redirect("games")
    if len(all_opponents) != 1:
        messages.error(request, "This game supports exactly two players.")
        return redirect("games")
    challenge = GameChallenge.objects.create(game=game, challenger=request.user, opponent=opponent,
                                              time_control=request.POST.get("time_control", "")[:40])
    GameParticipant.objects.create(challenge=challenge, user=request.user, seat=0)
    for seat, player in enumerate(all_opponents, start=1):
        GameParticipant.objects.create(challenge=challenge, user=player, seat=seat)
    notify(opponent, f"{request.user.display_name} challenged you to {challenge.get_game_display()}",
           "Choose a game and accept the invite.", reverse("games"), Notification.Kind.GAME, request.user)
    return redirect("games")


@login_required
@require_POST
def game_update(request, pk):
    challenge = get_object_or_404(GameChallenge, pk=pk, participants=request.user, status=GameChallenge.Status.PENDING)
    answer = request.POST.get("answer")
    if answer in (GameChallenge.Status.ACCEPTED, GameChallenge.Status.DECLINED):
        challenge.status = answer
        challenge.save(update_fields=["status"])
        if answer == GameChallenge.Status.ACCEPTED:
            session = session_for_challenge(challenge)
            return redirect("game_play", pk=session.pk)
        notify(challenge.challenger, f"{request.user.display_name} {answer} your game invite", url=reverse("games"),
               kind=Notification.Kind.GAME, actor=request.user)
    return redirect("games")


def game_access_or_404(user, pk):
    session = get_object_or_404(GameSession.objects.select_related("challenge", "current_turn"), pk=pk)
    challenge = session.challenge
    if challenge.game not in AVAILABLE_GAME_VALUES:
        from django.http import Http404
        raise Http404
    participant_ids = list(challenge.participantships.values_list("user_id", flat=True))
    if not participant_ids:
        participant_ids = [challenge.challenger_id, challenge.opponent_id]
    if user.id not in participant_ids:
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    return session


def game_payload(session, user):
    challenge = session.challenge
    if challenge.game == GameChallenge.Game.TIC_TAC_TOE:
        symbols = session.state.get("symbols", {})
        players = session.state.get("players", [challenge.challenger_id, challenge.opponent_id])
        if set(symbols.values()) != {"X", "O"}:
            session.state["symbols"] = {str(players[0]): "X", str(players[1]): "O"}
            if session.state.get("board"):
                session.state["board"] = [{"R": "X", "B": "O"}.get(mark, mark) for mark in session.state["board"]]
            session.save(update_fields=["state"])
    player_ids = session.state.get("players") or [challenge.challenger_id, challenge.opponent_id]
    opponents = User.objects.filter(pk__in=player_ids).exclude(pk=user.id).select_related("profile")
    opponent = next(iter(opponents), challenge.opponent if challenge.challenger_id == user.id else challenge.challenger)
    return {"game": challenge.game, "label": challenge.get_game_display(), "active": session.is_active,
            "state": session.state, "turn": session.current_turn_id, "you": user.id,
            "challenger": challenge.challenger.display_name, "opponent": challenge.opponent.display_name,
            "challenger_id": challenge.challenger_id, "opponent_id": challenge.opponent_id,
            "players": [{"id": p.id, "name": p.display_name, "online": p.profile.is_online}
                        for p in User.objects.filter(pk__in=player_ids).select_related("profile")],
            "winner": session.winner_id, "opponent_online": opponent.profile.is_online,
            "rematch_url": reverse("game_play", args=[session.rematch_session_id]) if session.rematch_session_id else ""}


@login_required
def game_play(request, pk):
    session = game_access_or_404(request.user, pk)
    return render(request, "game_play.html", {"session": session, "payload": game_payload(session, request.user)})


def ttt_winner(board):
    lines = ((0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6), (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6))
    for a, b, c in lines:
        if board[a] and board[a] == board[b] == board[c]:
            return board[a]
    return "draw" if all(board) else ""


def connect4_winner(board, row, col):
    token = board[row][col]
    for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
        count = 1
        for direction in (-1, 1):
            r, c = row + dr * direction, col + dc * direction
            while 0 <= r < 6 and 0 <= c < 7 and board[r][c] == token:
                count += 1; r += dr * direction; c += dc * direction
        if count >= 4:
            return token
    return "draw" if all(board[0]) else ""


def next_player_id(session, user_id):
    players = session.state.get("players") or [session.challenge.challenger_id, session.challenge.opponent_id]
    return players[(players.index(user_id) + 1) % len(players)]


@login_required
@require_POST
def game_move(request, pk):
    session = game_access_or_404(request.user, pk)
    if not session.is_active:
        return JsonResponse({"error": "This game is over.", "game": game_payload(session, request.user)}, status=400)
    if session.current_turn_id != request.user.id:
        return JsonResponse({"error": "Wait for the other player.", "game": game_payload(session, request.user)}, status=409)
    challenge, state = session.challenge, session.state
    symbol = state["symbols"][str(request.user.id)]
    try:
        move = int(request.POST.get("move", "-1"))
    except ValueError:
        move = -1
    winner = ""
    bonus_turn = False
    if challenge.game == GameChallenge.Game.TIC_TAC_TOE:
        if not 0 <= move < 9 or state["board"][move]:
            return JsonResponse({"error": "That square is unavailable."}, status=400)
        state["board"][move] = symbol; winner = ttt_winner(state["board"])
    elif challenge.game == GameChallenge.Game.CONNECT_FOUR:
        if not 0 <= move < 7:
            return JsonResponse({"error": "Choose a column."}, status=400)
        row = next((r for r in range(5, -1, -1) if not state["board"][r][move]), None)
        if row is None:
            return JsonResponse({"error": "That column is full."}, status=400)
        state["board"][row][move] = symbol; winner = connect4_winner(state["board"], row, move)
    elif challenge.game == GameChallenge.Game.SNAKES_LADDERS:
        if state["dice"] is None:
            import secrets
            state["dice"] = secrets.randbelow(6) + 1
            state["last_roll"] = state["dice"]
            state["last_jump"] = None
            state["rolled_by"] = request.user.id
            session.state = state; session.save(update_fields=["state", "updated_at"])
            return JsonResponse({"game": game_payload(session, request.user)})
        roll = state["dice"]
        old_position = state["positions"][str(request.user.id)]
        position = old_position + roll if old_position + roll <= 100 else old_position
        jump_type = "ladder" if str(position) in state["ladders"] else "snake" if str(position) in state["snakes"] else ""
        jumped_to = state["ladders"].get(str(position), state["snakes"].get(str(position), position))
        state["last_jump"] = {"from": position, "to": jumped_to, "type": jump_type} if jump_type else None
        position = jumped_to
        state["positions"][str(request.user.id)] = position
        state["dice"] = None
        winner = symbol if position == 100 else ""
        if not winner and roll == 6:
            bonus_turn = True
    else:
        return JsonResponse({"error": "Unknown game."}, status=400)
    if winner:
        session.is_active = False
        if winner != "draw":
            session.winner_id = request.user.id
            challenge.winner_id = request.user.id
        challenge.status = GameChallenge.Status.FINISHED
        challenge.save(update_fields=["winner", "status"])
    elif not bonus_turn:
        session.current_turn_id = next_player_id(session, request.user.id)
    session.state = state
    session.save(update_fields=["state", "current_turn", "is_active", "winner", "updated_at"])
    return JsonResponse({"game": game_payload(session, request.user)})


@login_required
def game_state(request, pk):
    session = game_access_or_404(request.user, pk)
    return JsonResponse({"game": game_payload(session, request.user)})


@login_required
def game_chat(request, pk):
    session = game_access_or_404(request.user, pk)
    if request.method == "POST":
        body = request.POST.get("body", "").strip()[:500]
        if not body:
            return JsonResponse({"error": "Write a message first."}, status=400)
        message = GameChatMessage.objects.create(session=session, sender=request.user, body=body)
    after = request.GET.get("after", "0")
    try:
        after = int(after)
    except ValueError:
        after = 0
    messages = session.chat_messages.filter(id__gt=after).select_related("sender")
    return JsonResponse({"messages": [{"id": m.id, "body": m.body, "mine": m.sender_id == request.user.id,
                                        "sender": m.sender.display_name, "time": fmt_time(m.created_at)} for m in messages]})


@login_required
@require_POST
def game_rematch(request, pk):
    old = game_access_or_404(request.user, pk)
    with transaction.atomic():
        old = GameSession.objects.select_for_update().select_related("challenge").get(pk=old.pk)
        if old.rematch_session_id:
            session = old.rematch_session
        else:
            challenge = old.challenge
            new_challenge = GameChallenge.objects.create(
                game=challenge.game, challenger=challenge.challenger, opponent=challenge.opponent,
                time_control=challenge.time_control, status=GameChallenge.Status.ACCEPTED,
            )
            for participant in challenge.participantships.select_related("user").order_by("seat"):
                GameParticipant.objects.create(challenge=new_challenge, user=participant.user, seat=participant.seat)
            session = session_for_challenge(new_challenge)
            old.rematch_session = session
            old.save(update_fields=["rematch_session"])
    challenge = session.challenge
    notify(challenge.opponent, f"{request.user.display_name} wants a rematch in {challenge.get_game_display()}",
           url=reverse("game_play", args=[session.pk]), kind=Notification.Kind.GAME, actor=request.user)
    return redirect("game_play", pk=session.pk)


@login_required
@require_POST
def presence(request):
    ResidentProfile.objects.filter(user=request.user).update(last_seen_at=timezone.now())
    return JsonResponse({"online": True})


@login_required
@require_POST
def push_subscribe(request):
    if not (settings.VAPID_PUBLIC_KEY and settings.VAPID_PRIVATE_KEY):
        return JsonResponse({"ok": False, "error": "Push notifications are not configured on the server."}, status=503)
    data = request.POST or request.body
    if isinstance(data, bytes):
        import json
        try:
            data = json.loads(data or "{}")
        except ValueError:
            return JsonResponse({"ok": False, "error": "Invalid push subscription."}, status=400)
    endpoint = data.get("endpoint")
    keys = data.get("keys", {})
    if not endpoint or not keys.get("p256dh") or not keys.get("auth"):
        return JsonResponse({"ok": False, "error": "Incomplete push subscription."}, status=400)
    subscription, _ = PushSubscription.objects.update_or_create(endpoint=endpoint, defaults={"user": request.user,
        "p256dh": keys["p256dh"], "auth": keys["auth"]})
    return JsonResponse({"ok": True, "subscription_id": subscription.pk})


def service_worker(request):
    return HttpResponse("""self.addEventListener('push', event => { let data = {}; try { data = event.data ? event.data.json() : {}; } catch (_) { data = {body: event.data ? event.data.text() : ''}; } event.waitUntil(self.registration.showNotification(data.title || 'Wardbook', { body: data.body || '', tag: data.tag || 'wardbook', renotify: true, vibrate: [200, 100, 200], data: { url: data.url || '/' } })); }); self.addEventListener('notificationclick', event => { event.notification.close(); event.waitUntil(clients.openWindow(event.notification.data.url)); });""", content_type="application/javascript")
