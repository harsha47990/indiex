"""
game_engine/teen_patti.py — Server-side Teen Patti multiplayer engine
═════════════════════════════════════════════════════════════════════
All game logic runs here. Clients only send actions; server validates
everything and broadcasts state.
"""

import bisect
import random
import string
import time
import logging
from collections import Counter
from itertools import combinations
from enum import IntEnum
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

COMMISSION_RATE = 0.05  # 5% house commission
TURN_TIMEOUT = 90       # seconds per turn
MAX_ROOM_PLAYERS = 8    # max players + waiting per room
AK47_JOKER_RANKS = ["A", "K", "4", "7"]  # fixed wild ranks for AK47 mode


def _add_to_pot(room, cost: int):
    """Add bet to pot. Commission is taken at end of round."""
    room.pot += cost


# ═══════════════════════════════════════════════════════════════════════════
#  CARD / DECK
# ═══════════════════════════════════════════════════════════════════════════

SUITS = ["♠", "♥", "♦", "♣"]
RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]
RANK_VALUE = {r: i for i, r in enumerate(RANKS)}   # 2=0 … A=12


def new_deck() -> list[dict]:
    deck = [{"rank": r, "suit": s} for s in SUITS for r in RANKS]
    random.shuffle(deck)
    return deck


# ═══════════════════════════════════════════════════════════════════════════
#  HAND RANKING  (3-card poker / Teen Patti rules)
# ═══════════════════════════════════════════════════════════════════════════

class HandRank(IntEnum):
    HIGH_CARD      = 1
    PAIR           = 2
    FLUSH          = 3
    STRAIGHT       = 4
    STRAIGHT_FLUSH = 5
    TRAIL          = 6   # three of a kind


HAND_NAMES = {
    HandRank.HIGH_CARD:      "🃏 High Card",
    HandRank.PAIR:           "👯 Pair",
    HandRank.FLUSH:          "💎 Flush",
    HandRank.STRAIGHT:       "📈 Straight",
    HandRank.STRAIGHT_FLUSH: "✨ Straight Flush",
    HandRank.TRAIL:          "🔥 Trail",
}


def _sort_vals(cards: list[dict]) -> list[int]:
    return sorted([RANK_VALUE[c["rank"]] for c in cards], reverse=True)


def evaluate_hand(cards: list[dict]) -> tuple[int, list[int]]:
    """Return (HandRank, tiebreaker_list) for a 3-card hand."""
    vals = _sort_vals(cards)
    suits = [c["suit"] for c in cards]
    is_flush = len(set(suits)) == 1

    # Check straight (including A-2-3 wrap)
    is_straight = False
    straight_vals = vals
    if vals[0] - vals[2] == 2 and vals[0] - vals[1] == 1:
        is_straight = True
    elif vals == [12, 1, 0]:  # A-2-3
        is_straight = True
        straight_vals = [12, 1, 0]  # A-2-3 ranks just below A-K-Q but above K-Q-J

    # Trail
    if vals[0] == vals[1] == vals[2]:
        return (HandRank.TRAIL, vals)

    # Straight flush
    if is_straight and is_flush:
        return (HandRank.STRAIGHT_FLUSH, straight_vals)

    # Straight
    if is_straight:
        return (HandRank.STRAIGHT, straight_vals)

    # Flush
    if is_flush:
        return (HandRank.FLUSH, vals)

    # Pair
    if vals[0] == vals[1]:
        return (HandRank.PAIR, [vals[0], vals[2]])
    if vals[1] == vals[2]:
        return (HandRank.PAIR, [vals[1], vals[0]])
    if vals[0] == vals[2]:
        return (HandRank.PAIR, [vals[0], vals[1]])

    # High card
    return (HandRank.HIGH_CARD, vals)


def evaluate_hand_joker(cards: list[dict], joker_rank: str) -> tuple[int, list[int]]:
    """Evaluate the best possible hand when cards matching joker_rank are wild."""
    joker_indices = [i for i, c in enumerate(cards) if c["rank"] == joker_rank]
    if not joker_indices:
        return evaluate_hand(cards)
    n = len(joker_indices)
    if n == 3:
        return (HandRank.TRAIL, [12, 12, 12])   # Trail of Aces
    all_cards = [{
        "rank": r, "suit": s} for s in SUITS for r in RANKS]
    best: tuple = (0, [])
    if n == 1:
        idx = joker_indices[0]
        for repl in all_cards:
            h = list(cards); h[idx] = repl
            ev = evaluate_hand(h)
            if ev > best:
                best = ev
    else:                                        # n == 2
        i0, i1 = joker_indices
        for r1 in all_cards:
            for r2 in all_cards:
                h = list(cards); h[i0] = r1; h[i1] = r2
                ev = evaluate_hand(h)
                if ev > best:
                    best = ev
    return best


