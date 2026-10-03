(() => {
  const tg = window.Telegram?.WebApp;
  tg?.ready();
  tg?.expand();
  if (tg?.setHeaderColor) tg.setHeaderColor('#0d1118');
  if (tg?.setBackgroundColor) tg.setBackgroundColor('#0d1118');

  const $ = (id) => document.getElementById(id);
  let initData = tg?.initData || '';
  let snapshot = null;
  let pollTimer = null;
  let busy = false;

  const api = async (path, options = {}) => {
    const headers = {'Content-Type':'application/json','X-Telegram-Init-Data':initData};
    const res = await fetch(path, {...options, headers:{...headers,...(options.headers||{})}});
    let data = {};
    try { data = await res.json(); } catch (_) {}
    if (!res.ok) throw new Error(data.detail || 'Request failed');
    return data;
  };

  const startParam = () => {
    const unsafe = tg?.initDataUnsafe?.start_param;
    if (unsafe) return unsafe;
    return new URLSearchParams(location.search).get('tgWebAppStartParam') || new URLSearchParams(location.search).get('startapp') || '';
  };

  function toast(text) {
    $('toast').textContent = text;
    $('toast').classList.remove('hidden');
    clearTimeout(toast.timer); toast.timer = setTimeout(() => $('toast').classList.add('hidden'), 2600);
  }

  function renderLobby(data) {
    $('game').classList.add('hidden');
    $('lobby').classList.remove('hidden');
    $('statusCard').classList.add('hidden');
    const g = data.game || {};
    const deadline = g.join_deadline ? new Date(g.join_deadline).getTime() : Date.now();
    const seconds = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
    const joined = data.players || [];
    $('lobby').innerHTML = `
      <div class="eyebrow">NEW WORLD</div>
      <div class="lobby-title">${escapeHtml(g.title || 'WHAT HAPPENS?')}</div>
      <div class="countdown" id="lobbyCountdown">${seconds}s</div>
      <div class="hint">The 45-second join window is only for gathering players. The story itself has no round limit and does not end just because a timer expires.</div>
      <div class="players">${joined.map(p => `<div class="player">👤 ${escapeHtml(p.display_name || 'Player')}</div>`).join('') || '<div class="player">No one else has joined yet.</div>'}</div>
      <button class="primary" id="joinNow">JOIN THIS WORLD</button>
      <p class="hint">Late friends can still join while the world is alive. Existing players cannot join twice.</p>`;
    $('joinNow').onclick = async () => {
      try { await api('/api/miniapp/join',{method:'POST',body:JSON.stringify({game_id:g.id})}); await refresh(); }
      catch(e){ toast(e.message); }
    };
    clearInterval(renderLobby.timer);
    renderLobby.timer = setInterval(() => {
      const left = Math.max(0, Math.ceil((deadline-Date.now())/1000));
      const el = $('lobbyCountdown'); if (el) el.textContent = `${left}s`;
      if (left === 0) { clearInterval(renderLobby.timer); refresh(); }
    }, 1000);
  }

  function renderGame(data) {
    snapshot = data;
    $('statusCard').classList.add('hidden');
    $('lobby').classList.add('hidden');
    $('game').classList.remove('hidden');
    const w = data.world || {}, p = data.player?.state || {}, s = data.scene;
    $('dimension').textContent = w.dimension || '—';
    $('location').textContent = w.location || '—';
    $('turn').textContent = w.turn ?? p.turns ?? 0;
    $('health').textContent = p.health ?? 100;
    $('energy').textContent = p.energy ?? 100;
    $('deaths').textContent = p.deaths ?? 0;
    $('cycle').textContent = p.cycle ?? 0;
    $('lifeText').textContent = `${p.lives ?? 3} chances remaining`;
    $('healthBar').style.width = `${Math.max(0,Math.min(100,p.health ?? 100))}%`;
    const rel = Object.entries(p.relationships || {}).filter(([,v])=>v!==0).slice(0,8);
    $('relationships').innerHTML = rel.map(([id,v])=>`<span class="rel">${escapeHtml(id)} · ${v>0?'+':''}${v}</span>`).join('');
    $('discoveries').innerHTML = (w.discoveries || []).map(x=>`<span class="chip">${escapeHtml(x)}</span>`).join('');
    if (!s) return;
    $('sceneMeta').textContent = `${s.dimension} · ${s.location} · ${s.category}`;
    $('sceneTitle').textContent = s.title;
    $('sceneText').textContent = s.text;
    $('npcLine').textContent = s.npc ? `${s.npc.name} · relationship ${s.npc.bond}` : '';
    const choices = $('choices'); choices.innerHTML = '';
    s.choices.forEach(c => {
      const b = document.createElement('button'); b.className='choice'; b.disabled=busy;
      b.innerHTML = `<b>${escapeHtml(c.label)}</b><small>${c.risk>0?'Risk: '+c.risk:'A measured move'}</small>`;
      b.onclick = () => choose(c.id);
      choices.appendChild(b);
    });
  }

  async function choose(choiceId) {
    if (busy || !snapshot) return;
    busy = true;
    [...document.querySelectorAll('.choice')].forEach(b=>b.disabled=true);
    try {
      const result = await api('/api/miniapp/choice',{method:'POST',body:JSON.stringify({game_id:snapshot.game.id,choice_id:choiceId,expected_version:snapshot.game.version})});
      const event = result.event;
      if (event?.died) toast('💀 You died — the world continues and you wake somewhere else.');
      else toast('Choice recorded. The world remembers.');
      renderGame(result.snapshot);
    } catch(e) {
      toast(e.message);
      await refresh();
    } finally { busy=false; }
  }

  async function refresh() {
    try {
      const data = await api('/api/miniapp/bootstrap');
      if (data.game?.game?.status === 'active') renderGame(data.game);
      else if (data.game?.game?.status === 'waiting') renderLobby(data.game);
      else if (startParam().startsWith('invite_') && data.invite) renderGame(data.game);
      else showHome(data);
    } catch(e) {
      $('statusCard').innerHTML = `<div><b>Mini App connection failed.</b><div class="hint">${escapeHtml(e.message)}</div></div>`;
    }
  }

  function showHome(data) {
    $('statusCard').classList.remove('hidden');
    $('statusCard').innerHTML = `<div><b>Welcome${data.user?.first_name ? ', '+escapeHtml(data.user.first_name) : ''}.</b><div class="hint">Open a world from the group or create one here. Every choice is persistent.</div><button class="primary" id="newWorld">CREATE WORLD</button></div>`;
    $('newWorld').onclick = async () => { try { const r=await api('/api/miniapp/worlds',{method:'POST',body:'{}'}); toast('World created.'); location.search='?tgWebAppStartParam=game_'+r.game.game.id; } catch(e){toast(e.message);} };
    $('game').classList.add('hidden'); $('lobby').classList.add('hidden');
  }

  $('inviteBtn').onclick = async () => {
    if (!snapshot?.game?.id) { toast('Join a world first.'); return; }
    try {
      const r = await api('/api/miniapp/invite',{method:'POST',body:JSON.stringify({game_id:snapshot.game.id})});
      const bot = r.bot_username || 'YourOwnVisionBot';
      const link = `https://t.me/${bot}?start=${r.start_param}`;
      if (tg?.openTelegramLink) tg.openTelegramLink(link); else if (navigator.share) await navigator.share({title:'Join my WHAT HAPPENS? world',url:link}); else { await navigator.clipboard.writeText(link); toast('Invite link copied.'); }
    } catch(e){toast(e.message);}
  };

  function escapeHtml(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c]));}

  refresh();
  pollTimer = setInterval(() => { if (!busy) refresh(); }, 6000);
})();
