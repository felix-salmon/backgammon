"""
Render a Board (+ some game context) to a PNG image, in the classic
"top row is 13-24, bottom row is 12-1, bar in the middle" layout.
"""

import re

from PIL import Image, ImageDraw, ImageFont

MARGIN = 30
BAR_W = 60
BOARD_W = 760
OFF_W = 80
W = MARGIN + BOARD_W + OFF_W + MARGIN
H = 700
POINT_W = (BOARD_W - BAR_W) / 12
TRI_H = 230
CHECKER_R = 18
DIE_SIZE = 46
DIE_GAP = 12

BG = (30, 26, 22)
BOARD_BG = (222, 184, 135)
POINT_DARK = (110, 60, 40)
POINT_LIGHT = (200, 160, 110)
BAR_COLOR = (70, 45, 30)
WHITE_CHECKER = (245, 240, 230)
BLACK_CHECKER = (35, 30, 28)
OUTLINE = (10, 10, 10)
TEXT = (235, 230, 220)
TEXT_DIM = (170, 160, 145)
POINT_LABEL = (255, 255, 255)
DIE_FACE = (250, 248, 244)
DIE_PIP = (25, 22, 20)

# Named color palettes. Each key matches one of the module-level color
# constants above; draw_board(theme=...) picks one of these (falling back
# to the module-level defaults, i.e. "classic", for any key it omits).
THEMES = {
    "classic": {
        "bg": (30, 26, 22), "board_bg": (222, 184, 135),
        "point_dark": (110, 60, 40), "point_light": (200, 160, 110),
        "bar": (70, 45, 30), "white_checker": (245, 240, 230),
        "black_checker": (35, 30, 28), "outline": (10, 10, 10),
        "text": (235, 230, 220), "text_dim": (170, 160, 145),
        "point_label": (255, 255, 255),
        "die_face": (250, 248, 244), "die_pip": (25, 22, 20),
    },
    "ocean": {
        "bg": (15, 30, 45), "board_bg": (235, 225, 205),
        "point_dark": (25, 95, 115), "point_light": (235, 190, 90),
        "bar": (20, 45, 60), "white_checker": (250, 248, 244),
        "black_checker": (22, 32, 42), "outline": (10, 10, 15),
        "text": (235, 240, 245), "text_dim": (140, 165, 182),
        "point_label": (255, 255, 255),
        "die_face": (250, 248, 244), "die_pip": (20, 20, 25),
    },
    "emerald": {
        "bg": (10, 35, 25), "board_bg": (230, 215, 180),
        "point_dark": (18, 92, 58), "point_light": (198, 168, 100),
        "bar": (15, 50, 35), "white_checker": (250, 248, 240),
        "black_checker": (28, 24, 20), "outline": (10, 10, 10),
        "text": (235, 235, 220), "text_dim": (150, 178, 160),
        "point_label": (255, 255, 255),
        "die_face": (250, 248, 240), "die_pip": (20, 18, 15),
    },
    "palm_springs": {
        "bg": (48, 140, 158), "board_bg": (250, 220, 190),
        "point_dark": (198, 68, 58), "point_light": (245, 172, 88),
        "bar": (48, 140, 158), "white_checker": (255, 250, 245),
        "black_checker": (40, 25, 35), "outline": (20, 10, 15),
        "text": (252, 238, 230), "text_dim": (215, 172, 182),
        "point_label": (255, 255, 255),
        "die_face": (255, 250, 245), "die_pip": (30, 20, 25),
    },
    "slate": {
        "bg": (34, 39, 47), "board_bg": (245, 245, 240),
        "point_dark": (66, 88, 108), "point_light": (232, 138, 58),
        "bar": (44, 49, 57), "white_checker": (255, 255, 255),
        "black_checker": (35, 38, 45), "outline": (15, 15, 18),
        "text": (240, 240, 238), "text_dim": (155, 162, 172),
        "point_label": (255, 255, 255),
        "die_face": (255, 255, 255), "die_pip": (25, 25, 28),
    },
}

