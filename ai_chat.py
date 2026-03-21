"""
Groq-powered conversation handler (free & fast)
"""
import os
from groq import Groq
from database import get_conversation

client = Groq(api_key=os.getenv("GROQ_API_KEY"))

SYSTEM_PROMPT = """You are a friendly sales assistant for a web design agency in Ghana called {agency_name}.

Your job:
1. Respond naturally to business owners on WhatsApp
2. Explain the value of having a website (more customers, credibility, 24/7 visibility)
3. Answer questions about pricing: GHS 500 setup + GHS 700/month maintenance
4. If they show interest, tell them you'll have a human follow up shortly
5. If they're not interested, politely thank them and end the conversation

Keep messages short (2-4 sentences max) — this is WhatsApp, not email.
Be friendly, professional, and speak naturally. Don't be pushy.

When a lead says yes/interested/wants to know more/asks about price — that's a HOT LEAD.
End your reply with exactly: [HOT_LEAD] on a new line so the system can detect it.

When they say no/not interested/stop — end with: [NOT_INTERESTED]
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
        max_tokens=300,
        temperature=0.7,
    )

    reply = response.choices[0].message.content.strip()

    is_hot_lead = "[HOT_LEAD]" in reply
    is_not_interested = "[NOT_INTERESTED]" in reply

    clean_reply = reply.replace("[HOT_LEAD]", "").replace("[NOT_INTERESTED]", "").strip()

    return clean_reply, is_hot_lead, is_not_interested