def evaluate_hand_zandu(cards: list[dict], joker_ranks: list[str]) -> tuple[int, list[int]]:
    """Evaluate the best possible hand when cards matching ANY of joker_ranks are wild."""
    joker_indices = [i for i, c in enumerate(cards) if c["rank"] in joker_ranks]
    if not joker_indices:
        return evaluate_hand(cards)
    n = len(joker_indices)
    if n == 3:
        return (HandRank.TRAIL, [12, 12, 12])   # Trail of Aces
    all_cards = [{"rank": r, "suit": s} for s in SUITS for r in RANKS]
    best: tuple = (0, [])
    if n == 1:
        idx = joker_indices[0]
        for repl in all_cards:
            h = list(cards); h[idx] = repl
            ev = evaluate_hand(h)
            if ev > best:
                best = ev
    else:                                        # n == 2
        i0, i1 = joker_indices
        for r1 in all_cards:
            for r2 in all_cards:
                h = list(cards); h[i0] = r1; h[i1] = r2
                ev = evaluate_hand(h)
                if ev > best:
                    best = ev
    return best


def evaluate_hand_2card(cards: list[dict]) -> tuple[int, list[int]]:
    """2-card mode: determine the best 3-card hand achievable with a phantom card.
    Only 2 cards are dealt; the system imagines the best possible 3rd card.
    Trail > Straight Flush > Straight > Flush > Pair  (no High Card possible)."""
    vals = sorted([RANK_VALUE[c["rank"]] for c in cards], reverse=True)
    suits = [c["suit"] for c in cards]
    same_suit = suits[0] == suits[1]
    v_hi, v_lo = vals[0], vals[1]
    gap = v_hi - v_lo

    # Same rank → Trail (phantom duplicates the rank)
    if gap == 0:
        return (HandRank.TRAIL, vals)

    # Can form a straight? gap ≤ 2 OR A-2/A-3 wraps
    can_straight = (1 <= gap <= 2) or (v_hi == 12 and v_lo <= 1)

    if can_straight and same_suit:
        return (HandRank.STRAIGHT_FLUSH, vals)
    if can_straight:
        return (HandRank.STRAIGHT, vals)
    if same_suit:
        return (HandRank.FLUSH, vals)

    # Anything else → Pair (phantom duplicates the higher card)
    return (HandRank.PAIR, vals)


def evaluate_hand_4card(cards: list[dict]) -> tuple[int, list[int]]:
    """4-card mode: pick the best 3-card combo out of 4 dealt cards.
    C(4,3) = 4 possible combinations — evaluate each, return the best."""
    best: tuple = (0, [])
    for combo in combinations(cards, 3):
        ev = evaluate_hand(list(combo))
        if ev > best:
            best = ev
    return best


def hand_name_4card(cards: list[dict]) -> str:
    """Return human-readable hand name for 4-card mode (best 3 of 4)."""
    rank, _ = evaluate_hand_4card(cards)
    return HAND_NAMES.get(rank, "❓ Unknown") + " (4-Card)"


def hand_name_2card(cards: list[dict]) -> str:
    """Human-readable name for a 2-card hand."""
    rank, _ = evaluate_hand_2card(cards)
    return HAND_NAMES.get(rank, "❓ Unknown")


def _evaluate(cards: list[dict], game_type: str = "normal",
              joker_rank: str | None = None,
              joker_ranks: list[str] | None = None) -> tuple[int, list[int]]:
    """Dispatch to the correct evaluator based on game type."""
    if game_type == "2card":
        return evaluate_hand_2card(cards)
    if game_type == "4card":
        return evaluate_hand_4card(cards)
    if game_type == "zandu" and joker_ranks:
        return evaluate_hand_zandu(cards, joker_ranks)
    if game_type == "ak47":
        return evaluate_hand_zandu(cards, AK47_JOKER_RANKS)
    if game_type == "joker" and joker_rank:
        return evaluate_hand_joker(cards, joker_rank)
    return evaluate_hand(cards)


def compare_hands(a: list[dict], b: list[dict],
                  game_type: str = "normal",
                  joker_rank: str | None = None,
                  joker_ranks: list[str] | None = None) -> int:
    """Return  1 if a wins,  -1 if b wins,  0 if draw.
    Joker mode: wild cards considered.  Muflis mode: result inverted."""
    ra, ta = _evaluate(a, game_type, joker_rank, joker_ranks)
    rb, tb = _evaluate(b, game_type, joker_rank, joker_ranks)
    if ra != rb:
        result = 1 if ra > rb else -1
    else:
        result = 0
        for x, y in zip(ta, tb):
            if x != y:
                result = 1 if x > y else -1
                break
    if game_type == "muflis":
        result = -result
    return result


def hand_name(cards: list[dict], game_type: str = "normal",
              joker_rank: str | None = None,
              joker_ranks: list[str] | None = None) -> str:
    if game_type == "2card":
        return hand_name_2card(cards)
    if game_type == "4card":
        return hand_name_4card(cards)
    rank, _ = _evaluate(cards, game_type, joker_rank, joker_ranks)
    label = HAND_NAMES[rank]
    if game_type == "joker" and joker_rank and any(c["rank"] == joker_rank for c in cards):
        label += " (Joker)"
    if game_type == "zandu" and joker_ranks and any(c["rank"] in joker_ranks for c in cards):
        label += " (Joker)"
    if game_type == "ak47" and any(c["rank"] in AK47_JOKER_RANKS for c in cards):
        label += " (Joker)"
    return label


def _hand_score(rank: int, tiebreakers: list[int]) -> int:
    """Convert (HandRank, tiebreakers) → single comparable integer."""
    tb = list(tiebreakers) + [0] * (3 - len(tiebreakers))
    return rank * (13 ** 3) + tb[0] * (13 ** 2) + tb[1] * 13 + tb[2]


