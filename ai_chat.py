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
- Only mention pricing (GHS 500 setup + GHS 700/month) if they ask.
- If they said no before but come back, engage them warmly again.

Conversation flow:
1. First ask if they'd be interested in a website.
2. If yes, ask for their name and business name.
3. Then ask what kind of business they run and their location.
4. Once you have those details, confirm everything back to them (e.g. "Great! So you're [name], running [business] in [location] — I'll have someone reach out to you shortly 😊").
5. Only after confirming, mark as [HOT_LEAD].

When you have confirmed all details and they're happy — end your reply with: [HOT_LEAD]
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
