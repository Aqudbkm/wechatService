#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (C) 2026 tmwgsicp
# Licensed under the GNU Affero General Public License v3.0
# See LICENSE file in the project root for full license text.
# SPDX-License-Identifier: AGPL-3.0-only
"""
RSS 数据存储 — SQLite
管理订阅列表和文章缓存
"""

import sqlite3
import time
import logging
import os
from pathlib import Path
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

# Database path: configurable via env var, defaults to ./data/rss.db
_default_db = Path(__file__).parent.parent / "data" / "rss.db"
DB_PATH = Path(os.getenv("RSS_DB_PATH", str(_default_db)))


def _get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """建表（幂等）"""
    conn = _get_conn()
    
    # 先创建不依赖其他表的基础表
    conn.executescript("""
        -- 分类表（先创建，因为 subscriptions 依赖它）
        CREATE TABLE IF NOT EXISTS categories (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL DEFAULT '',
            color       TEXT NOT NULL DEFAULT 'blue',
            sort_order  INTEGER NOT NULL DEFAULT 0,
            created_at  INTEGER NOT NULL
        );
        
        -- 黑名单表
        CREATE TABLE IF NOT EXISTS fakeid_blacklist (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            fakeid      TEXT NOT NULL UNIQUE,
            nickname    TEXT NOT NULL DEFAULT '',
            reason      TEXT NOT NULL DEFAULT 'manual',
            verification_count INTEGER NOT NULL DEFAULT 0,
            is_active   INTEGER NOT NULL DEFAULT 1,
            blacklisted_at INTEGER NOT NULL,
            unblacklisted_at INTEGER DEFAULT NULL,
            note        TEXT NOT NULL DEFAULT ''
        );
        
        CREATE INDEX IF NOT EXISTS idx_blacklist_active ON fakeid_blacklist(is_active);
    """)
    conn.commit()
    
    # 检查 subscriptions 表是否存在
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='subscriptions'"
    )
    table_exists = cursor.fetchone() is not None
    
    if table_exists:
        # 表已存在，检查是否有 category_id 列
        cursor = conn.execute("PRAGMA table_info(subscriptions)")
        columns = [row[1] for row in cursor.fetchall()]
        if "category_id" not in columns:
            # 添加 category_id 列
            conn.execute("ALTER TABLE subscriptions ADD COLUMN category_id INTEGER DEFAULT NULL")
            conn.commit()
            logger.info("Added category_id column to subscriptions table")
    else:
        # 表不存在，创建新表
        conn.executescript("""
            CREATE TABLE subscriptions (
                fakeid      TEXT PRIMARY KEY,
                nickname    TEXT NOT NULL DEFAULT '',
                alias       TEXT NOT NULL DEFAULT '',
                head_img    TEXT NOT NULL DEFAULT '',
                category_id INTEGER DEFAULT NULL,
                created_at  INTEGER NOT NULL,
                last_poll   INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE SET NULL
            );
        """)
        conn.commit()
    
    # 创建 articles 表
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS articles (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            fakeid      TEXT NOT NULL,
            aid         TEXT NOT NULL DEFAULT '',
            title       TEXT NOT NULL DEFAULT '',
            link        TEXT NOT NULL DEFAULT '',
            digest      TEXT NOT NULL DEFAULT '',
            cover       TEXT NOT NULL DEFAULT '',
            author      TEXT NOT NULL DEFAULT '',
            content     TEXT NOT NULL DEFAULT '',
            plain_content TEXT NOT NULL DEFAULT '',
            publish_time INTEGER NOT NULL DEFAULT 0,
            fetched_at  INTEGER NOT NULL,
            UNIQUE(fakeid, link),
            FOREIGN KEY (fakeid) REFERENCES subscriptions(fakeid) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_articles_fakeid_time
            ON articles(fakeid, publish_time DESC);
        CREATE INDEX IF NOT EXISTS idx_subscriptions_category ON subscriptions(category_id);
    """)
    conn.commit()
    
    # 检查并添加 source 字段（用于区分轮询器文章和历史文章）
    cursor = conn.execute("PRAGMA table_info(articles)")
    columns = [row[1] for row in cursor.fetchall()]
    if "source" not in columns:
        logger.info("Adding source column to articles table")
        conn.execute("ALTER TABLE articles ADD COLUMN source TEXT NOT NULL DEFAULT 'poll'")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_source ON articles(source)")
        conn.commit()
        logger.info("Added source column and index to articles table")
    
    conn.close()
    logger.info("RSS database initialized: %s", DB_PATH)


# ── 订阅管理 ─────────────────────────────────────────────

def add_subscription(fakeid: str, nickname: str = "",
                     alias: str = "", head_img: str = "") -> bool:
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO subscriptions "
            "(fakeid, nickname, alias, head_img, created_at) VALUES (?,?,?,?,?)",
            (fakeid, nickname, alias, head_img, int(time.time())),
        )
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


def remove_subscription(fakeid: str) -> bool:
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM subscriptions WHERE fakeid=?", (fakeid,))
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


def list_subscriptions() -> List[Dict]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT s.*, c.name AS category_name, "
            "(SELECT COUNT(*) FROM articles a WHERE a.fakeid=s.fakeid) AS article_count "
            "FROM subscriptions s "
            "LEFT JOIN categories c ON s.category_id = c.id "
            "ORDER BY s.created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_subscription(fakeid: str) -> Optional[Dict]:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM subscriptions WHERE fakeid=?", (fakeid,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_last_poll(fakeid: str):
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE subscriptions SET last_poll=? WHERE fakeid=?",
            (int(time.time()), fakeid),
        )
        conn.commit()
    finally:
        conn.close()


# ── 文章缓存 ─────────────────────────────────────────────

def save_articles(fakeid: str, articles: List[Dict], source: str = "poll") -> int:
    """
    批量保存文章，返回新增数量。
    If an article already exists but has empty content, update it with new content.
    
    Args:
        fakeid: 公众号ID
        articles: 文章列表
        source: 文章来源标记，'poll'为轮询器拉取，'deep_fetch'为历史文章获取
    """
    conn = _get_conn()
    inserted = 0
    try:
        for a in articles:
            content = a.get("content", "")
            plain_content = a.get("plain_content", "")
            try:
                cursor = conn.execute(
                    "INSERT INTO articles "
                    "(fakeid, aid, title, link, digest, cover, author, "
                    "content, plain_content, publish_time, fetched_at, source) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(fakeid, link) DO UPDATE SET "
                    "content = CASE WHEN excluded.content != '' AND articles.content = '' "
                    "  THEN excluded.content ELSE articles.content END, "
                    "plain_content = CASE WHEN excluded.plain_content != '' AND articles.plain_content = '' "
                    "  THEN excluded.plain_content ELSE articles.plain_content END, "
                    "author = CASE WHEN excluded.author != '' AND articles.author = '' "
                    "  THEN excluded.author ELSE articles.author END",
                    (
                        fakeid,
                        a.get("aid", ""),
                        a.get("title", ""),
                        a.get("link", ""),
                        a.get("digest", ""),
                        a.get("cover", ""),
                        a.get("author", ""),
                        content,
                        plain_content,
                        a.get("publish_time", 0),
                        int(time.time()),
                        source,
                    ),
                )
                if cursor.rowcount > 0:
                    inserted += 1
            except sqlite3.IntegrityError:
                pass
        conn.commit()
        return inserted
    finally:
        conn.close()


def get_articles(fakeid: str, limit: int = 20) -> List[Dict]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM articles WHERE fakeid=? "
            "ORDER BY publish_time DESC LIMIT ?",
            (fakeid, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_regular_articles(fakeid: str, limit: int = 50) -> List[Dict]:
    """
    获取常规文章（轮询器拉取的文章）
    只返回 source='poll' 的文章，不包含历史文章
    """
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM articles WHERE fakeid=? AND source='poll' "
            "ORDER BY publish_time DESC LIMIT ?",
            (fakeid, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_historical_articles(fakeid: str, limit: int = 500, offset: int = 0) -> List[Dict]:
    """
    获取历史文章（通过"获取历史文章"功能拉取的文章）
    返回 source='deep_fetch' 的文章，用于独立的历史 RSS，支持分页
    """
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM articles WHERE fakeid=? AND source='deep_fetch' "
            "ORDER BY publish_time DESC LIMIT ? OFFSET ?",
            (fakeid, limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def count_historical_articles(fakeid: str) -> int:
    """统计历史文章数量（source='deep_fetch'的文章）"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM articles WHERE fakeid=? AND source='deep_fetch'",
            (fakeid,),
        ).fetchone()
        return row["cnt"] if row else 0
    finally:
        conn.close()


