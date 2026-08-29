"""
Core backgammon board representation and rules engine.

Numbering convention
---------------------
Points are stored ABSOLUTELY as 1..24:
  - White's home board is absolute points 1-6, White moves 24 -> 1.
  - Black's home board is absolute points 19-24, Black moves 1 -> 24.
  - board.points[i] holds the checker count on absolute point (i+1).
    Positive = White checkers, Negative = Black checkers, 0 = empty.

For NOTATION (what players type), point numbers always mean exactly what's
printed on the board image -- the same absolute 1-24 numbering for both
colors, with no "count from your own side" conversion. This is a deliberate
departure from traditional backgammon notation (where each player counts
from their own 24), made after that traditional convention proved
confusing for one player in practice. rel_to_abs / abs_to_rel below
convert between this absolute numbering and each player's own relative
frame, which is what the rules engine itself works in internally.
"""

from dataclasses import dataclass, field

WHITE = "W"
BLACK = "B"


def other(player):
    return BLACK if player == WHITE else WHITE


def rel_to_abs(player, rel):
    """Convert a player's own 1-24 point numbering to the absolute board numbering."""
    if player == WHITE:
        return rel
    return 25 - rel


def abs_to_rel(player, abs_pt):
    """Convert an absolute 1-24 point to a player's own point numbering."""
    if player == WHITE:
        return abs_pt
    return 25 - abs_pt


@dataclass
class Board:
    points: list = field(default_factory=lambda: [0] * 24)
    bar: dict = field(default_factory=lambda: {WHITE: 0, BLACK: 0})
    off: dict = field(default_factory=lambda: {WHITE: 0, BLACK: 0})

    @staticmethod
    def initial():
        b = Board()
        b.points[24 - 1] = 2   # White: 24-point
        b.points[13 - 1] = 5   # White: 13-point
        b.points[8 - 1] = 3    # White: 8-point
        b.points[6 - 1] = 5    # White: 6-point
        b.points[1 - 1] = -2   # Black: 1-point
        b.points[12 - 1] = -5  # Black: 12-point
        b.points[17 - 1] = -3  # Black: 17-point
        b.points[19 - 1] = -5  # Black: 19-point
        return b

    def clone(self):
        b = Board(points=list(self.points), bar=dict(self.bar), off=dict(self.off))
        return b

    def count_at_abs(self, abs_pt):
        """Signed count at an absolute point (positive=White, negative=Black)."""
        return self.points[abs_pt - 1]

    def owner_at_abs(self, abs_pt):
        c = self.count_at_abs(abs_pt)
        if c > 0:
            return WHITE
        if c < 0:
            return BLACK
        return None

    def checkers_of(self, player):
        """Dict: absolute point -> count of this player's checkers there (bar/off excluded)."""
        out = {}
        for i, c in enumerate(self.points):
            pt = i + 1
            if player == WHITE and c > 0:
                out[pt] = c
            elif player == BLACK and c < 0:
                out[pt] = -c
        return out

    def highest_rel_point_occupied(self, player):
        """Highest (furthest from home) relative point this player still occupies,
        or None if they have none outside home/bar. Used for bear-off overage rule."""
        best = None
        for abs_pt, cnt in self.checkers_of(player).items():
            rel = abs_to_rel(player, abs_pt)
            if best is None or rel > best:
                best = rel
        return best

    def all_home(self, player):
        """True if player has no checkers on the bar and none outside their home board (rel 1-6)."""
        if self.bar[player] > 0:
            return False
        for abs_pt in self.checkers_of(player):
            if abs_to_rel(player, abs_pt) > 6:
                return False
        return True

    def borne_off_all(self, player):
        return self.off[player] == 15

    def pip_count(self, player):
        """Total pips this player's checkers need to travel to bear off
        completely -- the standard backgammon race metric. A checker on
        the bar counts as needing the full 25 pips."""
        total = self.bar[player] * 25
        for abs_pt, cnt in self.checkers_of(player).items():
            total += abs_to_rel(player, abs_pt) * cnt
        return total

    def entry_blocked(self, player, die):
        """Is the entry point for this die blocked by 2+ opposing checkers?"""
        rel_entry = 25 - die
        abs_entry = rel_to_abs(player, rel_entry)
        owner = self.owner_at_abs(abs_entry)
        if owner is None or owner == player:
            return False
        return abs(self.count_at_abs(abs_entry)) >= 2

    # -- mutation helpers (assume legality already checked) --

    def _remove_from_abs(self, player, abs_pt):
        if player == WHITE:
            self.points[abs_pt - 1] -= 1
        else:
            self.points[abs_pt - 1] += 1

    def _add_to_abs(self, player, abs_pt):
        owner = self.owner_at_abs(abs_pt)
        if owner is not None and owner != player and abs(self.count_at_abs(abs_pt)) == 1:
            # hit a blot
            self.bar[other(player)] += 1
            self.points[abs_pt - 1] = 0
        if player == WHITE:
            self.points[abs_pt - 1] += 1
        else:
            self.points[abs_pt - 1] -= 1

    def apply_single(self, player, src, dest, die):
        """Apply one already-validated single-die move.
        src: 'bar' or relative point int
        dest: 'off' or relative point int
        """
        if src == "bar":
            self.bar[player] -= 1
        else:
            self._remove_from_abs(player, rel_to_abs(player, src))

        if dest == "off":
            self.off[player] += 1
        else:
            self._add_to_abs(player, rel_to_abs(player, dest))

    def to_dict(self):
        return {"points": list(self.points), "bar": dict(self.bar), "off": dict(self.off)}

    @staticmethod
    def from_dict(d):
        return Board(points=list(d["points"]), bar=dict(d["bar"]), off=dict(d["off"]))

    def ascii(self):
        """Quick debug rendering, not the pretty one."""
        lines = []
        lines.append("Bar: W=%d B=%d   Off: W=%d B=%d" %
                      (self.bar[WHITE], self.bar[BLACK], self.off[WHITE], self.off[BLACK]))
        top = " ".join("%3d" % p for p in range(13, 25))
        bot = " ".join("%3d" % p for p in range(12, 0, -1))
        toprow = " ".join("%3d" % self.points[p - 1] for p in range(13, 25))
        botrow = " ".join("%3d" % self.points[p - 1] for p in range(12, 0, -1))
        lines.append(top)
        lines.append(toprow)
        lines.append(bot)
        lines.append(botrow)
        return "\n".join(lines)


