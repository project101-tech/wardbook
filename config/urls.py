from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from core import game_views, social, live, views

urlpatterns = [
    path("admin/", admin.site.urls),
    # auth
    path("login/", auth_views.LoginView.as_view(
        template_name="auth.html", authentication_form=views.WardLoginForm,
        redirect_authenticated_user=True), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("join/", views.register, name="register"),
    # app
    path("", views.feed, name="feed"),
    path("post/<int:pk>/like/", views.like, name="like"),
    path("post/<int:pk>/comment/", views.comment, name="comment"),
    path("post/<int:pk>/delete/", views.delete_post, name="delete_post"),
    path("notices/", views.announcements, name="announcements"),
    path("notices/<int:pk>/respond/", views.respond, name="respond"),
    path("notices/<int:pk>/poke/", social.poke, name="poke"),
    path("notices/<int:pk>/poke/people/", social.poke_candidates, name="poke_people"),
    path("mentions/search/", social.mention_search, name="mention_search"),
    path("mentions/names/", social.mention_names, name="mention_names"),
    path("payments/", views.payments, name="payments"),
    path("payments/<int:pk>/paid/", views.pay, name="pay"),
    path("neighbours/", views.directory, name="directory"),
    path("me/", views.profile, name="profile"),
    path("neighbours/<int:user_id>/", views.public_profile, name="public_profile"),
    path("notifications/", views.notifications, name="notifications"),
    path("notifications/<int:pk>/read/", views.notification_read, name="notification_read"),
    path("notifications/read-all/", views.notifications_read_all, name="notifications_read_all"),
    path("live/", live.stream, name="live"),
    path("feed/live/", views.feed_live, name="feed_live"),
    path("communities/", views.communities, name="communities"),
    path("communities/new/", views.community_new, name="community_new"),
    path("communities/<int:pk>/join/", views.community_join, name="community_join"),
    path("communities/<int:pk>/", views.community_detail, name="community_detail"),
    path("communities/<int:pk>/post/", views.community_post, name="community_post"),
    path("communities/<int:pk>/event/", views.community_event, name="community_event"),
    path("games/", game_views.games, name="games"),
    path("games/wallet/refill/", game_views.wallet_refill, name="wallet_refill"),
    path("games/challenge/", game_views.game_challenge, name="game_challenge"),
    path("games/<int:pk>/start/", game_views.game_start, name="game_start"),
    path("games/<int:pk>/cancel/", game_views.game_cancel, name="game_cancel"),
    path("games/<int:pk>/update/", game_views.game_update, name="game_update"),
    path("games/session/<int:pk>/", game_views.game_play, name="game_play"),
    path("games/session/<int:pk>/state/", game_views.game_state, name="game_state"),
    path("games/session/<int:pk>/move/", game_views.game_move, name="game_move"),
    path("games/session/<int:pk>/chat/", game_views.game_chat, name="game_chat"),
    path("games/session/<int:pk>/rematch/", game_views.game_rematch, name="game_rematch"),
    path("presence/", views.presence, name="presence"),
    path("push/subscribe/", views.push_subscribe, name="push_subscribe"),
    path("push/unsubscribe/", views.push_unsubscribe, name="push_unsubscribe"),
    path("push/test/", views.push_test, name="push_test"),
    path("service-worker.js", views.service_worker, name="service_worker"),
    path("messages/", views.inbox, name="inbox"),
    path("messages/threads/", views.threads_partial, name="threads_partial"),
    path("messages/<int:user_id>/", views.conversation, name="conversation"),
    path("messages/<int:user_id>/api/", views.conversation_api, name="conversation_api"),
    path("groups/new/", views.group_new, name="group_new"),
    path("groups/<int:pk>/", views.group_chat, name="group"),
    path("groups/<int:pk>/api/", views.group_api, name="group_api"),
    path("groups/<int:pk>/add/", views.group_add, name="group_add"),
    path("groups/<int:pk>/leave/", views.group_leave, name="group_leave"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
