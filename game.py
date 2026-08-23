"""
Ties board + dice + notation together into turn-by-turn gameplay,
mirroring how the old PBM server worked:

    1. Player on roll has a dice roll already sitting in front of them.
    2. They email in a move (subject line notation).
    3. Server validates & applies it.
    4. Server rolls dice for the OTHER player and sends both players the
       new board + that roll, plus any message text.

DOUBLING CUBE / MANUAL MODE
----------------------------
By default (auto_dice=True) dice are rolled automatically the instant it
becomes a player's turn, same as above. Sending the command "manual"
switches the game to manual dice mode: instead of dice appearing
automatically, the player on roll is asked to reply with either:

    roll      -- roll the dice as normal
    double    -- offer to double the cube (only legal before rolling,
                 and only if you own the cube or it's centered)

If a double is offered, the *other* player is asked to reply with either:

    take / accept   -- cube value doubles, they now own the cube, and
                        the doubler rolls and continues their turn
    drop / pass     -- they concede the game at the current cube value

Sending "auto" switches back to automatic rolling at any point (and, if
someone was mid-decision about whether to roll or double, rolls for them
immediately).

FORCED MOVES
------------
After any dice roll, if there's exactly one legal way to play it (down to
a unique resulting position, however the dice are ordered) -- or no legal
way to play it at all -- the engine plays it automatically and moves on,
rather than making you type out a move you had no real choice about.

GREEDY
------
Sending "greedy" instead of a move plays the current dice for you, always
moving (or bearing off) the most-advanced checker with each die. It's
meant for pure bear-off races once contact is impossible -- it applies no
judgment about safety, so don't reach for it while there's still a blot
either of you could hit.

Known simplification: we validate that each submitted move is legal, but
we do NOT enforce the official "you must use both dice if it's possible
to do so" maximal-play rule beyond what's needed for forced-move detection
above (that would require searching every submitted move against the full
search too). For two friends playing casually this is normally a non-issue.
"""

from dataclasses import dataclass, field
from typing import Optional

from board import (
    Board, WHITE, BLACK, other, is_legal_single, dice_multiset,
    abs_to_rel, rel_to_abs,
)
from dice import roll_dice
from notation import parse_moves, NotationError, format_hop

# awaiting states
AWAIT_MOVE = "move"                    # dice are rolled, waiting for a move
AWAIT_ROLL_OR_DOUBLE = "roll_or_double"  # manual mode, waiting for to_move to roll or double
AWAIT_DOUBLE_RESPONSE = "double_response"  # waiting for the non-doubler to take/drop

MAX_CUBE = 64


class IllegalMove(ValueError):
    pass


class CommandError(ValueError):
    pass


@dataclass
class TurnRecord:
    player: str
    dice: tuple
    move_text: str
    message: str = ""
    hits: list = field(default_factory=list)  # points where an opponent blot was hit


@dataclass
class CubeEvent:
    kind: str  # "offered" | "taken" | "dropped"
    player: str
    value: int
    message: str = ""


