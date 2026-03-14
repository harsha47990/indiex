"""
game_engine/teen_patti.py — Server-side Teen Patti multiplayer engine
═════════════════════════════════════════════════════════════════════
All game logic runs here. Clients only send actions; server validates
everything and broadcasts state.
"""

import random
import string
import logging
from itertools import combinations
from enum import IntEnum
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

COMMISSION_RATE = 0.05  # 5% house commission


def _skim_to_pot(room, cost: int):
    """Add bet to pot after silently skimming commission."""
    commission = int(cost * COMMISSION_RATE)
    room.pot += cost - commission
    room.house_commission += commission


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


def compare_hands(a: list[dict], b: list[dict],
                  game_type: str = "normal",
                  joker_rank: str | None = None) -> int:
    """Return  1 if a wins,  -1 if b wins,  0 if draw.
    Joker mode: wild cards considered.  Muflis mode: result inverted."""
    if game_type == "2card":
        ra, ta = evaluate_hand_2card(a)
        rb, tb = evaluate_hand_2card(b)
    elif game_type == "4card":
        ra, ta = evaluate_hand_4card(a)
        rb, tb = evaluate_hand_4card(b)
    elif game_type == "joker" and joker_rank:
        ra, ta = evaluate_hand_joker(a, joker_rank)
        rb, tb = evaluate_hand_joker(b, joker_rank)
    else:
        ra, ta = evaluate_hand(a)
        rb, tb = evaluate_hand(b)
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
              joker_rank: str | None = None) -> str:
    if game_type == "2card":
        return hand_name_2card(cards)
    if game_type == "4card":
        return hand_name_4card(cards)
    if game_type == "joker" and joker_rank:
        rank, _ = evaluate_hand_joker(cards, joker_rank)
    else:
        rank, _ = evaluate_hand(cards)
    label = HAND_NAMES[rank]
    if game_type == "joker" and joker_rank and any(c["rank"] == joker_rank for c in cards):
        label += " (Joker)"
    return label


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
    total_bet: int = 0          # amount this player has put into pot
    is_connected: bool = True
    is_sitting_out: bool = False   # not enough coins to play this round

    def public_dict(self, reveal_cards: bool = False, for_self: bool = False,
                    game_type: str = "normal", joker_rank: str | None = None) -> dict:
        d = {
            "username": self.username,
            "coins": self.coins,
            "is_seen": self.is_seen,
            "is_folded": self.is_folded,
            "total_bet": self.total_bet,
            "is_connected": self.is_connected,
            "is_sitting_out": self.is_sitting_out,
        }
        if for_self and self.cards:
            # Result phase → always reveal, even if player was blind
            if reveal_cards:
                d["cards"] = self.cards
                d["hand_name"] = hand_name(self.cards, game_type, joker_rank)
            elif self.is_seen:
                d["cards"] = self.cards
                d["hand_name"] = hand_name(self.cards, game_type, joker_rank)
            else:
                d["cards"] = [{"rank": "?", "suit": "?"} for _ in self.cards]
        elif reveal_cards and self.cards:
            d["cards"] = self.cards
            d["hand_name"] = hand_name(self.cards, game_type, joker_rank)
        else:
            d["card_count"] = len(self.cards)
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
    game_type: str = "normal"                  # "normal", "joker", "muflis"
    joker_card: Optional[dict] = field(default=None)  # revealed joker (joker mode)
    mode_picker: str = "admin"                 # "admin" = admin always picks, "winner" = last winner picks
    last_winner_username: Optional[str] = None  # username of last round's winner
    house_commission: int = 0                    # accumulated commission this round

    # ── helpers ─────────────────────────────────────────────
    def player(self, username: str) -> Optional[Player]:
        for p in self.players:
            if p.username == username:
                return p
        return None

    def active_players(self) -> list[Player]:
        return [p for p in self.players if not p.is_folded]

    def active_count(self) -> int:
        return len(self.active_players())

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
        # Track full rounds (one cycle through active players)
        active = self.active_count()
        if active > 0 and self.turn_number > 0 and self.turn_number % active == 0:
            self.round_count += 1

    def current_player(self) -> Optional[Player]:
        if 0 <= self.current_turn < len(self.players):
            return self.players[self.current_turn]
        return None

    def seen_amount(self) -> int:
        return self.table_amount * 2

    # ── state for broadcast ─────────────────────────────────
    def public_state(self, for_username: str = "") -> dict:
        """Build a JSON-serialisable state dict.
        Cards are hidden unless it's the player's own hand (and they're seen)
        or we're in RESULT phase."""
        reveal = self.phase == RoomPhase.RESULT
        jk = self.joker_card["rank"] if self.joker_card else None
        players_data = []
        for p in self.players:
            is_self = p.username == for_username
            players_data.append(p.public_dict(
                reveal_cards=reveal, for_self=is_self,
                game_type=self.game_type, joker_rank=jk,
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
        }


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
    """Returns (success, message)."""
    room = get_room(code)
    if not room:
        return False, "Room not found"
    # Allow reconnection if player already exists (even mid-game)
    existing = room.player(username)
    if existing:
        existing.is_connected = True
        logger.info("%s reconnected to room %s", username, code)
        return True, "Reconnected"
    # New player can only join during lobby
    if room.phase != RoomPhase.LOBBY:
        return False, "Game already in progress"
    if len(room.players) >= 7:
        return False, "Room is full (max 7)"
    room.players.append(Player(username=username))
    logger.info("%s joined room %s", username, code)
    return True, "Joined"


def leave_room(code: str, username: str) -> bool:
    room = get_room(code)
    if not room:
        return False
    room.players = [p for p in room.players if p.username != username]
    if not room.players:
        remove_room(code)
        return True
    # If admin left, promote next player
    if room.admin == username:
        room.admin = room.players[0].username
    return True


def exit_room(code: str, username: str) -> tuple[bool, str]:
    """Player voluntarily exits a room. Auto-folds if game is active."""
    room = get_room(code)
    if not room:
        return False, "Room not found"
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

    # Remove from room
    room.players = [pl for pl in room.players if pl.username != username]
    if not room.players:
        remove_room(code)
        return True, "Room deleted — all players left"

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
    """Check if all players in a room are disconnected or room has no players."""
    room = get_room(code)
    if not room:
        return True
    if not room.players:
        return True
    return all(not p.is_connected for p in room.players)


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

    # Pre-round coin check — mark broke players as sitting out
    sitting_out_names = []
    for p in room.players:
        p.is_sitting_out = p.coins < min_coins
        if p.is_sitting_out:
            sitting_out_names.append(p.username)

    # Count eligible players
    eligible = [p for p in room.players if not p.is_sitting_out]
    if len(eligible) < 2:
        # Reset sitting_out flags — can't start
        for p in room.players:
            p.is_sitting_out = False
        return False, f"Not enough players with {min_coins}+ coins (need at least 2)"

    room.phase = RoomPhase.PLAYING
    room.pot = 0
    room.round_count = 0
    room.turn_number = 0
    room.winner = None

    # Reset players
    for p in room.players:
        p.cards = []
        p.is_seen = False
        p.is_folded = p.is_sitting_out   # sitting out = auto-folded
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
    if game_type == "joker" and room.deck:
        room.joker_card = room.deck.pop()

    # First turn = next player clockwise from last_winner_index (skip sitting-out)
    room.current_turn = (room.last_winner_index + 1) % len(room.players)
    if room.players[room.current_turn].is_folded:
        room.current_turn = room._next_active(room.current_turn)

    if game_type == "joker" and room.joker_card:
        mode_msg = f"\U0001f0cf Joker mode \u2014 {room.joker_card['rank']}{room.joker_card['suit']} is wild!"
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
        sitting_msg = "\n" + "\n".join(
            f"💸 {name} is sitting out — not enough coins (need {min_coins} 🪙)"
            for name in sitting_out_names
        )
    logger.info("Game started in room %s — table %d — %s (sitting out: %s)",
                room.code, table_amount, game_type, sitting_out_names or "none")
    return True, f"Game started — {mode_msg}{sitting_msg}"


def action_blind(room: Room, username: str) -> tuple[bool, str]:
    """Play blind — pay table amount, stay blind."""
    p = room.player(username)
    if not p or p.is_folded:
        return False, "Cannot act"
    cp = room.current_player()
    if not cp or cp.username != username:
        return False, "Not your turn"

    cost = room.table_amount
    if p.coins < cost:
        return False, f"Not enough coins (need {cost})"

    p.coins -= cost
    p.total_bet += cost
    _skim_to_pot(room, cost)

    room.advance_turn()
    _check_auto_win(room)
    return True, f"Played blind — paid {cost}"


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

    p.is_seen = True
    p.coins -= cost
    p.total_bet += cost
    _skim_to_pot(room, cost)

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

    p.is_folded = True
    room.advance_turn()
    _check_auto_win(room)
    return True, "Folded"


def action_show(room: Room, username: str) -> tuple[bool, str]:
    """Show — only when exactly 2 active players remain. Compare hands."""
    p = room.player(username)
    if not p or p.is_folded:
        return False, "Cannot act"
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
    _skim_to_pot(room, cost)

    # Find opponent
    active = room.active_players()
    opponent = active[0] if active[0].username != username else active[1]

    jk = room.joker_card["rank"] if room.joker_card else None
    result = compare_hands(p.cards, opponent.cards, room.game_type, jk)
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
    _skim_to_pot(room, cost)

    # Compare
    jk = room.joker_card["rank"] if room.joker_card else None
    result = compare_hands(p.cards, prev_player.cards, room.game_type, jk)

    # Store reveal data so router can send it privately to both players
    room.last_sideshow = {
        "challenger": username,
        "opponent": prev_player.username,
        "challenger_cards": list(p.cards),
        "opponent_cards": list(prev_player.cards),
        "challenger_hand": hand_name(p.cards, room.game_type, jk),
        "opponent_hand": hand_name(prev_player.cards, room.game_type, jk),
        "loser": username if result <= 0 else prev_player.username,
    }

    if result > 0:
        # Challenger strictly wins — previous player folds
        prev_player.is_folded = True
        msg = f"Side show: {prev_player.username} folds"
    else:
        # Tie or challenger loses — challenger folds (initiator loses ties)
        p.is_folded = True
        msg = f"Side show: {username} folds"

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
        result = compare_hands(cp.cards, opponent.cards, room.game_type, jk)
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
    """Award pot to winner and move to RESULT phase."""
    winner = room.player(winner_username)
    if winner:
        winner.coins += room.pot
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
    """Move room back to LOBBY for a new round. Keep players."""
    room.phase = RoomPhase.LOBBY
    room.pot = 0
    room.round_count = 0
    room.turn_number = 0
    room.winner = None
    room.deck = []
    room.last_sideshow = None
    room.last_auto_event = None
    room.joker_card = None
    room.house_commission = 0
    for p in room.players:
        p.cards = []
        p.is_seen = False
        p.is_folded = False
        p.is_sitting_out = False
        p.total_bet = 0
    logger.info("Room %s reset to lobby", room.code)