def _build_percentile() -> dict[int, int]:
    """Enumerate all C(52,3)=22,100 hands → {score: percentile 0-100}.

    Fixed bands per hand type — each group gets its own range:
      High Card      :  0–55
      Pair           : 55–70
      Flush          : 70–80
      Straight       : 80–90
      Straight Flush : 90–95
      Trail          : 95–100
    Within each band, hands are ranked among their group.
    """
    deck = [{"rank": r, "suit": s} for s in SUITS for r in RANKS]

    # Collect scores per hand type
    groups: dict[int, Counter] = {r: Counter() for r in HandRank}
    for combo in combinations(deck, 3):
        rank, tb = evaluate_hand(list(combo))
        groups[rank][_hand_score(rank, tb)] += 1

    # Band definitions: (HandRank, low%, high%)
    bands = [
        (HandRank.HIGH_CARD,       0,  55),
        (HandRank.PAIR,           55,  70),
        (HandRank.FLUSH,          70,  80),
        (HandRank.STRAIGHT,       80,  90),
        (HandRank.STRAIGHT_FLUSH, 90,  95),
        (HandRank.TRAIL,          95, 100),
    ]

    pct: dict[int, int] = {}
    for hand_rank, lo, hi in bands:
        counts = groups[hand_rank]
        if not counts:
            continue
        total = sum(counts.values())
        band_width = hi - lo
        cum = 0
        sorted_scores = sorted(counts)
        for s in sorted_scores:
            pct[s] = lo + int(cum / total * band_width)
            cum += counts[s]
        # Best hand in this band gets the top value
        pct[sorted_scores[-1]] = hi

    return pct

_PCT = _build_percentile()                # built once at import
_PCT_KEYS = sorted(_PCT)                  # for bisect fallback


def hand_strength_pct(cards: list[dict], game_type: str = "normal",
                      joker_rank: str | None = None,
                      joker_ranks: list[str] | None = None) -> int:
    """Return 0-100 % showing what fraction of all possible hands yours beats.
    For muflis the scale is simply inverted (100 − normal)."""
    if not cards:
        return 0
    rank, tb = _evaluate(cards, game_type, joker_rank, joker_ranks)
    score = _hand_score(rank, tb)
    # Exact lookup, or nearest lower score via bisect
    if score in _PCT:
        pct = _PCT[score]
    else:
        idx = bisect.bisect_right(_PCT_KEYS, score) - 1
        pct = _PCT[_PCT_KEYS[max(0, idx)]] if idx >= 0 else 0
    if game_type == "muflis":
        pct = 100 - pct
    return pct


# ═══════════════════════════════════════════════════════════════════════════
#  PLAYER STATE
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class Player:
    username: str
    coins: int = 0
    cards: list[dict] = field(default_factory=list)
    is_seen: bool = False
    is_folded: bool = False
    is_viewing: bool = False       # mid-turn: peeked at cards, must decide seen/fold
    total_bet: int = 0          # amount this player has put into pot
    is_connected: bool = True
    is_sitting_out: bool = False   # not enough coins to play this round

    def public_dict(self, reveal_cards: bool = False, for_self: bool = False,
                    game_type: str = "normal", joker_rank: str | None = None,
                    joker_ranks: list[str] | None = None) -> dict:
        d = {
            "username": self.username,
            "coins": self.coins,
            "is_seen": self.is_seen,
            "is_folded": self.is_folded,
            "is_viewing": self.is_viewing,
            "total_bet": self.total_bet,
            "is_connected": self.is_connected,
            "is_sitting_out": self.is_sitting_out,
        }
        show_cards = False
        if for_self and self.cards:
            if reveal_cards or self.is_seen or self.is_viewing:
                show_cards = True
            else:
                d["cards"] = [{"rank": "?", "suit": "?"} for _ in self.cards]
        elif reveal_cards and self.cards:
            show_cards = True
        else:
            d["card_count"] = len(self.cards)
        if show_cards:
            d["cards"] = self.cards
            d["hand_name"] = hand_name(self.cards, game_type, joker_rank, joker_ranks)
            d["hand_strength"] = hand_strength_pct(self.cards, game_type, joker_rank, joker_ranks)
        return d


# ═══════════════════════════════════════════════════════════════════════════
#  ROOM STATE
# ═══════════════════════════════════════════════════════════════════════════

class RoomPhase:
    LOBBY   = "lobby"
    PLAYING = "playing"
    RESULT  = "result"


