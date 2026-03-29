# Tuta 收信解密对接文档（FastAPI 对接版）

本文档说明如何在**另一开源项目**中复用当前仓库的收信与解密能力，包含必要文件、依赖、接口用法、代码示例与输出格式。

## 0. 你的项目调用方式（已对齐）
- HTTP：FastAPI 路由
  - 邮箱服务管理：`/email-services`
  - 注册流程：`/registration`
  - 测试服务：`/email-services/{id}/test`
- 执行方式：
  - 注册任务由 `RegistrationEngine` 同步执行
  - 批量任务通过后台线程池（非队列）
- 触发点：邮箱验证码读取由 `EmailService.get_verification_code()` 在注册流程中调用

以下文档已按这个调用方式给出具体落地建议与示例。

## 1. 功能概览
目标：给定 Tuta 邮箱账号与密码，完成以下流程并输出**解密后的纯文本邮件内容**：
1. 获取 salt
2. 登录创建 session（拿到 accessToken）
3. 拉取邮件索引与正文 blob
4. 解析 bucketKey / sessionKey
5. 解密邮件正文
6. 输出纯文本

实现入口脚本：`get_tuta_mail.py`

## 2. 必要文件清单（必须拷贝）

### Python 侧
- `get_tuta_mail.py`：收信入口脚本（已包含 HTML 清洗）
- `tuta_register.py`：Tuta API 封装 + 邮件解密核心逻辑
- `tuta_crypto_core.py`：基础对称加解密 / Argon2 逻辑
- `requirements.txt`：Python 依赖

### Node / PQ 解密侧（用于解 pubEncBucketKey）
- `pq_decrypt.mjs`
- `liboqs.wasm`
- `package/dist/*`（本仓库内置 `@tutao/tutanota-crypto` dist 产物）
- `node_modules/@tutao/tutanota-utils/dist/*`
- `node_modules/@tutao/tutanota-error/dist/*`

> 说明：PQ 解密路径依赖 `node` 运行 `pq_decrypt.mjs`。其中 `liboqs.wasm` 与 `package/dist` 必须同目录可访问。

## 3. 依赖安装

### Python 依赖
```bash
pip install -r requirements.txt
```
`requirements.txt` 至少包含：
- `curl_cffi`
- `cryptography`
- `argon2-cffi`
- `pynacl`

### Node 依赖
需要本地可执行 `node`。`node_modules` 已在仓库内，如需要单独部署，需确保：
- `@tutao/tutanota-utils` 与 `@tutao/tutanota-error` 安装完成

## 4. 使用方式（命令行）

运行：
```bash
python get_tuta_mail.py
```
交互输入：
- 邮箱
- 密码
- 二次认证码（没有则回车）

输出：
- `mail_details/mail_readable.json`
- `mail_details/mail_plain.txt`（已做 HTML 清洗）

## 5. 代码接口（供外部项目调用）

### 5.1 直接调用 Python API
建议在外部项目中直接复用 `TutaRegister` 类：

```python
from tuta_register import TutaRegister

reg = TutaRegister(proxy=None, tag="login")

# 1. 获取 salt
status, salt_data = reg.get_salt(email)
if status != 200:
    raise RuntimeError("get_salt failed")

# 2. 登录创建 session
salt_b64 = salt_data.get("422")
status, session_data = reg.create_session(email, password, salt_b64)
if status not in (200, 201):
    raise RuntimeError("create_session failed")

# 3. 获取 user
status, user = reg.get_user()
if status != 200:
    raise RuntimeError("get_user failed")

# 4. 拉取 + 解密邮件
reg.download_mail_details(
    output_dir="mail_details",
    max_mails=5,
    decrypt=True,
    password=password,
    user_data=user,
)
```

### 5.2 HTML 清洗（可复用 get_tuta_mail.py 中实现）
```python
import re, html

def _html_to_text(value: str) -> str:
    if not value:
        return ""
    text = value.replace("\r\n", "\n")
    text = re.sub(r"(?i)<br\\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\\s*>", "\n", text)
    text = re.sub(r"(?i)<p\\s*>", "", text)
    text = re.sub(r"(?i)<li\\s*>", "- ", text)
    text = re.sub(r"(?i)</li\\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
```

## 6. FastAPI 对接建议（与你们的调用方式对齐）

### 6.1 在 EmailService.get_verification_code() 内调用
建议把 Tuta 的收信解密封装成一个独立方法，返回纯文本或验证码：