@dataclass
class Game:
    board: Board
    to_move: str
    dice: tuple
    history: list = field(default_factory=list)
    winner: Optional[str] = None

    cube_value: int = 1
    cube_owner: Optional[str] = None   # None = centered, else only this player may double next
    auto_dice: bool = True
    awaiting: str = AWAIT_MOVE
    pending_doubler: Optional[str] = None
    pending_cube_value: Optional[int] = None
    win_reason: Optional[str] = None   # "normal" | "resignation" | "drop"

    # transient -- not persisted; whatever the most recent process_input()
    # call auto-played after the primary action (forced moves/dances)
    last_auto_played: list = field(default_factory=list)

    @staticmethod
    def new():
        starter, dice = _opening_roll()
        g = Game(board=Board.initial(), to_move=starter, dice=dice, awaiting=AWAIT_MOVE)
        g.last_auto_played = g.auto_resolve()
        return g

    def is_over(self):
        return self.winner is not None

    # ---------- serialization ----------

    def to_dict(self):
        return {
            "board": self.board.to_dict(),
            "to_move": self.to_move,
            "dice": list(self.dice),
            "winner": self.winner,
            "win_reason": self.win_reason,
            "cube_value": self.cube_value,
            "cube_owner": self.cube_owner,
            "auto_dice": self.auto_dice,
            "awaiting": self.awaiting,
            "pending_doubler": self.pending_doubler,
            "pending_cube_value": self.pending_cube_value,
            "history": [
                {"player": h.player, "dice": list(h.dice), "move_text": h.move_text,
                 "message": h.message, "hits": list(h.hits)}
                for h in self.history
            ],
        }

    @staticmethod
    def from_dict(d):
        g = Game(
            board=Board.from_dict(d["board"]),
            to_move=d["to_move"],
            dice=tuple(d["dice"]),
            winner=d.get("winner"),
            win_reason=d.get("win_reason"),
            cube_value=d.get("cube_value", 1),
            cube_owner=d.get("cube_owner"),
            auto_dice=d.get("auto_dice", True),
            awaiting=d.get("awaiting", AWAIT_MOVE),
            pending_doubler=d.get("pending_doubler"),
            pending_cube_value=d.get("pending_cube_value"),
        )
        g.history = [
            TurnRecord(player=h["player"], dice=tuple(h["dice"]), move_text=h["move_text"],
                       message=h.get("message", ""), hits=h.get("hits", []))
            for h in d.get("history", [])
        ]
        return g

    # ---------- input dispatch ----------

    def process_input(self, sender_player, text, message=""):
        """Main entry point: figure out whether `text` is a command
        (manual/auto/roll/double/take/drop/resign/greedy) or move notation,
        and dispatch accordingly. Returns a result object:
            TurnRecord    -- a move was applied
            CubeEvent     -- a cube-related action happened
            str           -- an informational note (e.g. mode switched)
        Raises IllegalMove or CommandError on problems; nothing is changed
        in that case.

        After a successful action, any forced moves/dances that followed
        are auto-played and left in self.last_auto_played for the caller
        to report if it wants to.
        """
        cmd = text.strip().lower().rstrip(".!")
        if cmd in ("manual",):
            result = self._cmd_manual(sender_player)
        elif cmd in ("auto", "automatic"):
            result = self._cmd_auto(sender_player)
        elif cmd == "roll":
            result = self._cmd_roll(sender_player)
        elif cmd == "double":
            result = self._cmd_double(sender_player, message)
        elif cmd in ("take", "accept"):
            result = self._cmd_take(sender_player, message)
        elif cmd in ("drop", "pass"):
            result = self._cmd_drop(sender_player, message)
        elif cmd == "resign":
            result = self._cmd_resign(sender_player, message)
        elif cmd == "greedy":
            result = self._cmd_greedy(sender_player, message)
        else:
            result = self.apply_turn(text, message, player=sender_player)

        self.last_auto_played = self.auto_resolve()
        return result

    # ---------- moves ----------

    def apply_turn(self, move_text, message="", player=None):
        """Validate + apply a full turn of move notation for self.to_move.
        Raises IllegalMove on any problem (nothing is applied if it fails).
        """
        if self.is_over():
            raise IllegalMove("the game is already over")
        if player is not None and player != self.to_move:
            raise IllegalMove("it isn't your turn")
        if self.awaiting == AWAIT_ROLL_OR_DOUBLE:
            raise IllegalMove("you need to 'roll' or 'double' before playing a move")
        if self.awaiting == AWAIT_DOUBLE_RESPONSE:
            raise IllegalMove("there's a double pending -- reply 'take' or 'drop' first")

        mover = self.to_move
        try:
            hops = parse_moves(move_text, mover, dice=self.dice)
        except NotationError as e:
            raise IllegalMove(str(e))

        remaining = dice_multiset(self.dice)
        work = self.board.clone()
        hits = []

        for src, dest in hops:
            if dest == "auto":
                dest = _resolve_auto_dest(work, mover, src, remaining)

            die = _infer_die(work, mover, src, dest, remaining)
            if die is None:
                raise IllegalMove(
                    f"'{format_hop(mover, src, dest)}' doesn't match any of your remaining dice {remaining}"
                )
            ok, reason = is_legal_single(work, mover, src, dest, die)
            if not ok:
                raise IllegalMove(f"'{format_hop(mover, src, dest)}': {reason}")

            if dest != "off":
                abs_dest = rel_to_abs(mover, dest)
                owner = work.owner_at_abs(abs_dest)
                if owner is not None and owner != mover and abs(work.count_at_abs(abs_dest)) == 1:
                    hits.append(dest)

            work.apply_single(mover, src, dest, die)
            remaining.remove(die)

        self.board = work
        record = TurnRecord(player=mover, dice=self.dice, move_text=move_text,
                             message=message, hits=hits)
        self.history.append(record)

        if self.board.borne_off_all(mover):
            self.winner = mover
            self.win_reason = "normal"
            self.dice = ()
            self.awaiting = AWAIT_MOVE
        else:
            self._start_turn(other(mover))

        return record

    # ---------- cube / dice-mode commands ----------

    def _cmd_manual(self, player):
        if self.is_over():
            raise CommandError("the game is already over")
        if not self.auto_dice:
            return "Already in manual dice mode."
        self.auto_dice = False
        return ("Switched to manual dice mode. On your turn, reply 'roll' to roll, "
                "or 'double' to offer a double.")

    def _cmd_auto(self, player):
        if self.is_over():
            raise CommandError("the game is already over")
        if self.auto_dice:
            return "Already in automatic dice mode."
        self.auto_dice = True
        note = "Switched to automatic dice mode."
        if self.awaiting == AWAIT_ROLL_OR_DOUBLE:
            self.dice = roll_dice()
            self.awaiting = AWAIT_MOVE
            note += f" Rolled {self.dice[0]}-{self.dice[1]} for the player on roll."
        return note

    def _cmd_roll(self, player):
        if self.is_over():
            raise CommandError("the game is already over")
        if player != self.to_move:
            raise CommandError("it isn't your turn")
        if self.awaiting != AWAIT_ROLL_OR_DOUBLE:
            raise CommandError("there's nothing to roll for right now")
        self.dice = roll_dice()
        self.awaiting = AWAIT_MOVE
        return f"Rolled {self.dice[0]}-{self.dice[1]}."

    def _cmd_double(self, player, message):
        if self.is_over():
            raise CommandError("the game is already over")
        if player != self.to_move:
            raise CommandError("only the player on roll can double")
        if self.awaiting != AWAIT_ROLL_OR_DOUBLE:
            raise CommandError(
                "you can only double before rolling -- switch to manual mode first if needed"
            )
        if self.cube_owner is not None and self.cube_owner != player:
            raise CommandError("you don't own the cube, so you can't double right now")
        if self.cube_value >= MAX_CUBE:
            raise CommandError(f"the cube is already at its max ({MAX_CUBE})")

        self.pending_doubler = player
        self.pending_cube_value = self.cube_value * 2
        self.awaiting = AWAIT_DOUBLE_RESPONSE
        return CubeEvent(kind="offered", player=player, value=self.pending_cube_value, message=message)

    def _cmd_take(self, player, message):
        if self.is_over():
            raise CommandError("the game is already over")
        if self.awaiting != AWAIT_DOUBLE_RESPONSE or player != other(self.pending_doubler):
            raise CommandError("there's no double waiting for your response")

        self.cube_value = self.pending_cube_value
        self.cube_owner = player
        doubler = self.pending_doubler
        self.pending_doubler = None
        self.pending_cube_value = None

        # doubler still had the roll -- give it to them now, decision's resolved
        self.to_move = doubler
        self.dice = roll_dice()
        self.awaiting = AWAIT_MOVE
        return CubeEvent(kind="taken", player=player, value=self.cube_value, message=message)

    def _cmd_drop(self, player, message):
        if self.is_over():
            raise CommandError("the game is already over")
        if self.awaiting != AWAIT_DOUBLE_RESPONSE or player != other(self.pending_doubler):
            raise CommandError("there's no double waiting for your response")

        winner = self.pending_doubler
        value = self.cube_value  # game ends at the value BEFORE the declined double
        self.winner = winner
        self.win_reason = "drop"
        self.pending_doubler = None
        self.pending_cube_value = None
        self.dice = ()
        return CubeEvent(kind="dropped", player=player, value=value, message=message)

    def _cmd_resign(self, player, message):
        if self.is_over():
            raise CommandError("the game is already over")
        self.winner = other(player)
        self.win_reason = "resignation"
        self.dice = ()
        return CubeEvent(kind="resigned", player=player, value=self.cube_value, message=message)

    def _cmd_greedy(self, player, message):
        if self.is_over():
            raise CommandError("the game is already over")
        if player != self.to_move:
            raise IllegalMove("it isn't your turn")
        if self.awaiting == AWAIT_ROLL_OR_DOUBLE:
            raise IllegalMove("you need to 'roll' or 'double' before playing a move")
        if self.awaiting == AWAIT_DOUBLE_RESPONSE:
            raise IllegalMove("there's a double pending -- reply 'take' or 'drop' first")

        hops = _greedy_hops(self.board, player, self.dice)
        if not hops:
            raise IllegalMove("no legal moves available")
        return self._commit_hops(player, hops, message or "(greedy)")

    # ---------- forced-move automation ----------

    def auto_resolve(self):
        """Repeatedly check whether the player now on roll has a forced
        move (or no legal move at all) and play it automatically, looping
        in case that chains into the next player also being forced. Stops
        as soon as a turn requires an actual decision, or the game ends.
        Returns the list of TurnRecords that were auto-played.
        """
        auto = []
        while not self.is_over() and self.awaiting == AWAIT_MOVE:
            forced, hops = _is_forced(self.board, self.to_move, self.dice)
            if not forced:
                break
            note = "(forced)" if hops else "(no legal move)"
            auto.append(self._commit_hops(self.to_move, hops, note))
        return auto

    def _commit_hops(self, player, hops, message):
        """Apply an already-validated (src, dest, die) hop list directly,
        bypassing notation parsing -- used by forced-move automation and
        the 'greedy' command, which both compute hops programmatically."""
        work = self.board.clone()
        hits = []
        display = []
        for src, dest, die in hops:
            if dest != "off":
                abs_dest = rel_to_abs(player, dest)
                owner = work.owner_at_abs(abs_dest)
                if owner is not None and owner != player and abs(work.count_at_abs(abs_dest)) == 1:
                    hits.append(dest)
            work.apply_single(player, src, dest, die)
            display.append(format_hop(player, src, dest))

        self.board = work
        move_text = " ".join(display) if display else "(no legal move)"
        record = TurnRecord(player=player, dice=self.dice, move_text=move_text,
                             message=message, hits=hits)
        self.history.append(record)

        if self.board.borne_off_all(player):
            self.winner = player
            self.win_reason = "normal"
            self.dice = ()
            self.awaiting = AWAIT_MOVE
        else:
            self._start_turn(other(player))

        return record

    # ---------- internal ----------

    def _start_turn(self, player):
        self.to_move = player
        if self.auto_dice:
            self.dice = roll_dice()
            self.awaiting = AWAIT_MOVE
        else:
            self.dice = ()
            self.awaiting = AWAIT_ROLL_OR_DOUBLE

    def result_summary(self):
        """Only meaningful once is_over(). Returns a dict describing the
        finish: winner/loser colors, 'kind' (normal/gammon/backgammon),
        the multiplier that implies, and the resulting points (cube value
        times that multiplier)."""
        if not self.is_over():
            return None
        winner = self.winner
        loser = other(winner)
        if self.win_reason == "normal" and self.board.off[loser] == 0:
            if _is_backgammon(self.board, winner, loser):
                kind, mult = "backgammon", 3
            else:
                kind, mult = "gammon", 2
        else:
            kind, mult = "normal", 1
        return {
            "winner": winner, "loser": loser, "kind": kind, "multiplier": mult,
            "cube_value": self.cube_value, "points": self.cube_value * mult,
        }

    def status_text(self, white_name="White", black_name="Black"):
        """Short human-readable line describing what's currently needed."""
        name = white_name if self.to_move == WHITE else black_name
        if self.is_over():
            winner_name = white_name if self.winner == WHITE else black_name
            summary = self.result_summary()
            kind_suffix = {"normal": "", "gammon": " (gammon)", "backgammon": " (backgammon)"}[summary["kind"]]
            return f"{winner_name} wins {summary['points']} point(s){kind_suffix}!"
        if self.awaiting == AWAIT_DOUBLE_RESPONSE:
            doubler_name = white_name if self.pending_doubler == WHITE else black_name
            other_name = black_name if self.pending_doubler == WHITE else white_name
            return (f"{doubler_name} offers to double to {self.pending_cube_value}. "
                    f"{other_name}: reply 'take' or 'drop'.")
        if self.awaiting == AWAIT_ROLL_OR_DOUBLE:
            return f"{name}'s turn: reply 'roll' or 'double'."
        return f"{name} to play {self.dice[0]}-{self.dice[1]}."


