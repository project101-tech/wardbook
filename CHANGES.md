# Blackjack (Hangout → Games): chips, bets and "your seat at the bottom"

This zip contains the **whole** Blackjack feature (it replaces the earlier Blackjack zip; apply it on top of your repo either way).
**Run `python manage.py migrate`** (0019 only updates the game choices; no table changes). No new packages or settings.

## What is new in this update
- **Betting.** Everyone starts every game with 1,000 play chips (no real money). Before each deal, every player (not the dealer) bets any **even** number of chips from 10 up to the table limit, using the bet box (number field, +10 / +50 / +100 / +500, Last, Max, Clear) or sits the round out. A bet can be changed until the dealer deals.
- **The dealer is the bank.** The dealer does not bet; they collect losing bets and pay winning ones. The **table limit** is a share of the bank (bank ÷ (2 × players)), so the bank can always pay. Double / Split are only offered if you have the chips *and* the bank can cover the bigger hand.
- **Standard payouts, settled after every round:** win 1:1, blackjack 3:2, push returns the bet, loss loses the bet, double and each split hand on their own (larger) bet, insurance costs half the bet and pays 2:1. Chips only move between players.
- **Live holdings.** A "Your holdings / On the table / This game" strip (the dealer sees "Your bank / Table limit / This game"), chips under every name on the table, and a Holdings table for everyone.
- **Your seat is at the bottom.** On your screen your own cards are always in front of you at the bottom; the dealer is at the top and the other players sit in turn order in between. If you are the dealer, you sit at the bottom and the players face you.
- **Games.** Every game starts everyone on a fresh 1,000 chips; the game goes to whoever holds the most at the end. Match winner = most games won (ties: total chips won; still tied = draw). A player who runs out of chips sits out until the next game.
- Players who do not bet in time sit that round out (and miss a turn; three missed turns in a row removes a player). A round in which nobody bets is skipped (it still counts as one of the dealer's five).

## Rules (unchanged)
6-deck shoe; J/Q/K = 10; Ace = 1 or 11 (best total used automatically); dealer peeks on Ace/ten and stands on all 17s (including soft 17); dealer doesn't draw if everyone busted or has blackjack; double on first two cards (also after a split); split equal-value cards up to 4 hands, split aces get one card each; no surrender; bust loses even if the dealer busts; a 21 after a split is not blackjack. Timers: bets+deal 45s, insurance 25s, turn 40s. A game = every player has dealt 5 rounds; the host picks how many games (1, 2, 3, 4, 5 or 10) when inviting. The shoe and the dealer's face-down card are never sent to other players' browsers.

## Files
New: `core/blackjack_engine.py`, `core/test_blackjack.py`, `core/migrations/0019_blackjack.py`, `static/js/games-blackjack.js`
Changed: `core/models.py`, `core/gamelogic.py`, `core/game_views.py`, `templates/games.html`, `templates/_game_active_row.html`, `templates/game_play.html`, `static/js/games.js`, `static/css/games.css`
