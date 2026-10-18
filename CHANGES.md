# This update: seen receipts, instant updates, phone push

Copy every file listed below into your repo (keep the same paths), then commit.
After deploying, run migrations (`python manage.py migrate` locally; Render does it on deploy if your build runs it).
No new Python packages.

## New files (add)
| File | What it is |
|------|-----------|
| `core/push.py` | Sends phone notifications in the background, checks your VAPID keys, removes dead phones |
| `core/live.py` | The "live" channel that makes chat, feed, games and badges update instantly |
| `core/migrations/0013_message_read_at.py` | Adds the "seen at" time to messages |
| `templates/sw.js` | The service worker that shows notifications on the phone |
| `templates/_post.html` | One post card (so new posts can be added live) |
| `templates/_threads.html` | The conversation list (so it can refresh live) |
| `static/icon-192.png`, `static/icon-512.png`, `static/badge-96.png` | Notification icons |

## Changed files (replace whole file)
`config/settings.py`, `config/urls.py`, `core/apps.py`, `core/models.py`, `core/views.py`, `render.yaml`,
`templates/base.html`, `templates/feed.html`, `templates/game_play.html`, `templates/games.html`,
`templates/messages.html`, `templates/notifications.html`

## What you get
1. **Seen receipts.** Under your last message in a DM you see *Sent* then *Seen · 10:10 PM*. In groups: *Seen by 3 of 5*.
2. **Instant updates.** New messages, typing, notifications, badges, posts, comments, likes and game moves appear in well under a second, without refreshing. A small toast pops up for new notifications.
3. **Phone push.** Notifications appear in the phone's notification bar even when Wardbook is closed. Tapping one opens the right chat/page. Several messages from one person collapse into "Bob (3 new messages)".

## Why push never worked before
`core/views.py` called `webpush(..., urgency="high")`. That function has no `urgency` argument, so every push crashed
with a TypeError that a broad `except` silently swallowed. It is now sent as the `Urgency: high` header.

## Render settings (important)
- **Start command** must be `gunicorn config.wsgi:application --worker-class gthread --workers 1 --threads 40 --timeout 60`
  (`render.yaml` has it; if your service was created earlier, also change it in the Render dashboard → Settings).
  Keep **workers at 1**: the live channel keeps its state in memory. Extra workers will not break anything, but chat/feed/games then refresh only every 30–60 s.
- `VAPID_PUBLIC_KEY` and `VAPID_PRIVATE_KEY` must be **one matching pair**. The Notifications page now tells you if they are not.
- Set `VAPID_CLAIMS_EMAIL` to a real address (e.g. `mailto:you@gmail.com`).

## How to turn on push on a phone
Open Wardbook in Chrome → tap **Turn on** in the bar (or Notifications → Turn on) → allow → **Send a test**.
