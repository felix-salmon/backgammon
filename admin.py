"""
Shared logic for creating a new game and sending the opening-board email.
Used both by start_game.py (for local runs) and by app.py's /admin/start_game
route (for creating games against a deployed instance's database, where
there's no other way to reach the right SQLite file).
"""

import tempfile
import os

from render import save_board_png
from email_io import send_board_email


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
            f"themselves automatically.",
        ]
        footer_lines = [f"Current board: {base_url}/board/{gid}"] if base_url else []
        send_board_email(
            [white_email, black_email], subj, png_path,
            summary_lines=summary_lines, footer_lines=footer_lines,
        )
    return gid

