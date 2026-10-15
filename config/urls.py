from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from core import views

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
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
