"""
Move notation parser. Point numbers always mean exactly what's printed on
the board image -- the same absolute 1-24 numbering for both players.
There's no "count from your own side" convention to learn: whatever
number is next to a checker in the picture is the number you type,
regardless of whether you're playing White or Black.

Interchangeable styles -- mix and match freely, even within the same turn:

    24/21 13/8          slash style, space-separated (default)
    24-21,13-8           PBeM style: hyphen instead of slash, comma between moves
    2x24                 shorthand: move 2 checkers off point 24, one per
                          available die (needs the current dice roll to
                          expand -- see the `dice` argument below)
    22                   bare point: move whatever's on 22, inferring the
                          destination -- only works if exactly one of your
                          remaining dice gives a legal move from there;
                          raises an error if it's ambiguous or impossible.
                          'b' works the same way as a source, for the bar.
                          Chain several with spaces or commas, e.g. 'b,22'
                          for a forced bar entry followed by a forced move
                          of whatever's sitting on 22.

Other accepted forms:
    13/11/8   or   13-11-8      one checker run with two dice chained together
    bar/19    or   bar-19  b-19  entering from the bar ('b' is short for 'bar')
    6/off     or   6-off  6/o    bearing off
    24/18*                       trailing '*' marking a hit is accepted but
                                  optional/cosmetic

Everything is case-insensitive. parse_moves() needs to know which player
is moving (since the same absolute point means a different distance
travelled for each side) and returns a flat list of (src, dest) hops
already converted to that player's own relative frame -- 'bar' or an int
1-24 for src, 'off', 'auto', or an int 1-24 for dest -- which is what the
rest of the engine (board.py, game.py) works in internally. A dest of
'auto' means "figure this out from the dice and board", resolved later by
game.py once it has the live board state to check against. format_hop()
converts the other way, for displaying an internal relative hop back in
the absolute terms the player actually typed.
"""

import re

from board import dice_multiset, abs_to_rel, rel_to_abs


class NotationError(ValueError):
    pass


_SHORTHAND_RE = re.compile(r"^(\d+)[xX](\d{1,2}|bar|b)$")


def parse_moves(text, player, dice=None):
    """player: whose move this is (WHITE or BLACK) -- required, since the
    same absolute point number is a different distance for each side.
    dice: the player's current (a, b) roll, only needed if the text uses
    'NxPOINT' shorthand (which has no explicit destinations -- they're
    inferred from the dice). Pass None if you know the text won't use it;
    doing so will raise NotationError instead of silently guessing.
    """
    text = text.strip()
    if not text:
        raise NotationError("empty move")
    # commas are just an alternate separator between moves
    normalized = text.replace(",", " ")
    tokens = normalized.split()
    hops = []
    for tok in tokens:
        tok = tok.strip().rstrip("*")
        if not tok:
            continue

        m = _SHORTHAND_RE.match(tok)
        if m:
            hops.extend(_expand_shorthand(m, player, dice, tok))
            continue

        if "/" in tok:
            parts = tok.split("/")
        elif "-" in tok:
            parts = tok.split("-")
        else:
            # bare point (or 'b'/'bar'): "move whatever's here", destination
            # to be inferred later once the board state is available
            point = _parse_point(tok)
            if point == "off":
                raise NotationError("'off' isn't a valid starting point")
            hops.append((_to_relative(player, point), "auto"))
            continue

        if len(parts) < 2:
            raise NotationError(f"'{tok}' doesn't look like a move (expected e.g. 13/11)")
        parsed = [_to_relative(player, _parse_point(p)) for p in parts]
        for a, b in zip(parsed, parsed[1:]):
            hops.append((a, b))
    if not hops:
        raise NotationError("no moves found")
    return hops


def _expand_shorthand(m, player, dice, tok):
    n = int(m.group(1))
    src = _to_relative(player, _parse_point(m.group(2)))
    if dice is None:
        raise NotationError(
            f"'{tok}' is shorthand for using each die on a checker from that point, "
            f"but the current dice roll isn't available here"
        )
    dice_list = list(dice_multiset(dice))
    if n < 1 or n > len(dice_list):
        raise NotationError(f"'{tok}': only {len(dice_list)} dice available this turn")

    hops = []
    for die in dice_list[:n]:
        if src == "bar":
            dest = 25 - die
        else:
            dest = src - die
            if dest <= 0:
                dest = "off"
        hops.append((src, dest))
    return hops


def _parse_point(p):
    p = p.strip().lower()
    if p in ("bar", "b"):
        return "bar"
    if p in ("off", "o"):
        return "off"
    if not re.fullmatch(r"\d{1,2}", p):
        raise NotationError(f"'{p}' isn't a valid point")
    n = int(p)
    if not (1 <= n <= 24):
        raise NotationError(f"point {n} is out of range (1-24)")
    return n


def _to_relative(player, point):
    """Convert an absolute (as-printed-on-the-board) point into the given
    player's own relative frame, which is what board.py works in. 'bar'
    and 'off' pass through unchanged -- they're unambiguous either way."""
    if point in ("bar", "off"):
        return point
    return abs_to_rel(player, point)


def format_hop(player, src, dest):
    """The inverse of the conversion above: given an internal relative
    hop, render it back using the absolute point numbers the player
    would actually see printed on the board."""
    s = "bar" if src == "bar" else str(rel_to_abs(player, src))
    d = "off" if dest == "off" else str(rel_to_abs(player, dest))
    return f"{s}/{d}"
