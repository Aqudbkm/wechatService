"""
开源版（单用户 / SQLite）的 MCP 数据适配层。

把 6 个 MCP tool 需要的能力桥接到 utils.rss_store（sqlite3）+ 搜索/凭证。
与 SaaS 版的多租户 SQLAlchemy 适配层不同：单用户、无 user_id、无付费门禁。
"""
import os
import time
import logging

import httpx

from utils import rss_store
from utils.image_proxy import proxy_image_url
from utils.auth_manager import auth_manager

logger = logging.getLogger(__name__)

WECHAT_API_BASE = "https://mp.weixin.qq.com"


def _base_url() -> str:
    return os.getenv("SITE_URL", "").rstrip("/")


class StoreAdapter:
    """单用户数据适配：全部同步 sqlite 读，MCP 调用稀疏，直接调用即可。"""

    async def list_subscriptions(self) -> list[dict]:
        subs = rss_store.list_subscriptions()
        base = _base_url()
        return [{
            "fakeid": s.get("fakeid", ""),
            "nickname": s.get("nickname", ""),
            "alias": s.get("alias", ""),
            "head_img": proxy_image_url(s.get("head_img", ""), base) if s.get("head_img") else "",
            "article_count": s.get("article_count", 0),
            "category": s.get("category_name", ""),
            "created_at": s.get("created_at", 0),
        } for s in subs]

    async def search_accounts(self, query: str) -> list[dict]:
        creds = auth_manager.get_credentials()
        if not creds:
            raise RuntimeError("服务端未登录微信，请先在管理页扫码登录后再搜索")
        token, cookie = creds.get("token"), creds.get("cookie")
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(
                f"{WECHAT_API_BASE}/cgi-bin/searchbiz",
                params={"action": "search_biz", "token": token, "lang": "zh_CN",
                        "f": "json", "ajax": 1, "random": time.time(),
                        "query": query, "begin": 0, "count": 5},
                headers={"Cookie": cookie,
                         "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            )
            data = r.json()
        if data.get("base_resp", {}).get("ret") != 0:
            raise RuntimeError(f"微信搜索失败：{data.get('base_resp', {}).get('err_msg', '未知')}（可能登录态过期，请重新扫码）")
        blacklisted = set(rss_store.get_active_blacklist_fakeids())
        out = []
        for a in data.get("list", []):
            fid = a.get("fakeid", "")
            if fid in blacklisted:
                continue
            out.append({
                "fakeid": fid,
                "nickname": a.get("nickname", ""),
                "alias": a.get("alias", ""),
                "service_type": a.get("service_type", 0),
            })
        return out

    async def get_recent_articles(self, since: int, limit: int, source_id: str | None) -> dict:
        arts = rss_store.get_feed_articles(since=since, fakeid=source_id or None, limit=limit)
        items = [{
            "id": a.get("id"),
            "title": a.get("title", ""),
            "digest": a.get("digest", ""),
            "author": a.get("author", ""),
            "fakeid": a.get("fakeid", ""),
            "publish_time": a.get("publish_time", 0),
            "link": a.get("link", ""),
            "content_fetched": bool(a.get("content_fetched", 0)),
        } for a in arts]
        next_since = arts[-1].get("publish_time", since) if arts else since
        return {"articles": items, "next_since": next_since}

    async def read_article(self, article_id: int) -> str:
        from routes.feed import _build_article_markdown
        art = rss_store.get_article_by_id(article_id)
        if not art:
            raise ValueError(f"文章 {article_id} 不存在")
        if not art.get("content"):
            raise ValueError("文章正文尚未抓取完成，稍后重试")
        sub = rss_store.get_subscription(art.get("fakeid", ""))
        nickname = (sub or {}).get("nickname", "") or art.get("author", "")
        return _build_article_markdown(art, nickname)

    async def subscribe_account(self, fakeid: str) -> dict:
        if rss_store.is_blacklisted(fakeid):
            raise ValueError("该公众号已被标记失效，无法订阅")
        if rss_store.get_subscription(fakeid):
            return {"success": True, "message": "已订阅，无需重复"}
        # 尽量取个昵称（失败不阻塞；轮询会补全元数据）
        nickname = ""
        try:
            creds = auth_manager.get_credentials()
            if creds:
                async with httpx.AsyncClient(timeout=8.0) as c:
                    r = await c.get(
                        f"{WECHAT_API_BASE}/cgi-bin/searchbiz",
                        params={"action": "search_biz", "token": creds.get("token"),
                                "lang": "zh_CN", "f": "json", "ajax": 1,
                                "random": time.time(), "query": fakeid, "begin": 0, "count": 5},
                        headers={"Cookie": creds.get("cookie"),
                                 "User-Agent": "Mozilla/5.0"})
                    for a in r.json().get("list", []):
                        if a.get("fakeid") == fakeid:
                            nickname = a.get("nickname", "")
                            break
        except Exception:
            pass
        rss_store.add_subscription(fakeid, nickname=nickname)
        return {"success": True, "message": f"已订阅 {nickname or fakeid}"}

    async def unsubscribe_account(self, fakeid: str) -> dict:
        ok = rss_store.remove_subscription(fakeid)
        return {"success": ok, "message": "已取消订阅" if ok else "未找到该订阅"}