def _abs_str(player, point):
    """A single point rendered in the absolute (as-printed) terms a player
    would recognize, for error messages."""
    return "bar" if point == "bar" else str(rel_to_abs(player, point))


def _resolve_auto_dest(board, player, src, remaining_dice):
    """Given a bare 'move whatever's on this point' token (no destination
    specified), work out the single legal destination using the dice
    still available this turn. Raises IllegalMove if there's no legal
    move from there, or more than one distinct destination possible."""
    if board.bar[player] > 0 and src != "bar":
        raise IllegalMove("you have a checker on the bar and must enter it first")

    dest_to_dice = {}
    for die in set(remaining_dice):
        if src == "bar":
            dest = 25 - die
        else:
            dest = src - die
            dest = "off" if dest <= 0 else dest
        ok, _ = is_legal_single(board, player, src, dest, die)
        if ok:
            dest_to_dice.setdefault(dest, []).append(die)

    if not dest_to_dice:
        raise IllegalMove(
            f"no legal move from {_abs_str(player, src)} with your remaining dice {remaining_dice}"
        )
    if len(dest_to_dice) > 1:
        options = ", ".join(_abs_str(player, d) for d in dest_to_dice)
        raise IllegalMove(
            f"more than one way to move from {_abs_str(player, src)} ({options}) "
            f"-- please specify the destination"
        )
    return next(iter(dest_to_dice))


