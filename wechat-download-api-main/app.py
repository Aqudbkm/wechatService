#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (C) 2026 tmwgsicp
# Licensed under the GNU Affero General Public License v3.0
# See LICENSE file in the project root for full license text.
# SPDX-License-Identifier: AGPL-3.0-only
"""
微信公众号文章API服务 - FastAPI版本
主应用文件
"""

import os
import logging
from contextlib import asynccontextmanager
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

load_dotenv()

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path

# 导入路由
from routes import article, articles, search, admin, login, image, health, stats, rss, account, feed, export
from utils.rss_store import init_db
from utils.rss_poller import rss_poller

API_DESCRIPTION = """
微信公众号文章下载 API，支持文章解析、公众号搜索、文章列表获取等功能。

## 快速开始

1. 访问 `/login.html` 扫码登录微信公众号后台
2. 调用 `GET /api/public/searchbiz?query=公众号名称` 搜索目标公众号
3. 从返回结果中取 `fakeid`，调用 `GET /api/public/articles?fakeid=xxx` 获取文章列表
4. 对每篇文章调用 `POST /api/article` 获取完整内容

## 认证说明

所有核心接口都需要先登录。登录后凭证自动保存到 `.env` 文件，服务重启后无需重新登录（有效期约 4 天）。
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动和关闭"""
    env_file = Path(__file__).parent / ".env"
    if not env_file.exists():
        print("\n" + "=" * 60)
        print("[WARNING] .env file not found")
        print("=" * 60)
        print("Please configure .env file or login via admin page")
        print("Visit: http://localhost:5000/admin.html")
        print("=" * 60 + "\n")
    else:
        print("\n" + "=" * 60)
        print("[OK] .env file loaded")
        print("=" * 60 + "\n")

    init_db()
    _skip_bg = os.getenv("SKIP_BACKGROUND_TASKS", "").lower() in ("1", "true", "yes")
    if not _skip_bg:
        await rss_poller.start()

        # 启动登录过期提醒器（自动检测凭证有效期并 webhook 通知）
        from utils.login_reminder import login_reminder
        await login_reminder.start()
    else:
        logger.warning("SKIP_BACKGROUND_TASKS 已开 → 轮询器/登录提醒未启动（仅本地测试用）")

    # [2026-07-05] MCP streamable-http session manager 随主 app 生命周期运行（否则 /mcp 请求 500）
    from contextlib import AsyncExitStack
    async with AsyncExitStack() as _mcp_stack:
        if (os.getenv("ENABLE_MCP", "").lower() in ("1", "true", "yes")
                and os.getenv("MCP_TOKEN")):
            from mcp_server.server import mcp as _mcp_instance
            await _mcp_stack.enter_async_context(_mcp_instance.session_manager.run())
            logger.info("MCP session manager started")
        yield

    if not _skip_bg:
        await login_reminder.stop()
        await rss_poller.stop()


app = FastAPI(
    title="WeChat Download API",
    description=API_DESCRIPTION,
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
    license_info={
        "name": "AGPL-3.0",
        "url": "https://www.gnu.org/licenses/agpl-3.0.html",
    },
    lifespan=lifespan,
)

# CORS配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# [2026-07-05] 全站 gzip：RSS 大源 ~8x 压缩，治阅读器超时 + 所有 JSON 响应提速。
# 按 more_body 分块压（保住 streaming 低内存）；仅客户端 Accept-Encoding 含 gzip 时压；跳过 /mcp。
from fastapi.middleware.gzip import GZipMiddleware

# 已压缩的二进制下载（zip/docx/xlsx/pdf/epub）再 gzip 是纯浪费 CPU（压不动还要缓冲整个大文件）；
# 导出的 .html / .json 是文本、仍走 gzip 受益。
_GZIP_SKIP_SUFFIX = (".zip", ".docx", ".xlsx", ".pdf", ".epub")


class _GZipExceptMCP:
    def __init__(self, app):
        self.app = app
        self._gzip = GZipMiddleware(app, minimum_size=500)

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if (scope.get("type") == "http"
                and not path.startswith("/mcp")
                and not path.endswith(_GZIP_SKIP_SUFFIX)):
            await self._gzip(scope, receive, send)
        else:
            await self.app(scope, receive, send)


app.add_middleware(_GZipExceptMCP)

# 注册路由（注意：articles.router 必须在 search.router 之前注册，避免路由冲突）
app.include_router(health.router, prefix="/api", tags=["健康检查"])
app.include_router(stats.router, prefix="/api", tags=["统计信息"])
app.include_router(article.router, prefix="/api", tags=["文章内容"])
app.include_router(articles.router, prefix="/api/public", tags=["文章列表"])  # 必须先注册
app.include_router(search.router, prefix="/api/public", tags=["公众号搜索"])  # 后注册
app.include_router(account.router, prefix="/api/public", tags=["公众号信息"])
app.include_router(admin.router, prefix="/api/admin", tags=["管理"])
app.include_router(login.router, prefix="/api/login", tags=["登录"])
app.include_router(image.router, prefix="/api", tags=["图片代理"])
app.include_router(rss.router, prefix="/api", tags=["RSS 订阅"])
app.include_router(feed.router, prefix="/api", tags=["Feed（文章列表 / markdown 导出）"])
app.include_router(export.router, prefix="/api", tags=["文章导出（整号 md/html/pdf/docx/epub/xlsx/json）"])

