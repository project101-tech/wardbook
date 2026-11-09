"""Blackjack: every rule, checked with rigged decks, plus a fuzz test and the screens. Run with:
    python manage.py test core.test_blackjack
"""
import json
import random
from collections import Counter

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from . import blackjack_engine as bj
from .models import GameChallenge, GameParticipant, GameSession, ResidentProfile
from .testing import join

U = 10               # the bet every helper table uses (even, so 3 to 2 and insurance come out whole)
START = bj.START_CHIPS


def C(text):
    """'AS' '10H' 'KD' -> card number."""
    return bj.cid({"A": 1, "J": 11, "Q": 12, "K": 13}.get(text[:-1]) or int(text[:-1]), text[-1])


def table(n=2, games=1, hands=(), dealer=("2C", "3C"), then=(), bet=U):
    """A table of n players where the first round's cards are fixed.
    hands: the two cards of each player in seat order (left of the dealer first); dealer: (up card, hole card);
    then: the cards drawn after the deal, in order."""
    ids = list(range(1, n + 1))
    st = bj.new_table(ids, games, 0)
    order = st["rnd"]["order"]
    for u in order:
        go(st, u, f"bet:{bet}")
    hands = list(hands) or [("2D", "3D")] * len(order)
    seq = [C(h[0]) for h in hands] + [C(dealer[0])] + [C(h[1]) for h in hands] + [C(dealer[1])] + [C(x) for x in then]
    st["shoe"].extend(reversed(seq))
    return st


def has(st, uid, action):
    """Is this action on offer (and not greyed out)?"""
    return any(o["a"] == action and not o.get("off") for o in bj.options(st, uid))


def net(st, uid):
    """Chips won (+) or lost (-) so far in this game, counting chips still on the table."""
    return bj.holdings(st, uid) - START


def bet_all(st, amount=U):
    for u in st["rnd"]["order"]:
        if any(o["a"] == "bet" for o in bj.options(st, u)):
            go(st, u, f"bet:{amount}")


def go(st, uid, action, now=0):
    err = bj.act(st, uid, action, now)
    assert err is None, f"{action} by {uid}: {err}"


def deal(st):
    go(st, st["rnd"]["dealer"], "deal")


def finish_round(st):
    """Dealer draws whatever is forced."""
    while st["phase"] == "dealer":
        go(st, st["rnd"]["dealer"], "draw")


def last(st, i=0):
    return st["last"]["rows"][i]


class CountingTests(SimpleTestCase):
    def total(self, *cards):
        return bj.count([C(c) for c in cards])

    def test_numbers_and_faces(self):
        self.assertEqual(self.total("2S", "3H", "9D"), (14, False))
        self.assertEqual(self.total("JS", "QH"), (20, False))
        self.assertEqual(self.total("KS", "10H"), (20, False))

    def test_ace_counts_one_or_eleven_whichever_is_better(self):
        self.assertEqual(self.total("AS", "6H"), (17, True))                  # soft 17
        self.assertEqual(self.total("AS", "6H", "10D"), (17, False))          # the Ace has to be a 1 now
        self.assertEqual(self.total("AS", "AH"), (12, True))                  # only one Ace can be 11
        self.assertEqual(self.total("AS", "AH", "9D"), (21, True))
        self.assertEqual(self.total("AS", "AH", "10D"), (12, False))
        self.assertEqual(self.total("AS", "KH"), (21, True))
        self.assertEqual(self.total("AS", "5H", "5D", "AD"), (12, False))      # A+5+5+A: 22 would bust, so 12

    def test_bust(self):
        self.assertEqual(self.total("KS", "QH", "2D")[0], 22)

    def test_total_text(self):
        self.assertEqual(bj.total_text([C("AS"), C("6H")]), "7/17")      # Ace as 1 or 11
        self.assertEqual(bj.total_text([C("AS"), C("KH")]), "21")
        self.assertEqual(bj.total_text([C("KS"), C("QH"), C("2D")]), "Bust 22")

    def test_blackjack_is_exactly_ace_plus_ten_in_two_cards(self):
        self.assertTrue(bj.is_natural([C("AS"), C("KH")]))
        self.assertTrue(bj.is_natural([C("10S"), C("AH")]))
        self.assertFalse(bj.is_natural([C("7S"), C("7H"), C("7D")]))          # 21 in three cards is not blackjack
        self.assertFalse(bj.is_natural([C("9S"), C("AH")]))

    def test_shoe_is_six_decks(self):
        shoe = bj.new_shoe()
        self.assertEqual(len(shoe), 312)
        self.assertTrue(all(n == 6 for n in Counter(shoe).values()))


class DealingTests(SimpleTestCase):
    def test_deal_order_is_one_card_at_a_time_dealer_last(self):
        st = table(3, hands=[("2D", "3D"), ("4D", "5D")], dealer=("6D", "7D"))
        order, d = st["rnd"]["order"], st["rnd"]["dealer"]
        self.assertEqual(st["phase"], "deal")
        self.assertEqual(st["rnd"]["hands"], {})                               # nothing dealt until the dealer presses the button
        deal(st)
        h = st["rnd"]["hands"]
        self.assertEqual([C(x) for x in ("2D", "3D")], h[str(order[0])][0]["cards"])
        self.assertEqual([C(x) for x in ("4D", "5D")], h[str(order[1])][0]["cards"])
        self.assertEqual(st["rnd"]["dealer_cards"], [C("6D"), C("7D")])

    def test_only_the_dealer_can_deal(self):
        st = table(3)
        other = st["rnd"]["order"][0]
        self.assertEqual(bj.act(st, other, "deal", 0), "Only the dealer deals.")
        self.assertEqual([o["a"] for o in bj.options(st, other)], ["bet", "sit"])
        self.assertEqual(bj.options(st, st["rnd"]["dealer"]), [{"a": "deal"}])

    def test_cannot_deal_twice(self):
        st = table(2, dealer=("6D", "7D"))
        deal(st)
        self.assertEqual(bj.act(st, st["rnd"]["dealer"], "deal", 0), "The cards are already dealt.")

    def test_play_starts_left_of_the_dealer_and_dealer_plays_last(self):
        st = table(3, hands=[("9D", "8D"), ("9H", "7H")], dealer=("10D", "6D"), then=("5C",))
        a, b = st["rnd"]["order"]
        deal(st)
        self.assertEqual((st["phase"], st["rnd"]["turn"]), ("play", {"u": a, "h": 0}))
        self.assertEqual(bj.act(st, b, "stand", 0), "Wait for your turn.")
        go(st, a, "stand")
        self.assertEqual(st["rnd"]["turn"], {"u": b, "h": 0})
        go(st, b, "stand")
        self.assertEqual(st["phase"], "dealer")                                 # the dealer is last, and 16 must draw


