/* 合成大猪猪 · 管理台 */
const $ = (id) => document.getElementById(id);
const bridge = window.AstrBotPluginView;

const PIG_EMOJI = ['', '🐽', '🐷', '🐖', '🐗', '🥓', '🍖', '🍔', '🍲', '👑'];

const state = {
  query: '',
  offset: 0,
  total: 0,
  pageSize: 100,
  service: null,
  loaded: false,
};

function fmtInt(value) {
  const n = Math.round(Number(value) || 0);
  return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
}

function fmtTime(ts) {
  const n = Number(ts) || 0;
  if (!n) return '—';
  const d = new Date(n * 1000);
  const pad = (x) => String(x).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function shortUmo(umo) {
  const text = String(umo || '');
  if (!text) return '';
  return text.length > 30 ? text.slice(0, 27) + '…' : text;
}

/**
 * UMO = 「平台:消息类型:会话ID」。
 * QQ 官方机器人给的是 openid/哈希，本来就不是 QQ 号，看着像乱码是正常的；
 * 这个函数只是把它拆开给管理员看，别拿它当 QQ 号用。
 */
function umoParts(umo) {
  const text = String(umo || '');
  if (!text) return null;
  const bits = text.split(':');
  const platform = bits[0] || '?';
  const kind = bits.length >= 3 ? bits[1] : '';
  const sid = bits.length >= 3 ? bits.slice(2).join(':') : bits.slice(1).join(':');
  return { platform, kind, sid };
}

function umoPlatform(umo) {
  const info = umoParts(umo);
  return info ? info.platform : '';
}

function sessionLabel(row) {
  const umo = String(row.session || '');
  if (!umo) return '单机';
  const parts = umo.split(':');
  if (parts.length >= 3) {
    const kind = parts[1];
    const id = parts[2];
    if (kind === 'group') return '群 ' + id;
    if (kind === 'friend') return '私聊 ' + id.slice(0, 8);
    return kind + ' ' + id.slice(0, 8);
  }
  return shortUmo(umo);
}

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

let toastTimer = 0;
function toast(message, isError) {
  const box = $('toast');
  box.textContent = message;
  box.classList.remove('hidden');
  box.classList.toggle('error', Boolean(isError));
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => box.classList.add('hidden'), 2600);
}

/* ================= 数据 ================= */

async function loadOverview() {
  const data = await bridge.apiGet('admin/overview');
  state.service = data.service || null;
  renderService(data.service);
  renderStats(data.stats);
  renderRuns(data.recent || []);
  fillConfig(data.config || {});
  $('footVersion').textContent = `${data.plugin || '合成大猪猪'} v${data.version || '—'}`;
  $('svcDb').textContent = data.db_path || '—';
  state.loaded = true;
}

async function loadPlayers(reset) {
  if (reset) state.offset = 0;
  const data = await bridge.apiGet('admin/players', {
    q: state.query,
    limit: state.pageSize,
    offset: state.offset,
  });
  state.total = Number(data.total) || 0;
  const rows = data.rows || [];
  renderPlayers(rows, reset);
  state.offset += rows.length;
  $('playerCount').textContent = state.total
    ? `共 ${fmtInt(state.total)} 位猪农 · 已显示 ${fmtInt(state.offset)} 位`
    : '还没有猪农';
  $('morePlayers').hidden = state.offset >= state.total;
}

/* ================= 渲染 ================= */

function renderService(service) {
  const pill = $('servicePill');
  const running = Boolean(service && service.running);
  pill.textContent = running ? '游戏页运行中' : '游戏页未启动';
  pill.classList.toggle('ok', running);
  pill.classList.toggle('bad', !running);

  $('svcState').textContent = running ? '运行中' : '未启动';
  $('svcState').className = running ? 'good' : 'bad-text';
  $('svcPort').textContent = service && service.port ? `${service.port}` : '—';
  $('svcUrl').textContent = (service && service.url) || '—';
  $('statPortRange').textContent =
    service && service.port_range
      ? `${service.port_range[0]}–${service.port_range[1]}`
      : '6100–6110';
  $('footPort').textContent =
    service && service.port ? `实际端口 ${service.port}` : '端口 —';

  const note = $('svcNote');
  if (!running && service && service.error) {
    note.textContent = '启动失败：' + service.error;
    note.className = 'service-note bad-text';
  } else if (service && service.port && service.port !== service.configured_port) {
    note.textContent = `配置端口 ${service.configured_port} 被占用，已自动切到 ${service.port}。`;
    note.className = 'service-note warn-text';
  } else {
    note.textContent = '访问地址留空时会自动用本机内网 IP；公网访问记得在防火墙放行端口。';
    note.className = 'service-note';
  }
  if ($('subtitle') && !state.loaded) {
    $('subtitle').textContent = '管理猪农档案、对局记录和游戏页设置';
  }
}

