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

# Email addresses of players who specifically want their home board to
# always read 1-6 -- which, under this app's absolute numbering, is
# already exactly what White's home board is (it doesn't rotate or
# change with perspective, just with who's on roll). Rather than build
# an entire separate relative-numbering scheme for one person's
# preference, the simplest fix is making sure they're always assigned
# White, regardless of who happens to initiate the game or rematch.
# Set via a comma-separated env var so this doesn't need a code change
# to add someone.
ALWAYS_WHITE_EMAILS = {
    e.strip().lower() for e in os.environ.get("ALWAYS_WHITE_EMAILS", "").split(",") if e.strip()
}


def format_tally_line(tally, name_a, email_a, name_b, email_b):
    """The one-line head-to-head summary shown at the end of a game and
    at the start of a rematch -- shared so both spots (and app.py's own
    game-over notification) stay in sync rather than drifting apart as
    separate copies of the same formatting."""
    wins_a = tally["wins"].get(email_a, 0)
    wins_b = tally["wins"].get(email_b, 0)
    pts_a = tally["points"].get(email_a, 0)
    pts_b = tally["points"].get(email_b, 0)
    diff = pts_a - pts_b
    if diff > 0:
        net = f"{name_a} +{diff}"
    elif diff < 0:
        net = f"{name_b} +{-diff}"
    else:
        net = "tied"
    return (f"Head-to-head: {name_a} {wins_a}-{wins_b} {name_b} in games, "
            f"{pts_a}-{pts_b} in points ({net}).")


def create_and_announce(store, label, white_email, white_name, black_email, black_name,
                         base_url=None):
    # honor an "always White" preference regardless of who initiated
    # this game -- if only the intended-Black player has it, swap the
    # assignment; if both do (or neither), leave it as given, since
    # there's no way to satisfy two such preferences at once anyway.
    if (black_email.strip().lower() in ALWAYS_WHITE_EMAILS
            and white_email.strip().lower() not in ALWAYS_WHITE_EMAILS):
        white_email, black_email = black_email, white_email
        white_name, black_name = black_name, white_name

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
            f"'[{label}] greedy' to turn on bear-off auto-play (send it again to "
            f"turn off) -- once you're all home, it plays any turn where there's "
            f"a single clear way to bear off the most checkers, and leaves "
            f"anything else for you. Once this game finishes, reply 'rematch' "
            f"to start a fresh one against the same opponent.",
        ]
        footer_lines = [f"Current board: {base_url}/board/{gid}"] if base_url else []
        tally = store.get_tally(white_email, black_email)
        if tally["games_played"] > 0:
            footer_lines.append(format_tally_line(tally, white_name, white_email, black_name, black_email))
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


def _first_available_label(store, email_a, email_b, desired_label):
    """A label for a brand-new pairing: use it as-is if these two haven't
    used it before (the common case -- e.g. 'skye' for a first-ever game
    against someone new), otherwise fall back to the same incrementing
    scheme rematches use."""
    existing = {lbl.lower() for _, lbl in store.list_for_pair(email_a, email_b)}
    if desired_label.lower() not in existing:
        return desired_label
    return _next_label(store, email_a, email_b, desired_label)


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