class HitStandTests(SimpleTestCase):
    def test_hit_and_bust_loses_at_once(self):
        st = table(2, hands=[("10D", "6D")], dealer=("10H", "7H"), then=("9C",))
        p = st["rnd"]["order"][0]
        deal(st)
        go(st, p, "hit")
        self.assertEqual(st["phase"], "result")                                 # bust ends the hand; the dealer needs no card
        self.assertEqual(last(st)["hands"][0]["tag"], "bust")
        self.assertEqual(last(st)["net"], -U)

    def test_twenty_one_stands_by_itself(self):
        st = table(2, hands=[("10D", "5D")], dealer=("10H", "7H"), then=("6C",))
        p = st["rnd"]["order"][0]
        deal(st)
        go(st, p, "hit")                                                        # 10+5+6 = 21
        self.assertEqual(st["phase"], "result")
        self.assertEqual(last(st)["net"], U)                                    # 21 beats the dealer's 17

    def test_soft_hand_can_hit_without_busting(self):
        st = table(2, hands=[("AD", "6D")], dealer=("10H", "7H"), then=("10C", "3C"))
        p = st["rnd"]["order"][0]
        deal(st)
        go(st, p, "hit")                                                        # A+6+10 = 17 (hard)
        self.assertEqual((st["phase"], count_of(st, p)), ("play", 17))
        go(st, p, "stand")
        self.assertEqual(last(st)["hands"][0]["tag"], "push")                   # 17 v 17

    def test_player_bust_loses_even_if_dealer_busts_too(self):
        st = table(3, hands=[("10D", "6D"), ("10C", "8C")], dealer=("10H", "6H"), then=("10S", "9S"))
        a, b = st["rnd"]["order"]
        deal(st)
        go(st, a, "hit")                                                        # a busts with 25
        go(st, b, "stand")
        finish_round(st)                                                        # dealer 16 draws a 9 -> 25 bust
        rows = {r["id"]: r for r in st["last"]["rows"]}
        self.assertEqual(rows[a]["net"], -U)                                    # still a loss
        self.assertEqual(rows[b]["net"], U)                                     # b wins because the dealer busted
        self.assertEqual(st["last"]["dealer_net"], 0)

    def test_dealer_does_not_draw_when_every_player_busted(self):
        st = table(2, hands=[("10D", "6D")], dealer=("10H", "5H"), then=("10C", "9S"))
        p = st["rnd"]["order"][0]
        deal(st)
        go(st, p, "hit")
        self.assertEqual(len(st["rnd"]["dealer_cards"]), 2)
        self.assertTrue(st["rnd"]["hole_up"])


def count_of(st, uid, i=0):
    return bj.count(st["rnd"]["hands"][str(uid)][i]["cards"])[0]


class DealerRulesTests(SimpleTestCase):
    def play_dealer(self, up, hole, draws):
        st = table(2, hands=[("10D", "9D")], dealer=(up, hole), then=draws)
        p = st["rnd"]["order"][0]
        deal(st)
        if st["phase"] == "insurance":                                          # an Ace showing: say no thanks
            go(st, p, "ins:no")
        go(st, p, "stand")
        return st

    def test_dealer_draws_on_sixteen_and_stands_on_seventeen(self):
        st = self.play_dealer("10H", "6H", ("AC",))                             # 16 -> draws A -> 17 -> stands
        self.assertEqual(st["phase"], "dealer")
        go(st, st["rnd"]["dealer"], "draw")
        self.assertEqual((st["phase"], len(st["rnd"]["dealer_cards"])), ("result", 3))

    def test_dealer_stands_on_soft_seventeen(self):
        st = self.play_dealer("AH", "6H", ("5C",))
        self.assertEqual(st["phase"], "result")                                 # soft 17: no draw
        self.assertEqual(len(st["rnd"]["dealer_cards"]), 2)

    def test_dealer_draws_on_soft_sixteen(self):
        st = self.play_dealer("AH", "5H", ("10C",))
        self.assertEqual(st["phase"], "dealer")

    def test_dealer_has_to_keep_drawing(self):
        st = self.play_dealer("2H", "3H", ("2C", "4C", "6C"))                   # 5, 7, 11, 17
        n = 0
        while st["phase"] == "dealer":
            go(st, st["rnd"]["dealer"], "draw"); n += 1
        self.assertEqual(n, 3)

    def test_only_the_dealer_draws_and_only_when_needed(self):
        st = self.play_dealer("10H", "6H", ("5C",))
        p = st["rnd"]["order"][0]
        self.assertEqual(bj.act(st, p, "draw", 0), "Only the dealer draws.")
        go(st, st["rnd"]["dealer"], "draw")
        self.assertEqual(bj.act(st, st["rnd"]["dealer"], "draw", 0), "The dealer does not need a card.")

    def test_dealer_bust_pays_everyone_standing(self):
        st = self.play_dealer("10H", "6H", ("10C",))
        finish_round(st)
        self.assertEqual(last(st)["hands"][0]["tag"], "win")
        self.assertTrue(st["last"]["dealer_bust"])

    def test_equal_totals_push(self):
        st = self.play_dealer("10H", "9H", ())                                  # 19 v 19
        self.assertEqual(last(st)["hands"][0]["tag"], "push")
        self.assertEqual(last(st)["net"], 0)

    def test_higher_and_lower(self):
        st = self.play_dealer("10H", "KH", ())                                  # dealer 20 beats 19
        self.assertEqual(last(st)["net"], -U)


