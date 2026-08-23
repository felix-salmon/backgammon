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

# Markers that reliably indicate "everything from here on is quoted history
# from earlier in the thread", across Gmail, Apple Mail, and Outlook's
# conventions. ImprovMX doesn't offer a pre-stripped body field, so this is
# done by hand -- cut at whichever marker appears earliest in the text.
_QUOTE_MARKERS = [
    re.compile(r"^On .{0,140}wrote:\s*$", re.MULTILINE),      # Gmail / Apple Mail
    re.compile(r"^>", re.MULTILINE),                          # any quoted line
    re.compile(r"^-{2,}\s*Original Message\s*-{2,}", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^_{5,}\s*$", re.MULTILINE),                  # Outlook separator
]


def _strip_quoted_reply(text):
    """Keep only the part of an email body written above the quoted
    reply chain, if any -- otherwise a reply email ends up including
    every previous message in the thread as if it were new text."""
    if not text:
        return text
    cut_at = len(text)
    for pattern in _QUOTE_MARKERS:
        m = pattern.search(text)
        if m and m.start() < cut_at:
            cut_at = m.start()
    return text[:cut_at].strip()


def parse_inbound_improvmx(payload):
    """payload: the parsed JSON body ImprovMX POSTs to your webhook.
    Returns dict with sender, subject (cleaned), body (plain text, with
    any quoted reply history stripped off).
    """
    sender = ((payload.get("from") or {}).get("email") or "").strip().lower()
    subject = payload.get("subject", "") or ""
    subject = SUBJECT_PREFIX_RE.sub("", subject).strip()
    body = payload.get("text", "") or ""
    return {"sender": sender, "subject": subject, "body": _strip_quoted_reply(body.strip())}


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


def send_board_email(to_addrs, subject, image_path, summary_lines=None,
                      sender_name=None, message_text=None, footer_lines=None):
    """Send the rendered board PNG to one or more recipients, laid out as
    three visually distinct pieces:
        summary_lines -- what just happened (one line each), e.g.
                          "Felix played 24/18 13/11.", "Hit on: 18."
        message_text  -- the sender's own note, if any, clearly attributed
                          to sender_name and set apart from the summary
        footer_lines   -- secondary info (tally, board link), dimmed and
                          pushed to the bottom
    to_addrs: list of email addresses.
    """
    summary_lines = summary_lines or []
    footer_lines = footer_lines or []

    text_parts = []
    if summary_lines:
        text_parts.append("\n".join(summary_lines))
    if message_text:
        prefix = f"{sender_name}: " if sender_name else ""
        text_parts.append(f"{prefix}{message_text}")
    if footer_lines:
        text_parts.append("\n".join(footer_lines))
    text_parts.append("[board image attached]")
    text_body = "\n\n".join(text_parts)

    summary_html = "".join(f"<div>{_escape(line)}</div>" for line in summary_lines)

    message_html = ""
    if message_text:
        prefix = f"<strong>{_escape(sender_name)}:</strong> " if sender_name else ""
        message_html = (
            "<div style='margin-top:14px; padding-left:12px; "
            "border-left:3px solid #ccc; white-space:pre-wrap;'>"
            f"{prefix}{_escape(message_text)}</div>"
        )

    footer_html = ""
    if footer_lines:
        footer_html = (
            "<div style='margin-top:16px; color:#888; font-size:0.9em;'>"
            + "".join(f"<div>{_escape(line)}</div>" for line in footer_lines)
            + "</div>"
        )

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"Backgammon <{SMTP_FROM}>"
    msg["To"] = ", ".join(to_addrs)
    msg.set_content(text_body)

    html = f"""\
    <div style="font-family: sans-serif;">
      <img src="cid:board" style="max-width: 100%; border: 1px solid #ccc;" />
      <div style="margin-top:12px;">{summary_html}</div>
      {message_html}
      {footer_html}
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
