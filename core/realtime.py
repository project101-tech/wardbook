
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer


def send_user_event(user_id, payload):
    """Deliver an event to all active sockets for one user."""
    channel_layer = get_channel_layer()

    async_to_sync(channel_layer.group_send)(
        f"wardbook_user_{user_id}",
        {
            "type": "wardbook.event",
            "payload": payload,
        },
    )
