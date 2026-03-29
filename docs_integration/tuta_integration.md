# Tuta 账号对接文档

本文件说明如何将本项目注册成功的 Tuta 邮箱账号导入到外部系统，并用于后续取件/解密。

## 0. 代码入口与依赖（TutaRegister）

**模块位置**
- 主模块：`tuta_register.py`
- 密码学实现：`tuta_crypto_core.py`

**初始化方式**
```python
from tuta_register import TutaRegister

reg = TutaRegister(
    proxy=None,          # 代理字符串，如 "socks5://ip:port"
    tag="import"         # 日志 tag，可选
)
```

**核心依赖（运行必需）**
- `curl_cffi`（请求库，伪装浏览器）
- `cryptography`
- `argon2-cffi`

**验证码识别依赖（混合方案启用时）**
- `opencv-python`
- `numpy`

说明：依赖以 `requirements.txt` 为准，本文件仅列出关键模块。

## 0.1 对接前准备清单（需对接方提供/确认）

1. `tuta_register.py` 与 `tuta_crypto_core.py` 实际代码文件（路径按文档应在仓库根目录）。
2. Tuta 服务 base URL 是否为默认值；若不是 `https://mail.tutanota.com`，请提供正确地址。  
   说明：已确认使用 `https://app.tuta.com`（见 `tuta_register.py` 的 `TutaEndpoints.BASE_URL`）。
3. 验证码邮件筛选规则是否需要微调；若需要，请提供真实邮件样例或明确发件人/主题关键词。

**对接参考文件（对方项目）**
- `src/services/tuta_mail.py`
- `src/web/routes/registration.py`
- `static/js/app.js`
- `templates/index.html`
- `requirements.txt`

## 1. 导入格式

### 1.1 简要格式（tuta_accounts.txt）
每行一条，字段用 `----` 分隔：

```
email----password----client_id----access_token
```

示例：
```
vzqfboq55@tutamail.com----vC2BdfY#BrOa!j
9j13nmlejn6@tutamail.com----Zx%huYa!7WuPzm----4_46TSHW----Z03lZIaAAUAKhGXnLKym-o4LRkrO6icYIQ
```

说明：
- 最小可用只需要 `email----password`
- `client_id` 与 `access_token` 可选，若有则便于复用会话

### 1.2 详细格式（tuta_accounts_full.jsonl）
每行一个 JSON，包含所有可用信息（推荐用于管理系统）

关键字段：
- `email`, `password`
- `access_token`, `user_id`
- `salt_b64`, `recover_code_hex`
- `captcha_token`, `captcha_answer`
- `system_keys`
- `session_raw`

## 2. 邮件读取所需信息

**最小可用（重新登录取件）：**
- `email`
- `password`

**推荐保存（可避免重新登录）：**
- `access_token`
- `user_id`

**若需解密正文（推荐保存）：**
- `email`
- `password`
- `salt_b64`（可选：也可重新拉取）

Tuta 没有 refresh_token 机制，`access_token` 过期后需重新登录。

## 3. 读取邮件内容（代码示例）

以下示例直接复用本项目的 `TutaRegister` 类进行登录与下载邮件。

```python
from tuta_register import TutaRegister

email = "vzqfboq55@tutamail.com"
password = "vC2BdfY#BrOa!j"

reg = TutaRegister(proxy=None, tag="import")

# 1. 获取 salt
status, salt_data = reg.get_salt(email)
if status != 200:
    raise RuntimeError(f"get_salt failed: {status} {salt_data}")

salt_b64 = salt_data.get("422")

# 2. 创建 session（登录）
status, session_data = reg.create_session(email, password, salt_b64)
if status not in (200, 201):
    raise RuntimeError(f"create_session failed: {status} {session_data}")

# 3. 拉取并解密邮件（max_mails 可调整）
reg.download_mail_details(output_dir="mail_details", max_mails=5, decrypt=True, password=password)
```

输出文件：
- `mail_details/mail_index.json`
- `mail_details/mail_readable.json`
- `mail_details/mail_readable.txt`

## 4. 接入建议

- 管理系统可优先解析 `tuta_accounts_full.jsonl`。
- 若只保留简要格式，至少保证 `email + password` 存在。
- `access_token` 过期后，应重新登录获取新 token。

## 5. 对接字段映射（建议）

| 外部系统字段 | 本项目字段 |
|---|---|
| email | email |
| password | password |
| client_id | client_id |
| access_token | access_token |
| user_id | user_id |
| salt | salt_b64 |
| recover_code | recover_code_hex |

## 6. 登录/取件 API 细节（接口地址、请求头、认证流程、返回结构）

