"""
Groq-powered conversation handler (free & fast)
"""
import os
from groq import Groq
from database import get_conversation

client = Groq(api_key=os.getenv("GROQ_API_KEY"))

SYSTEM_PROMPT = """You are a friendly WhatsApp sales assistant for a web design agency in Ghana called {agency_name}.

Rules:
- Keep ALL messages under 3 sentences. Short like a real human texting.
- Be warm and casual, not formal or robotic.
- First, ask if they'd be interested in a website before pitching anything.
- Only mention pricing (GHS 500 setup + GHS 700/month) if they ask.
- If they seem interested, tell them someone will follow up soon.
- Never send long paragraphs. One or two short sentences max.

When they clearly want a website or ask about price or say yes — end your reply with: [HOT_LEAD]
When they say no or not interested — end with: [NOT_INTERESTED]
"""

AGENCY_NAME = os.getenv("AGENCY_NAME", "WebGh Agency")


def get_ai_reply(phone: str, new_message: str) -> tuple[str, bool, bool]:
    history = get_conversation(phone)

    messages = [{"role": "system", "content": SYSTEM_PROMPT.format(agency_name=AGENCY_NAME)}]

    for msg in history[-10:]:
        messages.append({"role": msg["role"], "content": msg["content"]})

    messages.append({"role": "user", "content": new_message})

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=messages,
        max_tokens=150,
        temperature=0.7,
    )

    reply = response.choices[0].message.content.strip()

    is_hot_lead = "[HOT_LEAD]" in reply
    is_not_interested = "[NOT_INTERESTED]" in reply

    clean_reply = reply.replace("[HOT_LEAD]", "").replace("[NOT_INTERESTED]", "").strip()

    return clean_reply, is_hot_lead, is_not_interested
