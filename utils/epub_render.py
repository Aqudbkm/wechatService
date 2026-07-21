#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (C) 2026 tmwgsicp
# Licensed under the GNU Affero General Public License v3.0
# See LICENSE file in the project root for full license text.
# SPDX-License-Identifier: AGPL-3.0-only
"""
EPUB 导出。

"文章合集"的最佳阅读格式：ZIP 压缩 + 图去重共享 + 每篇=一章 + 目录导航 + 可重排（手机/阅读器/Apple Books）。
图片走服务端取图器（export_render.fetch_images_bytes，复用全局并发闸），唯一图各存一份进
OEBPS/images、正文用相对路径引用（天然去重）。

关键：EPUB 章节必须是合法 XHTML，而微信正文是脏 HTML → 用 BeautifulSoup 清洗（去 script/style、去所有属性
只留 img src / a href、bs4 序列化会闭合 void 标签并转义实体），再包进 XHTML 骨架。

注：本模块的 article 为 dict（单机版 SQLite 行），字段用 .get 访问。
"""
import io
import logging
from datetime import datetime, timezone, timedelta

from bs4 import BeautifulSoup
from ebooklib import epub

from utils.export_render import fetch_images_bytes, _real_url, _is_wx_image

logger = logging.getLogger(__name__)


def _cn_date(pt: int) -> str:
    if not pt:
        return ""
    return datetime.fromtimestamp(pt, timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _img_kind(b: bytes):
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "png", "image/png"
    if b[:3] == b"GIF":
        return "gif", "image/gif"
    return "jpg", "image/jpeg"


def _collect_urls(articles) -> list:
    urls = []
    for a in articles:
        for img in BeautifulSoup(a.get("content") or "", "html.parser").find_all("img"):
            real = _real_url(img.get("src") or img.get("data-src") or "")
            if _is_wx_image(real):
                urls.append(real)
    return urls


def _article_xhtml(a: dict, nickname: str, url2file: dict) -> str:
    """把单篇脏 HTML 清洗为合法 XHTML body：去脚本/样式、img 换相对路径(未内嵌则删)、a 只留 http href、其余去属性。"""
    soup = BeautifulSoup(a.get("content") or "", "html.parser")
    for t in soup(["script", "style"]):
        t.decompose()
    for tag in soup.find_all(True):
        if tag.name == "img":
            real = _real_url(tag.get("src") or tag.get("data-src") or "")
            fn = url2file.get(real)
            if fn:
                tag.attrs = {"src": fn, "alt": ""}
            else:
                tag.decompose()
        elif tag.name == "a":
            href = tag.get("href", "")
            tag.attrs = {"href": href} if href.startswith("http") else {}
        else:
            tag.attrs = {}
    body = soup.decode()  # bs4 会闭合 void 标签 + 转义文本实体 → XHTML 友好
    title = _esc(a.get("title") or "(无标题)")
    meta = _esc(f"{a.get('author') or nickname} · {_cn_date(a.get('publish_time') or 0)}")
    # 注意：不要加 <?xml ... encoding=?> 声明——lxml 拒绝解析带 encoding 声明的 unicode 串，
    # 会导致 ebooklib 内部解析失败、章节写成空。ebooklib 写盘时会自行补 xml 头。
    return (
        '<html xmlns="http://www.w3.org/1999/xhtml"><head>'
        f'<title>{title}</title>'
        '<link rel="stylesheet" href="style/main.css" type="text/css"/></head><body>'
        f'<h1>{title}</h1><p class="meta">{meta}</p>{body}</body></html>'
    )


async def build_epub(nickname: str, articles: list, note: str,
                     max_side: int = 800, jpeg_q: int = 80) -> bytes:
    """整号文章 → EPUB bytes。articles 为 dict 列表。"""
    img_map = await fetch_images_bytes(_collect_urls(articles), max_side, jpeg_q)

    book = epub.EpubBook()
    book.set_identifier(f"wechat-download-{nickname}-{len(articles)}")
    book.set_title(f"{nickname} · 文章合集")
    book.set_language("zh")
    book.add_author("WeChat Download API")

    url2file = {}
    for i, (url, b) in enumerate(img_map.items()):
        ext, mt = _img_kind(b)
        fn = f"images/img{i}.{ext}"
        url2file[url] = fn
        book.add_item(epub.EpubItem(uid=f"img{i}", file_name=fn, media_type=mt, content=b))

    css = (
        'body{font-family:-apple-system,"PingFang SC","Noto Serif CJK SC",serif;'
        "line-height:1.8;margin:1em 6%;color:#1a1a1a;font-size:1em}"
        "h1{font-size:1.4em;line-height:1.45;margin:.3em 0 .2em;font-weight:700}"
        "h2{font-size:1.15em;margin:1.1em 0 .4em;font-weight:700}"
        "h3{font-size:1.05em;margin:1em 0 .4em;font-weight:700}"
        ".meta{color:#8a8a8a;font-size:.82em;margin:0 0 1.6em;padding-bottom:.9em;border-bottom:1px solid #ececec}"
        "p{margin:.75em 0;text-align:justify;word-break:break-word}"
        "img{display:block;max-width:100%;height:auto;margin:1.1em auto;border-radius:3px}"
        "blockquote{margin:1em 0;padding:.4em 1em;border-left:3px solid #dcdcdc;color:#555;background:#fafafa}"
        "a{color:#576b95;text-decoration:none}"
        "figure{margin:1em 0;text-align:center}figcaption{font-size:.82em;color:#999;margin-top:.4em}"
        "ul,ol{padding-left:1.4em}li{margin:.3em 0}"
        "hr{border:none;border-top:1px solid #eee;margin:1.5em 0}"
    )
    book.add_item(epub.EpubItem(uid="style", file_name="style/main.css",
                                media_type="text/css", content=css))

    chapters = []
    for idx, a in enumerate(articles):
        c = epub.EpubHtml(title=(a.get("title") or "(无标题)")[:80], file_name=f"ch{idx}.xhtml", lang="zh")
        c.content = _article_xhtml(a, nickname, url2file)
        book.add_item(c)
        chapters.append(c)

    book.toc = tuple(chapters)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav"] + chapters

    buf = io.BytesIO()
    # epub3_pages=False：关掉 epub3 page-list 生成（ebooklib 会对空文档解析崩 "Document is empty"）
    epub.write_epub(buf, book, {"epub3_pages": False})
    return buf.getvalue()
