from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.db.models import Count, Q, Sum

from .models import (Announcement, AnnouncementResponse, ChatGroup, Comment, Community, CommunityEvent,
                     CommunityMember, GameChallenge, GameWallet, GroupMember, Notification, Payment, PaymentRequest, Post,
                     ResidentProfile, User)

admin.site.site_header = "Wardbook Control Room"
admin.site.site_title = "Wardbook"
admin.site.index_title = "Approvals, notices, payments and moderation"


class ProfileInline(admin.StackedInline):
    model = ResidentProfile
    can_delete = False


@admin.register(User)
class WardUserAdmin(UserAdmin):
    inlines = [ProfileInline]
    list_display = ("username", "get_full_name", "house", "is_active", "date_joined")
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

    @admin.display(description="House", ordering="profile__house_number")
    def house(self, obj):
        return obj.house or "-"

    @admin.action(description="Approve selected residents")
    def approve_selected(self, request, queryset):
        n = queryset.update(is_active=True)
        self.message_user(request, f"{n} resident(s) approved.", messages.SUCCESS)


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
    list_display = ("title", "category", "event_date", "is_pinned", "going", "not_going", "acknowledged", "created_at")
    list_filter = ("category", "is_pinned")
    search_fields = ("title", "body")
    inlines = [ResponseInline]
    fieldsets = (
        (None, {"fields": ("title", "body", "category", "is_pinned")}),
        ("Event details (category = Event)", {"fields": ("event_date", "venue")}),
    )

    def get_queryset(self, request):
        a = AnnouncementResponse.Answer
        return super().get_queryset(request).annotate(
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
    list_display = ("title", "amount", "due_date", "is_open", "approved_payments", "collected", "announcement")
    list_editable = ("is_open",)
    list_filter = ("is_open",)
    search_fields = ("title",)

    def get_queryset(self, request):
        ok = Q(payments__status=Payment.Status.APPROVED)
        return super().get_queryset(request).annotate(
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
    list_display = ("__str__", "author", "created_at", "likes_total", "is_hidden")
    list_editable = ("is_hidden",)
    list_filter = ("is_hidden", "created_at")
    search_fields = ("body", "author__username", "author__first_name")
    raw_id_fields = ("author",)
    exclude = ("likes",)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("author").annotate(_likes=Count("likes"))

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
    list_display = ("name", "created_by", "created_at", "member_count")
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


@admin.register(GameWallet)
class GameWalletAdmin(admin.ModelAdmin):
    list_display = ("user", "tokens", "last_refill", "updated_at")
    search_fields = ("user__first_name", "user__last_name", "user__username")
    list_editable = ("tokens",)


@admin.register(CommunityEvent)
class CommunityEventAdmin(admin.ModelAdmin):
    list_display = ("title", "community", "event_date", "created_by")
    list_filter = ("community",)


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("recipient", "kind", "title", "is_read", "created_at")
    list_filter = ("kind", "is_read")
    search_fields = ("recipient__username", "title", "body")
