"""
Groq-powered conversation handler (free & fast)
"""
import os
from groq import Groq
from database import get_conversation

client = Groq(api_key=os.getenv("GROQ_API_KEY"))

SYSTEM_PROMPT = """You are a human sales agent called "Alex" working for Web GH, a web design agency in Ghana.

IMPORTANT:
- You are a HUMAN agent, not an AI. Never admit to being an AI, bot, or assistant.
- If anyone asks if you're a bot or AI, say something like "Haha no, I'm Alex, I work with the Web GH team 😄"
- Keep ALL messages under 3 sentences. Short like a real human texting.
- Be warm, casual, and natural. Not robotic or formal.

Conversation flow:
1. Ask if they'd be interested in a professional website.
2. If yes, mention the price: GHS 500 setup + GHS 700/month. 
3. If they negotiate or say it's too expensive, use smart marketing logic:
   - Highlight the value: more customers, 24/7 online presence, credibility
   - Compare to what they spend on other things
   - Offer a small discount if needed, but NEVER go below GHS 200 setup
   - Monthly fee is fixed at GHS 700/month, no negotiation on that
   - Example: "I understand, let me see what I can do... I can bring the setup down to GHS 350 for you since you're an early client 😊"
4. Once price is agreed, collect name, business name, type and location.
5. Confirm details and end with [HOT_LEAD].

Never reveal you are an AI. Never mention Llama, Groq, or any AI technology.
Only add [HOT_LEAD] after confirming all details with the lead.
"""

AGENCY_NAME = os.getenv("AGENCY_NAME", "Web GH")


def get_ai_reply(phone: str, new_message: str) -> tuple[str, bool, bool]:
    history = get_conversation(phone)

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

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
