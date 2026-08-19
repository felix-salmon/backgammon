"""
Email in/out.

Inbound: ImprovMX's webhook feature. Set an alias's forward destination
to your webhook URL (e.g. bg@felixsalmon.com -> https://yourhost/inbound)
in the ImprovMX dashboard, and it POSTs JSON for each incoming email --
no plan restriction on this, works on the free tier.
See: https://improvmx.com/guides/webhooks

Outbound: plain SMTP via smtplib. This is deliberately NOT tied to any
one provider -- point it at ImprovMX's SMTP (smtp.improvmx.com:587,
requires their Premium plan) or at any other mailbox you can get SMTP
credentials for (Gmail app password, Fastmail, etc). See README.md.

Required environment variables:
    SMTP_HOST   -- e.g. "smtp.improvmx.com"
    SMTP_PORT   -- e.g. 587
    SMTP_USER   -- the full address you authenticate as, e.g. "bg@felixsalmon.com"
    SMTP_PASS   -- its password / app password
    SMTP_FROM   -- (optional) the From: header to send with; defaults to SMTP_USER
"""

import os
import re
import smtplib
from email.message import EmailMessage

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.improvmx.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASS = os.environ.get("SMTP_PASS")
SMTP_FROM = os.environ.get("SMTP_FROM", SMTP_USER)

SUBJECT_PREFIX_RE = re.compile(r"^(re|fwd|fw)\s*:\s*", re.IGNORECASE)


def parse_inbound_improvmx(payload):
    """payload: the parsed JSON body ImprovMX POSTs to your webhook.
    Returns dict with sender, subject (cleaned), body (plain text).
    """
    sender = ((payload.get("from") or {}).get("email") or "").strip().lower()
    subject = payload.get("subject", "") or ""
    subject = SUBJECT_PREFIX_RE.sub("", subject).strip()
    body = payload.get("text", "") or ""
    return {"sender": sender, "subject": subject, "body": body.strip()}


def _require_smtp_config():
    if not (SMTP_HOST and SMTP_USER and SMTP_PASS):
        raise RuntimeError(
            "SMTP_HOST / SMTP_USER / SMTP_PASS are not set. See README.md for setup."
        )


def _send(msg):
    _require_smtp_config()
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.send_message(msg)


def send_board_email(to_addrs, subject, message_text, image_path, extra_note=None):
    """Send the rendered board PNG to one or more recipients, with the
    player's message text underneath -- same shape as the old PBM emails.
    to_addrs: list of email addresses.
    """
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"Backgammon <{SMTP_FROM}>"
    msg["To"] = ", ".join(to_addrs)

    text_body = (message_text or "") + (f"\n\n{extra_note}" if extra_note else "")
    msg.set_content(text_body + "\n\n[board image attached]")

    html = f"""\
    <div style="font-family: sans-serif;">
      <img src="cid:board" style="max-width: 100%; border: 1px solid #ccc;" />
      {"<p>" + _escape(message_text) + "</p>" if message_text else ""}
      {"<p style='color:#888'>" + _escape(extra_note) + "</p>" if extra_note else ""}
    </div>
    """
    msg.add_alternative(html, subtype="html")

    with open(image_path, "rb") as f:
        img_data = f.read()
    # attach inline image to the html alternative part
    html_part = msg.get_payload()[-1]
    html_part.add_related(img_data, maintype="image", subtype="png", cid="<board>")

    _send(msg)


def send_text_email(to_addr, subject, body):
    """Plain text-only email, e.g. for error replies ('that move isn't legal')."""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"Backgammon <{SMTP_FROM}>"
    msg["To"] = to_addr
    msg.set_content(body)
    _send(msg)


def _escape(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
