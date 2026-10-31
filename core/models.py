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
    def house_label(self):
        """"House 13" / "Class 8-B": the word depends on the network the person signed up to."""
        profile = getattr(self, "profile", None)
        return f"{profile.unit_label or 'House'} {profile.house_number}" if profile and profile.house_number else ""

    @property
    def avatar_html(self):
        """Round avatar: photo if uploaded, else initials. Size is set by CSS."""
        profile = getattr(self, "profile", None)
        if profile and profile.avatar:
            return format_html('<img class="av" src="{}" alt="">', profile.avatar.url)
        return format_html('<span class="av av-{}">{}</span>', (self.pk or 0) % 8, (self.display_name[:1] or "?").upper())


class ResidentProfile(models.Model):
    class Gender(models.TextChoices):
        WOMAN = "woman", "Woman"
        MAN = "man", "Man"
        NON_BINARY = "non_binary", "Non-binary"
        PREFER_NOT_TO_SAY = "prefer_not_to_say", "Prefer not to say"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    house_number = models.CharField("House / class", max_length=10, db_index=True)  # not unique: families share a house
    unit_label = models.CharField(max_length=20, default="House", blank=True, help_text="The word shown before the number: House, Class, Flat ...")
    avatar = models.ImageField(upload_to="avatars/", blank=True)
    bio = models.CharField(max_length=160, blank=True)
    gender = models.CharField(max_length=20, choices=Gender.choices, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    show_age = models.BooleanField("Show my age to neighbours", default=True)
    pronouns = models.CharField(max_length=30, blank=True)
    occupation = models.CharField(max_length=80, blank=True)
    hometown = models.CharField(max_length=80, blank=True)
    interests = models.CharField(max_length=250, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ["house_number"]

    @property
    def is_approved(self):
        return self.user.is_active

    @property
    def is_online(self):
        return bool(self.last_seen_at and self.last_seen_at >= timezone.now() - timezone.timedelta(seconds=45))

    @property
    def age(self):
        if not self.date_of_birth:
            return None
        today = timezone.localdate()
        return today.year - self.date_of_birth.year - ((today.month, today.day) <
                                                        (self.date_of_birth.month, self.date_of_birth.day))

    def __str__(self):
        return f"{self.user.display_name} ({self.unit_label or 'House'} {self.house_number})"


# --------------------------------------------------------------------------
# Environments: separate private networks (a society, a school ...). A person can be in several.
# --------------------------------------------------------------------------
ENV_COLORS = ["#1877F2", "#12A150", "#F2A024", "#8B6CF0", "#E5484D", "#0EA5A4", "#D6409F", "#6B7A90"]


class Environment(models.Model):
    name = models.CharField(max_length=60, unique=True, help_text="e.g. Ward 11-A, DPS Noida")
    slug = models.SlugField(max_length=70, unique=True, blank=True, editable=False)
    description = models.CharField(max_length=160, blank=True, help_text="One line shown on the sign-up page.")
    is_active = models.BooleanField(default=True, help_text="Untick to switch this network off for everyone in it.")
    is_listed = models.BooleanField("Show on sign-up page", default=True,
                                    help_text="Untick for a hidden network: people can only join with its invite code.")
    join_code = models.CharField("Invite code", max_length=24, blank=True,
                                 help_text="Optional. Anyone with the code can ask to join, even if the network is hidden.")
    auto_approve = models.BooleanField("Approve automatically", default=False,
                                       help_text="Let people in at once. If there is an invite code, only people who enter it get in at once.")
    unit_label = models.CharField("Word before the number", max_length=20, default="House",
                                  help_text="Shown before each person's number: House, Class, Flat ...")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            from django.utils.text import slugify
            base = slugify(self.name)[:60] or "network"
            slug, n = base, 2
            while Environment.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug, n = f"{base}-{n}", n + 1
            self.slug = slug
        super().save(*args, **kwargs)

    @property
    def color(self):
        return ENV_COLORS[(self.pk or 0) % len(ENV_COLORS)]


class EnvironmentMembership(models.Model):
    class Role(models.TextChoices):
        MEMBER = "member", "Member"
        ADMIN = "admin", "Admin"

    class Status(models.TextChoices):
        PENDING = "pending", "Waiting for approval"
        APPROVED = "approved", "Approved"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="env_memberships")
    environment = models.ForeignKey(Environment, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.MEMBER)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    requested_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "environment"], name="one_membership_per_env")]
        indexes = [models.Index(fields=["environment", "status"]), models.Index(fields=["user", "status"])]
        ordering = ["environment__name", "status", "user__first_name"]

    def __str__(self):
        return f"{self.user.display_name} in {self.environment.name} ({self.get_status_display()})"


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
    environments = models.ManyToManyField("Environment", related_name="notices", blank=True,
                                          help_text="Which networks see this notice. Pick at least one.")
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


