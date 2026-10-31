"""The full-screen phone chat: markup and the extra fields the page uses. Run with:  python manage.py test core.test_chat"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import ChatGroup, GroupMember, Message, ResidentProfile

User = get_user_model()


class ChatScreenTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.me = User.objects.create_user(username="9200000001", password="x", first_name="Meera", last_name="K", is_active=True)
        cls.other = User.objects.create_user(username="9200000002", password="x", first_name="Raj", last_name="P", is_active=True)
        for u in (cls.me, cls.other):
            ResidentProfile.objects.get_or_create(user=u)
        Message.objects.create(sender=cls.other, recipient=cls.me, body="hello there")

    def setUp(self):
        self.client.force_login(self.me)

    def test_dm_page_is_a_full_screen_chat_on_phones(self):
        html = self.client.get(reverse("conversation", args=[self.other.pk])).content.decode()
        self.assertIn("classList.add('chat-open')", html)          # hides the site bars on phones
        self.assertIn('id="chat-shell"', html)
        self.assertIn('class="composer"', html)
        self.assertIn('<textarea id="msg"', html)                  # grows as you type
        self.assertIn('data-day="', html)                          # day labels
        self.assertIn('aria-label="Back to messages"', html)

    def test_inbox_is_not_full_screen(self):
        html = self.client.get(reverse("inbox")).content.decode()
        self.assertNotIn("classList.add('chat-open')", html)
        self.assertNotIn('id="chat-shell"', html)

    def test_group_page_uses_the_same_screen(self):
        g = ChatGroup.objects.create(name="Block A", created_by=self.me)
        GroupMember.objects.create(group=g, user=self.me)
        GroupMember.objects.create(group=g, user=self.other)
        html = self.client.get(reverse("group", args=[g.pk])).content.decode()
        self.assertIn('id="chat-shell"', html)
        self.assertIn("Block A", html)
        self.assertIn('data-mention="group:%d"' % g.pk, html)

    def test_api_messages_carry_day_and_short_clock(self):
        j = self.client.get(reverse("conversation_api", args=[self.other.pk]) + "?after=0").json()
        m = j["messages"][0]
        self.assertRegex(m["day"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertRegex(m["clock"], r"^\d{1,2}:\d{2} (AM|PM)$")
        self.assertIn(m["clock"], m["time"])                       # old field still there
