"""
Tuta 邮箱批量自动注册工具 (基础脚手架)
依赖: pip install curl_cffi pynacl cryptography argon2-cffi
功能: 自动注册 Tuta 免费邮箱账号

注册流程:
  1. TimeLockCaptcha 时间锁验证
  2. 检查邮箱地址可用性
  3. RegistrationCaptcha 注册验证码（提交 TimeLock 解）
  4. 获取系统公钥 (RSA / Kyber)
  5. 本地生成加密密钥 (Argon2 + AES + RSA + Kyber)
  6. 创建客户账号 (POST customeraccountservice)
  7. 获取 Salt
  8. 创建 Session (登录)
"""

import os
import re
import uuid
import json
import random
import string
import time
import sys
import base64
import hashlib
import struct
import math
import subprocess
import tempfile
from pathlib import Path
from datetime import datetime
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlencode, quote
import urllib.request
import urllib.error

from curl_cffi import requests as curl_requests

# ================= 加载配置 =================
def _load_config():
    """从 config.json 加载配置，环境变量优先级更高"""
    config = {
        "total_accounts": 3,        # 默认注册数量
        "proxy": "",                # 固定代理地址
        "proxy_mode": "local",      # local | dynamic | none
        "dynamic_proxy_url": "",    # 动态代理获取链接
        "dynamic_proxy_protocol": "socks5",  # socks5 | http | https
        "output_file": "tuta_accounts.txt",  # 输出文件
        "output_file_detail": "tuta_accounts_full.jsonl",  # 详细输出文件
        "mail_domain": "tutamail.com",       # 邮箱域名 (tutamail.com / tuta.com / tutanota.com)
        "language": "en",                     # 注册语言
        # 验证码识别配置
        "captcha_auto": False,                # 自动识别验证码
        "captcha_api_key": "",                # 识别服务 API Key
        "captcha_base_url": "https://grok.ksxuemm.serv00.net/v1",
        "captcha_model": "gpt-5.2",
        "captcha_only": False,                # 仅保存验证码图片，不提交答案
        "captcha_code": "",                   # 手动指定验证码答案（优先级高）
        "captcha_max_attempts": 5,            # 自动识别最多尝试次数
        # 视觉识别（混合方案）配置
        "vision_enabled": True,
        "vision_api_key": "",
        "vision_base_url": "",
        "vision_model": "",
        "vision_resize_enabled": True,
        "vision_resize_max": 256,
        "vision_resize_width": 0,
        "vision_resize_height": 0,
        "vision_resize_ref": "",              # 参考缩略图路径
        "vision_crop_mode": "auto",           # auto | full
        "vision_save_thumbs": True,
        "vision_thumb_dir": "captchas/_thumbs",
        # 动态代理相关
        "dynamic_proxy_max_attempts": 5,
    }

    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                file_config = json.load(f)
                config.update(file_config)
        except Exception as e:
            print(f"⚠️ 加载 config.json 失败: {e}")

    # 环境变量优先级更高
    config["proxy"] = os.environ.get("PROXY", config["proxy"])
    config["total_accounts"] = int(os.environ.get("TOTAL_ACCOUNTS", config["total_accounts"]))

    return config


_CONFIG = _load_config()
DEFAULT_TOTAL_ACCOUNTS = _CONFIG["total_accounts"]
DEFAULT_PROXY = _CONFIG["proxy"]
DEFAULT_PROXY_MODE = _CONFIG.get("proxy_mode", "local")
DEFAULT_DYNAMIC_PROXY_URL = _CONFIG.get("dynamic_proxy_url", "")
DEFAULT_DYNAMIC_PROXY_PROTOCOL = _CONFIG.get("dynamic_proxy_protocol", "socks5")
DEFAULT_OUTPUT_FILE = _CONFIG["output_file"]
DEFAULT_OUTPUT_FILE_DETAIL = _CONFIG.get("output_file_detail", "tuta_accounts_full.jsonl")
DEFAULT_MAIL_DOMAIN = _CONFIG["mail_domain"]
DEFAULT_LANGUAGE = _CONFIG["language"]
DEFAULT_CAPTCHA_AUTO = _CONFIG["captcha_auto"]
DEFAULT_CAPTCHA_API_KEY = _CONFIG["captcha_api_key"]
DEFAULT_CAPTCHA_BASE_URL = _CONFIG["captcha_base_url"]
DEFAULT_CAPTCHA_MODEL = _CONFIG["captcha_model"]
DEFAULT_CAPTCHA_ONLY = _CONFIG["captcha_only"]
DEFAULT_CAPTCHA_CODE = _CONFIG["captcha_code"]
DEFAULT_CAPTCHA_MAX_ATTEMPTS = _CONFIG["captcha_max_attempts"]
DEFAULT_VISION_ENABLED = _CONFIG["vision_enabled"]
DEFAULT_VISION_API_KEY = _CONFIG["vision_api_key"]
DEFAULT_VISION_BASE_URL = _CONFIG["vision_base_url"]
DEFAULT_VISION_MODEL = _CONFIG["vision_model"]
DEFAULT_VISION_RESIZE_ENABLED = _CONFIG["vision_resize_enabled"]
DEFAULT_VISION_RESIZE_MAX = _CONFIG["vision_resize_max"]
DEFAULT_VISION_RESIZE_WIDTH = _CONFIG["vision_resize_width"]
DEFAULT_VISION_RESIZE_HEIGHT = _CONFIG["vision_resize_height"]
DEFAULT_VISION_RESIZE_REF = _CONFIG["vision_resize_ref"]
DEFAULT_VISION_CROP_MODE = _CONFIG["vision_crop_mode"]
DEFAULT_VISION_SAVE_THUMBS = _CONFIG["vision_save_thumbs"]
DEFAULT_VISION_THUMB_DIR = _CONFIG["vision_thumb_dir"]
DEFAULT_DYNAMIC_PROXY_MAX_ATTEMPTS = _CONFIG.get("dynamic_proxy_max_attempts", 5)

# 全局线程锁
_print_lock = threading.Lock()
_file_lock = threading.Lock()


# ================= Tuta API 常量 =================

# Tuta 使用数字 ID 作为字段名，这里定义语义化别名
class TutaApiVersion:
    """API 版本号常量"""
    SYS = "146"           # sys 模型版本
    TUTANOTA = "107"      # tutanota 模型版本
    STORAGE = "14"        # storage 模型版本
    MONITOR = "39"        # monitor 模型版本
    BASE = "2"            # base 模型版本
    CLIENT = "340.260326.1"  # 客户端版本号


class TutaEndpoints:
    """Tuta REST API 端点"""
    BASE_URL = "https://app.tuta.com"

    # 注册相关
    TIMELOCK_CAPTCHA    = "/rest/sys/timelockcaptchaservice"
    MAIL_AVAILABILITY   = "/rest/sys/multiplemailaddressavailabilityservice"
    REG_CAPTCHA         = "/rest/sys/registrationcaptchaservice"
    SYSTEM_KEYS         = "/rest/sys/systemkeysservice"
    CUSTOMER_ACCOUNT    = "/rest/tutanota/customeraccountservice"
    SALT                = "/rest/sys/saltservice"
    SESSION             = "/rest/sys/sessionservice"

    # 登录后使用
    USER                = "/rest/sys/user"
    GROUP_INFO          = "/rest/sys/groupinfo"
    ROOT_INSTANCE       = "/rest/sys/rootinstance"
    ROLLOUT             = "/rest/sys/rolloutservice"
    IDENTITY_KEY        = "/rest/sys/identitykeyservice"
    PUBLIC_KEY           = "/rest/sys/publickeyservice"
    BLOB_ACCESS_TOKEN    = "/rest/storage/blobaccesstokenservice"


# Tuta 可用的免费邮箱域名
TUTA_FREE_DOMAINS = ["tutamail.com", "tuta.com", "tutanota.com", "tutanota.de", "keemail.me"]


# ================= 浏览器指纹 =================