@dataclass
class Room:
    code: str
    admin: str                             # username of room creator
    table_amount: int = 0
    phase: str = RoomPhase.LOBBY
    players: list[Player] = field(default_factory=list)
    pot: int = 0
    current_turn: int = 0                  # index in players list
    round_count: int = 0                   # rounds completed (incremented each full cycle)
    turn_number: int = 0                   # absolute turn number in this deal
    winner: Optional[str] = None
    last_winner_index: int = 0             # for next-game rotation
    deck: list[dict] = field(default_factory=list)
    last_sideshow: Optional[dict] = field(default=None)  # transient reveal data
    last_auto_event: Optional[str] = field(default=None)  # auto-show/fold message
    game_type: str = "normal"                  # "normal", "joker", "muflis", "zandu"
    joker_card: Optional[dict] = field(default=None)  # revealed joker (joker mode)
    zandu_jokers: list[dict] = field(default_factory=list)  # 3 joker cards (zandu mode)
    zandu_revealed: int = 0                      # how many jokers are face-up (1-3)
    mode_picker: str = "admin"                 # "admin" = admin always picks, "winner" = last winner picks
    last_winner_username: Optional[str] = None  # username of last round's winner
    house_commission: int = 0                    # accumulated commission this round
    turn_start_time: float = 0.0                 # Unix ts when current turn started
    round_start_active: int = 0                  # active count when current round started
    round_turns_taken: int = 0                   # turns taken in current round
    waiting_players: list[Player] = field(default_factory=list)  # queued during mid-game

    # ── helpers ─────────────────────────────────────────────
    def player(self, username: str) -> Optional[Player]:
        for p in self.players:
            if p.username == username:
                return p
        return None

    def waiting_player(self, username: str) -> Optional[Player]:
        for p in self.waiting_players:
            if p.username == username:
                return p
        return None

    def any_player(self, username: str) -> Optional[Player]:
        """Lookup in both active players and waiting queue."""
        return self.player(username) or self.waiting_player(username)

    def active_players(self) -> list[Player]:
        return [p for p in self.players if not p.is_folded]

    def active_count(self) -> int:
        return sum(1 for p in self.players if not p.is_folded)

    def _next_active(self, idx: int) -> int:
        n = len(self.players)
        i = (idx + 1) % n
        while i != idx:
            if not self.players[i].is_folded:
                return i
            i = (i + 1) % n
        return idx  # only one left

    def advance_turn(self):
        self.current_turn = self._next_active(self.current_turn)
        self.turn_number += 1
        self.turn_start_time = time.time()
        # Track full rounds — count turns since round started
        self.round_turns_taken += 1
        if self.round_start_active > 0 and self.round_turns_taken >= self.round_start_active:
            self.round_count += 1
            self.round_turns_taken = 0
            self.round_start_active = self.active_count()  # snapshot for next round
            # Zandu: reveal next joker after each completed round
            if (self.game_type == "zandu"
                    and self.zandu_revealed < len(self.zandu_jokers)):
                self.zandu_revealed += 1
                new_jk = self.zandu_jokers[self.zandu_revealed - 1]
                self.last_auto_event = (
                    f"🃏 Zandu — Joker #{self.zandu_revealed} revealed: "
                    f"{new_jk['rank']}{new_jk['suit']} is now wild!"
                )

    def current_player(self) -> Optional[Player]:
        if 0 <= self.current_turn < len(self.players):
            return self.players[self.current_turn]
        return None

    def seen_amount(self) -> int:
        return self.table_amount * 2

    def active_joker_ranks(self) -> list[str] | None:
        """Return list of active joker ranks, or None if not applicable."""
        if self.game_type == "zandu" and self.zandu_jokers:
            return [c["rank"] for c in self.zandu_jokers[:self.zandu_revealed]]
        if self.game_type == "ak47":
            return AK47_JOKER_RANKS
        return None

    # ── state for broadcast ─────────────────────────────────
    def public_state(self, for_username: str = "") -> dict:
        """Build a JSON-serialisable state dict.
        Cards are hidden unless it's the player's own hand (and they're seen)
        or we're in RESULT phase."""
        reveal = self.phase == RoomPhase.RESULT
        jk = self.joker_card["rank"] if self.joker_card else None
        jk_ranks = self.active_joker_ranks()
        players_data = []
        for p in self.players:
            is_self = p.username == for_username
            players_data.append(p.public_dict(
                reveal_cards=reveal, for_self=is_self,
                game_type=self.game_type, joker_rank=jk,
                joker_ranks=jk_ranks,
            ))

        cp = self.current_player()

        # Who can start the next game?
        starter = self.admin
        if self.mode_picker == "winner" and self.last_winner_username:
            # Only if the winner is still in the room
            if self.player(self.last_winner_username):
                starter = self.last_winner_username

        return {
            "code": self.code,
            "admin": self.admin,
            "phase": self.phase,
            "table_amount": self.table_amount,
            "pot": self.pot,
            "players": players_data,
            "current_turn": cp.username if cp else None,
            "round_count": self.round_count,
            "turn_number": self.turn_number,
            "winner": self.winner,
            "active_count": self.active_count(),
            "side_show_unlocked": self.round_count >= 3,
            "game_type": self.game_type,
            "joker_card": self.joker_card,
            "mode_picker": self.mode_picker,
            "starter": starter,
            "min_coins": self.table_amount * MIN_COIN_MULTIPLIER if self.table_amount else 0,
            "turn_deadline": self.turn_start_time + TURN_TIMEOUT if self.phase == RoomPhase.PLAYING and self.turn_start_time else 0,
            "turn_timeout": TURN_TIMEOUT,
            "zandu_jokers": self._zandu_public() if self.game_type == "zandu" else None,
            "zandu_revealed": self.zandu_revealed if self.game_type == "zandu" else 0,
            "waiting_players": [
                {"username": wp.username, "coins": wp.coins}
                for wp in self.waiting_players
            ],
        }

    def _zandu_public(self) -> list[dict | None]:
        """Return the 3 zandu joker cards — revealed ones shown, hidden ones as None."""
        result = []
        for i, card in enumerate(self.zandu_jokers):
            if i < self.zandu_revealed:
                result.append(card)
            else:
                result.append(None)  # face-down
        return result


