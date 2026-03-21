# Web Agency Bot 🚀

Automated lead generation & outreach bot for a web design agency in Ghana.

## How it works
1. **Scraper** pulls businesses from Google Maps
2. **Outreach** sends a first WhatsApp message to each lead
3. **AI Chat** (OpenAI) replies to responses automatically
4. **Telegram** notifies you when a lead is hot
5. You close the deal and collect payment via MoMo

## Setup

### 1. Clone & install
```bash
git clone https://github.com/YOUR_USERNAME/web-agency-bot
cd web-agency-bot
pip install -r requirements.txt
playwright install chromium
```

### 2. Configure environment
```bash
cp .env.example .env
# Fill in your API keys in .env
```

### 3. Get your API keys
- **WhatsApp**: Meta Developer Portal → https://developers.facebook.com
- **OpenAI**: https://platform.openai.com/api-keys
- **Telegram Bot**: Message @BotFather on Telegram

### 4. Run locally
```bash
# Scrape leads
python scraper.py --query "restaurants in Accra Ghana" --limit 30

# Send outreach
python outreach.py

# Start webhook server
uvicorn app:app --reload
```

## Deploy to Render
1. Push this repo to GitHub
2. Go to https://render.com → New Web Service → Connect your repo
3. Add all environment variables from `.env.example`
4. Deploy — Render uses `render.yaml` automatically

## WhatsApp Webhook Setup (after deploying to Render)
1. Go to Meta Developer Portal → Your App → WhatsApp → Configuration
2. Set Webhook URL: `https://your-app.onrender.com/webhook`
3. Set Verify Token: same value as `WEBHOOK_VERIFY_TOKEN` in your env
4. Subscribe to `messages` field

## Workflow
```
scraper.py  →  leads.db  →  outreach.py  →  WhatsApp
                                                ↓
                                         app.py (webhook)
                                                ↓
                                          ai_chat.py
                                                ↓
                                     [HOT_LEAD] → Telegram alert
```
