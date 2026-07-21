#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (C) 2026 tmwgsicp
# Licensed under the GNU Affero General Public License v3.0
# See LICENSE file in the project root for full license text.
# SPDX-License-Identifier: AGPL-3.0-only
"""
整号文章导出 —— 把某个公众号已抓到正文的存量文章批量导出成多种格式。

单机自托管、无鉴权：导的是你本地已抓取入库的正文（poll + 历史），**纯读库、零微信调用**。

7 种格式：
- 文本/表格类（不抓图、秒出）：
  - .zip   Markdown 合集（每篇一个 .md + INDEX.md），图片引用式（指向 /api/image 代理）
  - .html  单文件 HTML 合集（苹果风样式 + 暗色 + 目录），图片引用式
  - .xlsx  文章元数据表（标题/作者/时间/链接）
  - .json  文章元数据 JSON
- 离线自包含类（现抓图内嵌，稍慢）：
  - .pdf   PDF（reportlab 内置中文字体，图片 data-URI 内嵌）
  - .docx  Word（图去重压缩、可编辑）
  - .epub  EPUB 电子书（每篇一章 + 目录，手机/阅读器读合集最佳）

范围参数：since / before / limit → 全部 / 最近 N / 时间窗 / 增量（自某时间之后）。
"""
import asyncio
import io
import html as html_module
import logging
import re
import zipfile
from datetime import datetime, timezone, timedelta
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from utils import rss_store
from routes.feed import _build_article_markdown, _safe_md_filename
from utils.export_render import inline_images, render_pdf

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_EXPORT = 3000       # md/html/xlsx/json 单次上限（纯读库，可放宽）
MAX_EXPORT_DOC = 500    # Word/EPUB 上限：要现抓图内嵌，保守些
MAX_EXPORT_PDF = 200    # PDF 上限：xhtml2pdf 纯 Python 渲染最慢，压最狠


