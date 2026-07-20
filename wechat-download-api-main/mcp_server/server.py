"""开源版 wechatrss MCP 资源服务器（单用户）。

鉴权：不走 OAuth，由 app 层的静态 Bearer Token 中间件统一把关（单用户自托管最简）。
挂载：app.mount("/mcp", mcp_app)。
"""
import os
import logging
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from mcp_server.store_adapter import StoreAdapter
from mcp_server.tools import register_tools

logger = logging.getLogger(__name__)

RESOURCE = os.getenv("MCP_RESOURCE_URL", "http://localhost:8000/mcp").rstrip("/")


def _transport_security() -> TransportSecuritySettings:
    """DNS-rebinding 白名单：放行部署域名(任意端口)+本地。"""
    p = urlparse(RESOURCE)
    hostname = p.hostname or "localhost"
    netloc = p.netloc or "localhost"
    hosts = {netloc, f"{hostname}:*", "localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*"}
    origins = set()
    if p.scheme and p.netloc:
        origins.update({f"{p.scheme}://{netloc}", f"{p.scheme}://{hostname}:*"})
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=sorted(hosts),
        allowed_origins=sorted(origins),
    )


def build_mcp() -> FastMCP:
    mcp = FastMCP(
        name="wechatrss",
        instructions=(
            "微信公众号 RSS（单机版）MCP：搜索/订阅公众号，按时间增量拉取已订阅文章元数据，"
            "并读取单篇文章的 markdown 全文。先 search_accounts 找号 → subscribe_account 订阅 → "
            "get_recent_articles 看列表 → read_article 读全文。"
        ),
        stateless_http=True,
        streamable_http_path="/",
        transport_security=_transport_security(),
    )
    register_tools(mcp, StoreAdapter())
    logger.info("wechatrss MCP (opensource) built (resource=%s)", RESOURCE)
    return mcp


mcp = build_mcp()
mcp_app = mcp.streamable_http_app()
