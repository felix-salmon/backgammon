"""
Minimal persistence layer: one SQLite file, one row per game.
Enough for a couple of friends running a handful of games at once.
"""

import json
import sqlite3
import time
from contextlib import closing

from game import Game

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL,
    white_email TEXT NOT NULL,
    black_email TEXT NOT NULL,
    white_name TEXT NOT NULL,
    black_name TEXT NOT NULL,
    state_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    reminder_48h_at REAL,
    reminder_7d_at REAL
);
CREATE TABLE IF NOT EXISTS processed_messages (
    message_id TEXT PRIMARY KEY,
    processed_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id INTEGER NOT NULL UNIQUE,
    winner_email TEXT NOT NULL,
    loser_email TEXT NOT NULL,
    points INTEGER NOT NULL,
    multiplier INTEGER NOT NULL,
    cube_value INTEGER NOT NULL,
    win_reason TEXT NOT NULL,
    recorded_at REAL NOT NULL
);
"""


class Store:
    """One SQLite file, one row per game.

    Concurrency note: load-modify-save (read a game, apply a move,
    write it back) is not wrapped in a transaction, so it's only safe
    under a single writer -- which is what this project's README has
    you run (`gunicorn app:app`, no -w flag: one worker, requests
    handled one at a time). With more than one worker or thread able to
    process requests concurrently, two replies to the same game
    arriving close together could race and one could silently clobber
    the other's update. WAL mode below helps general read/write
    concurrency but does not by itself fix that specific race -- fixing
    it properly would need each save to check the row hasn't changed
    since it was loaded (e.g. a version column) and retry if it has.
    Not implemented, since it's real complexity for a scenario ("two
    people's replies to the same specific game landing in the same
    instant") that a single-worker deployment doesn't have.
    """

    def __init__(self, path="backgammon.db"):
        self.path = path
        with closing(sqlite3.connect(self.path)) as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript(SCHEMA)
            # migrate older databases created before reminders existed --
            # CREATE TABLE IF NOT EXISTS above is a no-op on a table that
            # already exists, so a pre-existing games table needs these
            # columns added by hand.
            cols = {r[1] for r in con.execute("PRAGMA table_info(games)").fetchall()}
            if "reminder_48h_at" not in cols:
                con.execute("ALTER TABLE games ADD COLUMN reminder_48h_at REAL")
            if "reminder_7d_at" not in cols:
                con.execute("ALTER TABLE games ADD COLUMN reminder_7d_at REAL")
            con.commit()

    def create_game(self, label, white_email, black_email, white_name="White", black_name="Black"):
        # normalize case at the point of storage -- inbound sender
        # addresses always arrive lowercased already, so a game created
        # with different casing (e.g. typed by hand via curl) would
        # otherwise silently never match anything that player sends in
        white_email = white_email.strip().lower()
        black_email = black_email.strip().lower()
        game = Game.new()
        now = time.time()
        with closing(sqlite3.connect(self.path)) as con:
            cur = con.execute(
                "INSERT INTO games (label, white_email, black_email, white_name, black_name, "
                "state_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (label, white_email, black_email, white_name, black_name,
                 json.dumps(game.to_dict()), now, now),
            )
            con.commit()
            return cur.lastrowid

    def load(self, game_id):
        with closing(sqlite3.connect(self.path)) as con:
            row = con.execute(
                "SELECT id, label, white_email, black_email, white_name, black_name, state_json "
                "FROM games WHERE id=?", (game_id,)
            ).fetchone()
        if row is None:
            return None
        gid, label, we, be, wn, bn, state_json = row
        game = Game.from_dict(json.loads(state_json))
        return {
            "id": gid, "label": label,
            "white_email": we, "black_email": be,
            "white_name": wn, "black_name": bn,
            "game": game,
        }

    def save(self, game_id, game):
        with closing(sqlite3.connect(self.path)) as con:
            con.execute(
                "UPDATE games SET state_json=?, updated_at=? WHERE id=?",
                (json.dumps(game.to_dict()), time.time(), game_id),
            )
            con.commit()

    def find_game_for_player(self, email, label=None):
        """Find the (probably unique) in-progress game a given email address is part of.
        If a player has several games going, pass label to disambiguate (e.g. from a
        tag you put in the email subject, like '[game2]'). Without a label, finished
        games are only used as a last resort -- if this player has exactly one game
        still in progress, that's the one returned even if other, finished games
        also exist under the same email (e.g. right after a rematch, when the old
        finished game and the new live one both still match by address)."""
        with closing(sqlite3.connect(self.path)) as con:
            rows = con.execute(
                "SELECT id FROM games WHERE white_email=? OR black_email=?", (email, email)
            ).fetchall()
        ids = [r[0] for r in rows]
        if not ids:
            return None
        if label:
            for gid in ids:
                row = self.load(gid)
                if row and row["label"].lower() == label.lower():
                    return row
            return None
        loaded = [self.load(gid) for gid in ids]
        loaded = [r for r in loaded if r is not None]
        in_progress = [r for r in loaded if not r["game"].is_over()]
        if len(in_progress) == 1:
            return in_progress[0]
        if not in_progress and len(loaded) == 1:
            # their only game at all happens to be finished -- still
            # useful to resolve unlabeled (e.g. 'resend' or 'rematch')
            return loaded[0]
        # multiple in-progress games, or multiple finished ones with none
        # live -- genuinely ambiguous, caller should ask for a label
        return None

    def list_for_player(self, email):
        with closing(sqlite3.connect(self.path)) as con:
            rows = con.execute(
                "SELECT id, label FROM games WHERE white_email=? OR black_email=?", (email, email)
            ).fetchall()
        return rows

    def list_for_pair(self, email_a, email_b):
        """All games (any status) between exactly these two email
        addresses, regardless of which color each played. Used to pick a
        fresh, non-colliding label when starting a rematch."""
        with closing(sqlite3.connect(self.path)) as con:
            rows = con.execute(
                "SELECT id, label FROM games WHERE "
                "(white_email=? AND black_email=?) OR (white_email=? AND black_email=?)",
                (email_a, email_b, email_b, email_a),
            ).fetchall()
        return rows

    def is_seen(self, message_id):
        """Read-only idempotency check -- has this webhook delivery
        already been fully handled? Doesn't record anything itself; see
        mark_seen, which the caller should call only after successfully
        finishing the work for this message."""
        with closing(sqlite3.connect(self.path)) as con:
            row = con.execute(
                "SELECT 1 FROM processed_messages WHERE message_id=?", (message_id,)
            ).fetchone()
        return row is not None

    def mark_seen(self, message_id):
        """Record a webhook delivery as fully handled. Call this only
        once the corresponding work (applying a move, sending a reply,
        etc.) has actually completed -- marking it seen any earlier
        means a crash between marking and finishing would make a retry
        of a genuinely-lost message look like a harmless duplicate and
        get silently dropped instead of retried."""
        with closing(sqlite3.connect(self.path)) as con:
            con.execute(
                "INSERT OR IGNORE INTO processed_messages (message_id, processed_at) VALUES (?, ?)",
                (message_id, time.time()),
            )
            con.commit()

    def seen_message(self, message_id):
        """Idempotency check for webhook deliveries. Returns True if this
        message_id has already been processed (in which case the caller
        should treat this delivery as a harmless duplicate and do nothing
        further); records it and returns False the first time. Webhook
        senders retry on timeouts/network hiccups as standard practice, so
        every inbound handler needs to tolerate seeing the same event more
        than once -- this is what makes that safe."""
        with closing(sqlite3.connect(self.path)) as con:
            cur = con.execute(
                "INSERT OR IGNORE INTO processed_messages (message_id, processed_at) VALUES (?, ?)",
                (message_id, time.time()),
            )
            con.commit()
            return cur.rowcount == 0

    def record_result(self, game_id, winner_email, loser_email, points, multiplier,
                       cube_value, win_reason):
        """Records a finished game's outcome for the running tally. Keyed
        uniquely by game_id, so calling this more than once for the same
        game (e.g. from a retried webhook) only records it once."""
        with closing(sqlite3.connect(self.path)) as con:
            cur = con.execute(
                "INSERT OR IGNORE INTO results "
                "(game_id, winner_email, loser_email, points, multiplier, cube_value, "
                "win_reason, recorded_at) VALUES (?,?,?,?,?,?,?,?)",
                (game_id, winner_email, loser_email, points, multiplier, cube_value,
                 win_reason, time.time()),
            )
            con.commit()
            return cur.rowcount == 1

    def get_tally(self, email_a, email_b):
        """Aggregate head-to-head record between two players across every
        completed game between them, regardless of label."""
        with closing(sqlite3.connect(self.path)) as con:
            rows = con.execute(
                "SELECT winner_email, loser_email, points FROM results WHERE "
                "(winner_email=? AND loser_email=?) OR (winner_email=? AND loser_email=?)",
                (email_a, email_b, email_b, email_a),
            ).fetchall()
        wins = {email_a: 0, email_b: 0}
        points = {email_a: 0, email_b: 0}
        for winner_email, loser_email, pts in rows:
            wins[winner_email] = wins.get(winner_email, 0) + 1
            points[winner_email] = points.get(winner_email, 0) + pts
        return {"a": email_a, "b": email_b, "games_played": len(rows),
                "wins": wins, "points": points}

    def list_all_raw(self):
        """Every game row, un-deserialized, for the reminder checker to
        scan. Includes reminder_48h_at/reminder_7d_at -- each holds the
        updated_at value the game had when that reminder was last sent,
        so comparing it against the CURRENT updated_at tells you whether
        a reminder has already gone out for this specific waiting period
        (any new activity changes updated_at, which naturally clears the
        way for a fresh reminder next time the game goes quiet again)."""
        with closing(sqlite3.connect(self.path)) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "SELECT id, label, white_email, black_email, white_name, black_name, "
                "state_json, updated_at, reminder_48h_at, reminder_7d_at FROM games"
            ).fetchall()
        return [dict(r) for r in rows]

    def mark_reminder_sent(self, game_id, which, updated_at_value):
        col = "reminder_48h_at" if which == "48h" else "reminder_7d_at"
        with closing(sqlite3.connect(self.path)) as con:
            con.execute(f"UPDATE games SET {col}=? WHERE id=?", (updated_at_value, game_id))
            con.commit()
