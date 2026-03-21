"""
WhatsApp Cloud API — send messages & parse incoming webhooks
"""
import os
import httpx

WA_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
BASE_URL = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"


def send_message(to: str, text: str) -> dict:
    """Send a WhatsApp text message."""
    headers = {
        "Authorization": f"Bearer {WA_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text},
    }
    response = httpx.post(BASE_URL, json=payload, headers=headers)
    response.raise_for_status()
    return response.json()


def parse_incoming(data: dict) -> dict | None:
    """
    Parse a WhatsApp webhook payload.
    Returns: { phone, message } or None if not a text message.
    """
    try:
        entry = data["entry"][0]
        changes = entry["changes"][0]
        value = changes["value"]
        msg = value["messages"][0]

        if msg.get("type") != "text":
            return None

        phone = msg["from"]
        text = msg["text"]["body"]
        return {"phone": phone, "message": text}
    except (KeyError, IndexError):
        return None


FIRST_MESSAGE_TEMPLATE = """Hello {name}! 👋

I noticed {business_name} doesn't have a website yet. In today's market, a professional website helps you get more customers online.

We build clean, fast websites for businesses like yours — fully set up in 48 hours.

Would you be interested in learning more? 😊"""


def first_outreach_message(business_name: str) -> str:
    return FIRST_MESSAGE_TEMPLATE.format(
        name=business_name,
        business_name=business_name
    )
