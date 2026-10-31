"""People who are logged in but not approved into any network yet only get the Networks / profile pages."""
from django.shortcuts import redirect

from .environments import my_envs

OPEN_PREFIXES = ("/admin/", "/static/", "/media/", "/logout/", "/login/", "/join/", "/networks/", "/me/",
                 "/presence/", "/live/", "/service-worker.js", "/push/", "/notifications/")


class NetworkGateMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and not request.path.startswith(OPEN_PREFIXES) and not my_envs(user):
            return redirect("networks")
        return self.get_response(request)
