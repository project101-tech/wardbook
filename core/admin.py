from django import forms
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.db.models import Count, Q, Sum
from django.template.response import TemplateResponse
from django.utils import timezone

from . import environments as envs
from .models import (Environment, EnvironmentMembership, Announcement, AnnouncementResponse, ChatGroup, Comment, Community, CommunityEvent,
                     CommunityMember, GameChallenge, GroupMember, MusicPlaylist, MusicProfile, Notification, Payment, PaymentRequest, Post,
                     ResidentProfile, User)

admin.site.site_header = "Wardbook Control Room"
admin.site.site_title = "Wardbook"
admin.site.index_title = "Approvals, notices, payments and moderation"


class ProfileInline(admin.StackedInline):
    model = ResidentProfile
    can_delete = False


class MembershipInline(admin.TabularInline):
    """Which networks this person is in. Set Status to "Approved" to let them in (and tick Active above so they can log in)."""
    model = EnvironmentMembership
    extra = 0
    fields = ("environment", "role", "status")


@admin.register(User)
class WardUserAdmin(UserAdmin):
    inlines = [ProfileInline, MembershipInline]
    list_display = ("username", "get_full_name", "house", "networks", "is_active", "date_joined")
    list_editable = ("is_active",)  # tick the box to approve
    list_filter = ("is_active",)
    search_fields = ("username", "first_name", "last_name", "profile__house_number")
    ordering = ("is_active", "-date_joined")  # pending residents first
    list_select_related = ("profile",)
    actions = ["approve_selected"]
    add_fieldsets = (
        (None, {"classes": ("wide",),
                "fields": ("username", "first_name", "last_name", "password1", "password2", "is_active")}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("env_memberships__environment")

    @admin.display(description="House", ordering="profile__house_number")
    def house(self, obj):
        return obj.house or "-"

    @admin.display(description="Networks")
    def networks(self, obj):
        return ", ".join(f"{m.environment.name}{'' if m.status == 'approved' else ' (waiting)'}" for m in obj.env_memberships.all()) or "-"

    @admin.action(description="Approve selected residents (and the networks they asked for)")
    def approve_selected(self, request, queryset):
        users = list(queryset)
        n = queryset.update(is_active=True)
        EnvironmentMembership.objects.filter(user__in=users, status=EnvironmentMembership.Status.PENDING) \
            .update(status=EnvironmentMembership.Status.APPROVED, decided_at=timezone.now())
        self.message_user(request, f"{n} resident(s) approved.", messages.SUCCESS)


# --------------------------------------------------------------------------
# Networks (societies, schools ...) and who is in them
# --------------------------------------------------------------------------
@admin.register(Environment)
class EnvironmentAdmin(admin.ModelAdmin):
    """Create a network here (e.g. "DPS Noida"). People pick it when they register; you approve them under "Environment memberships"."""
    list_display = ("name", "unit_label", "is_active", "is_listed", "auto_approve", "join_code", "members", "waiting")
    list_editable = ("is_active",)
    search_fields = ("name",)
    fieldsets = (
        (None, {"fields": ("name", "description", "unit_label", "is_active")}),
        ("Who can join", {"fields": ("is_listed", "join_code", "auto_approve")}),
    )

    def get_queryset(self, request):
        a, p = EnvironmentMembership.Status.APPROVED, EnvironmentMembership.Status.PENDING
        return super().get_queryset(request).annotate(_in=Count("memberships", filter=Q(memberships__status=a)),
                                                      _wait=Count("memberships", filter=Q(memberships__status=p)))

    @admin.display(description="Members", ordering="_in")
    def members(self, obj):
        return obj._in

    @admin.display(description="Waiting", ordering="_wait")
    def waiting(self, obj):
        return obj._wait or ""


@admin.register(EnvironmentMembership)
class EnvironmentMembershipAdmin(admin.ModelAdmin):
    """Approve people into a network. Approving also activates their account if it was waiting."""
    list_display = ("user", "house", "status", "role", "environment", "requested_at")
    list_editable = ("role", "status")
    list_filter = ("status", "environment", "role")
    search_fields = ("user__username", "user__first_name", "user__last_name", "user__profile__house_number")
    list_select_related = ("user__profile", "environment")
    raw_id_fields = ("user",)
    actions = ["approve", "decline"]
    ordering = ("status", "-requested_at")             # waiting first

    @admin.display(description="House / class", ordering="user__profile__house_number")
    def house(self, obj):
        return obj.user.house_label or "-"

    def changelist_view(self, request, extra_context=None):
        """First show the environments; clicking one lists only its people (with the approve actions)."""
        picked = request.GET.get("environment__id__exact", "")
        if request.method == "GET" and not picked and not request.GET.get("all") and not request.GET.get("q"):
            a, p = EnvironmentMembership.Status.APPROVED, EnvironmentMembership.Status.PENDING
            rows = Environment.objects.annotate(members=Count("memberships", filter=Q(memberships__status=a)),
                                                waiting=Count("memberships", filter=Q(memberships__status=p))).order_by("name")
            return TemplateResponse(request, "admin/core/environmentmembership/pick_environment.html",
                                    {**self.admin_site.each_context(request), "title": "Environment memberships",
                                     "environments": rows, "opts": self.model._meta})
        extra_context = dict(extra_context or {})
        env = Environment.objects.filter(pk=picked).first() if picked.isdigit() else None
        if env:
            extra_context["title"] = f"{env.name}: people"
        return super().changelist_view(request, extra_context)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if obj.status == EnvironmentMembership.Status.APPROVED:
            envs.approve(obj)

    @admin.action(description="Approve selected (and activate their accounts)")
    def approve(self, request, queryset):
        n = 0
        for em in queryset.select_related("user"):
            envs.approve(em)
            n += 1
        self.message_user(request, f"{n} membership(s) approved.", messages.SUCCESS)

    @admin.action(description="Decline selected (removes the request)")
    def decline(self, request, queryset):
        n, _ = queryset.filter(status=EnvironmentMembership.Status.PENDING).delete()
        self.message_user(request, f"{n} request(s) declined.", messages.WARNING)


class NetworksRequiredForm(forms.ModelForm):
    """Notices, payments: must say which networks they are for (pre-ticked when there is only one)."""

    def clean_environments(self):
        chosen = self.cleaned_data.get("environments")
        if not chosen:
            raise forms.ValidationError("Pick at least one network.")
        return chosen

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "environments" in self.fields:
            self.fields["environments"].widget = forms.CheckboxSelectMultiple()
            self.fields["environments"].queryset = Environment.objects.filter(is_active=True)
            if not self.instance.pk:
                only = list(Environment.objects.filter(is_active=True)[:2])
                if len(only) == 1:
                    self.fields["environments"].initial = only


# --------------------------------------------------------------------------
# Notices, events, rules
# --------------------------------------------------------------------------
class ResponseInline(admin.TabularInline):
    """Read-only list of who RSVP'd / acknowledged."""
    model = AnnouncementResponse
    extra = 0
    can_delete = False
    readonly_fields = ("user", "answer", "updated_at")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    form = NetworksRequiredForm
    list_display = ("title", "category", "networks", "event_date", "is_pinned", "going", "not_going", "acknowledged", "created_at")
    list_filter = ("category", "is_pinned", "environments")
    search_fields = ("title", "body")
    inlines = [ResponseInline]
    fieldsets = (
        (None, {"fields": ("title", "body", "category", "is_pinned", "environments")}),
        ("Event details (category = Event)", {"fields": ("event_date", "venue")}),
    )

    @admin.display(description="Networks")
    def networks(self, obj):
        return ", ".join(e.name for e in obj.environments.all())

    def get_queryset(self, request):
        a = AnnouncementResponse.Answer
        return super().get_queryset(request).prefetch_related("environments").annotate(
            _going=Count("responses", filter=Q(responses__answer=a.GOING)),
            _no=Count("responses", filter=Q(responses__answer=a.NOT_GOING)),
            _ack=Count("responses", filter=Q(responses__answer=a.ACK)),
        )

    @admin.display(description="Going", ordering="_going")
    def going(self, obj):
        return obj._going

    @admin.display(description="Can't make it", ordering="_no")
    def not_going(self, obj):
        return obj._no

    @admin.display(description="Acknowledged", ordering="_ack")
    def acknowledged(self, obj):
        return obj._ack

    def save_model(self, request, obj, form, change):
        if not obj.author_id:
            obj.author = request.user
        super().save_model(request, obj, form, change)


# --------------------------------------------------------------------------
# Payments
# --------------------------------------------------------------------------
@admin.register(PaymentRequest)
class PaymentRequestAdmin(admin.ModelAdmin):
    form = NetworksRequiredForm
    list_display = ("title", "networks", "amount", "due_date", "is_open", "approved_payments", "collected", "announcement")
    list_editable = ("is_open",)
    list_filter = ("is_open", "environments")
    search_fields = ("title",)

    @admin.display(description="Networks")
    def networks(self, obj):
        return ", ".join(e.name for e in obj.environments.all())

    def get_queryset(self, request):
        ok = Q(payments__status=Payment.Status.APPROVED)
        return super().get_queryset(request).prefetch_related("environments").annotate(
            _paid=Count("payments", filter=ok), _total=Sum("payments__amount", filter=ok)
        )

    @admin.display(description="Approved payments", ordering="_paid")
    def approved_payments(self, obj):
        return obj._paid

    @admin.display(description="Collected", ordering="_total")
    def collected(self, obj):
        return f"₹{obj._total or 0}"


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    """Check each Transaction ID against the bank statement, then approve."""
    list_display = ("payer", "house", "request", "amount", "txn_id", "status", "admin_note", "created_at")
    list_editable = ("status", "admin_note")
    list_filter = ("status", "request")
    search_fields = ("txn_id", "payer__username", "payer__first_name", "payer__last_name", "payer__profile__house_number")
    list_select_related = ("payer__profile", "request")
    raw_id_fields = ("payer",)
    actions = ["approve", "reject"]

    @admin.display(description="House", ordering="payer__profile__house_number")
    def house(self, obj):
        return obj.payer.house or "-"

    @admin.action(description="Approve selected payments")
    def approve(self, request, queryset):
        n = queryset.update(status=Payment.Status.APPROVED)
        self.message_user(request, f"{n} payment(s) approved.", messages.SUCCESS)

    @admin.action(description="Reject selected payments")
    def reject(self, request, queryset):
        n = queryset.update(status=Payment.Status.REJECTED)
        self.message_user(request, f"{n} payment(s) rejected. Add a note so residents know why.", messages.WARNING)


# --------------------------------------------------------------------------
# Town Square moderation
# --------------------------------------------------------------------------
@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ("__str__", "author", "networks", "created_at", "likes_total", "is_hidden")
    list_editable = ("is_hidden",)
    list_filter = ("is_hidden", "environments", "created_at")
    search_fields = ("body", "author__username", "author__first_name")
    raw_id_fields = ("author",)
    exclude = ("likes",)

    @admin.display(description="Networks")
    def networks(self, obj):
        return ", ".join(e.name for e in obj.environments.all())

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("author").prefetch_related("environments").annotate(_likes=Count("likes"))

    @admin.display(description="Likes", ordering="_likes")
    def likes_total(self, obj):
        return obj._likes


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ("__str__", "post", "created_at", "is_hidden")
    list_editable = ("is_hidden",)
    list_filter = ("is_hidden",)
    raw_id_fields = ("post", "author")


# --------------------------------------------------------------------------
# Chat groups (names and members only; messages are never shown here)
# --------------------------------------------------------------------------
class MemberInline(admin.TabularInline):
    model = GroupMember
    extra = 0
    raw_id_fields = ("user",)
    exclude = ("last_read",)


@admin.register(ChatGroup)
class ChatGroupAdmin(admin.ModelAdmin):
    list_display = ("name", "created_by", "member_count", "created_at")
    search_fields = ("name",)
    inlines = [MemberInline]
    raw_id_fields = ("created_by",)

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_members=Count("memberships"))

    @admin.display(description="Members", ordering="_members")
    def member_count(self, obj):
        return obj._members


@admin.register(Community)
class CommunityAdmin(admin.ModelAdmin):
    list_display = ("name", "environment", "created_by", "created_at", "member_count")
    list_filter = ("environment",)
    search_fields = ("name", "description")

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_members=Count("memberships"))

    @admin.display(description="Members", ordering="_members")
    def member_count(self, obj):
        return obj._members


@admin.register(GameChallenge)
class GameChallengeAdmin(admin.ModelAdmin):
    list_display = ("game", "challenger", "opponent", "status", "created_at")
    list_filter = ("game", "status")


@admin.register(CommunityEvent)
class CommunityEventAdmin(admin.ModelAdmin):
    list_display = ("title", "community", "event_date", "created_by")
    list_filter = ("community",)


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("recipient", "kind", "title", "is_read", "created_at")
    list_filter = ("kind", "is_read")
    search_fields = ("recipient__username", "title", "body")


@admin.register(MusicProfile)
class MusicProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "is_shared", "updated_at")
    list_filter = ("is_shared",)
    search_fields = ("user__first_name", "user__last_name", "user__profile__house_number")
    list_select_related = ("user",)


@admin.register(MusicPlaylist)
class MusicPlaylistAdmin(admin.ModelAdmin):
    list_display = ("display_title", "user", "is_visible", "meta_status", "position", "created_at")
    list_filter = ("is_visible", "meta_status")
    search_fields = ("title", "user__first_name", "user__last_name")
    list_select_related = ("user",)
    raw_id_fields = ("user",)
