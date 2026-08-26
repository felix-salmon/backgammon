# Backgammon PBM (Play By Email) replacement

A from-scratch replacement for Richard's PBM server: email your move in the
subject line, get back an emailed image of the new board with the next
roll already made, plus whatever you wrote in the body underneath. Includes
the doubling cube, with the same "manual" switch PBM used.

## What's here

- `board.py` -- board state, legality checking, hits, bar, bearing off
- `dice.py` -- dice rolling
- `notation.py` -- parses subject-line move text like `24/18 13/11`, `bar/19`, `6/off`
- `game.py` -- turn validation/application, dice inference, win detection, **doubling cube + manual/auto dice mode**
- `render.py` -- draws the board (and cube) to a PNG
- `state.py` -- SQLite storage, one row per game
- `cli.py` -- **play locally in the terminal right now, no email needed**
- `email_io.py` -- ImprovMX webhook parsing (inbound) + plain SMTP (outbound)
- `app.py` -- the Flask webhook ImprovMX calls on each incoming email, plus an admin endpoint for starting games on a deployed instance
- `admin.py` -- shared "create a game and send the opening email" logic
- `start_game.py` -- CLI wrapper around `admin.py`, for local use only (see the Render section for why)

## Try it right now (no setup)

```
pip install -r requirements.txt
python3 cli.py
```

It'll ask for both names, then alternate turns in the terminal, printing an
ASCII board and saving a real rendered PNG into `boards/` after every move
so you can see exactly what the emailed board would look like. Type `help`
at any prompt for the full command list.

Move notation -- any of these, mixed freely, even within the same turn:
- `24/18 13/11` -- slash style, two separate checkers
- `24-18,13-11` -- PBeM style: hyphen instead of slash, comma between moves
- `2x24` -- shorthand: run 2 checkers off point 24, one per available die
  (needs the actual dice roll to expand, so it only works mid-game, not
  standalone)
- `13/11/8` or `13-11-8` -- one checker using both dice in sequence, with
  the intermediate stop spelled out
- `13/8` -- the same move, but written as just the endpoints. If no single
  die covers the distance, but two or more of your remaining dice sum to
  it, the engine finds a legal path through them automatically (checking
  every ordering, so a blocked intermediate point on one path doesn't
  stop it from trying the other). Works with doubles too, combining as
  many dice as needed.
- `bar/19` or `bar-19` or `b-19` -- entering from the bar (`b` is short for `bar`)
- `6/off` or `6-off` -- bearing off
- `22` (a bare point, no `/` or `-`) -- move whatever's on 22, letting the
  engine work out the destination, as long as exactly one of your
  remaining dice gives a legal move from there. Chain several with spaces
  or commas -- `b,22` means "enter from the bar (using whichever die is
  forced), then move whatever's on 22 with whatever die is left." Errors
  out clearly if it's ambiguous (more than one legal destination) or
  impossible (no legal destination at all).

Point numbers always mean exactly what's printed on the board image --
the same numbering for both colors. There's no "count from your own
side" convention to remember; whatever number sits next to a checker in
the picture is the number you type, whether you're White or Black.

## Recent moves and full history

Every board-update email now includes a plain-text "Recent moves" section
in the body -- the last several turns, each showing who rolled what and
what they played, or "no legal move" if they danced. It's regular
legible body text, not baked into the image, so it's never a legibility
fight regardless of board theme. Every line always shows the dice that
were rolled, even on a turn where nothing could be played -- so a dance
at least tells you what you rolled, rather than just silently passing.

## Checking the board without waiting for an email

Every board-update email also ends with a link to a live view of the
current game:

```
https://yourhost/board/<game_id>
```

That page shows the board itself plus the **complete** move history
below it, most recent first -- so it's the place to go if you want the
full record, not just the last several turns an email shows.

If a move email doesn't arrive at all (an email delivery hiccup, not
something in your control), send **`resend`** and the game re-sends the
most recent move -- board, summary, and the other player's message --
to just you, not both players. Handy for exactly the case where you can
already see the board's moved on via the `/board/<id>` link but never
actually got the email explaining what happened.

