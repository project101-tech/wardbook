from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.html import format_html


class User(AbstractUser):
    """Username = mobile number. Accounts are created inactive by the
    registration view and activated by the admin."""

    @property
    def display_name(self):
        return self.get_full_name() or self.username

    @property
    def house(self):
        profile = getattr(self, "profile", None)
        return profile.house_number if profile else ""

    @property
    def avatar_html(self):
        """Round avatar: photo if uploaded, else initials. Size is set by CSS."""
        profile = getattr(self, "profile", None)
        if profile and profile.avatar:
            return format_html('<img class="av" src="{}" alt="">', profile.avatar.url)
        return format_html('<span class="av">{}</span>', (self.display_name[:1] or "?").upper())


class ResidentProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    house_number = models.CharField(max_length=10, db_index=True)  # not unique: families share a house
    avatar = models.ImageField(upload_to="avatars/", blank=True)
    bio = models.CharField(max_length=160, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["house_number"]

    @property
    def is_approved(self):
        return self.user.is_active

    def __str__(self):
        return f"{self.user.display_name} (House {self.house_number})"


# --------------------------------------------------------------------------
# Notices, events and rules
# --------------------------------------------------------------------------
class Announcement(models.Model):
    class Category(models.TextChoices):
        NOTICE = "notice", "Notice"
        RULE = "rule", "Rule"
        URGENT = "urgent", "Urgent"
        EVENT = "event", "Event"

    title = models.CharField(max_length=150)
    body = models.TextField()
    category = models.CharField(max_length=10, choices=Category.choices, default=Category.NOTICE)
    is_pinned = models.BooleanField(default=False)
    # Events only: residents can RSVP until the event starts
    event_date = models.DateTimeField(null=True, blank=True, help_text="Only for events. Residents can RSVP until this time.")
    venue = models.CharField(max_length=120, blank=True, help_text="Only for events.")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, editable=False, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-is_pinned", "-created_at"]

    def __str__(self):
        return self.title

    def clean(self):
        if self.category == self.Category.EVENT and not self.event_date:
            raise ValidationError({"event_date": "Events need a date and time so residents can RSVP."})

    @property
    def is_upcoming(self):
        return self.event_date is None or self.event_date >= timezone.now()


class AnnouncementResponse(models.Model):
    """One row per resident per announcement: an event RSVP or a rule acknowledgement.
    Clicking the same button again deletes the row (that is the 'revert')."""

    class Answer(models.TextChoices):
        GOING = "going", "Going"
        NOT_GOING = "not_going", "Can't make it"
        ACK = "ack", "Acknowledged"

    announcement = models.ForeignKey(Announcement, on_delete=models.CASCADE, related_name="responses")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="responses")
    answer = models.CharField(max_length=10, choices=Answer.choices)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["announcement", "user"], name="one_response_per_resident")]

    def __str__(self):
        return f"{self.user.display_name}: {self.get_answer_display()}"


# --------------------------------------------------------------------------
# Manual UPI payments
# --------------------------------------------------------------------------
class PaymentRequest(models.Model):
    """Something residents are asked to pay for (maintenance, an event fee, a voluntary fund).
    You create these in the admin; they show on the Payments page."""

    title = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    amount = models.DecimalField(max_digits=9, decimal_places=2, null=True, blank=True,
                                 help_text="Fixed amount in ₹. Leave empty for voluntary contributions (resident enters the amount).")
    due_date = models.DateField(null=True, blank=True)
    upi_id = models.CharField("UPI ID", max_length=60, help_text="e.g. society@okhdfcbank")
    upi_qr = models.ImageField("UPI QR code", upload_to="upi/", blank=True)
    announcement = models.ForeignKey(Announcement, null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="payment_requests",
                                     help_text="Optional: show this payment on a notice/event, e.g. an event fee.")
    is_open = models.BooleanField(default=True, help_text="Untick to close collection.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-is_open", "-created_at"]

    def __str__(self):
        return self.title


class Payment(models.Model):
    class Status(models.IntegerChoices):  # integers so "pending" sorts first in the admin
        PENDING = 0, "Pending"
        APPROVED = 1, "Approved"
        REJECTED = 2, "Rejected"

    request = models.ForeignKey(PaymentRequest, on_delete=models.CASCADE, related_name="payments")
    payer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="payments")
    amount = models.DecimalField(max_digits=9, decimal_places=2)
    txn_id = models.CharField("UPI transaction ID", max_length=40, unique=True)
    status = models.PositiveSmallIntegerField(choices=Status.choices, default=Status.PENDING)
    admin_note = models.CharField(max_length=200, blank=True, help_text="Shown to the resident, e.g. why it was rejected.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["status", "-created_at"]

    def __str__(self):
        return f"{self.payer.display_name} ₹{self.amount} ({self.get_status_display()})"


# --------------------------------------------------------------------------
# Town Square
# --------------------------------------------------------------------------
class Post(models.Model):
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="posts")
    body = models.TextField(max_length=2000)
    image = models.ImageField(upload_to="posts/", blank=True)
    likes = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name="liked_posts", blank=True)
    is_hidden = models.BooleanField(default=False)  # moderation: hide instead of delete
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.author.username}: {self.body[:40]}"


class Comment(models.Model):
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="comments")
    body = models.CharField(max_length=500)
    is_hidden = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.author.username}: {self.body[:40]}"


# --------------------------------------------------------------------------
# Chat: direct messages and groups
# --------------------------------------------------------------------------
class ChatGroup(models.Model):
    name = models.CharField(max_length=60)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="created_groups")
    members = models.ManyToManyField(settings.AUTH_USER_MODEL, through="GroupMember", related_name="chat_groups")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    @property
    def avatar_html(self):
        return format_html('<span class="av group"><i class="bi bi-people-fill"></i></span>')


class GroupMember(models.Model):
    group = models.ForeignKey(ChatGroup, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="group_memberships")
    last_read = models.PositiveBigIntegerField(default=0)  # id of the newest message this member has seen
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["group", "user"], name="one_membership_per_user")]

    def __str__(self):
        return f"{self.user.display_name} in {self.group.name}"


def audio_storage():
    """Voice notes: Cloudinary files them under 'video' (its audio type); locally it's the plain media folder."""
    if settings.USE_CLOUDINARY:
        from cloudinary_storage.storage import VideoMediaCloudinaryStorage
        return VideoMediaCloudinaryStorage()
    from django.core.files.storage import FileSystemStorage
    return FileSystemStorage()


class Message(models.Model):
    """A direct message (recipient set) or a group message (group set).
    Deliberately not registered in the admin, so private chats aren't browsable from there."""

    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="sent_messages")
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE,
                                  related_name="received_messages")
    group = models.ForeignKey(ChatGroup, null=True, blank=True, on_delete=models.CASCADE, related_name="messages")
    body = models.CharField(max_length=1000, blank=True)
    image = models.ImageField(upload_to="chat/", blank=True)
    audio = models.FileField(upload_to="voice/", blank=True, storage=audio_storage)
    is_read = models.BooleanField(default=False)  # DMs only; groups use GroupMember.last_read
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        indexes = [models.Index(fields=["recipient", "is_read"])]
        constraints = [
            models.CheckConstraint(
                condition=(models.Q(recipient__isnull=False, group__isnull=True)
                           | models.Q(recipient__isnull=True, group__isnull=False)),
                name="message_is_dm_or_group",
            )
        ]

    @property
    def preview(self):
        if self.body:
            return self.body
        return "📷 Photo" if self.image else "🎤 Voice note"
