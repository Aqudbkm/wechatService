"""开源版（单用户）MCP tools。鉴权由 app 层的静态 Bearer Token 中间件统一把关，
本层不再做 per-user 门禁，直接调数据适配层。"""
import logging

from mcp.server.fastmcp.exceptions import ToolError

logger = logging.getLogger(__name__)


def register_tools(mcp, adapter):
    """把 6 个 tool 注册到 FastMCP 实例。"""

    @mcp.tool()
    async def list_subscriptions() -> list[dict]:
        """列出本机当前订阅的所有公众号。

        返回每项含 fakeid / nickname / alias / head_img / article_count / category。
        拿到 fakeid 后可配合 get_recent_articles(source_id=fakeid) 只看某个号。
        """
        return await adapter.list_subscriptions()

    @mcp.tool()
    async def search_accounts(query: str) -> list[dict]:
        """按关键词搜索微信公众号（用于订阅）。返回匹配列表（fakeid/nickname/alias/service_type）。
        拿到 fakeid 后用 subscribe_account(fakeid) 订阅。需服务端已扫码登录微信。"""
        if not (query or "").strip():
            raise ToolError("query 不能为空")
        try:
            return await adapter.search_accounts(query)
        except RuntimeError as e:
            raise ToolError(str(e))

    @mcp.tool()
    async def get_recent_articles(since: int = 0, limit: int = 30, source_id: str = "") -> dict:
        """按时间增量拉取已抓取的文章元数据（不含正文）。

        - since: Unix 时间戳，只返回 publish_time > since 的文章；首次传 0，之后传上次返回的 next_since 增量同步。
        - limit: 单次最多返回几篇（1-200，默认 30）。
        - source_id: 可选，只看某个 fakeid。
        返回 {articles:[{id,title,digest,author,fakeid,publish_time,link,content_fetched}], next_since}。
        拿到 id 后用 read_article(id) 读 markdown 全文。
        """
        limit = max(1, min(int(limit), 200))
        return await adapter.get_recent_articles(int(since), limit, source_id or None)

    @mcp.tool()
    async def read_article(article_id: int) -> str:
        """读取单篇文章的 markdown 全文（带 YAML frontmatter）。正文未抓取完成时会报错，稍后重试。"""
        try:
            return await adapter.read_article(int(article_id))
        except ValueError as e:
            raise ToolError(str(e))

    @mcp.tool()
    async def subscribe_account(fakeid: str) -> dict:
        """订阅一个公众号（按 fakeid，一般来自 search_accounts）。返回 {success, message}。"""
        try:
            return await adapter.subscribe_account(fakeid)
        except ValueError as e:
            raise ToolError(str(e))

    @mcp.tool()
    async def unsubscribe_account(fakeid: str) -> dict:
        """取消订阅一个公众号（按 fakeid）。返回 {success, message}。"""
        return await adapter.unsubscribe_account(fakeid)

    return [list_subscriptions, search_accounts, get_recent_articles,
            read_article, subscribe_account, unsubscribe_account]