```python
# src/services/tuta_mail.py (示例)
from tuta_register import TutaRegister
from pathlib import Path
import json, time, re, html

def _html_to_text(value: str) -> str:
    if not value:
        return ""
    text = value.replace("\r\n", "\n")
    text = re.sub(r"(?i)<br\\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\\s*>", "\n", text)
    text = re.sub(r"(?i)<p\\s*>", "", text)
    text = re.sub(r"(?i)<li\\s*>", "- ", text)
    text = re.sub(r"(?i)</li\\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def fetch_latest_mail_plain(email: str, password: str, max_mails: int = 5) -> str:
    reg = TutaRegister(proxy=None, tag="mail")
    status, salt_data = reg.get_salt(email)
    if status != 200:
        raise RuntimeError("get_salt failed")
    salt_b64 = salt_data.get("422")

    status, _ = reg.create_session(email, password, salt_b64)
    if status not in (200, 201):
        raise RuntimeError("create_session failed")

    status, user = reg.get_user()
    if status != 200:
        raise RuntimeError("get_user failed")

    out_dir = Path("mail_details_runtime") / f"{email.split('@')[0]}_{int(time.time())}"
    reg.download_mail_details(
        output_dir=str(out_dir),
        max_mails=max_mails,
        decrypt=True,
        password=password,
        user_data=user,
    )

    readable = out_dir / "mail_readable.json"
    if not readable.exists():
        raise RuntimeError("mail_readable.json not generated")
    data = json.loads(readable.read_text(encoding="utf-8"))
    if not data:
        return ""
    # 默认取最新一封
    body = data[0].get("body") or ""
    return _html_to_text(body)
```

你们在 `EmailService.get_verification_code()` 中直接调用 `fetch_latest_mail_plain()`，再结合规则解析验证码。

### 6.2 测试接口 /email-services/{id}/test
你们已有测试路由，可将 Tuta 收信逻辑挂到测试接口：

```python
# src/web/routes/email_services.py (示例)
from fastapi import APIRouter
from src.services.tuta_mail import fetch_latest_mail_plain

router = APIRouter()

@router.post("/email-services/{id}/test")
def test_email_service(id: str):
    # 根据 id 获取配置（邮箱/密码）
    email, password = load_from_db(id)
    plain = fetch_latest_mail_plain(email, password, max_mails=5)
    return {"ok": True, "plain": plain}
```

### 6.3 注册流程 /registration
在 `RegistrationEngine` 的同步流程中：
1. 调用 `EmailService.get_verification_code()`
2. 内部调用 `fetch_latest_mail_plain()` 拉取并解密最新邮件
3. 根据规则提取验证码并回填注册流程

## 7. 验证码提取规则（示例）
- 发件人：`@openai.com`
- 主题包含：`chatgpt`
- 正文包含 6 位数字
- 正则示例：

```python
import re

def extract_code(text: str) -> str | None:
    m = re.search(r"\b(\d{6})\b", text)
    return m.group(1) if m else None
```

## 8. 输出格式说明

### 8.1 mail_readable.json
数组结构，每封邮件包含（示例字段）：
```json
{
  "mail_id": ["listId","elementId"],
  "subject": "邮件主题",
  "received_date": "1774760601059",
  "received_date_iso": "2026-03-29T...Z",
  "sent_date": "1774760601059",
  "sent_date_iso": "2026-03-29T...Z",
  "body": "<p>HTML内容</p>"
}
```

### 8.2 mail_plain.txt
纯文本输出，每封邮件之间用分隔线：
```
邮件正文...
============================================================
```

## 9. 核心流程细节（简述）

1. `get_salt`：`/rest/sys/saltservice`
2. `create_session`：`/rest/sys/sessionservice`
3. `get_user`：`/rest/sys/user/{id}`
4. `mailboxgrouproot` → `mailbox` → `mailset` → `mailsetentry` → `mail`
5. `blobAccessToken`：`/rest/storage/blobaccesstokenservice`
6. `maildetailsblob`：`/rest/tutanota/maildetailsblob/{archive_id}?ids=...`
7. 解密逻辑：
   - 优先用 `mail._ownerEncSessionKey`
   - 若缺失，走 bucketKey：
     - 若 `groupEncBucketKey` 存在 → 用 groupKey 解
     - 若 `pubEncBucketKey` → 通过 `pq_decrypt.mjs` 解

## 10. 常见问题

- **拿不到 mail_readable.json**：通常是 `pubEncBucketKey` 解密链路缺失，确认 `pq_decrypt.mjs` 与 `liboqs.wasm` 可用。
- **node 未找到**：请保证外部项目部署环境中有 Node.js。
- **输出是 HTML**：`mail_readable.json` 内保存 HTML，`mail_plain.txt` 已清洗成文本。

## 11. 安全提示
- 不要在代码库中硬编码邮箱或密码。
- 建议外部项目使用安全的密钥管理。

---
如需把功能进一步封装成模块/API，请明确你们项目的调用方式（同步/异步、RPC/HTTP），可继续扩展此文档。