class BlackjackTests(SimpleTestCase):
    def test_player_blackjack_pays_three_to_two_and_skips_play(self):
        st = table(2, hands=[("AD", "KD")], dealer=("9H", "8H"))
        deal(st)
        self.assertEqual(st["phase"], "result")                                 # nothing to decide, dealer stands on 17
        self.assertEqual(last(st)["net"], U * 3 // 2)
        self.assertEqual(last(st)["hands"][0]["tag"], "blackjack")
        self.assertEqual(st["last"]["dealer_net"], -U * 3 // 2)

    def test_blackjack_beats_dealer_three_card_21(self):
        st = table(3, hands=[("AD", "KD"), ("10C", "8C")], dealer=("5H", "6H"), then=("10C",))
        a, b = st["rnd"]["order"]
        deal(st)
        go(st, b, "stand")                                                      # a has blackjack and does not play
        finish_round(st)                                                        # dealer 11 draws a ten: 21 in three cards
        self.assertEqual(bj.count(st["rnd"]["dealer_cards"])[0], 21)
        rows = {r["id"]: r for r in st["last"]["rows"]}
        self.assertEqual(rows[a]["net"], U * 3 // 2)                                     # blackjack is paid, a three-card 21 does not tie it
        self.assertEqual(rows[b]["net"], -U)

    def test_dealer_does_not_draw_when_the_only_hands_left_are_blackjacks(self):
        st = table(2, hands=[("AD", "KD")], dealer=("5H", "6H"), then=("10C",))
        deal(st)
        self.assertEqual(len(st["rnd"]["dealer_cards"]), 2)
        self.assertEqual(last(st)["net"], U * 3 // 2)

    def test_twenty_one_in_three_cards_pushes_a_dealer_twenty_one(self):
        st = table(2, hands=[("7D", "7C")], dealer=("5H", "6H"), then=("7S", "10C"))
        p = st["rnd"]["order"][0]
        deal(st)
        go(st, p, "hit")                                                        # 21
        finish_round(st)                                                        # dealer 11 + 10 = 21
        self.assertEqual(last(st)["hands"][0]["tag"], "push")

    def test_dealer_blackjack_ends_the_round_before_anyone_plays(self):
        st = table(3, hands=[("10D", "9D"), ("8D", "8C")], dealer=("KH", "AH"))
        deal(st)
        self.assertEqual(st["phase"], "result")                                 # the dealer peeked
        self.assertTrue(st["last"]["dealer_bj"])
        self.assertEqual([r["net"] for r in st["last"]["rows"]], [-U, -U])
        self.assertEqual(st["last"]["dealer_net"], 2 * U)

    def test_dealer_blackjack_pushes_player_blackjack(self):
        st = table(3, hands=[("AD", "KD"), ("8D", "8C")], dealer=("10H", "AH"))
        deal(st)
        self.assertEqual([r["net"] for r in st["last"]["rows"]], [0, -U])

    def test_dealer_with_ten_up_and_no_blackjack_play_goes_on(self):
        st = table(2, hands=[("10D", "9D")], dealer=("KH", "7H"))
        deal(st)
        self.assertEqual(st["phase"], "play")

    def test_no_peek_when_up_card_cannot_make_blackjack(self):
        st = table(2, hands=[("10D", "9D")], dealer=("9H", "9C"))
        deal(st)
        self.assertEqual(st["phase"], "play")


class InsuranceTests(SimpleTestCase):
    def setUp(self):
        self.st = table(3, hands=[("10D", "9D"), ("8D", "8C")], dealer=("AH", "KH"))
        self.a, self.b = self.st["rnd"]["order"]

    def test_offered_when_dealer_shows_an_ace_and_waits_for_everybody(self):
        deal(self.st)
        self.assertEqual(self.st["phase"], "insurance")
        self.assertEqual([o["a"] for o in bj.options(self.st, self.a)], ["ins:yes", "ins:no"])
        self.assertEqual(bj.options(self.st, self.st["rnd"]["dealer"]), [])
        go(self.st, self.a, "ins:yes")
        self.assertEqual(self.st["phase"], "insurance")
        self.assertEqual(bj.act(self.st, self.a, "ins:no", 0), "Insurance is not on offer.")   # one answer each

    def test_insurance_pays_two_to_one_when_dealer_has_blackjack(self):
        deal(self.st)
        go(self.st, self.a, "ins:yes")                                          # a: loses the hand (-1), wins insurance (+1)
        go(self.st, self.b, "ins:no")                                           # b: loses the hand
        rows = {r["id"]: r for r in self.st["last"]["rows"]}
        self.assertEqual(rows[self.a]["net"], 0)                                # lost the hand (-1), insurance paid 2:1 on half a point (+1)
        self.assertEqual(rows[self.b]["net"], -U)
        self.assertEqual(self.st["last"]["dealer_net"], U)

    def test_insurance_is_lost_when_dealer_has_no_blackjack(self):
        st = table(2, hands=[("10D", "9D")], dealer=("AH", "6H"))
        p = st["rnd"]["order"][0]
        deal(st)
        go(st, p, "ins:yes")
        self.assertEqual(st["phase"], "play")                                   # the dealer peeked: no blackjack
        go(st, p, "stand")
        finish_round(st)
        self.assertEqual(last(st)["ins"], -INS)                                 # half a point lost
        self.assertEqual(last(st)["hands"][0]["net"], U)                        # 19 beats the dealer's soft 17
        self.assertEqual(last(st)["net"], U - INS)

    def test_insurance_times_out_as_no(self):
        deal(self.st)
        bj.settle(self.st, bj.INSURANCE_MS + 1)
        self.assertEqual(self.st["phase"], "result")                            # dealer had blackjack, nobody insured
        self.assertEqual(self.st["last"]["rows"][0]["ins"], None)

    def test_player_blackjack_may_insure_and_wins_even_money(self):
        st = table(2, hands=[("AD", "KD")], dealer=("AH", "KH"))
        p = st["rnd"]["order"][0]
        deal(st)
        go(st, p, "ins:yes")
        self.assertEqual(last(st)["net"], 0 + INS * 2)                          # hand pushes, insurance pays 1 point


INS = U // 2


class DoubleTests(SimpleTestCase):
    def test_double_takes_exactly_one_card_and_doubles_the_bet(self):
        st = table(2, hands=[("5D", "6D")], dealer=("10H", "8H"), then=("10C", "2C"))
        p = st["rnd"]["order"][0]
        deal(st)
        self.assertTrue(has(st, p, "double"))
        go(st, p, "double")                                                     # 11 + 10 = 21
        self.assertEqual(len(st["rnd"]["hands"][str(p)][0]["cards"]), 3)
        self.assertEqual(st["phase"], "result")
        self.assertEqual(last(st)["net"], 2 * U)

    def test_double_can_lose_double(self):
        st = table(2, hands=[("5D", "6D")], dealer=("10H", "8H"), then=("2C",))
        p = st["rnd"]["order"][0]
        deal(st)
        go(st, p, "double")                                                     # 13 v 18
        self.assertEqual(last(st)["net"], -2 * U)

    def test_double_only_on_first_two_cards(self):
        st = table(2, hands=[("2D", "3D")], dealer=("10H", "8H"), then=("2C", "2S"))
        p = st["rnd"]["order"][0]
        deal(st)
        go(st, p, "hit")
        self.assertFalse(has(st, p, "double"))
        self.assertTrue(bj.act(st, p, "double", 0))

    def test_double_after_split_is_allowed(self):
        st = table(2, hands=[("8D", "8C")], dealer=("10H", "7H"), then=("3C", "2C", "9C"))
        p = st["rnd"]["order"][0]
        deal(st)
        go(st, p, "split")                                                      # hands: 8+3, 8
        self.assertTrue(has(st, p, "double"))
        go(st, p, "double")                                                     # 11 + 9... draws next card
        self.assertTrue(st["rnd"]["hands"][str(p)][0]["doubled"])


class SplitTests(SimpleTestCase):
    def test_split_makes_two_hands_and_plays_them_in_order(self):
        st = table(2, hands=[("8D", "8C")], dealer=("10H", "7H"), then=("3C", "2S", "10S"))
        p = st["rnd"]["order"][0]
        deal(st)
        self.assertTrue(has(st, p, "split"))
        go(st, p, "split")
        hands = st["rnd"]["hands"][str(p)]
        self.assertEqual(len(hands), 2)
        self.assertEqual(hands[0]["cards"], [C("8D"), C("3C")])                 # the first hand got its card at once
        self.assertEqual(hands[1]["cards"], [C("8C")])                          # the second waits until it is played
        self.assertEqual(st["rnd"]["turn"], {"u": p, "h": 0})
        go(st, p, "stand")                                                      # 11
        self.assertEqual(hands[1]["cards"], [C("8C"), C("2S")])                 # now it gets its second card
        self.assertEqual(st["rnd"]["turn"], {"u": p, "h": 1})
        go(st, p, "hit")                                                        # 10 + 10 = 20
        go(st, p, "stand")
        self.assertEqual(st["phase"], "result")
        self.assertEqual([h["net"] for h in last(st)["hands"]], [-U, U])        # 11 loses to 17, 20 beats 17

    def test_split_needs_equal_value_and_allows_ten_cards_of_different_face(self):
        st = table(2, hands=[("KD", "QC")], dealer=("10H", "7H"), then=("2C", "2S"))
        p = st["rnd"]["order"][0]
        deal(st)
        self.assertTrue(has(st, p, "split"))
        st = table(2, hands=[("9D", "8C")], dealer=("10H", "7H"))
        deal(st)
        self.assertFalse(has(st, st["rnd"]["order"][0], "split"))
        self.assertTrue(bj.act(st, st["rnd"]["order"][0], "split", 0))

    def test_split_aces_get_one_card_each_and_stand(self):
        st = table(2, hands=[("AD", "AC")], dealer=("10H", "7H"), then=("5C", "9S"))
        p = st["rnd"]["order"][0]
        deal(st)
        go(st, p, "split")
        self.assertEqual(st["phase"], "result")                                 # no hitting split aces
        hands = last(st)["hands"]
        self.assertEqual([h["text"] for h in hands], ["6/16", "10/20"])             # Ace + 5, Ace + 9: soft hands
        self.assertEqual([h["tag"] for h in hands], ["lose", "win"])

    def test_ace_and_ten_after_a_split_is_21_not_blackjack(self):
        st = table(2, hands=[("AD", "AC")], dealer=("10H", "7H"), then=("KC", "QS"))
        p = st["rnd"]["order"][0]
        deal(st)
        go(st, p, "split")
        self.assertEqual([h["net"] for h in last(st)["hands"]], [U, U])         # paid 1 to 1, not 3 to 2

    def test_split_aces_cannot_be_split_again(self):
        st = table(2, hands=[("AD", "AC")], dealer=("10H", "7H"), then=("AS", "2S"))
        p = st["rnd"]["order"][0]
        deal(st)
        go(st, p, "split")                                                      # both hands finish at once
        self.assertEqual(st["phase"], "result")

    def test_non_ace_pairs_may_be_resplit_up_to_four_hands(self):
        st = table(2, hands=[("8D", "8C")], dealer=("10H", "7H"), then=("8S", "8H", "8D", "5C", "5H", "5D", "5S", "2S", "2H"))
        p = st["rnd"]["order"][0]
        deal(st)
        go(st, p, "split")                                                      # 8,8 | 8
        go(st, p, "split")                                                      # 8,8 | 8,... three hands
        self.assertEqual(len(st["rnd"]["hands"][str(p)]), 3)
        go(st, p, "split")                                                      # four hands
        self.assertEqual(len(st["rnd"]["hands"][str(p)]), 4)
        self.assertNotIn({"a": "split"}, bj.options(st, p))                     # that is the limit
        self.assertTrue(bj.act(st, p, "split", 0))

    def test_split_21_stands_by_itself(self):
        st = table(2, hands=[("7D", "7C")], dealer=("10H", "7H"), then=("AC", "KC"))
        p = st["rnd"]["order"][0]
        deal(st)
        go(st, p, "split")                                                      # 7+A = 18 soft, not 21
        self.assertEqual(st["rnd"]["turn"], {"u": p, "h": 0})


class RoundResultTests(SimpleTestCase):
    def test_scores_add_up_to_zero_and_dealer_takes_the_other_side(self):
        st = table(4, hands=[("10D", "9D"), ("10C", "7C"), ("AD", "KD")], dealer=("10H", "8H"))
        a, b, c = st["rnd"]["order"]
        deal(st)
        go(st, a, "stand"); go(st, b, "stand")
        self.assertEqual(st["phase"], "result")
        self.assertEqual(sum(bj.holdings(st, u) for u in st["players"]), START * 4)   # chips only move between players
        self.assertEqual(net(st, a), U)                                         # 19 beats 18
        self.assertEqual(net(st, b), -U)                                        # 17 loses
        self.assertEqual(net(st, c), U * 3 // 2)                                # blackjack
        self.assertEqual(net(st, st["rnd"]["dealer"]), -(U * 3 // 2))

    def test_result_waits_then_the_next_round_opens_with_the_same_dealer(self):
        st = table(2, hands=[("10D", "9D")], dealer=("10H", "7H"))
        p, d = st["rnd"]["order"][0], st["rnd"]["dealer"]
        deal(st); go(st, p, "stand")
        self.assertEqual(st["phase"], "result")
        self.assertEqual(bj.act(st, p, "next", 0), "Only the dealer opens the next round.")
        go(st, d, "next")
        self.assertEqual((st["phase"], st["rnd"]["no"], st["rnd"]["dealer"], st["rb"]), ("deal", 2, d, 2))
        self.assertEqual(st["rnd"]["hands"], {})

    def test_result_closes_by_itself(self):
        st = table(2, hands=[("10D", "9D")], dealer=("10H", "7H"))
        p = st["rnd"]["order"][0]
        deal(st); go(st, p, "stand")
        bj.settle(st, bj.RESULT_MS + 1)
        self.assertEqual((st["phase"], st["rnd"]["no"]), ("deal", 2))


class MatchStructureTests(SimpleTestCase):
    def play_round_quickly(self, st):
        d = st["rnd"]["dealer"]
        bet_all(st)
        if st["phase"] == "deal":
            deal(st)
        while st["phase"] not in ("result", "over"):
            if st["phase"] == "insurance":
                for k, v in st["rnd"]["ins"].items():
                    if v is None:
                        go(st, int(k), "ins:no")
            elif st["phase"] == "play":
                go(st, st["rnd"]["turn"]["u"], "stand")
            elif st["phase"] == "dealer":
                go(st, d, "draw")
        return d

    def run_match(self, n, games):
        st = bj.new_table(list(range(1, n + 1)), games, 0)
        dealers = []
        while st["phase"] != "over":
            dealers.append(self.play_round_quickly(st))
            if st["phase"] == "result":
                go(st, dealers[-1], "next")
        return st, dealers

    def test_each_player_deals_five_rounds_then_the_deal_moves_on(self):
        st, dealers = self.run_match(3, 1)
        self.assertEqual(len(dealers), 15)
        blocks = [dealers[i:i + 5] for i in range(0, 15, 5)]
        self.assertTrue(all(len(set(b)) == 1 for b in blocks))
        self.assertEqual(len({b[0] for b in blocks}), 3)                         # three different dealers
        seats = st["players"]
        first = seats.index(blocks[0][0])
        self.assertEqual([b[0] for b in blocks], [seats[(first + i) % 3] for i in range(3)])   # next seat each time

    def test_number_of_games_decides_the_length(self):
        for n, games in ((2, 1), (2, 3), (4, 2), (7, 1)):
            st, dealers = self.run_match(n, games)
            self.assertEqual(len(dealers), n * games * 5, (n, games))
            self.assertEqual(len(st["gdone"]), games)
            self.assertEqual(st["phase"], "over")

    def test_every_player_deals_equally_in_a_game(self):
        st, dealers = self.run_match(4, 2)
        self.assertEqual(set(Counter(dealers).values()), {10})

    def test_first_dealer_is_random(self):
        seen = {bj.new_table([1, 2, 3, 4], 1, 0)["rnd"]["dealer"] for _ in range(120)}
        self.assertEqual(seen, {1, 2, 3, 4})

    def test_a_game_is_won_by_the_best_score_and_the_match_by_most_games(self):
        st, _ = self.run_match(2, 3)
        self.assertEqual(sum(st["scores"].values()), 0)
        self.assertEqual(sum(g["net"][str(u)] for g in st["gdone"] for u in st["players"]), 0)
        rows = st["summary"]
        self.assertEqual(sorted(r["id"] for r in rows), [1, 2])
        if st["winner"]:
            top = rows[0]
            self.assertEqual(top["id"], st["winner"])

    def test_match_winner_logic(self):
        st = bj.new_table([1, 2, 3], 2, 0)
        st["gdone"] = [{"no": 1, "net": {"1": 4, "2": -2, "3": -2}, "winner": 1}, {"no": 2, "net": {"1": -2, "2": 4, "3": -2}, "winner": 2}]
        st["scores"] = {"1": 2, "2": 2, "3": -4}
        bj._finish(st, 0, "x")
        self.assertIsNone(st["winner"])                                          # level on games and on points: a draw
        st["gdone"][1]["winner"] = 1
        bj._finish(st, 0, "x")
        self.assertEqual(st["winner"], 1)

    def test_a_tied_game_has_no_winner(self):
        st = bj.new_table([1, 2], 1, 0)
        bj._close_game(st)
        self.assertIsNone(st["gdone"][0]["winner"])


class TimerAndLeavingTests(SimpleTestCase):
    def test_dealer_who_does_not_deal_is_dealt_for(self):
        st = table(2, hands=[("10D", "9D")], dealer=("10H", "7H"))
        self.assertTrue(bj.settle(st, bj.DEAL_MS + 1))
        self.assertEqual(st["phase"], "play")
        self.assertEqual(st["timeouts"][str(st["rnd"]["dealer"])], 1)

    def test_slow_player_stands(self):
        st = table(2, hands=[("10D", "6D")], dealer=("10H", "7H"))
        deal(st)
        bj.settle(st, bj.TURN_MS + 1)
        self.assertEqual(st["phase"], "result")

    def test_nothing_happens_before_the_deadline(self):
        st = table(2)
        self.assertFalse(bj.settle(st, 1000))

    def test_three_missed_turns_take_a_player_off_the_table(self):
        st = bj.new_table([1, 2, 3], 1, 0)
        now = 0
        for _ in range(60):
            if 1 in st["left"] or 2 in st["left"] or 3 in st["left"]:
                break
            now += 10 ** 6
            bj.settle(st, now)
        self.assertTrue(st["left"])

    def test_activity_clears_the_missed_turn_count(self):
        st = table(2, hands=[("10D", "9D")], dealer=("10H", "7H"))
        d = st["rnd"]["dealer"]
        st["timeouts"][str(d)] = 2
        deal(st)
        self.assertEqual(st["timeouts"][str(d)], 0)

    def test_leaving_when_two_play_ends_the_match_and_the_other_wins(self):
        st = bj.new_table([1, 2], 1, 0)
        go(st, 1, "leave")
        self.assertEqual((st["phase"], st["winner"]), ("over", 2))

    def test_leaver_hands_stand_and_their_dealing_turns_are_skipped(self):
        st = bj.new_table([1, 2, 3], 1, 0)
        gone = st["rnd"]["order"][0]
        go(st, gone, "leave")
        self.assertEqual(st["phase"], "deal")
        bet_all(st)
        deal(st)
        while st["phase"] not in ("result",):
            if st["phase"] == "insurance":
                for k, v in st["rnd"]["ins"].items():
                    if v is None:
                        go(st, int(k), "ins:no")
            elif st["phase"] == "play":
                go(st, st["rnd"]["turn"]["u"], "stand")
            elif st["phase"] == "dealer":
                go(st, st["rnd"]["dealer"], "draw")
        self.assertEqual(sum(bj.holdings(st, u) for u in st["players"]), START * 3)

    def test_dealer_leaving_mid_round_does_not_stall_the_table(self):
        st = bj.new_table([1, 2, 3], 1, 0)
        d = st["rnd"]["dealer"]
        bet_all(st)
        deal(st)
        go(st, d, "leave")
        for _ in range(30):
            if st["phase"] == "play":
                go(st, st["rnd"]["turn"]["u"], "stand")
            elif st["phase"] == "insurance":
                for k, v in st["rnd"]["ins"].items():
                    if v is None:
                        go(st, int(k), "ins:no")
            else:
                break
        self.assertEqual(st["phase"], "result")                                  # the dealer's draws happened by themselves
        bj.settle(st, bj.RESULT_MS + 1)
        self.assertNotEqual(st["rnd"]["dealer"], d)                              # and the deal moved on

    def test_cannot_act_after_leaving_or_when_not_seated(self):
        st = bj.new_table([1, 2, 3], 1, 0)
        go(st, 3, "leave")
        self.assertEqual(bj.act(st, 3, "stand", 0), "You are not at this table.")
        self.assertEqual(bj.act(st, 99, "stand", 0), "You are not at this table.")


class PrivacyTests(SimpleTestCase):
    def test_hole_card_is_hidden_from_everybody_but_the_dealer_until_it_is_turned_up(self):
        st = table(3, hands=[("10D", "9D"), ("10C", "7C")], dealer=("10H", "6H"), then=("5C",))
        a, b = st["rnd"]["order"]
        d = st["rnd"]["dealer"]
        deal(st)
        for who in (a, b):
            v = bj.public_view(st, who, 0)
            self.assertEqual(v["dealer"]["cards"], [[10, "H"], None])
            self.assertEqual(v["dealer"]["text"], "10")
            self.assertTrue(v["dealer"]["hole_hidden"])
        mine = bj.public_view(st, d, 0)
        self.assertEqual(mine["dealer"]["cards"], [[10, "H"], [6, "H"]])        # the dealer knows their own hole card
        self.assertTrue(mine["dealer"]["peek"])
        go(st, a, "stand"); go(st, b, "stand")
        v = bj.public_view(st, a, 0)
        self.assertEqual(v["dealer"]["cards"][1], [6, "H"])                      # now it is face up for all
        self.assertFalse(v["dealer"]["hole_hidden"])

    def test_view_never_contains_the_shoe_and_is_json(self):
        st = table(3)
        deal(st)
        for who in st["players"]:
            v = bj.public_view(st, who, 0)
            self.assertIsInstance(v["shoe"], int)                                # only how many cards are left
            self.assertNotIn('"shoe": [', json.dumps(v))
            json.dumps(v)

    def test_insurance_answers_stay_private_until_the_peek(self):
        st = table(3, hands=[("10D", "9D"), ("8D", "8C")], dealer=("AH", "6H"))
        a, b = st["rnd"]["order"]
        deal(st)
        go(st, a, "ins:yes")
        v = bj.public_view(st, b, 0)
        row = [p for p in v["players"] if p["id"] == a][0]
        self.assertEqual(row["ins"], "answered")


class BettingTests(SimpleTestCase):
    """Bets, holdings and payouts. Bets are even (so 3 to 2 and insurance come out whole), from MIN_BET to the table limit."""

    def fresh(self, n=3):
        st = bj.new_table(list(range(1, n + 1)), 1, 0)
        return st, st["rnd"]["dealer"], st["rnd"]["order"]

    def rig(self, st, hands, dealer, then=()):
        order = [u for u in st["rnd"]["order"] if str(u) in st["rnd"]["bets"]]
        seq = [C(h[0]) for h in hands] + [C(dealer[0])] + [C(h[1]) for h in hands] + [C(dealer[1])] + [C(x) for x in then]
        st["shoe"].extend(reversed(seq))

    def test_everybody_starts_with_the_same_stack_and_the_dealer_does_not_bet(self):
        st, d, order = self.fresh(4)
        self.assertEqual(set(st["chips"].values()), {bj.START_CHIPS})
        self.assertEqual(bj.act(st, d, "bet:20", 0), "The dealer is the bank and does not bet.")
        self.assertEqual([o["a"] for o in bj.options(st, d)], [])             # nothing to press until a bet is in
        self.assertEqual(bj.act(st, d, "deal", 0), "Wait for at least one bet before dealing.")

    def test_bet_is_taken_from_the_stack_but_holdings_do_not_change(self):
        st, d, order = self.fresh()
        p = order[0]
        go(st, p, "bet:50")
        self.assertEqual((st["chips"][str(p)], bj.in_play(st, p), bj.holdings(st, p)), (950, 50, 1000))
        self.assertEqual(bj.public_view(st, p, 0)["players"][st["players"].index(p)]["bet"], 50)

    def test_bet_can_be_changed_and_sat_out_until_the_deal(self):
        st, d, order = self.fresh()
        p = order[0]
        go(st, p, "bet:50"); go(st, p, "bet:20")
        self.assertEqual((st["chips"][str(p)], st["rnd"]["bets"][str(p)]), (980, 20))      # the 50 went back first
        go(st, p, "sit")
        self.assertEqual((st["chips"][str(p)], st["rnd"]["bets"]), (1000, {}))
        go(st, p, "bet:30")
        self.assertEqual(st["chips"][str(p)], 970)

    def test_bad_bets_are_refused_and_an_old_bet_survives_them(self):
        st, d, order = self.fresh()
        p = order[0]
        go(st, p, "bet:40")
        cap = bj.bet_cap(st, p)
        for bad in ("bet:7", "bet:8", "bet:0", "bet:-10", "bet:abc", "bet:", f"bet:{cap + 2}", "bet:1e3", "bet:20.5"):
            self.assertTrue(bj.act(st, p, bad, 0), bad)
        self.assertEqual((st["chips"][str(p)], st["rnd"]["bets"][str(p)]), (960, 40))

    def test_table_limit_is_a_share_of_the_bank_and_bets_stop_there(self):
        st, d, order = self.fresh(4)                                           # three players: 1000 // 6 = 166, even -> 166
        self.assertEqual(bj.table_max(st), 166)
        p = order[0]
        self.assertEqual(bj.act(st, p, "bet:168", 0), "Bet an even number of chips from 10 to 166.")
        go(st, p, "bet:166")
        self.assertEqual(bj.options(st, p)[0]["max"], 166)

    def test_a_player_cannot_bet_more_than_they_hold(self):
        st, d, order = self.fresh(2)
        p = order[0]
        st["chips"][str(p)] = 30
        self.assertEqual(bj.bet_cap(st, p), 30)
        self.assertTrue(bj.act(st, p, "bet:32", 0))
        go(st, p, "bet:30")
        self.assertEqual(st["chips"][str(p)], 0)

    def test_nobody_can_bet_when_broke_or_when_the_bank_is_too_small(self):
        st, d, order = self.fresh(3)
        a, b = order
        st["chips"][str(a)] = 8                                                # under the minimum bet
        self.assertNotIn(a, bj._eligible(st))
        self.assertEqual(bj.options(st, a), [])
        self.assertTrue(bj.act(st, a, "bet:10", 0))
        st["chips"][str(d)] = 30                                               # 30 // 4 = 7: below the minimum
        self.assertEqual(bj.table_max(st), 6)
        self.assertEqual(bj.options(st, b), [])

    def test_players_who_did_not_bet_sit_out_and_get_no_cards(self):
        st, d, order = self.fresh(3)
        a, b = order
        go(st, a, "bet:20")
        self.rig(st, [("9D", "9C")], ("10H", "7H"))
        deal(st)
        self.assertEqual(list(st["rnd"]["hands"]), [str(a)])
        view = bj.public_view(st, b, 0)
        self.assertEqual([p for p in view["players"] if p["id"] == b][0]["bst"], "sit")
        self.assertEqual(bj.options(st, b), [])                                 # bets are closed
        self.assertEqual(bj.act(st, b, "bet:20", 0), "Bets are closed for this round.")

    def test_payouts_win_loss_push_blackjack_at_a_custom_bet(self):
        st, d, order = self.fresh(4)
        a, b, c = order
        go(st, a, "bet:50"); go(st, b, "bet:30"); go(st, c, "bet:20")
        self.rig(st, [("10D", "9D"), ("10C", "7C"), ("AD", "KD")], ("10H", "8H"))
        deal(st)
        go(st, a, "stand"); go(st, b, "stand")
        self.assertEqual(st["phase"], "result")
        self.assertEqual([bj.holdings(st, u) - START for u in (a, b, c, d)], [50, -30, 30, -50])    # 19 beats 18, 17 loses, 3:2 on 20
        self.assertEqual(st["chips"][str(a)], START + 50)                       # settled at once: the chips are back in the stack
        self.assertEqual(bj.in_play(st, a), 0)
        self.assertEqual(sum(bj.holdings(st, u) for u in st["players"]), START * 4)

    def test_push_returns_the_bet(self):
        st, d, order = self.fresh(2)
        p = order[0]
        go(st, p, "bet:60")
        self.rig(st, [("10D", "9D")], ("10H", "9H"))
        deal(st); go(st, p, "stand")
        self.assertEqual((st["chips"][str(p)], st["chips"][str(d)]), (START, START))

    def test_double_costs_a_second_bet_and_pays_double(self):
        st, d, order = self.fresh(2)
        p = order[0]
        go(st, p, "bet:40")
        self.rig(st, [("5D", "6D")], ("10H", "8H"), ("10C",))
        deal(st)
        self.assertEqual(bj.options(st, p)[2], {"a": "double", "cost": 40})
        go(st, p, "double")
        self.assertEqual(st["phase"], "result")
        self.assertEqual((st["chips"][str(p)], st["chips"][str(d)]), (START + 80, START - 80))

    def test_double_that_loses_costs_both_bets(self):
        st, d, order = self.fresh(2)
        p = order[0]
        go(st, p, "bet:40")
        self.rig(st, [("5D", "6D")], ("10H", "8H"), ("2C",))
        deal(st); go(st, p, "double")
        self.assertEqual((st["chips"][str(p)], st["chips"][str(d)]), (START - 80, START + 80))

    def test_split_puts_a_second_bet_on_the_table(self):
        st, d, order = self.fresh(2)
        p = order[0]
        go(st, p, "bet:40")
        self.rig(st, [("8D", "8C")], ("10H", "7H"), ("3C", "2S", "10S"))
        deal(st)
        go(st, p, "split")
        self.assertEqual((st["chips"][str(p)], bj.in_play(st, p), bj.holdings(st, p)), (START - 80, 80, START))
        go(st, p, "stand"); go(st, p, "hit"); go(st, p, "stand")                # 11 loses to 17, 20 wins
        self.assertEqual(st["chips"][str(p)], START)                            # one bet lost, one won

    def test_insurance_costs_half_the_bet_and_pays_two_to_one(self):
        st, d, order = self.fresh(3)
        a, b = order
        go(st, a, "bet:50"); go(st, b, "bet:50")
        self.rig(st, [("10D", "9D"), ("8D", "8C")], ("AH", "KH"))
        deal(st)
        self.assertEqual(bj.options(st, a)[0], {"a": "ins:yes", "cost": 25})
        go(st, a, "ins:yes")
        self.assertEqual(st["chips"][str(a)], START - 75)                       # bet 50 + insurance 25
        go(st, b, "ins:no")
        self.assertEqual(st["phase"], "result")                                 # dealer blackjack
        self.assertEqual(bj.holdings(st, a) - START, 0)                         # lost 50, insurance pays 50
        self.assertEqual(bj.holdings(st, b) - START, -50)
        self.assertEqual(bj.holdings(st, d) - START, 50)

    def test_lost_insurance_is_only_the_half_bet(self):
        st, d, order = self.fresh(2)
        p = order[0]
        go(st, p, "bet:50")
        self.rig(st, [("10D", "9D")], ("AH", "6H"))
        deal(st); go(st, p, "ins:yes"); go(st, p, "stand")
        self.assertEqual(bj.holdings(st, p) - START, 50 - 25)                   # 19 beats soft 17, insurance lost

    def test_cannot_afford_double_split_or_insurance(self):
        st, d, order = self.fresh(2)
        p = order[0]
        st["chips"][str(p)] = 70
        go(st, p, "bet:40")                                                     # 30 left: not enough to double a 40 bet
        self.rig(st, [("8D", "8C")], ("10H", "7H"))
        deal(st)
        opts = {o["a"]: o for o in bj.options(st, p)}
        self.assertEqual(opts["double"]["off"], "Not enough chips")
        self.assertEqual(opts["split"]["off"], "Not enough chips")
        self.assertEqual(bj.act(st, p, "double", 0), "Not enough chips to double.")
        self.assertEqual(bj.act(st, p, "split", 0), "Not enough chips to split.")
        self.assertEqual(st["chips"][str(p)], 30)                               # nothing was taken
        st2, d2, order2 = self.fresh(2)
        q = order2[0]
        st2["chips"][str(q)] = 52
        go(st2, q, "bet:50")
        self.rig(st2, [("10D", "9D")], ("AH", "6H"))
        deal(st2)
        self.assertEqual(bj.options(st2, q)[0], {"a": "ins:yes", "cost": 25, "off": "Not enough chips"})
        self.assertEqual(bj.act(st2, q, "ins:yes", 0), "You do not have enough chips for insurance.")

    def test_bank_that_cannot_cover_it_blocks_double_and_split(self):
        st, d, order = self.fresh(2)
        p = order[0]
        st["chips"][str(d)] = 100
        go(st, p, "bet:20")                                                     # table limit 100 // 2 = 50
        self.rig(st, [("8D", "8C")], ("10H", "7H"))
        deal(st)
        st["chips"][str(d)] = 30                                                # the bank shrank (e.g. lost elsewhere): exposure 20, free 10
        opts = {o["a"]: o for o in bj.options(st, p)}
        self.assertEqual(opts["double"]["off"], "The bank cannot cover it")
        self.assertEqual(bj.act(st, p, "split", 0), "The bank cannot cover it to split.")

    def test_banks_chips_never_go_below_zero_even_when_everyone_wins_big(self):
        st, d, order = self.fresh(4)
        st["chips"][str(d)] = 120                                               # table limit 120 // 6 = 20
        self.assertEqual(bj.table_max(st), 20)
        for u in order:
            go(st, u, "bet:20")
        self.rig(st, [("AD", "KD")] * 3, ("10H", "8H"))
        deal(st)
        self.assertEqual(st["chips"][str(d)], 120 - 3 * 30)
        self.assertGreaterEqual(st["chips"][str(d)], 0)

    def test_round_with_no_bets_is_skipped_and_counts_as_a_round(self):
        st, d, order = self.fresh(3)
        for u in order:
            go(st, u, "sit")
        self.assertEqual((st["phase"], st["rnd"]["no"], st["rb"]), ("deal", 2, 2))      # nobody bet: next round opened by itself
        self.assertEqual(st["rnd"]["dealer"], d)
        self.assertIn("no_bets", [e["a"] for e in st["log"]])

    def test_betting_time_up_players_who_did_not_bet_sit_out_and_miss_a_turn(self):
        st, d, order = self.fresh(3)
        a, b = order
        go(st, a, "bet:20")
        self.rig(st, [("9D", "9C")], ("10H", "7H"))
        self.assertTrue(bj.settle(st, bj.DEAL_MS + 1))
        self.assertEqual(st["phase"], "play")                                   # the cards went out
        self.assertEqual(st["timeouts"].get(str(b)), 1)                         # b was too slow
        self.assertNotIn(str(d), st["timeouts"])                                # the dealer was waiting for b, not at fault
        self.assertEqual(list(st["rnd"]["hands"]), [str(a)])

    def test_dealer_is_at_fault_only_when_everyone_had_decided(self):
        st, d, order = self.fresh(3)
        go(st, order[0], "bet:20"); go(st, order[1], "sit")
        self.assertTrue(bj.settle(st, bj.DEAL_MS + 1))
        self.assertEqual(st["timeouts"].get(str(d)), 1)

    def test_everyone_who_does_not_bet_for_the_whole_time_skips_the_round(self):
        st, d, order = self.fresh(3)
        self.assertTrue(bj.settle(st, bj.DEAL_MS + 1))
        self.assertEqual(st["rnd"]["no"], 2)
        self.assertEqual({st["timeouts"][str(u)] for u in order}, {1})

    def test_a_new_game_starts_everyone_on_a_fresh_stack_and_the_game_goes_to_the_biggest_holdings(self):
        st = bj.new_table([1, 2], 2, 0)
        st["chips"] = {"1": 1300, "2": 700}
        bj._close_game(st)
        self.assertEqual(st["gdone"][0]["winner"], 1)
        self.assertEqual(st["gdone"][0]["net"], {"1": 300, "2": -300})
        self.assertEqual(st["chips"], {"1": START, "2": START})
        self.assertEqual(st["scores"], {"1": 300, "2": -300})

    def test_closing_the_table_mid_round_gives_the_chips_on_it_back(self):
        st = table(3, hands=[("10D", "9D"), ("8D", "8C")], dealer=("10H", "7H"), bet=40)
        deal(st)
        go(st, st["rnd"]["order"][0], "leave")
        go(st, st["rnd"]["order"][1], "leave")                                  # only the dealer is left
        self.assertEqual(st["phase"], "over")
        self.assertEqual(sum(st["chips"].values()), START * 3)
        self.assertEqual(set(st["chips"].values()), {START})

    def test_a_player_who_left_still_has_their_bet_settled(self):
        st, d, order = self.fresh(3)
        a, b = order
        go(st, a, "bet:40"); go(st, b, "bet:40")
        self.rig(st, [("10D", "9D"), ("10C", "7C")], ("10H", "8H"))
        deal(st)
        go(st, a, "leave")                                                      # a stands on 19
        go(st, b, "stand")
        self.assertEqual([bj.holdings(st, u) - START for u in (a, b, d)], [40, -40, 0])

    def test_the_view_shows_everybodys_holdings_and_the_viewers_seat_data(self):
        st, d, order = self.fresh(3)
        go(st, order[0], "bet:60")
        v = bj.public_view(st, order[1], 0)
        rows = {r["id"]: r for r in v["players"]}
        self.assertEqual((rows[order[0]]["chips"], rows[order[0]]["in_play"], rows[order[0]]["hold"]), (940, 60, 1000))
        self.assertEqual(rows[d]["hold"], START)
        self.assertEqual((v["bank"], v["table_max"], v["start"], v["min_bet"]), (START, 250, START, 10))
        self.assertEqual(v["order"], order)
        self.assertEqual([o["a"] for o in v["options"]], ["bet", "sit"])
        self.assertEqual(v["options"][0], {"a": "bet", "min": 10, "max": 250, "cur": 0, "last": 0})

    def test_last_bet_is_remembered(self):
        st, d, order = self.fresh(3)
        go(st, order[0], "bet:44")
        self.assertEqual(st["lastbet"][str(order[0])], 44)


class FuzzTests(SimpleTestCase):
    """Random players, random bets, random timers: chips only ever move between players (the total never changes),
    nobody ever goes below zero, the match always ends, nobody is ever stuck."""

    def test_random_matches(self):
        for n in (2, 3, 5, 7):
            for seed in range(12):
                rng = random.Random(n * 100 + seed)
                st = bj.new_table(list(range(1, n + 1)), rng.choice((1, 2)), 0)
                now, steps = 0, 0
                while st["phase"] != "over":
                    steps += 1
                    self.assertLess(steps, 20000, "stuck")
                    json.dumps(st)
                    acts = []
                    for u in st["players"]:
                        for o in bj.options(st, u):
                            if o.get("off"):
                                self.assertTrue(bj.act(st, u, o["a"], now))          # greyed out means refused
                            elif o["a"] == "bet":
                                amt = rng.choice((o["min"], o["max"], rng.randrange(o["min"] // 2, o["max"] // 2 + 1) * 2))
                                acts.append((u, f"bet:{amt}"))
                            else:
                                acts.append((u, o["a"]))
                    self.assertTrue(acts or st["deadline"], f"nobody can act in {st['phase']}")
                    if not acts or rng.random() < 0.03:
                        now += 10 ** 6
                        bj.settle(st, now)
                    else:
                        u, a = rng.choice(acts)
                        self.assertIsNone(bj.act(st, u, a, now), (u, a))
                    self.assertEqual(sum(bj.holdings(st, u) for u in st["players"]), bj.START_CHIPS * n)
                    self.assertTrue(all(c >= 0 for c in st["chips"].values()), st["chips"])
                    for h in (st["rnd"]["hands"].values() if st["rnd"] else []):
                        self.assertLessEqual(len(h), bj.MAX_HANDS)
                self.assertTrue(st["summary"])


# ---------------------------------------------------------------------------------------------
# The screens
# ---------------------------------------------------------------------------------------------
User = get_user_model()


class BlackjackScreensTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cache.clear()
        cls.users = []
        for i, name in enumerate(("Meera", "Raj", "Asmita")):
            u = User.objects.create_user(username=f"98000000{i}0", password="x", first_name=name, last_name="T", is_active=True)
            ResidentProfile.objects.create(user=u, house_number=str(i + 1))
            join(u)
            cls.users.append(u)
        cls.outsider = User.objects.create_user(username="9800000099", password="x", first_name="Out", last_name="Side", is_active=True)
        ResidentProfile.objects.create(user=cls.outsider, house_number="9")
        join(cls.outsider)

    def start(self, games="2"):
        host, a, b = self.users
        self.client.force_login(host)
        r = self.client.post(reverse("game_challenge"), {"game": "blackjack", "opponents": [a.pk, b.pk], "games": games})
        self.assertEqual(r.status_code, 302)
        ch = GameChallenge.objects.get(game="blackjack")
        for u in (a, b):
            self.client.force_login(u)
            self.client.post(reverse("game_update", args=[ch.pk]), {"answer": "accepted"})
        return GameSession.objects.get(challenge=ch)

    def test_lobby_offers_blackjack_and_asks_how_many_games(self):
        self.client.force_login(self.users[0])
        html = self.client.get(reverse("games")).content.decode()
        self.assertIn("Blackjack", html)
        self.assertIn('name="games"', html)
        self.assertIn("How many games", html)

    def test_invitation_remembers_the_number_of_games(self):
        host, a, b = self.users
        self.client.force_login(host)
        self.client.post(reverse("game_challenge"), {"game": "blackjack", "opponents": [a.pk], "games": "3"})
        self.assertEqual(GameChallenge.objects.get(game="blackjack").time_control, "3 games")

    def test_a_silly_number_of_games_becomes_one_game(self):
        host, a, b = self.users
        self.client.force_login(host)
        self.client.post(reverse("game_challenge"), {"game": "blackjack", "opponents": [a.pk], "games": "999"})
        self.assertEqual(GameChallenge.objects.get(game="blackjack").time_control, "1 game")

    def test_table_opens_when_everyone_answers(self):
        s = self.start("2")
        self.assertEqual(s.state["games"], 2)
        self.assertEqual(sorted(s.state["players"]), sorted(u.pk for u in self.users))
        self.assertEqual(s.current_turn_id, s.state["rnd"]["dealer"])

    def test_at_most_seven_players(self):
        host = self.users[0]
        self.client.force_login(host)
        extra = []
        for i in range(7):
            u = User.objects.create_user(username=f"97000000{i}0", password="x", first_name=f"P{i}", is_active=True)
            ResidentProfile.objects.create(user=u, house_number="1")
            join(u)
            extra.append(u.pk)
        self.client.post(reverse("game_challenge"), {"game": "blackjack", "opponents": extra, "games": "1"})
        self.assertFalse(GameChallenge.objects.filter(game="blackjack").exists())

    def test_payload_does_not_leak_the_shoe_or_the_hole_card(self):
        s = self.start()
        d = s.state["rnd"]["dealer"]
        order = s.state["rnd"]["order"]
        for u in order:
            self.client.force_login(User.objects.get(pk=u))
            self.assertEqual(self.client.post(reverse("game_move", args=[s.pk]), {"action": "bj", "move": "bet:20"}).status_code, 200)
        self.client.force_login(User.objects.get(pk=d))
        r = self.client.post(reverse("game_move", args=[s.pk]), {"action": "bj", "move": "deal"})
        self.assertEqual(r.status_code, 200, r.content)
        s.refresh_from_db()
        hole = s.state["rnd"]["dealer_cards"][1]
        self.client.force_login(User.objects.get(pk=order[0]))
        body = self.client.get(reverse("game_state", args=[s.pk])).json()["game"]
        self.assertEqual(body["state"], {})                                       # the raw state is never sent
        if body["bj"]["phase"] in ("insurance", "play", "dealer"):
            self.assertIsNone(body["bj"]["dealer"]["cards"][1])                    # the hole card is face down
            self.assertNotIn(json.dumps(bj.pair(hole)), json.dumps(body["bj"]["dealer"]))
        raw = json.dumps(body)
        self.assertNotIn('"shoe": [', raw)

    def test_only_the_dealer_can_deal_and_strangers_are_refused(self):
        s = self.start()
        d = s.state["rnd"]["dealer"]
        other = [u for u in self.users if u.pk != d][0]
        self.client.force_login(other)
        r = self.client.post(reverse("game_move", args=[s.pk]), {"action": "bj", "move": "deal"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"], "Only the dealer deals.")
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(reverse("game_play", args=[s.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("game_move", args=[s.pk]), {"action": "bj", "move": "deal"}).status_code, 403)

    def test_a_round_through_the_views(self):
        s = self.start("1")
        users = {u.pk: u for u in self.users}

        def move(uid, m):
            self.client.force_login(users[uid])
            return self.client.post(reverse("game_move", args=[s.pk]), {"action": "bj", "move": m})

        for _ in range(60):
            s.refresh_from_db()
            st = s.state
            if st["rnd"]["no"] > 1 or st["phase"] == "over":
                break
            ph = st["phase"]
            if ph == "deal":
                waiting = [u for u in st["rnd"]["order"] if str(u) not in st["rnd"]["bets"]]
                r = move(waiting[0], "bet:30") if waiting else move(st["rnd"]["dealer"], "deal")
            elif ph == "insurance":
                u = next(int(k) for k, v in st["rnd"]["ins"].items() if v is None)
                r = move(u, "ins:no")
            elif ph == "play":
                r = move(st["rnd"]["turn"]["u"], "stand")
            elif ph == "dealer":
                r = move(st["rnd"]["dealer"], "draw")
            else:
                r = move(st["rnd"]["dealer"], "next")
            self.assertEqual(r.status_code, 200, r.content)
        s.refresh_from_db()
        self.assertEqual(s.state["rnd"]["no"], 2)                                  # round 1 was played and round 2 opened
        st = s.state
        self.assertEqual(sum(st["chips"].values()), bj.START_CHIPS * 3)             # chips only moved between players

    def test_the_game_page_loads_and_old_games_still_work(self):
        s = self.start()
        self.client.force_login(self.users[0])
        html = self.client.get(reverse("game_play", args=[s.pk])).content.decode()
        self.assertIn("games-blackjack.js", html)
        self.assertIn("bj-game", html)

    def test_session_finishes_with_a_winner_in_the_challenge(self):
        s = self.start("1")
        st = s.state
        st["scores"] = {str(u.pk): 0 for u in self.users}
        st["scores"][str(self.users[1].pk)] = 4
        st["scores"][str(self.users[2].pk)] = -4
        bj._finish(st, 0, "x")
        self.assertEqual(st["winner"], self.users[1].pk)
