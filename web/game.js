/* 合成大猪猪 · 网页版前端 */
(function () {
  'use strict';

  var P = window.PigPhysics;
  if (!P) return;

  var LEVELS = P.LEVELS;
  var BW = P.CONFIG.width;
  var BH = P.CONFIG.height;
  var DANGER = P.CONFIG.dangerLine;
  var MAX_DPR = 2;
  var DROP_COOLDOWN = 240;
  var STEP = 1 / 120;

  var root = document.documentElement;
  var $ = function (id) { return document.getElementById(id); };

  var els = {
    canvas: $('board'),
    score: $('score'),
    best: $('best'),
    nextPig: $('nextPig'),
    nextName: $('nextName'),
    overlay: $('overlay'),
    ovTitle: $('ovTitle'),
    ovScore: $('ovScore'),
    ovMeta: $('ovMeta'),
    againBtn: $('againBtn'),
    shareBtn: $('shareBtn'),
    hint: $('hint'),
    rankList: $('rankList'),
    rankEmpty: $('rankEmpty'),
    sessionTab: $('sessionTab'),
    meBest: $('meBest'),
    meRank: $('meRank'),
    mePlays: $('mePlays'),
    nameInput: $('nameInput'),
    bindHint: $('bindHint'),
    themeBtn: $('themeBtn'),
    soundBtn: $('soundBtn'),
    helpBtn: $('helpBtn'),
    helpModal: $('helpModal'),
    helpClose: $('helpClose'),
    chainLine: $('chainLine'),
    restartBtn: $('restartBtn'),
    loginBtn: $('loginBtn'),
    claimBtn: $('claimBtn'),
    toast: $('toast'),
    gate: $('gate'),
    gateForm: $('gateForm'),
    gateName: $('gateName'),
    gatePass: $('gatePass'),
    gateMsg: $('gateMsg'),
    gateSubmit: $('gateSubmit'),
    gateTitle: $('gateTitle'),
    gateSub: $('gateSub'),
    tabRegister: $('tabRegister'),
    tabLogin: $('tabLogin'),
    gateTabs: $('gateTabs'),
    gateGuest: $('gateGuest'),
    gateClaim: $('gateClaim')
  };
  els.ctx = els.canvas.getContext('2d');

  var LS = {
    pid: 'pig_player_id',
    name: 'pig_player_name',
    best: 'pig_best_local',
    token: 'pig_player_token',
    guest: 'pig_guest_mode',
    theme: 'pig_theme',
    sound: 'pig_sound'
  };

  var state = {
    world: new P.World(),
    score: 0,
    best: 0,
    merges: 0,
    drops: 0,
    currentLevel: 1,
    nextLevel: 1,
    aimX: BW / 2,
    running: true,
    over: false,
    startedAt: Date.now(),
    lastDrop: 0,
    pressed: false,
    particles: [],
    floats: [],
    scope: 'all',
    session: '',
    token: '',
    playerId: '',
    playerName: '',
    palette: null,
    pxScale: 1,
    submitting: false,
    guest: false,
    hasPass: true
  };

  /* ================= 小工具 ================= */

  function clamp(v, lo, hi) {
    return v < lo ? lo : v > hi ? hi : v;
  }

  function fmt(n) {
    var v = Math.max(0, Math.round(Number(n) || 0));
    return String(v).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  }

  function lsGet(key, fallback) {
    try {
      var v = localStorage.getItem(key);
      return v === null ? fallback : v;
    } catch (e) {
      return fallback;
    }
  }

  function lsSet(key, value) {
    try {
      localStorage.setItem(key, String(value));
    } catch (e) {
      /* 隐身模式什么的，随它去 */
    }
  }

  function sanitizeName(raw) {
    var text = String(raw == null ? '' : raw).replace(/[\u0000-\u001f\u007f]/g, '');
    text = text.replace(/\s+/g, ' ').trim();
    return text.slice(0, 12);
  }

  function randomId() {
    var alphabet = 'abcdefghijkmnpqrstuvwxyz23456789';
    var out = 'p';
    var buf = new Uint8Array(12);
    if (window.crypto && window.crypto.getRandomValues) {
      window.crypto.getRandomValues(buf);
    } else {
      for (var i = 0; i < buf.length; i++) buf[i] = Math.floor(Math.random() * 256);
    }
    for (var j = 0; j < buf.length; j++) out += alphabet[buf[j] % alphabet.length];
    return out;
  }

  function defaultName() {
    return '猪农' + (1000 + Math.floor(Math.random() * 9000));
  }

  /**
   * 出球等级：只出 1~3 级，但比例跟着分数走。
   * 分数越高一级猪越少、三级猪越多，别让整局都在白送小球；
   * 另外同一等级最多连出 3 次（见 randomLevel），免得连着 4 颗一级猪白给。
   */
  var spawn = { streak: 0, last: 0 };

  function spawnWeights() {
    var t = clamp(state.score / 8000, 0, 1);
    return [0.66 - 0.22 * t, 0.26 + 0.12 * t, 0.08 + 0.10 * t];
  }

  function randomLevel() {
    var w = spawnWeights();
    // 同一个等级连出 3 次就够够的了，第 4 次强制换一个
    if (spawn.streak >= 3) w[spawn.last - 1] = 0;
    var total = w[0] + w[1] + w[2];
    var roll = Math.random() * total;
    var level = 0;
    for (var i = 0; i < w.length; i++) {
      roll -= w[i];
      if (roll < 0) {
        level = i + 1;
        break;
      }
    }
    // 浮点兜底：万一没落到任何一档，取剩下概率最大的
    if (!level) level = w[2] > 0 ? 3 : w[1] > 0 ? 2 : 1;
    if (level === spawn.last) {
      spawn.streak += 1;
    } else {
      spawn.last = level;
      spawn.streak = 1;
    }
    return level;
  }

  function pigIconHtml(spec) {
    if (!spec) return '🐽';
    var img = spec.img ? pigImgs[spec.level] : null;
    if (img) {
      return '<img class="pig-icon" src="' + spec.img + '" alt="' + spec.name +
        '" title="' + spec.name + '" draggable="false">';
    }
    return spec.emoji || '🐽';
  }

  function refreshChain() {
    if (!els.chainLine) return;
    var html = LEVELS.map(function (spec) {
      return pigIconHtml(spec);
    }).join('<i class="chain-sep">›</i>');
    // 最后一级标个顶，省得有人以为天使猪后面还有
    html += '<i class="chain-sep">›</i><b class="chain-max">顶</b>';
    els.chainLine.innerHTML = html;
  }

  function levelText(level) {
    var spec = LEVELS[level - 1];
    return spec ? spec.emoji + ' ' + spec.name : '—';
  }

  var toastTimer = null;
  function toast(message) {
    els.toast.textContent = message;
    els.toast.classList.remove('hidden');
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(function () {
      els.toast.classList.add('hidden');
    }, 2400);
  }

  function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text);
    }
    return Promise.reject(new Error('no clipboard'));
  }

  /* ================= 音效 ================= */

  // 全部用 WebAudio 现场合成，不带任何音频文件：不占体积、不用管版权。
  var sound = { on: lsGet(LS.sound, 'on') !== 'off', ctx: null, master: null };

  function ensureAudio() {
    if (sound.ctx) return sound.ctx;
    var AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return null;
    try {
      sound.ctx = new AC();
      sound.master = sound.ctx.createGain();
      sound.master.gain.value = 0.6;
      sound.master.connect(sound.ctx.destination);
    } catch (e) {
      sound.ctx = null;
    }
    return sound.ctx;
  }

  function beep(opts) {
    if (!sound.on) return;
    var ctx = ensureAudio();
    if (!ctx) return;
    if (ctx.state === 'suspended' && ctx.resume) {
      try { ctx.resume(); } catch (e) { /* ignore */ }
    }
    var t0 = ctx.currentTime;
    var dur = Math.max(0.03, opts.dur);
    var from = Math.max(20, opts.from);
    var to = Math.max(20, opts.to);
    var osc = ctx.createOscillator();
    var gain = ctx.createGain();
    osc.type = opts.type || 'sine';
    osc.frequency.setValueAtTime(from, t0);
    osc.frequency.exponentialRampToValueAtTime(to, t0 + dur);
    gain.gain.setValueAtTime(0.0001, t0);
    gain.gain.exponentialRampToValueAtTime(opts.vol || 0.12, t0 + Math.min(0.02, dur * 0.3));
    gain.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    osc.connect(gain);
    gain.connect(sound.master);
    osc.start(t0);
    osc.stop(t0 + dur + 0.03);
  }

  // 一小段带包络的噪声，用来做「呼噜 / 噗」这种非乐音的声音
  function noise(opts) {
    if (!sound.on) return;
    var ctx = ensureAudio();
    if (!ctx) return;
    if (ctx.state === 'suspended' && ctx.resume) {
      try { ctx.resume(); } catch (e) { /* ignore */ }
    }
    var t0 = ctx.currentTime;
    var dur = Math.max(0.04, opts.dur);
    var len = Math.max(1, Math.floor(ctx.sampleRate * dur));
    var buf = ctx.createBuffer(1, len, ctx.sampleRate);
    var data = buf.getChannelData(0);
    for (var i = 0; i < len; i++) {
      data[i] = (Math.random() * 2 - 1) * (1 - i / len);
    }
    var src = ctx.createBufferSource();
    src.buffer = buf;
    var filter = ctx.createBiquadFilter();
    filter.type = 'bandpass';
    filter.Q.value = opts.q || 1.4;
    filter.frequency.setValueAtTime(Math.max(80, opts.from || 800), t0);
    filter.frequency.exponentialRampToValueAtTime(Math.max(60, opts.to || 300), t0 + dur);
    var gain = ctx.createGain();
    gain.gain.setValueAtTime(opts.vol || 0.1, t0);
    gain.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    src.connect(filter);
    filter.connect(gain);
    gain.connect(sound.master);
    src.start(t0);
    src.stop(t0 + dur + 0.02);
  }

  var sfx = {
    drop: function () { beep({ type: 'sine', from: 280, to: 130, dur: 0.1, vol: 0.1 }); },
    land: function (power) {
      var vol = Math.min(0.14, 0.03 + power / 16000);
      beep({ type: 'triangle', from: 170, to: 92, dur: 0.07, vol: vol });
    },
    merge: function (level) {
      var base = 300 * Math.pow(1.1, Math.max(0, level - 2));
      beep({ type: 'triangle', from: base, to: base * 1.9, dur: 0.13, vol: 0.12 });
      beep({ type: 'sine', from: base * 2, to: base * 3.1, dur: 0.09, vol: 0.05 });
    },
    vanish: function () {
      [523, 659, 784, 1047].forEach(function (f, i) {
        setTimeout(function () {
          beep({ type: 'sine', from: f, to: f * 1.5, dur: 0.5, vol: 0.09 });
        }, i * 90);
      });
    },
    over: function () { beep({ type: 'sawtooth', from: 300, to: 80, dur: 0.65, vol: 0.09 }); },
    // 猪挨打：闷闷的一声「哼」，低频锯齿打底 + 一点带通噪声
    pigHurt: function () {
      beep({ type: 'sawtooth', from: 215, to: 108, dur: 0.15, vol: 0.1 });
      beep({ type: 'square', from: 118, to: 78, dur: 0.17, vol: 0.045 });
      noise({ from: 950, to: 260, dur: 0.14, vol: 0.065, q: 2.2 });
    },
    // 猪倒下：一声拖长的哀嚎，频率掉得更低
    pigDie: function () {
      beep({ type: 'sawtooth', from: 190, to: 58, dur: 0.52, vol: 0.1 });
      beep({ type: 'square', from: 96, to: 42, dur: 0.56, vol: 0.045 });
      noise({ from: 760, to: 130, dur: 0.5, vol: 0.07, q: 1.5 });
    }
  };

  function applySound(on) {
    sound.on = !!on;
    lsSet(LS.sound, sound.on ? 'on' : 'off');
    if (els.soundBtn) {
      els.soundBtn.textContent = sound.on ? '🔊' : '🔇';
      els.soundBtn.title = sound.on ? '音效开（点一下静音）' : '音效关（点一下打开）';
      els.soundBtn.setAttribute('aria-label', els.soundBtn.title);
    }
  }

  function toggleSound() {
    applySound(!sound.on);
    if (sound.on) sfx.merge(3);
    toast(sound.on ? '音效开了' : '静音了');
  }

  /** 服务端配置：背景图 + 默认主题（本地选过主题的话以本地为准） */
  async function loadServerConfig() {
    try {
      var res = await fetch('/api/config');
      var data = await res.json();
      if (!data || !data.ok) return;
      if (data.bg_image) applyBackground(data.bg_image);
      var saved = lsGet(LS.theme, '');
      if (saved !== 'dark' && saved !== 'light') {
        var want = data.default_theme;
        applyTheme(want === 'dark' || want === 'light' ? want : autoTheme(), false);
      }
    } catch (err) {
      /* 拿不到就按默认来 */
    }
  }

  /* ================= 主题 ================= */

  var ballColorCache = {};

  function autoTheme() {
    return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches
      ? 'dark'
      : 'light';
  }

  function readPalette() {
    var css = getComputedStyle(root);
    var dark = root.dataset.theme === 'dark';
    return {
      dark: dark,
      board: css.getPropertyValue('--board').trim() || '#fbf6ee',
      topZone: dark ? 'rgba(120,150,220,0.06)' : 'rgba(255,214,170,0.26)',
      line: css.getPropertyValue('--board-line').trim() || 'rgba(0,0,0,0.12)',
      danger: css.getPropertyValue('--danger').trim() || '#e0604f',
      ring: css.getPropertyValue('--ball-ring').trim() || 'rgba(255,255,255,0.8)',
      aim: css.getPropertyValue('--accent').trim() || '#4d6bfe',
      shadow: dark ? 'rgba(0,0,0,0.55)' : 'rgba(90,60,30,0.22)'
    };
  }

  function applyTheme(theme, persist) {
    var resolved = theme === 'dark' || theme === 'light' ? theme : autoTheme();
    root.dataset.theme = resolved;
    root.style.colorScheme = resolved;
    if (els.themeBtn) {
      els.themeBtn.textContent = resolved === 'dark' ? '☀️' : '🌙';
      els.themeBtn.title = resolved === 'dark' ? '切到浅色' : '切到深色';
      els.themeBtn.setAttribute('aria-label', els.themeBtn.title);
    }
    if (persist) lsSet(LS.theme, resolved);
    state.palette = readPalette();
    ballColorCache = {};
    pigSprites = {};
    spriteCount = 0;
  }

  function toggleTheme() {
    applyTheme(root.dataset.theme === 'dark' ? 'light' : 'dark', true);
  }

  /**
   * 自定义背景：后台配了图就用图，外加一层柔和遮罩，别把界面糊住。
   * 只认 http(s) 和站内绝对路径，别的（data:、javascript: 之类）一律不碰。
   */
  function applyBackground(url) {
    var safe = String(url == null ? '' : url).trim();
    if (!safe) return;
    if (!/^(https?:\/\/[^\s"')]+|\/[^\s"')]*)$/i.test(safe)) return;
    document.body.style.setProperty('--bg-image', 'url("' + safe.replace(/"/g, '%22') + '")');
    document.body.classList.add('has-bg');
  }

  function ballColors(hue, dark) {
    var key = hue + '|' + (dark ? 'd' : 'l');
    if (ballColorCache[key]) return ballColorCache[key];
    var c = dark
      ? { light: 'hsl(' + hue + ' 62% 46%)', dark: 'hsl(' + hue + ' 58% 26%)' }
      : { light: 'hsl(' + hue + ' 92% 88%)', dark: 'hsl(' + hue + ' 68% 63%)' };
    ballColorCache[key] = c;
    return c;
  }

  /* ================= 画布 ================= */

  function setupCanvas() {
    var stage = $('stage');
    var rect = els.canvas.getBoundingClientRect();

    // 老内核（部分 QQ/UC 内置浏览器）不认 aspect-ratio，.stage 会被压成 0 高，
    // canvas 就只剩一片空白。这里用 JS 兜底把高度撑开，顺便修宽高比。
    if (stage) {
      var boxW = stage.clientWidth || rect.width;
      if (boxW > 0) {
        var want = Math.round((boxW * BH) / BW);
        if (Math.abs(stage.clientHeight - want) > 1) stage.style.height = want + 'px';
        rect = els.canvas.getBoundingClientRect();
      }
    }

    var dpr = Math.min(window.devicePixelRatio || 1, MAX_DPR);
    var cssW = rect.width || BW;
    var cssH = rect.height || BH;
    var w = Math.max(1, Math.round(cssW * dpr));
    var h = Math.max(1, Math.round(cssH * dpr));
    if (els.canvas.width !== w || els.canvas.height !== h) {
      els.canvas.width = w;
      els.canvas.height = h;
    }
    // 宽高不一致时用较小的比例，宁可留一点边也不要把画面拉变形
    var scale = Math.min(cssW / BW, cssH / BH) * dpr;
    state.pxScale = isFinite(scale) && scale > 0 ? scale : dpr;
    state.palette = state.palette || readPalette();
  }

  /* ---------- 猪猪贴图 ---------- */

  var pigImgs = {};      // level -> Image（null = 加载失败，回退 emoji）
  var pigSprites = {};   // level|px|dark -> canvas，一次性预渲染
  var spriteCount = 0;

  function preloadPigs() {
    var jobs = [];
    for (var i = 0; i < LEVELS.length; i++) {
      (function (spec) {
        if (!spec.img) return;
        jobs.push(
          new Promise(function (resolve) {
            var img = new Image();
            img.decoding = 'async';
            img.onload = function () {
              pigImgs[spec.level] = img;
              resolve();
            };
            img.onerror = function () {
              pigImgs[spec.level] = null;
              resolve();
            };
            img.src = spec.img;
          })
        );
      })(LEVELS[i]);
    }
    return Promise.all(jobs);
  }

  /**
   * 把贴图预渲染成「球」：圆形裁切 + 内阴影 + 高光 + 外环。
   * 一帧最多上百个球，每帧现算 clip/渐变会掉帧，所以按像素尺寸缓存。
   */
  function buildSprite(level, px, dark) {
    var img = pigImgs[level];
    if (!img || !img.complete || !img.naturalWidth) return null;

    px = Math.max(8, Math.min(384, Math.round(px / 2) * 2));
    var key = level + '|' + px + '|' + (dark ? 'd' : 'l');
    if (pigSprites[key]) return pigSprites[key];
    if (spriteCount > 300) {
      pigSprites = {};
      spriteCount = 0;
    }

    var pad = Math.max(2, Math.round(px * 0.08));
    var size = px + pad * 2;
    var r = px / 2;
    var cx = size / 2;
    var cy = size / 2;

    var c = document.createElement('canvas');
    c.width = size;
    c.height = size;
    var g = c.getContext('2d');
    g.imageSmoothingEnabled = true;
    if ('imageSmoothingQuality' in g) g.imageSmoothingQuality = 'high';

    // 圆形裁切后铺满整个球
    g.save();
    g.beginPath();
    g.arc(cx, cy, r, 0, Math.PI * 2);
    g.clip();
    g.drawImage(img, cx - r, cy - r, px, px);

    // 底部内阴影，给球一点体积感
    var shade = g.createRadialGradient(
      cx - r * 0.18, cy - r * 0.24, r * 0.25,
      cx, cy, r * 1.02
    );
    shade.addColorStop(0, 'rgba(0,0,0,0)');
    shade.addColorStop(0.68, 'rgba(0,0,0,0)');
    shade.addColorStop(1, dark ? 'rgba(0,0,0,0.46)' : 'rgba(70,40,15,0.20)');
    g.fillStyle = shade;
    g.fillRect(cx - r, cy - r, px, px);

    // 左上高光
    var gloss = g.createRadialGradient(
      cx - r * 0.34, cy - r * 0.4, r * 0.05,
      cx - r * 0.34, cy - r * 0.4, r * 0.78
    );
    gloss.addColorStop(0, dark ? 'rgba(255,255,255,0.22)' : 'rgba(255,255,255,0.34)');
    gloss.addColorStop(1, 'rgba(255,255,255,0)');
    g.fillStyle = gloss;
    g.fillRect(cx - r, cy - r, px, px);

    if (dark) {
      g.fillStyle = 'rgba(8,10,16,0.10)';
      g.fillRect(cx - r, cy - r, px, px);
    }
    g.restore();

    // 外环
    g.beginPath();
    g.arc(cx, cy, r - Math.max(0.5, px * 0.02), 0, Math.PI * 2);
    g.strokeStyle = state.palette ? state.palette.ring : 'rgba(255,255,255,0.85)';
    g.lineWidth = Math.max(1.4, px * 0.045);
    g.stroke();

    var sprite = { canvas: c, px: px };
    pigSprites[key] = sprite;
    spriteCount++;
    return sprite;
  }

  function drawBall(ctx, ball, alpha) {
    var spec = LEVELS[ball.level - 1];
    if (!spec) return;
    var r = ball.r;
    var scale = state.pxScale > 0 ? state.pxScale : 1;

    var spr = buildSprite(ball.level, r * 2 * scale, state.palette.dark);
    if (spr) {
      var half = spr.canvas.width / scale / 2;
      ctx.save();
      if (alpha != null) ctx.globalAlpha = alpha;
      ctx.drawImage(spr.canvas, ball.x - half, ball.y - half, half * 2, half * 2);
      ctx.restore();
      return;
    }

    // 贴图还没解码完 / 加载失败 —— 退回原来的 emoji 球，至少能玩
    var colors = ballColors(spec.hue, state.palette.dark);
    ctx.save();
    if (alpha != null) ctx.globalAlpha = alpha;
    var grad = ctx.createRadialGradient(
      ball.x - r * 0.34, ball.y - r * 0.4, r * 0.18,
      ball.x, ball.y, r * 1.04
    );
    grad.addColorStop(0, colors.light);
    grad.addColorStop(1, colors.dark);
    ctx.beginPath();
    ctx.arc(ball.x, ball.y, r, 0, Math.PI * 2);
    ctx.fillStyle = grad;
    ctx.fill();
    ctx.beginPath();
    ctx.arc(ball.x, ball.y, r - 0.6, 0, Math.PI * 2);
    ctx.strokeStyle = state.palette.ring;
    ctx.lineWidth = Math.max(1.2, r * 0.055);
    ctx.stroke();
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.font = Math.round(r * 1.12) +
      'px "Apple Color Emoji","Segoe UI Emoji","Noto Color Emoji",sans-serif';
    ctx.fillText(spec.emoji, ball.x, ball.y + r * 0.05);
    ctx.restore();
  }

  function drawAim(ctx) {
    var spec = LEVELS[state.currentLevel - 1];
    if (!spec) return;
    var x = clamp(state.aimX, spec.r + 1, BW - spec.r - 1);
    var pal = state.palette;

    ctx.save();
    ctx.globalAlpha = 0.28;
    ctx.strokeStyle = pal.aim;
    ctx.lineWidth = 2;
    ctx.setLineDash([8, 10]);
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, BH);
    ctx.stroke();
    ctx.restore();

    var ghost = {
      x: x,
      y: spec.r + 10,
      r: spec.r,
      level: state.currentLevel
    };
    drawBall(ctx, ghost, 0.5);
  }

  function render() {
    var ctx = els.ctx;
    var pal = state.palette;
    if (!pal) return;

    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, els.canvas.width, els.canvas.height);
    ctx.setTransform(state.pxScale, 0, 0, state.pxScale, 0, 0);

    ctx.fillStyle = pal.board;
    ctx.fillRect(0, 0, BW, BH);

    ctx.fillStyle = pal.topZone;
    ctx.fillRect(0, 0, BW, DANGER);

    var balls = state.world.balls;
    for (var i = 0; i < balls.length; i++) {
      drawBall(ctx, balls[i]);
    }

    if (state.running && !state.over) drawAim(ctx);

    // 危险线
    ctx.save();
    ctx.globalAlpha = 0.5;
    ctx.strokeStyle = pal.danger;
    ctx.lineWidth = 1.6;
    ctx.setLineDash([9, 9]);
    ctx.beginPath();
    ctx.moveTo(0, DANGER);
    ctx.lineTo(BW, DANGER);
    ctx.stroke();
    ctx.restore();

    // 罐壁
    ctx.save();
    ctx.strokeStyle = pal.line;
    ctx.lineWidth = 2;
    ctx.strokeRect(1, 1, BW - 2, BH - 2);
    ctx.restore();

    drawParticles(ctx);
    drawFloats(ctx);
  }

  /* ================= 特效 ================= */

  function spawnBurst(x, y, hue, power) {
    var count = 8 + Math.round(power * 1.4);
    for (var i = 0; i < count; i++) {
      var angle = (Math.PI * 2 * i) / count + Math.random() * 0.5;
      var speed = 70 + Math.random() * 160;
      state.particles.push({
        x: x,
        y: y,
        vx: Math.cos(angle) * speed,
        vy: Math.sin(angle) * speed - 40,
        r: 2 + Math.random() * 3,
        life: 0,
        max: 0.45 + Math.random() * 0.35,
        hue: hue
      });
    }
  }

  function spawnFloat(x, y, text, color) {
    state.floats.push({
      x: x,
      y: y,
      text: text,
      color: color,
      life: 0,
      max: 0.9
    });
  }

  function updateEffects(dt) {
    var i;
    for (i = state.particles.length - 1; i >= 0; i--) {
      var p = state.particles[i];
      p.life += dt;
      if (p.life >= p.max) {
        state.particles.splice(i, 1);
        continue;
      }
      p.vy += 900 * dt;
      p.x += p.vx * dt;
      p.y += p.vy * dt;
    }
    for (i = state.floats.length - 1; i >= 0; i--) {
      var f = state.floats[i];
      f.life += dt;
      if (f.life >= f.max) {
        state.floats.splice(i, 1);
        continue;
      }
      f.y -= 34 * dt;
    }
  }

  function drawParticles(ctx) {
    var dark = state.palette.dark;
    for (var i = 0; i < state.particles.length; i++) {
      var p = state.particles[i];
      var k = 1 - p.life / p.max;
      ctx.save();
      ctx.globalAlpha = Math.max(0, k) * 0.9;
      ctx.fillStyle = 'hsl(' + p.hue + ' 78% ' + (dark ? 58 : 66) + '%)';
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.r * (0.4 + k * 0.8), 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();
    }
  }

  function drawFloats(ctx) {
    for (var i = 0; i < state.floats.length; i++) {
      var f = state.floats[i];
      var k = 1 - f.life / f.max;
      ctx.save();
      ctx.globalAlpha = Math.max(0, k);
      ctx.fillStyle = f.color;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.font = '700 20px system-ui, sans-serif';
      ctx.fillText(f.text, f.x, f.y);
      ctx.restore();
    }
  }

  /* ================= 记分 ================= */

  function updateScore() {
    els.score.textContent = fmt(state.score);
  }

  function updateNext() {
    var spec = LEVELS[state.nextLevel - 1];
    els.nextPig.innerHTML = pigIconHtml(spec);
    if (els.nextName) els.nextName.textContent = spec ? spec.name : '—';
    els.nextPig.classList.remove('pop');
    // 触发重绘动画
    void els.nextPig.offsetWidth;
    els.nextPig.classList.add('pop');
  }

  function handleMerge(event) {
    if (event.type === 'land') {
      sfx.land(event.power || 0);
      return;
    }

    state.score += event.gain;
    state.merges += 1;
    updateScore();

    var spec = LEVELS[event.level - 1] || LEVELS[0];
    if (event.type === 'vanish') {
      sfx.vanish();
      spawnBurst(event.x, event.y, 46, 16);
      spawnFloat(event.x, event.y - 20, '+' + fmt(event.gain), '#f0a92a');
      spawnFloat(event.x, event.y - 48, '升天', '#f0a92a');
    } else {
      sfx.merge(event.level);
      spawnBurst(event.x, event.y, spec.hue, event.level);
      spawnFloat(
        event.x,
        event.y - spec.r * 0.5,
        '+' + fmt(event.gain),
        state.palette.aim
      );
    }
  }

  /* ================= 流程 ================= */

  function newGame() {
    state.world = new P.World();
    state.score = 0;
    state.merges = 0;
    state.drops = 0;
    state.over = false;
    state.running = true;
    state.startedAt = Date.now();
    spawn.streak = 0;
    spawn.last = 0;
    state.currentLevel = randomLevel();
    state.nextLevel = randomLevel();
    state.aimX = BW / 2;
    state.particles.length = 0;
    state.floats.length = 0;
    state.submitting = false;
    els.overlay.classList.add('hidden');
    els.hint.classList.remove('hidden');
    updateScore();
    updateNext();
    els.best.textContent = fmt(state.best);
  }

  function dropPig() {
    if (state.over || !state.running) return;
    var now = performance.now();
    if (now - state.lastDrop < DROP_COOLDOWN) return;
    if (state.world.balls.length >= P.CONFIG.maxBalls) return;

    var level = state.currentLevel;
    var spec = LEVELS[level - 1];
    var x = clamp(state.aimX, spec.r + 1, BW - spec.r - 1);
    state.world.drop(x, level);
    sfx.drop();
    state.lastDrop = now;
    state.drops += 1;
    if (state.drops >= 3) els.hint.classList.add('hidden');

    state.currentLevel = state.nextLevel;
    state.nextLevel = randomLevel();
    updateNext();
  }

  function endGame() {
    state.over = true;
    state.running = false;
    sfx.over();
    var duration = Math.max(1, Math.round((Date.now() - state.startedAt) / 1000));
    var top = state.world.topLevel();

    els.ovScore.textContent = fmt(state.score);
    els.ovTitle.textContent = state.score >= state.best && state.score > 0 ? '新纪录！' : '罐子炸了';
    els.ovMeta.textContent =
      '最肥一头 ' + levelText(top) + ' · ' + duration + ' 秒 · 合成 ' + state.merges + ' 次';
    els.overlay.classList.remove('hidden');

    if (state.score > state.best) {
      state.best = state.score;
      lsSet(LS.best, state.best);
    }
    els.best.textContent = fmt(state.best);
    submitScore(duration, top);
  }

  async function submitScore(duration, top) {
    if (state.submitting) return;
    // 游客和没登录的一律不上榜，只在本机留个最高分
    if (state.guest || !state.token) {
      els.ovMeta.textContent += ' · 游客局，没上榜';
      return;
    }
    state.submitting = true;
    try {
      var res = await fetch('/api/records', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          player_id: state.playerId,
          name: state.playerName,
          t: state.token,
          score: state.score,
          level: top,
          merges: state.merges,
          duration: duration,
          session: state.session
        })
      });
      var data = await res.json();
      if (!data.ok) throw new Error(data.message || '没记上');
      if (data.best > state.best) {
        state.best = data.best;
        lsSet(LS.best, state.best);
        els.best.textContent = fmt(state.best);
      }
      var rankText = data.rank ? '总榜第 ' + data.rank + ' / ' + data.total + ' 位' : '';
      els.ovMeta.textContent =
        '最肥一头 ' + levelText(top) + ' · ' + duration + ' 秒 · 合成 ' + state.merges + ' 次' +
        (rankText ? ' · ' + rankText : '');
      if (data.new_best) toast('刷新个人纪录了');
      refreshLeaderboard();
      loadMe();
    } catch (err) {
      els.ovMeta.textContent += ' · 这次成绩没传上去（' + (err.message || '网络') + '）';
    } finally {
      state.submitting = false;
    }
  }

  async function copyReport() {
    var text =
      '我在合成大猪猪拿了 ' + fmt(state.score) + ' 分，最肥一头 ' + levelText(state.world.topLevel()) +
      '。来拼榜：' + location.origin + '/';
    try {
      await copyText(text);
      toast('战报复制好了，去群里发吧');
    } catch (err) {
      toast('复制不了，手动截个图吧');
    }
  }

  /* ================= 榜单 ================= */

  function clearNode(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function renderBoard(data) {
    clearNode(els.rankList);
    var rows = data.rows || [];
    if (!rows.length) {
      els.rankEmpty.classList.remove('hidden');
      els.rankEmpty.textContent =
        data.scope === 'session' ? '本群还没人交成绩，先去玩一局吧。' : '还没有猪农上榜，第一口就等你了。';
      return;
    }
    els.rankEmpty.classList.add('hidden');

    var medals = ['🥇', '🥈', '🥉'];
    rows.forEach(function (row, index) {
      var li = document.createElement('li');
      if (row.player_id === state.playerId) li.classList.add('me');

      var pos = document.createElement('span');
      pos.className = 'pos';
      pos.textContent = index < 3 ? medals[index] : String(index + 1);

      var who = document.createElement('span');
      who.className = 'who';
      who.appendChild(document.createTextNode(row.name || '无名猪农'));
      if (row.best_level) {
        var sub = document.createElement('small');
        sub.textContent = levelText(row.best_level);
        who.appendChild(sub);
      }

      var pts = document.createElement('span');
      pts.className = 'pts';
      pts.textContent = fmt(row.best);

      li.appendChild(pos);
      li.appendChild(who);
      li.appendChild(pts);
      els.rankList.appendChild(li);
    });
  }

  async function refreshLeaderboard() {
    var scope = state.scope;
    if (scope === 'session' && !state.session) scope = 'all';
    var url =
      '/api/records?scope=' + encodeURIComponent(scope) +
      '&limit=10&s=' + encodeURIComponent(state.session);
    try {
      var res = await fetch(url);
      var data = await res.json();
      if (!data.ok) throw new Error(data.message || '读不到');
      renderBoard(data);
    } catch (err) {
      els.rankEmpty.classList.remove('hidden');
      els.rankEmpty.textContent = '榜单暂时读不出来，稍后再刷一下。';
    }
  }

  async function loadMe() {
    // 游客没有服务端档案，别白跑一趟
    if (!state.token || state.guest) return;
    try {
      var res = await fetch('/api/players/' + encodeURIComponent(state.playerId));
      var data = await res.json();
      if (!data.ok) return;
      var player = data.player;
      if (player && player.best > 0) {
        els.meBest.textContent = fmt(player.best);
        els.meRank.textContent = '第 ' + player.rank;
        els.mePlays.textContent = fmt(player.plays);
        if (player.best > state.best) {
          state.best = player.best;
          lsSet(LS.best, state.best);
          els.best.textContent = fmt(state.best);
        }
      } else if (player) {
        els.meBest.textContent = '0';
        els.meRank.textContent = '—';
        els.mePlays.textContent = fmt(player.plays || 0);
      } else {
        els.meBest.textContent = '—';
        els.meRank.textContent = '—';
        els.mePlays.textContent = '—';
      }
      if (player && player.chat_name) {
        clearNode(els.bindHint);
        els.bindHint.textContent =
          '已绑定猪农「' + (player.name || '—') + '」（' + player.chat_name + '），分数记在这个档案下。';
      }
    } catch (err) {
      /* 静默失败，不影响游戏 */
    }
  }

  /* ================= 主循环 ================= */

  var lastTs = 0;
  var acc = 0;

  function frame(ts) {
    if (!lastTs) lastTs = ts;
    var dt = (ts - lastTs) / 1000;
    lastTs = ts;
    if (dt > 0.1) dt = 0.1;
    if (dt < 0) dt = 0;

    var world = state.world;
    if (state.running && !state.over) {
      acc += dt;
      var guard = 0;
      while (acc >= STEP && guard < 16) {
        world.step(STEP);
        acc -= STEP;
        guard++;
      }
      if (guard >= 16) acc = 0;
    }

    var events = world.drainEvents();
    for (var i = 0; i < events.length; i++) handleMerge(events[i]);

    updateEffects(dt);
    if (world.over && !state.over) endGame();

    render();
    requestAnimationFrame(frame);
  }

  /* ================= 输入 ================= */

  function pointerX(ev) {
    var rect = els.canvas.getBoundingClientRect();
    if (!rect.width) return state.aimX;
    return clamp(((ev.clientX - rect.left) / rect.width) * BW, 0, BW);
  }

  function bindInputs() {
    els.canvas.addEventListener('pointermove', function (ev) {
      if (state.over) return;
      state.aimX = pointerX(ev);
    });

    els.canvas.addEventListener('pointerdown', function (ev) {
      if (state.over) return;
      ensureAudio(); // 手势里唤醒音频上下文，不然 iOS 一声都不响
      state.aimX = pointerX(ev);
      state.pressed = true;
      if (els.canvas.setPointerCapture) {
        try { els.canvas.setPointerCapture(ev.pointerId); } catch (e) { /* ignore */ }
      }
      ev.preventDefault();
    });

    els.canvas.addEventListener('pointerup', function (ev) {
      ev.preventDefault();
      if (!state.pressed) return;
      state.pressed = false;
      state.aimX = pointerX(ev);
      dropPig();
    });

    els.canvas.addEventListener('pointercancel', function () {
      state.pressed = false;
    });

    els.canvas.addEventListener('pointerleave', function () {
      state.pressed = false;
    });

    window.addEventListener('keydown', function (ev) {
      var tag = (ev.target && ev.target.tagName) || '';
      if (tag === 'INPUT' || tag === 'TEXTAREA' || ev.target.isContentEditable) return;

      if (ev.key === 'ArrowLeft' || ev.key === 'a' || ev.key === 'A') {
        state.aimX = clamp(state.aimX - 16, 0, BW);
        ev.preventDefault();
      } else if (ev.key === 'ArrowRight' || ev.key === 'd' || ev.key === 'D') {
        state.aimX = clamp(state.aimX + 16, 0, BW);
        ev.preventDefault();
      } else if (ev.key === ' ' || ev.key === 'ArrowDown' || ev.key === 's' || ev.key === 'S') {
        dropPig();
        ev.preventDefault();
      } else if (ev.key === 'r' || ev.key === 'R') {
        newGame();
        ev.preventDefault();
      } else if (ev.key === 'Escape') {
        els.helpModal.classList.add('hidden');
      }
    });

    els.againBtn.addEventListener('click', newGame);
    els.restartBtn.addEventListener('click', newGame);
    els.shareBtn.addEventListener('click', copyReport);
    els.themeBtn.addEventListener('click', toggleTheme);
    if (els.soundBtn) els.soundBtn.addEventListener('click', toggleSound);
    applySound(sound.on);
    els.helpBtn.addEventListener('click', function () {
      els.helpModal.classList.remove('hidden');
    });
    els.helpClose.addEventListener('click', function () {
      els.helpModal.classList.add('hidden');
    });
    els.helpModal.addEventListener('click', function (ev) {
      if (ev.target === els.helpModal) els.helpModal.classList.add('hidden');
    });

    els.nameInput.addEventListener('change', function () {
      var name = sanitizeName(els.nameInput.value) || defaultName();
      state.playerName = name;
      els.nameInput.value = name;
      lsSet(LS.name, name);
      toast('昵称改成「' + name + '」，下一局生效');
    });

    var tabs = document.querySelectorAll('.tab');
    for (var i = 0; i < tabs.length; i++) {
      tabs[i].addEventListener('click', function (ev) {
        var scope = ev.currentTarget.getAttribute('data-scope');
        if (scope === 'session' && !state.session) return;
        var all = document.querySelectorAll('.tab');
        for (var k = 0; k < all.length; k++) all[k].classList.remove('active');
        ev.currentTarget.classList.add('active');
        state.scope = scope;
        refreshLeaderboard();
      });
    }

    var claimBtn = $('claimBtn');
    if (claimBtn) claimBtn.addEventListener('click', requestClaimCode);

    var fit = function () { setupCanvas(); };
    window.addEventListener('resize', fit);
    window.addEventListener('orientationchange', fit);
    if (window.ResizeObserver) {
      try { new ResizeObserver(fit).observe(els.canvas); } catch (e) { /* ignore */ }
    }
  }

  /* ================= 彩蛋：左上角那只猪 ================= */

  // 前 3 下点它没有任何反应。第 4 下亮出像素风血条（MC 里猪是 10 点血 = 5 颗心），
  // 之后每点一下掉 1 点血、响一声挨打的哼唧；血扣光后变红、顺时针转 90°、
  // 一团白雾加一声倒地音效，然后图标消失。刷新页面才会重新出现。

  var HEART_PATH = 'M12 21.3C9.2 19.2 3 14.7 3 9.9 3 6.6 5.4 4.4 8.4 4.4c1.5 0 2.8.7 3.6 1.8.8-1.1 2.1-1.8 3.6-1.8 3 0 5.4 2.2 5.4 5.5 0 4.8-6.2 9.3-9 11.4z';

  var egg = {
    el: null,
    bar: null,
    clicks: 0,
    hearts: 5,
    maxHp: 10,
    hp: 10,
    shown: false,
    dead: false
  };

  function heartNode() {
    var node = document.createElement('i');
    node.className = 'hp-heart';
    node.innerHTML = '<svg class="hp-empty" viewBox="0 0 24 24" aria-hidden="true"><path d="' + HEART_PATH + '"/></svg>' +
      '<span class="hp-cut"><svg class="hp-full" viewBox="0 0 24 24" aria-hidden="true"><path d="' + HEART_PATH + '"/></svg></span>';
    return node;
  }

  function buildEggBar() {
    var bar = document.createElement('span');
    bar.className = 'pig-hp';
    for (var i = 0; i < egg.hearts; i++) bar.appendChild(heartNode());
    return bar;
  }

  // 每颗心 2 点血，一颗心可以只亮一半
  function paintEggHp() {
    if (!egg.bar) return;
    var cuts = egg.bar.querySelectorAll('.hp-cut');
    for (var i = 0; i < cuts.length; i++) {
      var left = clamp(egg.hp - i * 2, 0, 2);
      cuts[i].style.width = (left / 2) * 100 + '%';
    }
    if (egg.hp <= 4) egg.bar.classList.add('low');
    else egg.bar.classList.remove('low');
  }

  function puffSmoke(host, count, spread) {
    for (var i = 0; i < count; i++) {
      var p = document.createElement('span');
      p.className = 'egg-smoke';
      var ang = Math.random() * Math.PI * 2;
      var dist = (0.4 + Math.random() * 0.6) * spread;
      p.style.setProperty('--dx', (Math.cos(ang) * dist).toFixed(1) + 'px');
      p.style.setProperty('--dy', (Math.sin(ang) * dist - spread * 0.25).toFixed(1) + 'px');
      p.style.setProperty('--sc', (1.1 + Math.random() * 1.6).toFixed(2));
      p.style.animationDelay = Math.round(Math.random() * 120) + 'ms';
      host.appendChild(p);
      (function (el) {
        setTimeout(function () {
          if (el.parentNode) el.parentNode.removeChild(el);
        }, 1100);
      })(p);
    }
  }

  function eggShake() {
    if (!egg.el) return;
    egg.el.classList.remove('hit');
    void egg.el.offsetWidth; // 强制回流，动画才能重播
    egg.el.classList.add('hit');
  }

  function eggDie() {
    egg.dead = true;
    if (egg.bar) egg.bar.classList.remove('show');
    egg.el.classList.remove('hit');
    egg.el.classList.add('dead');
    sfx.pigDie();
    puffSmoke(egg.el, 20, 26);
    setTimeout(function () {
      if (egg.bar && egg.bar.parentNode) egg.bar.parentNode.removeChild(egg.bar);
    }, 900);
    setTimeout(function () {
      if (!egg.el) return;
      egg.el.classList.add('gone');
      setTimeout(function () {
        if (egg.el && egg.el.parentNode) egg.el.parentNode.removeChild(egg.el);
      }, 320);
    }, 880);
  }

  function eggClick() {
    if (!egg.el || egg.dead) return;
    ensureAudio(); // 这一下是用户手势，顺手把音频上下文唤醒
    egg.clicks++;
    if (egg.clicks <= 3) return;

    if (!egg.shown) {
      egg.shown = true;
      egg.bar = buildEggBar();
      egg.el.appendChild(egg.bar);
      paintEggHp();
      requestAnimationFrame(function () {
        if (egg.bar) egg.bar.classList.add('show');
      });
      return;
    }

    if (egg.hp <= 0) return;
    egg.hp--;
    paintEggHp();
    sfx.pigHurt();
    eggShake();
    if (egg.hp <= 0) eggDie();
  }

  function initPigEgg() {
    var el = $('pigEgg');
    if (!el) return;
    egg.el = el;
    el.addEventListener('click', function (ev) {
      ev.preventDefault();
      eggClick();
    });
  }

  /* ================= 启动 ================= */

  /* ================= 身份门：注册 / 登录 ================= */

  var gateState = { mode: 'register', busy: false };

  function gateMsg(text, ok) {
    var el = els.gateMsg;
    if (!el) return;
    if (!text) {
      el.textContent = '';
      el.classList.add('hidden');
      return;
    }
    el.textContent = text;
    el.classList.toggle('ok', !!ok);
    el.classList.remove('hidden');
  }

  function setGateMode(mode) {
    var m = mode === 'login' ? 'login' : (mode === 'setpass' ? 'setpass' : 'register');
    gateState.mode = m;
    var isLogin = m === 'login';
    var isSet = m === 'setpass';
    if (els.tabRegister) els.tabRegister.classList.toggle('active', !isLogin && !isSet);
    if (els.tabLogin) els.tabLogin.classList.toggle('active', isLogin);
    if (els.gateTabs) els.gateTabs.classList.toggle('hidden', isSet);
    if (els.gateTitle) {
      els.gateTitle.textContent = isSet ? '给档案设个口令' : (isLogin ? '欢迎回来' : '先落个户吧');
    }
    if (els.gateSub) {
      els.gateSub.textContent = isSet
        ? '这一步别跳过：设好之后，换手机换浏览器都能用「昵称 + 口令」登回来'
        : (isLogin ? '昵称 + 口令，换设备也能接上原来的分数' : '建一份猪农档案，换手机换浏览器都能找回成绩');
    }
    if (els.gateSubmit) {
      els.gateSubmit.textContent = isSet ? '存好口令' : (isLogin ? '登录开玩' : '注册并开玩');
    }
    if (els.gateName) {
      els.gateName.disabled = isSet;
      if (isSet) els.gateName.value = state.playerName || '';
    }
    if (els.gateGuest) els.gateGuest.textContent = isSet ? '先不设，继续玩' : '先随便玩玩';
    if (els.gateClaim) els.gateClaim.classList.toggle('hidden', isSet);
    if (els.gatePass) {
      els.gatePass.autocomplete = isLogin ? 'current-password' : 'new-password';
      els.gatePass.value = '';
    }
    gateMsg('');
  }

  function openGate(mode) {
    if (!els.gate) return;
    setGateMode(mode || 'register');
    if (els.gateName && gateState.mode !== 'setpass') {
      // 游客或者之前留过名字的话，先替用户填好
      els.gateName.value = state.playerName && !state.guest ? state.playerName : '';
    }
    els.gate.classList.remove('hidden');
    setTimeout(function () {
      var focusEl = gateState.mode === 'setpass' ? els.gatePass : els.gateName;
      if (focusEl) focusEl.focus();
    }, 80);
  }

  function closeGate() {
    if (els.gate) els.gate.classList.add('hidden');
    gateMsg('');
  }

  function hintLine(text) {
    clearNode(els.bindHint);
    var p = document.createElement('p');
    p.className = 'bind-hint';
    p.textContent = text;
    els.bindHint.appendChild(p);
  }

  // 服务端档案落到本地状态：token、昵称、最高分、本群榜
  function applyProfile(data) {
    if (!data || !data.player) return false;
    var player = data.player;
    if (data.token) {
      state.token = String(data.token);
      lsSet(LS.token, state.token);
    }
    if (data.session) {
      state.session = String(data.session).slice(0, 512);
      if (els.sessionTab) {
        els.sessionTab.disabled = false;
        els.sessionTab.title = '只看这个群的成绩';
      }
    }
    state.guest = false;
    lsSet(LS.guest, '');
    state.hasPass = player.has_pass !== false;
    if (player.player_id) state.playerId = String(player.player_id);
    if (player.name) state.playerName = sanitizeName(player.name) || state.playerName;
    lsSet(LS.pid, state.playerId);
    lsSet(LS.name, state.playerName);

    if (els.nameInput) {
      els.nameInput.value = state.playerName;
      els.nameInput.disabled = true;
      els.nameInput.title = '名字跟着账号走';
    }

    if (player.best > 0 && player.best > state.best) {
      state.best = player.best;
      lsSet(LS.best, state.best);
      els.best.textContent = fmt(state.best);
    }

    var where = player.bound && player.chat_name
      ? '已绑定「' + player.name + '」（' + player.chat_name + '）'
      : '已登录「' + state.playerName + '」';
    hintLine(where + '，这一局的分数记到这份档案。');

    if (els.loginBtn) {
      if (state.hasPass) {
        els.loginBtn.classList.add('hidden');
        els.loginBtn.textContent = '注册 / 登录';
      } else {
        // 没有口令的档案：留一个入口，方便随时补设
        els.loginBtn.classList.remove('hidden');
        els.loginBtn.textContent = '设口令';
      }
    }
    showClaimBtn();
    return true;
  }

  function applyGuest(silent) {
    state.guest = true;
    state.token = '';
    lsSet(LS.guest, '1');
    lsSet(LS.token, '');
    if (els.nameInput) {
      els.nameInput.disabled = false;
      els.nameInput.title = '';
    }
    hintLine('游客模式：分数只存在这台设备，注册一下就能上排行榜。');
    if (els.loginBtn) {
      els.loginBtn.classList.remove('hidden');
      els.loginBtn.textContent = '注册 / 登录';
    }
    if (els.claimBtn) els.claimBtn.classList.add('hidden');
    if (!silent) toast('游客模式，分数不进榜');
  }

  async function doAuth(ev) {
    if (ev && ev.preventDefault) ev.preventDefault();
    if (gateState.busy) return;

    var mode = gateState.mode;
    var isSet = mode === 'setpass';
    var isLogin = mode === 'login';
    var name = sanitizeName(els.gateName ? els.gateName.value : '');
    var pass = String(els.gatePass ? els.gatePass.value : '');

    if (isSet) {
      if (!state.token) {
        gateMsg('登录状态丢了，回群里 @我 重新要个链接');
        return;
      }
    } else {
      if (!name || name.length < 2) {
        gateMsg('名字至少 2 个字');
        return;
      }
      // 注意：JS 的 \w 只认 ASCII，中文昵称得显式列出，否则会被误判
      if (!/^[\u3400-\u4dbf\u4e00-\u9fffA-Za-z0-9_·\-]{2,12}$/.test(name)) {
        gateMsg('名字只能是 2~12 个中英文、数字、下划线或短横线，不能带空格');
        return;
      }
    }
    if (pass.length < 4) {
      gateMsg('口令至少 4 位');
      return;
    }

    gateState.busy = true;
    var btn = els.gateSubmit;
    var label = btn ? btn.textContent : '';
    if (btn) {
      btn.disabled = true;
      btn.textContent = isSet ? '保存中…' : (isLogin ? '登录中…' : '注册中…');
    }
    try {
      var url = isSet ? '/api/setpass' : '/api/' + (isLogin ? 'login' : 'register');
      var body = isSet
        ? { t: state.token, pass: pass }
        : { name: name, pass: pass, s: state.session || '' };
      var res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
      var data = await res.json();
      if (!data.ok) throw new Error(data.message || '没成');

      if (isSet) {
        state.hasPass = true;
        closeGate();
        if (els.loginBtn) {
          els.loginBtn.classList.add('hidden');
          els.loginBtn.textContent = '注册 / 登录';
        }
        hintLine('已登录「' + state.playerName + '」· 口令已设好，换设备用昵称 + 口令就能回来。');
        toast('口令存好了');
        return;
      }

      if (!applyProfile(data)) throw new Error('档案没回来，再试一次');
      closeGate();
      toast((isLogin ? '欢迎回来，' : '注册好了，') + state.playerName);
      refreshLeaderboard();
      loadMe();
    } catch (err) {
      gateMsg(err.message || '网络不太行，等下再试');
    } finally {
      gateState.busy = false;
      if (btn) {
        btn.disabled = false;
        btn.textContent = label;
      }
    }
  }

  function bindGate() {
    if (els.gateForm) els.gateForm.addEventListener('submit', doAuth);
    if (els.tabRegister) {
      els.tabRegister.addEventListener('click', function () { setGateMode('register'); });
    }
    if (els.tabLogin) {
      els.tabLogin.addEventListener('click', function () { setGateMode('login'); });
    }
    if (els.gateGuest) {
      els.gateGuest.addEventListener('click', function () {
        // 「设口令」弹窗里的这个按钮是「先不设」，别把身份降级成游客
        if (gateState.mode === 'setpass') {
          closeGate();
          toast('先玩着，想设了点右上角「设口令」');
          return;
        }
        applyGuest(false);
        closeGate();
      });
    }
    if (els.gateClaim) {
      els.gateClaim.addEventListener('click', function () {
        gateMsg('在旧设备游戏页点「换设备」拿 6 位码，回群里 @我 发「猪猪认领 码」。', true);
      });
    }
    if (els.loginBtn) {
      els.loginBtn.addEventListener('click', function () {
        if (state.token && !state.hasPass && !state.guest) {
          openGate('setpass');
          return;
        }
        openGate(state.playerName ? 'login' : 'register');
      });
    }
  }

  // 门票 / 长期凭证换档案；fromLink 表示这次是点群里的链接进来的
  async function restoreIdentity(ticket, kind, fromLink) {
    try {
      var url = kind === 'k'
        ? '/api/session?k=' + encodeURIComponent(ticket)
        : '/api/identity?t=' + encodeURIComponent(ticket);
      var res = await fetch(url);
      var data = await res.json();
      if (!data.ok || !data.player) throw new Error(data.message || '凭证失效了');
      applyProfile(data);
      // 门票换来的长期凭证只存本地，链接里不再带它
      return true;
    } catch (err) {
      lsSet(LS.token, '');
      state.token = '';
      if (fromLink) {
        hintLine((err && err.message) || '这个入口链接失效了，回群里 @我 重新要一个。');
      }
      return false;
    }
  }

  function showClaimBtn() {
    var btn = $('claimBtn');
    if (btn) btn.classList.remove('hidden');
  }

  async function requestClaimCode() {
    if (!state.token) return;
    var btn = $('claimBtn');
    if (btn) btn.disabled = true;
    try {
      var res = await fetch('/api/claim', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ t: state.token })
      });
      var data = await res.json();
      if (!data.ok || !data.code) throw new Error(data.message || '拿不到码');
      clearNode(els.bindHint);
      var p1 = document.createElement('p');
      p1.className = 'bind-hint';
      p1.textContent = '迁移码 ' + data.code + '（10 分钟内有效，只能用一次）';
      var p2 = document.createElement('p');
      p2.className = 'bind-hint';
      p2.textContent = '在新平台/新设备里 @机器人 发「猪猪认领 ' + data.code + '」，成绩就跟着过去。';
      els.bindHint.appendChild(p1);
      els.bindHint.appendChild(p2);
      toast('迁移码 ' + data.code + '，10 分钟内有效');
    } catch (err) {
      toast(err.message || '拿码失败，等会儿再试');
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async function boot() {
    // 身份
    state.playerId = lsGet(LS.pid, '');
    if (!/^[A-Za-z0-9_-]{6,40}$/.test(state.playerId)) {
      state.playerId = randomId();
      lsSet(LS.pid, state.playerId);
    }
    state.playerName = sanitizeName(lsGet(LS.name, '')) || defaultName();
    lsSet(LS.name, state.playerName);
    els.nameInput.value = state.playerName;

    state.best = parseInt(lsGet(LS.best, '0'), 10) || 0;
    els.best.textContent = fmt(state.best);

    var params = new URLSearchParams(location.search);
    state.session = (params.get('s') || '').slice(0, 512);
    if (!state.session) {
      els.sessionTab.disabled = true;
      els.sessionTab.title = '从群里点链接进来才有本群榜';
    }

    // 群里的入口链接带 k（一次性门票），老链接可能还带 t；都没有就用本地存的凭证
    var ticket = '';
    var ticketKind = 't';
    var fromLink = false;
    if (params.get('k')) {
      ticket = params.get('k').trim();
      ticketKind = 'k';
      fromLink = true;
    } else if (params.get('t')) {
      ticket = params.get('t').trim();
      fromLink = true;
    } else {
      ticket = lsGet(LS.token, '');
    }

    var authed = false;
    if (ticket && /^[A-Za-z0-9_-]{6,128}$/.test(ticket)) {
      authed = await restoreIdentity(ticket, ticketKind, fromLink);
      // 门票用掉之后从地址栏里抹掉，别留在历史和截图里
      if (fromLink) {
        try {
          history.replaceState(null, '', location.pathname || '/');
        } catch (e) { /* ignore */ }
      }
    }

    // 没有可用身份：记住选过游客就继续游客，否则第一次进来先注册 / 登录
    if (!authed) {
      if (lsGet(LS.guest, '') === '1') {
        applyGuest(true);
      } else {
        openGate('register');
      }
    } else if (!state.hasPass) {
      // 群里注册的老档案没有口令，换浏览器就永久进不来，这里当面催一次
      openGate('setpass');
      gateMsg('这份档案还没口令，换个浏览器就登不进来了——现在补一个（4~32 位，别用生日）', true);
    }

    // 主题
    var savedTheme = lsGet(LS.theme, '');
    applyTheme(savedTheme === 'dark' || savedTheme === 'light' ? savedTheme : autoTheme(), false);
    loadServerConfig();
    if (window.matchMedia) {
      var mq = window.matchMedia('(prefers-color-scheme: dark)');
      var onChange = function () {
        var saved = lsGet(LS.theme, '');
        if (saved !== 'dark' && saved !== 'light') applyTheme(autoTheme(), false);
      };
      if (mq.addEventListener) mq.addEventListener('change', onChange);
      else if (mq.addListener) mq.addListener(onChange);
    }

    refreshChain();
    preloadPigs().then(function () {
      refreshChain();
      updateNext();
    });

    bindInputs();
    initPigEgg();
    bindGate();
    setupCanvas();
    newGame();
    requestAnimationFrame(frame);

    refreshLeaderboard();
    loadMe();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

  // 排查用的只读句柄：出问题时能在控制台看一眼世界状态
  window.__pig = {
    get state() { return state; },
    get world() { return state.world; },
    get pigs() { return pigImgs; },
    newGame: newGame
  };
})();
