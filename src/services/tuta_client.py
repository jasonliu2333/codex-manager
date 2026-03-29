import base64
import html
import json
import os
import random
import re
import string
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode, quote

from curl_cffi import requests as curl_requests

from .tuta_crypto_core import TutaCryptoCore


class TutaApiVersion:
    SYS = "146"
    TUTANOTA = "107"
    STORAGE = "14"
    CLIENT = "340.260326.1"


class TutaEndpoints:
    BASE_URL = "https://app.tuta.com"
    SALT = "/rest/sys/saltservice"
    SESSION = "/rest/sys/sessionservice"
    USER = "/rest/sys/user"
    BLOB_ACCESS_TOKEN = "/rest/storage/blobaccesstokenservice"


def _encode_query_body(body_dict: Dict[str, Any]) -> str:
    json_str = json.dumps(body_dict, separators=(",", ":"))
    return quote(json_str)


def _base64url_to_base64(b64url_str: str) -> str:
    s = b64url_str.replace("-", "+").replace("_", "/")
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return s


def _b64decode_any(val: Any) -> Optional[bytes]:
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


def _ms_to_iso(ms_val: Any) -> Optional[str]:
    try:
        return datetime.utcfromtimestamp(int(ms_val) / 1000).isoformat() + "Z"
    except Exception:
        return None


def _generate_random_id() -> str:
    raw = os.urandom(4)
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")[:6]


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
    profile = random.choice(_CHROME_PROFILES)
    major = profile["major"]
    build = profile["build"]
    patch = random.randint(*profile["patch_range"])
    full_ver = f"{major}.0.{build}.{patch}"
    ua = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{full_ver} Safari/537.36"
    return profile["impersonate"], full_ver, ua, profile["sec_ch_ua"]