def get_all_fakeids() -> List[str]:
    conn = _get_conn()
    try:
        rows = conn.execute("SELECT fakeid FROM subscriptions").fetchall()
        return [r["fakeid"] for r in rows]
    finally:
        conn.close()


def get_all_articles(limit: int = 50) -> List[Dict]:
    """
    获取所有订阅的常规文章（聚合RSS）
    只返回轮询器拉取的文章（source='poll'），不包含历史文章
    
    [2026-05-06 优化] 使用窗口函数实现"每号限额 + 总数限制"策略：
    - 根据订阅数量动态调整每个号的文章数限制
    - 保证每个订阅号都有文章显示（避免活跃号占满）
    - 单订阅场景与单个 RSS 保持一致
    """
    conn = _get_conn()
    try:
        # 获取所有订阅的fakeid
        subs = conn.execute("SELECT fakeid FROM subscriptions").fetchall()
        if not subs:
            return []
        
        fakeid_list = [s["fakeid"] for s in subs]
        subscription_count = len(fakeid_list)
        
        # 根据订阅数量计算动态限制
        per_sub_limit, total_limit = _calculate_aggregated_limits(subscription_count)
        
        # 使用实际的 limit 参数作为总数上限（用户可自定义）
        total_limit = min(limit, total_limit)
        
        placeholders = ",".join("?" * len(fakeid_list))
        
        # 使用窗口函数：每个订阅号最多 N 篇，总共最多 M 篇
        rows = conn.execute(
            f"""
            WITH ranked_articles AS (
                SELECT 
                    *,
                    ROW_NUMBER() OVER (
                        PARTITION BY fakeid 
                        ORDER BY publish_time DESC
                    ) AS rn
                FROM articles
                WHERE fakeid IN ({placeholders}) AND source='poll'
            )
            SELECT * FROM ranked_articles
            WHERE rn <= ?
            ORDER BY publish_time DESC
            LIMIT ?
            """,
            (*fakeid_list, per_sub_limit, total_limit),
        ).fetchall()
        
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_article_by_id(article_id: int) -> Optional[Dict]:
    """按 id 取单篇文章（含 content / plain_content）。供 md 导出接口用。"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM articles WHERE id=?", (article_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_export_articles(fakeid: str, since: int = 0, before: int = 0,
                        limit: int = 3000) -> List[Dict]:
    """
    取某号已抓到正文的文章（含 poll + deep_fetch 两种来源），按发布倒序，供整号导出用。
    since/before 为可选时间窗（秒级时间戳，0=不限）。
    """
    conn = _get_conn()
    try:
        sql = "SELECT * FROM articles WHERE fakeid=? AND content != ''"
        params: list = [fakeid]
        if since:
            sql += " AND publish_time >= ?"
            params.append(since)
        if before:
            sql += " AND publish_time <= ?"
            params.append(before)
        sql += " ORDER BY publish_time DESC, id DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def count_export_articles(fakeid: str, since: int = 0, before: int = 0) -> int:
    """统计某号在给定时间窗内已抓到正文的文章数（供导出截断提示）。"""
    conn = _get_conn()
    try:
        sql = "SELECT COUNT(*) AS cnt FROM articles WHERE fakeid=? AND content != ''"
        params: list = [fakeid]
        if since:
            sql += " AND publish_time >= ?"
            params.append(since)
        if before:
            sql += " AND publish_time <= ?"
            params.append(before)
        row = conn.execute(sql, params).fetchone()
        return row["cnt"] if row else 0
    finally:
        conn.close()


def get_feed_articles(since: int = 0, fakeid: Optional[str] = None,
                      limit: int = 50) -> List[Dict]:
    """
    按 publish_time 正序 + 增量游标返回本地已抓取文章（含 id），供 feed 同步用。
    客户端保存上次返回的最后一篇 publish_time 作为下次 since，循环拉到空为止。
    """
    conn = _get_conn()
    try:
        sql = "SELECT * FROM articles WHERE publish_time > ?"
        params: list = [since]
        if fakeid:
            sql += " AND fakeid=?"
            params.append(fakeid)
        sql += " ORDER BY publish_time ASC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _calculate_aggregated_limits(subscription_count: int) -> tuple:
    """
    根据订阅数量动态计算聚合 RSS 的限制策略
    
    Args:
        subscription_count: 订阅数量
    
    Returns:
        (per_sub_limit, total_limit): 每个订阅号的限额、总数上限
    
    策略设计：
    - 每个订阅号统一 30 篇
    - total_limit = subscription_count * 30（精确计算）
    - 最高支持 4500 篇（150 订阅 * 30）
    """
    if subscription_count == 0:
        return (0, 0)
    
    per_sub_limit = 30
    total_limit = subscription_count * 30
    
    # 上限：4500 篇（对应 150 个订阅）
    if total_limit > 4500:
        total_limit = 4500
    
    return (per_sub_limit, total_limit)


# ── 黑名单管理 ─────────────────────────────────────────────

def add_to_blacklist(fakeid: str, nickname: str = "", reason: str = "manual",
                     verification_count: int = 0, note: str = "") -> bool:
    """添加公众号到黑名单"""
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO fakeid_blacklist "
            "(fakeid, nickname, reason, verification_count, is_active, blacklisted_at, note) "
            "VALUES (?,?,?,?,1,?,?)",
            (fakeid, nickname, reason, verification_count, int(time.time()), note),
        )
        conn.commit()
        logger.info("Added %s to blacklist: %s", fakeid[:8], reason)
        return True
    finally:
        conn.close()


def remove_from_blacklist(fakeid: str) -> bool:
    """从黑名单移除（标记为非活跃）"""
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE fakeid_blacklist SET is_active=0, unblacklisted_at=? WHERE fakeid=?",
            (int(time.time()), fakeid),
        )
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


def delete_blacklist_record(blacklist_id: int) -> bool:
    """永久删除黑名单记录"""
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM fakeid_blacklist WHERE id=? AND is_active=0", (blacklist_id,))
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


def is_blacklisted(fakeid: str) -> bool:
    """检查公众号是否在黑名单中"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT 1 FROM fakeid_blacklist WHERE fakeid=? AND is_active=1",
            (fakeid,),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def get_blacklist() -> List[Dict]:
    """获取黑名单列表"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM fakeid_blacklist ORDER BY blacklisted_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_active_blacklist_fakeids() -> List[str]:
    """获取活跃黑名单的 fakeid 列表"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT fakeid FROM fakeid_blacklist WHERE is_active=1"
        ).fetchall()
        return [r["fakeid"] for r in rows]
    finally:
        conn.close()