function renderStats(stats) {
  stats = stats || {};
  $('statPlayers').textContent = fmtInt(stats.players || 0);
  $('statBound').textContent = fmtInt(stats.bound || 0);
  $('statRuns').textContent = fmtInt(stats.runs || 0);
  $('statToday').textContent = fmtInt(stats.today_runs || 0);
  $('statTop').textContent = fmtInt(stats.top_score || 0);
}

function renderPlayers(rows, reset) {
  const tbody = $('playerRows');
  if (reset) tbody.replaceChildren();
  if (!rows.length && !tbody.childElementCount) {
    const tr = document.createElement('tr');
    const td = el('td', 'empty', state.query ? '没搜到这个名字。' : '还没有猪农，去群里发一条「🐖游戏」吧。');
    td.colSpan = 6;
    tr.appendChild(td);
    tbody.appendChild(tr);
    return;
  }
  if (reset) tbody.replaceChildren();

  for (const p of rows) {
    const tr = document.createElement('tr');

    const who = el('td', 'who-cell');
    const nameLine = el('div', 'who-name');
    nameLine.appendChild(document.createTextNode(p.name || '无名猪农'));
    if (p.chat_umo) nameLine.appendChild(el('span', 'badge', '已绑定'));
    who.appendChild(nameLine);
    if (p.chat_name && p.chat_name !== p.name) {
      who.appendChild(el('small', 'who-sub', '聊天名 ' + p.chat_name));
    }
    tr.appendChild(who);

    tr.appendChild(el('td', 'num strong', fmtInt(p.best)));
    tr.appendChild(el('td', 'num', fmtInt(p.plays)));

    const bind = el('td', 'bind-cell');
    if (p.chat_umo) {
      const info = umoParts(p.chat_umo);
      bind.appendChild(el('div', '', p.group_name || p.chat_name || '已绑定'));
      if (info) {
        const tag = info.kind === 'group' ? '群聊' : info.kind === 'friend' ? '私聊' : (info.kind || '会话');
        bind.appendChild(
          el('small', 'mono dim', `${info.platform} · ${tag} · ${String(info.sid).slice(-6)}`)
        );
      }
      bind.title =
        `平台：${info ? info.platform : '—'}\n` +
        `会话类型：${info && info.kind ? info.kind : '—'}\n` +
        `会话 ID：${info ? info.sid : '—'}\n` +
        `发送者 ID：${p.chat_uid || '—'}\n\n` +
        `（QQ 官方机器人给的是 openid，不是 QQ 号，看着乱是正常的）`;
    } else {
      bind.appendChild(el('span', 'dim', '网页匿名'));
    }
    tr.appendChild(bind);

    tr.appendChild(el('td', 'dim', fmtTime(p.updated)));

    const ops = el('td', 'ops');
    const resetBtn = el('button', 'btn mini ghost', '清分');
    resetBtn.type = 'button';
    resetBtn.addEventListener('click', () => actOnPlayer(p, 'reset'));
    const delBtn = el('button', 'btn mini danger', '删除');
    delBtn.type = 'button';
    delBtn.addEventListener('click', () => actOnPlayer(p, 'delete'));
    ops.appendChild(resetBtn);
    ops.appendChild(delBtn);
    tr.appendChild(ops);

    tbody.appendChild(tr);
  }
}