# ---------- MCP server（单机版 AI 客户端入口；ENABLE_MCP + MCP_TOKEN 静态 Bearer 鉴权） ----------
# 让 Claude/Codex/Cline 等 AI 客户端直接搜索/订阅/读你的公众号文章。
# 单用户自托管：不走 OAuth，配一个 MCP_TOKEN 环境变量，客户端填 Authorization: Bearer <token> 即可。
_ENABLE_MCP = os.getenv("ENABLE_MCP", "").lower() in ("1", "true", "yes")
_MCP_TOKEN = os.getenv("MCP_TOKEN", "")
if _ENABLE_MCP and _MCP_TOKEN:
    from mcp_server.server import mcp_app as _mcp_app

    _MCP_BEARER = f"Bearer {_MCP_TOKEN}".encode()

    class _MCPGateMiddleware:
        """/mcp 网关：① 静态 Bearer Token 鉴权；② 无斜杠 /mcp 内部改写为 /mcp/ 消除 307
        （307 不带 WWW-Authenticate，Codex 等客户端斜杠规范化后会拿不到挑战/连不上）。"""
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope.get("type") == "http" and scope.get("path", "").startswith("/mcp"):
                headers = dict(scope.get("headers") or [])
                if headers.get(b"authorization", b"") != _MCP_BEARER:
                    await send({"type": "http.response.start", "status": 401,
                                "headers": [(b"content-type", b"application/json"),
                                            (b"www-authenticate", b'Bearer realm="mcp"')]})
                    await send({"type": "http.response.body",
                                "body": b'{"error":"unauthorized","detail":"need Authorization: Bearer <MCP_TOKEN>"}'})
                    return
                if scope.get("path") == "/mcp":  # 无斜杠 → 改写，避免 Mount 307
                    scope = dict(scope)
                    scope["path"] = "/mcp/"
                    if scope.get("raw_path") == b"/mcp":
                        scope["raw_path"] = b"/mcp/"
            await self.app(scope, receive, send)

    app.add_middleware(_MCPGateMiddleware)
    app.mount("/mcp", _mcp_app)
    logger.info("MCP server mounted at /mcp (static bearer token)")
elif _ENABLE_MCP and not _MCP_TOKEN:
    logger.warning("ENABLE_MCP 已开但未设 MCP_TOKEN → MCP 未启用。请设置 MCP_TOKEN 环境变量。")

# 静态文件
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

@app.get("/api/redoc", include_in_schema=False)
async def redoc_html():
    """ReDoc 文档（使用 cdnjs 加速）"""
    return HTMLResponse("""<!DOCTYPE html>
<html><head>
<title>WeChat Download API - ReDoc</title>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<link href="https://fonts.googleapis.com/css?family=Montserrat:300,400,700|Roboto:300,400,700" rel="stylesheet">
<style>body{margin:0;padding:0;}</style>
</head><body>
<redoc spec-url='/api/openapi.json'></redoc>
<script src="https://cdnjs.cloudflare.com/ajax/libs/redoc/2.1.5/bundles/redoc.standalone.min.js"></script>
</body></html>""")

# 静态页面路由
@app.get("/", include_in_schema=False)
async def root():
    """首页 - 重定向到管理页面"""
    return FileResponse(static_dir / "admin.html")

@app.get("/admin.html", include_in_schema=False)
async def admin_page():
    """管理页面"""
    return FileResponse(static_dir / "admin.html")

@app.get("/login.html", include_in_schema=False)
async def login_page():
    """登录页面"""
    return FileResponse(static_dir / "login.html")

@app.get("/verify.html", include_in_schema=False)
async def verify_page():
    """验证页面"""
    return FileResponse(static_dir / "verify.html")

@app.get("/rss.html", include_in_schema=False)
async def rss_page():
    """RSS 订阅管理页面"""
    return FileResponse(static_dir / "rss.html")

@app.get("/categories.html", include_in_schema=False)
async def categories_page():
    """分类管理页面"""
    return FileResponse(static_dir / "categories.html")

@app.get("/blacklist.html", include_in_schema=False)
async def blacklist_page():
    """黑名单管理页面"""
    return FileResponse(static_dir / "blacklist.html")

@app.get("/history.html", include_in_schema=False)
async def history_page():
    """历史文章获取页面"""
    return FileResponse(static_dir / "history.html")

if __name__ == "__main__":
    import os
    import uvicorn
    from dotenv import load_dotenv

    load_dotenv()
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "5000"))
    debug = os.getenv("DEBUG", "false").lower() in ("true", "1", "yes")

    print("=" * 60)
    print("Wechat Article API Service - FastAPI Version")
    print("=" * 60)
    print(f"Admin Page: http://localhost:{port}/admin.html")
    print(f"API Docs:   http://localhost:{port}/api/docs")
    print(f"ReDoc Docs: http://localhost:{port}/api/redoc")
    print("First time? Please login via admin page")
    print("=" * 60)

    uvicorn.run(
        "app:app",
        host=host,
        port=port,
        reload=debug,
        log_level="debug" if debug else "info",
    )
