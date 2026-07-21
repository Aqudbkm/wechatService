# 微信公众号文章内容类型与识别策略

本文档说明微信公众号文章的各种内容类型、不可用状态，以及对应的识别和处理策略。

---

## 一、文章内容类型

微信公众号使用 `item_show_type` 参数来区分不同的内容类型。这个参数通常在HTML的JavaScript代码中定义。

### item_show_type 值说明

| 值 | 类型 | 说明 |
|----|------|------|
| `0` | 标准富文本 | 最常见的图文文章 |
| `7` | 音频/视频分享 | 动态Vue应用，内容通过JS加载 |
| `8` | 图文消息 | 类似小红书的多图+短文风格 |
| `10` | 短内容 | 纯文字或转发消息，无 `js_content` 容器 |
| 其他 | 未知 | 其他特殊内容，待补充 |

---

### 1. 标准富文本文章

**item_show_type**: `0`（或未定义）

**特征**：
- 包含 `<div id="js_content">` 或 `<div class="rich_media_content">`
- 文字 + 图片混合
- HTML大小：通常 > 100KB

**提取策略**：
- 提取 `js_content` 区域的完整HTML
- 按顺序提取所有图片URL（`data-src` 或 `src` 属性）
- 生成纯文本（`plain_content`）供RSS阅读器使用
- 图片URL通过代理服务转发（避免防盗链）

---

### 1.5. 音频分享文章（Audio Share）

**item_show_type**: `7`

**特征**：
- 动态Vue应用（使用 `common_share_audio` 模块）
- **无传统的 `js_content` 容器**
- `og:image` 和 `og:description` 通常为空
- HTML中包含 `window.item_show_type = '7'`
- 内容通过JavaScript动态加载，静态HTML中看不到实际音频内容

**典型公众号**：
- 播客节目（如"马刺进步报告"）
- 音频节目分享
- 视频号音频内容

**提取策略**：
```python
# 检测逻辑
if get_item_show_type(html) == '7':
    # 这是音频分享页面
    return _extract_audio_share_content(html)
```

**可提取内容**：
- ✅ 标题（从 `og:title` 或 `window.msg_title`）
- ✅ 作者（从 `og:article:author` 或 `var nickname`）
- ✅ 封面图（从 `og:image`，如果有）
- ❌ 音频URL（需要JavaScript执行才能获取）
- ❌ 播放时长
- ❌ 音频播放器

**RSS展示效果**：
```html
<div style="background:#f6f6f6;padding:20px;border-radius:8px">
  <p>🎵 音频内容 / Audio Content</p>
  <p>这是微信音频分享文章，内容通过JavaScript动态加载，无法直接提取。</p>
  <p>请在微信中查看完整内容</p>
</div>
```

**已知限制**：
- 无法提取真实音频URL（需要浏览器环境执行JS）
- 只能提供标题、作者和封面图的基本信息
- RSS阅读器中显示占位符，引导用户到微信查看原文

**未来改进方向**：
- 使用无头浏览器（Playwright/Puppeteer）执行JavaScript
- 逆向分析微信音频API
- 提供更丰富的元数据展示

---

### 2. 纯图片文章

**item_show_type**: `0`

**特征**：
- 有 `<div id="js_content">` 容器
- 内容区域只有 `<img>` 标签，**没有任何文字**
- HTML大小：2-3MB（正常大小）

**处理策略**：
- 正常提取HTML和图片列表
- `plain_content` 生成占位文本：`[纯图片文章，共 X 张图片]`

**注意**：
- 必须使用严格的音频检测逻辑，避免误判为音频文章

---

### 3. 图文消息

**item_show_type**: `8`

**特征**：
- 类似"小红书"的多图+短文风格
- 包含特殊的图文混排结构
- 通常是手机端创作的内容

**识别**：`is_image_text_message(html)` → `get_item_show_type(html) == '8'`

**提取**：`_extract_image_text_content(html)`

---

### 4. 短内容消息

**item_show_type**: `10`

**特征**：
- 纯文字，无 `js_content` div
- 类似"朋友圈"的短文本或转发内容
- HTML结构简单，内容在特殊的容器中

**识别**：`is_short_content_message(html)` → `get_item_show_type(html) == '10'`

**提取**：`_extract_short_content(html)`

---

### 5. 音频文章（待完善）

**item_show_type**: `0`

**特征**：
- 包含音频播放器组件
- 可能包含 `<mpvoice>` 标签或 `<mp-common-mpaudio>` 标签
- 可能同时包含配图（图+音频混合）

**识别**：`is_audio_message(html)`
- 匹配真实的 `<mpvoice>` 标签
- 匹配 `<mp-common-mpaudio>` 标签
- 匹配 `<div id="js_editor_audio_xxx">` 容器

**重要**：
- 必须使用严格的正则匹配HTML标签
- 不要匹配JS代码中的 `voice_encode_fileid` 等字符串（会误判纯图片文章）

**当前状态**：
- 基础识别逻辑已实现
- 内容提取待完善（图+音频混合场景）

---

## 二、文章不可用状态

### 1. 验证页面（可重试）⚠️

**特征**：
- HTML大小：1.5-2MB（很大）
- 包含完整的验证组件代码
- 关键标记：`"环境异常"` + `"完成验证后即可继续访问"` + `"去验证"`

**原因**：
- 代理IP被微信风控
- 或服务器IP请求过于频繁