def increment_verification_count(fakeid: str, nickname: str = "") -> int:
    """
    增加验证码触发次数，达到阈值时自动加入黑名单

    [2026-05-18 优化]
    1. 阈值 5 → 8（避免误判，配合精确化 verification 检测后误报率本就低）
    2. 修复隐藏 bug：之前 UPDATE 强制 is_active=1 → admin 手动取消后，下次触发又被自动激活
       现在：仅在「跨阈值的瞬间」激活；已激活/已被 admin 取消的状态保持不变

    注意：本计数为永久累计（无 24h 窗口）。误判的 fakeid 可通过 remove_from_blacklist
    或 delete_blacklist_record 手动清理（开源版简化设计，不引入 PG/Redis）

    返回：当前触发次数
    """
    threshold = 8
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM fakeid_blacklist WHERE fakeid=?", (fakeid,)
        ).fetchone()

        if row:
            new_count = row["verification_count"] + 1
            crossing_threshold = (
                row["verification_count"] < threshold <= new_count
                and not row["is_active"]
            )
            if crossing_threshold:
                # 首次跨过阈值：激活拉黑
                conn.execute(
                    "UPDATE fakeid_blacklist SET verification_count=?, is_active=1, "
                    "blacklisted_at=?, note=? WHERE fakeid=?",
                    (new_count, int(time.time()),
                     f"自动记录: 触发验证码 {new_count} 次（达到阈值 {threshold}）",
                     fakeid),
                )
            else:
                # 仅累计计数，不动 is_active（保留 admin 手动取消的状态）
                conn.execute(
                    "UPDATE fakeid_blacklist SET verification_count=? WHERE fakeid=?",
                    (new_count, fakeid),
                )
        else:
            new_count = 1
            conn.execute(
                "INSERT INTO fakeid_blacklist "
                "(fakeid, nickname, reason, verification_count, is_active, blacklisted_at, note) "
                "VALUES (?,?,?,?,?,?,?)",
                (fakeid, nickname, "high_verification", new_count,
                 1 if new_count >= threshold else 0,
                 int(time.time()),
                 f"自动记录: 触发验证码 {new_count} 次"),
            )

        conn.commit()

        if new_count >= threshold:
            logger.warning("Fakeid %s reached %d verification triggers (threshold=%d)",
                          fakeid[:8], new_count, threshold)

        return new_count
    finally:
        conn.close()


