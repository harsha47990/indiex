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
  const me = indiex.username;

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
        if (msg.message.includes('Joker mode') || msg.message.includes('Muflis mode') || msg.message.includes('2-Card mode') || msg.message.includes('4-Card mode')) {
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
        showSideshowReveal(msg.data);
      } else if (msg.type === 'exit') {
        // Server confirmed we left — go back to games page
        addLog(msg.message);
        indiex.toast('You left the room');
        setTimeout(() => { window.location.href = '/games/teen-patti'; }, 600);
        return;
      } else if (msg.type === 'error') {
        indiex.toast(msg.message, 'error');
        // Terminal errors — stop auto-reconnect and go back to lobby
        _exiting = true;
        roomCode = null;
        if (ws) { try { ws.close(); } catch(e) {} }
        $room.style.display = 'none';
        $lobby.style.display = 'block';
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

    // Counts
    $playerCnt.textContent = s.players.length;
    $activeCnt.textContent = s.active_count;
    $roundCnt.textContent  = s.round_count;

    // Keep topbar coin badge in sync with live game state
    if (myPlayer) {
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

    // ── Mode indicator + joker card ──
    if (s.game_type && s.game_type !== 'normal') {
      if (s.game_type === 'joker') {
        $modeBadge.textContent = '🃏 JOKER MODE';
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
    } else {
      $jokerDisp.style.display = 'none';
    }

    // ── Render seats ────────────────
    $seats.innerHTML = s.players.map(p => {
      const isMe   = p.username === me;
      const isTurn = s.current_turn === p.username && s.phase === 'playing';
      let statusClass = '';
      let statusText  = '';
      if (p.is_folded)     { statusClass = 'fold'; statusText = 'FOLDED'; }
      else if (p.is_seen)  { statusClass = 'seen'; statusText = 'SEEN'; }
      else                  { statusClass = 'blind'; statusText = 'BLIND'; }

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
          ${isSittingOut ? '<span class="seat-sitting-badge">💸 LOW COINS</span>' : ''}
          <div class="seat-name">${p.username}${isMe ? ' (You)' : ''}</div>
          <div class="seat-coins">🪙 ${p.coins.toLocaleString()}</div>
          <div class="seat-status ${isSittingOut ? 'sitting' : statusClass}">${isSittingOut ? 'SITTING OUT' : statusText}</div>
          <div class="seat-bet">bet: ${p.total_bet}</div>
          <div class="seat-cards">${cardsHTML}</div>
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
      if (myPlayer.hand_name) {
        let hn = myPlayer.hand_name;
        if (s.game_type === 'muflis') hn += ' (lower is better!)';
        $myHand.textContent = hn;
      } else {
        $myHand.textContent = myPlayer.cards[0].rank !== '?' ? '' : '(Play Seen to reveal)';
      }
    } else {
      $myCards.innerHTML = '';
      $myHand.textContent = '';
    }

    // ── Action buttons ──────────────
    if (s.phase === 'playing' && s.current_turn === me && myPlayer && !myPlayer.is_folded) {
      $actions.style.display = 'flex';
      let btns = '';

      if (!myPlayer.is_seen) {
        btns += `<button class="act-btn blind" onclick="tp.doBlind()">
          🙈 Play Blind (${s.table_amount} 🪙)</button>`;
        btns += `<button class="act-btn seen" onclick="tp.doSeen()">
          👁️ Seen (${s.table_amount * 2} 🪙)</button>`;
      } else {
        btns += `<button class="act-btn seen" onclick="tp.doSeen()">
          👁️ Continue Seen (${s.table_amount * 2} 🪙)</button>`;
      }

      btns += `<button class="act-btn fold" onclick="tp.confirmFold()">
        🏳️ Fold</button>`;

      if (s.active_count === 2) {
        const showCost = myPlayer.is_seen ? s.table_amount * 2 : s.table_amount;
        btns += `<button class="act-btn show" onclick="tp.doShow()">
          🃏 Show (${showCost} 🪙)</button>`;
      }

      if (s.side_show_unlocked && myPlayer.is_seen && s.active_count > 2) {
        btns += `<button class="act-btn sideshow" onclick="tp.doSideshow()">
          🤝 Side Show (${s.table_amount * 2} 🪙)</button>`;
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
    if (s.phase === 'result') {
      // Only show overlay + sound ONCE per result
      if (!_resultShown) {
        _resultShown = true;
        $overlay.classList.add('show');
        $resWinner.textContent = s.winner;
        $resDetail.textContent = `Total pot: ${s.pot} 🪙`;
        $resPayout.textContent = `${s.winner} receives ${s.pot} 🪙`;

        // Result overlay buttons — starter gets "Next Round", others get "Close"
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

        // Refresh top bar coins
        indiex.fetchCoins();
      }
    } else {
      // Close new-round popup when game starts
      document.getElementById('newround-overlay').classList.remove('show');
      _resultShown = false;
    }
  }

  // ── Side-show reveal overlay ────────────────────────────
  let _ssTimer = null;
  function showSideshowReveal(data) {
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

    const hands = [
      { name: data.challenger, cards: data.challenger_cards, handName: data.challenger_hand, isLoser: data.loser === data.challenger },
      { name: data.opponent,   cards: data.opponent_cards,   handName: data.opponent_hand,   isLoser: data.loser === data.opponent },
    ];

    $ssHands.innerHTML = hands.map(h => `
      <div class="sideshow-hand ${h.isLoser ? 'loser' : 'winner'}">
        <div class="sh-name">${h.name}${h.name === me ? ' (You)' : ''}</div>
        <div class="sh-result">${h.isLoser ? '❌ Lost' : '✅ Won'}</div>
        <div class="sh-cards">${renderCards(h.cards)}</div>
        <div class="sh-hand-name">${h.handName || ''}</div>
      </div>
    `).join('');

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

  // ── Actions ────────────────────────────────────────────
  let _exiting = false;

  function startGame() {
    const amt = parseInt(document.getElementById('table-amount-input').value) || 10;
    const gameType = document.getElementById('game-type-select').value;
    const mp = document.getElementById('mode-picker-select');
    const modePicker = mp ? mp.value : 'admin';
    send('start', { table_amount: amt, game_type: gameType, mode_picker: modePicker });
  }
  function doBlind()    { send('blind'); }
  function doSeen()     { send('seen'); }
  function doFold()     { _closeFoldConfirm(); send('fold'); }
  function doShow()     { send('show'); }
  function doSideshow() { send('sideshow'); }

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

  return { createRoom, joinRoom, startGame, doBlind, doSeen, doFold, doShow, doSideshow, restart, sendChat, exitRoom, requestCoins, confirmFold, cancelFold, openNewRound, startNextRound };
})();
}); // end DOMContentLoaded
