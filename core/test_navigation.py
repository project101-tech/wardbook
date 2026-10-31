"""The five main tabs and the sub-tab strips. Run with:  python manage.py test core.test_navigation"""
import re

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from .models import GroupMember, ChatGroup, Community, ResidentProfile
from .testing import env, join
from .navigation import HUBS, SUBTABS, nav_context

User = get_user_model()


class MappingTests(SimpleTestCase):
    def test_every_page_lands_in_exactly_one_place(self):
        expect = {"feed": "square", "announcements": "notices", "payments": "pay",
                  "inbox": "connect", "conversation": "connect", "group": "connect", "communities": "connect",
                  "community_detail": "connect", "directory": "connect", "public_profile": "connect",
                  "games": "hangout", "game_play": "hangout", "music": "hangout", "music_profile": "hangout", "music_manage": "hangout",
                  "profile": "", "notifications": "", "": ""}
        for name, hub in expect.items():
            ctx = nav_context(name)
            self.assertEqual(ctx["hub"], hub, name)
            self.assertEqual([h["key"] for h in ctx["hubs"] if h["active"]], [hub] if hub else [], name)

    def test_five_tabs_in_order(self):
        self.assertEqual([h["key"] for h in HUBS], ["square", "notices", "pay", "connect", "hangout"])

    def test_subtabs(self):
        self.assertEqual([t["label"] for t in nav_context("directory")["subtabs"]], ["Messages", "Communities", "Neighbours"])
        self.assertEqual([t["active"] for t in nav_context("directory")["subtabs"]], [False, False, True])
        self.assertEqual([t["active"] for t in nav_context("group")["subtabs"]], [True, False, False])
        self.assertEqual([t["label"] for t in nav_context("music_profile")["subtabs"]], ["Games", "Music Taste"])
        self.assertEqual([t["active"] for t in nav_context("game_play")["subtabs"]], [True, False])
        self.assertEqual(nav_context("feed")["subtabs"], [])


class PageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.me = User.objects.create_user(username="9100000001", password="x", first_name="Meera", last_name="K", is_active=True)
        ResidentProfile.objects.create(user=cls.me, house_number="5")
        cls.other = User.objects.create_user(username="9100000002", password="x", first_name="Raj", last_name="P", is_active=True)
        ResidentProfile.objects.create(user=cls.other, house_number="6")
        join(cls.me); join(cls.other)

    def setUp(self):
        self.client.force_login(self.me)

    def html(self, name, *args):
        r = self.client.get(reverse(name, args=args))
        self.assertEqual(r.status_code, 200, name)
        return r.content.decode()

    def bottom_bar(self, html):
        return re.search(r'<nav class="wb-tabs.*?</nav>', html, re.S).group(0)

    def test_bottom_bar_has_exactly_five_tabs_and_not_the_moved_ones(self):
        bar = self.bottom_bar(self.html("feed"))
        self.assertEqual(bar.count('<a class="wb-tab'), 5)
        for gone in ("Alerts", "Neighbours", "Me<", reverse("profile"), reverse("notifications"), reverse("directory")):
            self.assertNotIn(gone, bar, gone)
        for want in (reverse("feed"), reverse("announcements"), reverse("payments"), reverse("inbox"), reverse("games"), "Connect", "Hangout"):
            self.assertIn(want, bar, want)

    def test_top_bar_has_neighbours_bell_and_profile(self):
        html = self.html("feed")
        top = re.search(r"<header class=\"wb-top\">.*?</header>", html, re.S).group(0)
        for want in (reverse("directory"), reverse("notifications"), reverse("profile"), 'aria-label="Neighbours"', 'aria-label="Notifications"', 'aria-label="My profile"', "js-notifs"):
            self.assertIn(want, top, want)
        self.assertEqual(top.count('class="wb-toplink'), 5)            # the same five tabs on a computer

    def test_subtab_strip_on_connect_and_hangout_pages_only(self):
        for name, args, labels, active in [
            ("inbox", (), ["Messages", "Communities", "Neighbours"], "Messages"),
            ("communities", (), ["Messages", "Communities", "Neighbours"], "Communities"),
            ("directory", (), ["Messages", "Communities", "Neighbours"], "Neighbours"),
            ("conversation", (self.other.pk,), ["Messages", "Communities", "Neighbours"], "Messages"),
            ("public_profile", (self.other.pk,), ["Messages", "Communities", "Neighbours"], "Neighbours"),
            ("games", (), ["Games", "Music Taste"], "Games"),
            ("music", (), ["Games", "Music Taste"], "Music Taste"),
            ("music_manage", (), ["Games", "Music Taste"], "Music Taste"),
        ]:
            html = self.html(name, *args)
            strip = re.search(r'<nav class="wb-subtabs.*?</nav>', html, re.S)
            self.assertIsNotNone(strip, name)
            strip = strip.group(0)
            self.assertEqual(re.findall(r'<i class="bi [^"]+"></i>([A-Za-z ]+)', strip), labels, name)
            self.assertRegex(strip, r'class="wb-subtab active"[^>]*><i class="bi [^"]+"></i>' + active, name)
            # and the main tab is lit
            want = "Connect" if "Messages" in labels else "Hangout"
            self.assertRegex(self.bottom_bar(html), r'class="wb-tab active"[^>]*><i class="bi [^"]+"></i>' + want, name)
        for name in ("feed", "announcements", "payments", "profile", "notifications"):
            self.assertNotIn('class="wb-subtabs', self.html(name), name)

    def test_group_and_community_pages_get_the_strip(self):
        g = ChatGroup.objects.create(name="Gang", created_by=self.me)
        GroupMember.objects.create(group=g, user=self.me)
        self.assertIn('class="wb-subtabs', self.html("group", g.pk))
        c = Community.objects.create(name="Chess club", created_by=self.me, environment=env()) if hasattr(Community, "created_by") else None
        if c:
            c.members.add(self.me)
            self.assertIn('class="wb-subtabs', self.html("community_detail", c.pk))

    def test_anonymous_pages_have_no_nav(self):
        self.client.logout()
        html = self.client.get(reverse("login")).content.decode()
        self.assertNotIn("wb-tabs", html.replace(".wb-tabs", ""))
        self.assertNotIn('class="wb-subtabs', html)