def is_legal_single(board, player, src, dest, die):
    """Check whether one (src, dest) hop is legal for the given die value.
    src: 'bar' or int 1-24 (relative to player)
    dest: 'off' or int 1-24 (relative to player)
    Returns (True, None) or (False, "reason")
    """
    # Must enter from bar first if any checkers are on the bar.
    if board.bar[player] > 0 and src != "bar":
        return False, "you have a checker on the bar and must enter it first"

    if src == "bar":
        expected_dest = 25 - die
        if dest != expected_dest:
            return False, f"a bar entry with a {die} must land on point {expected_dest}"
        if board.bar[player] <= 0:
            return False, "no checkers on the bar to enter"
        abs_pt = rel_to_abs(player, dest)
        owner = board.owner_at_abs(abs_pt)
        if owner is not None and owner != player and abs(board.count_at_abs(abs_pt)) >= 2:
            return False, f"point {dest} is blocked"
        return True, None

    # src is a normal point (relative)
    if not (1 <= src <= 24):
        return False, f"invalid source point {src}"
    abs_src = rel_to_abs(player, src)
    if board.owner_at_abs(abs_src) != player:
        return False, f"you have no checker on point {src}"

    if dest == "off":
        if not board.all_home(player):
            return False, "cannot bear off until all your checkers are in your home board"
        exact = src - die
        if exact == 0:
            return True, None
        if exact < 0:
            # overage bear-off only legal if src is the highest occupied point
            highest = board.highest_rel_point_occupied(player)
            if highest is not None and src == highest:
                return True, None
            return False, f"can't bear off from {src} with a {die} while you have checkers further out"
        return False, f"a {die} from point {src} doesn't reach off"

    # normal point-to-point (dest must be a real point, 1-24 -- 0 is never
    # valid here since that always means "off" and must arrive as that
    # sentinel; letting a bare 0 through would silently index points[-1],
    # i.e. point 24, via Python's negative-index wraparound)
    if not isinstance(dest, int) or not (1 <= dest <= 24):
        return False, f"invalid destination point {dest}"
    if src - die != dest:
        return False, f"point {src} to point {dest} is not a legal move with a {die}"
    abs_dest = rel_to_abs(player, dest)
    owner = board.owner_at_abs(abs_dest)
    if owner is not None and owner != player and abs(board.count_at_abs(abs_dest)) >= 2:
        return False, f"point {dest} is blocked"
    return True, None


def dice_multiset(dice):
    """Expand a rolled pair into the list of die-values available to play (4x for doubles)."""
    a, b = dice
    if a == b:
        return [a, a, a, a]
    return [a, b]