# ═══════════════════════════════════════════════════════════════════════════
#  ROOM MANAGER  (in-memory)
# ═══════════════════════════════════════════════════════════════════════════

_rooms: dict[str, Room] = {}


def _gen_code(length: int = 6) -> str:
    while True:
        code = "".join(random.choices(string.ascii_uppercase + string.digits, k=length))
        if code not in _rooms:
            return code


def create_room(admin_username: str) -> Room:
    code = _gen_code()
    room = Room(code=code, admin=admin_username)
    room.players.append(Player(username=admin_username))
    _rooms[code] = room
    logger.info("Room %s created by %s", code, admin_username)
    return room


def get_room(code: str) -> Optional[Room]:
    return _rooms.get(code.upper())


def remove_room(code: str):
    _rooms.pop(code.upper(), None)


def join_room(code: str, username: str) -> tuple[bool, str]:
    """Returns (success, message).
    'Waiting' message means the player is queued for next round.
    """
    room = get_room(code)
    if not room:
        return False, "Room not found"
    # Allow reconnection if player already exists (even mid-game)
    existing = room.player(username)
    if existing:
        existing.is_connected = True
        logger.info("%s reconnected to room %s", username, code)
        return True, "Reconnected"
    # Already in waiting queue — reconnect
    waiting = room.waiting_player(username)
    if waiting:
        waiting.is_connected = True
        logger.info("%s reconnected to waiting queue in room %s", username, code)
        return True, "Waiting"
    # Capacity check: active + waiting must not exceed limit
    if len(room.players) + len(room.waiting_players) >= MAX_ROOM_PLAYERS:
        return False, f"Room is full (max {MAX_ROOM_PLAYERS})"
    # New player during lobby — join immediately
    if room.phase == RoomPhase.LOBBY:
        room.players.append(Player(username=username))
        logger.info("%s joined room %s", username, code)
        return True, "Joined"
    # Game in progress — add to waiting queue
    wp = Player(username=username)
    room.waiting_players.append(wp)
    logger.info("%s queued in waiting list for room %s", username, code)
    return True, "Waiting"


def leave_room(code: str, username: str) -> bool:
    room = get_room(code)
    if not room:
        return False
    # Check if in waiting queue first
    room.waiting_players = [p for p in room.waiting_players if p.username != username]
    room.players = [p for p in room.players if p.username != username]
    if not room.players and not room.waiting_players:
        remove_room(code)
        return True
    # If admin left, promote next player
    if room.admin == username:
        room.admin = room.players[0].username if room.players else room.waiting_players[0].username
    return True


def exit_room(code: str, username: str) -> tuple[bool, str]:
    """Player voluntarily exits a room. Auto-folds if game is active."""
    room = get_room(code)
    if not room:
        return False, "Room not found"

    # Check if player is in the waiting queue — simple removal
    wp = room.waiting_player(username)
    if wp:
        room.waiting_players = [p for p in room.waiting_players if p.username != username]
        return True, f"{username} left the waiting queue"

    p = room.player(username)
    if not p:
        return False, "Not in room"

    if room.phase == RoomPhase.PLAYING and not p.is_folded:
        # Auto-fold the exiting player
        p.is_folded = True
        p.is_connected = False
        # If it was their turn, advance
        cp = room.current_player()
        if cp and cp.username == username:
            room.advance_turn()
        _check_auto_win(room)

    # Find index before removal (for adjusting indices below)
    exit_idx = next(i for i, pl in enumerate(room.players) if pl.username == username)

    # Remove from room
    room.players = [pl for pl in room.players if pl.username != username]
    if not room.players:
        remove_room(code)
        return True, "Room deleted — all players left"

    # Adjust indices that shifted after removal
    if exit_idx < room.current_turn:
        room.current_turn -= 1
    if room.current_turn >= len(room.players):
        room.current_turn = room.current_turn % len(room.players)
    if exit_idx < room.last_winner_index:
        room.last_winner_index -= 1
    if room.last_winner_index >= len(room.players):
        room.last_winner_index = 0

    # Promote admin if needed
    if room.admin == username:
        room.admin = room.players[0].username

    return True, f"{username} left the room"


def check_disconnected_turn(room: Room) -> Optional[str]:
    """If the current player is disconnected, auto-fold them.
    Returns an event message if an auto-fold happened, else None.
    Recurses if the next player is also disconnected."""
    if room.phase != RoomPhase.PLAYING:
        return None
    cp = room.current_player()
    if not cp or cp.is_folded:
        return None
    if cp.is_connected:
        return None

    # Disconnected player's turn → auto-fold
    cp.is_folded = True
    msg = f"⚠️ {cp.username} disconnected — Auto-Fold"
    logger.info("Auto-fold disconnected player %s in room %s", cp.username, room.code)

    room.advance_turn()
    _check_auto_win(room)

    # Recurse — next player might also be disconnected
    if room.phase == RoomPhase.PLAYING:
        next_msg = check_disconnected_turn(room)
        if next_msg:
            msg += "\n" + next_msg

    return msg