function renderRuns(rows) {
  const tbody = $('runRows');
  tbody.replaceChildren();
  if (!rows.length) {
    const tr = document.createElement('tr');
    const td = el('td', 'empty', '还没有对局记录。');
    td.colSpan = 7;
    tr.appendChild(td);
    tbody.appendChild(tr);
    return;
  }
  for (const run of rows) {
    const tr = document.createElement('tr');
    tr.appendChild(el('td', 'strong', run.display_name || run.name || '无名猪农'));
    tr.appendChild(el('td', 'num strong', fmtInt(run.score)));
    tr.appendChild(
      el('td', '', `${PIG_EMOJI[run.level] || '🐽'} ${run.level || 0} 级`)
    );
    tr.appendChild(el('td', 'num', fmtInt(run.merges)));
    tr.appendChild(el('td', 'num dim', `${fmtInt(run.duration)} 秒`));
    const sess = el('td', 'dim', sessionLabel(run));
    sess.title = run.session || '';
    tr.appendChild(sess);
    tr.appendChild(el('td', 'dim', fmtTime(run.at)));
    tbody.appendChild(tr);
  }
}

/* ================= 设置 ================= */

function fillConfig(config) {
  $('cfgPort').value = config.port || 6100;
  $('cfgUrl').value = config.public_base_url || '';
  $('cfgHost').value = config.host || '0.0.0.0';
  $('cfgBoard').value = config.board_size || 10;
  $('cfgInterval').value = config.submit_interval ?? 1.5;
  $('cfgMd').value = config.markdown_mode || 'auto';
  $('cfgPrivate').checked = Boolean(config.allow_private);
  $('cfgSession').checked = Boolean(config.session_link);
}

async function saveConfig(event) {
  event.preventDefault();
  const button = $('saveBtn');
  button.disabled = true;
  try {
    const payload = {
      port: Number($('cfgPort').value),
      public_base_url: $('cfgUrl').value.trim(),
      host: $('cfgHost').value.trim() || '0.0.0.0',
      board_size: Number($('cfgBoard').value),
      submit_interval: Number($('cfgInterval').value),
      markdown_mode: $('cfgMd').value,
      allow_private: $('cfgPrivate').checked,
      session_link: $('cfgSession').checked,
    };
    const data = await bridge.apiPost('admin/config/save', payload);
    state.service = data.service || null;
    renderService(data.service);
    fillConfig(data.config || {});
    toast(data.message || '保存好了');
  } catch (error) {
    toast(error.message || '保存失败', true);
  } finally {
    button.disabled = false;
  }
}

/* ================= 操作 ================= */

async function actOnPlayer(player, action) {
  const label = player.name || '这个猪农';
  const tip =
    action === 'delete'
      ? `删除「${label}」的档案和全部对局？删了就找不回来了。`
      : `清空「${label}」的成绩（档案和绑定保留）？`;
  if (!window.confirm(tip)) return;
  try {
    const data = await bridge.apiPost('admin/player/action', {
      player_id: player.player_id,
      action,
    });
    renderStats(data.stats);
    renderRuns(data.recent || []);
    await loadPlayers(true);
    toast(data.message || '处理完了');
  } catch (error) {
    toast(error.message || '处理失败', true);
  }
}

async function restartService() {
  const button = $('restartBtn');
  button.disabled = true;
  try {
    const data = await bridge.apiPost('admin/service/restart', {});
    state.service = data.service || null;
    renderService(data.service);
    toast(data.message || '重启完了');
  } catch (error) {
    toast(error.message || '重启失败', true);
  } finally {
    button.disabled = false;
  }
}

async function refreshAll(silent) {
  try {
    await loadOverview();
    await loadPlayers(true);
    if (!silent) toast('刷新好了');
  } catch (error) {
    toast(error.message || '读取失败', true);
  }
}

/* ================= 启动 ================= */

async function main() {
  if (!bridge) {
    $('subtitle').textContent = '这个页面要在 AstrBot 面板里打开。';
    return;
  }
  try {
    const context = await bridge.ready();
    $('subtitle').textContent = `${context.displayName || '合成大猪猪'} · ${context.pluginName || ''}`;
  } catch (error) {
    $('subtitle').textContent = '面板桥接没连上。';
    return;
  }

  $('refreshBtn').addEventListener('click', () => refreshAll(false));
  $('restartBtn').addEventListener('click', restartService);
  $('configForm').addEventListener('submit', saveConfig);
  $('searchBtn').addEventListener('click', () => {
    state.query = $('searchInput').value.trim();
    loadPlayers(true).catch((error) => toast(error.message || '搜索失败', true));
  });
  $('searchInput').addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      $('searchBtn').click();
    }
  });
  $('morePlayers').addEventListener('click', () => {
    loadPlayers(false).catch((error) => toast(error.message || '读取失败', true));
  });

  await refreshAll(true);
}

main();
