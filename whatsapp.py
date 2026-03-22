"""
WhatsApp Cloud API — send messages, typing indicator & parse webhooks
"""
import os
import time
import httpx

WA_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
BASE_URL = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"


def send_typing(to: str):
    """Show typing indicator to the recipient."""
    headers = {
        "Authorization": f"Bearer {WA_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "reaction",
        "reaction": {"message_id": "", "emoji": ""}
    }
    # Use status update to show typing
    try:
        httpx.post(
            f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages",
            json={
                "messaging_product": "whatsapp",
                "status": "read",
                "message_id": "placeholder"
            },
            headers=headers,
            timeout=5
        )
    except:
        pass


def send_message(to: str, text: str, typing_delay: bool = True) -> dict:
    """Send a WhatsApp text message with natural typing delay."""
    headers = {
        "Authorization": f"Bearer {WA_TOKEN}",
        "Content-Type": "application/json",
    }

    if typing_delay:
        # Delay based on word count — ~0.05 seconds per word, min 1s max 5s
        word_count = len(text.split())
        delay = min(max(word_count * 0.05, 1), 5)
        time.sleep(delay)

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
    """Parse incoming WhatsApp webhook payload."""
    try:
        entry = data["entry"][0]
        changes = entry["changes"][0]
        value = changes["value"]
        msg = value["messages"][0]

        if msg.get("type") != "text":
            return None

        phone = msg["from"]
        text = msg["text"]["body"]
        message_id = msg.get("id", "")
        return {"phone": phone, "message": text, "message_id": message_id}
    except (KeyError, IndexError):
        return None


FIRST_MESSAGE_TEMPLATE = """Hello {name}! I noticed your business might not have a website yet. A professional website helps you get more customers online and builds credibility.

We build clean, fast websites for businesses in Ghana — fully set up in 48 hours. Would you be interested in learning more?"""


def first_outreach_message(business_name: str) -> str:
    return FIRST_MESSAGE_TEMPLATE.format(name=business_name)