**基础请求头**
- `v`: 模型版本号（sys=146, tutanota=107 等）
- `cv`: 客户端版本号（如 `340.260326.1`）
- `Content-Type: application/json`
- `accessToken`: 登录后调用取件接口时必需

**登录流程**
1. 获取盐值  
   `GET /rest/sys/saltservice`  
   Query `_body`：
   ```json
   {"418":"0","419":"user@tutamail.com"}
   ```
   返回字段：
   - `422`: `salt_b64`
   - `2133`: KDF 版本（1=Argon2id）

2. 创建 Session  
   `POST /rest/sys/sessionservice`  
   Body：
   ```json
   {
     "1212":"0",
     "1213":"user@tutamail.com",
     "1214":"auth_verifier_b64url",
     "1215":"Chrome Browser",
     "1216":null,
     "1217":null,
     "1218":[],
     "1417":null
   }
   ```
   返回字段：
   - `1221`: `access_token`
   - `1223`: `user_id`

**取件流程**
- `GET /rest/sys/user/{user_id}`
- `GET /rest/tutanota/mailboxgrouproot/{mail_group_id}`
- `GET /rest/tutanota/mailbox/{mailbox_id}`
- `GET /rest/tutanota/mailset/{mailset_list_id}?start=0&count=...`
- `GET /rest/tutanota/mailsetentry/{entry_list_id}?start=0&count=...`
- `GET /rest/tutanota/mail/{mail_list_id}?ids=...`
- `POST /rest/storage/blobaccesstokenservice`
- `GET /rest/tutanota/maildetailsblob/{archive_id}?ids=...&blobAccessToken=...`

以上流程在 `TutaRegister.download_mail_details()` 已封装。

## 7. 验证码邮件筛选规则与解析方式

项目目前**没有内置验证码邮件解析逻辑**，仅提供邮件下载与解密。  
建议对接系统按以下**可配置规则模板**筛选（需结合真实邮件样例微调）：

- 发件人包含：`tuta` 或 `tutanota`（大小写不敏感）
- 主题包含：`verification`、`confirm`、`code`、`验证码`
- 正文正则：`\\b\\d{4,8}\\b`（提取 4~8 位数字）

解析方式建议：
- 先按发件人/主题过滤，再在正文中用正则提取验证码
- 若正文为 HTML，先剥离标签再提取

### 7.1 OpenAI 验证码提取规则（基于用户样例，建议可配置）

**过滤条件**
- 发件人域名包含：`openai.com`（样例为 `noreply@tm.openai.com`）
- 主题包含：`chatgpt`（大小写不敏感），样例为“你的 ChatGPT 代码为 XXXXXX”
- 可选辅助关键词：`verification`、`code`、`security`

**提取正则**
- `\\b\\d{6}\\b`（优先提取 6 位数字验证码）

**建议策略**
- 该样例邮件仅有 `text/html`，无 `text/plain`，建议先做 HTML 剥离再提取
- 若同时命中 `openai.com` 且正文包含 6 位数字，则取首个匹配值作为验证码
- 可加一句上下文锚点提高准确率：在“验证码/代码/临时验证码”附近优先取值

## 8. 解密所需字段来源与算法细节

**字段来源**
- `salt_b64`：来自 `GET /rest/sys/saltservice` 返回字段 `422`
- `system_keys`：来自 `GET /rest/sys/systemkeysservice`，用于注册时加密系统密钥
- `recover_code_hex`：注册时本地生成（`TutaCryptoCore.generate_registration_payload`）
- `access_token`、`user_id`：来自 `POST /rest/sys/sessionservice`

**算法要点（实现已在 `tuta_crypto_core.py`）**
- `salt_b64` + `password` → Argon2id 派生 `passphrase_key`
- `auth_verifier`：对 `passphrase_key` 做验证器派生（用于登录）
- 邮件解密流程：`passphrase_key` → `user_group_key` → `mail_group_key` → `session_key` → 解密 `subject` 与 `body`

## 9. 登录/取件是否需要验证码或人机验证

**注册阶段**
- `TimeLockCaptcha`：`/rest/sys/timelockcaptchaservice`，本地计算 puzzle 解
- `RegistrationCaptcha`：`/rest/sys/registrationcaptchaservice`，先 GET 获取图片与 `captcha_token`，再 POST 提交 `HH:MM` 字符串答案；本项目使用 `CaptchaTimeSolver`（OpenCV + 视觉模型）自动识别

**登录/取件阶段**
- 正常情况下**不需要验证码**  
- 如果启用了 2FA，`create_session` 的 `1217` 需填写二次认证 token  
  当前项目未实现 2FA 处理逻辑，需要对接方自行扩展

---

如需批量导入脚本或 API 对接代码，可继续在此基础上扩展。
