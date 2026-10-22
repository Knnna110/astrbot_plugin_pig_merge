#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""合成大猪猪 · 网页版 v3

浏览器里玩的「合成大西瓜」式物理合成游戏，主题换成猪。

架构：
    · 游戏页：插件自己起一个小 HTTP 服务（默认 6100，冲突时在 6100~6110 内顺延），
      吐页面 + 分数接口，聊天侧只负责发链接。
    · 管理页：挂在 AstrBot 面板里的插件视图（views/admin），
      看玩家、看对局、清档案、改端口，不额外占端口。

聊天触发（必须 @ 机器人，且「🐖/🐷/猪」和「游戏」挨在一起）：
    @机器人 🐖游戏          -> 发游戏链接；没注册就提示先注册
    猪猪排行榜                -> 总榜 / 本群榜 + 自己的名次
    猪猪注册 名字            -> 注册猪农档案，绑定当前会话的 UMO

输出优先走 Markdown（QQ 官方机器人），其余平台自动降级成纯文本。
"""

from __future__ import annotations

import asyncio
import base64
import random
import re
import secrets
import socket
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import At
from astrbot.api.star import Context, Star, StarTools

try:  # aiohttp 是 AstrBot 自带的依赖，兜个底省得崩
    from aiohttp import web as aioweb
except Exception:  # pragma: no cover
    aioweb = None  # type: ignore[assignment]

try:  # AstrBot 4.29+ 的插件 Web API 助手
    from astrbot.api.web import error_response, json_response, request
except Exception:  # pragma: no cover
    error_response = json_response = request = None  # type: ignore[assignment]


PLUGIN_NAME = "astrbot_plugin_pig_merge"
PLUGIN_VERSION = "3.5.0"

MD_PLATFORMS = {"qq_official", "qq_official_webhook"}

MAX_LEVEL = 9
MAX_SCORE = 2_000_000

PORT_MIN = 6100
PORT_MAX = 6110

# 等级 -> (图标, 名字)
PIGS: Dict[int, Tuple[str, str]] = {
    1: ("🐽", "小猪崽"),
    2: ("🐷", "苹果猪"),
    3: ("🐖", "大懒猪"),
    4: ("🐗", "野猪"),
    5: ("🥓", "培根"),
    6: ("🍢", "烤猪串"),
    7: ("🍔", "猪排堡"),
    8: ("👑", "锁冠猪王"),
    9: ("✨", "猪神"),
}

VANISH_BONUS = 5000  # 两个猪王撞一起

PLAYER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,40}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
NAME_RE = re.compile(r"^[\w·\-]{2,12}$", re.UNICODE)
# 网页口令：4~32 位，可见 ASCII，不含空白
PASSWORD_RE = re.compile(r"^[\x21-\x7e]{4,32}$")
TICKET_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")
CLAIM_RE = re.compile(r"^[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{6}$")
CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")

# 必须「🐖 / 🐷 / 猪」和「游戏」连在一起才算；「合成大猪猪」也认
PIG_GAME_RE = re.compile(
    r"(?:🐖|🐷|猪{1,2})\s*游戏|游戏\s*(?:🐖|🐷)|合成大猪猪",
    re.IGNORECASE,
)
REGISTER_RE = re.compile(
    r"^\s*(?:猪猪注册|注册猪猪|猪注册|pig注册)\s*[:：]?\s*(?P<name>.*)$",
    re.IGNORECASE,
)
CLAIM_CMD_RE = re.compile(
    r"^\s*(?:猪猪认领|认领猪猪|猪猪迁移|猪认领)\s*[:：]?\s*(?P<code>[0-9A-Za-z]{4,10})?\s*$",
    re.IGNORECASE,
)
# 排行榜：允许「猪猪排行榜」「🐖排行榜」「猪榜」「合成大猪猪 排行榜」这类写法；
# 必须带「猪」相关前缀，免得把群里别家的「排行榜」抢过来
BOARD_CMD_RE = re.compile(
    r"^\s*(?:@\S+\s*)?(?:合成大猪猪|猪猪|猪|🐖|🐷)\s*(?:排行榜|排行|榜单|榜)\s*$",
    re.IGNORECASE,
)

MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".ico": "image/x-icon",
    ".webmanifest": "application/manifest+json",
}


# ==================== 小工具 ====================


def encode_session(umo: str) -> str:
    """把统一会话来源塞进链接里，前端原样带回来。"""
    if not umo:
        return ""
    raw = base64.urlsafe_b64encode(umo.encode("utf-8")).decode("ascii")
    return raw.rstrip("=")


def decode_session(token: str) -> str:
    token = (token or "").strip()
    if not token or len(token) > 512:
        return ""
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        text = raw.decode("utf-8")
    except Exception:
        return ""
    if len(text) > 300 or not text.isprintable():
        return ""
    return text


def clean_name(raw: Any, fallback: str = "无名猪农") -> str:
    text = str(raw or "")
    text = CTRL_RE.sub("", text).strip()
    text = re.sub(r"\s+", " ", text)
    if not text:
        return fallback
    return text[:24]


def clean_player_name(raw: Any) -> str:
    """注册名：去掉空白和控制字符，只留 2~12 个字符。"""
    text = CTRL_RE.sub("", str(raw or "")).strip()
    text = re.sub(r"\s+", "", text)
    return text[:12]


def fmt_int(value: Any) -> str:
    try:
        return f"{int(value):,}"
    except Exception:
        return "0"


def fmt_time(ts: Any) -> str:
    try:
        return time.strftime("%m-%d %H:%M", time.localtime(float(ts)))
    except Exception:
        return "—"


def guess_lan_ip() -> str:
    """尽最大努力猜一个别人能访问到的地址。"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 53))
            return sock.getsockname()[0]
        finally:
            sock.close()
    except Exception:
        pass
    try:
        return socket.gethostbyname(socket.gethostname())
    except Exception:
        return "127.0.0.1"


