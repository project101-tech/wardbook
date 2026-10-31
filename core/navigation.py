"""The five main tabs (bottom bar on phones, top bar on computers) and the little tab strip inside two of them.

Everything about "which page belongs to which tab" lives here, so base.html stays simple.
To add a page to a tab, add its URL name to that tab's `pages`.
"""

# Tabs 4 and 5 are made of sub-tabs. Each sub-tab lists every URL name that should light it up.
SUBTABS = {
    "connect": [
        {"label": "Messages", "url": "inbox", "icon": "chat-dots", "badge": "dms", "pages": ("inbox", "conversation", "group")},
        {"label": "Communities", "url": "communities", "icon": "people", "pages": ("communities", "community_detail")},
        {"label": "Neighbours", "url": "directory", "icon": "person-lines-fill", "pages": ("directory", "public_profile")},
    ],
    "hangout": [
        {"label": "Games", "url": "games", "icon": "controller", "pages": ("games", "game_play")},
        {"label": "Music Taste", "url": "music", "icon": "music-note-beamed", "pages": ("music", "music_profile", "music_manage")},
    ],
}

HUBS = [
    {"key": "square", "label": "Town Square", "icon": "house-heart", "on": "house-heart-fill", "url": "feed", "pages": ("feed",)},
    {"key": "notices", "label": "Notices", "icon": "megaphone", "on": "megaphone-fill", "url": "announcements", "pages": ("announcements",)},
    {"key": "pay", "label": "Payments", "short": "Pay", "icon": "wallet2", "on": "wallet-fill", "url": "payments", "pages": ("payments",)},
    {"key": "connect", "label": "Connect", "icon": "chat-heart", "on": "chat-heart-fill", "url": "inbox", "badge": "dms"},
    {"key": "hangout", "label": "Hangout", "icon": "controller", "on": "controller", "url": "games"},
]


def nav_context(url_name):
    """Template variables: hubs (with `active`), hub (key of the open tab), subtabs (with `active`) for tabs 4 and 5."""
    url_name = url_name or ""
    current = ""
    for hub in HUBS:
        pages = hub.get("pages") or tuple(p for s in SUBTABS.get(hub["key"], ()) for p in s["pages"])
        if url_name in pages:
            current = hub["key"]
    hubs = [dict(h, active=h["key"] == current, short=h.get("short", h["label"])) for h in HUBS]
    subtabs = [dict(s, active=url_name in s["pages"]) for s in SUBTABS.get(current, ())]
    return {"hubs": hubs, "hub": current, "subtabs": subtabs}
