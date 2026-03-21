"""
Outreach runner — sends the first WhatsApp message to all new leads.
Run this manually or schedule it via Render cron job.
"""
import time
from database import init_db, get_new_leads, update_lead_status, append_message
from whatsapp import send_message, first_outreach_message
from telegram_notify import notify_scrape_done


def run_outreach(delay_seconds: int = 10):
    init_db()
    leads = get_new_leads()

    if not leads:
        print("[!] No new leads to contact.")
        return

    print(f"[+] Sending outreach to {len(leads)} leads...")

    success = 0
    for lead in leads:
        phone = lead["phone"]
        name = lead["name"]

        try:
            message = first_outreach_message(name)
            send_message(phone, message)
            append_message(phone, "assistant", message)
            update_lead_status(phone, "contacted")
            print(f"  [✓] Sent to {name} ({phone})")
            success += 1
        except Exception as e:
            print(f"  [!] Failed for {name} ({phone}): {e}")

        time.sleep(delay_seconds)  # Avoid rate limits

    print(f"\n[✓] Outreach done: {success}/{len(leads)} sent")


if __name__ == "__main__":
    run_outreach()
