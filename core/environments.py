"""Networks ("environments"): separate private communities (a society, a school ...) inside one Wardbook.

A person can belong to several. Everything they see is limited to the networks they have been APPROVED into:
people (neighbours, DMs, groups, games, mentions, music), Town Square posts, notices, payments and communities.
This module is the one place that says who can see what; the views only ask it.
"""
from django.contrib.auth import get_user_model
from django.db.models import Exists, OuterRef
from django.utils import timezone

from .models import Announcement, Community, Environment, EnvironmentMembership as EM, PaymentRequest, Post

User = get_user_model()
APPROVED, PENDING = EM.Status.APPROVED, EM.Status.PENDING


# ---- my networks -----------------------------------------------------------------------------
def my_envs(user):
    """Networks I am approved in (and that are switched on). Cached on the user object for the request."""
    if not getattr(user, "is_authenticated", False):
        return []
    cached = getattr(user, "_wb_envs", None)
    if cached is None:
        cached = list(Environment.objects.filter(is_active=True, memberships__user=user, memberships__status=APPROVED)
                      .distinct().order_by("name"))
        user._wb_envs = cached
    return cached


def my_env_ids(user):
    return [e.pk for e in my_envs(user)]


def forget(user):
    """Call after memberships change inside one request."""
    if hasattr(user, "_wb_envs"):
        del user._wb_envs


def my_pending(user):
    return list(EM.objects.filter(user=user, status=PENDING, environment__is_active=True).select_related("environment"))


def admin_env_ids(user):
    """Networks whose requests I may approve: staff = all, else the ones where I am a network admin."""
    if user.is_staff:
        return list(Environment.objects.filter(is_active=True).values_list("pk", flat=True))
    return list(EM.objects.filter(user=user, status=APPROVED, role=EM.Role.ADMIN, environment__is_active=True)
                .values_list("environment_id", flat=True))


# ---- people ----------------------------------------------------------------------------------
def users_in_envs(env_ids):
    """Active people approved in ANY of these networks."""
    member = EM.objects.filter(user=OuterRef("pk"), status=APPROVED, environment__is_active=True, environment_id__in=list(env_ids))
    return User.objects.filter(is_active=True, profile__isnull=False).filter(Exists(member))


def visible_users(user):
    """Everyone I share at least one network with (me included)."""
    return users_in_envs(my_env_ids(user))


def can_see_user(viewer, target_id):
    return viewer.pk == target_id or visible_users(viewer).filter(pk=target_id).exists()


def shared_envs(viewer, target):
    """Networks both of us are in (what a profile shows, so one network never learns another's names)."""
    ids = set(EM.objects.filter(user=target, status=APPROVED, environment__is_active=True).values_list("environment_id", flat=True))
    return [e for e in my_envs(viewer) if e.pk in ids]


# ---- content ---------------------------------------------------------------------------------
def _through(model, rel):
    return getattr(model, rel).through


def visible_posts(user):
    ids = my_env_ids(user)
    link = Post.environments.through.objects.filter(post_id=OuterRef("pk"), environment_id__in=ids)
    return Post.objects.filter(Exists(link))


def visible_announcements(user):
    ids = my_env_ids(user)
    link = Announcement.environments.through.objects.filter(announcement_id=OuterRef("pk"), environment_id__in=ids)
    return Announcement.objects.filter(Exists(link))


def visible_payment_requests(user):
    ids = my_env_ids(user)
    link = PaymentRequest.environments.through.objects.filter(paymentrequest_id=OuterRef("pk"), environment_id__in=ids)
    return PaymentRequest.objects.filter(Exists(link))


def visible_communities(user):
    return Community.objects.filter(environment_id__in=my_env_ids(user), environment__is_active=True)


def audience_ids(envs):
    """People who can see something shared in these networks (used so @mentions and pings never reach outsiders)."""
    return set(users_in_envs([e.pk for e in envs]).values_list("pk", flat=True))


def only_mine(envs, user):
    """From a post's / notice's networks, just the ones the viewer is in."""
    mine = set(my_env_ids(user))
    return [e for e in envs if e.pk in mine]


# ---- joining ---------------------------------------------------------------------------------
def find_by_code(code):
    code = (code or "").strip()
    if not code:
        return None
    return Environment.objects.filter(is_active=True, join_code__iexact=code).exclude(join_code="").first()


def request_join(user, env, code=""):
    """Ask to join. Returns (membership, created). Approved at once only for networks with 'approve automatically'
    (and, if they have an invite code, only when the right code was typed). Approving also activates a waiting account."""
    em = EM.objects.filter(user=user, environment=env).first()
    if em:
        return em, False
    ok = env.auto_approve and (not env.join_code or (code or "").strip().lower() == env.join_code.lower())
    em = EM.objects.create(user=user, environment=env, status=APPROVED if ok else PENDING,
                           decided_at=timezone.now() if ok else None)
    if ok and not user.is_active:
        user.is_active = True
        user.save(update_fields=["is_active"])
    forget(user)
    return em, True


def approve(em, notify_fn=None):
    """Let someone in. Also activates their account if it was waiting (the only thing it was waiting for)."""
    if em.status != APPROVED:
        em.status = APPROVED
        em.decided_at = timezone.now()
        em.save(update_fields=["status", "decided_at"])
        if notify_fn:
            notify_fn(em)
    if not em.user.is_active:
        em.user.is_active = True
        em.user.save(update_fields=["is_active"])
    return em
