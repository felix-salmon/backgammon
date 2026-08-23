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


def _point_x(point_abs, top_row):
    """x-center of the triangle base for absolute point 1..24."""
    if top_row:
        # top row goes 13..24 left to right, but 13-18 | bar | 19-24
        idx = point_abs - 13  # 0..11
    else:
        # bottom row goes 12..1 left to right, 12-7 | bar | 6-1
        idx = 12 - point_abs  # for point 12 -> 0 ... point 1 -> 11
    half = idx // 6
    offset = idx % 6
    x = MARGIN + half * (6 * POINT_W + BAR_W) + offset * POINT_W + POINT_W / 2
    return x


def _draw_die(d, cx, cy, size, value):
    """One die face with pips, centered at (cx, cy)."""
    half = size / 2
    d.rounded_rectangle(
        [cx - half, cy - half, cx + half, cy + half],
        radius=size * 0.16, fill=DIE_FACE, outline=OUTLINE, width=2,
    )
    pip_r = size * 0.09
    offset = size * 0.26
    for ox, oy in _PIP_LAYOUT.get(value, []):
        px, py = cx + ox * offset, cy + oy * offset
        d.ellipse([px - pip_r, py - pip_r, px + pip_r, py + pip_r], fill=DIE_PIP)


def draw_board(board, to_move=None, dice=None,
               white_name="White", black_name="Black", turn_no=None,
               cube_value=1, cube_owner=None, status_text=None):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    board_top = 90
    board_bottom = board_top + 2 * TRI_H
    d.rectangle([MARGIN, board_top, MARGIN + BOARD_W, board_bottom], fill=BOARD_BG, outline=OUTLINE, width=2)

    bar_x = MARGIN + 6 * POINT_W
    d.rectangle([bar_x, board_top, bar_x + BAR_W, board_bottom], fill=BAR_COLOR)

    # triangles
    f_label = _font(17, bold=True)
    for abs_pt in range(1, 25):
        top_row = abs_pt >= 13
        cx = _point_x(abs_pt, top_row)
        color = POINT_LIGHT if abs_pt % 2 == 0 else POINT_DARK
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
        d.text((cx, label_y), str(abs_pt), fill=POINT_LABEL, font=f_label, anchor="mm")

    # checkers
    f_small = _font(14, bold=True)
    for abs_pt in range(1, 25):
        cnt = board.points[abs_pt - 1]
        if cnt == 0:
            continue
        top_row = abs_pt >= 13
        cx = _point_x(abs_pt, top_row)
        n = abs(cnt)
        color = WHITE_CHECKER if cnt > 0 else BLACK_CHECKER
        text_color = BLACK_CHECKER if cnt > 0 else WHITE_CHECKER
        max_stack = 5
        shown = min(n, max_stack)
        for i in range(shown):
            if top_row:
                cy = board_top + CHECKER_R + i * (CHECKER_R * 1.9)
            else:
                cy = board_bottom - CHECKER_R - i * (CHECKER_R * 1.9)
            d.ellipse([cx - CHECKER_R, cy - CHECKER_R, cx + CHECKER_R, cy + CHECKER_R],
                      fill=color, outline=OUTLINE, width=2)
            if i == shown - 1 and n > max_stack:
                d.text((cx, cy), f"+{n - max_stack + 1}", fill=text_color, font=f_small, anchor="mm")

    # bar checkers
    f_bar = _font(15, bold=True)
    if board.bar["W"] > 0:
        cy = board_top + TRI_H - 40
        d.ellipse([bar_x + BAR_W / 2 - CHECKER_R, cy - CHECKER_R,
                   bar_x + BAR_W / 2 + CHECKER_R, cy + CHECKER_R],
                  fill=WHITE_CHECKER, outline=OUTLINE, width=2)
        d.text((bar_x + BAR_W / 2, cy), str(board.bar["W"]), fill=BLACK_CHECKER, font=f_bar, anchor="mm")
    if board.bar["B"] > 0:
        cy = board_bottom - TRI_H + 40
        d.ellipse([bar_x + BAR_W / 2 - CHECKER_R, cy - CHECKER_R,
                   bar_x + BAR_W / 2 + CHECKER_R, cy + CHECKER_R],
                  fill=BLACK_CHECKER, outline=OUTLINE, width=2)
        d.text((bar_x + BAR_W / 2, cy), str(board.bar["B"]), fill=WHITE_CHECKER, font=f_bar, anchor="mm")

    # off (borne-off) trays, right side
    f_off = _font(16, bold=True)
    off_x = MARGIN + BOARD_W + 10
    off_box_w = OFF_W - 20
    d.rectangle([off_x, board_top, off_x + off_box_w, board_top + TRI_H - 10], outline=TEXT_DIM, width=2)
    d.rectangle([off_x, board_bottom - TRI_H + 10, off_x + off_box_w, board_bottom], outline=TEXT_DIM, width=2)
    d.text((off_x + off_box_w / 2, board_top + TRI_H / 2 - 5), f"W\n{board.off['W']}", fill=TEXT, font=f_off, anchor="mm")
    d.text((off_x + off_box_w / 2, board_bottom - TRI_H / 2 + 5), f"B\n{board.off['B']}", fill=TEXT, font=f_off, anchor="mm")

    # doubling cube
    f_cube = _font(20, bold=True)
    cube_cx = off_x + off_box_w / 2
    if cube_owner is None:
        cube_cy = (board_top + board_bottom) / 2
    elif cube_owner == "W":
        cube_cy = board_top + TRI_H - 15
    else:
        cube_cy = board_bottom - TRI_H + 15
    cube_size = 34
    d.rounded_rectangle(
        [cube_cx - cube_size / 2, cube_cy - cube_size / 2,
         cube_cx + cube_size / 2, cube_cy + cube_size / 2],
        radius=6, fill=(235, 230, 220), outline=OUTLINE, width=2,
    )
    d.text((cube_cx, cube_cy), str(cube_value), fill=BLACK_CHECKER, font=f_cube, anchor="mm")

    # header / footer text
    f_head = _font(22, bold=True)
    f_body = _font(16)
    header = f"{white_name} (White)  vs  {black_name} (Black)"
    if turn_no is not None:
        header += f"   -   turn {turn_no}"
    d.text((MARGIN, 20), header, fill=TEXT, font=f_head)

    y = board_bottom + 60
    row_h = 26
    text_x = MARGIN

    if dice:
        die_row_h = DIE_SIZE + 6
        row_h = max(row_h, die_row_h)
        die_cy = y + row_h / 2
        _draw_die(d, MARGIN + DIE_SIZE / 2, die_cy, DIE_SIZE, dice[0])
        _draw_die(d, MARGIN + DIE_SIZE * 1.5 + DIE_GAP, die_cy, DIE_SIZE, dice[1])
        text_x = MARGIN + 2 * DIE_SIZE + DIE_GAP + 18

    if status_text:
        display_status = _TRAILING_DICE_RE.sub(".", status_text) if dice else status_text
        text_y = y + row_h / 2 if dice else y
        anchor = "lm" if dice else "la"
        d.text((text_x, text_y), display_status, fill=TEXT, font=f_body, anchor=anchor)
    elif to_move and dice:
        mover_name = white_name if to_move == "W" else black_name
        d.text((text_x, y + row_h / 2), f"On roll: {mover_name} ({to_move})",
               fill=TEXT, font=f_body, anchor="lm")

    return img


def save_board_png(board, path, **kwargs):
    img = draw_board(board, **kwargs)
    img.save(path, "PNG")
    return path
