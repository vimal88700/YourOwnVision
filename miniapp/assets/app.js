(() => {
  const tg = window.Telegram?.WebApp;
  tg?.ready();
  tg?.expand();
  tg?.disableVerticalSwipes?.();
  tg?.setHeaderColor?.('#08090d');
  tg?.setBackgroundColor?.('#08090d');

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

  const esc = value => String(value ?? '').replace(/[&<>\"']/g, c => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'
  }[c]));

  async function api(path, options = {}) {
    const headers = {
      'Content-Type': 'application/json',
      'X-Telegram-Init-Data': initData,
      'X-MiniApp-Start-Param': startParam,
      ...(options.headers || {})
    };
    const response = await fetch(path, {
      ...options,
      headers,
      cache: 'no-store',
      credentials: 'same-origin'
    });

    let data = {};
    try { data = await response.json(); } catch (_) {}

    if (!response.ok) {
      throw new Error(data.detail || `Request failed (${response.status})`);
    }
    return data;
  }

  function haptic(type = 'light') {
    try { tg?.HapticFeedback?.impactOccurred(type); } catch (_) {}
  }

  function tone(freq = 520, duration = .05) {
    if (!soundOn) return;
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      const ctx = tone.ctx || (tone.ctx = new Ctx());
      if (ctx.state === 'suspended') ctx.resume();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = 'sine';
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(.025, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(.0001, ctx.currentTime + duration);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start();
      osc.stop(ctx.currentTime + duration);
    } catch (_) {}
  }

  function toast(text, kind = '') {
    const node = $('toast');
    node.textContent = text;
    node.className = `toast ${kind}`;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => node.classList.add('hidden'), 3000);
  }

  function showOnly(id) {
    ['statusCard', 'home', 'world'].forEach(x => $(x).classList.toggle('hidden', x !== id));
    $('bottomNav').classList.toggle('hidden', id !== 'world');
  }

  function initials(name) {
    return String(name || 'P').trim().slice(0, 1).toUpperCase();
  }

  function rosterHTML(players) {
    if (!players?.length) return '<div class="muted">No players have entered this world yet.</div>';
    return players.map(p => `
      <div class="player">
        <div class="avatar">${esc(initials(p.display_name))}</div>
        <div class="pname">
          <b>${esc(p.display_name || 'Player')}</b>
          <small>${p.status === 'alive' ? 'ACTIVE IN WORLD' : 'RUN ENDED — CAN RETURN'}</small>
        </div>
        <span class="dot"></span>
      </div>
    `).join('');
  }

  function renderPermissions() {
    const g = snapshot?.game || {};
    const canControl = permissions.is_admin || permissions.can_terminate || permissions.can_moderate;
    $('settingsPanel').classList.toggle('hidden', !canControl || currentTab !== 'settings');
    $('settingsNav').classList.toggle('hidden', !canControl);
    $('toggleInvites').disabled = !permissions.is_admin;
    $('pauseBtn').disabled = !permissions.can_moderate;
    $('resumeBtn').disabled = !permissions.can_moderate;
    $('terminateWrap').classList.toggle('hidden', !permissions.can_terminate);
    $('operatorWrap').classList.toggle('hidden', !permissions.can_manage_operators);
    $('inviteSetting').textContent = g.settings?.allow_external_invites === false ? 'OFF' : 'ON';
  }

  function renderLobby(data) {
    snapshot = data;
    const g = data.game || {};
    showOnly('world');

    $('worldTitle').textContent = g.title || 'WHAT HAPPENS?';
    $('dimension').textContent = data.world?.dimension || 'Unknown';
    $('location').textContent = data.world?.location || 'Waiting';
    $('worldStatus').textContent = 'LOBBY';
    $('worldStatus').className = 'status waiting';

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

  function renderGame(data) {
    snapshot = data;
    const g = data.game || {};
    const p = data.player?.state || {};
    const s = data.scene;

    if (g.id) startParam = `w_${g.id}`;

    showOnly('world');
    $('worldTitle').textContent = g.title || 'WHAT HAPPENS?';
    $('dimension').textContent = data.world?.dimension || 'Unknown';
    $('location').textContent = data.world?.location || 'Somewhere';

    const status = g.status || 'active';
    $('worldStatus').textContent = status === 'paused' ? 'PAUSED' : status === 'archived' ? 'ARCHIVED' : 'LIVE';
    $('worldStatus').className = `status ${status === 'waiting' ? 'waiting' : 'live'}`;

    $('lobbyPanel').classList.toggle('hidden', status !== 'waiting');
    $('pausedPanel').classList.toggle('hidden', status !== 'paused');
    $('storyPanel').classList.toggle('hidden', status !== 'active');

    $('lifeText').textContent = p.lives ?? 3;
    $('health').textContent = p.health ?? 100;
    $('energy').textContent = p.energy ?? 100;
    $('worldTurn').textContent = data.world?.turn ?? 0;
    $('healthBar').style.width = `${Math.max(0, Math.min(100, Number(p.health ?? 100)))}%`;

    const players = data.players || [];
    $('peopleList').innerHTML = rosterHTML(players);
    $('peopleCount').textContent = String(players.length);

    $('relationships').innerHTML =
      Object.entries(p.relationships || {})
        .filter(([, value]) => Number(value) !== 0)
        .slice(0, 8)
        .map(([id, value]) => `<span class="chip">${esc(id)} ${Number(value) > 0 ? '+' : ''}${value}</span>`)
        .join('') || '<span class="muted">Relationships will appear here.</span>';

    $('discoveries').innerHTML =
      (data.world?.discoveries || [])
        .map(x => `<span class="chip dim">${esc(x)}</span>`)
        .join('') || '<span class="muted">No other dimensions discovered yet.</span>';

    renderPermissions();

    if (!s || status !== 'active') return;

    $('sceneCategory').textContent = String(s.category || 'story').toUpperCase();
    $('sceneConvergence').classList.toggle('hidden', !s.convergence);
    $('sceneTitle').textContent = s.title || 'The next moment';
    $('sceneText').textContent = s.text || '';
    $('npcLine').textContent = s.npc
      ? `${s.npc.name}  •  relationship ${s.npc.bond}`
      : '';

    const choices = $('choices');
    choices.innerHTML = '';

    (s.choices || []).forEach((choice, index) => {
      const button = document.createElement('button');
      button.className = 'choice';
      button.disabled = busy;
      button.innerHTML = `
        <span class="idx">${index + 1}</span>
        <span class="copy">
          <b>${esc(choice.label)}</b>
          <small>${esc(choice.preview)}</small>
        </span>
        <span class="arrow">›</span>
      `;
      button.onclick = () => choose(choice.id);
      choices.appendChild(button);
    });
  }

  async function choose(choiceId) {
    if (busy || !snapshot?.game?.id) return;

    busy = true;
    haptic('medium');
    tone(690);
    document.querySelectorAll('.choice').forEach(button => {
      button.disabled = true;
      button.classList.add('pressed');
    });

    try {
      const result = await api('/api/miniapp/choice', {
        method: 'POST',
        body: JSON.stringify({
          game_id: snapshot.game.id,
          choice_id: choiceId
        })
      });

      if (result.event?.died) {
        haptic('heavy');
        tone(190, .12);
        toast(
          result.event.respawn
            ? `Your run ended. You wake in ${result.event.respawn.location}. The world continues.`
            : 'Your run ended. The world continues.',
          'bad'
        );
      } else if (result.event?.convergence) {
        tone(830, .09);
        toast('The world is converging. Other paths are meeting yours.', 'good');
      }

      renderGame(result.snapshot);
    } catch (error) {
      toast(error.message, 'bad');
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
        can_manage_operators: !!data.can_manage_operators
      };

      if (data.maintenance?.active) {
        showOnly('home');
        $('homeTitle').textContent = 'Maintenance in progress.';
        const note = $('home').querySelector('.hero-note');
        note.innerHTML =
          `World data is safe. Updates are being applied until ` +
          `<b>${new Date(data.maintenance.until).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'})}</b>.`;
        return;
      }

      if (!data.game) {
        renderHome(data);
        return;
      }

      const game = data.game.game;
      if (game.status === 'waiting') renderLobby(data.game);
      else renderGame(data.game);
    } catch (error) {
      showOnly('statusCard');
      $('statusCard').innerHTML =
        `<div><b>WORLD CONNECTION FAILED</b><span>${esc(error.message)}</span></div>`;
    }
  }

  function renderHome(data) {
    showOnly('home');
    const name = data.user?.first_name ? `, ${data.user.first_name}` : '';
    $('homeTitle').textContent = `The world is waiting${name}.`;
  }

  async function invite() {
    if (!snapshot?.game?.id || !snapshot.player) {
      toast('Join the world first.', 'bad');
      return;
    }

    try {
      const result = await api('/api/miniapp/invite', {
        method: 'POST',
        body: JSON.stringify({ game_id: snapshot.game.id })
      });

      if (navigator.share) {
        await navigator.share({
          title: 'Join my WHAT HAPPENS? world',
          text: 'Enter this persistent multiplayer world.',
          url: result.link
        });
      } else if (navigator.clipboard) {
        await navigator.clipboard.writeText(result.link);
        toast('Invite link copied.', 'good');
      } else {
        tg?.openTelegramLink?.(result.link);
      }
    } catch (error) {
      toast(error.message, 'bad');
    }
  }

  async function toggleInvites() {
    if (!snapshot?.game?.id || !permissions.is_admin) return;

    try {
      const result = await api('/api/miniapp/settings/invites', {
        method: 'POST',
        body: JSON.stringify({ game_id: snapshot.game.id })
      });
      snapshot.game.settings = result.settings;
      renderPermissions();
      toast(
        result.settings.allow_external_invites
          ? 'Outside invites enabled.'
          : 'Outside invites disabled.',
        'good'
      );
    } catch (error) {
      toast(error.message, 'bad');
    }
  }

  async function moderation(action) {
    if (!snapshot?.game?.id || !permissions.can_moderate) return;

    try {
      await api(`/api/miniapp/moderation/${action}`, {
        method: 'POST',
        body: JSON.stringify({ game_id: snapshot.game.id })
      });
      await sync(false);
      toast(action === 'pause' ? 'World paused.' : 'World resumed.', 'good');
    } catch (error) {
      toast(error.message, 'bad');
    }
  }

  async function terminate() {
    if (!snapshot?.game?.id || !permissions.can_terminate) return;
    if (!confirm('Terminate this world? Its saved history remains, but players cannot continue it.')) return;

    try {
      await api('/api/miniapp/moderation/terminate', {
        method: 'POST',
        body: JSON.stringify({ game_id: snapshot.game.id })
      });
      toast('World archived. Saved data remains.', 'good');
      await sync(false);
    } catch (error) {
      toast(error.message, 'bad');
    }
  }

  async function operator(action) {
    const id = Number($('operatorId').value.trim());
    if (!id || !snapshot?.game?.id || !permissions.can_manage_operators) {
      toast('Enter a valid Telegram user ID.', 'bad');
      return;
    }

    try {
      await api('/api/miniapp/settings/operators', {
        method: 'POST',
        body: JSON.stringify({
          game_id: snapshot.game.id,
          user_id: id,
          action
        })
      });
      $('operatorId').value = '';
      toast(action === 'add' ? 'Operator assigned.' : 'Operator removed.', 'good');
      await sync(false);
    } catch (error) {
      toast(error.message, 'bad');
    }
  }

  let currentTab = 'story';

  function setTab(tab) {
    currentTab = tab;
    document.querySelectorAll('.bottom button').forEach(button => {
      button.classList.toggle('active', button.dataset.tab === tab);
    });

    const activeGame = snapshot?.game?.status === 'active';
    $('storyPanel').classList.toggle('hidden', tab !== 'story' || !activeGame);
    $('peoplePanel').classList.toggle('hidden', tab !== 'people');
    $('settingsPanel').classList.toggle(
      'hidden',
      tab !== 'settings' ||
      !(permissions.is_admin || permissions.can_terminate || permissions.can_moderate)
    );
  }

  $('soundBtn').onclick = () => {
    soundOn = !soundOn;
    $('soundBtn').textContent = soundOn ? '◉' : '○';
    if (soundOn) tone();
  };

  $('inviteBtn').onclick = invite;
  $('toggleInvites').onclick = toggleInvites;
  $('pauseBtn').onclick = () => moderation('pause');
  $('resumeBtn').onclick = () => moderation('resume');
  $('terminateBtn').onclick = terminate;
  $('operatorAdd').onclick = () => operator('add');
  $('operatorRemove').onclick = () => operator('remove');

  document.querySelectorAll('.bottom button').forEach(button => {
    button.onclick = () => {
      setTab(button.dataset.tab);
      if (button.dataset.tab === 'story') window.scrollTo({top: 0, behavior: 'smooth'});
    };
  });

  setInterval(tickLobby, 1000);

  function startPolling() {
    clearInterval(pollTimer);
    pollTimer = setInterval(() => {
      if (document.hidden || busy) return;
      if (snapshot?.game?.status === 'waiting' ||
          snapshot?.game?.status === 'active' ||
          snapshot?.game?.status === 'paused') {
        sync(false);
      }
    }, 3000);
  }

  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) sync(false);
  });

  startPolling();
  sync(true);
})();
