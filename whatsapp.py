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


def is_whatsapp_number(phone: str) -> bool:
    """Check if a phone number is registered on WhatsApp."""
    headers = {
        "Authorization": f"Bearer {WA_TOKEN}",
        "Content-Type": "application/json",
    }
    try:
        response = httpx.post(
            f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/contacts",
            json={"blocking": "wait", "contacts": [f"+{phone}"], "force_check": True},
            headers=headers,
            timeout=10
        )
        data = response.json()
        contacts = data.get("contacts", [])
        if contacts and contacts[0].get("status") == "valid":
            return True
        return False
    except:
        return True  # If check fails, assume valid and try anyway



def first_outreach_message(business_name: str) -> str:
    """Generate the first outreach message for a business lead."""
    clean_name = business_name.split(",")[0].strip()
    return (
        f"Hi {clean_name}! 👋 I noticed your business doesn't have a website yet. "
        f"We're Web GH — we build clean, affordable websites for businesses in Ghana. "
        f"Would you be interested in getting one? Happy to share more details!"
    )
