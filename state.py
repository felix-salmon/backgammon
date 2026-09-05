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

# Kept separate from SCHEMA above since this one can legitimately fail to
# apply -- on a database that already has two rows sharing a
# (white_email, black_email, label) combo from before this existed (e.g.
# a race between two background rematch/new-game creations), SQLite
# refuses to build the index. That's fine: the app should still start up
# and let you find and clean up the duplicate via /admin/games, not
# crash outright.
UNIQUE_PAIR_LABEL_INDEX_SQL = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_games_pair_label "
    "ON games(white_email, black_email, label)"
)


class Store:
    """One SQLite file, one row per game.

    Concurrency note: load-modify-save (read a game, apply a move,
    write it back) is not wrapped in a transaction, so it's only safe
    under a single writer -- which is what this project's README has
    you run (`gunicorn app:app`, no -w flag: one worker, requests
    handled one at a time). With more than one worker or thread able to
    process requests concurrently, two replies to the same game
    arriving close together could race and one could silently clobber
    the other's update. Fixing that properly would need each save to
    check the row hasn't changed since it was loaded (e.g. a version
    column) and retry if it has. Not implemented, since it's real
    complexity for a scenario a single-worker deployment doesn't have.

    Journal mode is deliberately kept at SQLite's default (DELETE), not
    WAL -- WAL was tried briefly as a cheap concurrency improvement but
    caused 'disk I/O error' on every query on at least one real hosting
    disk backend (Render's persistent disk, in practice). Not every
    filesystem supports the shared-memory/locking behavior WAL needs.
    The __init__ below actively forces the database back to DELETE mode
    rather than just not re-requesting WAL, since journal mode is a
    persistent property of the database file itself -- a database that
    already got flipped to WAL by a previous version of this code would
    otherwise stay in that broken state indefinitely.
    """

    def __init__(self, path="backgammon.db"):
        self.path = path
        with closing(sqlite3.connect(self.path)) as con:
            try:
                con.execute("PRAGMA journal_mode=DELETE")
            except sqlite3.OperationalError:
                pass
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
            try:
                con.execute(UNIQUE_PAIR_LABEL_INDEX_SQL)
                con.commit()
            except sqlite3.Error as e:
                print(f"[state.py] could not add unique (white_email, black_email, label) index "
                      f"-- likely pre-existing duplicate rows from before this existed; "
                      f"check /admin/games to find and remove them: {e}")

    def create_game(self, label, white_email, black_email, white_name="White", black_name="Black"):
        # normalize case at the point of storage -- inbound sender
        # addresses always arrive lowercased already, so a game created
        # with different casing (e.g. typed by hand via curl) would
        # otherwise silently never match anything that player sends in
        white_email = white_email.strip().lower()
        black_email = black_email.strip().lower()
        game = Game.new()
        now = time.time()
        state_json = json.dumps(game.to_dict())
        attempt_label = label
        suffix = 0
        while True:
            try:
                with closing(sqlite3.connect(self.path)) as con:
                    cur = con.execute(
                        "INSERT INTO games (label, white_email, black_email, white_name, black_name, "
                        "state_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                        (attempt_label, white_email, black_email, white_name, black_name,
                         state_json, now, now),
                    )
                    con.commit()
                    return cur.lastrowid
            except sqlite3.IntegrityError:
                # Extremely rare: two near-simultaneous requests (e.g. a
                # genuine race between two background rematch/new-game
                # creations) both tried to create the same label for the
                # same pair. The unique index catches it at the database
                # level -- retry with a distinguishing suffix rather than
                # end up with two rows silently sharing one label, which
                # would confuse every lookup keyed on that label,
                # including the reminder de-dup logic.
                suffix += 1
                attempt_label = f"{label}-{suffix}"

    def list_all_labels(self):
        """Every (id, label) pair in the database, across every player --
        for admin/diagnostic use (e.g. spotting an accidental duplicate
        label from a race), not anything player-facing."""
        with closing(sqlite3.connect(self.path)) as con:
            return con.execute("SELECT id, label FROM games").fetchall()

    def delete_game(self, game_id):
        """Permanently remove one game by id. Returns True if a row was
        actually deleted, False if no such game existed."""
        with closing(sqlite3.connect(self.path)) as con:
            cur = con.execute("DELETE FROM games WHERE id=?", (game_id,))
            con.commit()
            return cur.rowcount > 0

    def load(self, game_id):
        with closing(sqlite3.connect(self.path)) as con:
            row = con.execute(
                "SELECT id, label, white_email, black_email, white_name, black_name, state_json "
                "FROM games WHERE id=?", (game_id,)
            ).fetchone()
        if row is None:
            return None
        gid, label, we, be, wn, bn, state_json = row
        try:
            game = Game.from_dict(json.loads(state_json))
        except Exception as e:
            # A single corrupted/incompatible game record shouldn't take
            # down every lookup for every player who happens to share an
            # email with it -- find_game_for_player and friends load
            # every candidate game for an address in one pass, so one
            # bad record here used to be able to 500 every email from
            # that person, not just requests touching this one game.
            # Treat it as missing (and log it) rather than crash.
            print(f"[state.py] failed to load game {game_id} ({label}): {e}")
            return None
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

    def find_unique_live_game(self, email):
        """The player's one and only in-progress game, if they have
        exactly one -- regardless of any other finished games under the
        same email. Used as a fallback when an explicit label points at
        a finished game (or no game at all), but the sender clearly only
        has one active game -- e.g. replying to a stale email thread
        whose subject still carries an old, now-finished label."""
        with closing(sqlite3.connect(self.path)) as con:
            rows = con.execute(
                "SELECT id FROM games WHERE white_email=? OR black_email=?", (email, email)
            ).fetchall()
        loaded = [self.load(r[0]) for r in rows]
        in_progress = [r for r in loaded if r is not None and not r["game"].is_over()]
        return in_progress[0] if len(in_progress) == 1 else None

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
