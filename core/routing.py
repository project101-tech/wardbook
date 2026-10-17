
from django.urls import path
from .consumers import WardbookConsumer

websocket_urlpatterns = [
    path("ws/live/", WardbookConsumer.as_asgi(), name="ws_live"),
]