# ── 分类管理 ─────────────────────────────────────────────

def create_category(name: str, description: str = "", color: str = "blue") -> Optional[int]:
    """创建分类，返回新分类 ID"""
    conn = _get_conn()
    try:
        # 获取最大 sort_order
        row = conn.execute("SELECT MAX(sort_order) as max_order FROM categories").fetchone()
        max_order = row["max_order"] or 0
        
        cursor = conn.execute(
            "INSERT INTO categories (name, description, color, sort_order, created_at) "
            "VALUES (?,?,?,?,?)",
            (name, description, color, max_order + 1, int(time.time())),
        )
        conn.commit()
        return cursor.lastrowid
    except sqlite3.IntegrityError:
        return None
    finally:
        conn.close()


def update_category(category_id: int, name: str = None, 
                    description: str = None, color: str = None) -> bool:
    """更新分类"""
    conn = _get_conn()
    try:
        updates = []
        params = []
        if name is not None:
            updates.append("name=?")
            params.append(name)
        if description is not None:
            updates.append("description=?")
            params.append(description)
        if color is not None:
            updates.append("color=?")
            params.append(color)
        
        if not updates:
            return False
        
        params.append(category_id)
        conn.execute(
            f"UPDATE categories SET {', '.join(updates)} WHERE id=?",
            params,
        )
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


