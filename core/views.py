import re
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
from django.db.models import Count, Exists, IntegerField, OuterRef, Prefetch, Q, Subquery
from django.db.models.functions import Coalesce
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .models import (Announcement, AnnouncementResponse, ChatGroup, Comment, GroupMember, Message, Payment,
                     PaymentRequest, Post, ResidentProfile)

User = get_user_model()


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
    ctx = {"SOCIETY_NAME": settings.SOCIETY_NAME}
    if request.user.is_authenticated:
        dms = Message.objects.filter(recipient=request.user, is_read=False).count()
        ctx["unread_dms"] = dms + sum(m.unread for m in group_memberships(request.user))
    return ctx


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
        fields = ["bio", "avatar"]
        widgets = {"avatar": forms.FileInput(attrs={"accept": "image/*"}),
                   "bio": forms.TextInput(attrs={"placeholder": "A line about you (optional)"})}

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields["first_name"].initial = user.first_name
        self.fields["last_name"].initial = user.last_name
        self.order_fields(["first_name", "last_name", "bio", "avatar"])
        style(self)

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
        if d.get("image") and d.get("audio"):
            raise ValidationError("Send one attachment at a time.")
        if not self.errors and not (d.get("body") or d.get("image") or d.get("audio")):
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
        form.save()
        messages.success(request, "Profile updated.")
        return redirect("profile")
    return render(request, "profile.html", {
        "form": form,
        "my_posts": Post.objects.filter(author=request.user).count(),
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
                      "preview": preview_text(t["last"], me)})

    memberships = list(group_memberships(me))
    last_by_group = {}
    if memberships:
        recent = (Message.objects.filter(group_id__in=[ms.group_id for ms in memberships])
                  .select_related("sender").order_by("-id")[:500])
        for m in recent:
            last_by_group.setdefault(m.group_id, m)
    for ms in memberships:
        g, last = ms.group, last_by_group.get(ms.group_id)
        items.append({"key": f"g-{g.pk}", "url": reverse("group", args=[g.pk]), "title": g.name, "chip": "",
                      "avatar": g.avatar_html, "unread": ms.unread, "is_group": True,
                      "sort": last.id if last else 10 ** 12,  # brand-new empty groups float to the top
                      "preview": preview_text(last, me, is_group=True)})
    items.sort(key=lambda t: t["sort"], reverse=True)
    return items


def chat_base(me):
    # peer/group default to None so messages.html can always test them; the open chat's view overrides one of them
    return {"threads": threads(me), "residents": residents_except(me), "peer": None, "group": None}


def thread_qs(me, other):
    return Message.objects.filter(group__isnull=True).filter(
        Q(sender=me, recipient=other) | Q(sender=other, recipient=me))


def as_json(m, me):
    return {
        "id": m.id, "mine": m.sender_id == me.id, "body": m.body, "time": fmt_time(m.created_at),
        "sender": m.sender.first_name or m.sender.username,
        "image": m.image.url if m.image else "", "audio": m.audio.url if m.audio else "",
    }


def post_message(request, **target):
    """Shared by DM and group send. Returns a JsonResponse."""
    form = MessageForm(request.POST, request.FILES)
    if not form.is_valid():
        err = " ".join(e for errs in form.errors.values() for e in errs)
        return JsonResponse({"error": err}, status=400)
    d = form.cleaned_data
    m = Message.objects.create(sender=request.user, body=d["body"], image=d["image"] or "",
                               audio=d["audio"] or "", **target)
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
        return post_message(request, recipient=other)
    new = list(thread_qs(me, other).filter(id__gt=after_param(request)).select_related("sender").order_by("id"))
    Message.objects.filter(group__isnull=True, sender=other, recipient=me, is_read=False).update(is_read=True)
    return JsonResponse({"messages": [as_json(m, me) for m in new]})


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
        "can_manage": can_manage,
        "addable": residents_except(me).exclude(chat_groups=group) if can_manage else [],
    })


@login_required
def group_api(request, pk):
    me = request.user
    group = my_group_or_404(me, pk)
    if request.method == "POST":
        return post_message(request, group=group)
    new = list(Message.objects.filter(group=group, id__gt=after_param(request)).select_related("sender").order_by("id"))
    if new:
        GroupMember.objects.filter(group=group, user=me, last_read__lt=new[-1].id).update(last_read=new[-1].id)
    return JsonResponse({"messages": [as_json(m, me) for m in new]})


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