def _infer_die(board, player, src, dest, remaining):
    """Work out which of the remaining dice values this hop is spending.
    Returns the die value, or None if no remaining die can produce this hop.
    """
    if src == "bar":
        die = 25 - dest if isinstance(dest, int) else None
        return die if die in remaining else None

    if dest == "off":
        exact = src
        if exact in remaining:
            return exact
        highest = board.highest_rel_point_occupied(player)
        if highest is not None and src == highest:
            candidates = sorted(d for d in remaining if d >= src)
            return candidates[0] if candidates else None
        return None

    if not isinstance(dest, int):
        return None

    die = src - dest
    if die in remaining:
        return die
    return None


def _opening_roll():
    """Traditional opening: each side rolls one die, higher goes first and
    plays both dice as the opening roll. Re-rolls on a tie."""
    while True:
        w, b = roll_dice()[0], roll_dice()[0]
        if w != b:
            starter = WHITE if w > b else BLACK
            return starter, (w, b)


# ---------- scoring ----------

def _is_backgammon(board, winner, loser):
    """True if the loser still has a checker on the bar, or in the
    WINNER's home board (the classic triple-value finish)."""
    if board.bar[loser] > 0:
        return True
    home_range = range(1, 7) if winner == WHITE else range(19, 25)
    for pt in home_range:
        if board.owner_at_abs(pt) == loser:
            return True
    return False