def is_room_empty(code: str) -> bool:
    """Check if all players (including waiting) are disconnected or room has no players."""
    room = get_room(code)
    if not room:
        return True
    if not room.players and not room.waiting_players:
        return True
    all_dc = all(not p.is_connected for p in room.players) if room.players else True
    all_wait_dc = all(not p.is_connected for p in room.waiting_players) if room.waiting_players else True
    return all_dc and all_wait_dc


def cleanup_empty_room(code: str) -> bool:
    """Remove room if all players are disconnected. Returns True if removed."""
    if is_room_empty(code):
        logger.info("Room %s — all players disconnected, removing", code)
        remove_room(code)
        return True
    return False


# ═══════════════════════════════════════════════════════════════════════════
#  GAME ACTIONS  (called from the router)
# ═══════════════════════════════════════════════════════════════════════════

MIN_COIN_MULTIPLIER = 3   # player needs at least 3× table amount to play


def start_game(room: Room, table_amount: int, game_type: str = "normal") -> tuple[bool, str]:
    """Admin starts the game — deals cards to all players."""
    if len(room.players) < 2:
        return False, "Need at least 2 players"
    if table_amount < 1:
        return False, "Table amount must be at least 1"

    room.table_amount = table_amount
    min_coins = table_amount * MIN_COIN_MULTIPLIER

    # Pre-round check — mark broke AND disconnected players as sitting out
    sitting_out_names = []
    dc_sitting_out_names = []
    for p in room.players:
        if not p.is_connected:
            p.is_sitting_out = True
            dc_sitting_out_names.append(p.username)
        elif p.coins < min_coins:
            p.is_sitting_out = True
            sitting_out_names.append(p.username)
        else:
            p.is_sitting_out = False

    # Count eligible players
    eligible = [p for p in room.players if not p.is_sitting_out]
    if len(eligible) < 2:
        # Reset sitting_out flags — can't start
        for p in room.players:
            p.is_sitting_out = False
        return False, f"Not enough connected players with {min_coins}+ coins (need at least 2)"

    room.phase = RoomPhase.PLAYING
    room.pot = 0
    room.round_count = 0
    room.turn_number = 0
    room.round_turns_taken = 0
    room.winner = None

    # Reset players
    for p in room.players:
        p.cards = []
        p.is_seen = False
        p.is_folded = p.is_sitting_out   # sitting out = auto-folded
        p.is_viewing = False
        p.total_bet = 0

    # Deal only to eligible players
    room.deck = new_deck()
    num_cards = 4 if game_type == "4card" else 2 if game_type == "2card" else 3
    for p in room.players:
        if not p.is_sitting_out:
            p.cards = [room.deck.pop() for _ in range(num_cards)]

    # Game type setup
    room.game_type = game_type
    room.joker_card = None
    room.zandu_jokers = []
    room.zandu_revealed = 0
    if game_type == "joker" and room.deck:
        room.joker_card = room.deck.pop()
    elif game_type == "zandu" and len(room.deck) >= 3:
        room.zandu_jokers = [room.deck.pop() for _ in range(3)]
        room.zandu_revealed = 1  # first joker face-up

    # Ante: deduct 1× table_amount from every eligible player
    for p in room.players:
        if not p.is_sitting_out:
            p.coins -= table_amount
            p.total_bet += table_amount
            _add_to_pot(room, table_amount)

    # First turn = next player clockwise from last_winner_index (skip sitting-out)
    room.current_turn = (room.last_winner_index + 1) % len(room.players)
    if room.players[room.current_turn].is_folded:
        room.current_turn = room._next_active(room.current_turn)
    room.turn_start_time = time.time()
    room.round_start_active = room.active_count()  # snapshot for round counting

    if game_type == "joker" and room.joker_card:
        mode_msg = f"\U0001f0cf Joker mode \u2014 {room.joker_card['rank']}{room.joker_card['suit']} is wild!"
    elif game_type == "zandu" and room.zandu_jokers:
        jk1 = room.zandu_jokers[0]
        mode_msg = f"\U0001f0cf Zandu mode \u2014 3 Jokers! {jk1['rank']}{jk1['suit']} is wild, 2 more hidden!"
    elif game_type == "ak47":
        mode_msg = "\U0001f52b AK47 mode \u2014 A, K, 4, 7 are wild!"
    elif game_type == "muflis":
        mode_msg = "\U0001f504 Muflis mode \u2014 lowest hand wins!"
    elif game_type == "2card":
        mode_msg = "\u2702\ufe0f 2-Card mode \u2014 2 cards, best hand wins!"
    elif game_type == "4card":
        mode_msg = "\U0001f4a5 4-Card mode \u2014 4 cards dealt, best 3-card combo wins!"
    else:
        mode_msg = "Normal mode"
    sitting_msg = ""
    if sitting_out_names:
        sitting_msg += "\n" + "\n".join(
            f"💸 {name} is sitting out — not enough coins (need {min_coins} 🪙)"
            for name in sitting_out_names
        )
    if dc_sitting_out_names:
        sitting_msg += "\n" + "\n".join(
            f"📡 {name} is sitting out — disconnected"
            for name in dc_sitting_out_names
        )
    logger.info("Game started in room %s — table %d — %s (sitting out: %s, disconnected: %s)",
                room.code, table_amount, game_type,
                sitting_out_names or "none", dc_sitting_out_names or "none")
    return True, f"Game started — {mode_msg}{sitting_msg}"


