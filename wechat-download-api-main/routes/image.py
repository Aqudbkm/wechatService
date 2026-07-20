#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (C) 2026 tmwgsicp
# Licensed under the GNU Affero General Public License v3.0
# See LICENSE file in the project root for full license text.
# SPDX-License-Identifier: AGPL-3.0-only
"""
图片代理路由 - 真流式版本

[2026-05-15 OS-1 修复] 改用 client.stream() + aiter_bytes 真流式输出
- 原代码 response.content 全加载到内存（微信封面图 1-5MB，长图 10MB+，并发会 OOM）
- 加 30MB content-length 上限防御
- 生成器内部管理 client + response 生命周期，确保正确关闭
"""

from fastapi import APIRouter, Query, HTTPException
from fastapi.responses import StreamingResponse
from urllib.parse import urlparse
import httpx
import logging

logger = logging.getLogger(__name__)

router = APIRouter()

ALLOWED_IMAGE_HOSTS = {
    "mmbiz.qpic.cn",
    "mmbiz.qlogo.cn",
    "wx.qlogo.cn",
    "res.wx.qq.com",
}

MAX_IMAGE_BYTES = 30 * 1024 * 1024  # 30MB，运行时也再次校验
STREAM_CHUNK = 8192


@router.get("/image", summary="图片代理下载")
async def proxy_image(url: str = Query(..., description="图片URL")):
    """
    代理下载微信图片，避免防盗链。

    安全约束：
    - 仅允许 ALLOWED_IMAGE_HOSTS 中的微信 CDN 域名
    - 单图最大 30MB（content-length 预检 + 流式累计校验）

    Returns:
        StreamingResponse: 真流式输出图片数据，内存峰值 ~8KB
    """
    if not url:
        raise HTTPException(status_code=400, detail="URL参数不能为空")

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="仅支持HTTP/HTTPS协议")
    if parsed.hostname not in ALLOWED_IMAGE_HOSTS:
        raise HTTPException(status_code=403, detail="仅允许代理微信CDN图片")

    # 打开 stream 拿 headers 做预检，client 和 response 生命周期托管到 generator
    client = httpx.AsyncClient(timeout=30.0)
    try:
        # [2026-07-20] 带合法 referer mp.weixin.qq.com：微信防盗链永远放行（比无 referer 更稳）
        request = client.build_request("GET", url, headers={"Referer": "https://mp.weixin.qq.com/"})
        response = await client.send(request, stream=True, follow_redirects=True)
    except Exception as e:
        await client.aclose()
        raise HTTPException(status_code=500, detail=f"下载图片失败: {str(e)}")

    if response.status_code != 200:
        status = response.status_code
        await response.aclose()
        await client.aclose()
        raise HTTPException(status_code=status, detail=f"下载图片失败: HTTP {status}")

    cl_str = response.headers.get("content-length")
    if cl_str:
        try:
            cl = int(cl_str)
            if cl > MAX_IMAGE_BYTES:
                await response.aclose()
                await client.aclose()
                raise HTTPException(status_code=413, detail="图片过大（>30MB）")
        except ValueError:
            pass

    content_type = response.headers.get("content-type", "image/jpeg")

    async def iter_chunks():
        total = 0
        try:
            async for chunk in response.aiter_bytes(STREAM_CHUNK):
                total += len(chunk)
                if total > MAX_IMAGE_BYTES:
                    logger.warning("Image %s exceeded MAX_IMAGE_BYTES, truncated", url[:80])
                    break
                yield chunk
        finally:
            try:
                await response.aclose()
            finally:
                await client.aclose()

    return StreamingResponse(
        iter_chunks(),
        media_type=content_type,
        headers={
            "Content-Disposition": f"inline; filename={url.split('/')[-1]}",
            "Cache-Control": "public, max-age=86400",
        },
    )