If someone's move fails validation (bad notation, illegal move, etc.),
they get the specific error, and -- new -- their opponent quietly gets a
one-line heads-up ("so-and-so's last message didn't go through, still
their move") with the same link, so a silent failure doesn't leave the
other player wondering why nothing's happening.

## The doubling cube

Exactly like PBM: send **`manual`** at any point and *your own* turns stop
auto-rolling -- it's per-player, so it never affects your opponent's
turns unless they separately send `manual` too. From then on, on your
own turns, you get asked to reply with either:

- **`roll`** -- roll as normal
- **`double`** -- offer to double (only if you own the cube, or it's
  centered, and only before you've rolled)

If someone doubles, the other player replies **`take`**/**`accept`** (cube
doubles, they now own it, and play continues) or **`drop`**/**`pass`**
(they concede at the current cube value). Send **`auto`** any time to go
back to automatic rolling for your own turns. There's also a plain
**`resign`** if someone just wants to concede outright.

## Forced moves and greedy racing

If there's only one legal way to play a roll (or no legal way at all),
the game plays it automatically rather than making you type it out --
you'll see it show up tagged `(forced)` or `(no legal move)`. This can
chain through several turns in a row if both of you keep getting forced
positions (common in a tight bear-off race).

Send **`greedy`** instead of a move and the game plays your current dice
for you, always moving the most-advanced checker with each die. It's
meant for pure races once no contact is possible -- it has no notion of
safety, so don't use it while there's still a blot in play.

## Running tally

Every finished game is scored like a real money game: the winner gets the
cube value, doubled to 2x if the loser bore off zero checkers (a gammon),
or tripled to 3x if the loser also still had a checker on the bar or in
the winner's home board (a backgammon). Declined doubles and resignations
always score at just the current cube value -- no gammon/backgammon
multiplier, since the board never finished.

The running head-to-head score between two players (across every game
they've played, regardless of label) shows up automatically in the
win-announcement email, and you can check it any time at:

```
https://yourhost/tally?a=felix@felixsalmon.com&b=simon@example.com
```

## Multiple games, multiple people

Nothing stops you from running several games at once -- against Simon,
against someone else, whatever -- as long as each has its own label (see
`start_game.py`/the admin endpoint). If a player only has one game going,
they can leave the `[label]` off their subject entirely and it'll figure
out which game they mean. Once someone has more than one game running,
they need to lead their subject with the right label, e.g. `[g2] 24/18`
-- if they forget, they'll get an email back listing their active labels
rather than the move silently landing in the wrong game.

## Rematches

Once a game finishes, either player can reply `rematch` (or `new game`,
`again`, `play again`) to start a fresh one against the same opponent --
no terminal, no curl command. It continues whatever numbering pattern
the two of you already have going: rematching `g1` when `g2` also exists
gives `g3` (not `g1-2`, which would read like a variant of `g1` rather
than the next game in the sequence), and rematching a plain-named game
like `skye` gives `skye2`. This only fires once the referenced game has
actually ended; sending it to a game still in progress is just treated
as an invalid move, same as any other typo.

## Maximal play

Same rule as real backgammon: you have to play as many of your dice as
any legal sequence can manage, not just stop once you've found *a*
legal move. If a submitted move leaves dice unplayed that could have
been used, it's rejected with a note on how many you could have played.
Combining two or more dice to reach a point no single die connects to
is found automatically -- including right after entering from the bar,
e.g. `b/2` with a roll of all 1s enters on 1 and continues to 2 with a
second 1, no need to spell out the intermediate stop.

## Wiring up actual email

You've got a domain (`felixsalmon.com`) and an ImprovMX account, so this
uses **ImprovMX** for both directions:

- **Inbound**: ImprovMX's webhook feature POSTs a JSON body to your app for
  every email an alias receives. No plan restriction on this as of writing.
- **Outbound**: ImprovMX's SMTP sending (`smtp.improvmx.com`), which
  **does** require their Premium plan. If you'd rather not upgrade, `email_io.py`
  talks plain SMTP, so pointing it at any mailbox you already have working
  SMTP credentials for (a Gmail app password, Fastmail, etc.) works exactly
  the same way -- just set the `SMTP_*` environment variables below to that
  provider instead.

### 1. Pick an address and set up the ImprovMX webhook

Rather than reuse your main `felixsalmon.com` mail flow, it's cleanest to
use a dedicated alias, e.g. `bg@felixsalmon.com`, purely for this game (you
and Simon will both send TO this address; the game figures out who's who
from the sender).

1. In the [ImprovMX dashboard](https://app.improvmx.com/), make sure
   `felixsalmon.com` is added and its MX records are pointed at ImprovMX
   (you may already have this from your existing account).
2. Add an alias `bg` (i.e. `bg@felixsalmon.com`).
3. As that alias's forward destination, enter your webhook URL once you've
   deployed the app (step 2 below):
   ```
   https://yourhost/inbound
   ```
   You can test it first with a free URL from webhook.site before your app
   is deployed, just to confirm mail is arriving.

### 2. Deploy `app.py` on Render

**Use a paid Starter instance with a persistent disk attached, not the free tier.** Render's free web services have no persistent disk at all -- every file write vanishes the moment the service spins down (which it does after 15 minutes of no traffic), and free services also don't support SSH/shell access. Since the whole game lives in one SQLite file, and moves might come in hours apart, free tier will silently lose your game. Starter is $7/mo, plus about $0.25/mo for a 1GB disk (way more than you need) -- call it $7.25/mo, and it also removes the cold-start delay that could otherwise make ImprovMX's webhook time out.

1. Push this project to a GitHub repo.
2. In Render, **New -> Web Service**, connect the repo.
3. Environment: Python 3. Build command: `pip install -r requirements.txt`. Start command: `gunicorn app:app`.
4. Instance type: **Starter** (or higher).
5. Add a disk: Settings -> Disks -> Add Disk. Mount path `/data`, size 1GB is plenty.
6. Add environment variables:
   ```
   BACKGAMMON_DB=/data/backgammon.db
   SMTP_HOST=smtp.improvmx.com
   SMTP_PORT=587
   SMTP_USER=bg@felixsalmon.com
   SMTP_PASS=<the SMTP credential password from step 2 above>
   ADMIN_TOKEN=<any long random string you make up>
   ```
   Optionally also `NOTIFY_WAITING_EMAILS` -- a comma-separated list of
   who gets the quiet "still waiting on X" heads-up when someone's move
   fails. Defaults to just `felix@felixsalmon.com`; set it explicitly if
   you want to add people or change that.
7. Deploy. Render gives you a URL like `https://backgammon-pbm.onrender.com`.

### 3. Point ImprovMX at it

Set `bg@felixsalmon.com`'s forward destination to:
```
https://backgammon-pbm.onrender.com/inbound
```

For testing before you deploy, you can instead run `python3 app.py` locally
and use `ngrok http 5000` to get a temporary public URL to test against.

### 4. Start a game

Because Render's filesystem isn't reachable from your laptop, running
`start_game.py` locally would create a game in a *different* SQLite file
than the one your deployed app actually uses. Instead, hit the admin
endpoint on the deployed app directly:

```
curl -X POST https://backgammon-pbm.onrender.com/admin/start_game \
  -H "X-Admin-Token: <the ADMIN_TOKEN you set above>" \
  -H "Content-Type: application/json" \
  -d '{"label":"g1","white_email":"felix@felixsalmon.com","white_name":"Felix",
       "black_email":"simon@example.com","black_name":"Simon"}'
```

That creates the game and emails both of you the opening board. (If
you're testing entirely locally with `python3 app.py` + `cli.py`/manual
webhooks, `start_game.py` works fine there instead, since everything
shares one filesystem.)

From then on, just reply to that email with your move (or a command like
`manual`, `double`, `roll`) in the subject line -- the label prefix
(`[g1]`) only matters if you and Simon ever have more than one game
running at once; the app strips it back off before parsing.

### A couple of things worth knowing

- ImprovMX's webhook payload already gives clean, pre-parsed JSON (sender,
  subject, plain-text body) -- no MIME wrangling needed on your end.
- The move itself always comes from the subject line, never the body --
  so quoting weirdness in someone's reply can never affect which move
  gets played, only whether their accompanying note comes through cleanly.
- Replies usually carry quoted history from earlier in the thread.
  `parse_inbound_improvmx()` in `email_io.py` splits the body at the
  first thing that looks like a quote marker (a `>` line, "On ... wrote:",
  etc.) and treats everything above it as the real new note. The quoted
  part isn't discarded, though -- it's still included in the outgoing
  email, just visually demoted and labeled, in case the split ever
  guesses wrong (a reply client can occasionally put someone's actual
  new text inside what looks like a quote).
- ImprovMX's webhook always comes from a fixed IP (`15.237.103.194`) if you
  want to lock down your Flask endpoint to only accept requests from there.
