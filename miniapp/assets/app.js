(() => {
  const tg = window.Telegram?.WebApp;
  tg?.ready();
  tg?.expand();
  tg?.disableVerticalSwipes?.();
  tg?.setHeaderColor?.('#080b12');
  tg?.setBackgroundColor?.('#080b12');

  const $ = (id) => document.getElementById(id);
  const qs = new URLSearchParams(location.search);
  const initData = tg?.initData || '';
  let startParam = tg?.initDataUnsafe?.start_param || qs.get('startapp') || qs.get('tgWebAppStartParam') || '';
  let snapshot = null;
  let isAdmin = false;
  let busy = false;
  let lobbyTimer = null;
  let syncTimer = null;
  let soundOn = true;

  const api = async (path, options = {}) => {
    const headers = {
      'Content-Type': 'application/json',
      'X-Telegram-Init-Data': initData,
      'X-MiniApp-Start-Param': startParam,
      ...(options.headers || {})
    };
    const response = await fetch(path, { ...options, headers, cache: 'no-store' });
    let data = {};
    try { data = await response.json(); } catch (_) {}
    if (!response.ok) throw new Error(data.detail || 'Request failed');
    return data;
  };

  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c]));

  function tone(freq = 540, duration = 0.045) {
    if (!soundOn || !window.AudioContext && !window.webkitAudioContext) return;
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      const ctx = tone.ctx || (tone.ctx = new Ctx());
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.frequency.value = freq;
      osc.type = 'sine';
      gain.gain.setValueAtTime(0.035, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + duration);
      osc.connect(gain); gain.connect(ctx.destination);
      osc.start(); osc.stop(ctx.currentTime + duration);
    } catch (_) {}
  }

  function haptic(type = 'light') {
    try { tg?.HapticFeedback?.impactOccurred(type); } catch (_) {}
  }

  function toast(text, kind = '') {
    $('toast').textContent = text;
    $('toast').className = `toast ${kind}`;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => $('toast').classList.add('hidden'), 2600);
  }

  function showOnly(id) {
    ['statusCard','home','lobby','game'].forEach(x => $(x).classList.toggle('hidden', x !== id));
    $('bottomNav').classList.toggle('hidden', id !== 'game');
  }

  function renderRoster(players) {
    const html = players.length ? players.map((p, i) => `
      <div class="roster-row">
        <div class="avatar">${esc((p.display_name || 'P').slice(0,1).toUpperCase())}</div>
        <div class="roster-name"><b>${esc(p.display_name || 'Player')}</b><small>${p.status === 'alive' ? (i === 0 ? 'Joined' : 'In the world') : 'Returning soon'}</small></div>
        <span class="online-dot"></span>
      </div>`).join('') : `<div class="empty-state">Waiting for the first player…</div>`;
    $('lobbyPlayers').innerHTML = html;
    $('playerCount').textContent = players.length;
  }

  function renderLobby(data) {
    snapshot = data;
    const g = data.game || {};
    const me = data.player;
    showOnly('lobby');
    $('lobbyTitle').textContent = g.title || 'WHAT HAPPENS?';
    renderRoster(data.players || []);
    const joined = !!me;
    $('joinBtn').textContent = joined ? '✓ YOU ARE IN — OPEN STORY' : 'JOIN WORLD';
    $('joinBtn').classList.toggle('joined', joined);
    $('joinBtn').disabled = busy;
    $('lobbyStatus').textContent = g.status === 'active' ? 'WORLD LIVE' : 'JOINING';
    $('lobbyStatus').className = `status-pill ${g.status === 'active' ? 'live' : 'waiting'}`;
    $('lobbyHint').textContent = joined
      ? 'You are already in this world. Your next action continues your own path.'
      : 'One click joins you. Clicking again never creates a duplicate player.';
    const deadline = g.join_deadline ? new Date(g.join_deadline).getTime() : Date.now();
    clearInterval(lobbyTimer);
    const tick = () => {
      const left = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
      $('lobbyTimer').textContent = left;
      if (left === 0 && g.status === 'waiting') {
        clearInterval(lobbyTimer);
        sync();
      }
    };
    tick();
    if (g.status === 'waiting') lobbyTimer = setInterval(tick, 1000);
  }

  async function joinWorld() {
    if (!snapshot?.game?.id || busy) return;
    if (snapshot.player) { renderGame(snapshot); return; }
    busy = true; tone(620); haptic('light'); $('joinBtn').disabled = true;
    try {
      const r = await api('/api/miniapp/join', { method:'POST', body: JSON.stringify({ game_id: snapshot.game.id }) });
      snapshot = r.snapshot;
      toast('You joined the world.', 'good');
      renderGame(snapshot);
    } catch (e) {
      toast(e.message, 'bad');
      await sync();
    } finally { busy = false; }
  }

  function renderGame(data) {
    snapshot = data;
    if (data.game?.id) startParam = `game_${data.game.id}`;
    const g = data.game || {};
    const p = data.player?.state || {};
    const s = data.scene;
    showOnly('game');
    $('worldTitle').textContent = g.title || 'WHAT HAPPENS?';
    $('dimension').textContent = data.world?.dimension || 'The Unknown';
    $('location').textContent = data.world?.location || 'Somewhere';
    $('lifeText').textContent = p.lives ?? 3;
    $('health').textContent = p.health ?? 100;
    $('energy').textContent = p.energy ?? 100;
    $('cycle').textContent = p.cycle ?? 0;
    $('healthBar').style.width = `${Math.max(0, Math.min(100, Number(p.health ?? 100)))}%`;
    $('relationships').innerHTML = Object.entries(p.relationships || {}).filter(([,v]) => Number(v) !== 0).slice(0,6).map(([id,v]) => `<span class="chip">${esc(id)} ${Number(v)>0?'+':''}${v}</span>`).join('');
    $('discoveries').innerHTML = (data.world?.discoveries || []).map(x => `<span class="chip dim">${esc(x)}</span>`).join('');
    const roster = data.players || [];
    $('gamePlayers').innerHTML = roster.length ? roster.map(p => `<div class="roster-row"><div class="avatar">${esc((p.display_name || 'P').slice(0,1).toUpperCase())}</div><div class="roster-name"><b>${esc(p.display_name || 'Player')}</b><small>${p.status === 'alive' ? 'In the world' : 'Returning soon'}</small></div><span class="online-dot"></span></div>`).join('') : `<div class="empty-state">No players yet.</div>`;
    $('settingsPanel').classList.toggle('hidden', !isAdmin);
    $('settingsNav').classList.toggle('hidden', !isAdmin);
    $('inviteSetting').textContent = (g.settings?.allow_external_invites ?? true) ? 'ON' : 'OFF';

    if (!s) {
      $('choices').innerHTML = `<div class="panel empty-state">Your character is between paths. Reopen the world to continue.</div>`;
      return;
    }
    $('sceneCategory').textContent = (s.category || 'story').toUpperCase();
    $('sceneConvergence').classList.toggle('hidden', !s.convergence);
    $('sceneTitle').textContent = s.title;
    $('sceneText').textContent = s.text;
    $('npcLine').textContent = s.npc ? `${s.npc.name}  •  bond ${s.npc.bond}` : '';

    const choices = $('choices');
    choices.innerHTML = '';
    (s.choices || []).forEach((c, index) => {
      const b = document.createElement('button');
      b.className = 'choice-card';
      b.disabled = busy;
      b.innerHTML = `<span class="choice-index">${index + 1}</span><span class="choice-copy"><b>${esc(c.label)}</b><small>${esc(c.preview || 'The world will remember this.')}</small></span><span class="choice-arrow">›</span>`;
      b.onclick = () => choose(c.id);
      choices.appendChild(b);
    });
  }

  async function choose(choiceId) {
    if (busy || !snapshot?.game?.id) return;
    busy = true; tone(720); haptic('medium');
    document.querySelectorAll('.choice-card').forEach(b => b.disabled = true);
    document.querySelectorAll('.choice-card').forEach(b => b.classList.add('pressed'));
    try {
      const r = await api('/api/miniapp/choice', { method:'POST', body: JSON.stringify({ game_id: snapshot.game.id, choice_id: choiceId, expected_version: snapshot.game.version }) });
      if (r.event?.died) {
        tone(220, .12); haptic('heavy');
        toast(r.event.respawn ? `💀 You fell. You wake in ${r.event.respawn.location}.` : '💀 You fell. The world continues.', 'bad');
      } else if (r.event?.convergence) {
        toast('◇ Everyone is being pulled toward a shared moment.', 'good');
      } else {
        toast('The world changed.', 'good');
      }
      renderGame(r.snapshot);
    } catch (e) {
      toast(e.message, 'bad');
      await sync();
    } finally { busy = false; }
  }

  async function sync() {
    try {
      const data = await api('/api/miniapp/bootstrap');
      isAdmin = !!data.is_admin;
      if (!data.game) return renderHome(data);
      if (data.invite === 'joined' && data.game.game?.id) startParam = `game_${data.game.game.id}`;
      const g = data.game.game;
      if (g.status === 'waiting' || !data.game.player) renderLobby(data.game);
      else if (g.status === 'active') renderGame(data.game);
      else renderHome(data);
    } catch (e) {
      showOnly('statusCard');
      $('statusCard').innerHTML = `<div><strong>Could not enter the world.</strong><div class="muted">${esc(e.message)}</div></div>`;
    }
  }

  function renderHome(data) {
    showOnly('home');
    const first = data.user?.first_name ? ` ${esc(data.user.first_name)}` : '';
    $('home').querySelector('h2').textContent = `Your choices change the world${first ? ',' + first : ''}.`;
  }

  $('createWorld').onclick = async () => {
    if (busy) return;
    busy = true; tone(580); haptic('medium'); $('createWorld').disabled = true;
    try {
      const r = await api('/api/miniapp/worlds', { method:'POST', body:'{}' });
      startParam = `game_${r.game.game.id}`;
      snapshot = r.game;
      renderLobby(r.game);
      toast('World created. The lobby is open.', 'good');
    } catch (e) { toast(e.message, 'bad'); }
    finally { busy = false; $('createWorld').disabled = false; }
  };

  $('joinBtn').onclick = joinWorld;

  $('inviteBtn').onclick = async () => {
    if (!snapshot?.game?.id || !snapshot.player) { toast('Join the world first.', 'bad'); return; }
    try {
      const r = await api('/api/miniapp/invite', { method:'POST', body: JSON.stringify({ game_id: snapshot.game.id }) });
      if (tg?.openTelegramLink) tg.openTelegramLink(r.link);
      else if (navigator.share) await navigator.share({ title:'Join my WHAT HAPPENS? world', url:r.link });
      else { await navigator.clipboard.writeText(r.link); toast('Invite link copied.', 'good'); }
    } catch (e) { toast(e.message, 'bad'); }
  };

  $('soundBtn').onclick = () => {
    soundOn = !soundOn; $('soundBtn').textContent = soundOn ? '🔊' : '🔇'; if (soundOn) tone();
  };

  $('toggleInvites').onclick = async () => {
    if (!snapshot?.game?.id || !isAdmin) return;
    try {
      const r = await api('/api/miniapp/settings/invites', { method:'POST', body: JSON.stringify({ game_id: snapshot.game.id }) });
      snapshot.game.settings = r.settings;
      $('inviteSetting').textContent = r.settings.allow_external_invites ? 'ON' : 'OFF';
      toast(r.settings.allow_external_invites ? 'External invites enabled.' : 'External invites disabled.', 'good');
    } catch (e) { toast(e.message, 'bad'); }
  };

  document.querySelectorAll('.bottom-nav button').forEach(btn => btn.onclick = () => {
    document.querySelectorAll('.bottom-nav button').forEach(x => x.classList.remove('active'));
    btn.classList.add('active');
    const tab = btn.dataset.tab;
    if (tab === 'settings' && isAdmin) $('settingsPanel').scrollIntoView({ behavior:'smooth', block:'center' });
    if (tab === 'people') $('gamePlayers').scrollIntoView({ behavior:'smooth', block:'center' });
    if (tab === 'story') window.scrollTo({ top: 0, behavior: 'smooth' });
  });

  // Fast, light sync: lobby needs roster updates; live story only refreshes after a choice.
  syncTimer = setInterval(() => { if (!busy && snapshot?.game?.status === 'waiting') sync(); }, 2200);
  sync();
})();
