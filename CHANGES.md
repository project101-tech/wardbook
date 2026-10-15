# What changed in this update, file by file

You do NOT need to hand-merge anything. Every file below is complete in the zip. Where a change is small, the exact snippet is shown so you can paste it instead.

**Order:** copy the files → `python manage.py migrate` → restart `runserver`. No new packages. Your existing users, posts and messages are kept.

| # | File | What to do |
|---|------|-----------|
| 1 | `core/models.py` | **Replace whole file** |
| 2 | `core/migrations/0002_events_payments_groups_media.py` | **Add this new file** (or delete it and run `python manage.py makemigrations core`) |
| 3 | `core/admin.py` | **Replace whole file** |
| 4 | `core/views.py` | **Replace whole file** |
| 5 | `config/urls.py` | **Paste a block** (below) |
| 6 | `templates/base.html` | **Replace whole file** (blue theme, W logo, Payments in nav) |
| 7 | `templates/announcements.html` | **Replace whole file** (RSVP + thumbs-up + pay buttons) |
| 8 | `templates/payments.html` | **Add new file** |
| 9 | `templates/messages.html` | **Replace whole file** (groups, photos, voice notes) |
| 10 | `templates/auth.html` | **Change one line** (below) |
| 11 | `templates/feed.html` | **Change two lines** (below) |
| 12 | `config/settings.py` | **Nothing new**, but make sure the two cookie-name lines from the CSRF fix are there (below) |

Unchanged: `directory.html`, `profile.html`, `render.yaml`, `requirements.txt`.

---

## 5. `config/urls.py`

Inside `urlpatterns`, **replace** the notices / me / messages lines with this block (keep everything else):

```python
    path("notices/", views.announcements, name="announcements"),
    path("notices/<int:pk>/respond/", views.respond, name="respond"),
    path("payments/", views.payments, name="payments"),
    path("payments/<int:pk>/paid/", views.pay, name="pay"),
    path("neighbours/", views.directory, name="directory"),
    path("me/", views.profile, name="profile"),
    path("messages/", views.inbox, name="inbox"),
    path("messages/<int:user_id>/", views.conversation, name="conversation"),
    path("messages/<int:user_id>/api/", views.conversation_api, name="conversation_api"),
    path("groups/new/", views.group_new, name="group_new"),
    path("groups/<int:pk>/", views.group_chat, name="group"),
    path("groups/<int:pk>/api/", views.group_api, name="group_api"),
    path("groups/<int:pk>/add/", views.group_add, name="group_add"),
    path("groups/<int:pk>/leave/", views.group_leave, name="group_leave"),
```

## 10. `templates/auth.html`

Near the top, **replace** this line:

```html
    <span class="wb-logo mb-3" style="width:64px;height:64px;font-size:2rem;border-radius:20px;"><i class="bi bi-houses-fill"></i></span>
```

**with:**

```html
    <svg class="mb-3 mx-auto" style="width:72px;height:72px;display:block" aria-hidden="true"><use href="#wb-logo"/></svg>
```

## 11. `templates/feed.html`

In the notice banner at the top, **replace** both uses of `var(--wb-saffron)` with `var(--wb-accent)`:

```html
     style="border-left: 5px solid var(--wb-accent); color: inherit;">
    <i class="bi bi-megaphone-fill fs-4" style="color: var(--wb-accent)"></i>
```

## 12. `config/settings.py`

Just above `LOGIN_URL = "login"`:

```python
CSRF_COOKIE_NAME = "wardbook_csrftoken"
SESSION_COOKIE_NAME = "wardbook_sessionid"
```

---

## Where each new feature lives

| Feature | Model (`models.py`) | Admin (`admin.py`) | View (`views.py`) | Template |
|---|---|---|---|---|
| Event RSVP (with undo) | `Announcement.event_date/venue`, `AnnouncementResponse` | `AnnouncementAdmin` | `respond` | `announcements.html` |
| Rule thumbs-up (with undo) | `AnnouncementResponse` (answer = ack) | `AnnouncementAdmin` | `respond` | `announcements.html` |
| Payments page + "I paid" | `PaymentRequest`, `Payment` | `PaymentRequestAdmin`, `PaymentAdmin` | `payments`, `pay`, `PaymentForm` | `payments.html` |
| Chat groups | `ChatGroup`, `GroupMember` | `ChatGroupAdmin` | `group_new/chat/api/add/leave` | `messages.html` |
| Photos and voice notes | `Message.image/audio` | (not in admin, by design) | `MessageForm`, `post_message` | `messages.html` |
| Blue theme + W logo | n/a | n/a | n/a | `base.html` (`:root` colours, `#wb-logo` symbol) |

Colours are CSS variables at the top of `base.html`. To change the blue, edit `--wb-accent`, `--wb-accent-dark` and `--wb-accent-soft` once and the whole site follows. The logo's blue is the `fill="#1877F2"` inside the `#wb-logo` symbol (and in the tab icon on the `<link rel="icon">` line).