class EventPoke(models.Model):
    """"Are you coming too?" A resident who is going to an event nudges a neighbour who has not answered yet."""
    announcement = models.ForeignKey(Announcement, on_delete=models.CASCADE, related_name="pokes")
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="pokes_sent")
    target = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="pokes_received")
    poked_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["announcement", "sender", "target"], name="one_poke_per_pair")]
        ordering = ["-poked_at"]

    def __str__(self):
        return f"{self.sender.display_name} poked {self.target.display_name} about {self.announcement.title}"


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
    environments = models.ManyToManyField("Environment", related_name="payment_requests", blank=True,
                                          help_text="Which networks are asked to pay. Pick at least one.")
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
    environments = models.ManyToManyField("Environment", related_name="posts", blank=True)  # where it is shared
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


class Notification(models.Model):
    class Kind(models.TextChoices):
        MENTION = "mention", "Mention"
        MESSAGE = "message", "Message"
        GAME = "game", "Game invite"
        COMMUNITY = "community", "Community"
        TOWN_SQUARE = "town_square", "Town Square"
        POKE = "poke", "Poke"
        SYSTEM = "system", "Update"

    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="triggered_notifications")
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.SYSTEM)
    title = models.CharField(max_length=160)
    body = models.CharField(max_length=300, blank=True)
    url = models.CharField(max_length=300, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["recipient", "is_read"])]


class PushSubscription(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="push_subscriptions")
    endpoint = models.URLField(max_length=500, unique=True)
    p256dh = models.CharField(max_length=200)
    auth = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)


class Community(models.Model):
    environment = models.ForeignKey("Environment", null=True, on_delete=models.CASCADE, related_name="communities",
                                    help_text="Only members of this network can see and join it.")
    name = models.CharField(max_length=80)
    description = models.TextField(max_length=500, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="communities_created")
    members = models.ManyToManyField(settings.AUTH_USER_MODEL, through="CommunityMember", related_name="communities")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]


class CommunityMember(models.Model):
    community = models.ForeignKey(Community, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="community_memberships")
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["community", "user"], name="one_community_membership")]


class CommunityPost(models.Model):
    community = models.ForeignKey(Community, on_delete=models.CASCADE, related_name="posts")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="community_posts")
    body = models.TextField(max_length=2000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class CommunityEvent(models.Model):
    community = models.ForeignKey(Community, on_delete=models.CASCADE, related_name="events")
    title = models.CharField(max_length=150)
    details = models.TextField(blank=True)
    event_date = models.DateTimeField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="community_events")

    class Meta:
        ordering = ["event_date"]


class GameChallenge(models.Model):
    class Game(models.TextChoices):
        TIC_TAC_TOE = "tic_tac_toe", "Tic-tac-toe"
        CONNECT_FOUR = "connect_four", "Connect Four"
        SNAKES_LADDERS = "snakes_ladders", "Snakes and Ladders"
        LUDO = "ludo", "Ludo"
        CHESS = "chess", "Chess"
        TEEN_PATTI = "teen_patti", "Teen Patti"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        DECLINED = "declined", "Declined"
        FINISHED = "finished", "Finished"

    game = models.CharField(max_length=20, choices=Game.choices)
    challenger = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="games_challenged")
    opponent = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="games_received")
    time_control = models.CharField(max_length=40, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    winner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="games_won")
    created_at = models.DateTimeField(auto_now_add=True)
    participants = models.ManyToManyField(settings.AUTH_USER_MODEL, through="GameParticipant", related_name="game_challenges_joined")

    class Meta:
        ordering = ["-created_at"]


class GameSession(models.Model):
    challenge = models.OneToOneField(GameChallenge, on_delete=models.CASCADE, related_name="session")
    rematch_session = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL,
                                        related_name="rematch_source")
    state = models.JSONField(default=dict)
    current_turn = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="game_turns")
    is_active = models.BooleanField(default=True)
    winner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="game_session_wins")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]


