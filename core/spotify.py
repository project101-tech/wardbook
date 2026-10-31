"""Everything Spotify for Music Taste, in one small file with no Django views in it.

* parse_playlist(): turns whatever a resident pasted into a validated playlist id, or a friendly error.
* canonical_url() / embed_url(): the only way a link or an iframe address is ever built, always from a validated id.
* fetch_meta(): asks Spotify's public oEmbed endpoint for the title and artwork. No login, no tokens, best effort.

Why no "connect your Spotify account"? Checked against Spotify's Web API rules (February 2026): apps in development
mode are limited to 5 users and need a Premium-owning developer, and extended access needs a registered business with
a very large user base, so it cannot work for a whole society. Spotify also stopped returning other people's playlists.
Pasting a link + the official embed works for everyone, so that is the whole feature.
"""
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings

ID_RE = re.compile(r"^[A-Za-z0-9]{22}$")
SPOTIFY_HOST = "open.spotify.com"
OEMBED_URL = "https://open.spotify.com/oembed"
# artwork is only ever shown from Spotify's own image hosts
COVER_HOSTS = ("scdn.co", "spotifycdn.com")
OTHER_KINDS = {"track": "a song", "album": "an album", "artist": "an artist", "show": "a podcast", "episode": "a podcast episode",
               "user": "a profile", "audiobook": "an audiobook"}


class LinkError(ValueError):
    """Message is safe to show to the resident."""


def parse_playlist(raw):
    """Return the 22-character playlist id inside a pasted link, or raise LinkError.

    Accepts open.spotify.com/playlist/<id> (with ?si= tracking, an intl-xx/ prefix or the /embed/ form) and the
    spotify:playlist:<id> URI. Everything else is refused."""
    raw = (raw or "").strip()
    if not raw:
        raise LinkError("Paste a Spotify playlist link.")
    if len(raw) > 300 or re.search(r"\s", raw):
        raise LinkError("That doesn't look like a single Spotify link.")
    m = re.fullmatch(r"spotify:(\w+):([A-Za-z0-9]+)", raw, re.I)
    if m:
        kind, ident = m.group(1).lower(), m.group(2)
    else:
        if "://" not in raw:
            raw = "https://" + raw
        try:
            parts = urllib.parse.urlsplit(raw)
            host, port = parts.hostname, parts.port
        except ValueError:
            raise LinkError("That doesn't look like a Spotify link.")
        if parts.scheme not in ("http", "https") or parts.username or parts.password or port:
            raise LinkError("That doesn't look like a Spotify link.")
        if host == "spotify.link" or host == "spotify.app.link":
            raise LinkError("That's a short Spotify link. Open it, then copy the full link from Share → Copy link to playlist.")
        if host != SPOTIFY_HOST:
            raise LinkError("Only open.spotify.com playlist links can be added.")
        segs = [s for s in parts.path.split("/") if s]
        if segs and segs[0].startswith("intl-"):
            segs = segs[1:]
        if segs and segs[0] == "embed":
            segs = segs[1:]
        if len(segs) < 2:
            raise LinkError("That link doesn't point to a playlist.")
        kind, ident = segs[0].lower(), segs[1]
    if kind != "playlist":
        what = OTHER_KINDS.get(kind)
        raise LinkError(f"That link is {what}, not a playlist. Open the playlist in Spotify and copy its link."
                        if what else "That link doesn't point to a playlist.")
    if not ID_RE.match(ident):
        raise LinkError("That playlist link looks incomplete. Copy it again from Spotify.")
    return ident


def canonical_url(spotify_id):
    return f"https://{SPOTIFY_HOST}/playlist/{spotify_id}"


def embed_url(spotify_id):
    if not ID_RE.match(spotify_id or ""):
        return ""
    return f"https://{SPOTIFY_HOST}/embed/playlist/{spotify_id}?utm_source=generator"


def safe_cover(url):
    """Artwork address if (and only if) it is https on one of Spotify's image hosts."""
    try:
        parts = urllib.parse.urlsplit(url or "")
    except ValueError:
        return ""
    host = (parts.hostname or "").lower()
    if parts.scheme == "https" and not parts.username and any(host == h or host.endswith("." + h) for h in COVER_HOSTS):
        return url[:500]
    return ""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None


def _http_get(url, timeout):
    """One GET with no redirects and a size cap. Split out so tests can replace it."""
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "Wardbook/1.0 (music taste)"})
    opener = urllib.request.build_opener(_NoRedirect)
    with opener.open(req, timeout=timeout) as r:
        return r.status, r.read(65536)


def fetch_meta(spotify_id):
    """Ask Spotify's oEmbed endpoint about a playlist. Never raises.

    Returns {"status": "ok" | "unavailable" | "unknown", "title": str, "cover_url": str}. "unavailable" means Spotify
    answered "not found" (private or deleted); "unknown" means we could not get an answer, which must never stop a
    valid link from being saved."""
    out = {"status": "unknown", "title": "", "cover_url": ""}
    if not ID_RE.match(spotify_id or ""):
        return out
    url = OEMBED_URL + "?" + urllib.parse.urlencode({"url": canonical_url(spotify_id)})
    try:
        status, body = _http_get(url, getattr(settings, "SPOTIFY_OEMBED_TIMEOUT", 4))
    except urllib.error.HTTPError as e:
        if e.code in (400, 403, 404):
            out["status"] = "unavailable"
        return out
    except Exception:
        return out
    if status != 200:
        return out
    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        return out
    if not isinstance(data, dict):
        return out
    title = data.get("title")
    out["title"] = title.strip()[:200] if isinstance(title, str) else ""
    thumb = data.get("thumbnail_url")
    out["cover_url"] = safe_cover(thumb) if isinstance(thumb, str) else ""
    out["status"] = "ok"
    return out
