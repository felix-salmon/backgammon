"""
Shared logic for creating a new game and sending the opening-board email.
Used by start_game.py (for local runs), by app.py's /admin/start_game
route (for creating games against a deployed instance's database, where
there's no other way to reach the right SQLite file), and by the
"rematch" email trigger for starting a fresh game once one finishes.
"""

import re
import tempfile
import os

from render import save_board_png
from email_io import send_board_email

REMATCH_TRIGGERS = {"rematch", "new game", "again", "play again", "new"}


def create_and_announce(store, label, white_email, white_name, black_email, black_name,
                         base_url=None):
    gid = store.create_game(label, white_email, black_email, white_name, black_name)
    row = store.load(gid)
    game = row["game"]

    with tempfile.TemporaryDirectory() as tmp:
        png_path = os.path.join(tmp, "board.png")
        save_board_png(
            game.board, png_path,
            to_move=game.to_move, dice=game.dice,
            white_name=white_name, black_name=black_name, turn_no=1,
            cube_value=game.cube_value, cube_owner=game.cube_owner,
            status_text=game.status_text(white_name, black_name),
        )
        mover_name = white_name if game.to_move == "W" else black_name
        subj = f"[{label}] New game! {mover_name} to play {game.dice[0]}-{game.dice[1]}"
        summary_lines = [
            f"New game started between {white_name} and {black_name}.",
            f"{mover_name} rolled {game.dice[0]}-{game.dice[1]} and plays first.",
            "",
            f"Reply with your move in the subject line, e.g. '[{label}] 24/18 13/11', "
            f"'[{label}] 24-18,13-11', or '[{label}] 2x24'.",
            "Point numbers are always exactly what's printed on the board picture, "
            "for either color.",
            f"Send '[{label}] manual' any time to unlock the doubling cube, or "
            f"'[{label}] greedy' to auto-play a pure race -- forced moves play "
            f"themselves automatically. Once this game finishes, reply 'rematch' "
            f"to start a fresh one against the same opponent.",
        ]
        footer_lines = [f"Current board: {base_url}/board/{gid}"] if base_url else []
        tally = store.get_tally(white_email, black_email)
        if tally["games_played"] > 0:
            footer_lines.append(
                f"Head-to-head: {white_name} {tally['wins'].get(white_email, 0)}-"
                f"{tally['wins'].get(black_email, 0)} {black_name} in games, "
                f"{tally['points'].get(white_email, 0)}-{tally['points'].get(black_email, 0)} in points."
            )
        send_board_email(
            [white_email, black_email], subj, png_path,
            summary_lines=summary_lines, footer_lines=footer_lines,
        )
    return gid


def _label_root_num(label):
    """Split a label into (root, number) -- 'g1' -> ('g', 1), 'g12' ->
    ('g', 12), 'skye' -> ('skye', 1) (no trailing number means it's
    implicitly the first of its own family)."""
    m = re.match(r"^(.*?)(\d+)$", label)
    if m:
        return m.group(1), int(m.group(2))
    return label, 1


def _next_label(store, white_email, black_email, base_label):
    """A fresh label that continues whatever numbering family base_label
    already belongs to, rather than tacking on a suffix -- rematching
    'g1' (when 'g2' also exists as a separate game) gives 'g3', not
    'g1-2'; rematching a plain 'skye' gives 'skye2'."""
    root, base_num = _label_root_num(base_label)
    max_num = base_num
    for _, lbl in store.list_for_pair(white_email, black_email):
        lbl_root, lbl_num = _label_root_num(lbl)
        if lbl_root.lower() == root.lower():
            max_num = max(max_num, lbl_num)
    return f"{root}{max_num + 1}"


def start_rematch(store, finished_row, base_url=None):
    """Start a fresh game between the same two players as finished_row
    (expected to be a game that's already over), auto-generating a label
    that won't collide with any of their previous games."""
    new_label = _next_label(store, finished_row["white_email"], finished_row["black_email"],
                             finished_row["label"])
    return create_and_announce(
        store, new_label,
        finished_row["white_email"], finished_row["white_name"],
        finished_row["black_email"], finished_row["black_name"],
        base_url=base_url,
    )

