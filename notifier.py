"""
notifier.py
Sends the "New Storm Report" alert via SMS and/or WhatsApp using Twilio.

Setup required (see README.md for full walkthrough):
  1. Create a free Twilio account: https://www.twilio.com/try-twilio
  2. Get a Twilio phone number capable of SMS.
  3. Set these environment variables wherever you deploy this:
       TWILIO_ACCOUNT_SID
       TWILIO_AUTH_TOKEN
       TWILIO_SMS_FROM        e.g. +18165551234  (your Twilio number)
       TWILIO_WHATSAPP_FROM   e.g. whatsapp:+14155238886  (optional)
       ALERT_TO_PHONE         +19137872654
       DASHBOARD_URL          https://your-deployed-dashboard-url

WhatsApp note: Twilio's WhatsApp sandbox works instantly for testing,
but sending WhatsApp messages in production (to a number that hasn't
messaged you first) requires an approved message template from Meta.
SMS has no such approval step and will work immediately - start there,
add WhatsApp once the template is approved if you want it too.
"""

import os
from twilio.rest import Client

ALERT_TITLE = "New Storm Report"


def _message_body(dashboard_url: str) -> str:
    return (
        f"{ALERT_TITLE}\n\n"
        f"Hey Iconic Team, you have a new storm report available. "
        f"Access the dashboard to learn more about it and start selling!\n"
        f"{dashboard_url}"
    )


def send_storm_alert(dashboard_url: str = None):
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")

    if not account_sid or not auth_token:
        print("[notifier] Twilio not configured yet - skipping alert send "
              "(dashboard still updates normally).")
        return []

    to_number = os.environ.get("ALERT_TO_PHONE", "+19137872654")
    dashboard_url = dashboard_url or os.environ.get("DASHBOARD_URL", "")

    client = Client(account_sid, auth_token)
    body = _message_body(dashboard_url)

    sent = []

    sms_from = os.environ.get("TWILIO_SMS_FROM")
    if sms_from:
        msg = client.messages.create(body=body, from_=sms_from, to=to_number)
        sent.append(("sms", msg.sid))

    wa_from = os.environ.get("TWILIO_WHATSAPP_FROM")
    if wa_from:
        msg = client.messages.create(
            body=body, from_=wa_from, to=f"whatsapp:{to_number}"
        )
        sent.append(("whatsapp", msg.sid))

    if not sent:
        print("[notifier] No TWILIO_SMS_FROM or TWILIO_WHATSAPP_FROM configured - "
              "alert was not sent anywhere.")

    return sent
