# Blackjack (Hangout → Games)

**Run `python manage.py migrate`** (0019 only updates the game choices; no table changes).
No new packages or settings.

## How it works
- Pick **Blackjack** in Games, choose **How many games?** (1, 2, 3, 4, 5 or 10), invite 1–6 people (2–7 at the table). It starts when everyone answers or the host presses Start.
- Seats are shuffled; a random player deals first. Each player is the dealer for **5 rounds**, then the deal moves to the next seat. When everyone has dealt 5 rounds, that is **one game**.
- The dealer presses **Deal cards**. Everyone's cards are on the table with names underneath. Players act in seat order (clockwise from the dealer's left): Hit, Stand, Double, Split.
- Winner of a game = most points that game. Winner of the match = most games won (ties: total points; still tied = draw).
- Points, not money: win +1, blackjack +1.5, push 0, loss −1, insurance ½ (scores are stored in half-points).

## Rules implemented
- 6-deck shoe, reshuffled between rounds when 25% is left. J/Q/K = 10. Ace = 1 or 11 (best total is used automatically; soft totals show as "7/17").
- Blackjack = Ace + ten-value card as first two cards (not after a split), pays 3:2. Dealer peeks on Ace/ten upcard.
- Insurance offered when dealer shows an Ace: half a point, pays 2:1.
- Dealer draws to 16 and stands on every 17 (including soft 17). Dealer does not draw if every hand is bust or blackjack.
- Double on first two cards (also after a split). Split any equal-value pair (10/J/Q/K count as equal), up to 4 hands; split aces get one card each and cannot be re-split; 21 after a split is not blackjack. No surrender.
- Bust loses even if the dealer also busts. Equal totals push.
- Timers: dealer 45s to deal, insurance 25s, 40s per turn (auto-stand). Three missed turns in a row removes a player; **Leave table** is always available. The match ends if fewer than two players remain.
- The shoe and the dealer's face-down card are never sent to other players' browsers.

## Files
New: `core/blackjack_engine.py`, `core/test_blackjack.py`, `core/migrations/0019_blackjack.py`, `static/js/games-blackjack.js`
Changed: `core/models.py`, `core/gamelogic.py`, `core/game_views.py`, `templates/games.html`, `templates/_game_active_row.html`, `templates/game_play.html`, `static/js/games.js`, `static/css/games.css`
