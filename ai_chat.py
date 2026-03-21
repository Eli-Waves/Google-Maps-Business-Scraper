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
- Never send long paragraphs. One or two short sentences max.
- If they said no before but come back, engage them warmly again.

Conversation flow:
1. Ask if they'd be interested in a professional website.
2. If yes, mention the price upfront: GHS 500 setup + GHS 700/month. Ask if that works for them.
3. If they're okay with price, ask for their name and business name.
4. Then ask their business type and location.
5. Confirm everything: "Perfect! So you're [name], running [business type] called [business name] in [location]. I'll have someone reach out to you shortly 😊"
6. Only after confirming details, add [HOT_LEAD] at the end.

Only add [HOT_LEAD] once you have confirmed name, business, and location with the lead.
"""

AGENCY_NAME = os.getenv("AGENCY_NAME", "WebGh Agency")


def get_ai_reply(phone: str, new_message: str) -> tuple[str, bool, bool]:
    history = get_conversation(phone)

    messages = [{"role": "system", "content": SYSTEM_PROMPT.format(agency_name=AGENCY_NAME)}]

    for msg in history[-20:]:
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
    clean_reply = reply.replace("[HOT_LEAD]", "").strip()

    return clean_reply, is_hot_lead, False
