"""
Nudges whoever's move is overdue: once at 48 hours of inactivity on a
game, again at 7 days. Runs as a daemon thread inside the same process
as the web app, checking periodically -- there's no separate scheduled
job to set up.

This assumes a single gunicorn worker, which is what this project's
README has you run (`gunicorn app:app`, no -w flag). With more than one
worker, each would run its own copy of this loop and could send
duplicate reminders, since they wouldn't share the "already sent" state
in memory (though the database-backed de-dup below means the worst case
is still just an extra email, not anything actually breaking).
"""

import json
import os
import tempfile
import threading
import time

from board import WHITE, BLACK, other
from game import Game, AWAIT_DOUBLE_RESPONSE, AWAIT_MOVE
from render import save_board_png
from email_io import send_board_email

CHECK_INTERVAL_SECONDS = 30 * 60
REMINDER_48H_SECONDS = 48 * 3600
REMINDER_7D_SECONDS = 7 * 24 * 3600

# Needed to build a board link in reminder emails, since these fire from
# a background thread with no incoming request to read the host from.
PUBLIC_BASE_URL = (os.environ.get("PUBLIC_BASE_URL") or "").rstrip("/") or None


def _actor_for(game):
    """Who needs to act right now, for reminder purposes -- normally
    whoever's turn it is, but the responder (not the doubler) while a
    double is pending, same distinction the board-rotation logic makes."""
    if game.awaiting == AWAIT_DOUBLE_RESPONSE:
        return other(game.pending_doubler)
    return game.to_move


def _send_reminder(row, game, since_label):
    actor = _actor_for(game)
    actor_email = row["white_email"] if actor == WHITE else row["black_email"]
    actor_name = row["white_name"] if actor == WHITE else row["black_name"]

    with tempfile.TemporaryDirectory() as tmp:
        png_path = os.path.join(tmp, "board.png")
        save_board_png(
            game.board, png_path,
            to_move=actor,
            dice=game.dice if game.awaiting == AWAIT_MOVE else None,
            white_name=row["white_name"], black_name=row["black_name"],
            turn_no=len(game.history) + 1,
            cube_value=game.cube_value, cube_owner=game.cube_owner,
            status_text=game.status_text(row["white_name"], row["black_name"]),
        )
        subj = f"[{row['label']}] Reminder: still waiting on {actor_name} ({since_label})"
        footer_lines = [f"Current board: {PUBLIC_BASE_URL}/board/{row['id']}"] if PUBLIC_BASE_URL else []
        send_board_email(
            [actor_email], subj, png_path,
            summary_lines=[
                f"It's been {since_label} since the last move in this game -- "
                f"{actor_name}, it's your turn."
            ],
            footer_lines=footer_lines,
        )


def check_once(store):
    """Scan every game and send any reminders that are due. Kept
    separate from the sleep loop so tests (or a manual check) can call
    it directly without waiting on the real interval."""
    now = time.time()
    for row in store.list_all_raw():
        try:
            game = Game.from_dict(json.loads(row["state_json"]))
        except Exception:
            continue
        if game.is_over():
            continue

        elapsed = now - row["updated_at"]
        if elapsed >= REMINDER_7D_SECONDS and row["reminder_7d_at"] != row["updated_at"]:
            _send_reminder(row, game, "7 days")
            store.mark_reminder_sent(row["id"], "7d", row["updated_at"])
            # 7 days implies well past 48 hours too -- mark that threshold
            # handled as well, so it can't also fire separately later for
            # this same waiting period (e.g. if the very first check for a
            # game only happens once it's already past a week stale, with
            # the 48h mark never having been set at all).
            store.mark_reminder_sent(row["id"], "48h", row["updated_at"])
        elif elapsed >= REMINDER_48H_SECONDS and row["reminder_48h_at"] != row["updated_at"]:
            _send_reminder(row, game, "48 hours")
            store.mark_reminder_sent(row["id"], "48h", row["updated_at"])


def start_background_loop(store):
    def loop():
        while True:
            try:
                check_once(store)
            except Exception as e:
                print(f"[reminder loop error] {e}")
            time.sleep(CHECK_INTERVAL_SECONDS)
    threading.Thread(target=loop, daemon=True).start()