def delete_category(category_id: int) -> bool:
    """删除分类（订阅会自动解除关联）"""
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM categories WHERE id=?", (category_id,))
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


def list_categories() -> List[Dict]:
    """获取所有分类及其订阅数"""
    conn = _get_conn()
    try:
        rows = conn.execute("""
            SELECT c.*, 
                   (SELECT COUNT(*) FROM subscriptions s WHERE s.category_id=c.id) AS subscription_count
            FROM categories c 
            ORDER BY c.sort_order, c.created_at
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_category(category_id: int) -> Optional[Dict]:
    """获取单个分类"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM categories WHERE id=?", (category_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def set_subscription_category(fakeid: str, category_id: Optional[int]) -> bool:
    """设置订阅的分类"""
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE subscriptions SET category_id=? WHERE fakeid=?",
            (category_id, fakeid),
        )
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


def get_subscriptions_by_category(category_id: int) -> List[Dict]:
    """获取分类下的所有订阅"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT s.*, "
            "(SELECT COUNT(*) FROM articles a WHERE a.fakeid=s.fakeid) AS article_count "
            "FROM subscriptions s WHERE s.category_id=? ORDER BY s.created_at DESC",
            (category_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_articles_by_category(category_id: int, limit: int = 50) -> List[Dict]:
    """
    获取分类下所有订阅的常规文章
    只返回轮询器拉取的文章（source='poll'），不包含历史文章
    
    [2026-05-06 优化] 使用窗口函数实现"每号限额 + 总数限制"策略
    """
    conn = _get_conn()
    try:
        # 获取该分类下的所有fakeid
        subs = conn.execute(
            "SELECT fakeid FROM subscriptions WHERE category_id=?",
            (category_id,)
        ).fetchall()
        if not subs:
            return []
        
        fakeid_list = [s["fakeid"] for s in subs]
        subscription_count = len(fakeid_list)
        
        # [2026-05-06 优化] 使用窗口函数实现"每号限额 + 总数限制"策略
        # 根据订阅数量计算动态限制
        per_sub_limit, total_limit = _calculate_aggregated_limits(subscription_count)
        # 使用实际的 limit 参数作为总数上限（用户可自定义）
        total_limit = min(limit, total_limit)
        
        placeholders = ",".join("?" * len(fakeid_list))
        
        # 使用窗口函数：每个订阅号最多 N 篇，总共最多 M 篇
        rows = conn.execute(
            f"""
            WITH ranked_articles AS (
                SELECT 
                    *,
                    ROW_NUMBER() OVER (
                        PARTITION BY fakeid 
                        ORDER BY publish_time DESC
                    ) AS rn
                FROM articles
                WHERE fakeid IN ({placeholders}) AND source='poll'
            )
            SELECT * FROM ranked_articles
            WHERE rn <= ?
            ORDER BY publish_time DESC
            LIMIT ?
            """,
            (*fakeid_list, per_sub_limit, total_limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


