# This update: Music Taste (Spotify playlists + embedded player)

Built on top of the current `main` (commit 2e9147b, "Removed society naming").
Copy the files below into your repo (same paths), then run **`python manage.py migrate`** (adds migration `0016_music_taste`: two new tables, nothing existing is touched).
**No new Python packages. No new environment variables are required. No Spotify developer account is needed.**

## New files
| File | What it is |
|------|-----------|
| `core/music.py` | Views: discover/search, a resident's music profile, manage page, add / edit / hide / reorder / remove / refresh, link preview |
| `core/spotify.py` | Link validation (only `open.spotify.com/playlist/<id>`), builds link + embed URL from the validated id, asks Spotify's public oEmbed for title + artwork (best effort, never blocks saving) |
| `core/migrations/0016_music_taste.py` | `MusicProfile`, `MusicPlaylist` |
| `core/test_music.py` | 39 automated tests (`python manage.py test core.test_music`) |
| `templates/music.html`, `_music_results.html`, `music_profile.html`, `music_manage.html`, `_music_embed.html` | The pages |
| `static/css/music.css`, `static/js/music.js` | Styling, live search, link preview |

## Changed files
| File | Change |
|------|--------|
| `core/models.py` | Added `MusicProfile`, `MusicPlaylist` (appended at the end) |
| `config/urls.py` | `music/...` routes |
| `config/settings.py` | Three optional settings: `MUSIC_MAX_PLAYLISTS` (20), `SPOTIFY_OEMBED_TIMEOUT` (4 s), `SPOTIFY_EMBED_SANDBOX` |
| `core/admin.py` | Admin pages for both models (moderation) |
| `core/views.py` | `public_profile` passes `music_count` (3 lines) |
| `templates/base.html` | "Music Taste" in the top bar and a "Music" tab on phones. Top-bar links are now `nowrap` with slightly tighter spacing, because the bar was already wrapping/overflowing at tablet widths and one more link made it worse |
| `templates/public_profile.html` | "Music Taste" button when that neighbour shares music |
| `README.md` | Short Music Taste section |

## How privacy works
* Nothing is visible until the resident switches **Share my music with neighbours** on (off by default). Adding a playlist never publishes by itself.
* Each playlist also has Visible / Hidden. Hidden ones are never sent to anyone else's browser.
* Switching sharing off hides everything again immediately.
* Every edit is a POST that looks the playlist up by `user=request.user`; someone else's playlist is simply a 404.

## Optional settings
`SPOTIFY_EMBED_SANDBOX` is the iframe `sandbox` attribute for Spotify's player. If a browser ever blocks playback, set it to `-` in Render to switch the sandbox off (no redeploy of code needed).

## Spotify limits (why there is no "Connect Spotify" button)
Spotify's February 2026 rules cap development-mode apps at 5 users and require the developer to have Premium; extended access needs a registered business with a very large user base. Spotify also removed the endpoint that listed another person's playlists. So linking accounts cannot work for a society. Pasting a link + Spotify's official embed works for everyone, with no keys.
Private playlists cannot be played by neighbours: the owner must make the playlist public in Spotify.
