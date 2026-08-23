"""
Play a game locally in the terminal -- no email involved. Also the
"proof it works" harness for testing, including the doubling cube.

Usage:
    python3 cli.py [output_dir]

Commands you can type at the move prompt, in addition to move notation:
    manual    -- switch to manual dice mode (needed to be able to double)
    auto      -- switch back to automatic dice rolling
    roll      -- (manual mode only) roll your dice
    double    -- (manual mode only, before rolling) offer to double
    take      -- accept a pending double
    drop      -- decline a pending double (concede at the current cube value)
    resign    -- concede the game outright
    greedy    -- (once dice are rolled) auto-play them, always moving the
                 most-advanced checker -- for pure bear-off races only

Forced moves (only one legal way to play the dice, or none at all) are
played automatically and shown tagged '(forced)' or '(no legal move)'.

Move notation -- any of these, mixed freely:
    24/21 13/8       slash style
    24-21,13-8       PBeM style (hyphen + comma)
    2x24             shorthand: run 2 checkers off point 24, one per die

Point numbers always mean exactly what's printed on the board image --
same numbering for both colors, no need to count from your own side.
"""

import os
import sys

from board import WHITE, BLACK
from game import Game, IllegalMove, CommandError, TurnRecord, CubeEvent
from render import save_board_png


def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "boards"
    os.makedirs(out_dir, exist_ok=True)

    white_name = input("White player's name: ").strip() or "White"
    black_name = input("Black player's name: ").strip() or "Black"

    g = Game.new()
    print(f"\n{white_name if g.to_move == WHITE else black_name} goes first "
          f"with a roll of {g.dice[0]}-{g.dice[1]}.\n")
    print("(Type 'manual' any time to unlock the doubling cube. 'help' for commands.)\n")

    step = 0
    while not g.is_over():
        step += 1
        mover_name = white_name if g.to_move == WHITE else black_name
        print(g.board.ascii())
        print(f"\n--- {g.status_text(white_name, black_name)} ---")

        prompt_player = mover_name
        if g.awaiting == "double_response":
            from board import other
            prompt_player = white_name if other(g.pending_doubler) == WHITE else black_name

        text = input(f"[{prompt_player}] > ").strip()
        if text.lower() == "help":
            print(__doc__)
            step -= 1
            continue
        if not text:
            step -= 1
            continue

        message = ""
        if text.lower() not in ("manual", "auto", "roll", "double", "take", "accept",
                                 "drop", "pass", "resign", "greedy"):
            message = input("Message to include (optional): ").strip()

        sender = g.to_move
        if g.awaiting == "double_response" and text.lower() in ("take", "accept", "drop", "pass"):
            from board import other
            sender = other(g.pending_doubler)

        try:
            result = g.process_input(sender, text, message)
        except (IllegalMove, CommandError) as e:
            print(f"  -> {e}\n")
            step -= 1
            continue

        if isinstance(result, str):
            print(f"  -> {result}\n")
            step -= 1
            continue
        if isinstance(result, CubeEvent):
            print(f"  -> {result.kind}: {result.value}\n")
        if isinstance(result, TurnRecord) and result.hits:
            print(f"  -> hit on point(s): {result.hits}")
        for auto in g.last_auto_played:
            tag = "no legal move" if auto.move_text == "(no legal move)" else f"played {auto.move_text}"
            print(f"  -> {auto.player} was forced -- {tag}")

        png_path = os.path.join(out_dir, f"turn_{step:03d}.png")
        save_board_png(
            g.board, png_path,
            to_move=g.to_move if not g.is_over() else None,
            dice=g.dice if (not g.is_over() and g.awaiting == "move") else None,
            white_name=white_name, black_name=black_name,
            turn_no=step + 1 if not g.is_over() else step,
            cube_value=g.cube_value, cube_owner=g.cube_owner,
            status_text=g.status_text(white_name, black_name),
        )
        print(f"  -> board saved to {png_path}\n")
        if message:
            print(f"  -> message: {message}")

    print(f"\n{g.status_text(white_name, black_name)}")


if __name__ == "__main__":
    main()
