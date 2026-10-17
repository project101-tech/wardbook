
import json

from channels.generic.websocket import AsyncWebsocketConsumer


class WardbookConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        user = self.scope["user"]

        if not user.is_authenticated or not user.is_active:
            await self.close(code=4401)
            return

        self.user_group = f"wardbook_user_{user.pk}"

        await self.channel_layer.group_add(
            self.user_group,
            self.channel_name,
        )
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, "user_group"):
            await self.channel_layer.group_discard(
                self.user_group,
                self.channel_name,
            )

    async def wardbook_event(self, event):
        await self.send(text_data=json.dumps(event["payload"]))