def action_blind(room: Room, username: str) -> tuple[bool, str]:
    """Play blind — pay table amount, stay blind."""
    p = room.player(username)
    if not p or p.is_folded:
        return False, "Cannot act"
    cp = room.current_player()
    if not cp or cp.username != username:
        return False, "Not your turn"
    if p.is_seen or p.is_viewing:
        return False, "Cannot play blind after viewing cards"

    cost = room.table_amount
    if p.coins < cost:
        return False, f"Not enough coins (need {cost})"

    p.coins -= cost
    p.total_bet += cost
    _add_to_pot(room, cost)

    room.advance_turn()
    _check_auto_win(room)
    return True, f"Played blind — paid {cost}"

def action_view(room: Room, username: str) -> tuple[bool, str]:
    """View cards — free peek. Player must then Play Seen or Fold (same turn)."""
    p = room.player(username)
    if not p or p.is_folded:
        return False, "Cannot act"
    cp = room.current_player()
    if not cp or cp.username != username:
        return False, "Not your turn"
    if p.is_seen:
        return False, "Already seen"
    if p.is_viewing:
        return False, "Already viewing \u2014 choose Play Seen or Fold"

    p.is_viewing = True
    # No cost, no turn advance \u2014 player must still act
    return True, f"\U0001f440 {username} is peeking at their cards\u2026"

def action_seen(room: Room, username: str) -> tuple[bool, str]:
    """See cards — pay 2× table amount, reveal own cards."""
    p = room.player(username)
    if not p or p.is_folded:
        return False, "Cannot act"
    cp = room.current_player()
    if not cp or cp.username != username:
        return False, "Not your turn"

    cost = room.seen_amount()
    if p.coins < cost:
        return False, f"Not enough coins (need {cost})"
    p.is_viewing = False
    p.is_seen = True
    p.coins -= cost
    p.total_bet += cost
    _add_to_pot(room, cost)

    room.advance_turn()
    _check_auto_win(room)
    return True, f"Seen — paid {cost}"


def action_fold(room: Room, username: str) -> tuple[bool, str]:
    """Fold — drop out of the round."""
    p = room.player(username)
    if not p or p.is_folded:
        return False, "Cannot act"
    cp = room.current_player()
    if not cp or cp.username != username:
        return False, "Not your turn"

    p.is_viewing = False
    p.is_folded = True
    room.advance_turn()
    _check_auto_win(room)
    return True, "Folded"


def action_timeout_fold(room: Room) -> tuple[bool, str]:
    """Auto-fold current player due to turn timeout."""
    cp = room.current_player()
    if not cp or cp.is_folded:
        return False, "No active player"
    username = cp.username
    cp.is_viewing = False
    cp.is_folded = True
    room.advance_turn()
    _check_auto_win(room)
    return True, f"⏰ {username} ran out of time — Auto-Fold"


def action_show(room: Room, username: str) -> tuple[bool, str]:
    """Show — only when exactly 2 active players remain. Compare hands."""
    p = room.player(username)
    if not p or p.is_folded:
        return False, "Cannot act"
    if p.is_viewing:
        return False, "Finish viewing first — choose Play Seen or Fold"
    cp = room.current_player()
    if not cp or cp.username != username:
        return False, "Not your turn"
    if room.active_count() != 2:
        return False, "Show only available when 2 players remain"

    # The show initiator pays the seen amount
    cost = room.seen_amount() if p.is_seen else room.table_amount
    if p.coins < cost:
        return False, f"Not enough coins (need {cost})"

    p.coins -= cost
    p.total_bet += cost
    _add_to_pot(room, cost)

    # Find opponent
    active = room.active_players()
    opponent = active[0] if active[0].username != username else active[1]

    jk = room.joker_card["rank"] if room.joker_card else None
    jk_ranks = room.active_joker_ranks()
    result = compare_hands(p.cards, opponent.cards, room.game_type, jk, jk_ranks)
    if result > 0:
        winner = p        # initiator strictly better
    else:
        winner = opponent  # tie or worse → initiator loses

    _award_winner(room, winner.username)
    return True, f"Show! {winner.username} wins"


