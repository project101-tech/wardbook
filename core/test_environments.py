"""Networks ("environments"): people in one network never see another's things; joining is approved per network.
Run with:  python manage.py test core.test_environments"""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.admin.sites import site as admin_site
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.cache import cache
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from . import environments as envs
from .admin import EnvironmentMembershipAdmin
from .models import (Announcement, ChatGroup, Comment, Community, Environment, EnvironmentMembership as EM, GroupMember, Message,
                     Notification, PaymentRequest, Post, ResidentProfile)
from .testing import env, join

User = get_user_model()


def person(username, first, last="X", house="1", staff=False):
    u = User.objects.create_user(username=username, password="x", first_name=first, last_name=last, is_active=True, is_staff=staff)
    ResidentProfile.objects.create(user=u, house_number=house)
    return u


def post(author, body, *where):
    p = Post.objects.create(author=author, body=body)
    p.environments.set(where)
    return p


class IsolationTests(TestCase):
    """Ward: Meera + Raj.  APS: Asmita + Dev.  Anhad is in BOTH."""

    @classmethod
    def setUpTestData(cls):
        cache.clear()
        cls.ward, cls.aps = env("Ward 11-A"), env("APS Noida")
        cls.meera, cls.raj = person("9300000001", "Meera", "Iyer", "5"), person("9300000002", "Raj", "Patel", "6")
        cls.asmita, cls.dev = person("9300000003", "Asmita", "Rao", "8-B"), person("9300000004", "Dev", "Kapoor", "9-A")
        cls.anhad = person("9300000005", "Anhad", "Jaj", "A-1")
        join(cls.meera, cls.ward); join(cls.raj, cls.ward)
        join(cls.asmita, cls.aps); join(cls.dev, cls.aps)
        join(cls.anhad, cls.ward, cls.aps)
        cls.p_ward = post(cls.meera, "WARD-ONLY post", cls.ward)
        cls.p_aps = post(cls.asmita, "APS-ONLY post", cls.aps)
        cls.p_both = post(cls.anhad, "BOTH-NETWORKS post", cls.ward, cls.aps)

    def get(self, user, name, *args, **kw):
        self.client.force_login(user)
        return self.client.get(reverse(name, args=args), **kw)

    # ---- feed ----
    def test_feed_only_shows_my_networks(self):
        html = self.get(self.meera, "feed").content.decode()
        self.assertIn("WARD-ONLY", html); self.assertIn("BOTH-NETWORKS", html); self.assertNotIn("APS-ONLY", html)
        html = self.get(self.asmita, "feed").content.decode()
        self.assertIn("APS-ONLY", html); self.assertNotIn("WARD-ONLY", html)

    def test_person_in_both_sees_everything_with_tags(self):
        html = self.get(self.anhad, "feed").content.decode()
        for t in ("WARD-ONLY", "APS-ONLY", "BOTH-NETWORKS"):
            self.assertIn(t, html)
        self.assertIn("env-tag", html)                      # each post is marked with where it came from

    def test_single_network_user_sees_no_tags(self):
        self.assertNotIn('class="env-tag', self.get(self.meera, "feed").content.decode())

    def test_filter_to_one_network(self):
        self.client.force_login(self.anhad)
        html = self.client.get(reverse("feed"), {"env": self.aps.slug}).content.decode()
        self.assertIn("APS-ONLY", html); self.assertIn("BOTH-NETWORKS", html); self.assertNotIn("WARD-ONLY", html)

    def test_filter_cannot_peek_into_a_network_i_am_not_in(self):
        self.client.force_login(self.meera)
        html = self.client.get(reverse("feed"), {"env": self.aps.slug}).content.decode()
        self.assertNotIn("APS-ONLY", html)

    def test_live_feed_is_scoped(self):
        self.client.force_login(self.meera)
        r = self.client.get(reverse("feed_live"), {"after": 0, "wait": 0})
        self.assertNotIn(b"APS-ONLY", r.content)

    # ---- posting ----
    def test_post_to_one_or_several_networks(self):
        self.client.force_login(self.anhad)
        self.client.post(reverse("feed"), {"body": "to aps only", "environments": [self.aps.pk]})
        self.client.post(reverse("feed"), {"body": "to both", "environments": [self.aps.pk, self.ward.pk]})
        self.assertEqual(set(Post.objects.get(body="to aps only").environments.all()), {self.aps})
        self.assertEqual(set(Post.objects.get(body="to both").environments.all()), {self.aps, self.ward})
        self.assertNotIn("to aps only", self.get(self.meera, "feed").content.decode())
        self.assertIn("to both", self.get(self.meera, "feed").content.decode())

    def test_multi_network_member_must_choose(self):
        self.client.force_login(self.anhad)
        r = self.client.post(reverse("feed"), {"body": "nowhere"})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Post.objects.filter(body="nowhere").exists())

    def test_cannot_post_into_a_network_i_am_not_in(self):
        self.client.force_login(self.meera)
        self.client.post(reverse("feed"), {"body": "sneaky", "environments": [self.aps.pk]})
        self.assertFalse(Post.objects.filter(body="sneaky").exists())

    def test_single_network_member_posts_without_choosing(self):
        self.client.force_login(self.meera)
        self.client.post(reverse("feed"), {"body": "plain"})
        self.assertEqual(set(Post.objects.get(body="plain").environments.all()), {self.ward})

    # ---- likes and comments ----
    def test_cannot_like_or_comment_on_an_invisible_post(self):
        self.client.force_login(self.meera)
        self.assertEqual(self.client.post(reverse("like", args=[self.p_aps.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("comment", args=[self.p_aps.pk]), {"body": "hi"}).status_code, 404)
        self.assertEqual(self.p_aps.likes.count(), 0)
        self.assertEqual(Comment.objects.filter(post=self.p_aps).count(), 0)

    def test_can_like_a_shared_post(self):
        self.client.force_login(self.asmita)
        self.assertIn(self.client.post(reverse("like", args=[self.p_both.pk])).status_code, (200, 302))
        self.assertEqual(self.p_both.likes.count(), 1)

    # ---- notices, payments, communities ----
    def test_notices_are_per_network(self):
        a = Announcement.objects.create(title="WARD-NOTICE"); a.environments.set([self.ward])
        b = Announcement.objects.create(title="APS-NOTICE"); b.environments.set([self.aps])
        html = self.get(self.meera, "announcements").content.decode()
        self.assertIn("WARD-NOTICE", html); self.assertNotIn("APS-NOTICE", html)
        self.assertEqual(self.get(self.anhad, "announcements").content.decode().count("-NOTICE"), 2)

    def test_payments_are_per_network(self):
        c = PaymentRequest.objects.create(title="APS-FEES", amount=Decimal("100"), upi_id="a@b"); c.environments.set([self.aps])
        self.assertNotIn("APS-FEES", self.get(self.meera, "payments").content.decode())
        self.assertIn("APS-FEES", self.get(self.asmita, "payments").content.decode())
        self.client.force_login(self.meera)
        self.assertEqual(self.client.post(reverse("pay", args=[c.pk]), {"amount": "100"}).status_code, 404)

    def test_communities_are_per_network(self):
        c = Community.objects.create(name="APS-CHESS", created_by=self.asmita, environment=self.aps)
        self.assertNotIn("APS-CHESS", self.get(self.meera, "communities").content.decode())
        self.assertEqual(self.get(self.meera, "community_detail", c.pk).status_code, 404)
        self.client.force_login(self.meera)
        self.assertEqual(self.client.post(reverse("community_join", args=[c.pk])).status_code, 404)
        self.assertIn("APS-CHESS", self.get(self.dev, "communities").content.decode())

    # ---- people ----
    def test_directory_and_profiles(self):
        html = self.get(self.meera, "directory").content.decode()
        self.assertIn("Raj", html); self.assertIn("Anhad", html); self.assertNotIn("Asmita", html)
        self.assertEqual(self.get(self.meera, "public_profile", self.asmita.pk).status_code, 404)
        self.assertEqual(self.get(self.anhad, "public_profile", self.asmita.pk).status_code, 200)

    def test_direct_messages_across_networks_are_blocked(self):
        self.assertEqual(self.get(self.meera, "conversation", self.asmita.pk).status_code, 404)
        self.assertEqual(self.get(self.meera, "conversation_api", self.asmita.pk).status_code, 404)
        self.assertEqual(self.get(self.anhad, "conversation", self.asmita.pk).status_code, 200)

    def test_inbox_hides_people_i_no_longer_share_a_network_with(self):
        Message.objects.create(sender=self.asmita, recipient=self.meera, body="OLD-HELLO-FROM-APS")
        self.assertNotIn("OLD-HELLO-FROM-APS", self.get(self.meera, "inbox").content.decode())

    def test_mentions_only_offer_people_in_my_networks(self):
        self.client.force_login(self.meera)
        names = self.client.get(reverse("mention_search"), {"q": ""}).content.decode()
        self.assertIn("Raj", names); self.assertNotIn("Asmita", names); self.assertNotIn("Dev", names)
        listed = self.client.get(reverse("mention_names")).content.decode()
        self.assertNotIn("Asmita", listed)
        self.client.force_login(self.asmita)
        self.assertNotIn("Raj", self.client.get(reverse("mention_names")).content.decode())   # cache is per set of networks

    def test_mention_of_outsider_does_not_notify(self):
        self.client.force_login(self.meera)
        self.client.post(reverse("feed"), {"body": f"hello @{self.asmita.display_name}"})
        self.assertFalse(Notification.objects.filter(recipient=self.asmita).exists())

    def test_leaving_a_shared_network_drops_visibility(self):
        EM.objects.filter(user=self.anhad, environment=self.aps).delete(); envs.forget(self.anhad)
        self.assertNotIn("APS-ONLY", self.get(self.anhad, "feed").content.decode())
        EM.objects.create(user=self.anhad, environment=self.aps, status="approved")

    def test_pending_membership_sees_nothing_from_that_network(self):
        u = person("9300000009", "Pending"); join(u, self.ward); join(u, self.aps, status="pending")
        self.assertNotIn("APS-ONLY", self.get(u, "feed").content.decode())

    def test_switched_off_network_hides_its_content(self):
        off = Environment.objects.create(name="Old school", is_active=False)
        u = person("9300000010", "Zed"); join(u, self.ward, off)
        post(self.raj, "OFF-NETWORK", off)
        self.assertNotIn("OFF-NETWORK", self.get(u, "feed").content.decode())


class JoiningTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cache.clear()
        cls.ward, cls.aps = env("Ward 11-A"), env("APS Noida")
        cls.admin = person("9400000001", "Admin", "One", "1", staff=True); join(cls.admin, cls.ward, role="admin")

    def register(self, **over):
        d = {"full_name": "New Person", "house_number": "8b", "mobile": "9876543210", "password1": "Str0ng-pass!9", "password2": "Str0ng-pass!9",
             "networks": [self.aps.pk]}
        d.update(over)
        return self.client.post(reverse("register"), d)

    def test_registration_needs_a_network(self):
        r = self.register(networks=[])
        self.assertEqual(r.status_code, 200)
        self.assertFalse(User.objects.filter(username="9876543210").exists())

    def test_registration_creates_a_pending_membership_and_inactive_account(self):
        self.assertEqual(self.register().status_code, 302)
        u = User.objects.get(username="9876543210")
        self.assertFalse(u.is_active)
        self.assertEqual([(m.environment, m.status) for m in u.env_memberships.all()], [(self.aps, "pending")])

    def test_registration_can_ask_for_several_networks(self):
        self.register(networks=[self.aps.pk, self.ward.pk])
        self.assertEqual(User.objects.get(username="9876543210").env_memberships.count(), 2)

    def test_registration_notifies_the_network_admin(self):
        self.register(networks=[self.ward.pk])
        self.assertTrue(Notification.objects.filter(recipient=self.admin, title__icontains="wants to join").exists())

    def test_hidden_network_is_not_listed_but_works_with_its_code(self):
        h = Environment.objects.create(name="Secret club", is_listed=False, join_code="OPEN-SESAME")
        self.assertNotIn("Secret club", self.client.get(reverse("register")).content.decode())
        self.assertEqual(self.register(networks=[], join_code="wrong").status_code, 200)
        self.assertEqual(self.register(networks=[], join_code="open-sesame").status_code, 302)
        self.assertEqual(User.objects.get(username="9876543210").env_memberships.get().environment, h)

    def test_auto_approve_activates_the_account(self):
        self.aps.auto_approve = True; self.aps.save()
        self.register()
        u = User.objects.get(username="9876543210")
        self.assertTrue(u.is_active)
        self.assertEqual(u.env_memberships.get().status, "approved")

    def test_auto_approve_with_code_needs_the_code(self):
        self.aps.auto_approve = True; self.aps.join_code = "KEY"; self.aps.save()
        self.register()
        u = User.objects.get(username="9876543210")
        self.assertFalse(u.is_active)
        self.assertEqual(u.env_memberships.get().status, "pending")

    def test_default_is_manual_approval(self):
        self.assertFalse(Environment.objects.create(name="Fresh").auto_approve)

    # ---- the gate ----
    def test_user_with_no_approved_network_is_sent_to_the_waiting_page(self):
        u = person("9400000005", "Waiting"); join(u, self.aps, status="pending")
        self.client.force_login(u)
        r = self.client.get(reverse("feed"))
        self.assertRedirects(r, reverse("networks"))
        html = self.client.get(reverse("networks")).content.decode()
        self.assertIn("Waiting for approval", html)
        self.assertEqual(self.client.get(reverse("profile")).status_code, 200)       # still allowed

    def test_user_with_no_membership_at_all_is_gated(self):
        u = person("9400000006", "Nobody"); self.client.force_login(u)
        self.assertRedirects(self.client.get(reverse("directory")), reverse("networks"))

    def test_approval_lets_them_in(self):
        u = person("9400000007", "Later"); em = EM.objects.create(user=u, environment=self.aps)
        self.client.force_login(u)
        self.assertEqual(self.client.get(reverse("feed")).status_code, 302)
        envs.approve(em)
        envs.forget(u)
        self.assertEqual(self.client.get(reverse("feed")).status_code, 200)

    # ---- networks page ----
    def test_ask_to_join_another_network(self):
        u = person("9400000010", "Meera"); join(u, self.ward); self.client.force_login(u)
        self.client.post(reverse("network_join"), {"networks": [self.aps.pk]})
        self.assertEqual(EM.objects.get(user=u, environment=self.aps).status, "pending")
        self.assertEqual([e.name for e in envs.my_envs(User.objects.get(pk=u.pk))], ["Ward 11-A"])      # not in until approved
        self.assertTrue(Notification.objects.filter(recipient=self.admin, title__icontains="wants to join APS").exists())

    def test_asking_twice_does_not_duplicate(self):
        u = person("9400000011", "Meera"); join(u, self.ward); self.client.force_login(u)
        for _ in range(2):
            self.client.post(reverse("network_join"), {"networks": [self.aps.pk]})
        self.assertEqual(EM.objects.filter(user=u, environment=self.aps).count(), 1)

    def test_cannot_join_an_unlisted_network_by_id(self):
        h = Environment.objects.create(name="Hidden", is_listed=False, join_code="K")
        u = person("9400000012", "Meera"); join(u, self.ward); self.client.force_login(u)
        self.client.post(reverse("network_join"), {"networks": [h.pk]})
        self.assertFalse(EM.objects.filter(user=u, environment=h).exists())

    def test_cannot_leave_the_last_network(self):
        u = person("9400000013", "Meera"); join(u, self.ward); self.client.force_login(u)
        em = EM.objects.get(user=u)
        self.client.post(reverse("network_leave", args=[em.pk]))
        self.assertTrue(EM.objects.filter(pk=em.pk).exists())

    def test_can_leave_when_in_two_and_cannot_touch_someone_elses(self):
        u = person("9400000014", "Meera"); join(u, self.ward, self.aps); self.client.force_login(u)
        other = EM.objects.get(user=self.admin)
        self.assertEqual(self.client.post(reverse("network_leave", args=[other.pk])).status_code, 404)
        self.client.post(reverse("network_leave", args=[EM.objects.get(user=u, environment=self.aps).pk]))
        self.assertFalse(EM.objects.filter(user=u, environment=self.aps).exists())

    def test_network_admin_can_approve_only_their_own_network(self):
        mine = EM.objects.create(user=person("9400000020", "A"), environment=self.ward)
        theirs = EM.objects.create(user=person("9400000021", "B"), environment=self.aps)
        wa = person("9400000022", "Ward", "Admin"); join(wa, self.ward, role="admin"); self.client.force_login(wa)
        self.assertEqual(self.client.post(reverse("network_decide", args=[theirs.pk]), {"action": "approve"}).status_code, 404)
        self.client.post(reverse("network_decide", args=[mine.pk]), {"action": "approve"})
        mine.refresh_from_db(); theirs.refresh_from_db()
        self.assertEqual((mine.status, theirs.status), ("approved", "pending"))
        self.assertTrue(Notification.objects.filter(recipient=mine.user, title__icontains="You're in").exists())

    def test_ordinary_member_cannot_approve(self):
        em = EM.objects.create(user=person("9400000030", "A"), environment=self.ward)
        m = person("9400000031", "Member"); join(m, self.ward); self.client.force_login(m)
        self.assertEqual(self.client.post(reverse("network_decide", args=[em.pk]), {"action": "approve"}).status_code, 404)

    def test_decline_removes_the_request(self):
        em = EM.objects.create(user=person("9400000032", "A"), environment=self.ward)
        self.client.force_login(self.admin)
        self.client.post(reverse("network_decide", args=[em.pk]), {"action": "decline"})
        self.assertFalse(EM.objects.filter(pk=em.pk).exists())

    def test_staff_can_approve_any_network(self):
        em = EM.objects.create(user=person("9400000033", "A"), environment=self.aps)
        self.client.force_login(self.admin)
        self.client.post(reverse("network_decide", args=[em.pk]), {"action": "approve"})
        em.refresh_from_db(); self.assertEqual(em.status, "approved")


class AdminAndModelTests(TestCase):
    def test_admin_approve_action_approves_and_activates(self):
        e = env("APS Noida")
        u = User.objects.create_user(username="9500000001", password="x", is_active=False); ResidentProfile.objects.create(user=u, house_number="1")
        em = EM.objects.create(user=u, environment=e)
        admin = person("9500000009", "Boss", staff=True); admin.is_superuser = True; admin.save()
        req = RequestFactory().post("/admin/"); req.user = admin
        req.session = {}; req._messages = FallbackStorage(req)
        ma = EnvironmentMembershipAdmin(EM, admin_site)
        ma.approve(req, EM.objects.filter(pk=em.pk))
        em.refresh_from_db(); u.refresh_from_db()
        self.assertEqual(em.status, "approved"); self.assertTrue(u.is_active)

    def test_slug_is_unique_and_generated(self):
        a, b = Environment.objects.create(name="DPS Noida"), Environment.objects.create(name="DPS  Noida!")
        self.assertEqual(a.slug, "dps-noida"); self.assertNotEqual(a.slug, b.slug)

    def test_default_network_exists_after_migrating(self):
        from django.apps import apps
        import importlib
        mod = importlib.import_module("core.migrations.0018_default_environment")
        u = person("9500000020", "Old"); post_ = Post.objects.create(author=u, body="OLD POST")
        pending = User.objects.create_user(username="9500000021", password="x", is_active=False)
        Environment.objects.all().delete()
        mod.forwards(apps, None)
        e = Environment.objects.get()
        self.assertEqual(EM.objects.get(user=u).status, "approved")
        self.assertEqual(EM.objects.get(user=pending).status, "pending")
        self.assertEqual(list(post_.environments.all()), [e])
        mod.forwards(apps, None)                                   # running twice changes nothing
        self.assertEqual(Environment.objects.count(), 1); self.assertEqual(EM.objects.count(), 2)

    def test_age_and_visibility(self):
        u = person("9500000030", "Age"); join(u)
        today = timezone.localdate()
        u.profile.date_of_birth = date(today.year - 24, today.month, 1); u.profile.save()
        self.assertEqual(u.profile.age, 24)
        v = person("9500000031", "Viewer"); join(v)
        self.client.force_login(v)
        self.assertIn("years", self.client.get(reverse("public_profile", args=[u.pk])).content.decode())
        u.profile.show_age = False; u.profile.save()
        self.assertNotIn(" years", self.client.get(reverse("public_profile", args=[u.pk])).content.decode())
        self.client.force_login(u)                                  # you always see your own
        self.assertIn(" years", self.client.get(reverse("profile")).content.decode())

    def test_future_date_of_birth_is_rejected(self):
        u = person("9500000032", "Time"); join(u); self.client.force_login(u)
        r = self.client.post(reverse("profile"), {"first_name": "Time", "last_name": "X", "house_number": "1",
                                                  "date_of_birth": (timezone.localdate() + timedelta(days=5)).isoformat()})
        u.profile.refresh_from_db()
        self.assertIsNone(u.profile.date_of_birth)


class MembershipAdminPageTests(TestCase):
    """Admin > Environment memberships first lists the environments; opening one lists only its people."""

    @classmethod
    def setUpTestData(cls):
        cls.ward, cls.aps = env("Ward 11-A"), env("APS Noida")
        cls.boss = person("9600000001", "Boss", staff=True); cls.boss.is_superuser = True; cls.boss.save()
        cls.w, cls.a = person("9600000002", "Wardperson"), person("9600000003", "Apsperson")
        join(cls.w, cls.ward); join(cls.a, cls.aps, status="pending")
        cls.url = reverse("admin:core_environmentmembership_changelist")

    def setUp(self):
        self.client.force_login(self.boss)

    def test_first_page_lists_environments_not_people(self):
        html = self.client.get(self.url).content.decode()
        self.assertIn("Ward 11-A", html); self.assertIn("APS Noida", html)
        self.assertIn("1 waiting", html)
        self.assertNotIn("Wardperson", html); self.assertNotIn("Apsperson", html)

    def test_opening_an_environment_lists_only_its_people(self):
        html = self.client.get(self.url, {"environment__id__exact": self.aps.pk}).content.decode()
        self.assertIn("Apsperson", html); self.assertNotIn("Wardperson", html)
        self.assertIn("APS Noida: people", html)

    def test_approve_action_works_from_the_filtered_list(self):
        em = EM.objects.get(user=self.a)
        r = self.client.post(self.url + f"?environment__id__exact={self.aps.pk}",
                             {"action": "approve", "_selected_action": [em.pk]})
        self.assertEqual(r.status_code, 302)
        em.refresh_from_db(); self.assertEqual(em.status, "approved")

    def test_show_everyone_link_still_works(self):
        html = self.client.get(self.url, {"all": 1}).content.decode()
        self.assertIn("Wardperson", html); self.assertIn("Apsperson", html)
