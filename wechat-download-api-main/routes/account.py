#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (C) 2026 tmwgsicp
# Licensed under the GNU Affero General Public License v3.0
# See LICENSE file in the project root for full license text.
# SPDX-License-Identifier: AGPL-3.0-only
"""
公众号信息接口
获取公众号的主体信息、认证信息等
"""

from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict
import httpx
from utils.auth_manager import auth_manager
from utils.wechat_status import is_login_expired, LOGIN_EXPIRED_MSG

router = APIRouter()


class AccountInfoResponse(BaseModel):
    """公众号信息响应"""
    success: bool
    data: Optional[Dict] = None
    error: Optional[str] = None


@router.get("/accountinfo", response_model=AccountInfoResponse, summary="获取公众号主体信息")
async def get_account_info(
    fakeid: str = Query(..., description="公众号的 FakeID（通过搜索接口获取）")
):
    """
    获取指定公众号的主体信息（认证主体、原创文章数等）。
    
    **使用流程：**
    1. 先调用 `GET /api/public/searchbiz` 搜索目标公众号
    2. 从搜索结果中获取目标公众号的 `fakeid`
    3. 使用 `fakeid` 调用本接口获取主体信息
    
    **查询参数：**
    - **fakeid** (必填): 目标公众号的 FakeID
    
    **返回字段：**
    - `identity_name`: 认证主体名称（如"腾讯科技有限公司"）
    - `is_verify`: 认证状态（0=未认证, 1=微信认证, 2=新媒体认证）
    - `original_article_count`: 原创文章数量
    """
    # 获取认证信息（用于请求微信API）
    credentials = auth_manager.get_credentials()
    if not credentials:
        return AccountInfoResponse(
            success=False,
            error="服务器未登录，请先访问管理页面扫码登录"
        )
    
    token = credentials.get("token")
    cookie = credentials.get("cookie")
    
    try:
        print(f"[INFO] get account info: fakeid={fakeid[:8]}...")
        
        # 构建请求参数
        params = {
            "wxtoken": "777",
            "biz": fakeid,
            "__biz": fakeid,
            "x5": 0,
            "f": "json",
        }
        
        # 调用微信 authorinfo 接口
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                "https://mp.weixin.qq.com/mp/authorinfo",
                params=params,
                headers={
                    "Cookie": cookie,
                    "Referer": "https://mp.weixin.qq.com/",
                    "Origin": "https://mp.weixin.qq.com",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36"
                }
            )
            
            result = response.json()
            
            # 检查返回状态
            base_resp = result.get("base_resp", {})
            if base_resp.get("ret") == 0:
                # 提取关键信息
                data = {
                    "identity_name": result.get("identity_name", ""),
                    "is_verify": result.get("is_verify", 0),
                    "original_article_count": result.get("original_article_count", 0)
                }
                
                print(f"[SUCCESS] account info retrieved: {data.get('identity_name', 'N/A')}")
                
                return AccountInfoResponse(
                    success=True,
                    data=data
                )
            else:
                ret_code = base_resp.get("ret")
                err_msg = base_resp.get("err_msg", "未知错误")
                print(f"[ERROR] WeChat API error: ret={ret_code}, {err_msg}")
                # 登录态失效 → 明确提示重新扫码
                if is_login_expired(ret_code, err_msg):
                    return AccountInfoResponse(success=False, error=LOGIN_EXPIRED_MSG)
                return AccountInfoResponse(
                    success=False,
                    error=f"获取信息失败: {err_msg}"
                )
                
    except httpx.TimeoutException:
        print("[ERROR] request timeout")
        return AccountInfoResponse(
            success=False,
            error="请求超时，请稍后重试"
        )
    except httpx.RequestError as e:
        print(f"[ERROR] request failed: {str(e)}")
        return AccountInfoResponse(
            success=False,
            error=f"网络请求失败: {str(e)}"
        )
    except Exception as e:
        print(f"[ERROR] unexpected error: {str(e)}")
        return AccountInfoResponse(
            success=False,
            error=f"服务器内部错误: {str(e)}"
        )
