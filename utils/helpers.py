#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (C) 2026 tmwgsicp
# Licensed under the GNU Affero General Public License v3.0
# See LICENSE file in the project root for full license text.
# SPDX-License-Identifier: AGPL-3.0-only
"""
辅助函数模块
提供各种工具函数
"""

import re
from typing import Dict, Optional
from urllib.parse import urlparse, parse_qs

def html_to_text(html: str) -> str:
    """将 HTML 转为可读纯文本"""
    import html as html_module
    text = re.sub(r'<br\s*/?\s*>', '\n', html, flags=re.IGNORECASE)
    text = re.sub(r'</(?:p|div|section|h[1-6]|tr|li|blockquote)>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<hr[^>]*>', '\n---\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    text = html_module.unescape(text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def parse_article_url(url: str) -> Optional[Dict[str, str]]:
    """
    解析微信文章URL，提取参数
    
    Args:
        url: 微信文章URL
        
    Returns:
        包含__biz, mid, idx, sn的字典，如果解析失败返回None
    """
    try:
        # 确保是微信文章URL
        if not url or 'mp.weixin.qq.com/s' not in url:
            return None
        
        parsed = urlparse(str(url))  # 确保url是字符串
        params = parse_qs(parsed.query)
        
        __biz = params.get('__biz', [''])[0]
        mid = params.get('mid', [''])[0]
        idx = params.get('idx', [''])[0]
        sn = params.get('sn', [''])[0]
        
        # 必须有这4个参数才返回
        if not all([__biz, mid, idx, sn]):
            return None
        
        return {
            '__biz': __biz,
            'mid': mid,
            'idx': idx,
            'sn': sn
        }
    except Exception:
        return None

def get_item_show_type(html: str) -> Optional[str]:
    """提取 item_show_type 值"""
    m = re.search(r"window\.item_show_type\s*=\s*'(\d+)'", html)
    return m.group(1) if m else None


def is_image_text_message(html: str) -> bool:
    """检测是否为图文消息（item_show_type=8，类似小红书多图+文字）"""
    return get_item_show_type(html) == '8'


def is_short_content_message(html: str) -> bool:
    """检测是否为短内容/转发消息（item_show_type=10，纯文字无 js_content div）"""
    return get_item_show_type(html) == '10'


def is_audio_message(html: str) -> bool:
    """
    Detect audio articles (voice messages embedded via mpvoice / mp-common-mpaudio).
    检测是否为音频文章（包含 mpvoice 标签或音频播放器组件）。
    
    Important: Must check for ACTUAL audio tags, not just JS code that mentions audio.
    """
    # 方法1: 检查是否有真实的 <mpvoice> 标签（注意：mpvoice 是自定义标签）
    if '<mpvoice' in html:
        return True
    
    # 方法2: 检查是否有音频播放器组件的 **HTML标签**（不是JS代码）
    # 使用更严格的正则，确保匹配的是标签而不是JS变量
    import re
    
    # 匹配实际的音频标签：<mp-common-mpaudio ...>
    if re.search(r'<mp-common-mpaudio[^>]*>', html, re.IGNORECASE):
        return True
    
    # 匹配实际的音频容器：<div id="js_editor_audio_...">
    if re.search(r'<div[^>]+id=["\']js_editor_audio[^"\']*["\']', html, re.IGNORECASE):
        return True
    
    return False


def _extract_image_text_content(html: str) -> Dict:
    """
    提取图文消息的内容（item_show_type=8）

    图文消息的结构与普通文章完全不同：
    - 图片在 picture_page_info_list 的 JsDecode() 中
    - 文字在 meta description 或 content_desc 中
    - 没有 #js_content div
    """
    import html as html_module

    # 提取图片 URL（从 picture_page_info_list 中的 cdn_url）
    # 页面中有两种格式:
    #   1. picture_page_info_list: [ { cdn_url: JsDecode('...'), ... } ]  (带JsDecode)
    #   2. picture_page_info_list = [ { width:..., height:..., cdn_url: '...' } ]  (简单格式)
    # 每个 item 中第一个 cdn_url 是主图，watermark_info 内的是水印，需要跳过
    images = []

    # 优先使用简单格式（第二种），更易解析且包含所有图片
    simple_list_pos = html.find('picture_page_info_list = [')
    if simple_list_pos >= 0:
        bracket_start = html.find('[', simple_list_pos)
        depth = 0
        end = bracket_start
        for end in range(bracket_start, min(bracket_start + 20000, len(html))):
            if html[end] == '[':
                depth += 1
            elif html[end] == ']':
                depth -= 1
                if depth == 0:
                    break
        block = html[bracket_start:end + 1]
        # 按顶层 { 分割，每个 item 取第一个 cdn_url（主图）
        items = re.split(r'\n\s{4,10}\{', block)
        for item in items:
            m = re.search(r"cdn_url:\s*'([^']+)'", item)
            if m:
                url = m.group(1)
                if url not in images and ('mmbiz.qpic.cn' in url or 'mmbiz.qlogo.cn' in url):
                    images.append(url)

    # 降级: 使用 JsDecode 格式
    if not images:
        jsdecode_list_match = re.search(
            r'picture_page_info_list:\s*\[', html
        )
        if jsdecode_list_match:
            block_start = jsdecode_list_match.end() - 1
            depth = 0
            end = block_start
            for end in range(block_start, min(block_start + 20000, len(html))):
                if html[end] == '[':
                    depth += 1
                elif html[end] == ']':
                    depth -= 1
                    if depth == 0:
                        break
            block = html[block_start:end + 1]
            # 按顶层 { 分割
            items = re.split(r'\n\s{10,30}\{(?=\s*\n\s*cdn_url)', block)
            for item in items:
                m = re.search(r"cdn_url:\s*JsDecode\('([^']+)'\)", item)
                if m:
                    url = m.group(1).replace('\\x26amp;', '&').replace('\\x26', '&')
                    if url not in images and ('mmbiz.qpic.cn' in url or 'mmbiz.qlogo.cn' in url):
                        images.append(url)

    # 提取文字描述
    desc = ''
    # 方法1: meta description
    desc_match = re.search(r'<meta\s+name="description"\s+content="([^"]*)"', html)
    if desc_match:
        desc = desc_match.group(1)
        # 处理 \x26 编码（微信的双重编码：\x26lt; -> &lt; -> <）
        desc = re.sub(r'\\x([0-9a-fA-F]{2})', lambda m: chr(int(m.group(1), 16)), desc)
        desc = html_module.unescape(desc)
        # 二次 unescape 处理双重编码
        desc = html_module.unescape(desc)
        # 清理 HTML 标签残留
        desc = re.sub(r'<[^>]+>', '', desc)
        desc = desc.replace('\\x0a', '\n').replace('\\n', '\n')

    # 方法2: content_desc
    if not desc:
        desc_match2 = re.search(r"content_desc:\s*JsDecode\('([^']*)'\)", html)
        if desc_match2:
            desc = desc_match2.group(1)
            desc = html_module.unescape(desc)

    # 构建 HTML 内容：竖向画廊 + 文字（RSS 兼容）
    html_parts = []

    # 竖向画廊：每张图限宽，紧凑排列，兼容主流 RSS 阅读器
    if images:
        gallery_imgs = []
        for i, img_url in enumerate(images):
            gallery_imgs.append(
                f'<p style="text-align:center;margin:0 0 6px">'
                f'<img src="{img_url}" data-src="{img_url}" '
                f'style="max-width:480px;width:100%;height:auto;border-radius:4px" />'
                f'</p>'
            )
        gallery_imgs.append(
            f'<p style="text-align:center;color:#999;font-size:12px;margin:4px 0 0">'
            f'{len(images)} images'
            f'</p>'
        )
        html_parts.append('\n'.join(gallery_imgs))

    # 文字描述区域
    if desc:
        text_lines = []
        for line in desc.split('\n'):
            line = line.strip()
            if line:
                text_lines.append(
                    f'<p style="margin:0 0 8px;line-height:1.8;font-size:15px;color:#333">{line}</p>'
                )
        html_parts.append('\n'.join(text_lines))

    content = '\n'.join(html_parts)
    plain_content = desc if desc else ''

    return {
        'content': content,
        'plain_content': plain_content,
        'images': images,
    }


def _jsdecode_unescape(s: str) -> str:
    """Unescape JsDecode \\xNN sequences and HTML entities."""
    import html as html_module
    s = re.sub(r'\\x([0-9a-fA-F]{2})', lambda m: chr(int(m.group(1), 16)), s)
    s = html_module.unescape(s)
    s = html_module.unescape(s)
    return s


def _extract_short_content(html: str) -> Dict:
    """
    Extract content from item_show_type=10 (short posts / reposts).

    Type-10 articles have no js_content div; text and metadata are inside
    script tags. WeChat uses two storage formats:

    Old format: content_noencode: JsDecode('...escaped...')
    New format: content_noencode: '...escaped...'   (no JsDecode wrapper)

    Both are supported. If neither yields content, fallback to og:title /
    og:description meta (some accounts store the body there).
    """
    import html as html_module

    text = ''
    # [2026-05-25] WeChat 新版 type-10 把正文存为裸单引号字符串 (无 JsDecode 包装):
    #     content_noencode: 'AI 产业大爆发...\x0a\x0a1、芯片设计...'
    # 旧版用 JsDecode('...') 包装。两种格式都支持。
    for key in ('content_noencode', 'content'):
        # 先 try JsDecode 包装 (旧版 type-10)
        m = re.search(rf"\b{key}\s*:\s*JsDecode\('([^']*)'\)", html)
        # fallback: 裸单引号字符串 (新版 type-10) - 取最长候选避免命中短占位
        if not m or len(m.group(1)) <= 10:
            candidates = re.findall(rf"\b{key}\s*:\s*'([^']*)'", html)
            if candidates:
                longest = max(candidates, key=len)
                if len(longest) > 10:
                    text = _jsdecode_unescape(longest)
                    break
        elif m and len(m.group(1)) > 10:
            text = _jsdecode_unescape(m.group(1))
            break

    # og:title fallback (少数公众号把正文塞 og:title, 如部分 AI 周报)
    if not text:
        og_m = re.search(
            r'<meta\s+property=["\']og:title["\']\s+content=["\']([^"\']{30,})["\']',
            html,
        )
        if og_m:
            text = html_module.unescape(og_m.group(1)).replace('\\n', '\n').replace('\\x0a', '\n')

    # og:description fallback
    if not text:
        og_d = re.search(
            r'<meta\s+property=["\']og:description["\']\s+content=["\']([^"\']{30,})["\']',
            html,
        )
        if og_d:
            text = html_module.unescape(og_d.group(1)).replace('\\n', '\n').replace('\\x0a', '\n')

    # Cover / head image
    images = []
    img_m = re.search(r"round_head_img:\s*JsDecode\('([^']+)'\)", html)
    if img_m:
        img_url = _jsdecode_unescape(img_m.group(1))
        if 'mmbiz.qpic.cn' in img_url or 'wx.qlogo.cn' in img_url:
            images.append(img_url)
    if not images:
        og_i = re.search(
            r'<meta\s+property=["\']og:image["\']\s+content=["\']([^"\']+)["\']', html,
        )
        if og_i:
            url = og_i.group(1)
            if 'mmbiz.qpic.cn' in url or 'wx.qlogo.cn' in url:
                images.append(url)

    # Build HTML: simple paragraphs
    html_parts = []
    if text:
        for line in text.replace('\\x0a', '\n').replace('\\n', '\n').split('\n'):
            line = line.strip()
            if line:
                safe = html_module.escape(line)
                html_parts.append(
                    f'<p style="margin:0 0 8px;line-height:1.8;font-size:15px;color:#333">{safe}</p>'
                )

    content = '\n'.join(html_parts)
    plain_content = text.replace('\\x0a', '\n').replace('\\n', '\n') if text else ''

    return {
        'content': content,
        'plain_content': plain_content,
        'images': images,
    }


def _extract_audio_content(html: str) -> Dict:
    """
    Extract audio content from WeChat voice articles.
    音频文章使用 mpvoice / mp-common-mpaudio 标签嵌入语音，
    通过 voice_encode_fileid 构造下载链接。

    Also extracts any surrounding text content from js_content.
    """
    import html as html_module
    from bs4 import BeautifulSoup

    audio_items = []

    # Pattern 1: <mpvoice voice_encode_fileid="..." name="..." .../>
    for m in re.finditer(
        r'<mpvoice[^>]*voice_encode_fileid=["\']([^"\']+)["\'][^>]*/?>',
        html, re.IGNORECASE
    ):
        fileid = m.group(1)
        name_m = re.search(r'name=["\']([^"\']*)["\']', m.group(0))
        name = html_module.unescape(name_m.group(1)) if name_m else ''
        play_length_m = re.search(r'play_length=["\'](\d+)["\']', m.group(0))
        duration = int(play_length_m.group(1)) if play_length_m else 0
        audio_url = f"https://res.wx.qq.com/voice/getvoice?mediaid={fileid}"
        audio_items.append({'name': name, 'url': audio_url, 'duration': duration})

    # Pattern 2: mp-common-mpaudio with voice_encode_fileid in data or attributes
    if not audio_items:
        for m in re.finditer(
            r'<mp-common-mpaudio[^>]*voice_encode_fileid=["\']([^"\']+)["\'][^>]*>',
            html, re.IGNORECASE
        ):
            fileid = m.group(1)
            name_m = re.search(r'name=["\']([^"\']*)["\']', m.group(0))
            name = html_module.unescape(name_m.group(1)) if name_m else ''
            play_length_m = re.search(r'play_length=["\'](\d+)["\']', m.group(0))
            duration = int(play_length_m.group(1)) if play_length_m else 0
            audio_url = f"https://res.wx.qq.com/voice/getvoice?mediaid={fileid}"
            audio_items.append({'name': name, 'url': audio_url, 'duration': duration})

    # Build HTML content
    html_parts = []

    # Extract surrounding text from js_content (some audio articles have text too)
    text_content = ''
    js_match = re.search(
        r'<div[^>]*id=["\']js_content["\'][^>]*>([\s\S]*?)</div>\s*(?:<script|<div[^>]*class=["\']rich_media_tool)',
        html, re.IGNORECASE
    )
    if js_match:
        try:
            soup = BeautifulSoup(js_match.group(1), 'html.parser')
            for tag in soup.find_all(['mpvoice', 'mp-common-mpaudio']):
                tag.decompose()
            text_content = soup.get_text(separator='\n', strip=True)
        except Exception:
            pass

    if text_content:
        for line in text_content.split('\n'):
            line = line.strip()
            if line:
                html_parts.append(f'<p style="margin:0 0 8px;line-height:1.8">{html_module.escape(line)}</p>')

    for i, audio in enumerate(audio_items):
        dur_str = ''
        if audio['duration'] > 0:
            minutes = audio['duration'] // 60
            seconds = audio['duration'] % 60
            dur_str = f' ({minutes}:{seconds:02d})'

        display_name = audio['name'] or f'Audio {i + 1}'
        # 友好提示：音频需要微信鉴权，不提供无法播放的URL
        html_parts.append(
            f'<div style="margin:12px 0;padding:12px 16px;background:#fff9e6;'
            f'border-left:4px solid #fa8c16;border-radius:4px">'
            f'<p style="margin:0 0 4px;font-size:14px;color:#595959;font-weight:500">'
            f'音频内容: {html_module.escape(display_name)}{dur_str}</p>'
            f'<p style="margin:0;font-size:13px;color:#8c8c8c">'
            f'此文章包含音频，需要在微信中查看完整内容</p>'
            f'</div>'
        )

    content = '\n'.join(html_parts) if html_parts else ''

    plain_parts = []
    if text_content:
        plain_parts.append(text_content)
    for i, audio in enumerate(audio_items):
        display_name = audio['name'] or f'Audio {i + 1}'
        plain_parts.append(f"[Audio] {display_name} - {audio['url']}")

    return {
        'content': content,
        'plain_content': '\n'.join(plain_parts),
        'images': [],
        'audios': audio_items,
    }


def _extract_audio_share_content(html: str) -> Dict:
    """
    Extract content from item_show_type=7 audio/video share pages.
    
    These pages use dynamic Vue applications (common_share_audio module),
    so most content is loaded via JavaScript. We can only extract basic
    metadata from the static HTML.
    
    Example: Podcast episodes, audio shows (e.g., 马刺进步报告)
    """
    import html as html_module
    
    # 提取标题
    title = ''
    title_match = (
        re.search(r'<meta\s+property="og:title"\s+content="([^"]+)"', html) or
        re.search(r"window\.msg_title\s*=\s*window\.title\s*=\s*'([^']*)'", html)
    )
    if title_match:
        title = html_module.unescape(title_match.group(1))
    
    # 提取作者
    author = ''
    author_match = (
        re.search(r'<meta\s+property="og:article:author"\s+content="([^"]+)"', html) or
        re.search(r'var\s+nickname\s*=\s*"([^"]+)"', html)
    )
    if author_match:
        author = html_module.unescape(author_match.group(1))
    
    # 提取封面图(如果有)
    images = []
    og_image_match = re.search(r'<meta\s+property="og:image"\s+content="([^"]+)"', html)
    if og_image_match:
        img_url = og_image_match.group(1)
        if img_url and ('mmbiz' in img_url or img_url.startswith('http')):
            images.append(img_url)
    
    # 生成内容
    content_parts = []
    
    # 标题（如果有）
    if title:
        content_parts.append(
            f'<div style="margin:20px 0;text-align:center">'
            f'<h2 style="margin:0;font-size:22px;font-weight:600;color:#262626">{title}</h2>'
            f'</div>'
        )
    
    # 作者（如果有）
    if author:
        content_parts.append(
            f'<div style="margin:12px 0;text-align:center">'
            f'<p style="margin:0;font-size:14px;color:#8c8c8c">作者: {author}</p>'
            f'</div>'
        )
    
    # 封面图
    if images:
        for img_url in images:
            content_parts.append(
                f'<div style="text-align:center;margin:16px 0">'
                f'<img src="{img_url}" data-src="{img_url}" '
                f'style="max-width:100%;height:auto;border-radius:8px" />'
                f'</div>'
            )
    
    # 音频占位符（使用中英双语，适配RSS阅读器）
    content_parts.append(
        '<div style="background:#f6f6f6;padding:20px;border-radius:8px;'
        'text-align:center;margin:20px 0;border:2px dashed #d9d9d9">'
        '<p style="margin:0;font-size:18px;color:#333">🎵 音频内容 / Audio Content</p>'
        '<p style="margin:12px 0;font-size:14px;color:#666;line-height:1.6">'
        '这是微信音频分享文章，内容通过JavaScript动态加载，无法直接提取。<br>'
        'This is a WeChat audio share article. Content is loaded dynamically via JavaScript.</p>'
        '<p style="margin:8px 0;font-size:13px;color:#999">'
        '请在微信中查看完整内容 / Please view in WeChat app</p>'
        '</div>'
    )
    
    content = '\n'.join(content_parts)
    
    # 纯文本
    plain_content = f"[音频分享文章 / Audio Share Article]\n\n"
    if title:
        plain_content += f"标题 / Title: {title}\n"
    if author:
        plain_content += f"作者 / Author: {author}\n"
    plain_content += "\n(此音频内容无法直接提取，请在微信中查看)"
    plain_content += "\n(Audio content cannot be extracted directly, please view in WeChat)"
    
    return {
        'content': content,
        'plain_content': plain_content,
        'images': images,
    }


def extract_article_info(html: str, params: Optional[Dict] = None) -> Dict:
    """
    从HTML中提取文章信息

    Args:
        html: 文章HTML内容
        params: URL参数（可选，用于返回__biz等信息）

    Returns:
        文章信息字典
    """

    title = ''
    # 图文消息的标题通常在 window.msg_title 中
    title_match = (
        re.search(r'<h1[^>]*class=[^>]*rich_media_title[^>]*>([\s\S]*?)</h1>', html, re.IGNORECASE) or
        re.search(r'<h2[^>]*class=[^>]*rich_media_title[^>]*>([\s\S]*?)</h2>', html, re.IGNORECASE) or
        re.search(r"var\s+msg_title\s*=\s*'([^']+)'\.html\(false\)", html) or
        re.search(r"window\.msg_title\s*=\s*window\.title\s*=\s*'([^']*)'", html) or
        re.search(r'<meta\s+property="og:title"\s+content="([^"]+)"', html) or
        re.search(r"msg_title:\s*JsDecode\('([^']+)'\)", html)
    )

    if title_match:
        title = title_match.group(1)
        title = _jsdecode_unescape(title)
        title = re.sub(r'<[^>]+>', '', title)
        title = title.replace('&quot;', '"').replace('&amp;', '&').strip()

    author = ''
    author_match = (
        re.search(r'<a[^>]*id="js_name"[^>]*>([\s\S]*?)</a>', html, re.IGNORECASE) or
        re.search(r'var\s+nickname\s*=\s*"([^"]+)"', html) or
        re.search(r'<meta\s+property="og:article:author"\s+content="([^"]+)"', html) or
        re.search(r'<a[^>]*class=[^>]*rich_media_meta_nickname[^>]*>([^<]+)</a>', html, re.IGNORECASE)
    )

    if author_match:
        author = author_match.group(1)
        author = re.sub(r'<[^>]+>', '', author).strip()

    publish_time = 0
    time_match = (
        re.search(r'var\s+publish_time\s*=\s*"(\d+)"', html) or
        re.search(r'var\s+ct\s*=\s*"(\d+)"', html) or
        re.search(r"var\s+ct\s*=\s*'(\d+)'", html) or
        re.search(r'<em[^>]*id="publish_time"[^>]*>([^<]+)</em>', html)
    )

    if time_match:
        try:
            publish_time = int(time_match.group(1))
        except (ValueError, TypeError):
            pass

    # 优先处理特殊类型（按 item_show_type 判断）
    item_type = get_item_show_type(html)
    
    if item_type == '7':
        # item_show_type=7: 音频/视频分享页面（动态Vue应用）
        audio_share_data = _extract_audio_share_content(html)
        content = audio_share_data['content']
        images = audio_share_data['images']
        plain_content = audio_share_data['plain_content']
    elif item_type == '8' or is_image_text_message(html):
        # item_show_type=8: 图文消息
        img_text_data = _extract_image_text_content(html)
        content = img_text_data['content']
        images = img_text_data['images']
        plain_content = img_text_data['plain_content']
    elif item_type == '10' or is_short_content_message(html):
        # item_show_type=10: 短内容/转发消息
        short_data = _extract_short_content(html)
        content = short_data['content']
        images = short_data['images']
        plain_content = short_data['plain_content']
    elif is_audio_message(html):
        # 音频文章（mpvoice / mp-common-mpaudio）
        audio_data = _extract_audio_content(html)
        content = audio_data['content']
        images = audio_data['images']
        plain_content = audio_data['plain_content']
    else:
        content = ''
        images = []

        # 方法1: 匹配 id="js_content"
        content_match = re.search(r'<div[^>]*id="js_content"[^>]*>([\s\S]*?)<script[^>]*>[\s\S]*?</script>', html, re.IGNORECASE)

        if not content_match:
            # 方法2: 匹配 class包含rich_media_content
            content_match = re.search(r'<div[^>]*class="[^"]*rich_media_content[^"]*"[^>]*>([\s\S]*?)</div>', html, re.IGNORECASE)

        if content_match and content_match.group(1):
            content = content_match.group(1).strip()
        else:
            # 方法3: 手动截取
            js_content_pos = html.find('id="js_content"')
            if js_content_pos > 0:
                start = html.find('>', js_content_pos) + 1
                script_pos = html.find('<script', start)
                if script_pos > start:
                    content = html[start:script_pos].strip()
        if content:
            # 提取data-src属性
            img_regex = re.compile(r'<img[^>]+data-src="([^"]+)"')
            for img_match in img_regex.finditer(content):
                img_url = img_match.group(1)
                if img_url not in images:
                    images.append(img_url)

            # 提取src属性
            img_regex2 = re.compile(r'<img[^>]+src="([^"]+)"')
            for img_match in img_regex2.finditer(content):
                img_url = img_match.group(1)
                if not img_url.startswith('data:') and img_url not in images:
                    images.append(img_url)

        content = re.sub(r'<script[^>]*>[\s\S]*?</script>', '', content, flags=re.IGNORECASE)
        plain_content = html_to_text(content) if content else ''

    __biz = params.get('__biz', 'unknown') if params else 'unknown'
    publish_time_str = ''
    if publish_time > 0:
        from datetime import datetime
        dt = datetime.fromtimestamp(publish_time)
        publish_time_str = dt.strftime('%Y-%m-%d %H:%M:%S')

    return {
        'title': title,
        'content': content,
        'plain_content': plain_content,
        'images': images,
        'author': author,
        'publish_time': publish_time,
        'publish_time_str': publish_time_str,
        '__biz': __biz
    }

def has_article_content(html: str) -> bool:
    """
    Check whether the fetched HTML likely contains article content.
    Different WeChat account types use different content containers.

    Must match actual HTML elements (id/class attributes), not random JS strings,
    to avoid false positives on WeChat verification pages (~1.9MB) that contain
    "js_content" references in their JavaScript code.
    """
    element_markers = [
        'id="js_content"',
        'class="rich_media_content',
        'class="rich_media_area_primary',
        'id="page-content"',
        'id="page_content"',
    ]
    if any(marker in html for marker in element_markers):
        return True
    if is_image_text_message(html) or is_short_content_message(html) or is_audio_message(html):
        return True
    
    # item_show_type=7: Audio/video share pages (dynamic Vue app)
    # These pages have no traditional content container, but are valid articles
    if get_item_show_type(html) == '7':
        return True
    
    return False


def get_client_ip(request) -> str:
    """
    Extract real client IP from request, respecting reverse proxy headers.
    Priority: X-Forwarded-For > X-Real-IP > request.client.host
    """
    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip", "")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else "unknown"


def is_article_deleted(html: str) -> bool:
    """检查文章是否被删除"""
    return '已删除' in html or 'deleted' in html.lower()


def is_article_unavailable(html: str) -> bool:
    """
    Check if the article is permanently unavailable (deleted / censored / restricted).
    检查文章是否永久不可获取（删除/违规/限制）。
    """
    return get_unavailable_reason(html) is not None


def get_unavailable_reason(html: str) -> Optional[str]:
    """
    Return human-readable reason if article is permanently unavailable, else None.
    返回文章不可用的原因，如果文章正常则返回 None。
    
    Important: Must distinguish between:
    1. Verification pages (environment error) - NOT unavailable, should retry
    2. "暂时无法查看" standalone page - IS unavailable (HTML < 1KB, minimal structure)
    3. Privacy/payment pages (empty Vue app) - IS unavailable
    4. Truly unavailable articles (deleted/censored) - permanently unavailable
    """
    # 优先排除：微信验证页面（这不是文章不可用，而是IP风控）
    # 特征：包含"环境异常"+"完成验证"+"去验证"，且HTML较大（>1.5MB）
    verification_markers = ["环境异常", "完成验证后即可继续访问", "去验证"]
    if all(marker in html for marker in verification_markers):
        return None
    
    # 真正的不可用标记（静态HTML中的明确文字）
    # 注意：微信的正常文章HTML中可能在JS代码里包含"已删除"/"违规"等字符串
    # 需要确保这些关键字是在实际内容中，而不是在JS字符串字面量中
    markers = [
        ("该内容已被发布者删除", "已被发布者删除"),
        ("内容已删除", "已被发布者删除"),
        ("此内容因违规无法查看", "因违规无法查看"),
        ("涉嫌违反相关法律法规和政策", "涉嫌违规被限制"),
        ("此内容发送失败无法查看", "发送失败无法查看"),
        ("根据作者隐私设置，无法查看该内容", "作者隐私设置不可见"),
        ("接相关投诉，此内容违反", "因投诉违规被限制"),
        ("该文章已被第三方辟谣", "已被第三方辟谣"),
    ]
    for keyword, reason in markers:
        if keyword in html:
            # 额外验证：如果HTML很大(>1MB) 且有真实的内容容器，
            # 说明是正常文章，"已删除"/"违规"可能只是JS代码中的字符串
            if len(html) > 1000000:
                has_real_content = (
                    'id="js_content"' in html or
                    'class="rich_media_content' in html
                )
                if has_real_content:
                    # 进一步确认：检查关键字是否在 <body> 的前10KB可见区域
                    # 如果只在后面的 <script> 中出现，跳过
                    import re
                    body_match = re.search(r'<body[^>]*>(.*?)(?:<script|$)', html[:50000], re.DOTALL | re.IGNORECASE)
                    if body_match and keyword not in body_match.group(1):
                        # 关键字不在body前部，可能是JS代码，跳过此marker
                        continue
            return reason
    
    # 特殊处理："该内容暂时无法查看"独立页面
    # 特征：HTML很小（<2KB）+ <title>标签包含此文字 = 独立错误页面
    # 必须同时满足两个条件，避免误判正常文章中包含这句话的情况
    if "该内容暂时无法查看" in html and len(html) < 2000:
        import re
        title_match = re.search(r'<title>(.*?)</title>', html, re.IGNORECASE)
        if title_match and "该内容暂时无法查看" in title_match.group(1):
            return "该内容暂时无法查看"
    
    # 特殊处理：空Vue应用（隐私设置的动态错误页面）
    # 特征：<div id="app"></div> 是空的 + 无文章内容容器 + HTML不超大（<200KB）
    # 这种页面的错误提示通过JS动态加载，静态HTML中看不到
    # 实际显示："根据作者隐私设置，无法查看该内容"
    if '<div id="app">' in html and len(html) < 200000:
        import re
        # 检查是否有实际的文章内容容器
        has_content_container = (
            'id="js_content"' in html or
            'class="rich_media_content' in html or
            'class="rich_media_area_primary_inner' in html
        )
        # 如果没有内容容器，且title为空，是隐私限制页面
        title_match = re.search(r'<title>(.*?)</title>', html, re.IGNORECASE)
        if not has_content_container and title_match and not title_match.group(1).strip():
            return "根据作者隐私设置不可查看"
    
    return None


def get_exhausted_reason(html: str) -> Optional[str]:
    """
    [2026-05-26] retry 用尽后的精确失败原因 — 比 get_unavailable_reason 更宽松。

    开源版当前 rss_poller 是单次拉取无 retry, 暂未直接使用本函数;
    但提供给二次开发者扩展 retry 流程时使用, 保持与 SaaS 端 API 对齐。

    用法: poller 决定标 "retries exhausted" 之前先调本函数, 拿到精确原因
    可避免大量模糊的 retries exhausted 标签堆积。
    """
    if not html:
        return None

    reason = get_unavailable_reason(html)
    if reason:
        return reason

    # 拉到 HTML 但既无明确错误页特征也无正文容器
    if not has_article_content(html):
        if len(html) < 1000:
            return "拉取响应异常"
        return "内容无法解析"

    return "内容无法解析"


def is_need_verification(html: str) -> bool:
    """检查是否需要验证"""
    return ('verify' in html.lower() or
            '验证' in html or
            '环境异常' in html)

def is_login_required(html: str) -> bool:
    """检查是否需要登录"""
    return '请登录' in html or 'login' in html.lower()

def time_str_to_microseconds(time_str: str) -> int:
    """
    将时间字符串转换为微秒
    
    支持格式：
    - "5s" -> 5秒
    - "1m30s" -> 1分30秒
    - "1h30m" -> 1小时30分
    - "00:01:30" -> 1分30秒
    - 直接数字 -> 微秒
    """
    if isinstance(time_str, int):
        return time_str
    
    # 尝试解析为整数（已经是微秒）
    try:
        return int(time_str)
    except ValueError:
        pass
    
    # 解析时间字符串
    total_seconds = 0
    
    # 格式：HH:MM:SS 或 MM:SS
    if ':' in time_str:
        parts = time_str.split(':')
        if len(parts) == 3:
            total_seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        elif len(parts) == 2:
            total_seconds = int(parts[0]) * 60 + int(parts[1])
    else:
        # 格式：1h30m45s
        hours = re.search(r'(\d+)h', time_str)
        minutes = re.search(r'(\d+)m', time_str)
        seconds = re.search(r'(\d+)s', time_str)
        
        if hours:
            total_seconds += int(hours.group(1)) * 3600
        if minutes:
            total_seconds += int(minutes.group(1)) * 60
        if seconds:
            total_seconds += int(seconds.group(1))
    
    return total_seconds * 1000000  # 转换为微秒


