"""
WhatsApp Cloud API — send messages, typing indicator & parse webhooks
"""
import os
import time
import httpx

WA_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
BASE_URL = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"


def normalize_phone(phone: str) -> str:
    """Normalize phone number to WhatsApp format: 233XXXXXXXXX (no +, no spaces)"""
    if not phone:
        return phone
    # Strip everything except digits
    digits = ''.join(filter(str.isdigit, phone))
    # If starts with 0, replace with Ghana code
    if digits.startswith('0'):
        digits = '233' + digits[1:]
    # If starts with 233 already, good
    # If it's a 9-digit number with no country code, prepend 233
    if len(digits) == 9:
        digits = '233' + digits
    return digits


def send_message(to: str, text: str, typing_delay: bool = True) -> dict:
    """Send a WhatsApp text message with natural typing delay."""
    to = normalize_phone(to)
    headers = {
        "Authorization": f"Bearer {WA_TOKEN}",
        "Content-Type": "application/json",
    }
    if typing_delay:
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
    phone = normalize_phone(phone)
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
        return True


def first_outreach_message(business_name: str) -> str:
    """Generate the first outreach message for a business lead."""
    clean_name = business_name.split(",")[0].strip()
    return (
        f"Hi {clean_name}! 👋 I noticed your business doesn't have a website yet. "
        f"We're Web GH — we build clean, affordable websites for businesses in Ghana. "
        f"Would you be interested in getting one? Happy to share more details!"
    )
