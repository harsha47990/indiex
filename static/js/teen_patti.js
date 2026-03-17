/* ═══════════════════════════════════════════════════════════════════════
   TEEN PATTI — CLIENT CONTROLLER
   ═══════════════════════════════════════════════════════════════════════ */
let tp = null;
document.addEventListener('DOMContentLoaded', () => {
tp = (() => {
  let ws = null;
  let roomCode = '';
  let state = null;
  let prevTurn = null;
  let _resultShown = false;
  let _turnTimer = null;
  let _turnDeadline = 0;
  let _turnTimeout = 30000;
  const me = indiex.username;

  // Player color palette for avatars
  const _COLORS = ['#6c5ce7','#00cec9','#e17055','#fdcb6e','#a29bfe','#55efc4','#fab1a0'];

  // Hand strength — server sends hand_strength (0-100%) already computed.
  // Just pick label + colour based on the value.
  function _getStrength(player) {
    const pct = player.hand_strength;
    if (pct == null) return null;
    let l, c;
    if (pct >= 95)      { l = 'INCREDIBLE'; c = '#ff4757'; }
    else if (pct >= 85)  { l = 'AMAZING'; c = '#ffd700'; }
    else if (pct >= 70)  { l = 'GREAT'; c = '#ffa502'; }
    else if (pct >= 50)  { l = 'GOOD'; c = '#2ed573'; }
    else if (pct >= 30)  { l = 'OKAY'; c = '#74b9ff'; }
    else                 { l = 'WEAK'; c = '#636e72'; }
    return { pct, l, c };
  }

  // ── Sound effects loaded from /static/js/sounds.js ────

  // ── DOM refs ───────────────────────────────────────────
  const $lobby     = document.getElementById('lobby-screen');
  const $room      = document.getElementById('room-screen');
  const $roomCode  = document.getElementById('room-code-display');
  const $playerCnt = document.getElementById('player-count');
  const $activeCnt = document.getElementById('active-count');
  const $roundCnt  = document.getElementById('round-count');
  const $infoBar   = document.getElementById('info-bar');
  const $pot       = document.getElementById('pot-val');
  const $tableVal  = document.getElementById('table-val');
  const $seenVal   = document.getElementById('seen-val');
  const $adminLobby  = document.getElementById('admin-lobby-ctrl');
  // New-round popup replaces old admin-result-ctrl
  const $lobbyPlayers = document.getElementById('lobby-players');
  const $gameTable = document.getElementById('game-table');
  const $tableStatus = document.getElementById('table-status');
  const $seats     = document.getElementById('seats');
  const $myArea    = document.getElementById('my-cards-area');
  const $myCards   = document.getElementById('my-cards');
  const $myHand    = document.getElementById('my-hand-name');
  const $actions   = document.getElementById('action-buttons');
  const $log       = document.getElementById('event-log');
  const $modeBadge = document.getElementById('mode-badge');
  const $jokerDisp = document.getElementById('joker-display');
  const $overlay   = document.getElementById('result-overlay');
  const $resWinner = document.getElementById('result-winner');
  const $resDetail = document.getElementById('result-detail');
  const $resPayout = document.getElementById('result-payout');
  const $waitingOverlay = document.getElementById('waiting-overlay');
  const $waitingInfo    = document.getElementById('waiting-players-info');

  // ── API helpers ────────────────────────────────────────
  async function post(url, body = {}) {
    const r = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    return r.json();
  }

  function addLog(msg) {
    const d = document.createElement('div');
    d.className = 'ev';
    d.textContent = `[${new Date().toLocaleTimeString()}] ${msg}`;
    $log.prepend(d);
  }

  // ── Create room ────────────────────────────────────────
  async function createRoom() {
    const res = await post('/games/teen-patti/api/create-room');
    if (res.ok) {
      roomCode = res.room_code;
      connectWS();
    } else {
      indiex.toast(res.error || 'Failed', 'error');
    }
  }

  // ── Join room ──────────────────────────────────────────
  async function joinRoom() {
    const code = document.getElementById('join-code-input').value.trim().toUpperCase();
    if (!code) { indiex.toast('Enter a room code', 'error'); return; }
    const res = await post('/games/teen-patti/api/join-room', { room_code: code });
    if (res.ok) {
      roomCode = res.room_code;
      if (res.waiting) {
        indiex.toast('Game in progress — you\'ll join when this round ends!', 'info');
      }
      connectWS();
    } else {
      indiex.toast(res.error || 'Failed', 'error');
    }
  }

  // ── WebSocket ──────────────────────────────────────────
  let _heartbeat = null;
  function connectWS() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const url = `${proto}://${location.host}/games/teen-patti/ws/${roomCode}?session_key=${indiex.sessionKey}`;
    ws = new WebSocket(url);

    ws.onopen = () => {
      $lobby.style.display = 'none';
      $room.style.display = 'block';
      const $backLink = document.getElementById('tp-back-link');
      if ($backLink) $backLink.style.display = 'none';
      $roomCode.textContent = roomCode;
      addLog('Connected to room ' + roomCode);
      // Heartbeat keepalive — keeps tunnel/proxy from killing idle connections
      clearInterval(_heartbeat);
      _heartbeat = setInterval(() => {
        if (ws && ws.readyState === 1) ws.send(JSON.stringify({action:'ping'}));
      }, 25000);
    };

    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.type === 'pong') return; // heartbeat reply — ignore
      if (msg.type === 'state') {
        state = msg.data;
        render();
      } else if (msg.type === 'event') {
        addLog(msg.message);
        if (msg.message.includes('Joker mode') || msg.message.includes('Zandu mode') || msg.message.includes('AK47 mode') || msg.message.includes('Muflis mode') || msg.message.includes('2-Card mode') || msg.message.includes('4-Card mode')) {
          indiex.toast(msg.message);
          playModeSound();
        }
        if (msg.message.includes('Joker #') && msg.message.includes('revealed')) {
          indiex.toast(msg.message);
          playModeSound();
        }
      } else if (msg.type === 'ack') {
        if (!msg.ok) indiex.toast(msg.message, 'error');
      } else if (msg.type === 'chat') {
        appendChat(msg.from, msg.text);
        if (msg.from !== me) {
          showChatBubble(msg.from, msg.text);
          playChatSound();
        }
      } else if (msg.type === 'sideshow_reveal') {
        showSideshowReveal(msg.data, true);
      } else if (msg.type === 'sideshow_result') {
        // Only show public popup if we're NOT one of the participants
        // (participants get the full card reveal above)
        if (msg.data.challenger !== me && msg.data.opponent !== me) {
          showSideshowReveal(msg.data, false);
        }
      } else if (msg.type === 'reaction') {
        _showFloatingReaction(msg.from, msg.emoji);
      } else if (msg.type === 'exit') {
        // Server confirmed we left — go back to games page
        addLog(msg.message);
        indiex.toast('You left the room');
        setTimeout(() => { window.location.href = '/games/teen-patti'; }, 600);
        return;
      } else if (msg.type === 'kicked') {
        // Another device logged in — stop reconnect and redirect
        _exiting = true;
        roomCode = null;
        if (ws) { try { ws.close(); } catch(e) {} }
        indiex.toast(msg.message, 'error');
        setTimeout(() => { window.location.href = '/'; }, 1500);
        return;
      } else if (msg.type === 'error') {
        indiex.toast(msg.message, 'error');
        // Terminal errors — stop auto-reconnect and go back to lobby
        _exiting = true;
        roomCode = null;
        if (ws) { try { ws.close(); } catch(e) {} }
        $room.style.display = 'none';
        $lobby.style.display = 'block';
        const $bl = document.getElementById('tp-back-link');
        if ($bl) $bl.style.display = '';
      }
    };

    ws.onclose = () => {
      clearInterval(_heartbeat);
      addLog('Disconnected');
      // Auto-reconnect after 2 seconds (unless user intentionally exited)
      if (!_exiting && roomCode) {
        addLog('Reconnecting in 2s…');
        setTimeout(() => {
          if (!_exiting && roomCode) {
            addLog('Reconnecting…');
            connectWS();
          }
        }, 2000);
      }
    };
    ws.onerror = () => { addLog('WebSocket error'); };
  }

  // ── Send action ────────────────────────────────────────
  function send(action, extra = {}) {
    if (ws && ws.readyState === 1) {
      ws.send(JSON.stringify({ action, ...extra }));
    }
  }

  // ── RENDER ─────────────────────────────────────────────
  function render() {
    if (!state) return;
    const s = state;
    const isAdmin = s.admin === me;
    const isStarter = (s.starter || s.admin) === me;
    const myPlayer = s.players.find(p => p.username === me);
    const amWaiting = !myPlayer && s.waiting_players && s.waiting_players.some(w => w.username === me);

    // ── WAITING OVERLAY (mid-game join queue) ──
    if (amWaiting) {
      $waitingOverlay.style.display = 'flex';
      // Hide game elements
      $gameTable.style.display  = 'none';
      $myArea.style.display     = 'none';
      $actions.style.display    = 'none';
      $infoBar.style.display    = 'none';
      $lobbyPlayers.style.display = 'none';
      $adminLobby.style.display = 'none';
      $overlay.classList.remove('show');
      // Show info about current game
      const total = s.players.length;
      const waitCount = s.waiting_players ? s.waiting_players.length : 0;
      let info = `${total} player${total !== 1 ? 's' : ''} in game`;
      if (waitCount > 1) info += ` · ${waitCount} waiting`;
      $waitingInfo.textContent = info;
      return;
    }
    $waitingOverlay.style.display = 'none';

    // Counts
    $playerCnt.textContent = s.players.length;
    $activeCnt.textContent = s.active_count;
    $roundCnt.textContent  = s.turn_number || 0;

    // Keep topbar coin badge in sync with live game state
    if (myPlayer && myPlayer.coins != null) {
      const $topCoins = document.getElementById('coin-count');
      if ($topCoins) $topCoins.textContent = myPlayer.coins.toLocaleString();
    }

    // ── LOBBY phase ────────────────
    if (s.phase === 'lobby') {
      $gameTable.style.display = 'none';
      $myArea.style.display    = 'none';
      $actions.style.display   = 'none';
      $infoBar.style.display   = 'none';
      $overlay.classList.remove('show');
      _turnDeadline = 0;
      if (_turnTimer) { clearInterval(_turnTimer); _turnTimer = null; }

      // Show lobby players
      $lobbyPlayers.style.display = 'flex';
      $lobbyPlayers.innerHTML = s.players.map(p => `
        <div class="lobby-player ${p.username === s.admin ? 'admin-tag' : ''}">
          <span class="dot"></span>
          ${p.username} ${p.username === s.admin ? '👑' : ''}
          <span style="color:var(--warning);font-size:11px;">🪙 ${p.coins}</span>
        </div>
      `).join('');

      // Start controls (admin or winner depending on mode_picker)
      $adminLobby.style.display = isStarter ? 'block' : 'none';
      // Only admin can change mode_picker toggle
      const $mpToggle = document.getElementById('mode-picker-select');
      if ($mpToggle) {
        $mpToggle.style.display = isAdmin ? 'inline-block' : 'none';
        $mpToggle.value = s.mode_picker || 'admin';
      }
      if (isStarter && s.game_type) {
        document.getElementById('game-type-select').value = s.game_type;
      }
      // Show heading with who can start
      const $ctrlTitle = document.querySelector('#admin-lobby-ctrl h3');
      if ($ctrlTitle) {
        $ctrlTitle.textContent = isAdmin ? '⚙️ Room Admin Controls' : '🏆 Winner\'s Pick — Choose the next mode!';
      }
      return;
    }

    // ── PLAYING / RESULT phase ─────
    $lobbyPlayers.style.display = 'none';
    $adminLobby.style.display   = 'none';
    $gameTable.style.display     = 'flex';
    $myArea.style.display        = 'block';
    $infoBar.style.display       = 'flex';

    $pot.textContent      = s.pot.toLocaleString();
    $tableVal.textContent = s.table_amount;
    $seenVal.textContent  = s.table_amount * 2;

    // Table status + turn sound
    if (s.phase === 'playing') {
      $tableStatus.textContent = `${s.current_turn}'s turn`;
      if (s.current_turn === me && prevTurn !== me) {
        playTurnSound();
        // Vibrate on mobile when it's your turn
        if (navigator.vibrate) navigator.vibrate([100, 50, 100]);
      }
      prevTurn = s.current_turn;
    } else {
      $tableStatus.textContent = `🏆 ${s.winner} wins!`;
      prevTurn = null;
    }

    // ── Turn timer ──
    if (s.phase === 'playing' && s.turn_deadline) {
      _turnDeadline = s.turn_deadline * 1000;
      _turnTimeout = (s.turn_timeout || 30) * 1000;
      if (!_turnTimer) _turnTimer = setInterval(_updateTurnTimer, 100);
    } else {
      _turnDeadline = 0;
      if (_turnTimer) { clearInterval(_turnTimer); _turnTimer = null; }
    }

    // ── Mode indicator + joker card ──
    if (s.game_type && s.game_type !== 'normal') {
      if (s.game_type === 'joker') {
        $modeBadge.textContent = '🃏 JOKER MODE';
        $modeBadge.className = 'mode-badge joker';
      } else if (s.game_type === 'zandu') {
        $modeBadge.textContent = '🃏 ZANDU — 3 JOKERS';
        $modeBadge.className = 'mode-badge joker';
      } else if (s.game_type === 'ak47') {
        $modeBadge.textContent = '🔫 AK47 — A, K, 4, 7 WILD';
        $modeBadge.className = 'mode-badge joker';
      } else if (s.game_type === 'muflis') {
        $modeBadge.textContent = '🔄 MUFLIS — LOWEST WINS';
        $modeBadge.className = 'mode-badge muflis';
      } else if (s.game_type === '2card') {
        $modeBadge.textContent = '✂️ 2-CARD MODE';
        $modeBadge.className = 'mode-badge twocard';
      } else if (s.game_type === '4card') {
        $modeBadge.textContent = '💥 4-CARD MODE';
        $modeBadge.className = 'mode-badge fourcard';
      }
      $modeBadge.style.display = 'block';
    } else {
      $modeBadge.style.display = 'none';
    }

    if (s.game_type === 'joker' && s.joker_card) {
      const jc = s.joker_card;
      const isRed = jc.suit === '♥' || jc.suit === '♦';
      $jokerDisp.innerHTML = `
        <div class="joker-label">JOKER</div>
        <div class="joker-card ${isRed ? 'red' : ''}">
          <span class="rank">${jc.rank}</span><span class="suit">${jc.suit}</span>
        </div>`;
      $jokerDisp.style.display = 'flex';
    } else if (s.game_type === 'zandu' && s.zandu_jokers) {
      const cards = s.zandu_jokers;
      const revealed = s.zandu_revealed || 0;
      $jokerDisp.innerHTML = `
        <div class="joker-label">ZANDU JOKERS (${revealed}/3)</div>
        <div class="zandu-cards">
          ${cards.map((c, i) => {
            if (c) {
              const isRed = c.suit === '♥' || c.suit === '♦';
              return `<div class="joker-card ${isRed ? 'red' : ''}" title="Joker #${i+1}">
                <span class="rank">${c.rank}</span><span class="suit">${c.suit}</span>
              </div>`;
            } else {
              return `<div class="joker-card face-down" title="Hidden Joker #${i+1}">🂠</div>`;
            }
          }).join('')}
        </div>`;
      $jokerDisp.style.display = 'flex';
    } else {
      $jokerDisp.style.display = 'none';
    }

    // ── Render seats ────────────────
    $seats.innerHTML = s.players.map((p, pIdx) => {
      const isMe   = p.username === me;
      const isTurn = s.current_turn === p.username && s.phase === 'playing';
      let statusClass = '';
      let statusText  = '';
      if (p.is_folded)      { statusClass = 'fold'; statusText = 'FOLDED'; }
      else if (p.is_viewing) { statusClass = 'viewing'; statusText = 'VIEWING'; }
      else if (p.is_seen)   { statusClass = 'seen'; statusText = 'SEEN'; }
      else                   { statusClass = 'blind'; statusText = 'BLIND'; }

      // Cards
      let cardsHTML = '';
      if (p.cards) {
        cardsHTML = p.cards.map(c => {
          if (c.rank === '?') return `<div class="mini-card face-down"></div>`;
          const isRed = c.suit === '♥' || c.suit === '♦';
          return `<div class="mini-card face-up ${isRed ? 'red' : ''}">${c.rank}${c.suit}</div>`;
        }).join('');
      } else {
        const n = p.card_count || 0;
        cardsHTML = Array(n).fill('<div class="mini-card face-down"></div>').join('');
      }

      const isOffline = p.is_connected === false;
      const isSittingOut = p.is_sitting_out === true;
      return `
        <div class="seat ${isTurn ? 'active-turn' : ''} ${p.is_folded ? 'folded' : ''} ${isMe ? 'is-me' : ''} ${isOffline ? 'offline' : ''} ${isSittingOut ? 'sitting-out' : ''}">
          ${p.username === s.admin ? '<span class="seat-admin-badge">ADMIN</span>' : ''}
          ${isOffline ? '<span class="seat-offline-badge">⛔ OFFLINE</span>' : ''}
          ${isSittingOut && !isOffline ? '<span class="seat-sitting-badge">💸 LOW COINS</span>' : ''}
          <div class="seat-avatar" style="background:${_COLORS[pIdx % _COLORS.length]}">${p.username.charAt(0).toUpperCase()}</div>
          <div class="seat-name">${p.username}${isMe ? ' (You)' : ''}</div>
          <div class="seat-coins">🪙 ${p.coins.toLocaleString()}</div>
          <div class="seat-status ${isSittingOut ? 'sitting' : statusClass}">${isSittingOut ? 'SITTING OUT' : statusText}</div>
          <div class="seat-bet">bet: ${p.total_bet}</div>
          <div class="seat-cards">${cardsHTML}</div>
          ${isTurn && !isSittingOut ? '<div class="seat-timer"><div class="seat-timer-bar"></div></div><div class="seat-countdown" id="turn-countdown"></div>' : ''}
        </div>
      `;
    }).join('');

    // ── Sitting-out popup for self ────
    const $sittingPopup = document.getElementById('sitting-out-popup');
    if ($sittingPopup) {
      if (myPlayer && myPlayer.is_sitting_out && s.phase === 'playing') {
        const minC = s.min_coins || 0;
        $sittingPopup.innerHTML = `
          <div class="sitting-popup-inner">
            <div class="sitting-popup-icon">💸</div>
            <div class="sitting-popup-title">Not Enough Coins</div>
            <div class="sitting-popup-msg">You need at least <strong>${minC} 🪙</strong> to play (table amount: ${s.table_amount})</div>
            <div class="sitting-popup-coins">Current balance: <strong>${myPlayer.coins} 🪙</strong></div>
            <div class="sitting-popup-actions">
              <button class="sitting-btn request" onclick="tp.requestCoins()">🪙 Request & Refresh Coins</button>
            </div>
            <div class="sitting-popup-hint">Ask admin to load coins, then click the button above to check.</div>
          </div>`;
        $sittingPopup.style.display = 'flex';
      } else {
        $sittingPopup.style.display = 'none';
      }
    }

    // ── My cards (big) ──────────────
    if (myPlayer && myPlayer.cards) {
      $myCards.innerHTML = myPlayer.cards.map(c => {
        if (c.rank === '?') return `<div class="big-card face-down"></div>`;
        const isRed = c.suit === '♥' || c.suit === '♦';
        return `<div class="big-card face-up ${isRed ? 'red' : ''}">
          <span class="rank">${c.rank}</span><span class="suit">${c.suit}</span>
        </div>`;
      }).join('');
      // Hand name — always provided by server when cards are visible
      const cardsRevealed = myPlayer.cards.some(c => c.rank !== '?');
      if (myPlayer.hand_name && cardsRevealed) {
        let hn = myPlayer.hand_name;
        if (s.game_type === 'muflis') hn += ' (lower is better!)';
        $myHand.textContent = hn;
        // Strength bar — only after cards are revealed, stays entire round
        const str = _getStrength(myPlayer);
        let $sb = document.getElementById('hand-strength-bar');
        if (str) {
          if (!$sb) {
            $sb = document.createElement('div');
            $sb.id = 'hand-strength-bar';
            $sb.className = 'hand-strength';
            $myHand.after($sb);
          }
          $sb.innerHTML = `<div class="hs-track"><div class="hs-fill" style="width:${str.pct}%;background:${str.c}"></div></div><span class="hs-label" style="color:${str.c}">${str.pct}% ${str.l}</span>`;
        } else if ($sb) { $sb.remove(); }
      } else if (!cardsRevealed) {
        $myHand.textContent = '(View cards to peek)';
        const $sb = document.getElementById('hand-strength-bar');
        if ($sb) $sb.remove();
      } else {
        $myHand.textContent = '';
      }
    } else {
      $myCards.innerHTML = '';
      $myHand.textContent = '';
    }

    // ── Action buttons ──────────────
    if (s.phase === 'playing' && s.current_turn === me && myPlayer && !myPlayer.is_folded) {
      $actions.style.display = 'flex';
      let btns = '';

      if (myPlayer.is_viewing) {
        // Mid-view: player peeked at cards, now must Play Seen or Fold
        btns += `<button class="act-btn seen" onclick="tp.doSeen()">
          👁️ Play Seen (${s.table_amount * 2} 🪙)</button>`;
        btns += `<button class="act-btn fold" onclick="tp.confirmFold()">
          🏳️ Fold</button>`;
      } else if (!myPlayer.is_seen) {
        // Blind: Play Blind or View Cards
        btns += `<button class="act-btn blind" onclick="tp.doBlind()">
          🙈 Play Blind (${s.table_amount} 🪙)</button>`;
        btns += `<button class="act-btn view" onclick="tp.doView()">
          👀 View Cards</button>`;
        btns += `<button class="act-btn fold" onclick="tp.confirmFold()">
          🏳️ Fold</button>`;
        if (s.active_count === 2) {
          btns += `<button class="act-btn show" onclick="tp.doShow()">
            🃏 Show (${s.table_amount} 🪙)</button>`;
        }
      } else {
        // Seen: normal seen actions
        btns += `<button class="act-btn seen" onclick="tp.doSeen()">
          👁️ Continue Seen (${s.table_amount * 2} 🪙)</button>`;
        btns += `<button class="act-btn fold" onclick="tp.confirmFold()">
          🏳️ Fold</button>`;
        if (s.active_count === 2) {
          btns += `<button class="act-btn show" onclick="tp.doShow()">
            🃏 Show (${s.table_amount * 2} 🪙)</button>`;
        }
        if (s.side_show_unlocked && s.active_count > 2) {
          btns += `<button class="act-btn sideshow" onclick="tp.doSideshow()">
            🤝 Side Show (${s.table_amount * 2} 🪙)</button>`;
        }
      }

      $actions.innerHTML = btns;
    } else {
      $actions.style.display = 'none';
    }

    // ── Low coins warning (after action, before next turn) ──
    if (s.phase === 'playing' && myPlayer && !myPlayer.is_folded && !myPlayer.is_sitting_out
        && s.current_turn !== me) {
      const nextCost = myPlayer.is_seen ? s.table_amount * 2 : s.table_amount;
      if (myPlayer.coins < nextCost && myPlayer.coins >= 0) {
        const action = s.active_count === 2 ? 'Auto-Show (cards revealed)' : 'Auto-Fold (you lose your bet)';
        let warn = document.getElementById('low-coin-warning');
        if (!warn) {
          warn = document.createElement('div');
          warn.id = 'low-coin-warning';
          warn.className = 'low-coin-warning';
          $myArea.before(warn);
        }
        warn.innerHTML = `
          <span class="lcw-icon">⚠️</span>
          <div class="lcw-text">
            <strong>Insufficient coins!</strong> You have <strong>${myPlayer.coins} 🪙</strong> but need <strong>${nextCost} 🪙</strong>.
            <br>Next turn: <strong>${action}</strong>
          </div>`;
      } else {
        const existing = document.getElementById('low-coin-warning');
        if (existing) existing.remove();
      }
    } else {
      const existing = document.getElementById('low-coin-warning');
      if (existing) existing.remove();
    }

    // ── Result phase ────────────────
    const $resultBanner = document.getElementById('result-banner');
    if (s.phase === 'result') {
      // Only show overlay + sound ONCE per result
      if (!_resultShown) {
        _resultShown = true;
        $overlay.classList.add('show');
        $resWinner.textContent = s.winner;
        $resDetail.textContent = `Total pot: ${s.pot} 🪙`;
        $resPayout.textContent = `${s.winner} receives ${s.pot} 🪙`;

        // Result overlay buttons — everyone gets "Next Round" (starter) or "Close"
        const $resActions = document.getElementById('result-actions');
        if (isStarter) {
          $resActions.innerHTML = `
            <button class="nr-start-btn" style="max-width:200px;padding:12px 24px;font-size:14px;"
              onclick="tp.openNewRound()">🚀 Next Round</button>
            <button style="padding:12px 24px;border:none;border-radius:10px;
              background:var(--surface-2);color:var(--text);font-weight:700;cursor:pointer;
              border:1px solid var(--border);" 
              onclick="document.getElementById('result-overlay').classList.remove('show')">
              Close</button>`;
        } else {
          $resActions.innerHTML = `
            <button style="padding:12px 24px;border:none;border-radius:10px;
              background:var(--primary);color:#fff;font-weight:700;cursor:pointer;"
              onclick="document.getElementById('result-overlay').classList.remove('show')">
              Close</button>`;
        }

        // Victory fanfare
        playWinSound();
        if (navigator.vibrate) navigator.vibrate([100,50,100,50,200]);
        _fireConfetti();

        // Refresh top bar coins
        indiex.fetchCoins();
      }

      // Persistent banner — always visible during result phase
      // (safety net when overlay/popup are closed)
      const starter = s.starter || s.admin;
      if (isStarter) {
        $resultBanner.className = 'result-banner starter';
        $resultBanner.innerHTML = `
          <div class="rb-title">🏆 Round over — you're up!</div>
          <button class="rb-btn" onclick="tp.openNewRound()">🚀 Start Next Round</button>`;
      } else {
        $resultBanner.className = 'result-banner waiting';
        $resultBanner.innerHTML = `
          <div class="rb-waiting">⏳ Waiting for <strong>${starter}</strong> to start the next round…</div>`;
      }
      $resultBanner.style.display = 'block';
    } else {
      // Hide banner in non-result phases
      $resultBanner.style.display = 'none';
      // Close new-round popup only when game actually starts (not on lobby)
      if (s.phase === 'playing') {
        document.getElementById('newround-overlay').classList.remove('show');
      }
      _resultShown = false;
    }
  }

  // ── Turn timer countdown ───────────────────────────────
  function _updateTurnTimer() {
    if (!_turnDeadline) return;
    const remaining = Math.max(0, _turnDeadline - Date.now());
    const pct = Math.max(0, (remaining / _turnTimeout) * 100);
    const secs = Math.ceil(remaining / 1000);
    document.querySelectorAll('.seat-timer-bar').forEach(bar => {
      bar.style.width = pct + '%';
      bar.style.background = pct < 20 ? '#ff4757' : pct < 50 ? '#ffa502' : '#2ed573';
    });
    const $cd = document.getElementById('turn-countdown');
    if ($cd) $cd.textContent = secs > 0 ? secs + 's' : '';
  }

  // ── Side-show reveal overlay ────────────────────────────
  let _ssTimer = null;
  function showSideshowReveal(data, showCards) {
    const $ssOverlay = document.getElementById('sideshow-overlay');
    const $ssHands   = document.getElementById('sideshow-hands');
    const $ssTimerEl = document.getElementById('sideshow-timer');

    function renderCards(cards) {
      return cards.map(c => {
        const isRed = c.suit === '♥' || c.suit === '♦';
        return `<div class="sh-card ${isRed ? 'red' : ''}">
          <span class="sh-rank">${c.rank}</span><span class="sh-suit">${c.suit}</span>
        </div>`;
      }).join('');
    }

    const challenger = data.challenger;
    const opponent = data.opponent;
    const loser = data.loser;

    const hands = [
      { name: challenger, cards: data.challenger_cards, handName: data.challenger_hand, isLoser: loser === challenger },
      { name: opponent,   cards: data.opponent_cards,   handName: data.opponent_hand,   isLoser: loser === opponent },
    ];

    $ssHands.innerHTML = hands.map(h => {
      const cardsSection = showCards && h.cards
        ? `<div class="sh-cards">${renderCards(h.cards)}</div><div class="sh-hand-name">${h.handName || ''}</div>`
        : `<div class="sh-cards"><div class="sh-hidden">🃠 🃠 🃠</div></div>`;
      return `
        <div class="sideshow-hand ${h.isLoser ? 'loser' : 'winner'}">
          <div class="sh-name">${h.name}${h.name === me ? ' (You)' : ''}</div>
          <div class="sh-result">${h.isLoser ? '❌ Lost — Folded' : '✅ Won'}</div>
          ${cardsSection}
        </div>`;
    }).join('');

    $ssOverlay.classList.add('show');

    // Auto-close after 5 seconds
    if (_ssTimer) clearInterval(_ssTimer);
    let countdown = 5;
    $ssTimerEl.textContent = `Auto-closing in ${countdown}s…`;
    _ssTimer = setInterval(() => {
      countdown--;
      if (countdown <= 0) {
        clearInterval(_ssTimer);
        _ssTimer = null;
        $ssOverlay.classList.remove('show');
      } else {
        $ssTimerEl.textContent = `Auto-closing in ${countdown}s…`;
      }
    }, 1000);
  }

  // ── Chat helpers ───────────────────────────────────────
  const $chatMessages = document.getElementById('chat-messages');
  const $chatInput    = document.getElementById('chat-input');

  function sendChat() {
    const text = $chatInput.value.trim();
    if (!text) return;
    send('chat', { text });
    $chatInput.value = '';
  }

  function appendChat(from, text) {
    const d = document.createElement('div');
    d.className = `chat-msg ${from === me ? 'chat-me' : ''}`;
    const time = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    d.innerHTML = `<span class="chat-author">${from}</span>${escapeHtml(text)}<span class="chat-time">${time}</span>`;
    $chatMessages.appendChild(d);
    $chatMessages.scrollTop = $chatMessages.scrollHeight;
  }

  // ── Floating chat bubbles ──────────────────────────────
  // Container injected once into the room screen
  let _bubbleBox = null;
  function _ensureBubbleBox() {
    if (_bubbleBox) return;
    _bubbleBox = document.createElement('div');
    _bubbleBox.id = 'chat-bubble-box';
    document.getElementById('room-screen').appendChild(_bubbleBox);
  }

  function showChatBubble(from, text) {
    _ensureBubbleBox();
    const isMe = from === me;
    const bubble = document.createElement('div');
    bubble.className = `chat-bubble ${isMe ? 'bubble-me' : 'bubble-other'}`;
    bubble.innerHTML = `<span class="cb-name">${escapeHtml(from)}</span><span class="cb-text">${escapeHtml(text)}</span>`;
    _bubbleBox.appendChild(bubble);

    // Trigger entrance animation on next frame
    requestAnimationFrame(() => bubble.classList.add('show'));

    // Fade out after 4s
    setTimeout(() => {
      bubble.classList.add('fade-out');
      bubble.addEventListener('animationend', () => bubble.remove());
    }, 4000);

    // Cap at 5 visible bubbles
    while (_bubbleBox.children.length > 5) _bubbleBox.firstChild.remove();
  }

  function escapeHtml(s) {
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
  }

  // ── Emoji reactions ──────────────────────────────────
  function sendReaction(emoji) {
    send('reaction', { emoji });
    _closeReactionPicker();
  }
  function _showFloatingReaction(from, emoji) {
    const $table = document.getElementById('game-table');
    if (!$table) return;
    const el = document.createElement('div');
    el.className = 'float-reaction';
    el.innerHTML = `<span class="fr-emoji">${emoji}</span><span class="fr-name">${escapeHtml(from)}</span>`;
    el.style.left = (20 + Math.random() * 60) + '%';
    $table.appendChild(el);
    requestAnimationFrame(() => el.classList.add('rise'));
    setTimeout(() => el.remove(), 2000);
  }
  function _toggleReactionPicker() {
    const $rp = document.getElementById('reaction-picker');
    if ($rp) $rp.classList.toggle('show');
  }
  function _closeReactionPicker() {
    const $rp = document.getElementById('reaction-picker');
    if ($rp) $rp.classList.remove('show');
  }

  // ── Actions ────────────────────────────────────────────
  let _exiting = false;

  function startGame() {
    const amt = parseInt(document.getElementById('table-amount-input').value) || 10;
    const gameType = document.getElementById('game-type-select').value;
    const mp = document.getElementById('mode-picker-select');
    const modePicker = mp ? mp.value : 'admin';
    send('start', { table_amount: amt, game_type: gameType, mode_picker: modePicker });
  }
  function doBlind()    { if (navigator.vibrate) navigator.vibrate(50); send('blind'); }
  function doView()     { if (navigator.vibrate) navigator.vibrate(50); send('view'); }
  function doSeen()     { if (navigator.vibrate) navigator.vibrate(50); send('seen'); }
  function doFold()     { _closeFoldConfirm(); if (navigator.vibrate) navigator.vibrate([100,30,100]); send('fold'); }
  function doShow()     { if (navigator.vibrate) navigator.vibrate([50,30,50,30,100]); send('show'); }
  function doSideshow() { if (navigator.vibrate) navigator.vibrate([50,30,50]); send('sideshow'); }

  function confirmFold() {
    const $fc = document.getElementById('fold-confirm-overlay');
    if ($fc) { $fc.classList.add('show'); }
  }
  function _closeFoldConfirm() {
    const $fc = document.getElementById('fold-confirm-overlay');
    if ($fc) { $fc.classList.remove('show'); }
  }
  function cancelFold() { _closeFoldConfirm(); }
  function restart()    { send('restart'); }

  function openNewRound() {
    // Close result overlay, send restart, then show new-round popup
    $overlay.classList.remove('show');
    send('restart');
    // Pre-fill with last used values
    const $nr = document.getElementById('newround-overlay');
    if (state) {
      document.getElementById('nr-table-amount').value = state.table_amount || 10;
      const $gt = document.getElementById('nr-game-type');
      if ($gt && state.game_type) $gt.value = state.game_type;
    }
    $nr.classList.add('show');
  }

  function startNextRound() {
    const amt = parseInt(document.getElementById('nr-table-amount').value) || 10;
    const gameType = document.getElementById('nr-game-type').value;
    // Use whatever mode_picker is already set on the room
    const mp = document.getElementById('mode-picker-select');
    const modePicker = mp ? mp.value : 'admin';
    send('start', { table_amount: amt, game_type: gameType, mode_picker: modePicker });
    document.getElementById('newround-overlay').classList.remove('show');
  }

  function requestCoins() { send('request_coins'); indiex.toast('Checking coins & notifying admin...'); }

  function exitRoom() {
    if (!confirm('Leave this room? If a game is in progress, you will be auto-folded.')) return;
    _exiting = true;
    send('exit');
  }

  function _updateMuteBtn() {
    const $m = document.getElementById('mute-btn');
    if ($m) $m.textContent = isSoundMuted() ? '🔇' : '🔊';
  }
  function muteToggle() {
    toggleMute();
    _updateMuteBtn();
    indiex.toast(isSoundMuted() ? 'Sound muted' : 'Sound on');
  }
  // Set initial icon
  setTimeout(_updateMuteBtn, 0);

  // ── Confetti celebration ─────────────────────────────
  function _fireConfetti() {
    const canvas = document.createElement('canvas');
    canvas.className = 'confetti-canvas';
    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;
    document.body.appendChild(canvas);
    const ctx = canvas.getContext('2d');
    const colors = ['#ff4757','#ffd700','#2ed573','#6c5ce7','#00cec9','#ffa502','#ff6b81'];
    const particles = [];
    for (let i = 0; i < 120; i++) {
      particles.push({
        x: Math.random() * canvas.width,
        y: -10 - Math.random() * canvas.height * 0.3,
        w: 4 + Math.random() * 6,
        h: 8 + Math.random() * 10,
        color: colors[Math.floor(Math.random() * colors.length)],
        vx: (Math.random() - 0.5) * 4,
        vy: 2 + Math.random() * 4,
        rot: Math.random() * 360,
        rv: (Math.random() - 0.5) * 12,
      });
    }
    let frame = 0;
    function draw() {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      let alive = false;
      particles.forEach(p => {
        p.x += p.vx; p.y += p.vy; p.vy += 0.08; p.rot += p.rv;
        if (p.y < canvas.height + 20) alive = true;
        ctx.save();
        ctx.translate(p.x, p.y);
        ctx.rotate(p.rot * Math.PI / 180);
        ctx.fillStyle = p.color;
        ctx.fillRect(-p.w / 2, -p.h / 2, p.w, p.h);
        ctx.restore();
      });
      frame++;
      if (alive && frame < 180) requestAnimationFrame(draw);
      else canvas.remove();
    }
    draw();
  }

  return { createRoom, joinRoom, startGame, doBlind, doView, doSeen, doFold, doShow, doSideshow, restart, sendChat, exitRoom, requestCoins, confirmFold, cancelFold, openNewRound, startNextRound, toggleMute: muteToggle, sendReaction, toggleReactions: _toggleReactionPicker };
})();
}); // end DOMContentLoaded