def _cn_date(pt: int) -> str:
    if not pt:
        return ""
    return datetime.fromtimestamp(pt, timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")


def _load_articles(fakeid: str, since: int, before: int, limit: int):
    """取某号可导出的文章（按发布倒序）+ 昵称 + 总可见数（判断是否被上限截断）。"""
    sub = rss_store.get_subscription(fakeid)
    if not sub:
        raise HTTPException(404, "未订阅该公众号")
    nickname = sub.get("nickname") or fakeid
    articles = rss_store.get_export_articles(fakeid, since, before, limit)
    total = rss_store.count_export_articles(fakeid, since, before)
    return nickname, articles, total


def _download_headers(filename: str) -> dict:
    # 中文文件名用 RFC5987 编码
    return {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"}


# ── Markdown 合集（zip，每篇一文件）────────────────────────────────────

@router.get("/export/account/{fakeid}.zip", summary="整号导出为 Markdown 合集（zip，每篇一文件）")
async def export_account_md_zip(
    fakeid: str,
    since: int = Query(0, ge=0, description="只导 publish_time>=since 的文章（时间窗起点/增量，秒级时间戳）"),
    before: int = Query(0, ge=0, description="只导 publish_time<=before 的文章（时间窗终点）"),
    limit: int = Query(MAX_EXPORT, ge=1, le=MAX_EXPORT, description="最多导出篇数（最近优先）"),
):
    nickname, articles, total = _load_articles(fakeid, since, before, limit)
    if not articles:
        raise HTTPException(404, "该范围内没有可导出的文章（可能正文还没抓取完成）")

    truncated = total > len(articles)
    buf = io.BytesIO()
    index_lines = [f"# {nickname} · 导出索引", "", f"共导出 {len(articles)} 篇"]
    if truncated:
        index_lines.append(f"（该范围实际可见 {total} 篇，本次只导出最新 {len(articles)} 篇，缩小时间范围可导出更早的）")
    index_lines.append("")

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        seen = set()
        for i, a in enumerate(articles, 1):
            md = _build_article_markdown(a, nickname)
            fname = _safe_md_filename(a.get("title") or "", a.get("id"))
            entry = f"{i:03d}_{fname}"          # 前缀序号：稳定排序 + 防同名覆盖
            if entry in seen:
                entry = f"{i:03d}_{a.get('id')}_{fname}"
            seen.add(entry)
            zf.writestr(entry, md)
            index_lines.append(f"- [{_cn_date(a.get('publish_time') or 0)}] {a.get('title') or '(无标题)'} → `{entry}`")
        zf.writestr("INDEX.md", "\n".join(index_lines) + "\n")

    filename = f"{nickname}_导出_{len(articles)}篇.zip"
    return Response(content=buf.getvalue(), media_type="application/zip", headers=_download_headers(filename))


# ── 单文件 HTML 合集 ──────────────────────────────────────────────────

_HTML_STYLE = """
:root{color-scheme:light dark}
body{max-width:720px;margin:0 auto;padding:24px 16px;font:16px/1.75 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;color:#222;background:#fff}
.wx-book-title{font-size:22px;margin:0 0 4px}
.wx-book-sub{color:#888;font-size:13px;margin:0 0 24px}
.wx-toc{border:1px solid #eee;border-radius:10px;padding:12px 16px;margin:0 0 32px}
.wx-toc ol{margin:0;padding-left:20px}
.wx-toc a{color:#576b95;text-decoration:none}
.wx-article{border-top:1px solid #eee;padding-top:32px;margin-top:32px}
.wx-title{font-size:20px;line-height:1.4;margin:0 0 8px}
.wx-meta{color:#888;font-size:13px;margin:0 0 20px}
.wx-content img{max-width:100%;height:auto;border-radius:4px}
.wx-content{word-wrap:break-word}
@media(prefers-color-scheme:dark){body{color:#ddd;background:#1a1a1a}.wx-toc{border-color:#333}.wx-article{border-color:#333}.wx-toc a{color:#7d90c7}}
"""


def _article_html_fragment(a: dict, nickname: str) -> str:
    title = html_module.escape(a.get("title") or "")
    src = html_module.escape(a.get("link") or "")
    date_str = _cn_date(a.get("publish_time") or 0)
    author = html_module.escape(a.get("author") or nickname)
    src_link = f'<a href="{src}" target="_blank" rel="noopener">原文</a>' if src else ""
    return (
        f'<article class="wx-article" id="a{a.get("id")}">'
        f'<h1 class="wx-title">{title}</h1>'
        f'<div class="wx-meta">{author} · {date_str} · {src_link}</div>'
        f'<div class="wx-content">{a.get("content") or ""}</div>'
        f'</article>\n'
    )


def _build_combined_html(nickname: str, articles: list, note: str) -> str:
    fragments = [_article_html_fragment(a, nickname) for a in articles]
    toc_items = "".join(
        f'<li><a href="#a{a.get("id")}">{html_module.escape(a.get("title") or "(无标题)")}</a></li>'
        for a in articles
    )
    note_html = f'<p class="wx-book-sub">{html_module.escape(note)}</p>' if note else ""
    return (
        "<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{html_module.escape(nickname)} · 文章合集</title>"
        f"<style>{_HTML_STYLE}</style></head><body>"
        f'<h1 class="wx-book-title">{html_module.escape(nickname)}</h1>'
        f'<p class="wx-book-sub">共 {len(fragments)} 篇 · 由 WeChat Download API 导出</p>'
        f"{note_html}"
        f'<nav class="wx-toc"><ol>{toc_items}</ol></nav>'
        f'{"".join(fragments)}'
        "</body></html>"
    )


@router.get("/export/account/{fakeid}.html", summary="整号导出为单文件 HTML 合集")
async def export_account_html(
    fakeid: str,
    since: int = Query(0, ge=0),
    before: int = Query(0, ge=0),
    limit: int = Query(MAX_EXPORT, ge=1, le=MAX_EXPORT),
):
    nickname, articles, total = _load_articles(fakeid, since, before, limit)
    if not articles:
        raise HTTPException(404, "该范围内没有可导出的文章（可能正文还没抓取完成）")
    note = ""
    if total > len(articles):
        note = f"该范围实际可见 {total} 篇，本次只导出最新 {len(articles)} 篇（缩小时间范围可导出更早的）"

    doc = _build_combined_html(nickname, articles, note)
    filename = f"{nickname}_合集_{len(articles)}篇.html"
    return Response(content=doc, media_type="text/html", headers=_download_headers(filename))


# ── PDF（自包含内嵌图片，离线可读）────────────────────────────────────

# xhtml2pdf(reportlab) 不认 CSS 关键字(color:initial/inherit/unset、var()…)会直接抛错崩掉整份 PDF；
# 且微信那堆内联样式 xhtml2pdf 本就基本忽略、还会导致图文重叠。故 PDF 前剥掉内联 style 与 style/script 块，
# 只留语义 HTML，交给下方干净 CSS 渲染（更稳更整齐）。
_PDF_STRIP_BLOCK = re.compile(r'<(style|script)\b[^>]*>.*?</\1>', re.IGNORECASE | re.DOTALL)
_PDF_STRIP_STYLEATTR = re.compile(r'\sstyle\s*=\s*"[^"]*"', re.IGNORECASE)
_PDF_STRIP_STYLEATTR2 = re.compile(r"\sstyle\s*=\s*'[^']*'", re.IGNORECASE)


def _strip_css_for_pdf(html: str) -> str:
    if not html:
        return html
    html = _PDF_STRIP_BLOCK.sub("", html)
    html = _PDF_STRIP_STYLEATTR.sub("", html)
    html = _PDF_STRIP_STYLEATTR2.sub("", html)
    return html


_PDF_IMG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_PDF_DATAURI_RE = re.compile(r'src="(data:image/[^;]+;base64,[^"]+)"', re.IGNORECASE)
_PDF_WH_RE = re.compile(r'\s(?:width|height)="[^"]*"', re.IGNORECASE)


def _cap_pdf_img_width(html: str, cap_px: int = 620) -> str:
    """给每个已内嵌 data-URI 的 <img> 设显示宽度=min(原图宽, cap)。
    高分源图塞进 ≤620px 显示框 → 超采样更清晰，且绝不超 A4 内容宽(防溢出/重叠)；小图不放大。"""
    from PIL import Image
    import base64 as _b64

    def repl(m):
        tag = m.group(0)
        sm = _PDF_DATAURI_RE.search(tag)
        if not sm:
            return tag
        try:
            raw = _b64.b64decode(sm.group(1).split(",", 1)[1])
            w = Image.open(io.BytesIO(raw)).size[0]
        except Exception:
            return tag
        disp = min(w, cap_px)
        tag = _PDF_WH_RE.sub("", tag)  # 去掉原有 width/height
        return '<img width="%d"' % disp + tag[4:]

    return _PDF_IMG_RE.sub(repl, html)


def _build_pdf_html(nickname: str, articles: list, note: str) -> str:
    """xhtml2pdf 友好的合集 HTML：STSong-Light 渲染中文 + 精简 CSS；正文剥内联样式避免 reportlab 崩。"""
    parts = []
    for a in articles:
        title = html_module.escape(a.get("title") or "(无标题)")
        date_str = _cn_date(a.get("publish_time") or 0)
        author = html_module.escape(a.get("author") or nickname)
        parts.append(
            f'<div class="art"><div class="t">{title}</div>'
            f'<div class="m">{author} · {date_str}</div>'
            f'<div class="c">{_strip_css_for_pdf(a.get("content") or "")}</div></div>'
        )
    note_html = f'<p class="sub">{html_module.escape(note)}</p>' if note else ""
    body = "".join(parts)
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8"><style>'
        '@page{size:a4;margin:1.6cm 1.4cm}'
        'body,div,p,span,td,h1,h2,h3,li{font-family:STSong-Light}'
        'body{font-size:11pt;line-height:1.6;color:#222}'
        '.book{font-size:17pt;font-weight:bold;margin-bottom:2pt}'
        '.sub{color:#888;font-size:9pt;margin:0 0 10pt}'
        '.art{margin-top:14pt;padding-top:10pt;border-top:1px solid #ccc}'
        '.t{font-size:14pt;font-weight:bold;margin-bottom:3pt}'
        '.m{color:#888;font-size:9pt;margin-bottom:8pt}'
        '.c img{max-width:100%}'
        '</style></head><body>'
        f'<div class="book">{html_module.escape(nickname)}</div>'
        f'<p class="sub">共 {len(articles)} 篇 · 由 WeChat Download API 导出</p>{note_html}'
        f'{body}</body></html>'
    )


@router.get("/export/account/{fakeid}.pdf", summary="整号导出为 PDF（图片内嵌，离线可读）")
async def export_account_pdf(
    fakeid: str,
    since: int = Query(0, ge=0),
    before: int = Query(0, ge=0),
    limit: int = Query(MAX_EXPORT_PDF, ge=1, le=MAX_EXPORT_PDF),
):
    nickname, articles, total = _load_articles(fakeid, since, before, min(limit, MAX_EXPORT_PDF))
    if not articles:
        raise HTTPException(404, "该范围内没有可导出的文章（可能正文还没抓取完成）")
    note = ""
    if total > len(articles):
        note = f"该范围实际可见 {total} 篇，本次只导出最新 {len(articles)} 篇（PDF 单次上限 {MAX_EXPORT_PDF} 篇，缩小时间范围可导更早的）"

    raw_html = _build_pdf_html(nickname, articles, note)
    self_contained = await inline_images(raw_html, max_side=900)  # 高分源图更清晰
    self_contained = _cap_pdf_img_width(self_contained, cap_px=620)  # 显示宽封顶，防溢出
    pdf_bytes = await asyncio.to_thread(render_pdf, self_contained)
    if not pdf_bytes:
        raise HTTPException(500, "PDF 生成失败")

    filename = f"{nickname}_{len(articles)}篇.pdf"
    return Response(content=pdf_bytes, media_type="application/pdf", headers=_download_headers(filename))


# ── Word(.docx) ───────────────────────────────────────────────────────

@router.get("/export/account/{fakeid}.docx", summary="整号导出为 Word(.docx，图去重压缩，可编辑离线)")
async def export_account_docx(
    fakeid: str,
    since: int = Query(0, ge=0),
    before: int = Query(0, ge=0),
    limit: int = Query(MAX_EXPORT_DOC, ge=1, le=MAX_EXPORT_DOC),
):
    nickname, articles, total = _load_articles(fakeid, since, before, min(limit, MAX_EXPORT_DOC))
    if not articles:
        raise HTTPException(404, "该范围内没有可导出的文章（可能正文还没抓取完成）")
    note = ""
    if total > len(articles):
        note = f"该范围实际可见 {total} 篇，本次只导出最新 {len(articles)} 篇（Word 单次上限 {MAX_EXPORT_DOC} 篇，缩小时间范围可导更早的）"

    from utils.docx_render import build_docx  # 局部导入：仅 docx 导出时才拉 python-docx
    docx_bytes = await build_docx(nickname, articles, note, max_side=800)

    filename = f"{nickname}_{len(articles)}篇.docx"
    return Response(
        content=docx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers=_download_headers(filename),
    )


# ── EPUB ──────────────────────────────────────────────────────────────

@router.get("/export/account/{fakeid}.epub", summary="整号导出为 EPUB(合集阅读，图去重，手机/阅读器)")
async def export_account_epub(
    fakeid: str,
    since: int = Query(0, ge=0),
    before: int = Query(0, ge=0),
    limit: int = Query(MAX_EXPORT_DOC, ge=1, le=MAX_EXPORT_DOC),
):
    nickname, articles, total = _load_articles(fakeid, since, before, min(limit, MAX_EXPORT_DOC))
    if not articles:
        raise HTTPException(404, "该范围内没有可导出的文章（可能正文还没抓取完成）")
    note = ""
    if total > len(articles):
        note = f"该范围实际可见 {total} 篇，本次只导出最新 {len(articles)} 篇（EPUB 单次上限 {MAX_EXPORT_DOC} 篇）"

    from utils.epub_render import build_epub  # 局部导入：仅 EPUB 导出时才拉 ebooklib
    epub_bytes = await build_epub(nickname, articles, note, max_side=800)

    filename = f"{nickname}_{len(articles)}篇.epub"
    return Response(
        content=epub_bytes,
        media_type="application/epub+zip",
        headers=_download_headers(filename),
    )


# ── 表格导出（元数据，纯读库零抓图）──────────────────────────────────

def _meta_rows(nickname: str, articles: list) -> list:
    return [{
        "标题": a.get("title") or "",
        "作者": a.get("author") or nickname,
        "发布时间": _cn_date(a.get("publish_time") or 0),
        "发布时间戳": a.get("publish_time") or 0,
        "原文链接": a.get("link") or "",
        "公众号": nickname,
    } for a in articles]


@router.get("/export/account/{fakeid}.xlsx", summary="整号文章信息导出为 Excel 表")
async def export_account_xlsx(
    fakeid: str,
    since: int = Query(0, ge=0),
    before: int = Query(0, ge=0),
    limit: int = Query(MAX_EXPORT, ge=1, le=MAX_EXPORT),
):
    nickname, articles, _ = _load_articles(fakeid, since, before, limit)
    if not articles:
        raise HTTPException(404, "该范围内没有可导出的文章")
    rows = _meta_rows(nickname, articles)

    from openpyxl import Workbook  # 局部导入
    wb = Workbook()
    ws = wb.active
    ws.title = "文章列表"
    headers = ["标题", "作者", "发布时间", "发布时间戳", "原文链接", "公众号"]
    ws.append(headers)
    for r in rows:
        ws.append([r[h] for h in headers])
    ws.column_dimensions["A"].width = 50
    ws.column_dimensions["E"].width = 60
    buf = io.BytesIO()
    wb.save(buf)
    filename = f"{nickname}_{len(rows)}篇.xlsx"
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=_download_headers(filename),
    )


@router.get("/export/account/{fakeid}.json", summary="整号文章信息导出为 JSON")
async def export_account_json(
    fakeid: str,
    since: int = Query(0, ge=0),
    before: int = Query(0, ge=0),
    limit: int = Query(MAX_EXPORT, ge=1, le=MAX_EXPORT),
):
    nickname, articles, _ = _load_articles(fakeid, since, before, limit)
    if not articles:
        raise HTTPException(404, "该范围内没有可导出的文章")
    rows = _meta_rows(nickname, articles)
    import json as _json
    body = _json.dumps({"account": nickname, "count": len(rows), "articles": rows},
                       ensure_ascii=False, indent=2)
    filename = f"{nickname}_{len(rows)}篇.json"
    return Response(
        content=body.encode("utf-8"),
        media_type="application/json",
        headers=_download_headers(filename),
    )
