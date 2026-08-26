"""
Flask app: receives ImprovMX's webhook for each incoming email, applies
the move/command, and emails both players the new board -- the actual
PBM replacement.

Deploy this somewhere reachable (Render, Railway, Fly.io, a small VPS...),
then in the ImprovMX dashboard set your game's alias (e.g.
bg@felixsalmon.com) to forward to:

    https://yourhost/inbound

See README.md for the full walkthrough.

Run locally for testing with:  python3 app.py
(then use ngrok or similar to expose it to ImprovMX while testing)
"""

import base64
import html
import os
import re
import tempfile
import threading

from flask import Flask, request, Response

from game import (
    IllegalMove, CommandError, TurnRecord, CubeEvent,
    AWAIT_DOUBLE_RESPONSE, AWAIT_ROLL_OR_DOUBLE,
)
from render import save_board_png
from state import Store
from email_io import parse_inbound_improvmx, send_board_email, send_text_email
from admin import create_and_announce, start_rematch, REMATCH_TRIGGERS
from board import WHITE, BLACK, other

DB_PATH = os.environ.get("BACKGAMMON_DB", "backgammon.db")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN")
# Who gets the quiet "still waiting on X" heads-up when someone's move
# fails validation. Defaults to just Felix; override with a comma-
# separated list via the env var if you want to add or change recipients.
NOTIFY_WAITING_EMAILS = {
    e.strip().lower() for e in
    os.environ.get("NOTIFY_WAITING_EMAILS", "felix@felixsalmon.com").split(",")
    if e.strip()
}
RESEND_TRIGGERS = {"resend", "resend last move", "resend move"}
store = Store(DB_PATH)

app = Flask(__name__)


def _bg(fn, *args, **kwargs):
    """Run fn in a background thread so the webhook response comes back
    fast (reducing the odds ImprovMX times out and retries), and catch
    any exception so a failed send doesn't vanish without a trace -- it
    still shows up in the server logs."""
    def wrapper():
        try:
            fn(*args, **kwargs)
        except Exception as e:
            print(f"[background task error] {fn.__name__}: {e}")
    threading.Thread(target=wrapper, daemon=True).start()


@app.route("/admin/start_game", methods=["POST"])
def admin_start_game():
    """Create a new game against THIS deployment's database and email the
    opening board. Needed because a deployed instance's SQLite file isn't
    reachable from your laptop -- this is the only way to reach it.

    Protected by a shared-secret token (env var ADMIN_TOKEN). If that env
    var isn't set, this route refuses all requests.

    curl example:
        curl -X POST https://yourhost/admin/start_game \\
          -H "X-Admin-Token: $ADMIN_TOKEN" \\
          -H "Content-Type: application/json" \\
          -d '{"label":"g1","white_email":"felix@felixsalmon.com","white_name":"Felix",
               "black_email":"simon@example.com","black_name":"Simon"}'
    """
    if not ADMIN_TOKEN or request.headers.get("X-Admin-Token") != ADMIN_TOKEN:
        return ("forbidden", 403)

    data = request.get_json(force=True, silent=True) or {}
    required = ["label", "white_email", "white_name", "black_email", "black_name"]
    missing = [k for k in required if not data.get(k)]
    if missing:
        return (f"missing fields: {', '.join(missing)}", 400)

    base_url = request.host_url.rstrip("/")
    gid = create_and_announce(
        store, data["label"], data["white_email"], data["white_name"],
        data["black_email"], data["black_name"], base_url=base_url,
    )
    return ({"game_id": gid}, 200)


