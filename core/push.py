"""Web Push: sends a notification to a resident's phones/browsers even when Wardbook is closed.

Delivery runs on a small background worker, so the request that triggered it (sending a message,
posting a comment, ...) never waits for Google's / Mozilla's push servers. Nothing in here raises:
a push problem must never break the action that caused it.
"""
import base64
import json
import logging
import os
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

from django.conf import settings
from django.db import connection
from django.templatetags.static import static
from django.utils import timezone

log = logging.getLogger(__name__)

TTL = 60 * 60 * 24  # if the phone is off, keep trying for a day


def enabled():
    return bool(settings.VAPID_PUBLIC_KEY and settings.VAPID_PRIVATE_KEY)


def subject():
    """The VAPID 'sub' claim. Push services want mailto: or https:; add mailto: if someone forgot it."""
    s = (getattr(settings, "VAPID_CLAIMS_EMAIL", "") or "").strip() or "mailto:admin@example.com"
    return s if s.startswith(("mailto:", "https://")) else "mailto:" + s


def _b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


@lru_cache(maxsize=2)
def _vapid(private):
    from py_vapid import Vapid

    private = private.strip().strip('"').strip("'")
    if "BEGIN" in private:  # a PEM pasted into an env var, with \n written out
        return Vapid.from_pem(private.replace("\\n", "\n").encode())
    return Vapid.from_string(private)


