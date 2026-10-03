(() => {
  const tg = window.Telegram?.WebApp;
  tg?.ready();
  tg?.expand();
  tg?.disableVerticalSwipes?.();
  tg?.setHeaderColor?.('#070a11');
  tg?.setBackgroundColor?.('#070a11');

  const $ = id => document.getElementById(id);
  const initData = tg?.initData || '';
  const qs = new URLSearchParams(location.search);
  let startParam = tg?.initDataUnsafe?.start_param || qs.get('startapp') || qs.get('tgWebAppStartParam') || '';
  let snapshot = null;
  let permissions = {};
  let busy = false;
  let soundOn = true;
  let pollTimer = null;
  let lobbyDeadline = 0;
  let currentTab = 'story';

  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c]));

  async function api(path, options = {}) {
    const headers = {
      'Content-Type': 'application/json',
      'X-Telegram-Init-Data': initData,
      'X-MiniApp-Start-Param': startParam,
      ...(options.headers || {})
    };
    const res = await fetch(path, { ...options, headers, cache: 'no-store' });
    let data = {};
    try { data = await res.json(); } catch (_) {}
    if (!res.ok) throw new Error(data.detail || 'Request failed');
    return data;
  }

  function haptic(type = 'light') { try { tg?.HapticFeedback?.impactOccurred(type); } catch (_) {} }
  function tone(freq = 520, duration = .045) {
    if (!soundOn) return;
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      const ctx = tone.ctx || (tone.ctx = new Ctx());
      const osc = ctx.createOscillator(), gain = ctx.createGain();
      osc.type = 'sine'; osc.frequency.value = freq;
      gain.gain.setValueAtTime(.028, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(.0001, ctx.currentTime + duration);
      osc.connect(gain); gain.connect(ctx.destination);
      osc.start(); osc.stop(ctx.currentTime + duration);
    } catch (_) {}
  }

  function toast(text, kind = '') {
    const node = $('toast');
    node.textContent = text;
    node.className = `toast ${kind}`;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => node.classList.add('hidden'), 2800);
  }

  function showOnly(id) {
    ['statusCard','home','world'].forEach(x => $(x).classList.toggle('hidden', x !== id));
    $('bottomNav').classList.toggle('hidden', id !== 'world');
  }

  function initials(name) { return String(name || 'P').trim().slice(0, 1).toUpperCase(); }

  function rosterHTML(players) {
    if (!players?.length) return '<div class="empty-state">Waiting for the first player…</div>';
    return players.map(p => `
      <div class="roster-row">
        <div class="avatar">${esc(initials(p.display_name))}</div>
        <div class="roster-name"><b>${esc(p.display_name || 'Player')}</b><small>${p.status === 'alive' ? 'In the world' : 'Run ended — may return'}</small></div>
        <span class="live-dot"></span>
      </div>`).join('');
  }

  function renderLobby(data) {
    snapshot = data;
    const g = data.game;
    showOnly('world');
    $('worldTitle').textContent = g.title || 'WHAT HAPPENS?';
    $('dimension').textContent = data.world?.dimension || 'The Unknown';
    $('location').textContent = data.world?.location || 'Waiting…';
    $('worldStatus').textContent = 'JOINING';
    $('worldStatus').className = 'status-pill waiting';
    $('lobbyPanel').classList.remove('hidden');
    $('storyPanel').classList.add('hidden');
    $('pausedPanel').classList.add('hidden');
    $('lobbyPlayers').innerHTML = rosterHTML(data.players || []);
    $('playerCount').textContent = String((data.players || []).length);
    lobbyDeadline = g.join_deadline ? new Date(g.join_deadline).getTime() : Date.now();
    renderPermissions();
    tickLobby();
  }

  function tickLobby() {
    if (!snapshot?.game || snapshot.game.status !== 'waiting') return;
    const left = Math.max(0, Math.ceil((lobbyDeadline - Date.now()) / 1000));
    $('lobbyTimer').textContent = String(left);
    if (left === 0) sync(false);
  }

  function renderPermissions() {
    const g = snapshot?.game || {};
    $('settingsPanel').classList.toggle('hidden', !(permissions.is_admin || permissions.can_terminate || permissions.can_moderate));
    $('settingsNav').classList.toggle('hidden', !(permissions.is_admin || permissions.can_terminate || permissions.can_moderate));
    $('toggleInvites').disabled = !permissions.is_admin;
    $('pauseBtn').disabled = !permissions.can_moderate;
    $('resumeBtn').disabled = !permissions.can_moderate;
    $('terminateWrap').classList.toggle('hidden', !permissions.can_terminate);
    $('operatorWrap').classList.toggle('hidden', !permissions.can_manage_operators);
    $('inviteSetting').textContent = g.settings?.allow_external_invites === false ? 'OFF' : 'ON';
  }

  function renderGame(data) {
    snapshot = data;
    if (data.game?.id) startParam = `w_${data.game.id}`;
    const g = data.game || {};
    const p = data.player?.state || {};
    const s = data.scene;
    showOnly('world');
    $('worldTitle').textContent = g.title || 'WHAT HAPPENS?';
    $('dimension').textContent = data.world?.dimension || 'The Unknown';
    $('location').textContent = data.world?.location || 'Somewhere';
    $('worldStatus').textContent = g.status === 'paused' ? 'PAUSED' : 'LIVE';
    $('worldStatus').className = `status-pill ${g.status === 'paused' ? 'waiting' : 'live'}`;
    $('lobbyPanel').classList.toggle('hidden', g.status !== 'waiting');
    $('pausedPanel').classList.toggle('hidden', g.status !== 'paused');
    $('storyPanel').classList.toggle('hidden', g.status !== 'active');
    $('lifeText').textContent = p.lives ?? 3;
    $('health').textContent = p.health ?? 100;
    $('energy').textContent = p.energy ?? 100;
    $('worldTurn').textContent = data.world?.turn ?? 0;
    $('healthBar').style.width = `${Math.max(0, Math.min(100, Number(p.health ?? 100)))}%`;

    const players = data.players || [];
    $('peopleList').innerHTML = rosterHTML(players);
    $('peopleCount').textContent = String(players.length);
    $('relationships').innerHTML = Object.entries(p.relationships || {}).filter(([,v]) => Number(v) !== 0).slice(0, 8).map(([id,v]) => `<span class="chip">${esc(id)} ${Number(v) > 0 ? '+' : ''}${v}</span>`).join('') || '<span class="muted">Your relationships will appear here.</span>';
    $('discoveries').innerHTML = (data.world?.discoveries || []).map(x => `<span class="chip dim">${esc(x)}</span>`).join('') || '<span class="muted">You have not crossed another dimension yet.</span>';
    renderPermissions();

    if (!s || g.status !== 'active') return;
    $('sceneCategory').textContent = String(s.category || 'story').toUpperCase();
    $('sceneConvergence').classList.toggle('hidden', !s.convergence);
    $('sceneTitle').textContent = s.title || 'The next moment';
    $('sceneText').textContent = s.text || '';
    $('npcLine').textContent = s.npc ? `${s.npc.name}  •  relationship ${s.npc.bond}` : '';
    const choices = $('choices');
    choices.innerHTML = '';
    (s.choices || []).forEach((c, index) => {
      const b = document.createElement('button');
      b.className = 'choice-card';
      b.disabled = busy;
      b.innerHTML = `<span class="choice-index">${index + 1}</span><span class="choice-copy"><b>${esc(c.label)}</b><small>${esc(c.preview)}</small></span><span class="choice-arrow">›</span>`;
      b.onclick = () => choose(c.id);
      choices.appendChild(b);
    });
  }

  async function choose(choiceId) {
    if (busy || !snapshot?.game?.id) return;
    busy = true;
    haptic('medium'); tone(690);
    document.querySelectorAll('.choice-card').forEach(b => { b.disabled = true; b.classList.add('pressed'); });
    try {
      const result = await api('/api/miniapp/choice', { method: 'POST', body: JSON.stringify({ game_id: snapshot.game.id, choice_id: choiceId }) });
      if (result.event?.died) {
        tone(210, .12); haptic('heavy');
        toast(result.event.respawn ? `💀 Your run ended. You wake in ${result.event.respawn.location}. The world continues.` : '💀 Your run ended. The world continues.', 'bad');
      } else if (result.event?.convergence) {
        tone(820, .09); toast('◇ The world is pulling everyone toward a shared meeting point.', 'good');
      }
      renderGame(result.snapshot);
    } catch (e) {
      toast(e.message, 'bad');
      await sync(false);
    } finally {
      busy = false;
    }
  }

  async function sync(showLoading = true) {
    if (showLoading && !snapshot) showOnly('statusCard');
    try {
      const data = await api('/api/miniapp/bootstrap');
      permissions = {
        is_admin: !!data.is_admin,
        can_moderate: !!data.can_moderate,
        can_terminate: !!data.can_terminate,
        can_manage_operators: !!data.can_manage_operators,
      };
      if (data.maintenance?.active) {
        showOnly('home');
        $('homeTitle').textContent = 'Maintenance in progress';
        $('home').querySelector('p').textContent = `The worlds are safe. Updates are being applied until ${new Date(data.maintenance.until).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'})}.`;
        return;
      }
      if (!data.game) return renderHome(data);
      const g = data.game.game;
      if (g.status === 'waiting') renderLobby(data.game);
      else renderGame(data.game);
    } catch (e) {
      showOnly('statusCard');
      $('statusCard').innerHTML = `<div><b>Could not enter the world.</b><span>${esc(e.message)}</span></div>`;
    }
  }

  function renderHome(data) {
    showOnly('home');
    const name = data.user?.first_name ? `, ${data.user.first_name}` : '';
    $('homeTitle').textContent = `Open a world${name}.`;
  }

  async function invite() {
    if (!snapshot?.game?.id || !snapshot.player) return toast('Join the world first.', 'bad');
    try {
      const r = await api('/api/miniapp/invite', { method:'POST', body: JSON.stringify({ game_id: snapshot.game.id }) });
      if (navigator.share) await navigator.share({ title: 'Join my WHAT HAPPENS? world', url: r.link });
      else if (navigator.clipboard) { await navigator.clipboard.writeText(r.link); toast('Invite link copied.', 'good'); }
      else tg?.openTelegramLink?.(r.link);
    } catch (e) { toast(e.message, 'bad'); }
  }

  async function toggleInvites() {
    if (!snapshot?.game?.id || !permissions.is_admin) return;
    try {
      const r = await api('/api/miniapp/settings/invites', { method:'POST', body: JSON.stringify({ game_id: snapshot.game.id }) });
      snapshot.game.settings = r.settings; renderPermissions(); toast(r.settings.allow_external_invites ? 'Outside invites enabled.' : 'Outside invites disabled.', 'good');
    } catch (e) { toast(e.message, 'bad'); }
  }

  async function moderation(action) {
    if (!snapshot?.game?.id || !permissions.can_moderate) return;
    try {
      await api(`/api/miniapp/moderation/${action}`, { method:'POST', body: JSON.stringify({ game_id: snapshot.game.id }) });
      await sync(false); toast(action === 'pause' ? 'World paused.' : 'World resumed.', 'good');
    } catch (e) { toast(e.message, 'bad'); }
  }

  async function terminate() {
    if (!snapshot?.game?.id || !permissions.can_terminate) return;
    if (!confirm('Archive this world? Current data remains stored, but this world will no longer accept play.')) return;
    try {
      await api('/api/miniapp/moderation/terminate', { method:'POST', body: JSON.stringify({ game_id: snapshot.game.id }) });
      toast('World archived.', 'good');
      await sync(false);
    } catch (e) { toast(e.message, 'bad'); }
  }

  async function operator(action) {
    const id = Number($('operatorId').value.trim());
    if (!id || !snapshot?.game?.id || !permissions.can_manage_operators) return toast('Enter a valid Telegram user ID.', 'bad');
    try {
      await api('/api/miniapp/settings/operators', { method:'POST', body: JSON.stringify({ game_id: snapshot.game.id, user_id: id, action }) });
      toast(action === 'add' ? 'Operator assigned.' : 'Operator removed.', 'good');
      $('operatorId').value = '';
    } catch (e) { toast(e.message, 'bad'); }
  }

  $('soundBtn').onclick = () => { soundOn = !soundOn; $('soundBtn').textContent = soundOn ? '◉' : '○'; if (soundOn) tone(); };
  $('inviteBtn').onclick = invite;
  $('toggleInvites').onclick = toggleInvites;
  $('pauseBtn').onclick = () => moderation('pause');
  $('resumeBtn').onclick = () => moderation('resume');
  $('terminateBtn').onclick = terminate;
  $('operatorAdd').onclick = () => operator('add');
  $('operatorRemove').onclick = () => operator('remove');

  document.querySelectorAll('.bottom-nav button').forEach(btn => {
    btn.onclick = () => {
      document.querySelectorAll('.bottom-nav button').forEach(x => x.classList.remove('active'));
      btn.classList.add('active');
      currentTab = btn.dataset.tab;
      $('storyPanel').classList.toggle('hidden', currentTab !== 'story' || snapshot?.game?.status !== 'active');
      $('peoplePanel').classList.toggle('hidden', currentTab !== 'people');
      $('settingsPanel').classList.toggle('hidden', currentTab !== 'settings' || !(permissions.is_admin || permissions.can_terminate || permissions.can_moderate));
      if (currentTab === 'story') window.scrollTo({ top: 0, behavior: 'smooth' });
    };
  });

  function startPolling() {
    clearInterval(pollTimer);
    pollTimer = setInterval(() => {
      if (document.hidden || busy) return;
      if (snapshot?.game?.status === 'waiting' || snapshot?.game?.status === 'active' || snapshot?.game?.status === 'paused') sync(false);
    }, 4500);
  }

  setInterval(tickLobby, 1000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) sync(false); });
  startPolling();
  sync(true);
})();
