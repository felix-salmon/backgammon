"""
Create a new game and email both players the opening board.

This talks to whatever SQLite file BACKGAMMON_DB points at (default:
./backgammon.db). That's only useful if you're running it somewhere that
shares a filesystem with your deployed app -- for a Render deployment,
your laptop does NOT share a filesystem with the server, so use the
/admin/start_game HTTP endpoint instead (see README.md).

Usage:
    python3 start_game.py <label> <white_email> <white_name> <black_email> <black_name>

Example:
    python3 start_game.py g1 felix@felixsalmon.com Felix simon@example.com Simon
"""

import os
import sys

from state import Store
from admin import create_and_announce

DB_PATH = os.environ.get("BACKGAMMON_DB", "backgammon.db")


def main():
    if len(sys.argv) != 6:
        print(__doc__)
        sys.exit(1)

    label, white_email, white_name, black_email, black_name = sys.argv[1:]
    store = Store(DB_PATH)
    gid = create_and_announce(store, label, white_email, white_name, black_email, black_name)
    print(f"Game '{label}' created (id={gid}). Opening email sent to both players.")


if __name__ == "__main__":
    main()