_PIP_LAYOUT = {
    1: [(0, 0)],
    2: [(-1, -1), (1, 1)],
    3: [(-1, -1), (0, 0), (1, 1)],
    4: [(-1, -1), (1, -1), (-1, 1), (1, 1)],
    5: [(-1, -1), (1, -1), (0, 0), (-1, 1), (1, 1)],
    6: [(-1, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (1, 1)],
}

_TRAILING_DICE_RE = re.compile(r"\s*\d+-\d+\.$")


def _font(size, bold=False):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for c in candidates:
        try:
            return ImageFont.truetype(c, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _point_x(abs_pt):
    """x-center of the triangle base for absolute point 1..24. This is
    the same regardless of rotation -- only the row (top/bottom) a point
    renders in changes; its left-right position within that row doesn't."""
    if abs_pt >= 13:
        idx = abs_pt - 13  # 0..11, points 13..24 left to right
    else:
        idx = 12 - abs_pt  # 0..11, points 12..1 left to right
    half = idx // 6
    offset = idx % 6
    x = MARGIN + half * (6 * POINT_W + BAR_W) + offset * POINT_W + POINT_W / 2
    return x


def _draw_die(d, cx, cy, size, value, face_color, pip_color, outline_color):
    """One die face with pips, centered at (cx, cy)."""
    half = size / 2
    d.rounded_rectangle(
        [cx - half, cy - half, cx + half, cy + half],
        radius=size * 0.16, fill=face_color, outline=outline_color, width=2,
    )
    pip_r = size * 0.09
    offset = size * 0.26
    for ox, oy in _PIP_LAYOUT.get(value, []):
        px, py = cx + ox * offset, cy + oy * offset
        d.ellipse([px - pip_r, py - pip_r, px + pip_r, py + pip_r], fill=pip_color)


def _draw_off_stack(d, box_left, box_right, start_y, count, color, outline_color,
                     slot_h=11, bar_h=8, max_shown=15):
    """A stack of thin horizontal bars, one per borne-off checker -- like
    checkers lying flat in a real bear-off tray, viewed from above."""
    cx_left = box_left + 6
    cx_right = box_right - 6
    for i in range(min(count, max_shown)):
        y_top = start_y + i * slot_h
        d.rectangle([cx_left, y_top, cx_right, y_top + bar_h], fill=color, outline=outline_color, width=1)


def draw_board(board, to_move=None, dice=None,
               white_name="White", black_name="Black", turn_no=None,
               cube_value=1, cube_owner=None, status_text=None, theme="palm_springs"):
    palette = THEMES.get(theme, THEMES["classic"])
    bg = palette["bg"]
    board_bg = palette["board_bg"]
    point_dark = palette["point_dark"]
    point_light = palette["point_light"]
    bar_color = palette["bar"]
    white_checker = palette["white_checker"]
    black_checker = palette["black_checker"]
    outline = palette["outline"]
    text = palette["text"]
    text_dim = palette["text_dim"]
    point_label = palette["point_label"]
    die_face = palette["die_face"]
    die_pip = palette["die_pip"]

    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)

    # Whoever's on roll gets their own home board rendered bottom-right --
    # same numbering throughout (point 15 is always "15"), just flipped
    # which physical row each point lands in, so it reads the same way
    # for both colors instead of only being naturally oriented for White.
    rotate = (to_move == "B")

    board_top = 90
    board_bottom = board_top + 2 * TRI_H
    d.rectangle([MARGIN, board_top, MARGIN + BOARD_W, board_bottom], fill=board_bg, outline=outline, width=2)

    bar_x = MARGIN + 6 * POINT_W
    d.rectangle([bar_x, board_top, bar_x + BAR_W, board_bottom], fill=bar_color)

    # triangles
    f_label = _font(17, bold=True)
    for abs_pt in range(1, 25):
        top_row = (abs_pt >= 13) != rotate
        cx = _point_x(abs_pt)
        color = point_light if abs_pt % 2 == 0 else point_dark
        if top_row:
            apex = (cx, board_top + TRI_H)
            base_l = (cx - POINT_W / 2, board_top)
            base_r = (cx + POINT_W / 2, board_top)
        else:
            apex = (cx, board_bottom - TRI_H)
            base_l = (cx - POINT_W / 2, board_bottom)
            base_r = (cx + POINT_W / 2, board_bottom)
        d.polygon([base_l, base_r, apex], fill=color)

        # point number label -- same clearance from the board on both
        # sides, bright white and large enough to read at a glance
        label_y = board_top - 26 if top_row else board_bottom + 26
        d.text((cx, label_y), str(abs_pt), fill=point_label, font=f_label, anchor="mm")

    # checkers
    f_small = _font(14, bold=True)
    for abs_pt in range(1, 25):
        cnt = board.points[abs_pt - 1]
        if cnt == 0:
            continue
        top_row = (abs_pt >= 13) != rotate
        cx = _point_x(abs_pt)
        n = abs(cnt)
        color = white_checker if cnt > 0 else black_checker
        text_color = black_checker if cnt > 0 else white_checker
        max_stack = 5
        shown = min(n, max_stack)
        for i in range(shown):
            if top_row:
                cy = board_top + CHECKER_R + i * (CHECKER_R * 1.9)
            else:
                cy = board_bottom - CHECKER_R - i * (CHECKER_R * 1.9)
            d.ellipse([cx - CHECKER_R, cy - CHECKER_R, cx + CHECKER_R, cy + CHECKER_R],
                      fill=color, outline=outline, width=2)
            if i == shown - 1 and n > max_stack:
                d.text((cx, cy), f"+{n - max_stack + 1}", fill=text_color, font=f_small, anchor="mm")

    # bar checkers
    f_bar = _font(15, bold=True)
    if board.bar["W"] > 0:
        cy = board_top + TRI_H - 40
        d.ellipse([bar_x + BAR_W / 2 - CHECKER_R, cy - CHECKER_R,
                   bar_x + BAR_W / 2 + CHECKER_R, cy + CHECKER_R],
                  fill=white_checker, outline=outline, width=2)
        d.text((bar_x + BAR_W / 2, cy), str(board.bar["W"]), fill=black_checker, font=f_bar, anchor="mm")
    if board.bar["B"] > 0:
        cy = board_bottom - TRI_H + 40
        d.ellipse([bar_x + BAR_W / 2 - CHECKER_R, cy - CHECKER_R,
                   bar_x + BAR_W / 2 + CHECKER_R, cy + CHECKER_R],
                  fill=black_checker, outline=outline, width=2)
        d.text((bar_x + BAR_W / 2, cy), str(board.bar["B"]), fill=white_checker, font=f_bar, anchor="mm")

    # off (borne-off) trays, right side -- each borne-off checker shown
    # as a thin bar rather than just a count, like checkers lying flat
    # in a real bear-off tray. Shrunk a bit from the board's full half-
    # height so there's a real gap between the two boxes for the cube
    # to live in without overlapping either box's contents.
    f_off = _font(15, bold=True)
    f_pip = _font(12)
    off_x = MARGIN + BOARD_W + 10
    off_box_w = OFF_W - 20
    off_box_h = TRI_H - 30
    box_top_top, box_top_bottom = board_top, board_top + off_box_h
    box_bot_top, box_bot_bottom = board_bottom - off_box_h, board_bottom
    d.rectangle([off_x, box_top_top, off_x + off_box_w, box_top_bottom], outline=text_dim, width=2)
    d.rectangle([off_x, box_bot_top, off_x + off_box_w, box_bot_bottom], outline=text_dim, width=2)

    w_pips = board.pip_count("W")
    b_pips = board.pip_count("B")
    cx = off_x + off_box_w / 2
    d.text((cx, box_top_top + 12), "W", fill=text, font=f_off, anchor="mm")
    d.text((cx, box_bot_top + 12), "B", fill=text, font=f_off, anchor="mm")
    d.text((cx, box_top_bottom - 9), f"{w_pips}p", fill=point_label, font=f_pip, anchor="mm")
    d.text((cx, box_bot_bottom - 9), f"{b_pips}p", fill=point_label, font=f_pip, anchor="mm")

    _draw_off_stack(d, off_x, off_x + off_box_w, box_top_top + 22, board.off["W"], white_checker, outline,
                     slot_h=10, bar_h=7)
    _draw_off_stack(d, off_x, off_x + off_box_w, box_bot_top + 22, board.off["B"], black_checker, outline,
                     slot_h=10, bar_h=7)

    # doubling cube -- always drawn in the gap between the two boxes
    # (never inside either one), positioned within that gap by who owns
    # it so the position still reads as "whose cube is this"
    f_cube = _font(20, bold=True)
    cube_cx = off_x + off_box_w / 2
    cube_size = 34
    if cube_owner is None:
        cube_cy = (box_top_bottom + box_bot_top) / 2
    elif cube_owner == "W":
        cube_cy = box_top_bottom + cube_size / 2 + 1
    else:
        cube_cy = box_bot_top - cube_size / 2 - 1
    d.rounded_rectangle(
        [cube_cx - cube_size / 2, cube_cy - cube_size / 2,
         cube_cx + cube_size / 2, cube_cy + cube_size / 2],
        radius=6, fill=die_face, outline=outline, width=2,
    )
    d.text((cube_cx, cube_cy), str(cube_value), fill=black_checker, font=f_cube, anchor="mm")

    # header / footer text
    f_head = _font(22, bold=True)
    f_body = _font(16)
    header = f"{white_name} (White)  vs  {black_name} (Black)"
    if turn_no is not None:
        header += f"   -   turn {turn_no}"
    d.text((MARGIN, 20), header, fill=text, font=f_head)

    y = board_bottom + 60
    row_h = 26
    text_x = MARGIN

    if dice:
        die_row_h = DIE_SIZE + 6
        row_h = max(row_h, die_row_h)
        die_cy = y + row_h / 2
        _draw_die(d, MARGIN + DIE_SIZE / 2, die_cy, DIE_SIZE, dice[0], die_face, die_pip, outline)
        _draw_die(d, MARGIN + DIE_SIZE * 1.5 + DIE_GAP, die_cy, DIE_SIZE, dice[1], die_face, die_pip, outline)
        text_x = MARGIN + 2 * DIE_SIZE + DIE_GAP + 18

    if status_text:
        display_status = _TRAILING_DICE_RE.sub(".", status_text) if dice else status_text
        text_y = y + row_h / 2 if dice else y
        anchor = "lm" if dice else "la"
        d.text((text_x, text_y), display_status, fill=text, font=f_body, anchor=anchor)
    elif to_move and dice:
        mover_name = white_name if to_move == "W" else black_name
        d.text((text_x, y + row_h / 2), f"On roll: {mover_name} ({to_move})",
               fill=text, font=f_body, anchor="lm")

    return img


def save_board_png(board, path, **kwargs):
    img = draw_board(board, **kwargs)
    img.save(path, "PNG")
    return path
