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

# Once text is past the split point above, it's the OLD thread contents --
# which, in this app, is mostly copies of our OWN previous emails (move
# confirmations, recent-moves lists, instructions, tally lines, and so
# on), not things a human actually wrote. Rather than show all of that
# back to whoever's reading, these patterns recognize every line format
# this codebase itself generates and strip them out, leaving only actual
# player-written text. Deliberately blacklist-based: human text can say
# anything, but our own output is a small, fixed set of templates we
# control, so recognizing those is far more reliable than guessing at
# what "looks human." If a new outbound message format is added
# elsewhere in the app, add its pattern here too.
_BOT_LINE_PATTERNS = [
    # game.py status_text() / app.py summary_lines outcomes
    re.compile(r"^.+ wins \d+ point\(s\)(?: \(gammon\)| \(backgammon\))?!$"),
    re.compile(r"^.+ offers to double to \d+\. .+: reply 'take' or 'drop'\.$"),
    re.compile(r"^.+'s turn: reply 'roll' or 'double'\.$"),
    re.compile(r"^.+ to play \d+-\d+\.$"),
    re.compile(r"^.+ played .+\.$"),
    re.compile(r"^Hit on: .+\.$"),
    re.compile(r"^.+ takes the double -- cube is now at \d+\.$"),
    re.compile(r"^.+ drops\.$"),
    re.compile(r"^.+ resigns\.$"),
    re.compile(r"^.+ had no legal move\.$"),
    re.compile(r"^.+ \(on greedy\) played .+\.$"),
    re.compile(r"^.+ was forced: .+\.$"),
    re.compile(r"^Head-to-head: .+ in games, .+ in points \(.+\)\.$"),
    re.compile(r"^New game started between .+ and .+\.$"),
    re.compile(r"^.+ rolled \d+-\d+ and plays first\.$"),
    # manual/auto/greedy toggle confirmations
    re.compile(r"^Switched you to (manual|automatic) dice mode\b.*$"),
    re.compile(r"^Turned (on|off) greedy mode\b.*$"),
    # move-history lines ("N. Name rolled X-Y: ..." / "N. Name: action")
    re.compile(r"^Recent moves:$"),
    re.compile(r"^\d+\. .+ rolled \d+-\d+: .+$"),
    re.compile(r"^\d+\. [^:]+: .+$"),
    # footer / meta
    re.compile(r"^Current board: https?://\S+$"),
    re.compile(r"^\(quoted from earlier in the thread\)$"),
    re.compile(r"^\[board image attached\]$"),
    # onboarding / instructional text (new-game and rematch announcements)
    re.compile(r"^Reply with your move in the subject line\b.*$"),
    re.compile(r"^Point numbers are always exactly what's printed\b.*$"),
    re.compile(r"^Send '\[.+\] manual'.*$"),
    re.compile(r"^Once this game finishes, reply 'rematch'.*$"),
    # no-game / which-game / not-so-fast / still-waiting / status listing
    re.compile(r"^I couldn't find a game of yours labeled .+\.$"),
    re.compile(r"^I couldn't find a backgammon game with this address on it\.$"),
    re.compile(r"^You have more than one game going \(.+\)\. Put the game label\b.*$"),
    re.compile(r"^'.+': .+n't .+$"),   # "Not so fast" rejection line ("'input': reason")
    re.compile(r"^\. Reply 'rematch' to start a new game\.$"),
    re.compile(r"^.+'s last message didn't go through, so it's still their move\b.*$"),
    re.compile(r"^You have \d+ active games?:$"),
    re.compile(r"^You don't have any active games right now\.$"),
    re.compile(r"^\[.+\] vs .+: .+$"),
    # reminders
    re.compile(r"^It's been .+ since the last move in this game\b.*it's your turn\.$"),
    re.compile(r"^\[.+\] Reminder: still waiting on .+ \(.+\)$"),
]

# Quote-chain headers an email client inserts (not part of the quote
# markers above, since those are used to find the split point across the
# WHOLE body -- these are checked per-line, after splitting, since a
# reply chain can nest several of them one after another).
_QUOTE_HEADER_LINE_PATTERNS = [
    re.compile(r"^On .{0,140}wrote:\s*$"),
    re.compile(r"^-{2,}\s*Original Message\s*-{2,}", re.IGNORECASE),
    re.compile(r"^_{5,}\s*$"),
    re.compile(r"^(From|Sent|To|Subject|Date):\s*.*$", re.IGNORECASE),
]


def _strip_bot_generated_lines(text):
    """Remove lines from quoted reply text that are recognizably content
    THIS BOT generated (move confirmations, recent-moves lists,
    instructions, tally lines, board links, quote-chain headers, etc.),
    leaving only whatever a human player actually typed. See
    _BOT_LINE_PATTERNS above for what's recognized and why this is
    blacklist- rather than whitelist-based."""
    if not text:
        return text
    kept = []
    for raw_line in text.splitlines():
        # strip leading '>' quote-depth markers a nested reply chain adds
        line = re.sub(r"^(>\s*)+", "", raw_line).strip()
        if not line:
            kept.append("")
            continue
        if any(p.match(line) for p in _QUOTE_HEADER_LINE_PATTERNS):
            continue
        if any(p.match(line) for p in _BOT_LINE_PATTERNS):
            continue
        kept.append(line)
    # collapse runs of blank lines left behind by the removals, and trim
    # leading/trailing blanks
    result_lines = []
    for line in kept:
        if line == "" and (not result_lines or result_lines[-1] == ""):
            continue
        result_lines.append(line)
    return "\n".join(result_lines).strip()


