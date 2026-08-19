"""
Move notation parser. Three interchangeable styles are accepted -- mix and
match freely, even within the same turn:

    24/21 13/8          slash style, space-separated (default)
    24-21,13-8           PBeM style: hyphen instead of slash, comma between moves
    2x24                 shorthand: move 2 checkers off point 24, one per
                          available die (needs the current dice roll to
                          expand -- see the `dice` argument below)

Other accepted forms:
    13/11/8   or   13-11-8      one checker run with two dice chained together
    bar/19    or   bar-19        entering from the bar
    6/off     or   6-off  6/o    bearing off
    24/18*                       trailing '*' marking a hit is accepted but
                                  optional/cosmetic

Everything is case-insensitive. parse_moves() returns a flat list of
(src, dest) hops where src is 'bar' or an int 1-24, and dest is 'off' or
an int 1-24.
"""

import re

from board import dice_multiset


class NotationError(ValueError):
    pass


_SHORTHAND_RE = re.compile(r"^(\d+)[xX](\d{1,2}|bar)$")


def parse_moves(text, dice=None):
    """dice: the player's current (a, b) roll, only needed if the text uses
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
            hops.extend(_expand_shorthand(m, dice, tok))
            continue

        if "/" in tok:
            parts = tok.split("/")
        elif "-" in tok:
            parts = tok.split("-")
        else:
            raise NotationError(
                f"'{tok}' doesn't look like a move (expected e.g. 13/11, 13-11, or 2x13)"
            )
        if len(parts) < 2:
            raise NotationError(f"'{tok}' doesn't look like a move (expected e.g. 13/11)")
        parsed = [_parse_point(p) for p in parts]
        for a, b in zip(parsed, parsed[1:]):
            hops.append((a, b))
    if not hops:
        raise NotationError("no moves found")
    return hops


def _expand_shorthand(m, dice, tok):
    n = int(m.group(1))
    src = _parse_point(m.group(2))
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
    if p == "bar":
        return "bar"
    if p in ("off", "o"):
        return "off"
    if not re.fullmatch(r"\d{1,2}", p):
        raise NotationError(f"'{p}' isn't a valid point")
    n = int(p)
    if not (1 <= n <= 24):
        raise NotationError(f"point {n} is out of range (1-24)")
    return n


def format_hop(src, dest):
    s = "bar" if src == "bar" else str(src)
    d = "off" if dest == "off" else str(dest)
    return f"{s}/{d}"