# ==================== 存储 ====================

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    score INTEGER NOT NULL DEFAULT 0,
    level INTEGER NOT NULL DEFAULT 0,
    merges INTEGER NOT NULL DEFAULT 0,
    duration INTEGER NOT NULL DEFAULT 0,
    session TEXT NOT NULL DEFAULT '',
    at REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_runs_player ON runs(player_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_runs_score ON runs(score DESC);
CREATE INDEX IF NOT EXISTS idx_runs_session ON runs(session, score DESC);

CREATE TABLE IF NOT EXISTS players (
    player_id TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    token TEXT NOT NULL DEFAULT '',
    best INTEGER NOT NULL DEFAULT 0,
    best_level INTEGER NOT NULL DEFAULT 0,
    plays INTEGER NOT NULL DEFAULT 0,
    total_score INTEGER NOT NULL DEFAULT 0,
    best_merges INTEGER NOT NULL DEFAULT 0,
    chat_umo TEXT NOT NULL DEFAULT '',
    chat_uid TEXT NOT NULL DEFAULT '',
    chat_name TEXT NOT NULL DEFAULT '',
    group_name TEXT NOT NULL DEFAULT '',
    pass_hash TEXT NOT NULL DEFAULT '',
    pass_salt TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'chat',
    created REAL NOT NULL DEFAULT 0,
    updated REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_players_best ON players(best DESC);
CREATE INDEX IF NOT EXISTS idx_players_chat ON players(chat_umo, chat_uid);

-- 一次性门票：群里发的链接只带它，几分钟就过期、只能用一次，
-- 长期凭证（players.token）不再出现在聊天记录和浏览器历史里。
CREATE TABLE IF NOT EXISTS tickets (
    ticket TEXT PRIMARY KEY,
    player_id TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'login',
    session TEXT NOT NULL DEFAULT '',
    created REAL NOT NULL DEFAULT 0,
    expires REAL NOT NULL DEFAULT 0,
    used INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_tickets_player ON tickets(player_id, kind);
"""


class ScoreStore:
    """分数和玩家档案的落地存储。所有方法都是同步的，调用方丢线程池。"""

    def __init__(self, path: Path, keep_days: int = 365, keep_runs: int = 80):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.keep_runs = max(10, int(keep_runs))
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.executescript(SCHEMA)
        self._migrate()
        self._db.commit()
        self._purge(max(1, int(keep_days)))

    # ---------- 生命周期 ----------

    def close(self) -> None:
        with self._lock:
            try:
                self._db.commit()
            finally:
                self._db.close()

    def _migrate(self) -> None:
        """给旧版本数据库补字段：token / group_name。"""
        cols = {
            str(row["name"])
            for row in self._db.execute("PRAGMA table_info(players)").fetchall()
        }
        if "token" not in cols:
            self._db.execute(
                "ALTER TABLE players ADD COLUMN token TEXT NOT NULL DEFAULT ''"
            )
        if "group_name" not in cols:
            self._db.execute(
                "ALTER TABLE players ADD COLUMN group_name TEXT NOT NULL DEFAULT ''"
            )
        # 网页注册用：口令只存 PBKDF2 摘要，永远不存明文
        if "pass_hash" not in cols:
            self._db.execute(
                "ALTER TABLE players ADD COLUMN pass_hash TEXT NOT NULL DEFAULT ''"
            )
        if "pass_salt" not in cols:
            self._db.execute(
                "ALTER TABLE players ADD COLUMN pass_salt TEXT NOT NULL DEFAULT ''"
            )
        if "source" not in cols:
            self._db.execute(
                "ALTER TABLE players ADD COLUMN source TEXT NOT NULL DEFAULT 'chat'"
            )
        self._db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_players_token "
            "ON players(token) WHERE token != ''"
        )
        # 老玩家已经绑了聊天但没有 token，补一个，下次要链接就能直接用
        rows = self._db.execute(
            "SELECT player_id FROM players WHERE chat_umo != '' AND token = ''"
        ).fetchall()
        for row in rows:
            self._db.execute(
                "UPDATE players SET token = ? WHERE player_id = ?",
                (self._new_token(), str(row["player_id"])),
            )

    def _purge(self, keep_days: int) -> None:
        cutoff = time.time() - keep_days * 86400
        with self._lock:
            self._db.execute("DELETE FROM runs WHERE at < ?", (cutoff,))
            self._db.execute(
                """
                UPDATE players SET best = COALESCE(
                    (SELECT MAX(score) FROM runs WHERE runs.player_id = players.player_id), 0
                )
                """
            )
            self._db.commit()

    @staticmethod
    def _new_token() -> str:
        return secrets.token_urlsafe(24)

    @staticmethod
    def _new_player_id() -> str:
        return "p" + secrets.token_hex(8)

    # ---------- 网页端口令 ----------

    @staticmethod
    def _hash_pass(password: str, salt: str) -> str:
        import hashlib

        return hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000
        ).hex()

    def set_password(self, player_id: str, password: str) -> bool:
        """给档案设/改口令（网页注册、聊天注册补齐都用它）。"""
        salt = secrets.token_hex(8)
        digest = self._hash_pass(password, salt)
        with self._lock:
            cur = self._db.execute(
                "UPDATE players SET pass_hash = ?, pass_salt = ?, updated = ? "
                "WHERE player_id = ?",
                (digest, salt, time.time(), player_id),
            )
            self._db.commit()
            return cur.rowcount > 0

    def player_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM players WHERE name = ? COLLATE NOCASE LIMIT 1",
                (name,),
            ).fetchone()
        return dict(row) if row else None

    def check_password(self, player: Dict[str, Any], password: str) -> bool:
        salt = str(player.get("pass_salt") or "")
        digest = str(player.get("pass_hash") or "")
        if not salt or not digest:
            return False
        try:
            return secrets.compare_digest(self._hash_pass(password, salt), digest)
        except Exception:
            return False

    def register_web(
        self, *, name: str, password: str, umo: str = ""
    ) -> Dict[str, Any]:
        """网页自助注册。不带聊天会话也能建档案，回头在群里认领即可。"""
        name = clean_player_name(name)
        if not name or not NAME_RE.match(name):
            return {"status": "bad_name"}
        if not PASSWORD_RE.match(password or ""):
            return {"status": "bad_pass"}

        now = time.time()
        with self._lock:
            clash = self._db.execute(
                "SELECT player_id FROM players WHERE name = ? COLLATE NOCASE LIMIT 1",
                (name,),
            ).fetchone()
            if clash is not None:
                return {"status": "name_taken"}

            # 从群里链接进来（带会话）的话，顺手把会话来源挂上
            chat_umo = (umo or "").strip()
            uid = ""
            chat_name = ""
            group_name = ""

            player_id = self._new_player_id()
            while self._db.execute(
                "SELECT 1 FROM players WHERE player_id = ?", (player_id,)
            ).fetchone():
                player_id = self._new_player_id()
            token = self._new_token()
            salt = secrets.token_hex(8)
            digest = self._hash_pass(password, salt)
            self._db.execute(
                """
                INSERT INTO players(
                    player_id, name, token, chat_umo, chat_uid, chat_name,
                    group_name, pass_hash, pass_salt, source, created, updated
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    player_id,
                    name,
                    token,
                    chat_umo,
                    uid,
                    chat_name,
                    group_name,
                    digest,
                    salt,
                    "web",
                    now,
                    now,
                ),
            )
            self._db.commit()
            row = self._db.execute(
                "SELECT * FROM players WHERE player_id = ?", (player_id,)
            ).fetchone()
        return {"status": "ok", "player": dict(row) if row else {}}

    def login_web(self, *, name: str, password: str) -> Dict[str, Any]:
        name = clean_player_name(name)
        if not name:
            return {"status": "bad_name"}
        player = self.player_by_name(name)
        if player is None:
            return {"status": "no_user"}
        if not str(player.get("pass_hash") or ""):
            return {"status": "no_pass"}
        if not self.check_password(player, password or ""):
            return {"status": "bad_pass"}
        player_id = str(player.get("player_id") or "")
        token = str(player.get("token") or "") or self.ensure_token(player_id)
        player = self.player(player_id) or player
        player["token"] = token
        return {"status": "ok", "player": player}

    # ---------- 注册 ----------

    def register(
        self,
        *,
        name: str,
        umo: str,
        uid: str,
        chat_name: str,
        group_name: str = "",
    ) -> Dict[str, Any]:
        """注册一个猪农档案，并绑定当前会话。幂等：同一账号重复注册直接返回旧档案。"""
        now = time.time()
        name = clean_player_name(name)
        with self._lock:
            if umo and uid:
                row = self._db.execute(
                    "SELECT * FROM players WHERE chat_umo = ? AND chat_uid = ?",
                    (umo, uid),
                ).fetchone()
                if row is not None:
                    if not row["token"]:
                        token = self._new_token()
                        self._db.execute(
                            "UPDATE players SET token = ? WHERE player_id = ?",
                            (token, row["player_id"]),
                        )
                        self._db.commit()
                        row = self._db.execute(
                            "SELECT * FROM players WHERE player_id = ?",
                            (row["player_id"],),
                        ).fetchone()
                    return {"status": "exists", "player": dict(row)}

            clash = self._db.execute(
                "SELECT player_id FROM players WHERE name = ? COLLATE NOCASE LIMIT 1",
                (name,),
            ).fetchone()
            if clash is not None:
                return {"status": "name_taken"}

            player_id = self._new_player_id()
            while self._db.execute(
                "SELECT 1 FROM players WHERE player_id = ?", (player_id,)
            ).fetchone():
                player_id = self._new_player_id()
            token = self._new_token()
            self._db.execute(
                """
                INSERT INTO players(
                    player_id, name, token, chat_umo, chat_uid, chat_name,
                    group_name, created, updated
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    player_id,
                    name,
                    token,
                    umo,
                    uid,
                    clean_name(chat_name, ""),
                    clean_name(group_name, ""),
                    now,
                    now,
                ),
            )
            self._db.commit()
            row = self._db.execute(
                "SELECT * FROM players WHERE player_id = ?", (player_id,)
            ).fetchone()
        return {"status": "ok", "player": dict(row)}

    def ensure_token(self, player_id: str) -> str:
        now = time.time()
        with self._lock:
            row = self._db.execute(
                "SELECT token FROM players WHERE player_id = ?", (player_id,)
            ).fetchone()
            if row is None:
                return ""
            token = str(row["token"] or "")
            if token:
                return token
            token = self._new_token()
            self._db.execute(
                "UPDATE players SET token = ?, updated = ? WHERE player_id = ?",
                (token, now, player_id),
            )
            self._db.commit()
        return token

    def player_by_token(self, token: str) -> Optional[Dict[str, Any]]:
        token = (token or "").strip()
        if not TOKEN_RE.match(token):
            return None
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM players WHERE token = ?", (token,)
            ).fetchone()
        return dict(row) if row else None

    def player_by_chat(self, umo: str, uid: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM players WHERE chat_umo = ? AND chat_uid = ?",
                (umo, uid),
            ).fetchone()
        return dict(row) if row else None

    # ---------- 一次性门票 / 迁移码 ----------

    def issue_ticket(
        self,
        player_id: str,
        session: str = "",
        ttl: float = 600.0,
        kind: str = "login",
    ) -> str:
        """发一张入场券。login=群里点链接用，claim=换平台认领旧档案用。"""
        now = time.time()
        with self._lock:
            self._db.execute("DELETE FROM tickets WHERE expires < ?", (now - 86400,))
            for _ in range(8):
                if kind == "claim":
                    ticket = "".join(
                        secrets.choice("23456789ABCDEFGHJKLMNPQRSTUVWXYZ")
                        for _ in range(6)
                    )
                else:
                    ticket = secrets.token_urlsafe(24)
                try:
                    self._db.execute(
                        "INSERT INTO tickets(ticket, player_id, kind, session, "
                        "created, expires, used) VALUES(?, ?, ?, ?, ?, ?, 0)",
                        (ticket, player_id, kind, session, now, now + max(60.0, ttl)),
                    )
                    self._db.commit()
                    return ticket
                except sqlite3.IntegrityError:
                    continue
        return ""

    def redeem_ticket(self, ticket: str) -> Optional[Dict[str, Any]]:
        """核销入场券：只能用一次，过期作废。返回对应玩家（附带所属会话）。"""
        ticket = (ticket or "").strip()
        if not TICKET_RE.match(ticket):
            return None
        now = time.time()
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM tickets WHERE ticket = ? AND kind = 'login'",
                (ticket,),
            ).fetchone()
            if row is None or int(row["used"]) or float(row["expires"]) < now:
                return None
            self._db.execute(
                "UPDATE tickets SET used = 1 WHERE ticket = ?", (ticket,)
            )
            self._db.commit()
            player = self._db.execute(
                "SELECT * FROM players WHERE player_id = ?", (row["player_id"],)
            ).fetchone()
        if player is None:
            return None
        out = dict(player)
        out["_session"] = str(row["session"] or "")
        return out

    def issue_claim_code(self, player_id: str) -> str:
        return self.issue_ticket(player_id, ttl=600.0, kind="claim")

    def redeem_claim(
        self,
        code: str,
        *,
        umo: str,
        uid: str,
        chat_name: str = "",
        group_name: str = "",
    ) -> Dict[str, Any]:
        """把旧档案迁到当前会话。码只有本人网页里能生成，别人说不出来。"""
        code = (code or "").strip().upper()
        if not CLAIM_RE.match(code):
            return {"status": "bad_code"}
        now = time.time()
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM tickets WHERE ticket = ? AND kind = 'claim'",
                (code,),
            ).fetchone()
            if row is None or int(row["used"]) or float(row["expires"]) < now:
                return {"status": "expired"}
            player_id = str(row["player_id"])
            if umo and uid:
                other = self._db.execute(
                    "SELECT player_id FROM players WHERE chat_umo = ? AND chat_uid = ?",
                    (umo, uid),
                ).fetchone()
                if other is not None and str(other["player_id"]) != player_id:
                    return {"status": "taken"}
            self._db.execute("UPDATE tickets SET used = 1 WHERE ticket = ?", (code,))
            self._db.execute(
                """
                UPDATE players SET chat_umo = ?, chat_uid = ?, chat_name = ?,
                    group_name = ?, updated = ?
                WHERE player_id = ?
                """,
                (
                    umo,
                    uid,
                    clean_name(chat_name, ""),
                    clean_name(group_name, ""),
                    now,
                    player_id,
                ),
            )
            self._db.commit()
            player = self._db.execute(
                "SELECT * FROM players WHERE player_id = ?", (player_id,)
            ).fetchone()
        return {"status": "ok", "player": dict(player) if player else {}}

    # ---------- 写入 ----------

    def submit(
        self,
        *,
        player_id: str,
        name: str,
        score: int,
        level: int,
        merges: int,
        duration: int,
        session: str,
    ) -> Dict[str, Any]:
        now = time.time()
        with self._lock:
            cur = self._db.execute(
                """
                INSERT INTO runs(player_id, name, score, level, merges, duration, session, at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (player_id, name, score, level, merges, duration, session, now),
            )
            run_id = int(cur.lastrowid or 0)

            row = self._db.execute(
                "SELECT best, best_level, plays, chat_umo FROM players WHERE player_id = ?",
                (player_id,),
            ).fetchone()
            old_best = int(row["best"]) if row else 0
            old_level = int(row["best_level"]) if row else 0

            if row:
                if str(row["chat_umo"] or ""):
                    # 注册玩家：名字归注册名，网页昵称不许改
                    self._db.execute(
                        """
                        UPDATE players SET
                            best = MAX(best, ?),
                            best_level = MAX(best_level, ?),
                            plays = plays + 1,
                            total_score = total_score + ?,
                            best_merges = MAX(best_merges, ?),
                            updated = ?
                        WHERE player_id = ?
                        """,
                        (score, level, score, merges, now, player_id),
                    )
                else:
                    # 匿名网页玩家：允许随昵称走
                    self._db.execute(
                        """
                        UPDATE players SET
                            name = CASE WHEN ? != '' THEN ? ELSE name END,
                            best = MAX(best, ?),
                            best_level = MAX(best_level, ?),
                            plays = plays + 1,
                            total_score = total_score + ?,
                            best_merges = MAX(best_merges, ?),
                            updated = ?
                        WHERE player_id = ?
                        """,
                        (name, name, score, level, score, merges, now, player_id),
                    )
            else:
                self._db.execute(
                    """
                    INSERT INTO players(
                        player_id, name, best, best_level, plays, total_score,
                        best_merges, created, updated
                    ) VALUES(?, ?, ?, ?, 1, ?, ?, ?, ?)
                    """,
                    (player_id, name, score, level, score, merges, now, now),
                )

            # 每个人只留最近 N 局，别把库喂太饱
            self._db.execute(
                """
                DELETE FROM runs WHERE player_id = ? AND id NOT IN (
                    SELECT id FROM runs WHERE player_id = ? ORDER BY id DESC LIMIT ?
                )
                """,
                (player_id, player_id, self.keep_runs),
            )

            best = max(old_best, score)
            rank = 1 + int(
                self._db.execute(
                    "SELECT COUNT(*) AS c FROM players WHERE best > ?", (score,)
                ).fetchone()["c"]
            )
            best_rank = 1 + int(
                self._db.execute(
                    "SELECT COUNT(*) AS c FROM players WHERE best > ?", (best,)
                ).fetchone()["c"]
            )
            total = int(
                self._db.execute(
                    "SELECT COUNT(*) AS c FROM players WHERE best > 0"
                ).fetchone()["c"]
            )
            self._db.commit()

        return {
            "run_id": run_id,
            "best": best,
            "old_best": old_best,
            "best_level": max(old_level, level),
            "new_best": score > old_best,
            "rank": rank,
            "best_rank": best_rank,
            "total": total,
            "plays": (int(row["plays"]) + 1) if row else 1,
        }

    # ---------- 查询 ----------

    def board(
        self, scope: str = "all", session: str = "", limit: int = 10
    ) -> Dict[str, Any]:
        limit = max(1, min(int(limit or 10), 50))
        with self._lock:
            if scope == "session" and session:
                rows = self._db.execute(
                    """
                    SELECT r.player_id AS player_id,
                           MAX(r.score) AS best,
                           MAX(r.level) AS best_level,
                           COUNT(*) AS plays,
                           COALESCE(
                               NULLIF(p.chat_name, ''),
                               (SELECT name FROM runs r2
                                 WHERE r2.player_id = r.player_id
                                 ORDER BY r2.id DESC LIMIT 1),
                               '无名猪农'
                           ) AS name
                    FROM runs r
                    LEFT JOIN players p ON p.player_id = r.player_id
                    WHERE r.session = ?
                    GROUP BY r.player_id
                    ORDER BY best DESC, plays ASC
                    LIMIT ?
                    """,
                    (session, limit),
                ).fetchall()
                totals = self._db.execute(
                    """
                    SELECT COUNT(DISTINCT player_id) AS players,
                           COUNT(*) AS runs,
                           COALESCE(MAX(score), 0) AS top_score
                    FROM runs WHERE session = ?
                    """,
                    (session,),
                ).fetchone()
            else:
                rows = self._db.execute(
                    """
                    SELECT player_id, best, best_level, plays,
                           COALESCE(NULLIF(chat_name, ''), name, '无名猪农') AS name
                    FROM players
                    WHERE best > 0
                    ORDER BY best DESC, updated ASC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
                totals = self._db.execute(
                    """
                    SELECT COUNT(*) AS players,
                           COALESCE(SUM(plays), 0) AS runs,
                           COALESCE(MAX(best), 0) AS top_score
                    FROM players WHERE best > 0
                    """
                ).fetchone()

        return {
            "scope": "session" if (scope == "session" and session) else "all",
            "rows": [dict(r) for r in rows],
            "players": int(totals["players"] or 0),
            "runs": int(totals["runs"] or 0),
            "top_score": int(totals["top_score"] or 0),
        }

    def player(self, player_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM players WHERE player_id = ?", (player_id,)
            ).fetchone()
            if row is None:
                return None
            recent = self._db.execute(
                """
                SELECT score, level, merges, duration, at
                FROM runs WHERE player_id = ? ORDER BY id DESC LIMIT 5
                """,
                (player_id,),
            ).fetchall()
            rank = 1 + int(
                self._db.execute(
                    "SELECT COUNT(*) AS c FROM players WHERE best > ?",
                    (int(row["best"]),),
                ).fetchone()["c"]
            )
            total = int(
                self._db.execute(
                    "SELECT COUNT(*) AS c FROM players WHERE best > 0"
                ).fetchone()["c"]
            )
        data = dict(row)
        data["rank"] = rank
        data["total"] = total
        data["recent"] = [dict(r) for r in recent]
        data["chat_name"] = data.get("chat_name") or ""
        return data

    # ---------- 管理 ----------

    def overview(self, recent: int = 8, top: int = 5) -> Dict[str, Any]:
        day_start = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
        with self._lock:
            players = int(
                self._db.execute("SELECT COUNT(*) AS c FROM players").fetchone()["c"]
            )
            bound = int(
                self._db.execute(
                    "SELECT COUNT(*) AS c FROM players WHERE chat_umo != ''"
                ).fetchone()["c"]
            )
            runs = int(
                self._db.execute("SELECT COUNT(*) AS c FROM runs").fetchone()["c"]
            )
            today = int(
                self._db.execute(
                    "SELECT COUNT(*) AS c FROM runs WHERE at >= ?", (day_start,)
                ).fetchone()["c"]
            )
            top_score = int(
                self._db.execute(
                    "SELECT COALESCE(MAX(best), 0) AS c FROM players"
                ).fetchone()["c"]
            )
            recent_rows = self._db.execute(
                """
                SELECT r.id, r.player_id, r.name, r.score, r.level, r.merges,
                       r.duration, r.session, r.at,
                       COALESCE(NULLIF(p.chat_name, ''), r.name) AS display_name
                FROM runs r LEFT JOIN players p ON p.player_id = r.player_id
                ORDER BY r.id DESC LIMIT ?
                """,
                (max(1, min(int(recent), 50)),),
            ).fetchall()
            top_rows = self._db.execute(
                """
                SELECT player_id, name, best, best_level, plays,
                       chat_umo, chat_name, group_name, created, updated
                FROM players WHERE best > 0
                ORDER BY best DESC, updated ASC LIMIT ?
                """,
                (max(1, min(int(top), 20)),),
            ).fetchall()
        return {
            "stats": {
                "players": players,
                "bound": bound,
                "runs": runs,
                "today_runs": today,
                "top_score": top_score,
            },
            "recent": [dict(r) for r in recent_rows],
            "top": [dict(r) for r in top_rows],
        }

    def list_players(
        self, query: str = "", limit: int = 50, offset: int = 0
    ) -> Dict[str, Any]:
        limit = max(1, min(int(limit or 50), 200))
        offset = max(0, int(offset or 0))
        query = clean_name(query, "").strip()
        with self._lock:
            if query:
                like = f"%{query}%"
                where = (
                    "WHERE name LIKE ? OR chat_name LIKE ? OR chat_umo LIKE ?"
                    " OR group_name LIKE ?"
                )
                args: Tuple[Any, ...] = (like, like, like, like)
                total = int(
                    self._db.execute(
                        f"SELECT COUNT(*) AS c FROM players {where}", args
                    ).fetchone()["c"]
                )
                rows = self._db.execute(
                    f"""
                    SELECT player_id, name, best, best_level, plays, total_score,
                           chat_umo, chat_uid, chat_name, group_name, created, updated
                    FROM players {where}
                    ORDER BY best DESC, updated DESC LIMIT ? OFFSET ?
                    """,
                    (*args, limit, offset),
                ).fetchall()
            else:
                total = int(
                    self._db.execute(
                        "SELECT COUNT(*) AS c FROM players"
                    ).fetchone()["c"]
                )
                rows = self._db.execute(
                    """
                    SELECT player_id, name, best, best_level, plays, total_score,
                           chat_umo, chat_uid, chat_name, group_name, created, updated
                    FROM players
                    ORDER BY best DESC, updated DESC LIMIT ? OFFSET ?
                    """,
                    (limit, offset),
                ).fetchall()
        return {"total": total, "rows": [dict(r) for r in rows]}

    def list_runs(self, limit: int = 50) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit or 50), 200))
        with self._lock:
            rows = self._db.execute(
                """
                SELECT r.id, r.player_id, r.name, r.score, r.level, r.merges,
                       r.duration, r.session, r.at,
                       COALESCE(NULLIF(p.chat_name, ''), r.name) AS display_name
                FROM runs r LEFT JOIN players p ON p.player_id = r.player_id
                ORDER BY r.id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_player(self, player_id: str) -> bool:
        with self._lock:
            cur = self._db.execute(
                "DELETE FROM players WHERE player_id = ?", (player_id,)
            )
            self._db.execute("DELETE FROM runs WHERE player_id = ?", (player_id,))
            self._db.commit()
        return bool(cur.rowcount)

    def reset_player(self, player_id: str) -> bool:
        with self._lock:
            row = self._db.execute(
                "SELECT 1 FROM players WHERE player_id = ?", (player_id,)
            ).fetchone()
            if row is None:
                return False
            self._db.execute("DELETE FROM runs WHERE player_id = ?", (player_id,))
            self._db.execute(
                """
                UPDATE players SET best = 0, best_level = 0, plays = 0,
                    total_score = 0, best_merges = 0, updated = ?
                WHERE player_id = ?
                """,
                (time.time(), player_id),
            )
            self._db.commit()
        return True


# ==================== 游戏网页服务 ====================


class PigWebServer:
    """插件自带的小 HTTP 服务：吐游戏页面 + 分数接口。"""

    def __init__(self, plugin: "PigMergeWebPlugin"):
        self.plugin = plugin
        self.host = plugin.host
        self.port = plugin.port
        self.actual_port = 0
        self.running = False
        self.last_error = ""
        self.root = (Path(__file__).parent / "web").resolve()
        self._runner = None

    # ---------- 生命周期 ----------

    def candidate_ports(self) -> List[int]:
        start = min(max(int(self.plugin.port or PORT_MIN), PORT_MIN), PORT_MAX)
        return list(range(start, PORT_MAX + 1)) + list(range(PORT_MIN, start))

    async def start(self) -> bool:
        if aioweb is None:
            self.last_error = "没找到 aiohttp，网页端起不来"
            logger.error("[合成大猪猪] %s。装一下：pip install aiohttp", self.last_error)
            return False
        if not self.root.is_dir():
            self.last_error = f"缺少网页资源目录：{self.root}"
            logger.error("[合成大猪猪] %s", self.last_error)
            return False

        app = aioweb.Application(client_max_size=128 * 1024)
        app.router.add_get("/api/health", self.handle_health)
        app.router.add_get("/api/config", self.handle_config)
        app.router.add_get("/api/session", self.handle_session)
        app.router.add_post("/api/register", self.handle_register)
        app.router.add_post("/api/login", self.handle_login)
        app.router.add_post("/api/setpass", self.handle_setpass)
        app.router.add_get("/api/identity", self.handle_identity)
        app.router.add_get("/api/records", self.handle_records_get)
        app.router.add_post("/api/records", self.handle_records_post)
        app.router.add_get("/api/players/{player_id}", self.handle_player)
        app.router.add_post("/api/claim", self.handle_claim_code)
        app.router.add_get("/favicon.ico", self.handle_favicon)
        app.router.add_get("/{path:.*}", self.handle_static)

        runner = aioweb.AppRunner(app, access_log=None)
        await runner.setup()

        errors: List[str] = []
        for port in self.candidate_ports():
            site = aioweb.TCPSite(runner, self.host, port)
            try:
                await site.start()
            except OSError as exc:
                errors.append(f"{port}: {exc}")
                continue
            except Exception as exc:  # 地址写错之类的，别硬扛
                errors.append(f"{port}: {exc}")
                break
            self.actual_port = port
            self.running = True
            self.last_error = ""
            self._runner = runner
            if port != self.plugin.port:
                logger.warning(
                    "[合成大猪猪] 配置端口 %s 被占了，自动换成 %s。",
                    self.plugin.port,
                    port,
                )
            logger.info(
                "[合成大猪猪] 游戏页已启动：http://%s:%s/（可在管理页填对外地址）",
                self.host,
                port,
            )
            return True

        self.running = False
        self.last_error = "；".join(errors[-3:]) or "端口都被占完了"
        logger.error(
            "[合成大猪猪] %s~%s 都没抢到端口（%s）。",
            PORT_MIN,
            PORT_MAX,
            self.last_error,
        )
        try:
            await runner.cleanup()
        except Exception:
            pass
        return False

    async def stop(self) -> None:
        self.running = False
        self.actual_port = 0
        if self._runner is not None:
            try:
                await self._runner.cleanup()
            except Exception as exc:  # pragma: no cover
                logger.warning("[合成大猪猪] 网页端关闭时有点小状况：%s", exc)
            self._runner = None

    # ---------- 静态资源 ----------

    def _file_response(self, target: Path) -> "aioweb.Response":
        suffix = target.suffix.lower()
        ctype = MIME_TYPES.get(suffix, "application/octet-stream")
        body = target.read_bytes()
        return aioweb.Response(
            body=body,
            content_type=ctype.split(";")[0],
            charset="utf-8" if "charset=utf-8" in ctype else None,
            headers={
                "Cache-Control": "no-cache",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
            },
        )

    async def handle_static(self, request: "aioweb.Request") -> "aioweb.StreamResponse":
        rel = (request.match_info.get("path") or "").strip("/")
        if not rel:
            rel = "index.html"
        try:
            target = (self.root / rel).resolve()
        except Exception:
            raise aioweb.HTTPNotFound()
        if self.root not in target.parents and target != self.root:
            raise aioweb.HTTPNotFound()
        if not target.is_file():
            raise aioweb.HTTPNotFound()
        return await asyncio.to_thread(self._file_response, target)

    async def handle_favicon(self, request: "aioweb.Request") -> "aioweb.StreamResponse":
        icon = self.root / "favicon.svg"
        if icon.is_file():
            return await asyncio.to_thread(self._file_response, icon)
        return aioweb.Response(status=204)

    # ---------- 接口 ----------

    async def handle_health(self, request: "aioweb.Request") -> "aioweb.StreamResponse":
        return aioweb.json_response(
            {
                "ok": True,
                "plugin": PLUGIN_NAME,
                "version": PLUGIN_VERSION,
                "levels": MAX_LEVEL,
                "port": self.actual_port,
                "time": time.time(),
            }
        )

    async def handle_config(self, request: "aioweb.Request") -> "aioweb.StreamResponse":
        return aioweb.json_response(
            {
                "ok": True,
                "version": PLUGIN_VERSION,
                "default_theme": self.plugin.default_theme,
                "bg_image": getattr(self.plugin, "bg_image", ""),
                "max_level": MAX_LEVEL,
                "session_scope": True,
            }
        )

    async def handle_session(self, request: "aioweb.Request") -> "aioweb.StreamResponse":
        store: Optional[ScoreStore] = self.plugin.store
        if store is None:
            return aioweb.json_response(
                {"ok": False, "message": "还没准备好"}, status=503
            )
        ticket = (request.query.get("k") or "").strip()
        token = (request.query.get("t") or "").strip()
        session = ""
        if ticket:
            # 一次性门票：核销成功才知道是谁，顺便把所属群带回来
            player = await asyncio.to_thread(store.redeem_ticket, ticket)
            if not player:
                return aioweb.json_response(
                    {"ok": False, "message": "这个链接已经用过或者过期了，回群里 @我 重新要一个"},
                    status=410,
                )
            session = str(player.pop("_session", "") or "")
            token = str(player.get("token") or "") or await asyncio.to_thread(
                store.ensure_token, str(player.get("player_id") or "")
            )
        else:
            if not token:
                return aioweb.json_response({"ok": False, "message": "没有凭证"}, status=400)
            player = await asyncio.to_thread(store.player_by_token, token)
            if not player:
                return aioweb.json_response(
                    {"ok": False, "message": "这个链接不认识了，回群里重新要一次吧"},
                    status=404,
                )
            chat_umo = str(player.get("chat_umo") or "")
            if chat_umo:
                session = encode_session(chat_umo)
        stat = await asyncio.to_thread(store.player, player["player_id"])
        stat = stat or player
        return aioweb.json_response(
            {
                "ok": True,
                "token": token,
                "session": session,
                "player": {
                    "player_id": stat.get("player_id"),
                    "name": stat.get("name") or "无名猪农",
                    "chat_name": stat.get("chat_name") or "",
                    "group_name": stat.get("group_name") or "",
                    "best": int(stat.get("best") or 0),
                    "best_level": int(stat.get("best_level") or 0),
                    "plays": int(stat.get("plays") or 0),
                    "rank": int(stat.get("rank") or 0),
                    "total": int(stat.get("total") or 0),
                    "bound": bool(stat.get("chat_umo") and stat.get("chat_uid")),
                    "has_pass": bool(stat.get("pass_hash")),
                },
            }
        )

    async def handle_records_get(
        self, request: "aioweb.Request"
    ) -> "aioweb.StreamResponse":
        store: Optional[ScoreStore] = self.plugin.store
        if store is None:
            return aioweb.json_response(
                {"ok": False, "message": "还没准备好"}, status=503
            )
        scope = (request.query.get("scope") or "all").strip().lower()
        session = decode_session(request.query.get("s") or "")
        try:
            limit = int(request.query.get("limit") or 10)
        except Exception:
            limit = 10
        try:
            data = await asyncio.to_thread(store.board, scope, session, limit)
        except Exception as exc:
            logger.warning("[合成大猪猪] 读榜失败：%s", exc)
            return aioweb.json_response(
                {"ok": False, "message": "读榜失败了"}, status=500
            )
        return aioweb.json_response({"ok": True, **data})

    async def handle_records_post(
        self, request: "aioweb.Request"
    ) -> "aioweb.StreamResponse":
        store: Optional[ScoreStore] = self.plugin.store
        if store is None:
            return aioweb.json_response(
                {"ok": False, "message": "还没准备好"}, status=503
            )

        try:
            payload = await request.json()
        except Exception:
            return aioweb.json_response(
                {"ok": False, "message": "请求不是 JSON"}, status=400
            )
        if not isinstance(payload, dict):
            return aioweb.json_response(
                {"ok": False, "message": "请求格式不对"}, status=400
            )

        player_id = str(payload.get("player_id") or "").strip()
        name = clean_name(payload.get("name"), "")
        token = str(payload.get("t") or "").strip()

        # 带 token 的以服务端档案为准，防止有人拿别人的 player_id 交分
        if token:
            owner = await asyncio.to_thread(store.player_by_token, token)
            if not owner:
                return aioweb.json_response(
                    {"ok": False, "message": "凭证过期了，回群里重新要一次链接"},
                    status=403,
                )
            player_id = str(owner["player_id"])
            name = str(owner.get("name") or name or "无名猪农")

        if not PLAYER_ID_RE.match(player_id):
            return aioweb.json_response(
                {"ok": False, "message": "player_id 不合法"}, status=400
            )

        try:
            score = int(payload.get("score") or 0)
            level = int(payload.get("level") or 0)
            merges = int(payload.get("merges") or 0)
            duration = int(payload.get("duration") or 0)
        except Exception:
            return aioweb.json_response(
                {"ok": False, "message": "分数格式不对"}, status=400
            )
        if not 0 <= score <= MAX_SCORE:
            return aioweb.json_response(
                {"ok": False, "message": "这个分数有点离谱"}, status=400
            )
        level = max(0, min(level, MAX_LEVEL))
        merges = max(0, min(merges, 9999))
        duration = max(0, min(duration, 86400))

        now = time.time()
        last = self.plugin.last_submit.get(player_id, 0.0)
        if self.plugin.submit_interval > 0 and now - last < self.plugin.submit_interval:
            return aioweb.json_response(
                {"ok": False, "message": "交得太快了，慢一点"}, status=429
            )
        self.plugin.last_submit[player_id] = now

        session = decode_session(request.query.get("s") or "") or decode_session(
            str(payload.get("session") or "")
        )

        try:
            result = await asyncio.to_thread(
                store.submit,
                player_id=player_id,
                name=name,
                score=score,
                level=level,
                merges=merges,
                duration=duration,
                session=session,
            )
        except Exception as exc:
            logger.warning("[合成大猪猪] 记分失败：%s", exc)
            return aioweb.json_response(
                {"ok": False, "message": "分数没记上，等会儿再试"}, status=500
            )
        return aioweb.json_response({"ok": True, **result})

    # ---------- 网页端注册 / 登录 ----------

    AUTH_WINDOW = 60.0
    AUTH_LIMIT = 6

    def _auth_gate(self, key: str) -> bool:
        """同一名字一分钟内最多试 6 次，防止拿字典刷口令。"""
        now = time.time()
        bucket = self.plugin.auth_tries.setdefault(key, [])
        while bucket and now - bucket[0] > self.AUTH_WINDOW:
            bucket.pop(0)
        return len(bucket) < self.AUTH_LIMIT

    def _auth_hit(self, key: str) -> None:
        self.plugin.auth_tries.setdefault(key, []).append(time.time())

    @staticmethod
    def _player_payload(stat: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "player_id": stat.get("player_id"),
            "name": stat.get("name") or "无名猪农",
            "chat_name": stat.get("chat_name") or "",
            "group_name": stat.get("group_name") or "",
            "best": int(stat.get("best") or 0),
            "best_level": int(stat.get("best_level") or 0),
            "plays": int(stat.get("plays") or 0),
            "rank": int(stat.get("rank") or 0),
            "total": int(stat.get("total") or 0),
            "bound": bool(stat.get("chat_umo") and stat.get("chat_uid")),
            "has_pass": bool(stat.get("pass_hash")),
        }

    async def _read_json(self, request: "aioweb.Request") -> Dict[str, Any]:
        try:
            payload = await request.json()
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    async def handle_register(
        self, request: "aioweb.Request"
    ) -> "aioweb.StreamResponse":
        """网页自助注册：昵称 + 口令，建好档案直接发长期凭证。"""
        store: Optional[ScoreStore] = self.plugin.store
        if store is None:
            return aioweb.json_response(
                {"ok": False, "message": "还没准备好"}, status=503
            )
        payload = await self._read_json(request)
        name = clean_player_name(payload.get("name"))
        password = str(payload.get("pass") or "")
        # 从群里链接点进来会带上会话，注册时顺手挂上
        umo = decode_session(str(payload.get("s") or request.query.get("s") or ""))

        if not name or not NAME_RE.match(name):
            return aioweb.json_response(
                {"ok": False, "message": "名字 2~12 个字，中英文数字都行，别带空格"},
                status=400,
            )
        if not PASSWORD_RE.match(password):
            return aioweb.json_response(
                {"ok": False, "message": "口令 4~32 位，字母数字符号都行，别带空格"},
                status=400,
            )

        key = "r:" + name.upper()
        if not self._auth_gate(key):
            return aioweb.json_response(
                {"ok": False, "message": "试得太勤了，歇一分钟再来"}, status=429
            )
        self._auth_hit(key)

        try:
            result = await asyncio.to_thread(
                store.register_web, name=name, password=password, umo=umo
            )
        except Exception as exc:
            logger.warning("[合成大猪猪] 网页注册失败：%s", exc)
            return aioweb.json_response(
                {"ok": False, "message": "注册没成，等会儿再试"}, status=500
            )

        status = str(result.get("status") or "")
        if status == "name_taken":
            return aioweb.json_response(
                {"ok": False, "message": f"「{name}」已经有人用了，换个名字，或者直接登录"},
                status=409,
            )
        if status != "ok":
            return aioweb.json_response(
                {"ok": False, "message": "这个名字或口令不合规"}, status=400
            )

        player = result.get("player") or {}
        player_id = str(player.get("player_id") or "")
        stat = await asyncio.to_thread(store.player, player_id) or player
        session = encode_session(umo) if umo else ""
        return aioweb.json_response(
            {
                "ok": True,
                "token": str(player.get("token") or ""),
                "session": session,
                "player": self._player_payload(stat),
            }
        )

    async def handle_login(
        self, request: "aioweb.Request"
    ) -> "aioweb.StreamResponse":
        """换浏览器 / 换设备回来：昵称 + 口令换长期凭证。"""
        store: Optional[ScoreStore] = self.plugin.store
        if store is None:
            return aioweb.json_response(
                {"ok": False, "message": "还没准备好"}, status=503
            )
        payload = await self._read_json(request)
        name = clean_player_name(payload.get("name"))
        password = str(payload.get("pass") or "")
        if not name or not password:
            return aioweb.json_response(
                {"ok": False, "message": "名字和口令都得填"}, status=400
            )

        key = "l:" + name.upper()
        if not self._auth_gate(key):
            return aioweb.json_response(
                {"ok": False, "message": "输错太多次了，歇一分钟再来"}, status=429
            )

        try:
            result = await asyncio.to_thread(
                store.login_web, name=name, password=password
            )
        except Exception as exc:
            logger.warning("[合成大猪猪] 登录失败：%s", exc)
            return aioweb.json_response(
                {"ok": False, "message": "登录没成，等会儿再试"}, status=500
            )

        status = str(result.get("status") or "")
        if status == "no_user":
            self._auth_hit(key)
            return aioweb.json_response(
                {"ok": False, "message": "没有这个猪农，先注册一个吧"}, status=404
            )
        if status == "no_pass":
            return aioweb.json_response(
                {
                    "ok": False,
                    "message": "这份档案还没设口令。回群里 @我 发「猪猪」，"
                    "点新链接进来，页面会弹出来让你补一个。",
                },
                status=403,
            )
        if status == "bad_pass":
            self._auth_hit(key)
            return aioweb.json_response(
                {"ok": False, "message": "口令不对"}, status=403
            )
        if status != "ok":
            return aioweb.json_response(
                {"ok": False, "message": "登录没成，等会儿再试"}, status=400
            )

        player = result.get("player") or {}
        umo = str(player.get("chat_umo") or "")
        return aioweb.json_response(
            {
                "ok": True,
                "token": str(player.get("token") or ""),
                "session": encode_session(umo) if umo else "",
                "player": self._player_payload(player),
            }
        )

    async def handle_setpass(
        self, request: "aioweb.Request"
    ) -> "aioweb.StreamResponse":
        """已登录（拿着有效凭证）的人补设 / 修改口令。

        群里注册的老玩家档案里没有口令，只能靠这条路口补上，
        否则换个浏览器就永久进不来了。
        """
        store: Optional[ScoreStore] = self.plugin.store
        if store is None:
            return aioweb.json_response(
                {"ok": False, "message": "还没准备好"}, status=503
            )
        payload = await self._read_json(request)
        token = str(payload.get("t") or request.query.get("t") or "").strip()
        password = str(payload.get("pass") or "")
        old = str(payload.get("old") or "")

        if not PASSWORD_RE.match(password):
            return aioweb.json_response(
                {"ok": False, "message": "口令 4~32 位，字母数字符号都行，别带空格"},
                status=400,
            )

        player = await asyncio.to_thread(store.player_by_token, token)
        if not player:
            return aioweb.json_response(
                {"ok": False, "message": "凭证失效了，回群里 @我 重新要个链接"},
                status=403,
            )
        player_id = str(player.get("player_id") or "")

        key = "s:" + player_id
        if not self._auth_gate(key):
            return aioweb.json_response(
                {"ok": False, "message": "试得太勤了，歇一分钟再来"}, status=429
            )

        # 已经有口令的，改之前得知道旧口令
        if str(player.get("pass_hash") or ""):
            if not old:
                return aioweb.json_response(
                    {"ok": False, "message": "要改口令得先填原口令", "need_old": True},
                    status=400,
                )
            if not await asyncio.to_thread(store.check_password, player, old):
                self._auth_hit(key)
                return aioweb.json_response(
                    {"ok": False, "message": "原口令不对", "need_old": True}, status=403
                )

        try:
            ok = await asyncio.to_thread(store.set_password, player_id, password)
        except Exception as exc:
            logger.warning("[合成大猪猪] 设口令失败：%s", exc)
            return aioweb.json_response(
                {"ok": False, "message": "口令没存上，等会儿再试"}, status=500
            )
        if not ok:
            return aioweb.json_response(
                {"ok": False, "message": "档案没找到"}, status=404
            )

        stat = await asyncio.to_thread(store.player, player_id) or player
        logger.info("[合成大猪猪] 档案 %s 已设好网页口令", player_id)
        return aioweb.json_response(
            {"ok": True, "player": self._player_payload(stat)}
        )

    async def handle_identity(
        self, request: "aioweb.Request"
    ) -> "aioweb.StreamResponse":
        """本地存的凭证还活着吗？活着就顺手把档案带回去。"""
        store: Optional[ScoreStore] = self.plugin.store
        if store is None:
            return aioweb.json_response(
                {"ok": False, "message": "还没准备好"}, status=503
            )
        token = (request.query.get("t") or "").strip()
        if not token:
            return aioweb.json_response(
                {"ok": False, "message": "没有凭证"}, status=400
            )
        player = await asyncio.to_thread(store.player_by_token, token)
        if not player:
            return aioweb.json_response(
                {"ok": False, "message": "凭证失效了"}, status=404
            )
        stat = await asyncio.to_thread(store.player, player["player_id"]) or player
        umo = str(player.get("chat_umo") or "")
        return aioweb.json_response(
            {
                "ok": True,
                "token": token,
                "session": encode_session(umo) if umo else "",
                "player": self._player_payload(stat),
            }
        )

    async def handle_claim_code(
        self, request: "aioweb.Request"
    ) -> "aioweb.StreamResponse":
        """给本人发一个 6 位迁移码，用来换设备 / 换平台认领旧档案。"""
        store: Optional[ScoreStore] = self.plugin.store
        if store is None:
            return aioweb.json_response(
                {"ok": False, "message": "还没准备好"}, status=503
            )
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        token = str((payload or {}).get("t") or request.query.get("t") or "").strip()
        ticket = str((payload or {}).get("k") or "").strip()
        player: Optional[Dict[str, Any]] = None
        if token:
            player = await asyncio.to_thread(store.player_by_token, token)
        elif ticket:
            player = await asyncio.to_thread(store.redeem_ticket, ticket)
        if not player:
            return aioweb.json_response(
                {"ok": False, "message": "凭证不对，回群里重新要个链接"}, status=403
            )
        code = await asyncio.to_thread(store.issue_claim_code, player["player_id"])
        if not code:
            return aioweb.json_response(
                {"ok": False, "message": "码没生成出来，等会儿再试"}, status=500
            )
        return aioweb.json_response(
            {
                "ok": True,
                "code": code,
                "expires_in": 600,
                "how": "在新平台/新设备上 @机器人 发「猪猪认领 %s」" % code,
            }
        )

    async def handle_player(self, request: "aioweb.Request") -> "aioweb.StreamResponse":
        store: Optional[ScoreStore] = self.plugin.store
        if store is None:
            return aioweb.json_response(
                {"ok": False, "message": "还没准备好"}, status=503
            )
        player_id = (request.match_info.get("player_id") or "").strip()
        if not PLAYER_ID_RE.match(player_id):
            return aioweb.json_response(
                {"ok": False, "message": "player_id 不合法"}, status=400
            )
        data = await asyncio.to_thread(store.player, player_id)
        if not data:
            # 游客用的是本机随机 id，服务端本来就没有这条；返回空档案而不是 404，
            # 免得浏览器控制台每开一次页面就红一条
            return aioweb.json_response({"ok": True, "player": None})
        data.pop("token", None)
        return aioweb.json_response({"ok": True, "player": data})


# ==================== 插件主体 ====================


class PigMergeWebPlugin(Star):
    def __init__(self, context: Context, config: Any = None):
        super().__init__(context)
        self.config = config if config is not None else {}
        self.store: Optional[ScoreStore] = None
        self.web: Optional[PigWebServer] = None
        self.last_submit: Dict[str, float] = {}
        self.auth_tries: Dict[str, List[float]] = {}
        self.data_dir = Path(__file__).parent
        self._keep_days = 365
        self._load_runtime_config()
        self._register_admin_api()

    # ---------- 配置 ----------

    def _cfg(self, key: str, default: Any = None) -> Any:
        try:
            value = self.config.get(key, default)
        except Exception:
            return default
        return default if value is None else value

    def _load_runtime_config(self) -> None:
        self.host = str(self._cfg("host", "0.0.0.0") or "0.0.0.0").strip() or "0.0.0.0"
        try:
            port = int(self._cfg("port", PORT_MIN))
        except Exception:
            port = PORT_MIN
        self.port = min(max(port, PORT_MIN), PORT_MAX)

        self.public_base_url = (
            str(self._cfg("public_base_url", "") or "").strip().rstrip("/")
        )
        # 每次重读配置都重置，配好之后写错/清空还能再提醒一次
        self._warned_public_url = False
        self.markdown_mode = str(self._cfg("markdown_mode", "auto") or "auto")
        self.allow_private = bool(self._cfg("allow_private", True))
        try:
            self.board_size = max(3, min(int(self._cfg("board_size", 10)), 20))
        except Exception:
            self.board_size = 10
        self.default_theme = str(self._cfg("default_theme", "auto") or "auto")
        # 自定义背景图：只认 http(s) 直链和站内绝对路径，别的一律当没配
        raw_bg = str(self._cfg("bg_image", "") or "").strip()[:1024]
        self.bg_image = raw_bg if re.match(r"^(https?://|/)", raw_bg) else ""
        self.session_link = bool(self._cfg("session_link", True))
        try:
            ttl = int(self._cfg("link_ttl", 600))
        except Exception:
            ttl = 600
        self.link_ttl = min(max(ttl, 60), 86400)
        try:
            self.submit_interval = max(0.0, float(self._cfg("submit_interval", 1.5)))
        except Exception:
            self.submit_interval = 1.5
        try:
            self._keep_days = max(1, int(self._cfg("keep_days", 365)))
        except Exception:
            self._keep_days = 365

    def _base_url(self) -> str:
        if self.public_base_url:
            return self.public_base_url
        port = self.web.actual_port if (self.web and self.web.actual_port) else self.port
        url = f"http://{guess_lan_ip()}:{port}"
        if not self._warned_public_url:
            self._warned_public_url = True
            try:
                logger.warning(
                    "[合成大猪猪] 对外地址（public_base_url）没配，正在用本机地址拼链接：%s —— "
                    "如果插件跑在容器或内网机器上，发出去的链接在外部是打不开的。"
                    "请到插件设置里把「对外访问地址」填成外部可访问的地址，例如 https://pig.example.com",
                    url,
                )
            except Exception:
                pass
        return url

    def _service_info(self) -> Dict[str, Any]:
        web = self.web
        return {
            "running": bool(web and web.running),
            "host": self.host,
            "port": int(web.actual_port) if (web and web.actual_port) else 0,
            "configured_port": int(self.port),
            "port_range": [PORT_MIN, PORT_MAX],
            "url": self._base_url(),
            "url_from_config": bool(self.public_base_url),
            "url_warning": (
                "" if self.public_base_url
                else "对外地址没配，链接用的是内网 IP，外网可能打不开"
            ),
            "error": (web.last_error if web else ""),
        }

    # ---------- 生命周期 ----------

    async def initialize(self) -> None:
        try:
            self.data_dir = Path(StarTools.get_data_dir(PLUGIN_NAME))
        except Exception:
            self.data_dir = Path(__file__).parent / "data"
        try:
            self.store = await asyncio.to_thread(
                ScoreStore, self.data_dir / "scores.db", self._keep_days
            )
        except Exception as exc:
            logger.exception("[合成大猪猪] 数据库开不了：%s", exc)
            self.store = None

        self.web = PigWebServer(self)
        await self.web.start()

    async def terminate(self) -> None:
        if self.web is not None:
            await self.web.stop()
            self.web = None
        if self.store is not None:
            try:
                await asyncio.to_thread(self.store.close)
            except Exception as exc:  # pragma: no cover
                logger.warning("[合成大猪猪] 关库时有点小状况：%s", exc)
            self.store = None

    async def _restart_web(self) -> None:
        if self.web is not None:
            await self.web.stop()
        self.web = PigWebServer(self)
        await self.web.start()

    # ==================== 渲染 ====================

    def _use_markdown(self, event: AstrMessageEvent) -> bool:
        if self.markdown_mode == "always":
            return True
        if self.markdown_mode == "never":
            return False
        return event.get_platform_name() in MD_PLATFORMS

    def _crash_text(self, exc: BaseException) -> str:
        """任何意外都变成一句话回出去，绝不静默。"""
        logger.exception("[合成大猪猪] 处理消息出错：%s", exc)
        return (
            "🐖 **我这边卡了一下**\n"
            "> 刚才那步没做完，不是你的问题。\n"
            "再发一次试试；还不行就把时间点告诉我，我去翻日志。"
        )

    def _say(self, event: AstrMessageEvent, text: str):
        result = event.plain_result(text)
        result.use_t2i(False)
        if self._use_markdown(event):
            return result.use_markdown(True)
        result.use_markdown(False)
        if result.chain:
            result.chain[0].text = self._to_plain(text)
        return result

    _MD_LINE_PREFIX = re.compile(r"^\s{0,3}(#{1,6}\s*|>\s?|-{3,}\s*$)")

    def _to_plain(self, markdown: str) -> str:
        """把 Markdown 粗略降级成纯文本，给不支持 Markdown 的平台用。"""
        lines: List[str] = []
        for line in markdown.splitlines():
            line = self._MD_LINE_PREFIX.sub("", line)
            line = line.replace("**", "").replace("`", "").replace("~~", "")
            line = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 \2", line)
            lines.append(line.rstrip())
        return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()

    @staticmethod
    def _pig(level: int) -> str:
        icon, name = PIGS.get(int(level or 0), ("🐽", "猪鼻子"))
        return f"{icon} {name}"

    # ==================== 聊天触发 ====================

    def _uid(self, event: AstrMessageEvent) -> str:
        return str(event.get_sender_id() or "unknown")

    def _at_bot(self, event: AstrMessageEvent) -> bool:
        """群聊必须真的 @ 到机器人；私聊没有 @ 这回事，直接放行。"""
        messages = event.get_messages() or []
        self_id = str(event.get_self_id() or "")
        has_at = False
        for comp in messages:
            if not isinstance(comp, At):
                continue
            has_at = True
            qq = str(getattr(comp, "qq", "") or "")
            if qq and qq != "all" and (not self_id or qq == self_id):
                return True
        if not event.get_group_id() and not has_at:
            return True
        return False

    def _chat_label(self, event: AstrMessageEvent) -> str:
        group_id = event.get_group_id()
        if not group_id:
            return "私聊"
        group = getattr(event.message_obj, "group", None)
        group_name = clean_name(getattr(group, "group_name", "") or "", "")
        return f"群「{group_name}」" if group_name else f"群 {group_id}"

    # ==================== 聊天指令 ====================
    # 帮助插件（astrbot_plugin_help）只认 CommandFilter / CommandGroupFilter，
    # 所以这几个入口得挂成正规指令才能被收进帮助图；
    # 更宽松的写法（「🐖 游戏」「游戏🐖」之类）仍由下面的 on_message 兜底。

    @filter.command(
        "猪猪游戏",
        alias={
            "🐖游戏",
            "🐷游戏",
            "🐖 游戏",
            "🐷 游戏",
            "猪游戏",
            "合成大猪猪",
            "游戏🐖",
            "游戏🐷",
        },
    )
    async def cmd_game(self, event: AstrMessageEvent):
        """🐖 拿一条网页版「合成大猪猪」的入口链接"""
        if not self._at_bot(event):
            return
        event.stop_event()
        event.should_call_llm(False)
        try:
            text = await self._game_text(event)
        except Exception as exc:
            text = self._crash_text(exc)
        yield self._say(event, text)

    @filter.command("猪猪注册", alias={"注册猪猪", "猪注册", "pig注册"})
    async def cmd_register(self, event: AstrMessageEvent):
        """建猪农档案：猪猪注册 你的名字（2~12 字）"""
        raw = (event.message_str or "").strip()
        matched = REGISTER_RE.match(raw)
        name = matched.group("name") if matched else ""
        event.stop_event()
        event.should_call_llm(False)
        try:
            text = await self._register_text(event, name)
        except Exception as exc:
            text = self._crash_text(exc)
        yield self._say(event, text)

    @filter.command("猪猪认领", alias={"认领猪猪", "猪猪迁移", "猪认领"})
    async def cmd_claim(self, event: AstrMessageEvent):
        """换设备 / 换平台：猪猪认领 迁移码"""
        raw = (event.message_str or "").strip()
        matched = CLAIM_CMD_RE.match(raw)
        code = (matched.group("code") or "") if matched else ""
        event.stop_event()
        event.should_call_llm(False)
        try:
            text = await self._claim_text(event, code)
        except Exception as exc:
            text = self._crash_text(exc)
        yield self._say(event, text)

    @filter.command(
        "猪猪排行榜",
        alias={
            "猪猪排行",
            "猪猪榜",
            "猪榜",
            "🐖排行榜",
            "🐷排行榜",
            "猪宝排行榜",
            "pig排行榜",
        },
    )
    async def cmd_leaderboard(self, event: AstrMessageEvent):
        """🏆 看「合成大猪猪」的排行榜"""
        event.stop_event()
        event.should_call_llm(False)
        try:
            text = await self._leaderboard_text(event, "auto", 0)
        except Exception as exc:
            text = self._crash_text(exc)
        yield self._say(event, text)

    @filter.llm_tool(name="pig_merge_leaderboard")
    async def tool_leaderboard(
        self, event: AstrMessageEvent, scope: str = "auto", limit: int = 0
    ):
        """查看「合成大猪猪」网页小游戏的排行榜，并把榜单直接发到当前会话。

        用户想看看榜上谁分最高、自己排第几、群里谁最厉害的时候用这个工具。
        榜单是现成的成绩数据，不要自己编分数。

        Args:
            scope(string): 榜单范围。all 是全服总榜；session 只看当前群（私聊无效）；auto 在群里有成绩时先给本群榜，默认 auto
            limit(number): 想列前几名，1 到 20；填 0 或不填就用插件配置的人数
        """
        try:
            text = await self._leaderboard_text(event, scope, limit)
        except Exception as exc:
            text = self._crash_text(exc)
        yield self._say(event, text)
        event.stop_event()

    async def _game_text(self, event: AstrMessageEvent) -> str:
        if not self.allow_private and not event.get_group_id():
            return "入口链接只能在群里要，私聊里我不发。"
        if self.store is None:
            return "猪圈还没搭好，等插件加载完再叫我一声。"
        return await self._entry_text(event)

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        """@ 机器人 + 🐖游戏 -> 入口链接；猪猪注册 -> 建档案绑 UMO。"""
        text = (event.message_str or "").strip()
        if not text:
            return

        claim = CLAIM_CMD_RE.match(text)
        if claim:
            event.stop_event()
            event.should_call_llm(False)
            try:
                text = await self._claim_text(event, claim.group("code") or "")
            except Exception as exc:
                text = self._crash_text(exc)
            yield self._say(event, text)
            return

        if BOARD_CMD_RE.match(text) and (not event.get_group_id() or self._at_bot(event)):
            event.stop_event()
            event.should_call_llm(False)
            try:
                text = await self._leaderboard_text(event, "auto", 0)
            except Exception as exc:
                text = self._crash_text(exc)
            yield self._say(event, text)
            return

        if REGISTER_RE.match(text):
            name = REGISTER_RE.match(text).group("name")  # type: ignore[union-attr]
            if self.store is None:
                yield self._say(event, "猪圈还没搭好，等插件加载完再叫我一声。")
                return
            event.stop_event()
            event.should_call_llm(False)
            try:
                text = await self._register_text(event, name)
            except Exception as exc:
                text = self._crash_text(exc)
            yield self._say(event, text)
            return

        if not PIG_GAME_RE.search(text):
            return
        if not self._at_bot(event):
            return
        event.stop_event()
        event.should_call_llm(False)
        try:
            text = await self._game_text(event)
        except Exception as exc:
            text = self._crash_text(exc)
        yield self._say(event, text)

    async def _player_of_chat(
        self, umo: str, uid: str
    ) -> Optional[Dict[str, Any]]:
        if self.store is None:
            return None
        try:
            return await asyncio.to_thread(self.store.player_by_chat, umo, uid)
        except Exception as exc:
            logger.warning("[合成大猪猪] 查绑定失败：%s", exc)
            return None

    def _player_link(self, event: AstrMessageEvent, player: Dict[str, Any]) -> str:
        """签一张一次性门票塞进链接。

        不在链接里放长期 token：群聊记录、浏览器历史、转发都可能把链接漏出去，
        而门票几分钟内一次性有效，别人捡到也基本是废的。
        """
        base = self._base_url().rstrip("/")
        player_id = str(player.get("player_id") or "")
        session = event.unified_msg_origin or "" if self.session_link else ""
        ticket = ""
        if self.store is not None and player_id:
            try:
                ticket = self.store.issue_ticket(
                    player_id, session=session, ttl=int(getattr(self, "link_ttl", 600) or 600)
                )
            except Exception as exc:
                logger.warning("[合成大猪猪] 签门票失败：%s", exc)
        if ticket:
            return f"{base}/?k={quote(ticket, safe='')}"
        # 门票签不出来就退回长期凭证，至少别让人拿不到链接
        token = str(player.get("token") or "")
        if not token and self.store is not None and player_id:
            token = self.store.ensure_token(player_id)
        return f"{base}/?t={quote(token, safe='')}" if token else f"{base}/"

    def _link_hint(self) -> str:
        try:
            ttl = int(getattr(self, "link_ttl", 600) or 600)
        except Exception:
            ttl = 600
        minutes = max(1, int(round(max(60, min(ttl, 86400)) / 60)))
        return f"链接 {minutes} 分钟内有效，点一次就作废；过期了再 @我 要一个。"

    async def _board_brief(self, limit: int = 3) -> str:
        if self.store is None:
            return ""
        try:
            data = await asyncio.to_thread(self.store.board, "all", "", limit)
        except Exception:
            return ""
        rows = data.get("rows") or []
        if not rows:
            return ""
        medals = ["🥇", "🥈", "🥉"]
        parts = []
        for idx, row in enumerate(rows):
            mark = medals[idx] if idx < 3 else f"{idx + 1}."
            parts.append(
                f"{mark} {clean_name(row.get('name'), '无名猪农')} "
                f"{fmt_int(row.get('best'))}"
            )
        return " ｜ ".join(parts)

    async def _leaderboard_text(
        self, event: AstrMessageEvent, scope: str = "auto", limit: int = 0
    ) -> str:
        """排行榜正文：总榜 / 本群榜，外加「你排第几」。"""
        if self.store is None:
            return "猪圈还没搭好，等插件加载完再叫我一声。"

        wanted = str(scope or "auto").strip().lower()
        try:
            size = int(limit or 0)
        except Exception:
            size = 0
        if size <= 0:
            size = int(getattr(self, "board_size", 10) or 10)
        size = max(1, min(size, 20))

        umo = event.unified_msg_origin or ""
        session = umo if (self.session_link and event.get_group_id()) else ""

        if wanted in {"session", "group", "群", "本群"}:
            mode = "session" if session else "all"
        elif wanted in {"all", "global", "总榜", "全服"}:
            mode = "all"
        else:  # auto：群里有人交过分就先给本群榜
            mode = "session" if session else "all"

        data = await asyncio.to_thread(
            self.store.board, mode, session if mode == "session" else "", size
        )
        rows = data.get("rows") or []
        if mode == "session" and not rows:
            # 群里还没人交分，退回总榜，别让人对着空榜发呆
            mode = "all"
            data = await asyncio.to_thread(self.store.board, "all", "", size)
            rows = data.get("rows") or []

        title = "本群榜" if mode == "session" else "全服总榜"
        lines = [
            "🐖 **合成大猪猪 · 排行榜**",
            f"> {title}，最高分说话。",
            "---",
        ]
        medals = ["🥇", "🥈", "🥉"]
        for idx, row in enumerate(rows):
            mark = medals[idx] if idx < 3 else f"{idx + 1}."
            name = clean_name(row.get("name"), "无名猪农")
            bits = [f"{fmt_int(row.get('best'))} 分"]
            level = int(row.get("best_level") or 0)
            if level:
                bits.append(self._pig(level))
            plays = int(row.get("plays") or 0)
            if plays:
                bits.append(f"{plays} 局")
            lines.append(f"{mark} **{name}** · " + " · ".join(bits))
        if not rows:
            lines.append("榜上还空着，第一头猪王等你来当。")
        lines.append("---")

        me = await self._player_of_chat(umo, self._uid(event))
        if me:
            my_name = clean_name(me.get("chat_name") or me.get("name"), "猪农")
            stat: Optional[Dict[str, Any]] = None
            player_id = str(me.get("player_id") or "")
            if player_id:
                try:
                    stat = await asyncio.to_thread(self.store.player, player_id)
                except Exception:
                    stat = None
            my_best = int((stat or {}).get("best") or me.get("best") or 0)
            rank = int((stat or {}).get("rank") or 0)
            total = int((stat or {}).get("total") or 0)
            if my_best > 0:
                tail = f" · 总榜第 **{rank}** / {fmt_int(total)}" if rank else ""
                lines.append(f"👤 **{my_name}** · 最高 **{fmt_int(my_best)}** 分{tail}")
            else:
                lines.append(f"👤 **{my_name}** · 还没交过成绩，去开一罐")
        else:
            lines.append("👤 你还没建档案，回一句 `猪猪注册 你的名字` 就能上榜。")

        lines.append(
            f"🏁 共 {fmt_int(data.get('players'))} 头猪农 · "
            f"{fmt_int(data.get('runs'))} 局"
        )
        lines.append("📌 想开局就 @我 发「🐖游戏」要一条链接。")
        return "\n".join(lines)

    async def _entry_text(self, event: AstrMessageEvent) -> str:
        if self.store is None:
            return "猪圈还没搭好，等插件加载完再叫我一声。"
        umo = event.unified_msg_origin or ""
        uid = self._uid(event)
        me = await self._player_of_chat(umo, uid)
        if not me:
            return self._register_prompt(event)

        player_id = str(me.get("player_id") or "")
        name = clean_name(me.get("name") or me.get("chat_name"), "无名猪农")
        best = int(me.get("best") or 0)
        stat = await asyncio.to_thread(self.store.player, player_id)
        rank = int((stat or {}).get("rank") or 0)
        total = int((stat or {}).get("total") or 0)

        lines = [
            "🐖 **合成大猪猪 · 网页版**",
            "> 罐子搬到浏览器里了，点开就能玩，手机上也能划。",
            "---",
            f"🔗 {self._player_link(event, me)}",
            f"· {self._link_hint()}",
            "---",
        ]
        if best > 0:
            rank_text = f"，总榜第 **{rank}** / {fmt_int(total)}" if rank else ""
            lines.append(
                f"👤 **{name}** · 最高 **{fmt_int(best)}** 分{rank_text}"
            )
        else:
            lines.append(f"👤 **{name}** · 还没交过成绩，去开一罐")

        brief = await self._board_brief(3)
        if brief:
            lines.append(f"🏆 总榜前三：{brief}")
        else:
            lines.append("🏆 总榜还空着，第一头猪王等你来当。")
        return "\n".join(lines)

    def _register_prompt(self, event: AstrMessageEvent) -> str:
        return "\n".join(
            [
                "🐖 **先把猪农档案建起来**",
                "> 你还没注册，注册完就能拿到只属于你的入口链接。",
                "---",
                "回复这一行就行：",
                "`猪猪注册 你的名字`",
                "（2~12 个字，中文英文数字都可以，别带空格）",
                "---",
                f"📌 会把当前会话（{self._chat_label(event)}）和你的账号绑在一起，"
                "网页里的分数就记在你名下。",
            ]
        )

    async def _claim_text(self, event: AstrMessageEvent, code: str) -> str:
        """把旧档案迁到当前会话（换设备 / 换平台 / 换机器人时用）。"""
        if self.store is None:
            return "猪圈还没搭好，等插件加载完再叫我一声。"
        umo = event.unified_msg_origin or ""
        uid = self._uid(event)
        chat_name = clean_name(event.get_sender_name(), "")
        group = getattr(event.message_obj, "group", None)
        group_name = clean_name(getattr(group, "group_name", "") or "", "")
        try:
            result = await asyncio.to_thread(
                self.store.redeem_claim,
                code,
                umo=umo,
                uid=uid,
                chat_name=chat_name,
                group_name=group_name,
            )
        except Exception as exc:
            logger.warning("[合成大猪猪] 认领失败：%s", exc)
            return "认领的时候摔了一跤，等会儿再试。"

        status = result.get("status")
        if status == "bad_code":
            return (
                "🐖 **迁移码格式不对**\n"
                "> 是 6 位字母数字，比如 `8K3M5Q`。\n"
                "在旧设备打开游戏页，点「换设备」拿一个新的。"
            )
        if status == "expired":
            return (
                "🐖 **迁移码过期或者已经用过了**\n"
                "> 迁移码只有 10 分钟寿命，用完就作废。\n"
                "回旧设备再生成一个。"
            )
        if status == "taken":
            return (
                "🐖 **当前会话已经有档案了**\n"
                "> 一个会话只能挂一份档案，避免分数串号。\n"
                "换回原来的号，或者找管理员把这份清掉。"
            )
        if status != "ok":
            return "认领没成，等会儿再试一次。"

        player = result.get("player") or {}
        name = clean_name(player.get("name"), "无名猪农")
        best = int(player.get("best") or 0)
        lines = [
            f"✅ **档案已迁过来：{name}**",
            f"> 现在绑定在 {self._chat_label(event)}，最高 **{fmt_int(best)}** 分。",
            "---",
            f"🔗 {self._player_link(event, player)}",
            f"· {self._link_hint()}",
            "---",
            "换平台不会丢成绩，下次换回来还是这一份。",
        ]
        return "\n".join(lines)

    async def _register_text(self, event: AstrMessageEvent, raw_name: str) -> str:
        if self.store is None:
            return "猪圈还没搭好，等插件加载完再叫我一声。"

        umo = event.unified_msg_origin or ""
        uid = self._uid(event)
        name = clean_player_name(raw_name)
        if not name:
            return (
                "🐖 **注册还差一个名字**\n"
                "> 这样发：`猪猪注册 小猪快跑`\n"
                "名字 2~12 个字，不带空格。"
            )
        if not NAME_RE.match(name):
            return (
                "🐖 **这个名字不太行**\n"
                "> 只能是 2~12 个中英文、数字、下划线或短横线，别带空格和表情。\n"
                "重发一次：`猪猪注册 新名字`"
            )

        chat_name = clean_name(event.get_sender_name(), "")
        group = getattr(event.message_obj, "group", None)
        group_name = clean_name(getattr(group, "group_name", "") or "", "")
        try:
            result = await asyncio.to_thread(
                self.store.register,
                name=name,
                umo=umo,
                uid=uid,
                chat_name=chat_name or uid,
                group_name=group_name,
            )
        except Exception as exc:
            logger.warning("[合成大猪猪] 注册失败：%s", exc)
            return "注册的时候摔了一跤，等会儿再试。"

        status = result.get("status")
        player = result.get("player") or {}

        if status == "name_taken":
            return (
                f"🐖 **「{name}」被别的猪农占了**\n"
                "> 换一个吧，比如加个后缀。\n"
                "重发：`猪猪注册 新名字`"
            )
        if status not in {"ok", "exists"}:
            return "注册没成，等会儿再试一次。"

        player_id = str(player.get("player_id") or "")
        if self.store is not None and not player.get("token"):
            await asyncio.to_thread(self.store.ensure_token, player_id)
        stat = await asyncio.to_thread(self.store.player, player_id)
        best = int((stat or {}).get("best") or 0)
        registered_name = clean_name(player.get("name"), name)

        head = "✅ **注册好了**" if status == "ok" else "✅ **你已经有档案了**"
        lines = [
            f"{head}：**{registered_name}**",
            f"> 已经和 {self._chat_label(event)} 绑定。",
            "---",
            f"🔗 {self._player_link(event, player)}",
            f"· {self._link_hint()}",
        ]
        if best > 0:
            lines.append("---")
            lines.append(
                f"👤 最高 **{fmt_int(best)}** 分 · 总榜第 **{(stat or {}).get('rank', 0)}**"
            )
        else:
            lines.append("---")
            lines.append("去玩一局，回来就有分数了。")
        return "\n".join(lines)

    # ==================== 管理页 API ====================

    def _register_admin_api(self) -> None:
        if request is None or json_response is None:
            logger.warning("[合成大猪猪] 当前 AstrBot 版本没有插件 Web API，管理页不可用。")
            return
        routes = [
            ("overview", self.admin_overview, ["GET"], "猪猪管理：总览"),
            ("players", self.admin_players, ["GET"], "猪猪管理：玩家列表"),
            ("runs", self.admin_runs, ["GET"], "猪猪管理：对局列表"),
            ("player/action", self.admin_player_action, ["POST"], "猪猪管理：重置或删除玩家"),
            ("config", self.admin_get_config, ["GET"], "猪猪管理：读取配置"),
            ("config/save", self.admin_save_config, ["POST"], "猪猪管理：保存配置"),
            ("service/restart", self.admin_restart, ["POST"], "猪猪管理：重启游戏页"),
        ]
        for name, handler, methods, desc in routes:
            try:
                self.context.register_web_api(
                    f"/{PLUGIN_NAME}/admin/{name}", handler, methods, desc
                )
            except Exception as exc:  # pragma: no cover
                logger.warning("[合成大猪猪] 注册管理接口 %s 失败：%s", name, exc)

    def _admin_guard(self):
        if self.store is None:
            return error_response("数据库还没准备好", status_code=503)
        return None

    async def admin_overview(self):
        guard = self._admin_guard()
        if guard is not None:
            return guard
        assert self.store is not None
        data = await asyncio.to_thread(self.store.overview)
        return json_response(
            {
                "ok": True,
                "plugin": PLUGIN_NAME,
                "version": PLUGIN_VERSION,
                "service": self._service_info(),
                "config": self._config_payload(),
                "stats": data["stats"],
                "recent": data["recent"],
                "top": data["top"],
                "db_path": str(self.store.path),
            }
        )

    async def admin_players(self):
        guard = self._admin_guard()
        if guard is not None:
            return guard
        assert self.store is not None
        query = (request.query.get("q") or "").strip()
        try:
            limit = int(request.query.get("limit") or 50)
            offset = int(request.query.get("offset") or 0)
        except Exception:
            limit, offset = 50, 0
        data = await asyncio.to_thread(self.store.list_players, query, limit, offset)
        return json_response({"ok": True, **data})

    async def admin_runs(self):
        guard = self._admin_guard()
        if guard is not None:
            return guard
        assert self.store is not None
        try:
            limit = int(request.query.get("limit") or 50)
        except Exception:
            limit = 50
        rows = await asyncio.to_thread(self.store.list_runs, limit)
        return json_response({"ok": True, "rows": rows})

    async def admin_player_action(self):
        guard = self._admin_guard()
        if guard is not None:
            return guard
        assert self.store is not None
        payload = await request.json(default={})
        if not isinstance(payload, dict):
            return error_response("请求格式不对")
        player_id = str(payload.get("player_id") or "").strip()
        action = str(payload.get("action") or "").strip().lower()
        if not PLAYER_ID_RE.match(player_id):
            return error_response("player_id 不合法")
        if action not in {"reset", "delete"}:
            return error_response("action 只能是 reset 或 delete")

        if action == "delete":
            done = await asyncio.to_thread(self.store.delete_player, player_id)
        else:
            done = await asyncio.to_thread(self.store.reset_player, player_id)
        if not done:
            return error_response("没找到这个玩家", status_code=404)
        data = await asyncio.to_thread(self.store.overview)
        return json_response(
            {
                "ok": True,
                "action": action,
                "message": "删掉了" if action == "delete" else "成绩清空了",
                "stats": data["stats"],
                "recent": data["recent"],
                "top": data["top"],
            }
        )

    def _config_payload(self) -> Dict[str, Any]:
        return {
            "host": self.host,
            "port": int(self.port),
            "public_base_url": self.public_base_url,
            "markdown_mode": self.markdown_mode,
            "board_size": int(self.board_size),
            "allow_private": bool(self.allow_private),
            "session_link": bool(self.session_link),
            "submit_interval": float(self.submit_interval),
            "keep_days": int(self._keep_days),
            "port_range": [PORT_MIN, PORT_MAX],
        }

    async def admin_get_config(self):
        return json_response(
            {
                "ok": True,
                "config": self._config_payload(),
                "service": self._service_info(),
            }
        )

    async def admin_save_config(self):
        payload = await request.json(default={})
        if not isinstance(payload, dict):
            return error_response("请求格式不对")

        updates: Dict[str, Any] = {}
        if "port" in payload:
            try:
                port = int(payload.get("port"))
            except Exception:
                return error_response("端口得是数字")
            if not PORT_MIN <= port <= PORT_MAX:
                return error_response(f"端口只能在 {PORT_MIN}~{PORT_MAX} 之间")
            updates["port"] = port

        if "public_base_url" in payload:
            url = str(payload.get("public_base_url") or "").strip().rstrip("/")
            if url and not re.match(r"^https?://[^\s]+$", url, re.IGNORECASE):
                return error_response("对外地址要以 http:// 或 https:// 开头")
            if len(url) > 300:
                return error_response("对外地址太长了")
            updates["public_base_url"] = url

        if "host" in payload:
            host = str(payload.get("host") or "").strip()
            if not re.match(r"^[A-Za-z0-9_.:\-]{1,64}$", host):
                return error_response("监听地址不合法")
            updates["host"] = host

        if "markdown_mode" in payload:
            mode = str(payload.get("markdown_mode") or "").strip().lower()
            if mode not in {"auto", "always", "never"}:
                return error_response("Markdown 模式只能是 auto / always / never")
            updates["markdown_mode"] = mode

        if "board_size" in payload:
            try:
                size = int(payload.get("board_size"))
            except Exception:
                return error_response("榜单人数得是数字")
            if not 3 <= size <= 20:
                return error_response("榜单人数要在 3~20 之间")
            updates["board_size"] = size

        for key in ("allow_private", "session_link"):
            if key in payload:
                updates[key] = bool(payload.get(key))

        if "submit_interval" in payload:
            try:
                interval = float(payload.get("submit_interval"))
            except Exception:
                return error_response("交分间隔得是数字")
            if not 0 <= interval <= 60:
                return error_response("交分间隔要在 0~60 秒之间")
            updates["submit_interval"] = interval

        old_port = int(self.port)
        old_host = self.host

        for key, value in updates.items():
            try:
                self.config[key] = value
            except Exception:
                pass
        try:
            if hasattr(self.config, "save_config_async"):
                await self.config.save_config_async()
            elif hasattr(self.config, "save_config"):
                self.config.save_config()
        except Exception as exc:
            logger.warning("[合成大猪猪] 配置保存失败：%s", exc)
            return error_response("配置写不进去，看看日志")

        self._load_runtime_config()

        if int(self.port) != old_port or self.host != old_host:
            await self._restart_web()

        return json_response(
            {
                "ok": True,
                "message": "配置保存好了",
                "config": self._config_payload(),
                "service": self._service_info(),
            }
        )

    async def admin_restart(self):
        await self._restart_web()
        return json_response(
            {
                "ok": True,
                "message": "游戏页重启完了",
                "service": self._service_info(),
            }
        )