class GameParticipant(models.Model):
    class Response(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        DECLINED = "declined", "Declined"

    challenge = models.ForeignKey(GameChallenge, on_delete=models.CASCADE, related_name="participantships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="game_participations")
    seat = models.PositiveSmallIntegerField()
    response = models.CharField(max_length=10, choices=Response.choices, default=Response.ACCEPTED)  # Ludo / Snakes: 2-4 players

    class Meta:
        constraints = [models.UniqueConstraint(fields=["challenge", "user"], name="one_game_participant")]
        ordering = ["seat"]


class GameChatMessage(models.Model):
    session = models.ForeignKey(GameSession, on_delete=models.CASCADE, related_name="chat_messages")
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="game_chat_messages")
    body = models.CharField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]


# --------------------------------------------------------------------------
# Chat: direct messages and groups
# --------------------------------------------------------------------------
class ChatGroup(models.Model):
    name = models.CharField(max_length=60)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="created_groups")
    members = models.ManyToManyField(settings.AUTH_USER_MODEL, through="GroupMember", related_name="chat_groups")
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def env_list(self):
        """For templates that loop over a list of networks: a community lives in exactly one."""
        return [self.environment] if self.environment_id else []

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
    document = models.FileField(upload_to="documents/", blank=True)
    audio = models.FileField(upload_to="voice/", blank=True, storage=audio_storage)
    reply_to = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="replies")
    reactions = models.JSONField(default=dict, blank=True)
    is_reported = models.BooleanField(default=False)
    is_read = models.BooleanField(default=False)  # DMs only; groups use GroupMember.last_read
    read_at = models.DateTimeField(null=True, blank=True)  # DMs only: when the recipient saw it ("Seen 4:32 PM")
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
        return "📷 Photo" if self.image else "📎 Document" if self.document else "🎤 Voice note"


class TypingIndicator(models.Model):
    thread_key = models.CharField(max_length=80)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="typing_indicators")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["thread_key", "user"], name="one_typing_indicator_per_thread")]
        indexes = [models.Index(fields=["thread_key", "updated_at"])]


class GameWallet(models.Model):
    """Play money for Teen Patti. Everyone starts with 50 tokens (each token = 10 rupees of pretend money, so 500)."""
    START_TOKENS = 50
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="game_wallet")
    tokens = models.PositiveIntegerField(default=START_TOKENS)
    last_refill = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.display_name}: {self.tokens} tokens"


# --------------------------------------------------------------------------
# Music Taste: residents share Spotify playlists, neighbours listen through Spotify's own embedded player
# --------------------------------------------------------------------------
class MusicProfile(models.Model):
    """One per resident who has opened Music Taste. Nothing is visible to neighbours until `is_shared` is switched on."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="music_profile")
    is_shared = models.BooleanField(default=False, db_index=True,
                                    help_text="Opt-in. When off, neighbours cannot find or see any of this person's music.")
    about = models.CharField(max_length=280, blank=True)
    favourites = models.CharField(max_length=250, blank=True, help_text="Favourite artists or tracks")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.display_name} music ({'shared' if self.is_shared else 'private'})"


class MusicPlaylist(models.Model):
    """A Spotify playlist a resident added by link. Only the playlist id is trusted: the link and the embed URL are
    always rebuilt from it (see core/spotify.py), never taken from what was typed."""
    class Meta:
        ordering = ["position", "id"]
        constraints = [models.UniqueConstraint(fields=["user", "spotify_id"], name="one_music_playlist_per_resident")]
        indexes = [models.Index(fields=["user", "is_visible", "position"], name="music_pl_user_vis_pos")]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="music_playlists")
    spotify_id = models.CharField(max_length=32)
    title = models.CharField(max_length=200, blank=True)
    description = models.CharField(max_length=300, blank=True)
    cover_url = models.URLField(max_length=500, blank=True)
    is_visible = models.BooleanField(default=True)
    position = models.PositiveIntegerField(default=0)
    # what Spotify said the last time we asked: "ok", "unavailable" (private / deleted) or "unknown" (could not ask)
    meta_status = models.CharField(max_length=12, default="unknown")
    checked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.display_name}: {self.display_title}"

    @property
    def display_title(self):
        return self.title or "Spotify playlist"

    @property
    def url(self):
        from .spotify import canonical_url
        return canonical_url(self.spotify_id)

    @property
    def embed_url(self):
        from .spotify import embed_url
        return embed_url(self.spotify_id)

    @property
    def is_unavailable(self):
        return self.meta_status == "unavailable"