# Chrome 指纹配置: impersonate 与 sec-ch-ua 必须匹配真实浏览器
_CHROME_PROFILES = [
    {
        "major": 131, "impersonate": "chrome131",
        "build": 6778, "patch_range": (69, 205),
        "sec_ch_ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    },
    {
        "major": 133, "impersonate": "chrome133a",
        "build": 6943, "patch_range": (33, 153),
        "sec_ch_ua": '"Not(A:Brand";v="99", "Google Chrome";v="133", "Chromium";v="133"',
    },
    {
        "major": 136, "impersonate": "chrome136",
        "build": 7103, "patch_range": (48, 175),
        "sec_ch_ua": '"Chromium";v="136", "Google Chrome";v="136", "Not.A/Brand";v="99"',
    },
    {
        "major": 142, "impersonate": "chrome142",
        "build": 7540, "patch_range": (30, 150),
        "sec_ch_ua": '"Chromium";v="142", "Google Chrome";v="142", "Not_A Brand";v="99"',
    },
]


def _random_chrome_version():
    """随机选择 Chrome 版本配置"""
    profile = random.choice(_CHROME_PROFILES)
    major = profile["major"]
    build = profile["build"]
    patch = random.randint(*profile["patch_range"])
    full_ver = f"{major}.0.{build}.{patch}"
    ua = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{full_ver} Safari/537.36"
    return profile["impersonate"], major, full_ver, ua, profile["sec_ch_ua"]


def _random_delay(low=0.3, high=1.0):
    """随机延迟，模拟人类操作"""
    time.sleep(random.uniform(low, high))


def _fetch_dynamic_proxy(url: str, protocol: str = "socks5") -> str:
    """从动态代理接口获取代理地址，返回带协议的代理串"""
    if not url:
        raise ValueError("动态代理链接为空")
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            text = resp.read().decode("utf-8", errors="ignore").strip()
    except Exception as e:
        raise RuntimeError(f"动态代理请求失败: {e}") from e
    if not text:
        raise RuntimeError("动态代理返回为空")
    # 取第一条
    first = re.split(r"[\r\n,\s;]+", text)[0].strip()
    if not first:
        raise RuntimeError("动态代理解析失败")
    proto = (protocol or "").lower().strip()
    if proto in ("http", "https", "socks5"):
        return f"{proto}://{first}"
    # 默认不加协议
    return first


def _is_network_error(msg: str) -> bool:
    if not msg:
        return False
    m = msg.lower()
    keywords = [
        "proxy", "connection", "timed out", "timeout", "tls", "ssl",
        "reset", "refused", "unreachable", "failed to establish",
        "name or service not known", "host", "network",
    ]
    return any(k in m for k in keywords)


# ================= 通用工具函数 =================

def _generate_password(length=14):
    """生成随机密码（包含大小写字母、数字和特殊字符）"""
    lower = string.ascii_lowercase
    upper = string.ascii_uppercase
    digits = string.digits
    special = "!@#$%&*"
    pwd = [random.choice(lower), random.choice(upper),
           random.choice(digits), random.choice(special)]
    all_chars = lower + upper + digits + special
    pwd += [random.choice(all_chars) for _ in range(length - 4)]
    random.shuffle(pwd)
    return "".join(pwd)


def _random_email_prefix(length=None):
    """生成随机邮箱前缀 (6~12 位字母数字)"""
    if length is None:
        length = random.randint(6, 12)
    chars = string.ascii_lowercase + string.digits
    return "".join(random.choice(chars) for _ in range(length))


def _generate_client_id():
    """生成 Tuta 客户端 ID (8 字符随机字符串)
    
    在 HAR 中观察到的格式: "KL44hZP3" (Base64-like, 8字符)
    """
    # 生成 6 字节随机数据，然后 base64 编码得到 8 字符
    raw = os.urandom(6)
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")[:8]


def _generate_random_id():
    """生成 Tuta 风格的随机 ID (6 字符)
    
    HAR 中的 ID 格式: "4WytpQ", "GlRqfw", "LB7nLw" 等
    """
    raw = os.urandom(4)
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")[:6]


def _encode_query_body(body_dict):
    """将字典编码为 Tuta 的 _body URL 参数格式
    
    Tuta 的 GET 请求通过 URL 参数 _body 传递 JSON 数据
    """
    json_str = json.dumps(body_dict, separators=(',', ':'))
    return quote(json_str)


def _base64_to_base64url(b64_str):
    """标准 Base64 转 Base64url (用于 sessionservice)"""
    return b64_str.replace("+", "-").replace("/", "_").rstrip("=")


def _base64url_to_base64(b64url_str):
    """Base64url 转标准 Base64"""
    s = b64url_str.replace("-", "+").replace("_", "/")
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return s


def _b64decode_any(val):
    """兼容 Base64/Base64url 的解码"""
    if val is None:
        return None
    if isinstance(val, (bytes, bytearray)):
        return bytes(val)
    if not isinstance(val, str):
        return None
    s = val.strip()
    if s == "":
        return b""
    try:
        return base64.b64decode(s, validate=False)
    except Exception:
        try:
            return base64.b64decode(_base64url_to_base64(s), validate=False)
        except Exception:
            return None


def _ms_to_iso(ms_val):
    try:
        return datetime.utcfromtimestamp(int(ms_val) / 1000).isoformat() + "Z"
    except Exception:
        return None


# ================= 加密工具（核心，需逆向补全） =================

class TutaCrypto:
    """Tuta 端到端加密工具
    
    Tuta 的加密体系:
    1. Argon2id: 将用户密码派生为 passphrase key (32字节)
    2. AES-128-CBC / AES-256-CBC: 用于加密各种 group keys
    3. RSA-2048: 非对称加密
    4. ML-KEM (Kyber-1024): 后量子密码学，密钥封装
    
    密钥层级:
    - password → (Argon2id + salt) → passphrase_key (32B)
    - passphrase_key → encrypt → userGroupKey
    - userGroupKey → encrypt → adminGroupKey
    - adminGroupKey → encrypt → customerGroupKey
    - 每个 group 有对应的 RSA/Kyber 密钥对
    
    TODO: 需要逆向 Tuta 前端 JS 来完善以下方法
    """

    # KDF 版本常量
    KDF_BCRYPT = "0"
    KDF_ARGON2ID = "1"

    @staticmethod
    def generate_salt(length=16):
        """生成随机 salt"""
        return os.urandom(length)

    @staticmethod
    def argon2id_hash(password: str, salt: bytes) -> bytes:
        """使用 Argon2id 派生密钥
        
        Tuta 的 Argon2id 参数（需要逆向确认）:
        - 内存: 64MB (65536 KiB)
        - 迭代: 4
        - 并行度: 1
        - 输出长度: 32 字节
        
        TODO: 需要加载 argon2.wasm 或使用 argon2-cffi 库
        """
        try:
            import argon2
            # 使用 argon2-cffi 库进行计算
            # 参数需要逆向 Tuta 前端确认
            hasher = argon2.low_level.hash_secret_raw(
                secret=password.encode("utf-8"),
                salt=salt,
                time_cost=4,
                memory_cost=65536,  # 64MB
                parallelism=1,
                hash_len=32,
                type=argon2.low_level.Type.ID,
            )
            return hasher
        except ImportError:
            # 占位: 如果没有 argon2-cffi，用 SHA-256 临时替代
            print("⚠️ argon2-cffi 未安装，使用 SHA-256 占位（不可用于真实注册）")
            return hashlib.sha256(password.encode() + salt).digest()

    @staticmethod
    def generate_aes_key(bits=128):
        """生成 AES 密钥
        
        Tuta 使用:
        - AES-128: 用于 group keys
        - AES-256: 用于某些高安全场景
        """
        return os.urandom(bits // 8)

    @staticmethod
    def aes_encrypt(key: bytes, plaintext: bytes, iv: bytes = None) -> bytes:
        """AES-CBC 加密
        
        Tuta 的 AES 加密格式:
        - 第一个字节表示加密版本/类型标记
        - 后跟 IV (16 字节) + 密文
        
        TODO: 需要确认 Tuta 的具体 AES padding 和格式
        """
        if iv is None:
            iv = os.urandom(16)
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            from cryptography.hazmat.primitives import padding as sym_padding
            
            # PKCS7 填充
            padder = sym_padding.PKCS7(128).padder()
            padded = padder.update(plaintext) + padder.finalize()
            
            cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
            encryptor = cipher.encryptor()
            ciphertext = encryptor.update(padded) + encryptor.finalize()
            
            # Tuta 格式: 版本标记(1B) + IV(16B) + 密文
            # 版本标记 0x01 = AES-128-CBC
            version_byte = b'\x01' if len(key) == 16 else b'\x02'
            return version_byte + iv + ciphertext
        except ImportError:
            print("⚠️ cryptography 库未安装")
            return b'\x01' + iv + plaintext  # 占位

    @staticmethod
    def aes_decrypt(key: bytes, ciphertext: bytes) -> bytes:
        """AES-CBC 解密
        
        TODO: 与 aes_encrypt 对应
        """
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            from cryptography.hazmat.primitives import padding as sym_padding
            
            version = ciphertext[0]
            iv = ciphertext[1:17]
            ct = ciphertext[17:]
            
            cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
            decryptor = cipher.decryptor()
            padded = decryptor.update(ct) + decryptor.finalize()
            
            unpadder = sym_padding.PKCS7(128).unpadder()
            return unpadder.update(padded) + unpadder.finalize()
        except ImportError:
            return ciphertext[17:]  # 占位

    @staticmethod
    def generate_rsa_keypair():
        """生成 RSA-2048 密钥对
        
        TODO: Tuta 可能使用自定义 RSA 参数格式
        需要逆向确认密钥的序列化方式
        """
        try:
            from cryptography.hazmat.primitives.asymmetric import rsa
            from cryptography.hazmat.primitives import serialization
            
            private_key = rsa.generate_private_key(
                public_exponent=65537,
                key_size=2048,
            )
            return private_key
        except ImportError:
            print("⚠️ cryptography 库未安装，RSA 密钥生成不可用")
            return None

    @staticmethod
    def generate_kyber_keypair():
        """生成 ML-KEM (Kyber) 密钥对
        
        Tuta 使用后量子密码学 Kyber-1024 进行密钥封装
        前端通过 liboqs.wasm 执行此操作
        
        TODO: 需要使用 liboqs-python 或模拟 WASM 调用
        Python 可选方案:
        - pip install oqs (Open Quantum Safe)
        - 或者直接加载 liboqs.wasm
        """
        print("⚠️ Kyber 密钥生成尚未实现（需要 liboqs 库）")
        return None, None  # (public_key, secret_key)

    @classmethod
    def derive_passphrase_key(cls, password: str, salt: bytes) -> bytes:
        """从密码派生 passphrase key
        
        这是注册流程中最关键的一步:
        passphrase_key = Argon2id(password, salt)
        
        后续所有密钥都通过此 key 加密
        """
        return cls.argon2id_hash(password, salt)

    @classmethod
    def generate_all_keys(cls, password: str):
        """生成注册所需的全部密钥
        
        返回一个字典，包含注册请求（customeraccountservice）需要的所有密钥数据
        
        密钥生成流程:
        1. 生成 salt (16B)
        2. passphrase_key = Argon2id(password, salt)
        3. userGroupKey = random AES-128 key
        4. adminGroupKey = random AES-128 key
        5. customerGroupKey = random AES-128 key
        6. mailGroupKey = random AES-128 key
        7. contactGroupKey = random AES-128 key
        8. fileGroupKey = random AES-128 key
        9. 为 userGroup 生成 RSA + Kyber 密钥对
        10. 为 adminGroup 生成 RSA + Kyber 密钥对
        11. 为 customerGroup 生成 RSA + Kyber 密钥对
        12. 用 passphrase_key 加密 userGroupKey
        13. 用 userGroupKey 加密 adminGroupKey
        14. 用 adminGroupKey 加密 customerGroupKey
        15. 用各 groupKey 加密对应的 RSA 私钥和 Kyber 私钥
        
        TODO: 完整实现需要逆向 Tuta 前端 JS
        """
        # 步骤 1-2: 生成 salt 和 passphrase key
        salt = cls.generate_salt(16)
        passphrase_key = cls.derive_passphrase_key(password, salt)

        # 步骤 3-8: 生成各种 group keys
        user_group_key = cls.generate_aes_key(128)
        admin_group_key = cls.generate_aes_key(128)
        customer_group_key = cls.generate_aes_key(128)
        mail_group_key = cls.generate_aes_key(128)
        contact_group_key = cls.generate_aes_key(128)
        file_group_key = cls.generate_aes_key(128)

        # 步骤 9-11: 生成 RSA + Kyber 密钥对
        # TODO: 实际实现
        user_rsa = cls.generate_rsa_keypair()
        admin_rsa = cls.generate_rsa_keypair()
        customer_rsa = cls.generate_rsa_keypair()

        user_kyber_pub, user_kyber_sk = cls.generate_kyber_keypair()
        admin_kyber_pub, admin_kyber_sk = cls.generate_kyber_keypair()
        customer_kyber_pub, customer_kyber_sk = cls.generate_kyber_keypair()

        # 步骤 12-15: 加密密钥链
        encrypted_user_group_key = cls.aes_encrypt(passphrase_key, user_group_key)
        encrypted_admin_group_key = cls.aes_encrypt(user_group_key, admin_group_key)
        encrypted_customer_group_key = cls.aes_encrypt(admin_group_key, customer_group_key)
        encrypted_mail_group_key = cls.aes_encrypt(user_group_key, mail_group_key)
        encrypted_contact_group_key = cls.aes_encrypt(user_group_key, contact_group_key)
        encrypted_file_group_key = cls.aes_encrypt(user_group_key, file_group_key)

        return {
            "salt": salt,
            "passphrase_key": passphrase_key,
            "user_group_key": user_group_key,
            "admin_group_key": admin_group_key,
            "customer_group_key": customer_group_key,
            "mail_group_key": mail_group_key,
            "contact_group_key": contact_group_key,
            "file_group_key": file_group_key,
            # 加密后的密钥
            "encrypted_user_group_key": encrypted_user_group_key,
            "encrypted_admin_group_key": encrypted_admin_group_key,
            "encrypted_customer_group_key": encrypted_customer_group_key,
            "encrypted_mail_group_key": encrypted_mail_group_key,
            "encrypted_contact_group_key": encrypted_contact_group_key,
            "encrypted_file_group_key": encrypted_file_group_key,
            # RSA 密钥对
            "user_rsa": user_rsa,
            "admin_rsa": admin_rsa,
            "customer_rsa": customer_rsa,
            # Kyber 密钥对
            "user_kyber_pub": user_kyber_pub,
            "user_kyber_sk": user_kyber_sk,
            "admin_kyber_pub": admin_kyber_pub,
            "admin_kyber_sk": admin_kyber_sk,
            "customer_kyber_pub": customer_kyber_pub,
            "customer_kyber_sk": customer_kyber_sk,
        }


# ================= TimeLock Puzzle 求解器 =================

class TimeLockSolver:
    """TimeLock Puzzle 求解器
    
    Tuta 使用 TimeLock Puzzle 作为反自动化 / PoW 机制。
    客户端需要在一定时间内解出一个大数计算。
    """

    @staticmethod
    def solve(puzzle_params: dict) -> str:
        """解 TimeLock Puzzle
        
        计算算法:
            result = base ^ (2^difficulty) mod modulus
            即 e = e^2 mod modulus, 循环 difficulty 次
        """
        try:
            # 根据 HAR 请求提取字段
            base_str = puzzle_params.get("2636", "0")
            diff_str = puzzle_params.get("2634", "0")
            mod_str = puzzle_params.get("2635", "0")
            
            base = int(base_str)
            difficulty = int(diff_str)
            modulus = int(mod_str)
            
            if not base or not difficulty or not modulus:
                return "0"
                
            e = base
            # 重复平方计算
            for _ in range(difficulty):
                e = (e * e) % modulus
                
            return str(e)
        except Exception as e:
            print(f"⚠️ TimeLock 计算异常: {e}")
            return "0"


# ================= Captcha Time 识别器 =================

def _is_truthy(val) -> bool:
    if val is None:
        return False
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ("1", "true", "yes", "y", "on")


def _should_auto_solve_captcha() -> bool:
    """是否启用自动识别验证码"""
    v = _CONFIG.get("captcha_auto", DEFAULT_CAPTCHA_AUTO)
    if v is not None:
        return _is_truthy(v)
    # 未显式设置时，只有配置了 API Key 才尝试自动识别
    return bool(_CONFIG.get("captcha_api_key", DEFAULT_CAPTCHA_API_KEY))


def _pick_captcha_image(images):
    """从候选图中挑一张质量更高的"""
    if not images:
        return None
    def score(item):
        name, b64 = item
        name = name or ""
        b64_len = len(b64) if isinstance(b64, str) else 0
        prefer = 0
        if "captcha_1" in name:
            prefer = 2
        elif "captcha" in name:
            prefer = 1
        return (prefer, b64_len)
    # 先按 prefer，再按 b64 长度选最大
    return sorted(images, key=score, reverse=True)[0]


class CaptchaTimeSolver:
    """用于识别 Tuta 时钟验证码的两步识别器（昼夜 + 时间）"""

    BASE_URL = DEFAULT_CAPTCHA_BASE_URL
    API_KEY = DEFAULT_CAPTCHA_API_KEY
    MODEL = DEFAULT_CAPTCHA_MODEL
    _ref_size_cache = None

    @classmethod
    def _extract_json(cls, text: str):
        if not text:
            raise ValueError("Empty model response")
        text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise ValueError(f"No JSON object found in response: {text[:200]}")
        return json.loads(m.group(0))

    @classmethod
    def _resolve_chat_url(cls, base_url: str) -> str:
        if not base_url:
            return ""
        url = str(base_url).rstrip("/")
        if url.endswith("/chat/completions"):
            return url
        return url + "/chat/completions"

    @classmethod
    def _vision_cfg(cls):
        api_key = str(_CONFIG.get("vision_api_key", DEFAULT_VISION_API_KEY) or "").strip()
        base_url = str(_CONFIG.get("vision_base_url", DEFAULT_VISION_BASE_URL) or "").strip()
        model = str(_CONFIG.get("vision_model", DEFAULT_VISION_MODEL) or "").strip()
        if not api_key:
            api_key = str(_CONFIG.get("captcha_api_key", cls.API_KEY) or "").strip()
        if not base_url:
            base_url = str(_CONFIG.get("captcha_base_url", cls.BASE_URL) or "").strip()
        if not model:
            model = str(_CONFIG.get("captcha_model", cls.MODEL) or "").strip()
        return api_key, base_url, model

    @classmethod
    def _chat(cls, messages, timeout: int = 60) -> str:
        api_key, base_url, model = cls._vision_cfg()
        if not api_key:
            raise RuntimeError("Missing API key. Set captcha_api_key or vision_api_key in config.json.")
        url = cls._resolve_chat_url(base_url)
        if not url:
            raise RuntimeError("Missing base URL. Set captcha_base_url or vision_base_url in config.json.")
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": 200,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                resp_body = resp.read().decode("utf-8")
                if resp.status != 200:
                    raise RuntimeError(f"HTTP {resp.status}: {resp_body}")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"HTTP {e.code}: {body}") from e
        data = json.loads(resp_body)
        return data["choices"][0]["message"]["content"]

    @classmethod
    def _classify_day_night(cls, image_b64: str):
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a captcha visual inspector. Only classify day or night. "
                    "Reply with strict JSON: {\"day_night\":\"day|night\",\"confidence\":0-1}."
                ),
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Classify this image as day or night. JSON only."},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
                ],
            },
        ]
        text = cls._chat(messages)
        obj = cls._extract_json(text)
        day_night = str(obj.get("day_night", "")).lower().strip()
        conf = float(obj.get("confidence", 0))
        if day_night not in ("day", "night"):
            raise ValueError(f"Invalid day_night: {day_night}")
        return day_night, conf

    @classmethod
    def _read_time(cls, image_b64: str, day_night: str) -> str:
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a captcha clock reader. Given day/night, read the clock hands. "
                    "Minutes are multiples of 5. "
                    "Reply with strict JSON: {\"time\":\"HH:MM\"} in 24-hour format."
                ),
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"Day/night: {day_night}. "
                            "Treat this as an analog clock. Identify the hour and minute hands and tell the time. "
                            "JSON only."
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
                ],
            },
        ]
        text = cls._chat(messages)
        obj = cls._extract_json(text)
        time_str = str(obj.get("time", "")).strip()
        if not re.match(r"^\d{2}:\d{2}$", time_str):
            raise ValueError(f"Invalid time format: {time_str}")
        return time_str

    @classmethod
    def _load_ref_size(cls):
        if cls._ref_size_cache is not None:
            return cls._ref_size_cache
        ref_path = str(_CONFIG.get("vision_resize_ref", DEFAULT_VISION_RESIZE_REF) or "").strip()
        if not ref_path:
            cls._ref_size_cache = None
            return None
        if not os.path.isabs(ref_path):
            ref_path = os.path.join(os.getcwd(), ref_path)
        if not os.path.exists(ref_path):
            cls._ref_size_cache = None
            return None
        try:
            import cv2
            ref_img = cv2.imread(ref_path)
            if ref_img is None:
                cls._ref_size_cache = None
                return None
            h, w = ref_img.shape[:2]
            cls._ref_size_cache = (int(w), int(h))
            return cls._ref_size_cache
        except Exception:
            cls._ref_size_cache = None
            return None

    @classmethod
    def _resize_for_vision(cls, img, max_side: int, target_size):
        try:
            import cv2
        except Exception as e:
            raise RuntimeError("缺少 opencv-python，无法进行缩放处理") from e
        h, w = img.shape[:2]
        if target_size:
            tw, th = target_size
            if tw > 0 and th > 0:
                return cv2.resize(img, (tw, th), interpolation=cv2.INTER_AREA)
        if max_side <= 0:
            return img
        scale = max(h, w) / float(max_side)
        if scale <= 1.0:
            return img
        new_w = int(round(w / scale))
        new_h = int(round(h / scale))
        return cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)

    @classmethod
    def _detect_clock(cls, img):
        import cv2
        import numpy as np
        h, w = img.shape[:2]
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        circles = cv2.HoughCircles(
            gray,
            cv2.HOUGH_GRADIENT,
            dp=1.2,
            minDist=100,
            param1=50,
            param2=30,
            minRadius=min(w, h) // 4,
            maxRadius=min(w, h) // 2,
        )
        if circles is not None and len(circles) > 0:
            circles = np.uint16(np.around(circles))
            x, y, r = circles[0][0]
            return (int(x), int(y)), int(r)
        return (w // 2, h // 2), min(w, h) // 2

    @staticmethod
    def _angle_clock(center, tip):
        cx, cy = center
        tx, ty = tip
        dx = tx - cx
        dy = ty - cy
        dy_up = -dy
        deg = (math.degrees(math.atan2(dy_up, dx)) % 360)
        return (90 - deg) % 360

    @staticmethod
    def _ang_dist(a, b):
        d = abs(a - b) % 360
        return min(d, 360 - d)

    @classmethod
    def _opencv_detect(cls, img):
        import cv2
        import numpy as np
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blurred, 50, 150, apertureSize=3)
        kernel = np.ones((3, 3), np.uint8)
        edges = cv2.dilate(edges, kernel, iterations=1)
        edges = cv2.erode(edges, kernel, iterations=1)

        center, radius = cls._detect_clock(img)
        lines = cv2.HoughLinesP(
            edges,
            rho=1,
            theta=np.pi / 180,
            threshold=80,
            minLineLength=max(10, int(radius * 0.3)),
            maxLineGap=20,
        )
        hands = []
        if lines is not None:
            for line in lines:
                x1, y1, x2, y2 = line[0]
                length = math.hypot(x2 - x1, y2 - y1)
                d1 = math.hypot(x1 - center[0], y1 - center[1])
                d2 = math.hypot(x2 - center[0], y2 - center[1])
                if length < radius * 0.25:
                    continue
                if max(d1, d2) > radius * 1.1:
                    continue
                tip = (x1, y1) if d1 > d2 else (x2, y2)
                angle_clock = cls._angle_clock(center, tip)
                hands.append(
                    {
                        "line": [int(x1), int(y1), int(x2), int(y2)],
                        "length": float(length),
                        "angle_clock": float(round(angle_clock, 2)),
                    }
                )
        hands.sort(key=lambda h: h["length"], reverse=True)
        candidates = hands[:6]
        chosen_pair = None
        pred_score = None
        if len(candidates) >= 2:
            for i in range(len(candidates)):
                for j in range(len(candidates)):
                    if i == j:
                        continue
                    minute_c = candidates[i]
                    hour_c = candidates[j]
                    if minute_c["length"] < hour_c["length"]:
                        continue
                    minute_raw = minute_c["angle_clock"]
                    minute = int(round((minute_raw / 6.0) / 5.0) * 5) % 60
                    minute_angle = minute * 6.0
                    minute_err = cls._ang_dist(minute_raw, minute_angle)
                    hour_raw = hour_c["angle_clock"]
                    hour = int(round((hour_raw - minute * 0.5) / 30.0)) % 12
                    hour = 12 if hour == 0 else hour
                    hour_angle = (hour % 12) * 30.0 + minute * 0.5
                    hour_err = cls._ang_dist(hour_raw, hour_angle)
                    length_ratio = minute_c["length"] / max(1.0, hour_c["length"])
                    length_penalty = 0.0 if length_ratio >= 1.2 else (1.2 - length_ratio) * 10.0
                    score = minute_err * 1.2 + hour_err * 1.0 + length_penalty
                    if pred_score is None or score < pred_score:
                        pred_score = score
                        chosen_pair = {"minute": minute_c, "hour": hour_c}
        return center, radius, chosen_pair

    @classmethod
    def _crop_for_vision(cls, img, center, radius, chosen_pair, crop_mode: str):
        h, w = img.shape[:2]
        if crop_mode == "full":
            return img
        cx, cy = center
        if chosen_pair and chosen_pair.get("minute") and chosen_pair.get("hour"):
            coords = []
            for key in ("minute", "hour"):
                line = chosen_pair[key]["line"]
                coords.append((line[0], line[1]))
                coords.append((line[2], line[3]))
            coords.append((cx, cy))
            xs = [c[0] for c in coords]
            ys = [c[1] for c in coords]
            x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)
            margin = int(radius * 0.35)
        else:
            x1, y1, x2, y2 = cx - radius, cy - radius, cx + radius, cy + radius
            margin = int(radius * 0.1)
        x1 = max(0, x1 - margin)
        y1 = max(0, y1 - margin)
        x2 = min(w - 1, x2 + margin)
        y2 = min(h - 1, y2 + margin)
        return img[y1 : y2 + 1, x1 : x2 + 1]

    @classmethod
    def _prepare_image_for_vision(cls, image_b64: str, tag: str = ""):
        try:
            import cv2
            import numpy as np
        except Exception as e:
            raise RuntimeError("缺少 opencv-python/numpy，无法进行混合识别") from e

        img_bytes = base64.b64decode(image_b64)
        img_arr = np.frombuffer(img_bytes, dtype=np.uint8)
        img = cv2.imdecode(img_arr, cv2.IMREAD_COLOR)
        if img is None:
            raise RuntimeError("图像解码失败")

        crop_mode = str(_CONFIG.get("vision_crop_mode", DEFAULT_VISION_CROP_MODE) or "auto").lower().strip()
        center, radius, chosen_pair = cls._opencv_detect(img)
        crop = cls._crop_for_vision(img, center, radius, chosen_pair, crop_mode)

        target_w = int(_CONFIG.get("vision_resize_width", DEFAULT_VISION_RESIZE_WIDTH) or 0)
        target_h = int(_CONFIG.get("vision_resize_height", DEFAULT_VISION_RESIZE_HEIGHT) or 0)
        if target_w <= 0 or target_h <= 0:
            ref_size = cls._load_ref_size()
            if ref_size:
                target_w, target_h = ref_size
        target_size = (target_w, target_h) if target_w > 0 and target_h > 0 else None
        max_side = int(_CONFIG.get("vision_resize_max", DEFAULT_VISION_RESIZE_MAX) or 256)
        resize_enabled = bool(_CONFIG.get("vision_resize_enabled", DEFAULT_VISION_RESIZE_ENABLED))
        if resize_enabled:
            crop = cls._resize_for_vision(crop, max_side=max_side, target_size=target_size)

        # 保存缩略图
        thumb_path = None
        if bool(_CONFIG.get("vision_save_thumbs", DEFAULT_VISION_SAVE_THUMBS)):
            thumb_dir = str(_CONFIG.get("vision_thumb_dir", DEFAULT_VISION_THUMB_DIR) or "").strip()
            if not os.path.isabs(thumb_dir):
                thumb_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), thumb_dir)
            os.makedirs(thumb_dir, exist_ok=True)
            suffix = f"_{int(time.time())}"
            name = f"{tag}{suffix}_thumb.png" if tag else f"thumb{suffix}.png"
            thumb_path = os.path.join(thumb_dir, name)
            cv2.imwrite(thumb_path, crop)

        ok, buf = cv2.imencode(".png", crop)
        if not ok:
            raise RuntimeError("图片编码失败")
        crop_b64 = base64.b64encode(buf.tobytes()).decode("ascii")
        meta = {
            "crop_size": (int(crop.shape[1]), int(crop.shape[0])),
            "thumb_path": thumb_path,
            "crop_mode": crop_mode,
        }
        return crop_b64, meta

    @classmethod
    def solve_time(cls, image_b64: str, tag: str = ""):
        """混合方案：OpenCV 初步过滤 → 裁剪缩放 → 视觉模型读时"""
        crop_b64, meta = cls._prepare_image_for_vision(image_b64, tag=tag)
        day_night, conf = cls._classify_day_night(crop_b64)
        time_str = cls._read_time(crop_b64, day_night)
        return time_str, day_night, conf, meta