def _split_quoted_reply(text):
    """Split an email body into (new_text, quoted_text) at the earliest
    quote marker found. Nothing gets discarded -- the quoted part is
    returned too, for the caller to show underneath the new text rather
    than silently dropping it. This matters because the split is a
    heuristic: a reply client can occasionally put someone's actual new
    text inside what looks like a quoted block (e.g. resending from a
    quoted draft), and losing that outright is worse than just labeling
    it as possibly-old and showing it anyway.
    """
    if not text:
        return text, ""
    cut_at = len(text)
    for pattern in _QUOTE_MARKERS:
        m = pattern.search(text)
        if m and m.start() < cut_at:
            cut_at = m.start()
    return text[:cut_at].strip(), text[cut_at:].strip()


def parse_inbound_improvmx(payload):
    """payload: the parsed JSON body ImprovMX POSTs to your webhook.
    Returns dict with sender, subject (cleaned), body (the part of the
    message above any quoted reply chain), and quoted (whatever came
    after that split point, with the bot's own previously-sent content
    stripped out -- see _strip_bot_generated_lines -- leaving only
    actual player-written text from earlier in the thread, if any).
    """
    sender = ((payload.get("from") or {}).get("email") or "").strip().lower()
    subject = payload.get("subject", "") or ""
    subject = SUBJECT_PREFIX_RE.sub("", subject).strip()
    raw_body = payload.get("text", "") or ""
    body, quoted = _split_quoted_reply(raw_body.strip())
    quoted = _strip_bot_generated_lines(quoted)
    return {"sender": sender, "subject": subject, "body": body, "quoted": quoted}


def _require_smtp_config():
    if not (SMTP_HOST and SMTP_USER and SMTP_PASS):
        raise RuntimeError(
            "SMTP_HOST / SMTP_USER / SMTP_PASS are not set. See README.md for setup."
        )


def _send(msg, retries=4, retry_delays=(5, 15, 30)):
    """Send via SMTP, retrying on transient connection failures before
    giving up and letting the caller's error handling take over. The
    delays back off (5s, 15s, 30s -- about a minute total) since this
    runs in a background thread with nothing waiting on it, and a real
    provider-side blip can last a couple of minutes, not just a second."""
    _require_smtp_config()
    import time
    last_err = None
    for attempt in range(retries):
        try:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
                server.starttls()
                server.login(SMTP_USER, SMTP_PASS)
                server.send_message(msg)
            return
        except (smtplib.SMTPException, OSError) as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(retry_delays[min(attempt, len(retry_delays) - 1)])
    raise last_err


def send_board_email(to_addrs, subject, image_path, summary_lines=None,
                      sender_name=None, message_text=None, quoted_text=None,
                      history_lines=None, footer_lines=None):
    """Send the rendered board PNG to one or more recipients, laid out as
    distinct pieces, in order:
        summary_lines -- what just happened (one line each), e.g.
                          "Felix played 24/18 13/11.", "Hit on: 18."
        message_text  -- the sender's own note, if any, clearly attributed
                          to sender_name and set apart from the summary
        quoted_text   -- anything that looked like older quoted content
                          from earlier in the thread (rather than discard
                          it outright, in case the split guessed wrong),
                          shown smaller and clearly labeled as such
        history_lines -- the last several moves, shown at full legibility
                          (not dimmed) as plain reference text
        footer_lines   -- secondary info (tally, board link), dimmed and
                          pushed to the bottom
    to_addrs: list of email addresses.
    """
    summary_lines = summary_lines or []
    footer_lines = footer_lines or []
    history_lines = history_lines or []

    text_parts = []
    if summary_lines:
        text_parts.append("\n".join(summary_lines))
    if message_text:
        prefix = f"{sender_name}: " if sender_name else ""
        text_parts.append(f"{prefix}{message_text}")
    if quoted_text:
        text_parts.append(f"(quoted from earlier in the thread)\n{quoted_text}")
    if history_lines:
        text_parts.append("Recent moves:\n" + "\n".join(history_lines))
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

    quoted_html = ""
    if quoted_text:
        quoted_html = (
            "<div style='margin-top:10px; padding-left:12px; "
            "border-left:3px solid #444; white-space:pre-wrap;'>"
            "<em style='color:#999;'>(quoted from earlier in the thread)</em><br>"
            f"{_escape(quoted_text)}</div>"
        )

    history_html = ""
    if history_lines:
        history_html = (
            "<div style='margin-top:14px;'><strong>Recent moves:</strong>"
            + "".join(f"<div>{_escape(line)}</div>" for line in history_lines)
            + "</div>"
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
      {quoted_html}
      {history_html}
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
