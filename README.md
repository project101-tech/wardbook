# Wardbook

Private community hub for a residential society. Django + Bootstrap 5, no JS framework.
Verified-resident sign-up, Notices, Town Square, private messaging, communities, games, notifications, and browser push.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser     # this is YOUR admin login
python manage.py runserver
```

Site: http://127.0.0.1:8000/  ·  Control room: http://127.0.0.1:8000/admin/

Optional: `export SOCIETY_NAME="Your Society Name"` to change the name in the header.

For browser push notifications in production, generate a VAPID key pair and set `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, and `VAPID_CLAIMS_EMAIL` in the deployment environment. Render has placeholders for these variables. Residents then enable push alerts from Notifications; delivery covers DMs, mentions, games, community activity, and Town Square notifications. Without these variables, the in-app notification centre still works but closed-tab phone/desktop push cannot be delivered.

## Day-to-day admin workflow

| Task | Where in /admin/ |
|------|------------------|
| Approve new residents | **Users**. Pending people are listed first. Tick **Active** in the list (then *Save*), or select rows and use the *Approve selected residents* action. Check the house number against your records first. |
| Post a notice / rule | **Announcements → Add**. Tick *Pinned* to keep it on top. The newest notice also shows as a banner on the Town Square. |
| Hide an inappropriate post or comment | **Posts / Comments**. Tick *Is hidden* in the list and save. Nothing is deleted. |
| Reset a forgotten password | **Users →** open the person → "this form" link next to the password field. |
| Remove someone | Untick **Active** (keeps their history) |
| Announce an **event** | **Announcements → Add**, category *Event*, set the date/time (required) and venue. Residents get "I'll come / Can't make it" buttons until it starts. Tapping the same button again undoes the RSVP. Attendee counts show in the list; open the event to see names. |
| Post a **rule** | **Announcements → Add**, category *Rule*. Residents get an "I've read this" thumbs-up (tap again to undo). The list shows how many acknowledged. |
| Ask for a **payment** | **Payment requests → Add**: title, amount (leave empty for voluntary), UPI ID, optional QR image, due date. Optionally link it to a notice/event so the notice shows a "Pay" button. Untick *Is open* to close it. |
| **Verify payments** | **Payments**. Pending ones are listed first. Match each *UPI transaction ID* against your bank statement, then tick **Approved** in the list (or use the bulk action). To reject, set *Rejected* and write a note; the resident sees it. |
| Look at **chat groups** | **Chat groups** shows names and members only. Messages are never visible in the admin. |

Residents log in with their **mobile number** (it is the username) and the password they chose.
Direct messages are intentionally *not* in the admin, so private chats can't be browsed from there.

## Deploy on Render

1. Create a free Cloudinary account and copy the **API environment variable** (`cloudinary://KEY:SECRET@CLOUDNAME`) from the dashboard.
2. Push this folder to a GitHub repository.
3. Render → **New → Blueprint** → select the repo. `render.yaml` creates the web service and a PostgreSQL database.
4. When asked, paste `CLOUDINARY_URL`. Edit `SOCIETY_NAME` in `render.yaml` first.
5. After the first deploy, open the service **Shell** tab and run `python manage.py createsuperuser`.

`CLOUDINARY_URL` is required in production. Without it, the app stops during startup instead of silently producing broken images, because Render's local disk is not persistent across deploys. Existing images that were uploaded to an old Render disk must be uploaded to Cloudinary separately; they cannot be recovered from Git because `media/` is intentionally ignored.

## Files

```
config/settings.py   all settings, driven by environment variables
config/urls.py       every URL in one place
core/models.py       User, ResidentProfile, Announcement (+events/rules), AnnouncementResponse,
                     PaymentRequest, Payment, Post, Comment, ChatGroup, GroupMember, Message,
                     Notification, Community, CommunityPost, CommunityEvent, GameChallenge, GameSession
                     GameChatMessage
core/admin.py        approvals, notices + RSVP/ack counts, payment verification, moderation, groups
core/views.py        forms + views + the small context processor
templates/           base, auth, feed, announcements, payments, directory, messages, profile
render.yaml          Render blueprint (web service + Postgres)
```

## Notes

- Voice notes use the browser microphone, which only works on `https://` (Render provides this) or `localhost`/`127.0.0.1`.
- Photos and voice notes are capped at 5 MB each (`MAX_UPLOAD_MB` in settings).
- Chat updates by polling every 3 seconds. No Firebase is needed.

## Next

Accepted invitations now open live 1v1 sessions with server-validated turns, polling, presence heartbeats, online profile tags, private in-game chat, rematch and exit controls. Ludo and Snakes and Ladders use boards, and Chess validates pawn movement and sliding-piece blockers. A WebSocket transport can replace polling later for lower latency.
