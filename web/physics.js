/*
 * 合成大猪猪 · 圆形物理引擎
 * 纯逻辑，不碰 DOM，浏览器里挂在 window.PigPhysics，Node 里可以 require 做测试。
 */
(function (global) {
  'use strict';

  var CONFIG = {
    width: 420,
    height: 620,
    // 罐口：越小越靠上，留给玩家的空间越多。
    // v3.4.0 之前是 120，顶部一大片用不上，手感像「矮罐子」，这里收到 88。
    dangerLine: 88,
    gravity: 2200,
    restitution: 0.08,
    wallRestitution: 0.18,
    // 切向摩擦：越大越"黏"，堆起来的猪不会一碰就滑开，罐子更容易堆满
    friction: 0.18,
    // 贴墙 / 落地后的横向阻尼，越小越不容易被挤着滑走
    wallFriction: 0.72,
    // 慢速球的额外阻尼（每秒），让猪堆"压得住"
    slowDamp: 2.6,
    // 分离重叠时的位置修正比例，小一点更软、更不容易弹开
    splitCorrection: 0.78,
    // 合并判定放宽比例：贴图有描边/留白，视觉上贴住了但数值差一两像素时也能合上
    mergeTolerance: 1.02,
    iterations: 6,
    maxBalls: 110,
    maxSpeed: 4200,
    mergePerStep: 40,
    landPerStep: 3
  };

  /**
   * 每级猪猪：r 是世界坐标半径，img 是贴图（相对 web/ 目录），
   * emoji 只在贴图加载失败时兜底，保证没网/没图也能玩。
   */
  var LEVELS = [
    { level: 1, emoji: '🐽', name: '小猪崽', r: 19, hue: 16, img: 'pigs/l1.png' },
    { level: 2, emoji: '🐷', name: '苹果猪', r: 24, hue: 22, img: 'pigs/l2.png' },
    { level: 3, emoji: '🐖', name: '大懒猪', r: 30, hue: 348, img: 'pigs/l3.png' },
    { level: 4, emoji: '🐗', name: '野猪', r: 37, hue: 26, img: 'pigs/l4.png' },
    { level: 5, emoji: '🥓', name: '培根', r: 45, hue: 6, img: 'pigs/l5.png' },
    { level: 6, emoji: '🍢', name: '烤猪串', r: 53, hue: 34, img: 'pigs/l6.png' },
    { level: 7, emoji: '🍔', name: '猪排堡', r: 62, hue: 40, img: 'pigs/l7.png' },
    { level: 8, emoji: '👑', name: '锁冠猪王', r: 71, hue: 46, img: 'pigs/l8.png' },
    { level: 9, emoji: '✨', name: '猪神', r: 80, hue: 276, img: 'pigs/l9.png' }
  ];

  var MAX_LEVEL = LEVELS.length;
  var VANISH_BONUS = 5000;

  /** 合成出 level 级的得分 */
  function scoreOf(level) {
    if (level < 2) return 0;
    return 10 * Math.pow(2, level - 1);
  }

  function clamp(v, lo, hi) {
    return v < lo ? lo : v > hi ? hi : v;
  }

  function World(options) {
    var o = options || {};
    this.width = o.width || CONFIG.width;
    this.height = o.height || CONFIG.height;
    this.dangerLine = o.dangerLine || CONFIG.dangerLine;
    this.gravity = o.gravity != null ? o.gravity : CONFIG.gravity;
    this.restitution = o.restitution != null ? o.restitution : CONFIG.restitution;
    this.wallRestitution =
      o.wallRestitution != null ? o.wallRestitution : CONFIG.wallRestitution;
    this.friction = o.friction != null ? o.friction : CONFIG.friction;
    this.wallFriction = o.wallFriction != null ? o.wallFriction : CONFIG.wallFriction;
    this.slowDamp = o.slowDamp != null ? o.slowDamp : CONFIG.slowDamp;
    this.splitCorrection =
      o.splitCorrection != null ? o.splitCorrection : CONFIG.splitCorrection;
    this.mergeTolerance =
      o.mergeTolerance != null ? o.mergeTolerance : CONFIG.mergeTolerance;
    this.iterations = o.iterations || CONFIG.iterations;
    this.maxBalls = o.maxBalls || CONFIG.maxBalls;

    this.balls = [];
    this.events = [];
    this.nextId = 1;
    this.time = 0;
    this.over = false;
    this.merges = 0;
    this.overTime = 0;
    this._mergeBudget = CONFIG.mergePerStep;
    this._landBudget = CONFIG.landPerStep;
  }

  World.prototype.addBall = function (x, y, level) {
    level = clamp(level | 0, 1, MAX_LEVEL);
    var spec = LEVELS[level - 1];
    var r = spec.r;
    var ball = {
      id: this.nextId++,
      x: clamp(x, r, this.width - r),
      y: y,
      vx: 0,
      vy: 0,
      r: r,
      level: level,
      age: 0,
      danger: 0,
      dead: false
    };
    this.balls.push(ball);
    return ball;
  };

  /** 从顶部丢一颗，返回球体 */
  World.prototype.drop = function (x, level) {
    level = clamp(level | 0, 1, MAX_LEVEL);
    var r = LEVELS[level - 1].r;
    var ball = this.addBall(x, r + 6, level);
    ball.vy = 180;
    return ball;
  };

  World.prototype.step = function (dt) {
    if (this.over) return;
    dt = Math.min(Math.max(dt, 0), 1 / 30);
    this.time += dt;
    this._mergeBudget = CONFIG.mergePerStep;
    this._landBudget = CONFIG.landPerStep;

    var balls = this.balls;
    var i;
    var b;

    // 1. 积分 + 墙
    var airDrag = Math.pow(0.35, dt);
    for (i = 0; i < balls.length; i++) {
      b = balls[i];
      if (b.dead) continue;
      b.age += dt;
      b.vy += this.gravity * dt;
      b.vx *= airDrag;
      b.vy *= Math.pow(0.995, dt);
      var sp = Math.hypot(b.vx, b.vy);
      if (sp > CONFIG.maxSpeed) {
        b.vx *= CONFIG.maxSpeed / sp;
        b.vy *= CONFIG.maxSpeed / sp;
      }
      b.x += b.vx * dt;
      b.y += b.vy * dt;
      // 慢下来以后额外阻尼：压在堆里的猪别一直"打滑"
      if (Math.abs(b.vx) < 90 && Math.abs(b.vy) < 90) {
        var slow = Math.pow(0.35, this.slowDamp * dt);
        b.vx *= slow;
        b.vy *= slow;
      }
      this._clampWalls(b);
    }

    // 2. 同级相撞就合成
    this._resolveMerges();

    // 3. 分开重叠
    this._separate();

    // 4. 收拾尸体 / 判负
    var alive = [];
    for (i = 0; i < this.balls.length; i++) {
      if (!this.balls[i].dead) alive.push(this.balls[i]);
    }
    this.balls = alive;
    this._checkOver(dt);
  };

  World.prototype._clampWalls = function (b) {
    if (b.x - b.r < 0) {
      b.x = b.r;
      if (b.vx < 0) b.vx = -b.vx * this.wallRestitution;
    } else if (b.x + b.r > this.width) {
      b.x = this.width - b.r;
      if (b.vx > 0) b.vx = -b.vx * this.wallRestitution;
    }
    if (b.y + b.r > this.height) {
      var impact = b.vy;
      b.y = this.height - b.r;
      if (b.vy > 0) b.vy = -b.vy * this.restitution;
      b.vx *= this.wallFriction;
      if (Math.abs(b.vy) < 34) b.vy = 0;
      // 落地音效用：只报真正砸下来的那一下，且每步最多报几次
      if (impact > 620 && this._landBudget > 0) {
        this._landBudget--;
        this.events.push({ type: 'land', x: b.x, y: b.y, power: impact });
      }
    }
  };

  World.prototype._resolveMerges = function () {
    var balls = this.balls;
    for (var i = 0; i < balls.length; i++) {
      var a = balls[i];
      if (a.dead) continue;
      for (var j = i + 1; j < balls.length; j++) {
        var b = balls[j];
        if (b.dead || b.level !== a.level) continue;
        var dx = b.x - a.x;
        var dy = b.y - a.y;
        // 贴图是圆形裁切 + 描边，视觉上的边比碰撞半径"胖"一点点，
        // 所以这里给一点容差，别让玩家看着明明贴上了却不合。
        // 最高级（猪神）也照样判合并 —— 两颗撞一起走 vanish 分支一起升天。
        var rr = (a.r + b.r) * this.mergeTolerance;
        if (dx * dx + dy * dy > rr * rr) continue;
        if (this._mergeBudget <= 0) return;
        this._mergeBudget--;
        this._merge(a, b);
        break;
      }
    }
  };

  World.prototype._merge = function (a, b) {
    var nx = (a.x + b.x) / 2;
    var ny = (a.y + b.y) / 2;
    var nvx = (a.vx + b.vx) * 0.5 * 0.5;
    var nvy = (a.vy + b.vy) * 0.5 * 0.5;
    var level = a.level + 1;
    a.dead = true;
    b.dead = true;
    this.merges++;

    if (level > MAX_LEVEL) {
      this.events.push({
        type: 'vanish',
        from: MAX_LEVEL,
        level: MAX_LEVEL,
        x: nx,
        y: ny,
        gain: VANISH_BONUS
      });
      return;
    }

    var ball = this.addBall(nx, ny, level);
    ball.vx = nvx;
    ball.vy = nvy - 60;
    ball.age = 1;
    this.events.push({
      type: 'merge',
      from: level - 1,
      level: level,
      x: nx,
      y: ny,
      gain: scoreOf(level)
    });
  };

  World.prototype._separate = function () {
    var balls = this.balls;
    var n = balls.length;
    var iters = this.iterations;
    for (var k = 0; k < iters; k++) {
      for (var i = 0; i < n; i++) {
        var a = balls[i];
        if (a.dead) continue;
        for (var j = i + 1; j < n; j++) {
          var b = balls[j];
          if (b.dead) continue;
          var dx = b.x - a.x;
          var dy = b.y - a.y;
          var rr = a.r + b.r;
          var d2 = dx * dx + dy * dy;
          if (d2 >= rr * rr) continue;
          var d = Math.sqrt(d2);
          var nx;
          var ny;
          if (d < 0.0001) {
            nx = 0;
            ny = -1;
            d = 0.0001;
          } else {
            nx = dx / d;
            ny = dy / d;
          }
          var invA = 1 / (a.r * a.r);
          var invB = 1 / (b.r * b.r);
          var invSum = invA + invB;
          var overlap = rr - d;

          a.x -= nx * overlap * (invA / invSum) * this.splitCorrection;
          a.y -= ny * overlap * (invA / invSum) * this.splitCorrection;
          b.x += nx * overlap * (invB / invSum) * this.splitCorrection;
          b.y += ny * overlap * (invB / invSum) * this.splitCorrection;

          var rvx = b.vx - a.vx;
          var rvy = b.vy - a.vy;
          var vn = rvx * nx + rvy * ny;
          if (vn < 0) {
            var imp = (-(1 + this.restitution) * vn) / invSum;
            a.vx -= imp * invA * nx;
            a.vy -= imp * invA * ny;
            b.vx += imp * invB * nx;
            b.vy += imp * invB * ny;
          }
          // 切向摩擦，防止叠成一层滑来滑去
          var tx = -ny;
          var ty = nx;
          var vt = rvx * tx + rvy * ty;
          var jt = (-vt * this.friction) / invSum;
          a.vx -= jt * invA * tx;
          a.vy -= jt * invA * ty;
          b.vx += jt * invB * tx;
          b.vy += jt * invB * ty;
        }
      }
      for (var m = 0; m < n; m++) {
        if (!balls[m].dead) this._clampWalls(balls[m]);
      }
    }
  };

  World.prototype._checkOver = function (dt) {
    var over = false;
    var line = this.dangerLine;
    for (var i = 0; i < this.balls.length; i++) {
      var b = this.balls[i];
      if (b.dead) continue;
      if (b.age < 0.75) {
        b.danger = 0;
        continue;
      }
      var above = b.y - b.r < line;
      var settled = Math.abs(b.vy) < 110 && Math.abs(b.vx) < 110;
      if (above && settled) {
        b.danger += dt;
        if (b.danger > 1.1) over = true;
      } else {
        b.danger = 0;
      }
    }
    if (over) {
      this.over = true;
      this.overTime = this.time;
    }
  };

  World.prototype.drainEvents = function () {
    var out = this.events;
    this.events = [];
    return out;
  };

  World.prototype.topLevel = function () {
    var top = 0;
    for (var i = 0; i < this.balls.length; i++) {
      if (this.balls[i].level > top) top = this.balls[i].level;
    }
    return top;
  };

  World.prototype.heightRatio = function () {
    var lowest = this.height;
    for (var i = 0; i < this.balls.length; i++) {
      var b = this.balls[i];
      if (b.dead) continue;
      if (b.y - b.r < lowest) lowest = b.y - b.r;
    }
    return clamp(1 - (lowest - this.dangerLine) / (this.height - this.dangerLine), 0, 1);
  };

  var api = {
    CONFIG: CONFIG,
    LEVELS: LEVELS,
    MAX_LEVEL: MAX_LEVEL,
    VANISH_BONUS: VANISH_BONUS,
    scoreOf: scoreOf,
    World: World
  };

  global.PigPhysics = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
