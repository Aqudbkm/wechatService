#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (C) 2026 tmwgsicp
# Licensed under the GNU Affero General Public License v3.0
# See LICENSE file in the project root for full license text.
# SPDX-License-Identifier: AGPL-3.0-only
"""
图片 URL 处理工具
统一处理微信 CDN HTTP 图片转 HTTPS 代理
"""
import re
from urllib.parse import quote, unquote


def to_direct_wechat_images(html: str) -> str:
    """
    [2026-07-20] 读路径图片加速：把正文图片改成「直连 mmbiz + referrerpolicy=no-referrer」。

    存储的正文里图片是 `<host>/api/image?url=<编码原图>` 代理 URL（content_processor 处理过），
    阅读器走代理跨境拉图很慢。此函数把代理 URL 还原成直连 mmbiz、并给每个 <img> 补
    referrerpolicy="no-referrer"——浏览器不发 referer，微信防盗链放行，直连 CDN 最快。

    幂等：已直连的原样、只补 referrerpolicy；代理的先还原再补。存量内容无需迁移 DB。
    """
    if not html:
        return html
    # 1) 去代理 → 直连：<host>/api/image?url=<encoded mmbiz> → 直连 mmbiz
    html = re.sub(
        r'https?://[^"\'\s)]*/api/image\?url=([^"\'\s)]+)',
        lambda m: unquote(m.group(1)),
        html,
    )

    # 2) 每个 <img> 补 referrerpolicy=no-referrer（已有则跳过，幂等）
    def _add_rp(m):
        tag = m.group(0)
        if 'referrerpolicy' in tag.lower():
            return tag
        return '<img referrerpolicy="no-referrer"' + tag[4:]

    return re.sub(r'<img\b[^>]*>', _add_rp, html, flags=re.IGNORECASE)


def proxy_image_url(url: str, base_url: str) -> str:
    """
    将微信 CDN HTTP 图片 URL 转换为 HTTPS 代理 URL
    
    Args:
        url: 原始图片 URL
        base_url: 服务基础 URL (如 http://localhost:8000 或 https://your-domain.com)
    
    Returns:
        代理后的 HTTPS URL 或原始 URL
    
    Examples:
        >>> proxy_image_url("http://mmbiz.qpic.cn/xxx.jpg", "https://example.com")
        'https://example.com/api/image?url=http%3A//mmbiz.qpic.cn/xxx.jpg'
        
        >>> proxy_image_url("https://example.com/image.jpg", "https://example.com")
        'https://example.com/image.jpg'
    """
    if not url:
        return ""
    
    # 防止重复代理：如果 URL 已经是代理 URL，直接返回
    if "/api/image?url=" in url:
        return url
    
    # 只代理微信 CDN 的图片
    if "mmbiz.qpic.cn" in url or "mmbiz.qlogo.cn" in url or "wx.qlogo.cn" in url:
        return f"{base_url.rstrip('/')}/api/image?url={quote(url, safe='')}"
    
    return url


def proxy_content_images(html_content: str, base_url: str) -> str:
    """
    代理 HTML 内容中的所有微信图片 URL
    
    Args:
        html_content: 文章 HTML 内容
        base_url: 服务基础 URL
    
    Returns:
        代理后的 HTML 内容
    """
    import re
    
    if not html_content:
        return ""
    
    # 替换 data-src 属性
    def replace_data_src(match):
        url = match.group(1)
        proxied_url = proxy_image_url(url, base_url)
        return f'data-src="{proxied_url}" src="{proxied_url}"'
    
    html_content = re.sub(
        r'data-src="([^"]+)"',
        replace_data_src,
        html_content
    )
    
    # 替换 src 属性（避免重复替换已经有 data-src 的）
    def replace_src(match):
        full_tag = match.group(0)
        # 如果已经有 data-src，跳过
        if 'data-src=' in full_tag:
            return full_tag
        
        url = match.group(1)
        proxied_url = proxy_image_url(url, base_url)
        return f'src="{proxied_url}"'
    
    html_content = re.sub(
        r'src="([^"]+)"',
        replace_src,
        html_content
    )
    
    return html_content
