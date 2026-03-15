"""
routes/teen_patti_routes.py — Separate router for Teen Patti multiplayer
═══════════════════════════════════════════════════════════════════════════
REST endpoints for room management + WebSocket for real-time gameplay.
"""

import asyncio
import json
import logging
from time import monotonic, time

from fastapi import APIRouter, Cookie, WebSocket, WebSocketDisconnect, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

from auth import (
    get_user_from_session, verify_session, touch_activity, get_coins,
    load_coins, batch_get_coins, batch_sync_coins,
)
from dependencies import render_page, require_user
from game_engine.teen_patti import (
    create_room, get_room, join_room, leave_room, exit_room, start_game,
    action_blind, action_view, action_seen, action_fold, action_show, action_sideshow,
    action_timeout_fold, restart_game, get_starter, RoomPhase,
    check_disconnected_turn, cleanup_empty_room, TURN_TIMEOUT,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/games/teen-patti", tags=["teen-patti"])

# ── Configurable timeout (seconds) for each WS send during broadcast ───
BROADCAST_TIMEOUT: float = 5.0

# ── WebSocket rate limiting ────────────────────────────────────────
WS_RATE_LIMIT: int = 10       # max messages per window
WS_RATE_WINDOW: float = 1.0   # window in seconds


# ── Active WebSocket connections:  room_code -> {username: WebSocket} ───
_connections: dict[str, dict[str, WebSocket]] = {}

# ── Turn timers: room_code -> asyncio Task that auto-folds on timeout ──
_turn_timers: dict[str, asyncio.Task] = {}


@router.get("", response_class=HTMLResponse)
async def teen_patti_page(session_key: str | None = Cookie(default=None)):
    user = get_user_from_session(session_key)
    if not user:
        return RedirectResponse("/", status_code=302)
    if user.get("must_reset_password"):
        return RedirectResponse("/reset-password", status_code=302)
    touch_activity(session_key)
    return HTMLResponse(render_page("teen_patti.html"))


# ═══════════════════════════════════════════════════════════════════════════
#  REST — ROOM MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/api/create-room")
async def api_create_room(user: dict = Depends(require_user)):
    room = create_room(user["username"])
    # Sync coins from DB into the Player object
    room.player(user["username"]).coins = user.get("coins", 0)
    return {"ok": True, "room_code": room.code}


@router.post("/api/join-room")
async def api_join_room(payload: dict, user: dict = Depends(require_user)):
    code = (payload.get("room_code") or "").strip().upper()
    if not code:
        return JSONResponse({"ok": False, "error": "Room code required"}, 400)
    ok, msg = join_room(code, user["username"])
    if not ok:
        return JSONResponse({"ok": False, "error": msg}, 400)
    room = get_room(code)
    # Sync coins
    p = room.player(user["username"])
    if p:
        p.coins = user.get("coins", 0)
    return {"ok": True, "room_code": code, "message": msg}


# ═══════════════════════════════════════════════════════════════════════════
#  WEBSOCKET — REAL-TIME GAME
# ═══════════════════════════════════════════════════════════════════════════

async def _safe_send(ws: WebSocket, data: dict, uname: str, room_code: str):
    """Send JSON to a single client with a timeout; remove dead connections."""
    try:
        await asyncio.wait_for(ws.send_json(data), timeout=BROADCAST_TIMEOUT)
    except Exception:
        logger.warning("Dropping dead connection for %s in %s", uname, room_code)
        conns = _connections.get(room_code, {})
        conns.pop(uname, None)
        try:
            await ws.close()
        except Exception:
            pass


async def _broadcast(room_code: str, room):
    """Send personalised state to each connected player (parallel)."""
    conns = _connections.get(room_code, {})
    tasks = []
    for uname, ws in list(conns.items()):
        state = room.public_state(for_username=uname)
        tasks.append(_safe_send(ws, {"type": "state", "data": state}, uname, room_code))
    if tasks:
        await asyncio.gather(*tasks)


async def _broadcast_event(room_code: str, event: dict):
    """Send a non-state event (e.g. chat, notification) to all (parallel)."""
    conns = _connections.get(room_code, {})
    tasks = [
        _safe_send(ws, event, uname, room_code)
        for uname, ws in list(conns.items())
    ]
    if tasks:
        await asyncio.gather(*tasks)


# ═════════════════════════════════════════════════════════════════════════#  TURN TIMER (auto-fold on timeout)
# ═════════════════════════════════════════════════════════════════════

def _cancel_turn_timer(room_code: str):
    """Cancel any existing turn timer for a room."""
    task = _turn_timers.pop(room_code, None)
    if task and not task.done():
        task.cancel()


def _schedule_turn_timer(room_code: str, room):
    """Schedule auto-fold after TURN_TIMEOUT if the current turn doesn't change."""
    _cancel_turn_timer(room_code)
    if room.phase != RoomPhase.PLAYING:
        return
    expected_turn = room.current_turn
    expected_tn = room.turn_number
    deadline = room.turn_start_time + TURN_TIMEOUT

    async def _timer():
        remaining = deadline - time()
        if remaining > 0:
            await asyncio.sleep(remaining)
        r = get_room(room_code)
        if not r or r.phase != RoomPhase.PLAYING:
            return
        if r.current_turn != expected_turn or r.turn_number != expected_tn:
            return
        ok, msg = action_timeout_fold(r)
        if not ok:
            return
        if r.phase == RoomPhase.RESULT:
            _persist_coins(r)
        await _broadcast_event(room_code, {"type": "event", "message": msg})
        await _broadcast(room_code, r)
        await _handle_dc_chain(room_code, r)
        if r.phase == RoomPhase.PLAYING:
            _schedule_turn_timer(room_code, r)

    _turn_timers[room_code] = asyncio.create_task(_timer())


# ═════════════════════════════════════════════════════════════════════#  SHARED HELPERS
# ═════════════════════════════════════════════════════════════════════════

async def _handle_dc_chain(room_code: str, room) -> bool:
    """Auto-fold disconnected players and persist coins if game ended.
    Returns True if any auto-fold happened."""
    dc_msg = check_disconnected_turn(room)
    if not dc_msg:
        return False
    for line in dc_msg.split("\n"):
        await _broadcast_event(room_code, {"type": "event", "message": line})
    if room.phase == RoomPhase.RESULT:
        _persist_coins(room)
    await _broadcast(room_code, room)
    return True


def _persist_player_coins(player):
    """Persist a single player's coins to DB (safety net on disconnect)."""
    try:
        current_db = get_coins(player.username)
        diff = player.coins - current_db
        if diff != 0:
            load_coins(player.username, diff, loaded_by="teen_patti")
    except Exception:
        pass


def _persist_coins(room):
    """Write every player's coin balance back to the DB (single file write)."""
    try:
        batch_sync_coins({p.username: p.coins for p in room.players})
        # Credit accumulated house commission to the teen_patti account
        if room.house_commission > 0:
            load_coins("teen_patti", room.house_commission, loaded_by="commission")
            logger.info(
                "House commission %d credited (room %s)", room.house_commission, room.code,
            )
    except Exception as e:
        logger.error("Failed to persist coins: %s", e)


# ═════════════════════════════════════════════════════════════════════════
#  ACTION HANDLERS
# ═════════════════════════════════════════════════════════════════════════

# Simple actions: signature (room, username) → (ok, reply)
_SIMPLE_ACTIONS = {
    "blind":    action_blind,
    "view":     action_view,
    "seen":     action_seen,
    "fold":     action_fold,
    "show":     action_show,
    "sideshow": action_sideshow,
}


def _on_start(room, username, msg):
    """Handle 'start' action."""
    starter = get_starter(room)
    if username != starter:
        return False, f"Only {starter} can start"
    table_amount = int(msg.get("table_amount", 0))
    game_type = msg.get("game_type", "normal")
    if game_type not in ("normal", "joker", "muflis", "2card", "4card"):
        game_type = "normal"
    # Admin can change mode_picker setting
    mp = msg.get("mode_picker", "")
    if mp in ("admin", "winner") and username == room.admin:
        room.mode_picker = mp
    # Batch-sync all players' coins from DB (single file read)
    coin_map = batch_get_coins([p.username for p in room.players])
    for pl in room.players:
        pl.coins = coin_map.get(pl.username, pl.coins)
    return start_game(room, table_amount, game_type)


def _on_restart(room, username):
    """Handle 'restart' action."""
    starter = get_starter(room)
    if username != starter:
        return False, f"Only {starter} can restart"
    restart_game(room)
    return True, "Game restarted — set table amount and start"


async def _on_chat(room_code, username, msg):
    """Handle 'chat' — broadcast and skip normal post-processing."""
    text = (msg.get("text") or "").strip()
    if text:
        text = text[:200]  # cap length
        await _broadcast_event(room_code, {
            "type": "chat",
            "from": username,
            "text": text,
        })


async def _on_request_coins(ws, room_code, room, username):
    """Handle 'request_coins' — refresh + notify + skip normal post-processing."""
    p = room.player(username)
    if p:
        p.coins = get_coins(username)
        await _broadcast_event(room_code, {
            "type": "event",
            "message": f"🪙 {username} is requesting coins from admin! (current: {p.coins} 🪙)",
        })
        await _broadcast(room_code, room)
    reply = f"Coins refreshed — you have {p.coins if p else 0} 🪙"
    await ws.send_json({"type": "ack", "ok": True, "message": reply})


async def _on_exit(ws, room_code, room, username):
    """Handle 'exit' — player leaves, cleanup, close socket."""
    exit_ok, exit_msg = exit_room(room_code, username)
    await ws.send_json({"type": "ack", "ok": exit_ok, "message": exit_msg})
    await ws.send_json({"type": "exit", "message": exit_msg})

    # Remove their connection (only if this is still the registered socket)
    conns = _connections.get(room_code, {})
    if conns.get(username) is ws:
        conns.pop(username, None)
        if not conns:
            _connections.pop(room_code, None)

    # If room was deleted (all left), nothing to broadcast
    room_after = get_room(room_code)
    if room_after:
        if room_after.phase == RoomPhase.RESULT:
            _persist_coins(room_after)
        await _broadcast_event(room_code, {
            "type": "event",
            "message": exit_msg,
        })
        await _broadcast(room_code, room_after)
        await _handle_dc_chain(room_code, room_after)

    await ws.close()


async def _post_action(ws, room_code, room, action, ok, reply):
    """Common post-action flow: persist, ack, broadcast, DC chain, sideshow."""
    # If game just ended, persist coin changes to DB
    if room.phase == RoomPhase.RESULT:
        _persist_coins(room)

    # Send ACK to the acting player
    await ws.send_json({"type": "ack", "ok": ok, "message": reply})

    # Broadcast event message
    if ok:
        await _broadcast_event(room_code, {
            "type": "event",
            "message": reply,
        })

    # Broadcast updated state
    await _broadcast(room_code, room)

    # Broadcast auto-show/fold event if engine triggered one
    if room.last_auto_event:
        await _broadcast_event(room_code, {
            "type": "event",
            "message": room.last_auto_event,
        })
        room.last_auto_event = None

    # Auto-fold any disconnected player whose turn it now is
    await _handle_dc_chain(room_code, room)

    # Private sideshow card reveal to both involved players only
    # + public result announcement (no cards) to everyone else
    if action == "sideshow" and ok and room.last_sideshow:
        ss = room.last_sideshow
        conns = _connections.get(room_code, {})
        involved = {ss["challenger"], ss["opponent"]}

        # Public result (no cards) → everyone
        public_ss = {
            "challenger": ss["challenger"],
            "opponent": ss["opponent"],
            "loser": ss["loser"],
        }
        await _broadcast_event(room_code, {"type": "sideshow_result", "data": public_ss})

        # Private card reveal → only the two participants
        for target in involved:
            tw = conns.get(target)
            if tw:
                try:
                    await tw.send_json({"type": "sideshow_reveal", "data": ss})
                except Exception:
                    pass
        room.last_sideshow = None

    # Schedule/cancel turn timer
    if room.phase == RoomPhase.PLAYING:
        _schedule_turn_timer(room_code, room)
    else:
        _cancel_turn_timer(room_code)


@router.websocket("/ws/{room_code}")
async def ws_game(ws: WebSocket, room_code: str):
    """Each player opens one WebSocket per room."""
    await ws.accept()

    # Auth from query param
    session_key = ws.query_params.get("session_key", "")
    user = get_user_from_session(session_key)
    if not user:
        await ws.send_json({"type": "error", "message": "Not authenticated"})
        await ws.close()
        return

    username = user["username"]
    room_code = room_code.upper()
    room = get_room(room_code)
    if not room:
        await ws.send_json({"type": "error", "message": "Room not found"})
        await ws.close()
        return

    p = room.player(username)
    if not p:
        # Player not in room — try to rejoin (handles reconnection mid-game)
        ok, msg = join_room(room_code, username)
        if not ok:
            await ws.send_json({"type": "error", "message": msg})
            await ws.close()
            return
        p = room.player(username)

    # Evict old WebSocket for this user (duplicate login / stale tab)
    if room_code in _connections:
        old_ws = _connections[room_code].get(username)
        if old_ws and old_ws is not ws:
            try:
                await old_ws.send_json({"type": "kicked", "message": "Logged in from another device"})
                await old_ws.close()
            except Exception:
                pass  # already dead
            logger.info("Evicted old WS for %s in room %s", username, room_code)

    # Register connection
    if room_code not in _connections:
        _connections[room_code] = {}
    _connections[room_code][username] = ws
    p.is_connected = True

    # Sync coins from DB
    p.coins = get_coins(username)

    # Send initial state
    await _broadcast(room_code, room)
    await _broadcast_event(room_code, {
        "type": "event",
        "message": f"{username} connected",
    })

    # If this player reconnected and it's someone else's (disconnected) turn,
    # trigger the auto-fold chain
    await _handle_dc_chain(room_code, room)

    # Ensure turn timer is running if game is in progress
    if room.phase == RoomPhase.PLAYING:
        _schedule_turn_timer(room_code, room)

    try:
        _msg_times: list[float] = []
        while True:
            raw = await ws.receive_text()

            # ── Rate limiting ──────────────────────────────────────
            now = monotonic()
            _msg_times = [t for t in _msg_times if now - t < WS_RATE_WINDOW]
            if len(_msg_times) >= WS_RATE_LIMIT:
                await ws.send_json({"type": "ack", "ok": False, "message": "Too many messages — slow down"})
                continue
            _msg_times.append(now)

            msg = json.loads(raw)
            action = msg.get("action", "")

            # ── Heartbeat ───────────────────────────────────────
            if action == "ping":
                # Re-validate session — kicks stale tab after new login
                if not verify_session(session_key):
                    await ws.send_json({"type": "kicked", "message": "Logged in from another device"})
                    await ws.close()
                    return
                await ws.send_json({"type": "pong"})
                continue

            # ── Custom-flow actions (handle own ack/broadcast) ──
            if action == "chat":
                await _on_chat(room_code, username, msg)
                continue

            if action == "reaction":
                emoji = (msg.get("emoji") or "")[:4]
                if emoji:
                    await _broadcast_event(room_code, {
                        "type": "reaction",
                        "from": username,
                        "emoji": emoji,
                    })
                continue

            if action == "request_coins":
                await _on_request_coins(ws, room_code, room, username)
                continue

            if action == "exit":
                await _on_exit(ws, room_code, room, username)
                return

            # ── Standard game actions ────────────────────────────
            if action == "start":
                ok, reply = _on_start(room, username, msg)
            elif action == "restart":
                ok, reply = _on_restart(room, username)
            elif action in _SIMPLE_ACTIONS:
                ok, reply = _SIMPLE_ACTIONS[action](room, username)
            else:
                ok, reply = False, "Unknown action"

            await _post_action(ws, room_code, room, action, ok, reply)

    except WebSocketDisconnect:
        logger.info("%s disconnected from room %s", username, room_code)
    except Exception as e:
        logger.exception("WebSocket error for %s in %s: %s", username, room_code, e)
    finally:
        # Only clean up if THIS socket is still the registered one.
        # If a newer WS replaced us (eviction), skip cleanup — the new
        # handler owns the connection entry and the player state.
        conns = _connections.get(room_code, {})
        if conns.get(username) is ws:
            conns.pop(username, None)
            if not conns:
                _connections.pop(room_code, None)

            room = get_room(room_code)
            if room:
                p = room.player(username)
                if p:
                    p.is_connected = False
                    _persist_player_coins(p)

                # Check if all players disconnected — delete room
                if cleanup_empty_room(room_code):
                    _cancel_turn_timer(room_code)
                    return

                # Notify others about disconnect
                await _broadcast_event(room_code, {
                    "type": "event",
                    "message": f"{username} disconnected",
                })
                await _broadcast(room_code, room)

                # Auto-fold if it was this player's turn (or chain of disconnected)
                if await _handle_dc_chain(room_code, room):
                    # After auto-fold chain, check again if room is now empty
                    cleanup_empty_room(room_code)