class TutaMailClient:
    def __init__(
        self,
        proxy_url: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: int = 30,
        impersonate: str = "",
    ):
        self.base_url = base_url or TutaEndpoints.BASE_URL
        imp, full_ver, ua, sec_ch_ua = _random_chrome_version()
        self.session = curl_requests.Session(impersonate=impersonate or imp, timeout=timeout)
        if proxy_url:
            self.session.proxies = {"http": proxy_url, "https": proxy_url}
        self.session.headers.update({
            "User-Agent": ua,
            "Accept-Language": random.choice([
                "en-US,en;q=0.9",
                "en-US,en;q=0.9,zh-CN;q=0.8",
                "en,en-US;q=0.9",
                "en-US,en;q=0.8",
            ]),
            "sec-ch-ua": sec_ch_ua,
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": "\"Windows\"",
        })
        self.access_token: Optional[str] = None
        self.user_id: Optional[str] = None
        self.passphrase_key: Optional[bytes] = None
        self.salt_b64: Optional[str] = None
        self.login_email: Optional[str] = None

    def _api_headers(self, version: str, content_type: bool, authenticated: bool) -> Dict[str, str]:
        headers = {"v": version, "cv": TutaApiVersion.CLIENT}
        if content_type:
            headers["Content-Type"] = "application/json"
        if authenticated and self.access_token:
            headers["accessToken"] = self.access_token
        return headers

    def _api_get(
        self,
        endpoint: str,
        query_body: Optional[Dict[str, Any]] = None,
        version: str = TutaApiVersion.SYS,
        authenticated: bool = False,
        content_type: bool = True,
    ):
        url = f"{self.base_url}{endpoint}"
        if query_body is not None:
            encoded = _encode_query_body(query_body)
            url = f"{url}?_body={encoded}"
        headers = self._api_headers(version, content_type, authenticated)
        return self.session.get(url, headers=headers)

    def _api_post(
        self,
        endpoint: str,
        json_body: Dict[str, Any],
        version: str = TutaApiVersion.SYS,
        authenticated: bool = False,
    ):
        url = f"{self.base_url}{endpoint}"
        headers = self._api_headers(version, True, authenticated)
        return self.session.post(url, json=json_body, headers=headers)

    def get_salt(self, email: str) -> Tuple[int, Dict[str, Any]]:
        query_body = {"418": "0", "419": email}
        r = self._api_get(TutaEndpoints.SALT, query_body)
        try:
            data = r.json() if r.text else {}
        except Exception:
            data = {"raw": r.text[:500]}
        return r.status_code, data

    def create_session(
        self,
        email: str,
        password: str,
        salt_b64: Optional[str] = None,
        access_key: Optional[str] = None,
        auth_token: Optional[str] = None,
        max_attempts: int = 3,
    ) -> Tuple[int, Dict[str, Any]]:
        if not salt_b64:
            status, salt_data = self.get_salt(email)
            if status != 200:
                return status, salt_data
            salt_b64 = salt_data.get("422", "")

        salt = base64.b64decode(salt_b64) if salt_b64 else os.urandom(16)
        passphrase_key = TutaCryptoCore.argon2_derive_passphrase_key(password, salt)
        auth_verifier = TutaCryptoCore.get_auth_verifier(passphrase_key)
        auth_verifier_b64url = base64.urlsafe_b64encode(auth_verifier).decode().rstrip("=")

        self.salt_b64 = salt_b64
        self.passphrase_key = passphrase_key
        self.login_email = email

        post_body = {
            "1212": "0",
            "1213": email.lower().strip(),
            "1214": auth_verifier_b64url,
            "1215": "Chrome Browser",
            "1216": access_key,
            "1217": auth_token,
            "1218": [],
            "1417": None,
        }

        last_data: Dict[str, Any] = {}
        last_status = 0
        for attempt in range(1, max_attempts + 1):
            r = self._api_post(TutaEndpoints.SESSION, post_body)
            last_status = r.status_code
            try:
                data = r.json() if r.text else {}
            except Exception:
                data = {"raw": r.text[:500]}
            last_data = data if isinstance(data, dict) else {"raw": str(data)[:500]}

            if last_status in (200, 201):
                access_token = last_data.get("1221") or last_data.get(1221)
                user_id = last_data.get("1223") or last_data.get(1223)
                if isinstance(user_id, (list, tuple)) and user_id:
                    user_id = user_id[0]
                if access_token:
                    self.access_token = access_token
                    try:
                        self.session.headers["accessToken"] = access_token
                    except Exception:
                        pass
                if user_id:
                    self.user_id = user_id
                return last_status, last_data

            if last_status in (429, 503) and attempt < max_attempts:
                backoff = min(8, 1.5 * attempt + random.random())
                time.sleep(backoff)
                continue

            return last_status, last_data

        return last_status, last_data

    def get_user(self, user_id: Optional[str] = None) -> Tuple[int, Dict[str, Any]]:
        uid = user_id or self.user_id
        if not uid:
            raise ValueError("缺少 user_id")
        endpoint = f"{TutaEndpoints.USER}/{uid}"
        r = self._api_get(endpoint, query_body=None, authenticated=True, content_type=False)
        try:
            data = r.json() if r.text else {}
        except Exception:
            data = {"raw": r.text[:500]}
        return r.status_code, data

    def get_group(self, group_id: str) -> Tuple[int, Dict[str, Any]]:
        if not group_id:
            raise ValueError("缺少 group_id")
        endpoint = f"/rest/sys/group/{group_id}"
        r = self._api_get(endpoint, query_body=None, authenticated=True, content_type=False)
        try:
            data = r.json() if r.text else {}
        except Exception:
            data = {"raw": r.text[:500]}
        return r.status_code, data

    @staticmethod
    def _extract_mail_group_id(user_data: Dict[str, Any]) -> Optional[str]:
        memberships = user_data.get("96", []) if isinstance(user_data, dict) else []
        for m in memberships:
            if str(m.get("1030")) == "5":
                group = m.get("29")
                if isinstance(group, list) and group:
                    return group[0]
        return None

    @staticmethod
    def _extract_group_membership(user_data: Dict[str, Any], group_type: str) -> Optional[Dict[str, Any]]:
        memberships = user_data.get("96", []) if isinstance(user_data, dict) else []
        for m in memberships:
            if str(m.get("1030")) == str(group_type):
                return m
        return None

    @staticmethod
    def _find_membership_by_group_id(user_data: Dict[str, Any], group_id: str) -> Optional[Dict[str, Any]]:
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
    def _extract_first_dict(value: Any) -> Optional[Dict[str, Any]]:
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    return item
            if value and isinstance(value[0], dict):
                return value[0]
            return None
        return value if isinstance(value, dict) else None

    def _get_user_group_key(self, user_data: Dict[str, Any], passphrase_key: bytes) -> bytes:
        raw_user_group = user_data.get("95") if isinstance(user_data, dict) else None
        user_group = self._extract_first_dict(raw_user_group)
        if not isinstance(user_group, dict):
            raise ValueError("userGroup 缺失")
        enc_key_b64 = user_group.get("27")
        enc_key = _b64decode_any(enc_key_b64)
        if not enc_key:
            raise ValueError("userGroup symEncGKey 缺失")
        return TutaCryptoCore.decrypt_key(passphrase_key, enc_key)

    def _get_mail_group_key(self, user_data: Dict[str, Any], user_group_key: bytes) -> bytes:
        mail_membership = self._extract_group_membership(user_data, "5")
        if not mail_membership:
            raise ValueError("未找到 Mail Group Membership")
        enc_key_b64 = mail_membership.get("27")
        enc_key = _b64decode_any(enc_key_b64)
        if not enc_key:
            raise ValueError("MailGroup symEncGKey 缺失")
        return TutaCryptoCore.decrypt_key(user_group_key, enc_key)

    def _decrypt_encrypted_value(self, enc_b64: str, session_key: bytes, compressed: bool = False) -> str:
        if enc_b64 in (None, ""):
            return ""
        enc_bytes = _b64decode_any(enc_b64)
        if not enc_bytes:
            return ""
        plain = TutaCryptoCore.decrypt_bytes(session_key, enc_bytes)
        if compressed:
            return TutaCryptoCore.decompress_string(plain)
        return plain.decode("utf-8", errors="replace")

    def _extract_mail_details(self, blob_data: Any) -> Optional[Dict[str, Any]]:
        blob = self._extract_first_dict(blob_data)
        if not isinstance(blob, dict):
            return None
        details_list = blob.get("1305") or []
        return self._extract_first_dict(details_list)

    def _decrypt_mail_body_from_blob(self, blob_data: Any, session_key: bytes) -> str:
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

    def _decrypt_pub_enc_bucket_key(
        self,
        pub_enc_bucket_key: str,
        protocol_version: str,
        key_group_id: str,
        user_data: Dict[str, Any],
        user_group_key: bytes,
    ) -> Optional[bytes]:
        if not pub_enc_bucket_key or not key_group_id:
            return None

        cache_key = f"{key_group_id}:{protocol_version}:{pub_enc_bucket_key[:32]}"
        if not hasattr(self, "_bucket_key_cache"):
            self._bucket_key_cache = {}
        if cache_key in self._bucket_key_cache:
            return self._bucket_key_cache.get(cache_key)

        group_key = None
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

        pq_root = Path(__file__).resolve().parents[2] / "tuta_pq"
        script_path = pq_root / "pq_decrypt.mjs"
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
                cwd=str(pq_root),
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

    def _resolve_session_key_from_bucket(
        self,
        mail: Dict[str, Any],
        user_data: Dict[str, Any],
        user_group_key: bytes,
        mail_group_key: bytes,
    ) -> Optional[bytes]:
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

    def get_mailbox_group_root(self, mail_group_id: str) -> Tuple[int, Dict[str, Any]]:
        endpoint = f"/rest/tutanota/mailboxgrouproot/{mail_group_id}"
        r = self._api_get(endpoint, query_body=None, version=TutaApiVersion.TUTANOTA,
                          authenticated=True, content_type=False)
        try:
            data = r.json() if r.text else {}
        except Exception:
            data = {"raw": r.text[:500]}
        return r.status_code, data

    def get_mailbox(self, mailbox_id: str) -> Tuple[int, Dict[str, Any]]:
        endpoint = f"/rest/tutanota/mailbox/{mailbox_id}"
        r = self._api_get(endpoint, query_body=None, version=TutaApiVersion.TUTANOTA,
                          authenticated=True, content_type=False)
        try:
            data = r.json() if r.text else {}
        except Exception:
            data = {"raw": r.text[:500]}
        return r.status_code, data

    def list_mailsets(self, mailset_list_id: str, count: int = 1000, reverse: bool = False) -> Tuple[int, Any]:
        start = "------------"
        reverse_flag = "true" if reverse else "false"
        endpoint = f"/rest/tutanota/mailset/{mailset_list_id}?start={start}&count={count}&reverse={reverse_flag}"
        r = self._api_get(endpoint, query_body=None, version=TutaApiVersion.TUTANOTA,
                          authenticated=True, content_type=False)
        try:
            data = r.json() if r.text else []
        except Exception:
            data = {"raw": r.text[:500]}
        return r.status_code, data

    def list_mailset_entries(self, entry_list_id: str, count: int = 50, reverse: bool = True) -> Tuple[int, Any]:
        start = "_" * 200
        reverse_flag = "true" if reverse else "false"
        endpoint = f"/rest/tutanota/mailsetentry/{entry_list_id}?start={start}&count={count}&reverse={reverse_flag}"
        r = self._api_get(endpoint, query_body=None, version=TutaApiVersion.TUTANOTA,
                          authenticated=True, content_type=False)
        try:
            data = r.json() if r.text else []
        except Exception:
            data = {"raw": r.text[:500]}
        return r.status_code, data

    def get_mails(self, mail_list_id: str, mail_ids: List[str]) -> Tuple[int, Any]:
        ids_param = ",".join(mail_ids)
        endpoint = f"/rest/tutanota/mail/{mail_list_id}?ids={ids_param}"
        r = self._api_get(endpoint, query_body=None, version=TutaApiVersion.TUTANOTA,
                          authenticated=True, content_type=False)
        try:
            data = r.json() if r.text else []
        except Exception:
            data = {"raw": r.text[:500]}
        return r.status_code, data

    def request_blob_access_token(self, archive_id: str, archive_data_type: str = None) -> Tuple[int, Optional[Dict[str, Any]]]:
        post_body = {
            "78": "0",
            "80": [],
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

    def get_mail_details_blob(self, archive_id: str, blob_ids: List[str], blob_access_token: str, server_url: str):
        params = {
            "accessToken": self.access_token,
            "v": TutaApiVersion.TUTANOTA,
            "ids": ",".join(blob_ids),
            "blobAccessToken": blob_access_token,
            "cv": TutaApiVersion.CLIENT,
        }
        base = server_url.rstrip("/") if server_url else self.base_url
        url = f"{base}/rest/tutanota/maildetailsblob/{archive_id}?{urlencode(params)}"
        headers = self._api_headers(version=TutaApiVersion.TUTANOTA, content_type=False, authenticated=False)
        r = self.session.get(url, headers=headers)
        return r.status_code, r

    def download_mail_details(
        self,
        output_dir: str = "mail_details",
        max_mails: int = 5,
        decrypt: bool = False,
        password: Optional[str] = None,
        user_data: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        os.makedirs(output_dir, exist_ok=True)

        user = user_data
        if not user:
            status = None
            user = None
            for attempt in range(1, 4):
                status, user = self.get_user()
                if status == 200:
                    break
                if status in (401, 429):
                    time.sleep(2 * attempt)
                    continue
                break
            if status != 200 or not user:
                raise RuntimeError(f"获取用户失败 ({status})")

        mail_group_id = self._extract_mail_group_id(user)
        if not mail_group_id:
            raise RuntimeError("未找到 Mail Group")

        status, mbgr = self.get_mailbox_group_root(mail_group_id)
        if status != 200:
            raise RuntimeError(f"获取 MailboxGroupRoot 失败 ({status})")
        mailbox_id = mbgr.get("699", [None])[0]
        if not mailbox_id:
            raise RuntimeError("未找到 mailbox_id")

        status, mailbox = self.get_mailbox(mailbox_id)
        if status != 200:
            raise RuntimeError(f"获取 Mailbox 失败 ({status})")

        mailset_ref_list = mailbox.get("443") or []
        mailset_list_id = None
        if isinstance(mailset_ref_list, list) and mailset_ref_list:
            ref = mailset_ref_list[0]
            if isinstance(ref, dict):
                ids = ref.get("442")
                if isinstance(ids, list) and ids:
                    mailset_list_id = ids[0]
        if not mailset_list_id:
            raise RuntimeError("未找到 MailSet 列表 ID")

        status, mailsets = self.list_mailsets(mailset_list_id)
        if status != 200 or not isinstance(mailsets, list):
            raise RuntimeError(f"获取 MailSet 失败 ({status})")

        inbox = None
        for ms in mailsets:
            if str(ms.get("436")) == "1":
                inbox = ms
                break
        if not inbox:
            raise RuntimeError("未找到 INBOX MailSet")

        entry_list_id = inbox.get("1459", [None])[0]
        if not entry_list_id:
            raise RuntimeError("未找到 MailSetEntry 列表 ID")

        status, entries = self.list_mailset_entries(entry_list_id, count=max_mails, reverse=True)
        if status != 200 or not isinstance(entries, list):
            raise RuntimeError(f"获取 MailSetEntry 失败 ({status})")
        if not entries:
            return []

        mail_list_id = entries[0].get("1456", [[None, None]])[0][0]
        mail_ids = [e.get("1456", [[None, None]])[0][1] for e in entries if e.get("1456")]

        status, mails = self.get_mails(mail_list_id, mail_ids)
        if status != 200 or not isinstance(mails, list):
            raise RuntimeError(f"获取 Mail 失败 ({status})")

        index_path = os.path.join(output_dir, "mail_index.json")
        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(mails, f, ensure_ascii=False, indent=2)

        archive_to_blob_ids: Dict[str, List[str]] = {}
        blob_to_mail: Dict[str, Dict[str, Any]] = {}
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
        blob_payloads: Dict[str, Any] = {}
        for archive_id, blob_ids in archive_to_blob_ids.items():
            status, token_info = self.request_blob_access_token(archive_id)
            if status not in (200, 201) or not token_info:
                raise RuntimeError(f"获取 blobAccessToken 失败 ({status})")
            blob_access_token = token_info.get("blob_access_token")
            server_url = token_info.get("server_url")
            if not blob_access_token or not server_url:
                raise RuntimeError("blobAccessToken 返回缺失")

            for blob_id in blob_ids:
                status, resp = self.get_mail_details_blob(archive_id, [blob_id], blob_access_token, server_url)
                if status != 200:
                    raise RuntimeError(f"获取 maildetailsblob 失败 ({status})")

                ctype = resp.headers.get("content-type", "")
                ext = ".bin"
                if "application/json" in ctype or "text/" in ctype:
                    ext = ".json"
                out_path = os.path.join(output_dir, f"{blob_id}{ext}")
                with open(out_path, "wb") as f:
                    f.write(resp.content)
                saved.append(out_path)

                if ext == ".json":
                    try:
                        blob_payloads[blob_id] = resp.json()
                    except Exception:
                        pass
                if blob_id not in blob_payloads:
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
            passphrase_key = self.passphrase_key
            if passphrase_key is None:
                salt_b64 = self.salt_b64 or (user.get("90") if isinstance(user, dict) else None)
                if not salt_b64 and self.login_email:
                    _, salt_data = self.get_salt(self.login_email)
                    salt_b64 = salt_data.get("422")
                if not salt_b64:
                    raise RuntimeError("解密需要 salt，当前未获取")
                if not password:
                    raise RuntimeError("解密需要 password")
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
                    session_key = self._resolve_session_key_from_bucket(
                        mail, user, user_group_key, mail_group_key
                    )
                if not session_key:
                    continue

                body_text = self._decrypt_mail_body_from_blob(blob_data, session_key)
                body_plain = _html_to_text(body_text)
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
                    "body_plain": body_plain,
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
                        f.write(item.get("body_plain") or item.get("body") or "")
                        f.write("\n" + "=" * 60 + "\n\n")

                plain_path = os.path.join(output_dir, "mail_plain.txt")
                with open(plain_path, "w", encoding="utf-8") as f:
                    for item in readable:
                        f.write(item.get("body_plain") or item.get("body") or "")
                        f.write("\n" + "=" * 60 + "\n\n")

        return saved