@app.route("/board/<int:game_id>", methods=["GET"])
def board_image(game_id):
    """A live view of a game's current state -- click this any time to
    check whether a move has gone through and whose turn it is, without
    waiting on an email. Shows the board (with its last few moves baked
    in, same as the emails) plus the complete move history below it."""
    row = store.load(game_id)
    if row is None:
        return ("no such game", 404)
    game = row["game"]
    with tempfile.TemporaryDirectory() as tmp:
        png_path = os.path.join(tmp, "board.png")
        save_board_png(
            game.board, png_path,
            to_move=game.to_move if not game.is_over() else None,
            dice=game.dice if (not game.is_over() and game.awaiting == "move") else None,
            white_name=row["white_name"], black_name=row["black_name"],
            turn_no=len(game.history) + 1,
            cube_value=game.cube_value, cube_owner=game.cube_owner,
            status_text=game.status_text(row["white_name"], row["black_name"]),
        )
        with open(png_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode("ascii")

    full_history = _history_lines(row, game)
    history_html = "".join(f"<div>{html.escape(line)}</div>" for line in reversed(full_history))
    page = f"""\
    <!DOCTYPE html>
    <html>
    <head><title>{html.escape(row['label'])} -- {row['white_name']} vs {row['black_name']}</title></head>
    <body style="background:#221f1c; color:#eee; font-family:sans-serif; padding:20px; max-width:820px; margin:0 auto;">
      <img src="data:image/png;base64,{img_b64}" style="max-width:100%; border:1px solid #444;" />
      <h3 style="margin-top:24px;">Full move history</h3>
      <div style="line-height:1.6;">{history_html or '<em>No moves yet.</em>'}</div>
    </body>
    </html>
    """
    return Response(page, mimetype="text/html")


@app.route("/inbound", methods=["POST"])
def inbound():
    payload = request.get_json(force=True, silent=True) or {}

    # Webhook senders retry on timeouts and network hiccups as standard
    # practice -- ImprovMX includes a stable message-id we can use to make
    # sure a retried delivery of the same email is a harmless no-op rather
    # than re-applying (or wrongly rejecting) the same move twice.
    message_id = payload.get("message-id")
    if message_id and store.seen_message(message_id):
        return ("", 204)

    # request is only valid inside this handler -- background threads
    # can't touch it, so anything that needs the deployment's public URL
    # has to be captured now and passed along explicitly.
    base_url = request.host_url.rstrip("/")

    msg = parse_inbound_improvmx(payload)
    sender, subject, body, quoted = msg["sender"], msg["subject"], msg["body"], msg["quoted"]

    if not sender:
        return ("", 204)

    label, input_text = _extract_label(subject)
    row = store.find_game_for_player(sender, label=label)
    if row is None:
        matches = store.list_for_player(sender)
        if label:
            _bg(send_text_email, sender, "No game found",
                f"I couldn't find a game of yours labeled '[{label}]'.")
        elif len(matches) > 1:
            labels = ", ".join(f"[{lbl}]" for _, lbl in matches)
            links = "\n".join(f"[{lbl}]: {base_url}/board/{gid}" for gid, lbl in matches)
            _bg(send_text_email, sender, "Which game?",
                f"You have more than one game going ({labels}). "
                f"Put the game label at the start of your subject, e.g. "
                f"'[{matches[0][1]}] 24/18 13/11'.\n\n{links}")
        else:
            _bg(send_text_email, sender, "No game found",
                "I couldn't find a backgammon game with this address on it.")
        return ("", 204)

    game = row["game"]
    player = WHITE if sender == row["white_email"] else BLACK
    sender_name = row["white_name"] if player == WHITE else row["black_name"]
    opponent_email = row["black_email"] if player == WHITE else row["white_email"]
    board_link = f"{base_url}/board/{row['id']}"

    if game.is_over() and input_text.strip().lower().rstrip(".!") in REMATCH_TRIGGERS:
        start_rematch(store, row, base_url=base_url)
        return ("", 204)

    if input_text.strip().lower().rstrip(".!") in RESEND_TRIGGERS:
        _bg(_resend_last_move, row, game, base_url, sender)
        return ("", 204)

    try:
        result = game.process_input(player, input_text, body)
    except (IllegalMove, CommandError) as e:
        quoted_note = f"\n\n(quoted from earlier in the thread)\n{quoted}" if quoted else ""
        over_hint = ". Reply 'rematch' to start a new game." if game.is_over() else ""
        _bg(send_text_email, sender, "Not so fast",
            f"'{input_text}': {e}{over_hint}\n\nCurrent board: {board_link}{quoted_note}")
        if opponent_email in NOTIFY_WAITING_EMAILS:
            _bg(send_text_email, opponent_email, f"[{row['label']}] still waiting on {sender_name}",
                f"{sender_name}'s last message didn't go through, so it's still their move. "
                f"No action needed from you.\n\nCurrent board: {board_link}")
        return ("", 204)

    store.save(row["id"], game)

    if game.is_over():
        summary = game.result_summary()
        winner_email = row["white_email"] if summary["winner"] == WHITE else row["black_email"]
        loser_email = row["black_email"] if summary["winner"] == WHITE else row["white_email"]
        store.record_result(row["id"], winner_email, loser_email, summary["points"],
                             summary["multiplier"], summary["cube_value"], game.win_reason)

    _bg(_notify_both, row, game, body, result, base_url, player, quoted)
    return ("", 204)


@app.route("/tally", methods=["GET"])
def tally():
    """Head-to-head record between two players across every completed
    game between them. GET /tally?a=alice@x.com&b=bob@x.com"""
    a = (request.args.get("a") or "").strip().lower()
    b = (request.args.get("b") or "").strip().lower()
    if not a or not b:
        return ("usage: /tally?a=email1&b=email2", 400)
    return store.get_tally(a, b)


def _resend_last_move(row, game, base_url, requester_email):
    """Re-send the most recent turn's board + summary + message -- to
    just the requester, not both players. Reconstructed from the game's
    own history rather than depending on the original email having gone
    out correctly, since that's exactly the scenario this is for."""
    if not game.history:
        send_text_email(requester_email, f"[{row['label']}] Nothing to resend",
                         "No moves have been played in this game yet.")
        return

    last = game.history[-1]
    mover_name = row["white_name"] if last.player == WHITE else row["black_name"]
    summary_lines = [f"{mover_name} played {last.move_text}."]
    if last.hits:
        summary_lines.append(f"Hit on: {', '.join(str(p) for p in last.hits)}.")

    footer_lines = [f"Current board: {base_url}/board/{row['id']}"] if base_url else []

    if game.is_over():
        winner_name = row["white_name"] if game.winner == WHITE else row["black_name"]
        summary = game.result_summary()
        kind_suffix = {"normal": "", "gammon": " (gammon)", "backgammon": " (backgammon)"}[summary["kind"]]
        summary_lines.append(f"{winner_name} wins {summary['points']} point(s){kind_suffix}!")
        tally = store.get_tally(row["white_email"], row["black_email"])
        wn, bn = row["white_name"], row["black_name"]
        we, be = row["white_email"], row["black_email"]
        footer_lines.append(
            f"Head-to-head: {wn} {tally['wins'].get(we, 0)}-{tally['wins'].get(be, 0)} {bn} "
            f"in games, {tally['points'].get(we, 0)}-{tally['points'].get(be, 0)} in points."
        )

    with tempfile.TemporaryDirectory() as tmp:
        png_path = os.path.join(tmp, "board.png")
        save_board_png(
            game.board, png_path,
            to_move=game.to_move if not game.is_over() else None,
            dice=game.dice if (not game.is_over() and game.awaiting == "move") else None,
            white_name=row["white_name"], black_name=row["black_name"],
            turn_no=len(game.history) + 1,
            cube_value=game.cube_value, cube_owner=game.cube_owner,
            status_text=game.status_text(row["white_name"], row["black_name"]),
        )
        next_part = _next_part(game, row)
        subj = f"[{row['label']}] (resent) {next_part} after {mover_name} played {last.move_text}"
        send_board_email(
            [requester_email], subj, png_path,
            summary_lines=summary_lines,
            sender_name=mover_name if last.message else None,
            message_text=last.message or None,
            history_lines=_history_lines(row, game, limit=6),
            footer_lines=footer_lines,
        )


def _notify_both(row, game, message, result, base_url=None, sender_player=None, quoted_text=None):
    summary_lines = []

    if isinstance(result, str):
        summary_lines.append(result)
    elif isinstance(result, CubeEvent):
        white_name, black_name = row["white_name"], row["black_name"]
        who = white_name if result.player == WHITE else black_name
        if result.kind == "offered":
            summary_lines.append(f"{who} offers to double to {result.value}.")
        elif result.kind == "taken":
            summary_lines.append(f"{who} takes the double -- cube is now at {result.value}.")
        elif result.kind == "dropped":
            summary_lines.append(f"{who} drops.")
        elif result.kind == "resigned":
            summary_lines.append(f"{who} resigns.")
    elif isinstance(result, TurnRecord):
        mover_name = row["white_name"] if result.player == WHITE else row["black_name"]
        summary_lines.append(f"{mover_name} played {result.move_text}.")
        if result.hits:
            summary_lines.append(f"Hit on: {', '.join(str(p) for p in result.hits)}.")

    for auto in game.last_auto_played:
        who = row["white_name"] if auto.player == WHITE else row["black_name"]
        if auto.move_text == "(no legal move)":
            summary_lines.append(f"{who} had no legal move.")
        else:
            summary_lines.append(f"{who} was forced: {auto.move_text}.")

    footer_lines = []

    if game.is_over():
        winner_name = row["white_name"] if game.winner == WHITE else row["black_name"]
        summary = game.result_summary()
        kind_suffix = {"normal": "", "gammon": " (gammon)", "backgammon": " (backgammon)"}[summary["kind"]]
        summary_lines.append(f"{winner_name} wins {summary['points']} point(s){kind_suffix}!")

        tally = store.get_tally(row["white_email"], row["black_email"])
        wn, bn = row["white_name"], row["black_name"]
        we, be = row["white_email"], row["black_email"]
        footer_lines.append(
            f"Head-to-head: {wn} {tally['wins'].get(we, 0)}-{tally['wins'].get(be, 0)} {bn} "
            f"in games, {tally['points'].get(we, 0)}-{tally['points'].get(be, 0)} in points."
        )

    if base_url:
        footer_lines.append(f"Current board: {base_url}/board/{row['id']}")

    sender_name = None
    if sender_player is not None:
        sender_name = row["white_name"] if sender_player == WHITE else row["black_name"]

    with tempfile.TemporaryDirectory() as tmp:
        png_path = os.path.join(tmp, "board.png")
        save_board_png(
            game.board, png_path,
            to_move=game.to_move if not game.is_over() else None,
            dice=game.dice if (not game.is_over() and game.awaiting == "move") else None,
            white_name=row["white_name"], black_name=row["black_name"],
            turn_no=len(game.history) + 1,
            cube_value=game.cube_value, cube_owner=game.cube_owner,
            status_text=game.status_text(row["white_name"], row["black_name"]),
        )
        subj = f"[{row['label']}] {_subject_summary(game, result, row)}"
        send_board_email(
            [row["white_email"], row["black_email"]], subj, png_path,
            summary_lines=summary_lines, sender_name=sender_name,
            message_text=message, quoted_text=quoted_text,
            history_lines=_history_lines(row, game, limit=6),
            footer_lines=footer_lines,
        )


def _player_name(row, player):
    return row["white_name"] if player == WHITE else row["black_name"]


def _history_lines(row, game, limit=None):
    """Formatted move-history lines, most recent last, each numbered by
    its actual position in the game (so a 'last N' slice still shows the
    real turn numbers, not 1..N). Always shows the dice that were rolled,
    even for a turn with no legal move -- so a dance is at least visible
    as 'rolled a 4-4, no legal move' rather than just vanishing."""
    numbered = list(enumerate(game.history, start=1))
    if limit is not None:
        numbered = numbered[-limit:]
    lines = []
    for turn_no, rec in numbered:
        name = _player_name(row, rec.player)
        dice_str = f"{rec.dice[0]}-{rec.dice[1]}" if rec.dice else "?"
        body = "no legal move" if rec.move_text == "(no legal move)" else rec.move_text
        lines.append(f"{turn_no}. {name} rolled {dice_str}: {body}")
    return lines


def _next_part(game, row):
    """Short phrase describing who needs to do what next, for the
    subject line -- 'Skye to move', 'Felix to respond', 'Simon wins!'"""
    if game.is_over():
        return f"{_player_name(row, game.winner)} wins!"
    if game.awaiting == AWAIT_DOUBLE_RESPONSE:
        responder = other(game.pending_doubler)
        return f"{_player_name(row, responder)} to respond"
    if game.awaiting == AWAIT_ROLL_OR_DOUBLE:
        return f"{_player_name(row, game.to_move)} to roll or double"
    return f"{_player_name(row, game.to_move)} to move"


def _subject_summary(game, result, row):
    happened = None
    if isinstance(result, TurnRecord):
        mover = _player_name(row, result.player)
        happened = f"{mover} played {result.move_text}"
    elif isinstance(result, CubeEvent):
        who = _player_name(row, result.player)
        verb = {
            "offered": f"offered to double to {result.value}",
            "taken": f"took the double to {result.value}",
            "dropped": "dropped",
            "resigned": "resigned",
        }.get(result.kind, result.kind)
        happened = f"{who} {verb}"

    next_part = _next_part(game, row)
    return f"{next_part} after {happened}" if happened else next_part


def _extract_label(subject):
    """Pull a leading '[label]' off a subject line, if present. Returns
    (label_or_None, remaining_text)."""
    m = re.match(r"^\[([^\]]+)\]\s*(.*)$", subject.strip())
    if m:
        return m.group(1), m.group(2)
    return None, subject.strip()


if __name__ == "__main__":
    app.run(port=int(os.environ.get("PORT", 5000)), debug=True)
