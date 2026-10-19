# This update: Teen Patti without a wallet

Copy every file listed below into your repo (keep the same paths), then commit.
**No new migration. No new Python packages.** Nothing to run except your normal deploy.

## Changed files (replace whole file)
| File | What changed |
|------|--------------|
| `core/teenpatti_engine.py` | New buy-in phase, unlimited "add ₹500" top-ups, waiting state, per-player "invested" tracking |
| `core/game_views.py` | Wallet, refill and the 24 h timer are gone. Opening a table no longer touches any wallet |
| `config/urls.py` | Removed the `games/wallet/refill/` URL |
| `core/admin.py` | Removed the (now pointless) Game wallets page |
| `templates/games.html` | No token counter or Refill button in the lobby; new hint text under the boot choice |
| `static/js/games-teenpatti.js` | Buy-in picker, "+ Add ₹500" button, waiting banner, tokens / put in / net results |
| `static/css/games.css` | Styles for the above (appended at the end) |
| `README.md` | One sentence about Teen Patti updated |

## How it works now
1. **Lobby:** no tokens anywhere. You only pick the **boot** (₹10 / ₹20 / ₹50) and your neighbours, like before.
2. **When the table opens**, every player privately picks how much to bring: **₹500, ₹1,000, ₹1,500, ₹2,000, ₹3,000, ₹5,000**, or any other amount from ₹500 up in steps of ₹100.
   Everyone can pick a different amount. The first hand is dealt when all have chosen (3 minutes; anyone who does not choose is taken off the table).
3. **1 token = ₹10.** Your tokens show at the table (your own box and next to every player), never in the lobby.
4. **Run low?** Tap **+ Add ₹500** (tap twice to confirm) for **+50 tokens**. No limit on how many times. It works mid-hand or between hands.
   If nobody left can pay the boot, the table **waits 90 seconds** for someone to add tokens instead of throwing them out.
5. **Leaving or closing:** a result pops up with your **tokens, what you put in, and net (₹)**, next to every other player so you can compare.
   When the table closes, everyone gets the final comparison.
6. **Nothing carries over.** There is no refill and no 24 h wait. Every new table (including "New table" after a game) starts from scratch.

## Small fix included
If a third player left in the middle of a side show, the hand could get stuck. The remaining player now wins the pot correctly.

## Notes
- The old `GameWallet` database table is simply unused now. It is left in place on purpose so there is no migration; it is harmless.
- Tables that were already running when you deploy keep working.
