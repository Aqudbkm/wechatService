#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (C) 2026 tmwgsicp
# Licensed under the GNU Affero General Public License v3.0
# See LICENSE file in the project root for full license text.
# SPDX-License-Identifier: AGPL-3.0-only
"""
微信 cgi-bin 接口返回的 base_resp 语义化判断。

集中一处，避免 search / articles / account 各写一套 ret 判断逻辑。
登录态失效时给用户**明确**提示（指向重新扫码登录），而不是泛化的"失败"——
否则用户会误以为是程序坏了（凭证约 4 天过期，这是最常见的"搜不到/不更新"原因）。
"""

# 给用户的统一提示：明确指向「重新扫码登录」
LOGIN_EXPIRED_MSG = "微信登录已过期，请到管理面板重新扫码登录后再试"


def is_login_expired(ret, err_msg: str = "") -> bool:
    """base_resp 是否表示凭证失效、需要重新扫码登录。

    实测两种信号（都靠重新扫码登录解决，对用户是同一回事）：
    - ret=200003 'invalid session'   → 会话/cookie 过期（最常见，约 4 天到期）
    - ret=200040 'invalid csrf token' → token 失效/不匹配
    """
    msg = (err_msg or "").lower()
    return ret in (200003, 200040) or "invalid session" in msg or "csrf" in msg or "login" in msg


def is_invalid_fakeid(ret, err_msg: str = "") -> bool:
    """base_resp 是否表示 fakeid 已失效（注销/改名/重新注册）。"""
    return ret == 200002 and "invalid arg" in (err_msg or "").lower()