def action_sideshow(room: Room, username: str) -> tuple[bool, str]:
    """Side show — challenge the previous active player."""
    p = room.player(username)
    if not p or p.is_folded or not p.is_seen:
        return False, "Must be seen to request side show"
    if p.is_viewing:
        return False, "Finish viewing first — choose Play Seen or Fold"
    cp = room.current_player()
    if not cp or cp.username != username:
        return False, "Not your turn"
    if room.round_count < 3:
        return False, "Side show unlocked after 3 rounds"
    if room.active_count() <= 2:
        return False, "Use Show instead when only 2 players remain"

    # Find previous active player
    n = len(room.players)
    prev_idx = (room.current_turn - 1) % n
    while room.players[prev_idx].is_folded:
        prev_idx = (prev_idx - 1) % n
    prev_player = room.players[prev_idx]

    if not prev_player.is_seen:
        return False, "Previous player is blind — cannot side show"

    # Pay seen amount
    cost = room.seen_amount()
    if p.coins < cost:
        return False, f"Not enough coins (need {cost})"

    p.coins -= cost
    p.total_bet += cost
    _add_to_pot(room, cost)

    # Compare
    jk = room.joker_card["rank"] if room.joker_card else None
    jk_ranks = room.active_joker_ranks()
    result = compare_hands(p.cards, prev_player.cards, room.game_type, jk, jk_ranks)

    # Store reveal data so router can send it privately to both players
    room.last_sideshow = {
        "challenger": username,
        "opponent": prev_player.username,
        "challenger_cards": list(p.cards),
        "opponent_cards": list(prev_player.cards),
        "challenger_hand": hand_name(p.cards, room.game_type, jk, jk_ranks),
        "opponent_hand": hand_name(prev_player.cards, room.game_type, jk, jk_ranks),
        "loser": username if result <= 0 else prev_player.username,
    }

    if result > 0:
        # Challenger strictly wins — previous player folds
        prev_player.is_folded = True
        winner, loser = username, prev_player.username
    else:
        # Tie or challenger loses — challenger folds (initiator loses ties)
        p.is_folded = True
        winner, loser = prev_player.username, username

    msg = (f"🤝 Side Show! {username} challenged {prev_player.username} "
           f"— {winner} wins, {loser} folds!")

    room.advance_turn()
    _check_auto_win(room)
    return True, msg


def _check_auto_win(room: Room):
    """If only 1 active player remains, they win.
    Also auto-show/fold if the current player can't afford the minimum bet."""
    active = room.active_players()
    if len(active) == 1:
        _award_winner(room, active[0].username)
        return

    # If game is still in progress, check if current player is broke
    if room.phase != RoomPhase.PLAYING:
        return
    cp = room.current_player()
    if not cp or cp.is_folded:
        return

    min_cost = room.seen_amount() if cp.is_seen else room.table_amount
    if cp.coins >= min_cost:
        return  # can still play normally

    # Player can't afford to play
    if len(active) == 2:
        # Auto-show (free — they have no coins to pay)
        opponent = active[0] if active[0].username != cp.username else active[1]
        jk = room.joker_card["rank"] if room.joker_card else None
        jk_ranks = room.active_joker_ranks()
        result = compare_hands(cp.cards, opponent.cards, room.game_type, jk, jk_ranks)
        # Broke player is the "initiator" of this forced show → loses on tie
        if result > 0:
            winner = cp
        else:
            winner = opponent
        logger.info("Auto-show in room %s — %s can't afford %d coins",
                    room.code, cp.username, min_cost)
        room.last_auto_event = f"⚠️ {cp.username} can't afford {min_cost} 🪙 — Auto-Show! {winner.username} wins"
        _award_winner(room, winner.username)
    else:
        # More than 2 active — auto-fold the broke player
        logger.info("Auto-fold in room %s — %s can't afford %d coins",
                    room.code, cp.username, min_cost)
        room.last_auto_event = f"⚠️ {cp.username} can't afford {min_cost} 🪙 — Auto-Fold"
        cp.is_folded = True
        room.advance_turn()
        _check_auto_win(room)  # recurse — might trigger another auto-fold/show


def _award_winner(room: Room, winner_username: str):
    """Award pot to winner and move to RESULT phase.
    Commission is taken from the total pot here (not per-bet)."""
    commission = int(room.pot * COMMISSION_RATE)
    payout = room.pot - commission
    room.house_commission = commission
    winner = room.player(winner_username)
    if winner:
        winner.coins += payout
    room.winner = winner_username
    room.last_winner_username = winner_username
    room.phase = RoomPhase.RESULT

    # Record winner index for next rotation
    for i, p in enumerate(room.players):
        if p.username == winner_username:
            room.last_winner_index = i
            break

    logger.info(
        "Room %s — Winner: %s  Pot: %d",
        room.code, winner_username, room.pot,
    )


def get_starter(room: Room) -> str:
    """Return the username who can start/restart the next round."""
    if room.mode_picker == "winner" and room.last_winner_username:
        if room.player(room.last_winner_username):
            return room.last_winner_username
    return room.admin


def restart_game(room: Room):
    """Move room back to LOBBY for a new round. Keep players.
    Promote any waiting players into the room."""
    room.phase = RoomPhase.LOBBY
    room.pot = 0
    room.round_count = 0
    room.turn_number = 0
    room.winner = None
    room.deck = []
    room.last_sideshow = None
    room.last_auto_event = None
    room.joker_card = None
    room.zandu_jokers = []
    room.zandu_revealed = 0
    room.house_commission = 0
    room.turn_start_time = 0.0
    room.round_start_active = 0
    room.round_turns_taken = 0
    for p in room.players:
        p.cards = []
        p.is_seen = False
        p.is_folded = False
        p.is_viewing = False
        p.is_sitting_out = False
        p.total_bet = 0
    # Promote waiting players into the room (up to limit)
    while room.waiting_players and len(room.players) < MAX_ROOM_PLAYERS:
        wp = room.waiting_players.pop(0)
        wp.cards = []
        wp.is_seen = False
        wp.is_folded = False
        wp.is_viewing = False
        wp.is_sitting_out = False
        wp.total_bet = 0
        room.players.append(wp)
        logger.info("Promoted waiting player %s into room %s", wp.username, room.code)
    logger.info("Room %s reset to lobby", room.code)
