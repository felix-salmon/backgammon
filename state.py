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
    updated_at REAL NOT NULL
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
    def __init__(self, path="backgammon.db"):
        self.path = path
        with closing(sqlite3.connect(self.path)) as con:
            con.executescript(SCHEMA)
            con.commit()

    def create_game(self, label, white_email, black_email, white_name="White", black_name="Black"):
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
        tag you put in the email subject, like '[game2]')."""
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
        if len(ids) == 1:
            return self.load(ids[0])
        # multiple games -- caller should disambiguate by label
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