**处理**：
- ❌ **不应**标记为永久失效
- ✅ **应该**标记为可重试（`failed`）
- ✅ 切换代理或等待冷却后重试

---

### 2. 暂时无法查看（永久失效）❌

**特征**：
- HTML极小：< 1KB
- `<title>该内容暂时无法查看</title>`
- 页面只有一句提示

**处理**：
- ✅ 标记为永久失效（`permanent_fail`）
- 原因：`"暂时无法查看"`

---

### 3. 根据作者隐私设置不可查看（永久失效）❌

**特征**：
- HTML大小：10-20KB
- 空的Vue应用：`<div id="app"></div>`
- 空的 `<title></title>`
- 无任何文章内容容器
- 页面显示："根据作者隐私设置，无法查看该内容"（通过JS动态加载）

**原因**：
- 作者设置了文章隐私权限
- 通常是会员专属内容

**处理**：
- ✅ 标记为永久失效（`permanent_fail`）
- 原因：`"根据作者隐私设置不可查看"`

**注意**：
- 这种页面的错误提示不在静态HTML中
- 需要检查空Vue应用 + 无内容容器 + 空title的组合特征

---

### 4. 已被发布者删除（永久失效）❌

**标记**：
- `"该内容已被发布者删除"`
- `"内容已删除"`

**处理**：
- ✅ 标记为永久失效
- 原因：`"已被发布者删除"`

---

### 5. 违规内容（永久失效）❌

**标记**：
- `"此内容因违规无法查看"`
- `"涉嫌违反相关法律法规和政策"`
- `"此内容发送失败无法查看"`
- `"接相关投诉，此内容违反"`

**处理**：
- ✅ 标记为永久失效
- 原因：`"因违规无法查看"` 或 `"涉嫌违规被限制"`

---

### 6. 第三方辟谣（永久失效）❌

**标记**：
- `"该文章已被第三方辟谣"`

**处理**：
- ✅ 标记为永久失效
- 原因：`"已被第三方辟谣"`

---

## 三、提取流程

```
获取HTML
   ↓
检查是否不可用 (is_article_unavailable)
   ├─ 是 → 标记 permanent_fail + 原因
   └─ 否 → 继续
       ↓
   检查是否有内容容器 (has_article_content)
   ├─ 否 → 标记 failed（可重试）
   └─ 是 → 继续
       ↓
   按类型提取内容
   ├─ 图文消息 (type=8) → _extract_image_text_content()
   ├─ 短内容 (type=10) → _extract_short_content()
   ├─ 音频文章 → _extract_audio_content()
   └─ 标准文章 → extract_content()
       ↓
   提取图片 (extract_images_in_order)
       ↓
   生成纯文本 (html_to_text)
       ↓
   检查是否纯图片文章
   ├─ 是 → plain_content = "[纯图片文章，共 X 张图片]"
   └─ 否 → 保持原有纯文本
       ↓
   返回结果
```

---

## 四、关键函数

### 1. `get_unavailable_reason(html) -> str | None`

检测文章是否永久不可用。

**返回值**：
- `None` - 文章正常或可重试
- `str` - 不可用原因

**检测顺序**：
1. 优先排除：验证页面
2. 静态标记：删除、违规、辟谣等
3. 特殊页面："暂时无法查看"、隐私设置页面

---

### 2. `is_audio_message(html) -> bool`

检测是否为音频文章。

**要点**：
- ✅ 匹配真实的 `<mpvoice>` 标签
- ✅ 用正则匹配 `<mp-common-mpaudio>` 标签
- ✅ 用正则匹配 `<div id="js_editor_audio_xxx">` 容器
- ❌ 不要用简单的 `in` 检查（会误判JS代码）

---

### 3. `has_article_content(html) -> bool`

快速检查HTML是否包含文章内容容器。

**容器标记**：
- `id="js_content"`
- `class="rich_media_content"`
- `id="page-content"`（政府/机构账号）
- 或特殊类型标记

---

## 五、代理和反爬策略

1. **代理池轮转**：
   - 使用 SOCKS5 代理
   - 失败后冷却120秒
   - 所有代理失败后使用直连

2. **TLS指纹伪装**：
   - 使用 `curl_cffi` 库
   - 模拟 Chrome 120 浏览器：`impersonate="chrome120"`

3. **请求头**：
   - `Referer: https://mp.weixin.qq.com/`
   - 必要时添加 `Cookie`（微信token）

---

## 六、数据库字段

### `articles` 表关键字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `status` | `VARCHAR` | 文章状态：`pending`（等待）/ `fetched`（已获取）/ `failed`（失败，可重试）/ `permanent_fail`（永久失效） |
| `fetch_retry_count` | `INTEGER` | 重试次数（最多3次） |
| `content` | `TEXT` | HTML内容 |
| `plain_content` | `TEXT` | 纯文本内容（供RSS使用） |
| `unavailable_reason` | `VARCHAR` | 不可用原因（仅 `permanent_fail` 时有值） |

---

## 七、贡献指南

如果你发现新的文章类型或错误页面，欢迎提交Issue或PR：

1. **提供详细信息**：
   - 文章URL（至少3个样本）
   - 完整的HTML源码
   - 期望的提取结果

2. **遵循代码规范**：
   - 使用严格的正则匹配（避免误判）
   - 添加详细的注释说明

3. **测试充分**：
   - 测试正常文章不受影响
   - 测试新类型能正确识别

---

**最后更新**：2026-03-24  
**维护者**：WeChat RSS API 项目组
