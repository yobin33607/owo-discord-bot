"""Small helpers for sending Discord direct messages."""


async def send_user_dm(client, user_id, content):
    """Resolve a Discord user through the client's cache/API and send a DM."""
    try:
        user_id = int(user_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid Discord user ID") from exc

    user = client.get_user(user_id)
    if user is None:
        user = await client.fetch_user(user_id)
    if user is None:
        raise ValueError("User not found")

    await user.send(content)
    return user