# ================= TutaRegister 核心注册类 =================

class TutaRegister:
    """Tuta 邮箱注册核心类
    
    封装整个注册流程的所有 API 调用和加密操作
    """

    def __init__(self, proxy: str = None, tag: str = ""):
        self.tag = tag  # 线程标识，用于日志
        self.client_id = _generate_client_id()  # 每次注册生成唯一客户端 ID
        self.impersonate, self.chrome_major, self.chrome_full, self.ua, self.sec_ch_ua = _random_chrome_version()
        self.config = _CONFIG

        # 初始化 HTTP 会话
        self.session = curl_requests.Session(impersonate=self.impersonate)

        self.proxy = proxy
        if self.proxy:
            self.session.proxies = {"http": self.proxy, "https": self.proxy}

        # 设置默认请求头
        self.session.headers.update({
            "User-Agent": self.ua,
            "Accept-Language": random.choice([
                "en-US,en;q=0.9", "en-US,en;q=0.9,zh-CN;q=0.8",
                "en,en-US;q=0.9", "en-US,en;q=0.8",
            ]),
        })

        # 重要状态
        self.access_token = None         # 登录后的 accessToken
        self.user_id = None              # 登录后的 userId
        self.captcha_token = None        # 注册验证码 token
        self.timelock_solution = None    # TimeLock puzzle 的解
        self.system_keys = None          # 系统公钥
        self.crypto_keys = None          # 本地生成的加密密钥集合
        self.account_record = None       # 成功注册后记录
        self.last_captcha_answer = None  # 最近一次提交的验证码

    # ==================== 日志工具 ====================

    def _log(self, step, method, url, status, body=None):
        """格式化输出请求日志"""
        prefix = f"[{self.tag}] " if self.tag else ""
        lines = [
            f"\n{'='*60}",
            f"{prefix}[Step] {step}",
            f"{prefix}[{method}] {url}",
            f"{prefix}[Status] {status}",
        ]
        if body:
            try:
                lines.append(f"{prefix}[Response] {json.dumps(body, indent=2, ensure_ascii=False)[:1000]}")
            except Exception:
                lines.append(f"{prefix}[Response] {str(body)[:1000]}")
        lines.append(f"{'='*60}")
        with _print_lock:
            print("\n".join(lines))

    def _print(self, msg):
        """线程安全的打印"""
        prefix = f"[{self.tag}] " if self.tag else ""
        with _print_lock:
            print(f"{prefix}{msg}")

    # ==================== API 请求基础方法 ====================

    def _api_headers(self, version=TutaApiVersion.SYS, content_type=True, authenticated=False):
        """构造 Tuta API 请求头
        
        Tuta 的请求头特征:
        - v: 模型版本号 (sys=146, tutanota=107 等)
        - cv: 客户端版本号
        - Content-Type: application/json
        - accessToken: 登录后的认证 token
        """
        headers = {
            "v": version,
            "cv": TutaApiVersion.CLIENT,
            "Accept": "application/json",
            "cp": "5",
        }
        # tutanota 端点常见需要 dv=146
        if str(version) == str(TutaApiVersion.TUTANOTA):
            headers["dv"] = TutaApiVersion.SYS
        if content_type:
            headers["Content-Type"] = "application/json"
        if authenticated and self.access_token:
            headers["accessToken"] = self.access_token
        return headers

    def _api_get(self, endpoint, query_body=None, version=TutaApiVersion.SYS,
                 authenticated=False, content_type=True):
        """发送 Tuta 风格的 GET 请求
        
        Tuta 的 GET 请求特殊之处:
        - 请求体通过 URL 参数 _body=<urlencoded_json> 传递
        - 而非标准的 POST body
        """
        url = f"{TutaEndpoints.BASE_URL}{endpoint}"
        if query_body is not None:
            encoded = _encode_query_body(query_body)
            url = f"{url}?_body={encoded}"
        
        headers = self._api_headers(version, content_type, authenticated)
        r = self.session.get(url, headers=headers)
        return r

    def _api_post(self, endpoint, json_body, version=TutaApiVersion.SYS,
                  authenticated=False):
        """发送 Tuta 风格的 POST 请求"""
        url = f"{TutaEndpoints.BASE_URL}{endpoint}"
        headers = self._api_headers(version, True, authenticated)
        r = self.session.post(url, json=json_body, headers=headers)
        return r

    # ==================== 注册步骤 1: TimeLock Captcha ====================

    def solve_timelock_captcha(self):
        """Step 1: 请求并解决 TimeLock Captcha
        
        请求 timelockcaptchaservice 获取 puzzle 参数，
        然后在本地计算 puzzle 的解
        
        HAR 参考 (entry [4]):
        GET /rest/sys/timelockcaptchaservice?_body={...}
        - "2630": "0"           → 格式版本
        - "2631": "KL44hZP3"    → 客户端 ID
        - "2644": [...]         → puzzle 参数请求
        - "2645": "74.80..."    → 客户端性能标记（可能用于调节 puzzle 难度）
        """
        self._print("[Step 1] 请求 TimeLock Captcha...")
        
        query_body = {
            "2630": "0",
            "2631": self.client_id,
            "2644": [{
                "2642": _generate_random_id(),
                "2643": "0"
            }],
            "2645": str(round(random.uniform(50, 150), 10))  # 模拟性能标记
        }

        r = self._api_get(TutaEndpoints.TIMELOCK_CAPTCHA, query_body)
        
        try:
            data = r.json() if r.text else {}
        except Exception:
            data = {"raw": r.text[:500]}

        self._log("1. TimeLock Captcha", "GET", TutaEndpoints.TIMELOCK_CAPTCHA, r.status_code, data)

        self.timelock_solution = TimeLockSolver.solve(data)
        
        return r.status_code, data

    # ==================== 注册步骤 2: 邮箱可用性检查 ====================

    def check_email_availability(self, email: str):
        """Step 2: 检查邮箱地址是否可用
        
        HAR 参考 (entry [10]):
        GET /rest/sys/multiplemailaddressavailabilityservice?_body={...}
        - "2031": "0"           → 格式版本
        - "2032": [{"729": id, "730": email}]  → 要检查的邮箱列表
        - "2612": "KL44hZP3"   → 客户端 ID
        """
        self._print(f"[Step 2] 检查邮箱可用性: {email}")
        
        query_body = {
            "2031": "0",
            "2032": [{
                "729": _generate_random_id(),
                "730": email,
            }],
            "2612": self.client_id,
        }

        r = self._api_get(TutaEndpoints.MAIL_AVAILABILITY, query_body)
        
        try:
            data = r.json() if r.text else {}
        except Exception:
            data = {"raw": r.text[:500]}

        self._log("2. Mail Availability", "GET", TutaEndpoints.MAIL_AVAILABILITY, r.status_code, data)
        return r.status_code, data

    # ==================== 注册步骤 3: RegistrationCaptcha ====================

    def request_registration_captcha(self, email: str):
        """Step 3: 请求注册验证码
        
        提交 TimeLock puzzle 的解作为 PoW 证明，
        如果 puzzle 解正确，服务器直接通过 captcha 验证；
        否则返回图片验证码
        
        GET RegistrationCaptchaServiceGetData:
        - "1480": "0"                                → 格式版本
        - "1481": null                               → campaignToken
        - "1482": "stilt8974@tutamail.com"           → 邮箱地址
        - "1731": "KL44hZP3"                         → signupToken
        - "1751": "0"                                → paidSubscriptionSelected
        - "1752": "0"                                → businessUseSelected
        - "2623": "142601...(大数字)"                 → TimeLock puzzle 解
        - "2624": "zh-CN"                            → 语言
        - "2640": "0"                                → isAutomatedBrowser
        - "2689": []                                 → adAttribution

        如果返回挑战图，需 POST RegistrationCaptchaServiceData:
        - "675": "0"                                 → 格式版本
        - "676": token                               → captcha token
        - "677": visualChallengeResponse             → 图形答案
        - "2627": audioChallengeResponse             → 音频答案
        """
        self._print(f"[Step 3] 请求注册验证码...")
        
        query_body = {
            "1480": "0",
            "1481": None,               # 首次请求无 captcha 答案
            "1482": email,
            "1731": self.client_id,
            "1751": "0",
            "1752": "0",
            "2623": self.timelock_solution or "0",  # TimeLock puzzle 的解
            "2624": DEFAULT_LANGUAGE,
            "2640": "0",
            "2689": [],
        }

        r = self._api_get(TutaEndpoints.REG_CAPTCHA, query_body)

        try:
            data = r.json() if r.text else {}
        except Exception:
            data = {"raw": r.text[:500]}

        self._log("3. Registration Captcha", "GET", TutaEndpoints.REG_CAPTCHA, r.status_code, data)

        # 解析响应，提取 captcha token
        # 如果 puzzle 解正确，直接获得 token
        # 如果需要图片验证码，保存图片并等待人工输入，再通过 POST 验证
        def _extract_captcha_token(payload: dict):
            if not isinstance(payload, dict):
                return None
            for key in ("680", "650", "682", "1481"):
                val = payload.get(key)
                if isinstance(val, str) and val:
                    return val
            for key in (680, 650, 682, 1481):
                if key in payload and isinstance(payload.get(key), str) and payload.get(key):
                    return payload.get(key)
            for k, v in payload.items():
                if isinstance(v, str):
                    if v.startswith("iVBOR"):
                        continue
                    if 16 <= len(v) <= 128 and re.fullmatch(r"[A-Za-z0-9_-]+", v):
                        return v
            return None

        def _extract_captcha_images(payload: dict):
            images = []
            if not isinstance(payload, dict):
                return images
            image_b64 = payload.get("681")
            if not image_b64 and 681 in payload:
                image_b64 = payload.get(681)
            if isinstance(image_b64, str) and image_b64:
                images.append(("captcha", image_b64))
            candidates = payload.get("2625")
            if isinstance(candidates, list):
                for i, item in enumerate(candidates, 1):
                    if isinstance(item, dict):
                        img = item.get("2621") or item.get(2621)
                        if isinstance(img, str) and img:
                            images.append((f"captcha_{i}", img))
            return images

        token = _extract_captcha_token(data)
        images = _extract_captcha_images(data)

        final_status = r.status_code
        final_data = data
        if images:
            # 保存验证码图片
            captcha_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "captchas")
            os.makedirs(captcha_dir, exist_ok=True)
            saved_paths = []
            try:
                for name, img_b64 in images:
                    captcha_path = os.path.join(
                        captcha_dir, f"{name}_{self.tag}_{int(time.time())}.png"
                    )
                    with open(captcha_path, "wb") as f:
                        f.write(base64.b64decode(img_b64))
                    saved_paths.append(captcha_path)
                for p in saved_paths:
                    self._print(f"[Step 3] 验证码图片已保存: {p}")
            except Exception as e:
                raise Exception(f"验证码图片保存失败: {e}")

            # 仅获取验证码图片（用于人工输入）
            if _is_truthy(self.config.get("captcha_only", DEFAULT_CAPTCHA_ONLY)):
                if token:
                    self.captcha_token = token
                # 保存上下文，便于后续手工提交答案
                try:
                    context_path = os.path.join(captcha_dir, "captcha_context.json")
                    with open(context_path, "w", encoding="utf-8") as f:
                        json.dump(
                            {
                                "email": email,
                                "token": token,
                                "client_id": self.client_id,
                                "timestamp": int(time.time()),
                                "images": saved_paths,
                            },
                            f,
                            ensure_ascii=False,
                            indent=2,
                        )
                    self._print(f"[Step 3] captcha context 已保存: {context_path}")
                except Exception as e:
                    self._print(f"[Step 3] captcha context 保存失败: {e}")
                self._print("[Step 3] 已保存验证码图片，等待人工输入")
                return final_status, final_data

            # 优先自动识别验证码（如配置 GROK_API_KEY 或 CAPTCHA_AUTO=1）
            captcha_code = str(self.config.get("captcha_code", DEFAULT_CAPTCHA_CODE) or "").strip()
            auto_done = False
            if not captcha_code and _should_auto_solve_captcha():
                attempts_left = int(self.config.get("captcha_max_attempts", DEFAULT_CAPTCHA_MAX_ATTEMPTS) or 5)
                # 按优先级排序图片，依次尝试
                ordered = sorted(images, key=lambda it: (("captcha_1" in (it[0] or "")), len(it[1]) if isinstance(it[1], str) else 0), reverse=True)
                last_error = None
                for name, img_b64 in ordered:
                    if attempts_left <= 0:
                        break
                    while attempts_left > 0:
                        attempts_left -= 1
                        try:
                            t, day_night, conf, meta = CaptchaTimeSolver.solve_time(img_b64, tag=f"{self.tag}_{name}")
                            meta_info = ""
                            if isinstance(meta, dict):
                                cs = meta.get("crop_size")
                                tp = meta.get("thumb_path")
                                if cs:
                                    meta_info += f", crop={cs}"
                                if tp:
                                    meta_info += f", thumb={tp}"
                            self._print(
                                f"[Step 3] 自动识别 captcha: {name} => {t} "
                                f"(day_night={day_night}, conf={conf:.2f}{meta_info})"
                            )
                            self._print(f"[Step 3] 提交验证码答案: {t}")
                            r2_status, data2 = self.submit_registration_captcha(token, t)
                            if isinstance(data2, dict):
                                self._print(
                                    f"[Step 3] captcha token candidates: "
                                    f"680={data2.get('680') or data2.get(680)}, "
                                    f"650={data2.get('650') or data2.get(650)}, "
                                    f"keys={list(data2.keys())[:12]}"
                                )
                            # 简单判定：200/201 认为通过
                            if r2_status in (200, 201):
                                self._print(f"[Step 3] 自动识别通过，token: {token}")
                                final_status = r2_status
                                final_data = data2
                                auto_done = True
                                break
                            else:
                                last_error = f"status={r2_status}"
                                self._print(f"[Step 3] 自动识别答案被拒，剩余尝试 {attempts_left}")
                        except Exception as e:
                            last_error = str(e)
                            self._print(f"[Step 3] 自动识别失败: {e} (剩余尝试 {attempts_left})")
                    if auto_done:
                        break
                if not auto_done and last_error:
                    self._print(f"[Step 3] 自动识别未通过: {last_error}")

            # 不等待人工输入，自动识别失败即终止
            if not auto_done:
                raise Exception("自动识别失败，未获取有效验证码答案")

        if token:
            self.captcha_token = token
        else:
            self._print("⚠️ 未能从 registrationcaptchaservice 获取 captcha token")
        
        return final_status, final_data

    def submit_registration_captcha(self, token: str, captcha_code: str):
        """提交注册验证码答案（POST RegistrationCaptchaServiceData）"""
        self.last_captcha_answer = captcha_code
        post_body = {
            "675": "0",
            "676": token,
            "677": captcha_code,
            "2627": None,
        }
        r = self._api_post(TutaEndpoints.REG_CAPTCHA, post_body, version=TutaApiVersion.SYS)
        try:
            data = r.json() if r.text else {}
        except Exception:
            data = {"raw": r.text[:500]}
        self._log("3. Registration Captcha (Answer)", "POST", TutaEndpoints.REG_CAPTCHA, r.status_code, data)
        return r.status_code, data

    def _build_account_record(self, email: str, password: str, salt_b64: str, session_data: dict):
        """构建可持久化的账号记录（用于取件/解密）"""
        return {
            "created_at": datetime.utcnow().isoformat() + "Z",
            "email": email,
            "password": password,
            "mail_domain": email.split("@")[-1] if "@" in email else None,
            "proxy": self.proxy,
            "client_id": self.client_id,
            "captcha_token": self.captcha_token,
            "captcha_answer": self.last_captcha_answer,
            "timelock_solution": self.timelock_solution,
            "salt_b64": salt_b64,
            "recover_code_hex": (self.crypto_keys or {}).get("recover_code"),
            "access_token": self.access_token,
            "user_id": self.user_id,
            "system_keys": self.system_keys,
            "session_raw": session_data,
        }

    # ==================== 注册步骤 4: 获取系统公钥 ====================

    def get_system_keys(self):
        """Step 4: 获取 Tuta 系统公钥
        
        HAR 参考 (entry [14]):
        GET /rest/sys/systemkeysservice (无 query body)
        
        响应包含:
        - RSA 公钥 (用于加密 customerGroupKey 给系统)
        - Kyber 公钥 (后量子密码学)
        """
        self._print("[Step 4] 获取系统公钥...")
        
        r = self._api_get(TutaEndpoints.SYSTEM_KEYS, query_body=None, content_type=False)

        try:
            data = r.json() if r.text else {}
        except Exception:
            data = {"raw": r.text[:500]}

        self._log("4. System Keys", "GET", TutaEndpoints.SYSTEM_KEYS, r.status_code, data)
        
        self.system_keys = data
        return r.status_code, data

    # ==================== 注册步骤 5: 创建客户账号（核心） ====================

    def create_account(self, email: str, password: str):
        """Step 5: 创建客户账号
        
        这是整个注册流程中最复杂的步骤。
        需要在客户端生成所有加密密钥，然后打包成一个大 JSON 请求。
        
        HAR 参考 (entry [16]):
        POST /rest/tutanota/customeraccountservice
        - v: 107 (tutanota 模型版本)
        - Status: 201 Created
        - Body: ~24KB 的加密密钥数据
        
        请求体主要字段:
        - "649": "0"            → 格式版本
        - "650": captcha_token  → captcha 验证 token
        - "651": null           → 推荐码
        - "652": "zh"           → 语言
        - "653": [userdata]     → 用户数据（含所有加密密钥）
        - "654": encrypted_key  → 系统加密的 customer key
        - "656": [keypairs]     → RSA/Kyber 密钥对（3组: user/admin/customer）
        
        TODO: 需要完整实现 generate_keys() 后才能构造正确的请求体
        """
        # ==========================================================
        # 核心：使用纯 Python 实现动态生成 Tuta 密码学请求载荷
        # ==========================================================
        from tuta_crypto_core import TutaCryptoCore
        import base64

        # 获取系统 RSA 公钥
        sys_pub_rsa_b64 = self.system_keys.get("303", "")
        if not sys_pub_rsa_b64:
            raise ValueError("未能获取系统管理员公钥 (303)")
        sys_pub_rsa_bytes = base64.b64decode(sys_pub_rsa_b64)

        # 动态构造注册 Payload 与 Account 登录 Salt
        sys_pub_key_version = self.system_keys.get("304") or self.system_keys.get(304) or "0"
        post_body, salt_b64, recover_code_hex = TutaCryptoCore.generate_registration_payload(
            email=email,
            password=password,
            auth_token=self.captcha_token or "",
            sys_pub_rsa_bytes=sys_pub_rsa_bytes,
            lang=DEFAULT_LANGUAGE,
            app="0",
            system_admin_pub_key_version=str(sys_pub_key_version),
        )
        self._print(f"[Step 5] 使用 captcha token: {self.captcha_token}")
        try:
            def _len_or_none(v):
                if v is None:
                    return None
                if isinstance(v, (str, bytes, bytearray)):
                    return len(v)
                return None
            def _peek(path):
                cur = post_body
                for key in path:
                    if isinstance(cur, list):
                        cur = cur[key]
                    else:
                        cur = cur.get(key)
                return cur
            lengths = {}
            for key in ("654", "660", "873"):
                lengths[key] = _len_or_none(post_body.get(key))
            # 653[0] 关键字段
            for key in ("625", "626", "627", "629", "630", "631", "632", "633", "634", "635", "636", "637", "638", "639", "640", "641", "892", "893", "894"):
                lengths[f"653.0.{key}"] = _len_or_none(_peek(["653", 0, key]))
            # 656/657/658 第一个 keypair
            for grp in ("656", "657", "658"):
                for key in ("646", "647", "1342", "1343", "1344", "1345"):
                    lengths[f"{grp}.0.{key}"] = _len_or_none(_peek([grp, 0, key]))
            self._print(f"[Step 5] payload lengths: {lengths}")
        except Exception as e:
            self._print(f"[Step 5] payload length log failed: {e}")

        r = self._api_post(
            TutaEndpoints.CUSTOMER_ACCOUNT,
            post_body,
            version=TutaApiVersion.TUTANOTA,
        )

        try:
            data = r.json() if r.text else {}
        except Exception:
            data = {"raw": r.text[:500], "status": r.status_code}

        self._log("5. Create Account", "POST", TutaEndpoints.CUSTOMER_ACCOUNT, r.status_code, data)
        
        # 记录生成的 Salt 供后续 Session 使用
        self.crypto_keys = {"salt_b64": salt_b64, "recover_code": recover_code_hex}
        if r.status_code == 201:
            self._print(f"[Step 5] recovery code (hex): {recover_code_hex}")
        
        return r.status_code, data

        try:
            data = r.json() if r.text else {}
        except Exception:
            data = {"raw": r.text[:500], "status": r.status_code}

        self._log("5. Create Account", "POST", TutaEndpoints.CUSTOMER_ACCOUNT, r.status_code, data)
        return r.status_code, data

    # ==================== 注册步骤 6: 获取 Salt ====================

    def get_salt(self, email: str):
        """Step 6: 获取账号 Salt
        
        HAR 参考 (entry [17]):
        GET /rest/sys/saltservice?_body={"418":"0","419":"stilt8974@tutamail.com"}
        
        响应:
        {"421": "0", "2133": "1", "422": "UL/q1MO8rU6ujX6T6wYs+g=="}
        - "422" → salt 值 (Base64)
        - "2133" → KDF 版本 (1 = Argon2id)
        """
        self._print(f"[Step 6] 获取 Salt: {email}")
        
        query_body = {
            "418": "0",
            "419": email,
        }

        r = self._api_get(TutaEndpoints.SALT, query_body)

        try:
            data = r.json() if r.text else {}
        except Exception:
            data = {"raw": r.text[:500]}

        self._log("6. Get Salt", "GET", TutaEndpoints.SALT, r.status_code, data)
        return r.status_code, data

    # ==================== 注册步骤 7: 创建 Session ====================

    def create_session(self, email: str, password: str, salt_b64: str = None,
                       access_key: str = None, auth_token: str = None):
        """Step 7: 创建登录 Session"""
        self._print(f"[Step 7] 创建 Session: {email}")

        from tuta_crypto_core import TutaCryptoCore
        
        # 使用生成的或获取的 salt 计算 passphraseKey
        if not salt_b64 and self.crypto_keys and "salt_b64" in self.crypto_keys:
            salt_b64 = self.crypto_keys["salt_b64"]
            
        if salt_b64:
            salt = base64.b64decode(salt_b64)
        else:
            _, salt_data = self.get_salt(email)
            salt_b64 = salt_data.get("422", "")
            salt = base64.b64decode(salt_b64) if salt_b64 else os.urandom(16)

        passphrase_key = TutaCryptoCore.argon2_derive_passphrase_key(password, salt)
        auth_verifier = TutaCryptoCore.get_auth_verifier(passphrase_key)
        auth_verifier_b64url = _base64_to_base64url(base64.b64encode(auth_verifier).decode())

        # 记录盐值/派生密钥，供后续解密邮件使用
        self.salt_b64 = salt_b64
        self.passphrase_key = passphrase_key
        self.login_email = email

        mail_addr = email.lower().strip()
        post_body = {
            "1212": "0",
            "1213": mail_addr,
            "1214": auth_verifier_b64url,
            "1215": "Chrome Browser",
            "1216": access_key,   # accessKey (持久登录才需要)
            "1217": auth_token,   # authToken (二次认证)
            "1218": [],     # user (HAR 中为空数组)
            "1417": None,
        }

        r = self._api_post(TutaEndpoints.SESSION, post_body)

        try:
            data = r.json() if r.text else {}
        except Exception:
            data = {"raw": r.text[:500]}

        self._log("7. Create Session", "POST", TutaEndpoints.SESSION, r.status_code, data)

        # 提取 accessToken（如果响应中有的话）
        if isinstance(data, dict):
            access_token = data.get("1221")
            user_id = data.get("1223")
            if isinstance(user_id, (list, tuple)) and user_id:
                user_id = user_id[0]
            if access_token:
                self.access_token = access_token
                # 保险：写入会话默认头，避免后续请求漏传
                try:
                    self.session.headers["accessToken"] = access_token
                except Exception:
                    pass
            if user_id:
                self.user_id = user_id
        
        return r.status_code, data

    # ==================== 登录后: 读取用户与邮件 ====================

    def get_user(self, user_id: str = None):
        """获取当前用户信息 (需要 accessToken)"""
        uid = user_id or self.user_id
        if not uid:
            raise ValueError("缺少 user_id")
        endpoint = f"{TutaEndpoints.USER}/{uid}"
        r = self._api_get(endpoint, query_body=None, authenticated=True, content_type=False)
        try:
            data = r.json() if r.text else {}
        except Exception:
            data = {"raw": r.text[:500]}
        self._log("User", "GET", endpoint, r.status_code, data)
        return r.status_code, data

    def get_group(self, group_id: str):
        """获取 Group 实体 (包含 currentKeys)"""
        if not group_id:
            raise ValueError("缺少 group_id")
        endpoint = f"/rest/sys/group/{group_id}"
        r = self._api_get(endpoint, query_body=None, authenticated=True, content_type=False)
        try:
            data = r.json() if r.text else {}
        except Exception:
            data = {"raw": r.text[:500]}
        self._log("Group", "GET", endpoint, r.status_code, data)
        return r.status_code, data

    @staticmethod
    def _extract_mail_group_id(user_data: dict):
        """从用户数据中找出 Mail Group Id (groupType=5)"""
        memberships = user_data.get("96", []) if isinstance(user_data, dict) else []
        for m in memberships:
            if str(m.get("1030")) == "5":
                group = m.get("29")
                if isinstance(group, list) and group:
                    return group[0]
        return None

    @staticmethod
    def _extract_group_membership(user_data: dict, group_type: str):
        memberships = user_data.get("96", []) if isinstance(user_data, dict) else []
        for m in memberships:
            if str(m.get("1030")) == str(group_type):
                return m
        return None

    @staticmethod
    def _find_membership_by_group_id(user_data: dict, group_id: str):
        memberships = user_data.get("96", []) if isinstance(user_data, dict) else []
        for m in memberships:
            grp = m.get("29")
            if isinstance(grp, list) and grp:
                if grp[0] == group_id:
                    return m
            elif grp == group_id:
                return m
        return None

    @staticmethod
    def _extract_first_dict(value):
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    return item
            if value and isinstance(value[0], dict):
                return value[0]
            return None
        return value if isinstance(value, dict) else None

    def _get_user_group_key(self, user_data: dict, passphrase_key: bytes):
        """使用 passphraseKey 解出 userGroupKey"""
        from tuta_crypto_core import TutaCryptoCore

        raw_user_group = user_data.get("95") if isinstance(user_data, dict) else None
        user_group = self._extract_first_dict(raw_user_group)
        if not isinstance(user_group, dict):
            raise ValueError("userGroup 缺失")
        enc_key_b64 = user_group.get("27")
        enc_key = _b64decode_any(enc_key_b64)
        if not enc_key:
            raise ValueError("userGroup symEncGKey 缺失")
        return TutaCryptoCore.decrypt_key(passphrase_key, enc_key)

    def _get_mail_group_key(self, user_data: dict, user_group_key: bytes):
        """使用 userGroupKey 解出 mailGroupKey"""
        from tuta_crypto_core import TutaCryptoCore

        mail_membership = self._extract_group_membership(user_data, "5")
        if not mail_membership:
            raise ValueError("未找到 Mail Group Membership")
        enc_key_b64 = mail_membership.get("27")
        enc_key = _b64decode_any(enc_key_b64)
        if not enc_key:
            raise ValueError("MailGroup symEncGKey 缺失")
        return TutaCryptoCore.decrypt_key(user_group_key, enc_key)

    def _decrypt_encrypted_value(self, enc_b64: str, session_key: bytes, compressed: bool = False) -> str:
        from tuta_crypto_core import TutaCryptoCore

        if enc_b64 in (None, ""):
            return ""
        enc_bytes = _b64decode_any(enc_b64)
        if not enc_bytes:
            return ""
        plain = TutaCryptoCore.decrypt_bytes(session_key, enc_bytes)
        if compressed:
            return TutaCryptoCore.decompress_string(plain)
        return plain.decode("utf-8", errors="replace")

    def _extract_mail_details(self, blob_data):
        blob = self._extract_first_dict(blob_data)
        if not isinstance(blob, dict):
            return None
        details_list = blob.get("1305") or []
        return self._extract_first_dict(details_list)

    def _decrypt_mail_body_from_blob(self, blob_data, session_key: bytes) -> str:
        details = self._extract_mail_details(blob_data)
        if not isinstance(details, dict):
            return ""
        body = self._extract_first_dict(details.get("1288") or [])
        if not isinstance(body, dict):
            return ""
        enc_text = body.get("1275")
        if enc_text:
            return self._decrypt_encrypted_value(enc_text, session_key, compressed=False)
        enc_comp = body.get("1276")
        if enc_comp:
            return self._decrypt_encrypted_value(enc_comp, session_key, compressed=True)
        return ""

    def _decrypt_pub_enc_bucket_key(self, pub_enc_bucket_key: str, protocol_version: str,
                                    key_group_id: str, user_data: dict, user_group_key: bytes):
        """解密 pubEncBucketKey（TutaCrypt / RSA） -> bucketKey bytes"""
        from tuta_crypto_core import TutaCryptoCore

        if not pub_enc_bucket_key or not key_group_id:
            return None

        cache_key = f"{key_group_id}:{protocol_version}:{pub_enc_bucket_key[:32]}"
        if not hasattr(self, "_bucket_key_cache"):
            self._bucket_key_cache = {}
        if cache_key in self._bucket_key_cache:
            return self._bucket_key_cache.get(cache_key)

        group_key = None
        # 如果 key_group 就是用户 owner group，直接使用 user_group_key
        if isinstance(user_data, dict) and user_data.get("996") == key_group_id:
            group_key = user_group_key
        else:
            membership = self._find_membership_by_group_id(user_data, key_group_id)
            if membership:
                enc_group_key = _b64decode_any(membership.get("27"))
                if enc_group_key:
                    try:
                        group_key = TutaCryptoCore.decrypt_key(user_group_key, enc_group_key)
                    except Exception:
                        group_key = None
        if not group_key:
            return None

        status, group = self.get_group(key_group_id)
        if status != 200 or not isinstance(group, dict):
            return None

        keypair = group.get("13") or group.get(13)
        keypair = self._extract_first_dict(keypair)
        if not isinstance(keypair, dict):
            return None

        pub_ecc = _b64decode_any(keypair.get("2144"))
        enc_priv_ecc = _b64decode_any(keypair.get("2145"))
        pub_kyber = _b64decode_any(keypair.get("2146"))
        enc_priv_kyber = _b64decode_any(keypair.get("2147"))
        if not (pub_ecc and enc_priv_ecc and pub_kyber and enc_priv_kyber):
            return None

        try:
            priv_ecc = TutaCryptoCore.decrypt_bytes(group_key, enc_priv_ecc)
            priv_kyber = TutaCryptoCore.decrypt_bytes(group_key, enc_priv_kyber)
        except Exception:
            return None

        script_path = Path(__file__).resolve().parent / "pq_decrypt.mjs"
        if not script_path.exists():
            return None

        payload = {
            "pubEncBucketKey_b64": pub_enc_bucket_key,
            "x25519_priv_b64": base64.b64encode(priv_ecc).decode(),
            "x25519_pub_b64": base64.b64encode(pub_ecc).decode(),
            "kyber_priv_b64": base64.b64encode(priv_kyber).decode(),
            "kyber_pub_b64": base64.b64encode(pub_kyber).decode(),
            "protocolVersion": int(protocol_version or 2),
        }

        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile("w", delete=False, suffix=".json", encoding="utf-8") as f:
                json.dump(payload, f)
                tmp_path = f.name

            proc = subprocess.run(
                ["node", str(script_path), tmp_path],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            if proc.returncode != 0:
                return None
            out_b64 = (proc.stdout or "").strip()
            if not out_b64:
                return None
            bucket_key = base64.b64decode(out_b64)
        except Exception:
            return None
        finally:
            try:
                if tmp_path and os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass

        self._bucket_key_cache[cache_key] = bucket_key
        return bucket_key

    def _resolve_session_key_from_bucket(self, mail: dict, user_data: dict,
                                         user_group_key: bytes, mail_group_key: bytes):
        """当 mail._ownerEncSessionKey 缺失时，尝试用 bucketKey 解析 sessionKey"""
        from tuta_crypto_core import TutaCryptoCore

        if not isinstance(mail, dict):
            return None
        bucket_list = mail.get("1310") or []
        if not isinstance(bucket_list, list):
            return None
        mid = mail.get("99")
        list_id, mail_id = None, None
        if isinstance(mid, list) and len(mid) >= 2:
            list_id, mail_id = mid[0], mid[1]

        key_candidates = []
        if mail_group_key:
            key_candidates.append(mail_group_key)
        if user_group_key and user_group_key != mail_group_key:
            key_candidates.append(user_group_key)

        for bk in bucket_list:
            if not isinstance(bk, dict):
                continue
            enc_bucket_keys = []
            group_enc = bk.get("2046")
            pub_enc = bk.get("2045")
            if group_enc:
                enc_bucket_keys.append(_b64decode_any(group_enc))
            if pub_enc:
                enc_bucket_keys.append(_b64decode_any(pub_enc))

            # bucketEncSessionKeys
            sess_list = bk.get("2048") or []
            if not isinstance(sess_list, list):
                sess_list = []

            for enc_bucket_key in enc_bucket_keys:
                if not enc_bucket_key:
                    continue

                bucket_key = None
                if group_enc:
                    for k in key_candidates:
                        try:
                            bucket_key = TutaCryptoCore.decrypt_key(k, enc_bucket_key)
                            break
                        except Exception:
                            continue
                if bucket_key is None and pub_enc:
                    protocol_version = str(bk.get("2158") or "2")
                    key_group = bk.get("2047")
                    if isinstance(key_group, list) and key_group:
                        key_group = key_group[0]
                    if isinstance(pub_enc, bytes):
                        pub_enc_str = base64.b64encode(pub_enc).decode()
                    else:
                        pub_enc_str = pub_enc
                    bucket_key = self._decrypt_pub_enc_bucket_key(
                        pub_enc_str, protocol_version, key_group, user_data, user_group_key
                    )

                if not bucket_key:
                    continue

                for sk in sess_list:
                    if not isinstance(sk, dict):
                        continue
                    if list_id and mail_id:
                        if sk.get("2040") != list_id or sk.get("2041") != mail_id:
                            continue
                    enc_sess = _b64decode_any(sk.get("2042"))
                    if not enc_sess:
                        continue
                    try:
                        return TutaCryptoCore.decrypt_key(bucket_key, enc_sess)
                    except Exception:
                        continue
        return None

    def get_mailbox_group_root(self, mail_group_id: str):
        """Mail Group -> MailboxGroupRoot"""
        endpoint = f"/rest/tutanota/mailboxgrouproot/{mail_group_id}"
        r = self._api_get(endpoint, query_body=None, version=TutaApiVersion.TUTANOTA,
                          authenticated=True, content_type=False)
        try:
            data = r.json() if r.text else {}
        except Exception:
            data = {"raw": r.text[:500]}
        self._log("MailboxGroupRoot", "GET", endpoint, r.status_code, data)
        return r.status_code, data

    def get_mailbox(self, mailbox_id: str):
        """读取 Mailbox"""
        endpoint = f"/rest/tutanota/mailbox/{mailbox_id}"
        r = self._api_get(endpoint, query_body=None, version=TutaApiVersion.TUTANOTA,
                          authenticated=True, content_type=False)
        try:
            data = r.json() if r.text else {}
        except Exception:
            data = {"raw": r.text[:500]}
        self._log("Mailbox", "GET", endpoint, r.status_code, data)
        return r.status_code, data

    def list_mailsets(self, mailset_list_id: str, count: int = 1000, reverse: bool = False):
        """读取 MailSet 列表 (folders)"""
        start = "------------"
        reverse_flag = "true" if reverse else "false"
        endpoint = f"/rest/tutanota/mailset/{mailset_list_id}?start={start}&count={count}&reverse={reverse_flag}"
        r = self._api_get(endpoint, query_body=None, version=TutaApiVersion.TUTANOTA,
                          authenticated=True, content_type=False)
        try:
            data = r.json() if r.text else []
        except Exception:
            data = {"raw": r.text[:500]}
        self._log("MailSet", "GET", endpoint, r.status_code, data)
        return r.status_code, data

    def list_mailset_entries(self, entry_list_id: str, count: int = 50, reverse: bool = True):
        """读取 MailSetEntry 列表 (邮件索引)"""
        start = "_" * 200
        reverse_flag = "true" if reverse else "false"
        endpoint = f"/rest/tutanota/mailsetentry/{entry_list_id}?start={start}&count={count}&reverse={reverse_flag}"
        r = self._api_get(endpoint, query_body=None, version=TutaApiVersion.TUTANOTA,
                          authenticated=True, content_type=False)
        try:
            data = r.json() if r.text else []
        except Exception:
            data = {"raw": r.text[:500]}
        self._log("MailSetEntry", "GET", endpoint, r.status_code, data)
        return r.status_code, data

    def get_mails(self, mail_list_id: str, mail_ids: list):
        """批量读取 Mail 元数据"""
        ids_param = ",".join(mail_ids)
        endpoint = f"/rest/tutanota/mail/{mail_list_id}?ids={ids_param}"
        r = self._api_get(endpoint, query_body=None, version=TutaApiVersion.TUTANOTA,
                          authenticated=True, content_type=False)
        try:
            data = r.json() if r.text else []
        except Exception:
            data = {"raw": r.text[:500]}
        self._log("Mail", "GET", endpoint, r.status_code, data)
        return r.status_code, data

    def request_blob_access_token(self, archive_id: str, archive_data_type: str = None):
        """请求 blobAccessToken (maildetailsblob 需要)"""
        post_body = {
            "78": "0",
            "80": [],  # write
            "180": archive_data_type,
            "181": [
                {
                    "176": _generate_random_id(),
                    "177": archive_id,
                    "178": None,
                    "179": [],
                }
            ],
        }
        r = self._api_post(TutaEndpoints.BLOB_ACCESS_TOKEN, post_body,
                           version=TutaApiVersion.STORAGE, authenticated=True)
        try:
            data = r.json() if r.text else {}
        except Exception:
            data = {"raw": r.text[:500]}
        self._log("BlobAccessToken", "POST", TutaEndpoints.BLOB_ACCESS_TOKEN, r.status_code, data)

        access_info = None
        if isinstance(data, dict):
            access_info = data.get("161") or data.get(161)
            if isinstance(access_info, list) and access_info:
                access_info = access_info[0]

        if not isinstance(access_info, dict):
            return r.status_code, None

        blob_access_token = access_info.get("159") or access_info.get(159)
        servers = access_info.get("160") or access_info.get(160) or []
        server_url = None
        if isinstance(servers, list) and servers:
            server_url = servers[0].get("156") or servers[0].get(156)

        return r.status_code, {
            "blob_access_token": blob_access_token,
            "server_url": server_url,
            "raw": access_info,
        }

    def get_mail_details_blob(self, archive_id: str, blob_ids: list, blob_access_token: str, server_url: str):
        """获取 MailDetailsBlob 原始内容"""
        params = {
            "accessToken": self.access_token,
            "v": TutaApiVersion.TUTANOTA,
            "ids": ",".join(blob_ids),
            "blobAccessToken": blob_access_token,
            "cv": TutaApiVersion.CLIENT,
        }
        base = server_url.rstrip("/") if server_url else TutaEndpoints.BASE_URL
        url = f"{base}/rest/tutanota/maildetailsblob/{archive_id}?{urlencode(params)}"
        headers = self._api_headers(version=TutaApiVersion.TUTANOTA, content_type=False, authenticated=False)
        r = self.session.get(url, headers=headers)
        return r.status_code, r

    def download_mail_details(self, output_dir: str = "mail_details", max_mails: int = 5,
                              decrypt: bool = False, password: str = None, user_data: dict = None):
        """拉取并保存 MailDetailsBlob 到本地，并可选解密正文"""
        os.makedirs(output_dir, exist_ok=True)

        user = user_data
        if not user:
            status, user = self.get_user()
            if status != 200:
                raise Exception(f"获取用户失败 ({status})")

        mail_group_id = self._extract_mail_group_id(user)
        if not mail_group_id:
            raise Exception("未找到 Mail Group")

        status, mbgr = self.get_mailbox_group_root(mail_group_id)
        if status != 200:
            raise Exception(f"获取 MailboxGroupRoot 失败 ({status})")
        mailbox_id = mbgr.get("699", [None])[0]
        if not mailbox_id:
            raise Exception("未找到 mailbox_id")

        status, mailbox = self.get_mailbox(mailbox_id)
        if status != 200:
            raise Exception(f"获取 Mailbox 失败 ({status})")

        mailset_ref_list = mailbox.get("443") or []
        mailset_list_id = None
        if isinstance(mailset_ref_list, list) and mailset_ref_list:
            ref = mailset_ref_list[0]
            if isinstance(ref, dict):
                ids = ref.get("442")
                if isinstance(ids, list) and ids:
                    mailset_list_id = ids[0]
        if not mailset_list_id:
            raise Exception("未找到 MailSet 列表 ID")

        status, mailsets = self.list_mailsets(mailset_list_id)
        if status != 200 or not isinstance(mailsets, list):
            raise Exception(f"获取 MailSet 失败 ({status})")

        inbox = None
        for ms in mailsets:
            if str(ms.get("436")) == "1":
                inbox = ms
                break
        if not inbox:
            raise Exception("未找到 INBOX MailSet")

        entry_list_id = inbox.get("1459", [None])[0]
        if not entry_list_id:
            raise Exception("未找到 MailSetEntry 列表 ID")

        status, entries = self.list_mailset_entries(entry_list_id, count=max_mails, reverse=True)
        if status != 200 or not isinstance(entries, list):
            raise Exception(f"获取 MailSetEntry 失败 ({status})")
        if not entries:
            return []

        mail_list_id = entries[0].get("1456", [[None, None]])[0][0]
        mail_ids = [e.get("1456", [[None, None]])[0][1] for e in entries if e.get("1456")]

        status, mails = self.get_mails(mail_list_id, mail_ids)
        if status != 200 or not isinstance(mails, list):
            raise Exception(f"获取 Mail 失败 ({status})")

        # 保存元数据索引
        index_path = os.path.join(output_dir, "mail_index.json")
        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(mails, f, ensure_ascii=False, indent=2)

        # 提取 mailDetails blob 引用
        archive_to_blob_ids = {}
        blob_to_mail = {}
        for m in mails[:max_mails]:
            details = m.get("1308")
            if not details:
                continue
            if isinstance(details, list) and details and isinstance(details[0], list):
                for pair in details:
                    if len(pair) >= 2:
                        archive_to_blob_ids.setdefault(pair[0], []).append(pair[1])
                        blob_to_mail[pair[1]] = m
            elif isinstance(details, list) and len(details) >= 2:
                archive_to_blob_ids.setdefault(details[0], []).append(details[1])
                blob_to_mail[details[1]] = m

        saved = []
        blob_payloads = {}
        for archive_id, blob_ids in archive_to_blob_ids.items():
            status, token_info = self.request_blob_access_token(archive_id)
            if status not in (200, 201) or not token_info:
                raise Exception(f"获取 blobAccessToken 失败 ({status})")
            blob_access_token = token_info.get("blob_access_token")
            server_url = token_info.get("server_url")
            if not blob_access_token or not server_url:
                raise Exception("blobAccessToken 返回缺失")

            for blob_id in blob_ids:
                status, resp = self.get_mail_details_blob(archive_id, [blob_id], blob_access_token, server_url)
                if status != 200:
                    raise Exception(f"获取 maildetailsblob 失败 ({status})")

                # 尝试按 content-type 选择扩展名
                ctype = resp.headers.get("content-type", "")
                ext = ".bin"
                if "application/json" in ctype or "text/" in ctype:
                    ext = ".json"
                out_path = os.path.join(output_dir, f"{blob_id}{ext}")
                with open(out_path, "wb") as f:
                    f.write(resp.content)
                saved.append(out_path)

                # 尝试解析 JSON（部分情况下 content-type 不准确）
                try:
                    blob_payloads[blob_id] = resp.json()
                except Exception:
                    try:
                        blob_payloads[blob_id] = json.loads(resp.content.decode("utf-8", errors="ignore"))
                    except Exception:
                        pass

        if password and not decrypt:
            decrypt = True

        if decrypt:
            from tuta_crypto_core import TutaCryptoCore

            passphrase_key = getattr(self, "passphrase_key", None)
            if passphrase_key is None:
                salt_b64 = getattr(self, "salt_b64", None) or (user.get("90") if isinstance(user, dict) else None)
                if not salt_b64 and getattr(self, "login_email", None):
                    _, salt_data = self.get_salt(self.login_email)
                    salt_b64 = salt_data.get("422")
                if not salt_b64:
                    raise Exception("解密需要 salt，当前未获取")
                if not password:
                    raise Exception("解密需要 password")
                salt = base64.b64decode(salt_b64)
                passphrase_key = TutaCryptoCore.argon2_derive_passphrase_key(password, salt)

            user_group_key = self._get_user_group_key(user, passphrase_key)
            mail_group_key = self._get_mail_group_key(user, user_group_key)

            readable = []
            for blob_id, blob_data in blob_payloads.items():
                mail = blob_to_mail.get(blob_id)
                if not mail:
                    continue
                enc_session = _b64decode_any(mail.get("102"))
                session_key = None
                if enc_session:
                    try:
                        session_key = TutaCryptoCore.decrypt_key(mail_group_key, enc_session)
                    except Exception:
                        session_key = None
                if session_key is None:
                    # 尝试 bucketKey 路径
                    session_key = self._resolve_session_key_from_bucket(mail, user, user_group_key, mail_group_key)
                if not session_key:
                    continue

                body_text = self._decrypt_mail_body_from_blob(blob_data, session_key)
                subject = self._decrypt_encrypted_value(mail.get("105"), session_key, compressed=False)
                details = self._extract_mail_details(blob_data)
                sent_ms = details.get("1284") if isinstance(details, dict) else None

                readable.append({
                    "mail_id": mail.get("99"),
                    "subject": subject,
                    "received_date": mail.get("107"),
                    "received_date_iso": _ms_to_iso(mail.get("107")),
                    "sent_date": sent_ms,
                    "sent_date_iso": _ms_to_iso(sent_ms) if sent_ms else None,
                    "body": body_text,
                })

            if readable:
                readable_path = os.path.join(output_dir, "mail_readable.json")
                with open(readable_path, "w", encoding="utf-8") as f:
                    json.dump(readable, f, ensure_ascii=False, indent=2)

                txt_path = os.path.join(output_dir, "mail_readable.txt")
                with open(txt_path, "w", encoding="utf-8") as f:
                    for item in readable:
                        f.write(f"ID: {item.get('mail_id')}\n")
                        f.write(f"Subject: {item.get('subject')}\n")
                        if item.get("sent_date_iso"):
                            f.write(f"Sent: {item.get('sent_date_iso')}\n")
                        if item.get("received_date_iso"):
                            f.write(f"Received: {item.get('received_date_iso')}\n")
                        f.write("Body:\n")
                        f.write(item.get("body") or "")
                        f.write("\n" + "=" * 60 + "\n\n")

        return saved

    # ==================== 完整注册流程 ====================

    def run_register(self, email: str, password: str):
        """执行完整的 Tuta 邮箱注册流程
        
        流程:
        1. TimeLock Captcha → 获取 puzzle 并解题
        2. 邮箱可用性检查
        3. Registration Captcha → 提交 puzzle 解
        4. 获取系统公钥
        5. 创建账号 → 生成密钥 + 提交加密数据
        6. 获取 Salt → 用于登录
        7. 创建 Session → 获取 accessToken
        """
        try:
            self.last_error = None
            # Step 1: TimeLock Captcha
            status, data = self.solve_timelock_captcha()
            _random_delay(0.5, 1.5)

            # Step 2: 检查邮箱可用性
            status, data = self.check_email_availability(email)
            if status != 200:
                raise Exception(f"邮箱可用性检查失败 ({status}): {data}")
            _random_delay(0.3, 0.8)

            # Step 3: 注册验证码
            status, data = self.request_registration_captcha(email)
            if status not in (200, 201):
                raise Exception(f"注册验证码请求失败 ({status}): {data}")
            _random_delay(0.5, 1.0)

            # Step 4: 获取系统公钥
            status, data = self.get_system_keys()
            if status != 200:
                raise Exception(f"获取系统公钥失败 ({status}): {data}")
            _random_delay(0.3, 0.8)

            # Step 5: 创建账号（核心步骤）
            status, data = self.create_account(email, password)
            if status != 201:
                raise Exception(f"创建账号失败 ({status}): {data}")
            _random_delay(0.5, 1.5)

            # Step 6: 获取 Salt
            status, salt_data = self.get_salt(email)
            if status != 200:
                raise Exception(f"获取 Salt 失败 ({status}): {salt_data}")
            salt_b64 = salt_data.get("422", "")
            _random_delay(0.3, 0.5)

            # Step 7: 创建 Session
            status, session_data = self.create_session(email, password, salt_b64)
            if status not in [200, 201]:
                raise Exception(f"创建 Session 失败 ({status}): {session_data}")

            # 构建并保存记录（用于取件/解密）
            self.account_record = self._build_account_record(
                email=email,
                password=password,
                salt_b64=salt_b64,
                session_data=session_data if isinstance(session_data, dict) else {"raw": session_data},
            )

            self._print(f"✅ 注册成功! 邮箱: {email}")
            return True

        except Exception as e:
            self.last_error = str(e)
            self._print(f"❌ 注册失败: {e}")
            traceback.print_exc()
            return False


# ================= 并发批量注册 =================

def _register_one(idx, total, proxy, output_file, output_file_detail, mail_domain, proxy_mode, dynamic_proxy_url, dynamic_proxy_protocol):
    """单个注册任务（在线程中运行）"""
    reg = None
    try:
        max_proxy_attempts = int(_CONFIG.get("dynamic_proxy_max_attempts", DEFAULT_DYNAMIC_PROXY_MAX_ATTEMPTS) or 5)
        attempt = 0
        use_proxy = proxy
        while True:
            attempt += 1
            if proxy_mode == "dynamic":
                use_proxy = _fetch_dynamic_proxy(dynamic_proxy_url, dynamic_proxy_protocol)
            elif proxy_mode == "none":
                use_proxy = None

            reg = TutaRegister(proxy=use_proxy, tag=f"{idx}")

            # 生成随机邮箱和密码
            email_prefix = _random_email_prefix()
            email = f"{email_prefix}@{mail_domain}"
            password = _generate_password()
            reg.tag = email_prefix  # 更新 tag 为邮箱前缀

            with _print_lock:
                print(f"\n{'='*60}")
                print(f"  [{idx}/{total}] 注册: {email}")
                print(f"  密码: {password}")
                print(f"{'='*60}")

            # 执行注册
            success = reg.run_register(email, password)
            if success:
                break
            # 动态代理：连接失败则换 IP 重试
            if proxy_mode == "dynamic":
                err_msg = getattr(reg, "last_error", None) or "注册失败"
                if _is_network_error(err_msg) and attempt < max_proxy_attempts:
                    with _print_lock:
                        print(f"[Retry] 动态代理连接失败，切换 IP 重试 ({attempt}/{max_proxy_attempts})")
                    continue
            break

        if success:
            # 线程安全写入结果
            with _file_lock:
                with open(output_file, "a", encoding="utf-8") as out:
                    access_token = getattr(reg, "access_token", "") or ""
                    out.write(f"{email}----{password}----{reg.client_id}----{access_token}\n")
                if output_file_detail:
                    record = getattr(reg, "account_record", None)
                    if record:
                        with open(output_file_detail, "a", encoding="utf-8") as out_detail:
                            out_detail.write(json.dumps(record, ensure_ascii=False) + "\n")

            with _print_lock:
                print(f"\n[OK] [{email_prefix}] {email} 注册成功!")
            return True, email, None
        else:
            return False, email, "注册流程失败"

    except Exception as e:
        error_msg = str(e)
        with _print_lock:
            print(f"\n[FAIL] [{idx}] 注册失败: {error_msg}")
            traceback.print_exc()
        return False, None, error_msg


def run_batch(total_accounts: int = 3, output_file="tuta_accounts.txt",
              max_workers=3, proxy=None, mail_domain="tutamail.com",
              output_file_detail: str = None,
              proxy_mode: str = "local",
              dynamic_proxy_url: str = "",
              dynamic_proxy_protocol: str = "socks5"):
    """并发批量注册 Tuta 邮箱"""
    actual_workers = min(max_workers, total_accounts)
    print(f"\n{'#'*60}")
    print(f"  Tuta 邮箱批量自动注册")
    print(f"  注册数量: {total_accounts} | 并发数: {actual_workers}")
    print(f"  邮箱域名: {mail_domain}")
    print(f"  输出文件: {output_file}")
    print(f"{'#'*60}\n")

    success_count = 0
    fail_count = 0
    start_time = time.time()

    with ThreadPoolExecutor(max_workers=actual_workers) as executor:
        futures = {}
        for idx in range(1, total_accounts + 1):
            future = executor.submit(
                _register_one, idx, total_accounts, proxy, output_file, output_file_detail, mail_domain,
                proxy_mode, dynamic_proxy_url, dynamic_proxy_protocol
            )
            futures[future] = idx

        for future in as_completed(futures):
            idx = futures[future]
            try:
                ok, email, err = future.result()
                if ok:
                    success_count += 1
                else:
                    fail_count += 1
                    print(f"  [账号 {idx}] 失败: {err}")
            except Exception as e:
                fail_count += 1
                with _print_lock:
                    print(f"[FAIL] 账号 {idx} 线程异常: {e}")

    elapsed = time.time() - start_time
    avg = elapsed / total_accounts if total_accounts else 0
    print(f"\n{'#'*60}")
    print(f"  注册完成! 耗时 {elapsed:.1f} 秒")
    print(f"  总数: {total_accounts} | 成功: {success_count} | 失败: {fail_count}")
    print(f"  平均速度: {avg:.1f} 秒/个")
    if success_count > 0:
        print(f"  结果文件: {output_file}")
    print(f"{'#'*60}")


# ================= 主函数 =================

def main():
    print("=" * 60)
    print("  Tuta 邮箱批量自动注册工具")
    print("  ⚠️ 当前为基础脚手架版本，加密逻辑需要补全")
    print("=" * 60)

    # 代理配置
    proxy = DEFAULT_PROXY
    proxy_mode = DEFAULT_PROXY_MODE
    dynamic_proxy_url = DEFAULT_DYNAMIC_PROXY_URL
    dynamic_proxy_protocol = DEFAULT_DYNAMIC_PROXY_PROTOCOL

    print("\n代理模式选择：")
    print("  1. 固定代理")
    print("  2. 动态代理")
    print("  3. 不使用代理")
    mode_input = input(f"选择代理模式 (默认 {proxy_mode}): ").strip().lower()
    if mode_input in ("1", "fixed", "local"):
        proxy_mode = "local"
    elif mode_input in ("2", "dynamic"):
        proxy_mode = "dynamic"
    elif mode_input in ("3", "none"):
        proxy_mode = "none"

    if proxy_mode == "local":
        if proxy:
            print(f"[Info] 检测到默认代理: {proxy}")
            use_default = input("使用此代理? (Y/n): ").strip().lower()
            if use_default == "n":
                proxy = input("输入代理地址 (留空=不使用代理): ").strip() or None
        else:
            proxy = input("输入代理地址 (如 http://127.0.0.1:7890，留空=不使用代理): ").strip() or None
        if proxy:
            print(f"[Info] 使用固定代理: {proxy}")
        else:
            print("[Info] 固定代理为空，将不使用代理")
            proxy_mode = "none"
    elif proxy_mode == "dynamic":
        if dynamic_proxy_url:
            print(f"[Info] 当前动态代理链接: {dynamic_proxy_url}")
            use_default = input("使用此动态代理链接? (Y/n): ").strip().lower()
            if use_default == "n":
                dynamic_proxy_url = input("输入动态代理链接: ").strip()
        else:
            dynamic_proxy_url = input("输入动态代理链接: ").strip()
        proto_input = input(f"动态代理协议 (默认 {dynamic_proxy_protocol}): ").strip().lower()
        if proto_input in ("socks5", "http", "https"):
            dynamic_proxy_protocol = proto_input
        print(f"[Info] 使用动态代理: {dynamic_proxy_url} ({dynamic_proxy_protocol})")
        proxy = None
    else:
        print("[Info] 不使用代理")

    # 邮箱域名选择
    print(f"\n可用的免费邮箱域名:")
    for i, domain in enumerate(TUTA_FREE_DOMAINS, 1):
        marker = " (默认)" if domain == DEFAULT_MAIL_DOMAIN else ""
        print(f"  {i}. @{domain}{marker}")
    domain_input = input(f"选择域名序号 (默认 @{DEFAULT_MAIL_DOMAIN}): ").strip()
    if domain_input.isdigit() and 1 <= int(domain_input) <= len(TUTA_FREE_DOMAINS):
        mail_domain = TUTA_FREE_DOMAINS[int(domain_input) - 1]
    else:
        mail_domain = DEFAULT_MAIL_DOMAIN
    print(f"[Info] 使用域名: @{mail_domain}")

    # 注册数量
    count_input = input(f"\n注册账号数量 (默认 {DEFAULT_TOTAL_ACCOUNTS}): ").strip()
    total_accounts = int(count_input) if count_input.isdigit() and int(count_input) > 0 else DEFAULT_TOTAL_ACCOUNTS

    workers_input = input("并发数 (默认 3): ").strip()
    max_workers = int(workers_input) if workers_input.isdigit() and int(workers_input) > 0 else 3

    run_batch(
        total_accounts=total_accounts,
        output_file=DEFAULT_OUTPUT_FILE,
        max_workers=max_workers,
        proxy=proxy,
        mail_domain=mail_domain,
        output_file_detail=DEFAULT_OUTPUT_FILE_DETAIL,
        proxy_mode=proxy_mode,
        dynamic_proxy_url=dynamic_proxy_url,
        dynamic_proxy_protocol=dynamic_proxy_protocol,
    )


if __name__ == "__main__":
    main()