@lru_cache(maxsize=2)
def _check(public, private):
    try:
        from cryptography.hazmat.primitives import serialization

        v = _vapid(private)
        derived = _b64url(v.private_key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
    except Exception as e:
        return False, f"VAPID_PRIVATE_KEY could not be read ({type(e).__name__}). Generate a fresh pair and set both keys again."
    if derived != public.strip().strip('"').strip("'").rstrip("="):
        return False, ("VAPID_PUBLIC_KEY does not belong to VAPID_PRIVATE_KEY. Set both from the same generated pair, "
                       "redeploy, then turn notifications off and on again on each device.")
    return True, ""


def check_keys():
    """(ok, message): does the public key the browser subscribes with belong to the private key we sign with?"""
    if not enabled():
        return False, "VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY are not set on the server."
    return _check(settings.VAPID_PUBLIC_KEY, settings.VAPID_PRIVATE_KEY)


def _static(name):
    try:
        return static(name)
    except ValueError:  # staticfiles manifest not built yet: fall back to the plain path
        return f"/static/{name}"


def _clip(text, n):
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def make_payload(title, body="", url="/", tag="", collapse=False, kind=""):
    return json.dumps({
        "title": _clip(title, 80), "body": _clip(body, 160), "url": url or "/", "tag": tag or "",
        "collapse": bool(collapse), "kind": kind or "", "ts": int(time.time() * 1000),
        "icon": _static("icon-192.png"), "badge": _static("badge-96.png"),
    })


# ---- sending ---------------------------------------------------------------------------------

def _send_one(sub, payload):
    """-> (subscription id, HTTP status or None, detail). Never raises."""
    from pywebpush import WebPushException, webpush

    try:
        resp = webpush(
            subscription_info={"endpoint": sub["endpoint"], "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}},
            data=payload,
            vapid_private_key=_vapid(settings.VAPID_PRIVATE_KEY),
            vapid_claims={"sub": subject()},  # fresh dict every call: the library writes 'aud' and 'exp' into it
            ttl=TTL,
            headers={"Urgency": "high"},  # tells Android to deliver now, even in battery-saver (Doze)
            timeout=10,
        )
        return sub["id"], getattr(resp, "status_code", 201), ""
    except WebPushException as e:
        r = e.response
        return sub["id"], getattr(r, "status_code", None), (r.text[:200] if r is not None and r.text else str(e)[:200])
    except Exception as e:
        return sub["id"], None, f"{type(e).__name__}: {e}"[:200]


def _deliver(subs, payload):
    """Send to every subscription; forget the ones the push service says are gone. -> list of results."""
    from .models import PushSubscription

    if not subs:
        return []
    with ThreadPoolExecutor(max_workers=min(8, len(subs))) as pool:
        results = list(pool.map(lambda s: _send_one(s, payload), subs))
    owner = {s["id"]: s.get("user_id") for s in subs}
    dead = [sid for sid, status, _ in results if status in (404, 410)]
    for sid in dead:
        # match the owner too: if this phone was handed to another account while the push was in flight,
        # a late "gone" answer must not delete the new owner's fresh registration
        PushSubscription.objects.filter(id=sid, user_id=owner.get(sid)).delete()
    for sid, status, detail in results:
        if status is None or status >= 300:
            if status in (401, 403):
                log.error("push service rejected our VAPID keys (HTTP %s): %s", status, detail)
            elif status not in (404, 410):
                log.warning("push to subscription %s failed (HTTP %s): %s", sid, status, detail)
    return results


def _subs_for(user_ids):
    from .models import PushSubscription

    return list(PushSubscription.objects.filter(user_id__in=list(user_ids), user__is_active=True)
                .values("id", "user_id", "endpoint", "p256dh", "auth"))


_jobs = queue.Queue(maxsize=2000)
_started_pid = None
_start_lock = threading.Lock()


def _worker():
    while True:
        user_ids, payload = _jobs.get()
        try:
            _deliver(_subs_for(user_ids), payload)
        except Exception:
            log.exception("push worker crashed on a job")
        finally:
            connection.close()  # this thread's own DB connection; the request threads are untouched


def _ensure_workers():
    global _started_pid
    with _start_lock:
        if _started_pid == os.getpid():  # re-check after fork: threads don't survive it
            return
        _started_pid = os.getpid()
        for i in range(3):
            threading.Thread(target=_worker, daemon=True, name=f"wb-push-{i}").start()


def send(user_ids, title, body="", url="/", tag="", collapse=False, kind=""):
    """Queue a push for these users' devices and return immediately."""
    if not enabled():
        return
    try:
        user_ids = list(user_ids)
        if not user_ids:
            return
        payload = make_payload(title, body, url, tag, collapse, kind)
        if getattr(settings, "PUSH_ASYNC", True):
            _ensure_workers()
            _jobs.put_nowait((user_ids, payload))
        else:
            _deliver(_subs_for(user_ids), payload)
    except queue.Full:
        log.warning("push queue is full; dropped a notification")
    except Exception:
        log.exception("could not queue push")


HINTS = {
    400: "The push service rejected the request. Check VAPID_CLAIMS_EMAIL.",
    401: "The push service rejected the server's VAPID keys.",
    403: "The push service rejected the server's VAPID keys. They don't match the ones this device subscribed with.",
    404: "This device's subscription has expired and was removed. Turn notifications on again.",
    410: "This device's subscription has expired and was removed. Turn notifications on again.",
    413: "The notification was too large.",
    429: "The push service is rate-limiting us. Try again in a minute.",
}


def send_test(user):
    """Synchronous test push to all of this user's devices, with a plain-English result for each."""
    ok, msg = check_keys()
    if not ok:
        return {"devices": 0, "delivered": 0, "problems": [msg]}
    subs = _subs_for([user.pk])
    if not subs:
        return {"devices": 0, "delivered": 0,
                "problems": ["No device is registered for your account yet. Tap “Turn on notifications” on this phone first."]}
    # Plain wording on purpose: Chrome's on-device spam check dislikes emoji and generic, repeated text.
    when = timezone.localtime().strftime("%I:%M %p").lstrip("0")
    payload = make_payload("Wardbook notifications are on",
                           f"Test sent at {when}. You will get message and update alerts from your neighbours here.",
                           "/notifications/", f"test-{int(time.time())}")
    results = _deliver(subs, payload)
    delivered = sum(1 for _, status, _ in results if status and status < 300)
    problems = []
    for _, status, detail in results:
        if status and status < 300:
            continue
        problems.append(HINTS.get(status) or (f"Could not reach the push service ({detail or 'network error'})." if status is None
                                              else f"The push service answered HTTP {status}. {detail}"))
    return {"devices": len(subs), "delivered": delivered, "problems": sorted(set(problems))}