# ---------- move-sequence search (forced-move detection + greedy) ----------

def _legal_hops_for_die(board, player, die):
    """All legal single hops for one die value from the current position.
    If the player has a checker on the bar, only bar-entry hops are
    returned (entering is mandatory before anything else)."""
    hops = []
    if board.bar[player] > 0:
        dest = 25 - die
        ok, _ = is_legal_single(board, player, "bar", dest, die)
        if ok:
            hops.append(("bar", dest))
        return hops

    for pt in board.checkers_of(player):
        rel = abs_to_rel(player, pt)
        dest = rel - die
        dd = "off" if dest <= 0 else dest
        ok, _ = is_legal_single(board, player, rel, dd, die)
        if ok:
            hops.append((rel, dd))
    return hops


def _board_key(board):
    return (tuple(board.points), board.bar[WHITE], board.bar[BLACK],
            board.off[WHITE], board.off[BLACK])


def _enumerate_full_turns(board, player, dice):
    """All maximal-length legal ways to play this dice roll, as a list of
    (hops, resulting_board) pairs, where hops is a list of (src,dest,die).
    'Maximal' means: among every legal sequence, only the longest ones
    (using the most dice) are kept, matching the real rule that you must
    play as many dice as you can."""
    dice_list = dice_multiset(dice)
    results = []

    def recurse(work, remaining, hops_so_far):
        branched = False
        tried = set()
        for die in remaining:
            if die in tried:
                continue
            tried.add(die)
            for src, dest in _legal_hops_for_die(work, player, die):
                branched = True
                nb = work.clone()
                nb.apply_single(player, src, dest, die)
                nr = list(remaining)
                nr.remove(die)
                recurse(nb, nr, hops_so_far + [(src, dest, die)])
        if not branched:
            results.append((hops_so_far, work))

    recurse(board.clone(), dice_list, [])
    if not results:
        return []
    max_len = max(len(h) for h, _ in results)
    return [(h, b) for h, b in results if len(h) == max_len]


def _is_forced(board, player, dice):
    """Returns (True, hops) if there's exactly one distinct resulting
    position reachable this turn (however the dice are ordered) -- or a
    forced dance with hops=[] if there's no legal move at all. Returns
    (False, None) if there's a genuine choice."""
    maximal = _enumerate_full_turns(board, player, dice)
    if not maximal:
        return True, []
    distinct = {}
    for hops, b in maximal:
        distinct[_board_key(b)] = hops
    if len(distinct) == 1:
        return True, next(iter(distinct.values()))
    return False, None


def _greedy_hops(board, player, dice):
    """For each die (largest first), move the most-advanced legal checker
    -- i.e. always prefer bearing off, then the highest-numbered point.
    Meant for pure races; makes no attempt at safety."""
    dice_list = sorted(dice_multiset(dice), reverse=True)
    hops = []
    work = board.clone()
    for die in dice_list:
        candidates = _legal_hops_for_die(work, player, die)
        if not candidates:
            continue
        if candidates[0][0] == "bar":
            src, dest = candidates[0]
        else:
            src, dest = max(candidates, key=lambda c: c[0])
        hops.append((src, dest, die))
        work.apply_single(player, src, dest, die)
    return hops
