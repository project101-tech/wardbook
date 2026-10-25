"""Tests for Music Taste. Spotify is never contacted: core.spotify._http_get is replaced in every test.
Run with:  python manage.py test core.test_music"""
import json
import urllib.error
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from . import spotify
from .models import MusicPlaylist, MusicProfile, ResidentProfile

User = get_user_model()
ID1, ID2, ID3 = "37i9dQZF1DXcBWIGoYBM5M", "1A2B3C4D5E6F7G8H9I0J1K", "0123456789abcdefghij01"
OK_BODY = json.dumps({"title": "Today's Top Hits", "thumbnail_url": "https://image-cdn-ak.spotifycdn.com/image/abc",
                      "html": "<iframe></iframe>", "type": "rich"}).encode()


def ok(*a, **k):
    return 200, OK_BODY


def make(username, first, last, house="1"):
    u = User.objects.create_user(username=username, password="x", first_name=first, last_name=last, is_active=True)
    ResidentProfile.objects.create(user=u, house_number=house)
    return u


class SpotifyHelperTests(TestCase):
    def test_accepted_links(self):
        for raw in [f"https://open.spotify.com/playlist/{ID1}", f"https://open.spotify.com/playlist/{ID1}?si=abc123def",
                    f"http://open.spotify.com/playlist/{ID1}", f"open.spotify.com/playlist/{ID1}",
                    f"https://open.spotify.com/intl-fr/playlist/{ID1}?si=x", f"https://open.spotify.com/embed/playlist/{ID1}?utm_source=generator",
                    f"  https://open.spotify.com/playlist/{ID1}  ", f"spotify:playlist:{ID1}", f"https://OPEN.SPOTIFY.COM/playlist/{ID1}"]:
            self.assertEqual(spotify.parse_playlist(raw), ID1, raw)

    def test_rejected_links(self):
        bad = ["", "   ", "hello", "javascript:alert(1)", "data:text/html,<script>1</script>", "https://evil.com/playlist/" + ID1,
               f"https://open.spotify.com.evil.com/playlist/{ID1}", f"https://evil.com/open.spotify.com/playlist/{ID1}",
               f"https://user:pw@open.spotify.com/playlist/{ID1}", f"https://open.spotify.com:8443/playlist/{ID1}",
               f"https://open.spotify.com/playlist/{ID1}\nhttps://evil.com", "https://open.spotify.com/playlist/short",
               "https://open.spotify.com/playlist/" + "a" * 40, f"https://open.spotify.com/track/{ID1}", f"https://open.spotify.com/album/{ID1}",
               "https://open.spotify.com/", "https://spotify.link/abc", f"spotify:track:{ID1}", "https://open.spotify.com/playlist/" + ID1[:-1] + "!",
               f"https://open.spotify.com/playlist/{ID1}" + "x" * 400, "http://[::1"]
        for raw in bad:
            with self.assertRaises(spotify.LinkError, msg=raw):
                spotify.parse_playlist(raw)

    def test_urls_are_only_built_from_a_valid_id(self):
        self.assertEqual(spotify.embed_url(ID1), f"https://open.spotify.com/embed/playlist/{ID1}?utm_source=generator")
        self.assertEqual(spotify.embed_url('x"><script>'), "")

    def test_fetch_meta(self):
        with mock.patch("core.spotify._http_get", ok):
            m = spotify.fetch_meta(ID1)
        self.assertEqual((m["status"], m["title"]), ("ok", "Today's Top Hits"))
        self.assertTrue(m["cover_url"].startswith("https://image-cdn-ak.spotifycdn.com/"))

    def test_fetch_meta_failures_never_raise(self):
        err = lambda code: urllib.error.HTTPError("u", code, "m", {}, None)
        with mock.patch("core.spotify._http_get", side_effect=err(404)):
            self.assertEqual(spotify.fetch_meta(ID1)["status"], "unavailable")
        for exc in (err(500), err(302), TimeoutError(), OSError("down"), RuntimeError("x")):
            with mock.patch("core.spotify._http_get", side_effect=exc):
                self.assertEqual(spotify.fetch_meta(ID1)["status"], "unknown")
        for body in (b"not json", b"[1,2]", b"null"):
            with mock.patch("core.spotify._http_get", return_value=(200, body)):
                self.assertEqual(spotify.fetch_meta(ID1)["status"], "unknown")
        with mock.patch("core.spotify._http_get") as g:
            self.assertEqual(spotify.fetch_meta("not-an-id")["status"], "unknown")
            g.assert_not_called()

    def test_artwork_only_from_spotify_hosts(self):
        evil = json.dumps({"title": "T", "thumbnail_url": "https://evil.example/x.png"}).encode()
        with mock.patch("core.spotify._http_get", return_value=(200, evil)):
            self.assertEqual(spotify.fetch_meta(ID1)["cover_url"], "")
        for u in ("http://i.scdn.co/image/a", "https://i.scdn.co.evil.com/a", "javascript:alert(1)", "https://user@i.scdn.co/a"):
            self.assertEqual(spotify.safe_cover(u), "", u)
        self.assertTrue(spotify.safe_cover("https://i.scdn.co/image/a"))


class MusicViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.asmita = make("9000000001", "Asmita", "Rao", "12")
        cls.ravi = make("9000000002", "Ravi", "Shah", "7")
        cls.meera = make("9000000003", "Meera", "Iyer", "7")
        MusicProfile.objects.create(user=cls.asmita, is_shared=True, about="Old Bollywood on Sundays", favourites="Lata, Rahman")
        cls.pub = MusicPlaylist.objects.create(user=cls.asmita, spotify_id=ID1, title="Sunday Mornings", description="for chai", position=0,
                                               cover_url="https://i.scdn.co/image/a", meta_status="ok")
        cls.hid = MusicPlaylist.objects.create(user=cls.asmita, spotify_id=ID2, title="SECRET-GUILTY-PLEASURES", is_visible=False, position=1)
        # Meera added a visible playlist but never switched sharing on
        MusicProfile.objects.create(user=cls.meera, is_shared=False)
        cls.meera_pl = MusicPlaylist.objects.create(user=cls.meera, spotify_id=ID3, title="MEERA-PRIVATE", position=0)

    def login(self, u):
        self.client.force_login(u)

    # ---- 1/2: open the page and search ----
    def test_anonymous_is_sent_to_login(self):
        for name, args in [("music", []), ("music_manage", []), ("music_profile", [self.asmita.pk]), ("music_preview", [])]:
            r = self.client.get(reverse(name, args=args))
            self.assertEqual(r.status_code, 302, name)
            self.assertIn("/login/", r["Location"])
        for name, args in [("music_settings", []), ("music_add", []), ("music_edit", [self.pub.pk]), ("music_delete", [self.pub.pk]),
                           ("music_toggle", [self.pub.pk]), ("music_move", [self.pub.pk]), ("music_refresh", [self.pub.pk])]:
            r = self.client.post(reverse(name, args=args), {})
            self.assertEqual(r.status_code, 302, name)
        self.assertTrue(MusicPlaylist.objects.filter(pk=self.pub.pk).exists())

    def test_discover_lists_only_people_who_opted_in(self):
        self.login(self.ravi)
        r = self.client.get(reverse("music"))
        self.assertContains(r, "Music Taste")
        self.assertContains(r, "Asmita Rao")
        self.assertContains(r, "1 playlist")           # the hidden one is not counted
        self.assertNotContains(r, "Meera")
        self.assertNotContains(r, "SECRET-GUILTY-PLEASURES")

    def test_search(self):
        self.login(self.ravi)
        self.assertContains(self.client.get(reverse("music"), {"q": "asm"}), "Asmita Rao")
        self.assertContains(self.client.get(reverse("music"), {"q": "asmita rao"}), "Asmita Rao")
        self.assertContains(self.client.get(reverse("music"), {"q": "12"}), "Asmita Rao")
        r = self.client.get(reverse("music"), {"q": "zzz"})
        self.assertContains(r, "No neighbour matching")
        r = self.client.get(reverse("music"), {"q": "meera"})        # exists, but has not shared
        self.assertNotContains(r, "Meera Iyer")
        r = self.client.get(reverse("music"), {"q": "asm", "partial": "1"})
        self.assertNotContains(r, "<html")
        self.assertContains(r, "Asmita Rao")

    def test_discover_empty_state_and_own_card_hidden(self):
        self.login(self.asmita)
        r = self.client.get(reverse("music"))
        self.assertContains(r, "Nobody has shared music yet")       # she is the only sharer and never sees herself in the grid

    def test_nav_item_present(self):
        self.login(self.ravi)
        r = self.client.get(reverse("feed"))
        self.assertContains(r, reverse("music"))
        self.assertContains(r, "Music Taste")
        self.assertContains(r, "bi-music-note-beamed")

    # ---- 5/6: embedded player + open in Spotify ----
    def test_profile_embeds_official_player(self):
        self.login(self.ravi)
        r = self.client.get(reverse("music_profile", args=[self.asmita.pk]))
        self.assertContains(r, f'src="https://open.spotify.com/embed/playlist/{ID1}?utm_source=generator"')
        self.assertContains(r, f'href="https://open.spotify.com/playlist/{ID1}"')
        self.assertContains(r, "Open in Spotify")
        self.assertContains(r, "Sunday Mornings")
        self.assertContains(r, "Old Bollywood on Sundays")
        self.assertContains(r, "Lata, Rahman")
        self.assertContains(r, 'loading="lazy"')
        self.assertContains(r, 'sandbox="allow-scripts')
        html = r.content.decode()
        self.assertNotIn("autoplay", html.lower())
        self.assertEqual(html.count("<iframe"), 1)
        self.assertNotContains(r, "Manage my music")                # no management UI on someone else's profile
        self.assertNotContains(r, "SECRET-GUILTY-PLEASURES")        # 9: hidden playlist never reaches another browser

    def test_sandbox_can_be_switched_off(self):
        self.login(self.ravi)
        with self.settings(SPOTIFY_EMBED_SANDBOX="-"):
            r = self.client.get(reverse("music_profile", args=[self.asmita.pk]))
        self.assertNotContains(r, "sandbox=")

    # ---- 9/12: privacy + empty states ----
    def test_unshared_profile_shows_nothing(self):
        self.login(self.ravi)
        for target in (self.meera, self.ravi):                       # Meera: playlists but opted out. (Ravi: never opened Music Taste)
            r = self.client.get(reverse("music_profile", args=[target.pk]))
            self.assertEqual(r.status_code, 200)
            self.assertNotContains(r, "MEERA-PRIVATE")
            self.assertNotContains(r, "<iframe")
        r = self.client.get(reverse("music_profile", args=[self.meera.pk]))
        self.assertContains(r, "hasn't shared any music yet")

    def test_switching_sharing_off_hides_everything_again(self):
        self.login(self.asmita)
        self.client.post(reverse("music_settings"), {"about": "x", "favourites": ""})   # checkbox absent = off
        self.assertFalse(MusicProfile.objects.get(user=self.asmita).is_shared)
        self.login(self.ravi)
        r = self.client.get(reverse("music_profile", args=[self.asmita.pk]))
        self.assertNotContains(r, "Sunday Mornings")
        self.assertNotContains(r, "<iframe")
        self.assertNotContains(self.client.get(reverse("music")), "Asmita Rao")

    def test_owner_preview_and_private_notice(self):
        self.login(self.meera)
        r = self.client.get(reverse("music_profile", args=[self.meera.pk]))
        self.assertContains(r, "Neighbours can't see any of this yet")
        self.assertContains(r, "Manage my music")

    def test_inactive_or_unknown_users_404(self):
        self.login(self.ravi)
        self.assertEqual(self.client.get(reverse("music_profile", args=[99999])).status_code, 404)
        u = make("9000000009", "Gone", "Away"); u.is_active = False; u.save()
        self.assertEqual(self.client.get(reverse("music_profile", args=[u.pk])).status_code, 404)

    def test_unavailable_playlist_does_not_break_page(self):
        MusicPlaylist.objects.filter(pk=self.pub.pk).update(meta_status="unavailable")
        self.login(self.ravi)
        r = self.client.get(reverse("music_profile", args=[self.asmita.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Spotify couldn't find this playlist")
        self.assertContains(r, f'href="https://open.spotify.com/playlist/{ID1}"')
        self.assertNotContains(r, "<iframe")
        MusicPlaylist.objects.filter(pk=self.pub.pk).update(meta_status="ok")

    def test_user_text_is_escaped(self):
        MusicPlaylist.objects.filter(pk=self.pub.pk).update(description="<script>alert(1)</script>", title='"><img src=x onerror=1>')
        MusicProfile.objects.filter(user=self.asmita).update(about="<b>hi</b>")
        self.login(self.ravi)
        html = self.client.get(reverse("music_profile", args=[self.asmita.pk])).content.decode()
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertNotIn("<img src=x", html)
        self.assertNotIn("<b>hi</b>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_back_link_is_not_an_open_redirect(self):
        self.login(self.ravi)
        r = self.client.get(reverse("music_profile", args=[self.asmita.pk]), {"next": "https://evil.example/x"})
        self.assertNotContains(r, "evil.example")
        r = self.client.get(reverse("music_profile", args=[self.asmita.pk]), {"next": "/music/?q=a"})
        self.assertContains(r, 'href="/music/?q=a"')

    # ---- 3/4/10: adding playlists ----
    def test_add_valid_playlist(self):
        self.login(self.ravi)
        with mock.patch("core.spotify._http_get", ok):
            r = self.client.post(reverse("music_add"), {"url": f"https://open.spotify.com/playlist/{ID1}?si=zz", "description": " gym ", "is_visible": "on"})
        self.assertRedirects(r, reverse("music_manage"))
        pl = MusicPlaylist.objects.get(user=self.ravi)
        self.assertEqual((pl.spotify_id, pl.title, pl.description, pl.meta_status, pl.is_visible), (ID1, "Today's Top Hits", "gym", "ok", True))
        self.assertTrue(pl.cover_url.startswith("https://image-cdn-ak.spotifycdn.com/"))
        self.assertEqual(pl.url, f"https://open.spotify.com/playlist/{ID1}")      # rebuilt, tracking parameter dropped
        self.assertFalse(MusicProfile.objects.get(user=self.ravi).is_shared)       # adding never publishes by itself
        self.assertNotContains(self.client.get(reverse("music")), "Ravi Shah")

    def test_add_can_be_hidden_and_goes_last(self):
        self.login(self.asmita)
        with mock.patch("core.spotify._http_get", ok):
            self.client.post(reverse("music_add"), {"url": f"spotify:playlist:{ID3}"})   # checkbox absent = not visible
        pl = MusicPlaylist.objects.get(user=self.asmita, spotify_id=ID3)
        self.assertFalse(pl.is_visible)
        self.assertEqual(pl.position, 2)

    def test_add_rejects_bad_links_safely(self):
        self.login(self.ravi)
        for bad in ["", "nonsense", "https://evil.com/x", f"https://open.spotify.com/track/{ID1}", "javascript:alert(1)", f"https://open.spotify.com.evil.com/playlist/{ID1}"]:
            with mock.patch("core.spotify._http_get") as g:
                r = self.client.post(reverse("music_add"), {"url": bad})
                g.assert_not_called()                                  # nothing is ever fetched for an invalid link
            self.assertEqual(r.status_code, 400, bad)
            self.assertContains(r, "text-danger", status_code=400)
        self.assertFalse(MusicPlaylist.objects.filter(user=self.ravi).exists())

    def test_add_duplicate_rejected(self):
        self.login(self.asmita)
        with mock.patch("core.spotify._http_get", ok):
            r = self.client.post(reverse("music_add"), {"url": f"https://open.spotify.com/playlist/{ID1}"})
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, "already added", status_code=400)
        self.assertEqual(MusicPlaylist.objects.filter(user=self.asmita, spotify_id=ID1).count(), 1)
        with mock.patch("core.spotify._http_get", ok):               # the same playlist is fine for a different resident
            self.client.force_login(self.ravi)
            self.client.post(reverse("music_add"), {"url": f"https://open.spotify.com/playlist/{ID1}"})
        self.assertTrue(MusicPlaylist.objects.filter(user=self.ravi, spotify_id=ID1).exists())

    def test_add_works_when_spotify_cannot_be_reached(self):
        self.login(self.ravi)
        with mock.patch("core.spotify._http_get", side_effect=OSError("offline")):
            r = self.client.post(reverse("music_add"), {"url": f"https://open.spotify.com/playlist/{ID2}"})
        self.assertRedirects(r, reverse("music_manage"))
        pl = MusicPlaylist.objects.get(user=self.ravi)
        self.assertEqual((pl.meta_status, pl.title, pl.cover_url), ("unknown", "", ""))
        page = self.client.get(reverse("music_manage"))
        self.assertContains(page, "Spotify playlist")                # graceful fallback title

    def test_add_flags_missing_playlist_but_keeps_it(self):
        self.login(self.ravi)
        with mock.patch("core.spotify._http_get", side_effect=urllib.error.HTTPError("u", 404, "nf", {}, None)):
            r = self.client.post(reverse("music_add"), {"url": f"https://open.spotify.com/playlist/{ID2}"}, follow=True)
        self.assertContains(r, "couldn&#x27;t find that playlist")
        self.assertEqual(MusicPlaylist.objects.get(user=self.ravi).meta_status, "unavailable")

    def test_playlist_limit(self):
        self.login(self.ravi)
        with self.settings(MUSIC_MAX_PLAYLISTS=2), mock.patch("core.spotify._http_get", ok):
            for i in range(2):
                self.client.post(reverse("music_add"), {"url": f"spotify:playlist:{'a' * 21}{i}"})
            r = self.client.post(reverse("music_add"), {"url": f"spotify:playlist:{ID1}"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(MusicPlaylist.objects.filter(user=self.ravi).count(), 2)

    # ---- 7: edit / remove own ----
    def test_edit_toggle_move_delete_own(self):
        self.login(self.asmita)
        self.client.post(reverse("music_edit", args=[self.pub.pk]), {"title": "Renamed", "description": "new note", "is_visible": "on"})
        self.pub.refresh_from_db()
        self.assertEqual((self.pub.title, self.pub.description, self.pub.is_visible), ("Renamed", "new note", True))
        self.client.post(reverse("music_toggle", args=[self.pub.pk]))
        self.pub.refresh_from_db(); self.assertFalse(self.pub.is_visible)
        self.client.post(reverse("music_toggle", args=[self.pub.pk]))
        self.client.post(reverse("music_move", args=[self.pub.pk]), {"dir": "down"})
        order = list(MusicPlaylist.objects.filter(user=self.asmita).values_list("pk", flat=True))
        self.assertEqual(order, [self.hid.pk, self.pub.pk])
        self.client.post(reverse("music_move", args=[self.pub.pk]), {"dir": "down"})      # already last: no change, no error
        self.assertEqual(list(MusicPlaylist.objects.filter(user=self.asmita).values_list("pk", flat=True)), order)
        self.assertEqual(self.client.post(reverse("music_move", args=[self.pub.pk]), {"dir": "sideways"}).status_code, 404)
        self.client.post(reverse("music_delete", args=[self.hid.pk]))
        self.assertFalse(MusicPlaylist.objects.filter(pk=self.hid.pk).exists())
        self.assertEqual(MusicPlaylist.objects.get(pk=self.pub.pk).position, 0)           # numbering closes the gap

    def test_refresh_updates_details(self):
        MusicPlaylist.objects.filter(pk=self.pub.pk).update(title="", cover_url="", meta_status="unknown")
        self.login(self.asmita)
        with mock.patch("core.spotify._http_get", ok):
            self.client.post(reverse("music_refresh", args=[self.pub.pk]))
        self.pub.refresh_from_db()
        self.assertEqual((self.pub.title, self.pub.meta_status), ("Today's Top Hits", "ok"))

    def test_edit_validation(self):
        self.login(self.asmita)
        r = self.client.post(reverse("music_edit", args=[self.pub.pk]), {"title": "t" * 500, "description": "", "is_visible": "on"}, follow=True)
        self.assertContains(r, "Couldn&#x27;t save that playlist")
        self.pub.refresh_from_db(); self.assertEqual(self.pub.title, "Sunday Mornings")

    # ---- 8: nobody touches anybody else's entries ----
    def test_cannot_modify_other_residents_playlists(self):
        self.login(self.ravi)
        for name, data in [("music_edit", {"title": "pwned", "is_visible": "on"}), ("music_toggle", {}), ("music_move", {"dir": "down"}),
                           ("music_refresh", {}), ("music_delete", {})]:
            r = self.client.post(reverse(name, args=[self.pub.pk]), data)
            self.assertEqual(r.status_code, 404, name)
        self.pub.refresh_from_db()
        self.assertEqual((self.pub.title, self.pub.is_visible, self.pub.position), ("Sunday Mornings", True, 0))
        self.assertTrue(MusicPlaylist.objects.filter(pk=self.pub.pk).exists())

    def test_manage_page_only_lists_my_playlists(self):
        self.login(self.ravi)
        r = self.client.get(reverse("music_manage"))
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "Sunday Mornings")
        self.assertNotContains(r, "SECRET-GUILTY-PLEASURES")
        self.login(self.asmita)
        r = self.client.get(reverse("music_manage"))
        self.assertContains(r, "SECRET-GUILTY-PLEASURES")                # the owner sees their own hidden ones
        self.assertContains(r, "Hidden")

    def test_writes_are_post_only_and_csrf_protected(self):
        self.login(self.asmita)
        for name in ("music_settings", "music_add"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 405)
        for name in ("music_edit", "music_toggle", "music_move", "music_refresh", "music_delete"):
            self.assertEqual(self.client.get(reverse(name, args=[self.pub.pk])).status_code, 405)
        from django.test import Client
        c = Client(enforce_csrf_checks=True); c.force_login(self.asmita)
        self.assertEqual(c.post(reverse("music_delete", args=[self.pub.pk])).status_code, 403)
        self.assertTrue(MusicPlaylist.objects.filter(pk=self.pub.pk).exists())

    def test_settings_save(self):
        self.login(self.ravi)
        self.client.post(reverse("music_settings"), {"is_shared": "on", "about": "lofi", "favourites": "ARR"})
        mp = MusicProfile.objects.get(user=self.ravi)
        self.assertEqual((mp.is_shared, mp.about, mp.favourites), (True, "lofi", "ARR"))

    # ---- preview endpoint ----
    def test_preview(self):
        self.login(self.ravi)
        with mock.patch("core.spotify._http_get", ok):
            j = self.client.get(reverse("music_preview"), {"url": f"https://open.spotify.com/playlist/{ID1}"}).json()
        self.assertEqual((j["ok"], j["id"], j["title"], j["status"], j["duplicate"]), (True, ID1, "Today's Top Hits", "ok", False))
        with mock.patch("core.spotify._http_get") as g:
            r = self.client.get(reverse("music_preview"), {"url": "https://evil.com/x"})
            g.assert_not_called()
        self.assertEqual(r.status_code, 400)
        self.assertFalse(r.json()["ok"])
        self.login(self.asmita)
        with mock.patch("core.spotify._http_get", ok):
            self.assertTrue(self.client.get(reverse("music_preview"), {"url": f"spotify:playlist:{ID1}"}).json()["duplicate"])

    def test_preview_is_rate_limited(self):
        from django.core.cache import cache
        cache.clear()
        self.login(self.ravi)
        with mock.patch("core.spotify._http_get", ok):
            codes = [self.client.get(reverse("music_preview"), {"url": f"spotify:playlist:{ID1}"}).status_code for _ in range(32)]
        self.assertEqual(codes[:30], [200] * 30)
        self.assertEqual(codes[30:], [429, 429])
        cache.clear()

    # ---- 14: existing pages still fine; profile link ----
    def test_existing_pages_still_load_and_profile_links_to_music(self):
        self.login(self.ravi)
        for name in ("feed", "directory", "announcements", "games", "profile", "communities", "inbox", "notifications", "payments"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        r = self.client.get(reverse("public_profile", args=[self.asmita.pk]))
        self.assertContains(r, reverse("music_profile", args=[self.asmita.pk]))
        r = self.client.get(reverse("public_profile", args=[self.meera.pk]))
        self.assertNotContains(r, reverse("music_profile", args=[self.meera.pk]))      # she has not opted in

    def test_deleting_a_resident_removes_their_music(self):
        self.asmita.delete()
        self.assertFalse(MusicPlaylist.objects.filter(spotify_id=ID1).exists())
        self.assertFalse(MusicProfile.objects.filter(is_shared=True).exists())


class MusicBackLinkTests(TestCase):
    def test_cards_link_back_to_the_real_page_even_from_live_search(self):
        a, r = make("9000000011", "Asha", "N", "3"), make("9000000012", "Ravi", "S", "4")
        MusicProfile.objects.create(user=a, is_shared=True)
        MusicPlaylist.objects.create(user=a, spotify_id=ID1, title="T")
        self.client.force_login(r)
        html = self.client.get(reverse("music"), {"q": "as", "partial": "1"}).content.decode()
        self.assertIn("next=/music/%3Fq%3Das", html)
        self.assertNotIn("partial", html)
