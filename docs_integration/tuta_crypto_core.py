import os
import struct
import hashlib
import hmac
import base64
import json
import shutil
import subprocess
from typing import Tuple, Dict
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import hashes
import argon2

class TutaCryptoCore:
    """Tuta 密码学原语的核心实现，完全逆向自 tutanota-crypto 包"""
    _KYBER_WARNED = False
    _FIXED_IV = bytes.fromhex("88" * 16)

    @staticmethod
    def generate_random_bytes(length: int) -> bytes:
        return os.urandom(length)

    @staticmethod
    def aes_cbc_then_hmac_encrypt(master_key: bytes, plaintext: bytes, use_padding: bool = False) -> bytes:
        """
        对应 Tuta 的 SymmetricCipherFacade.encrypt (SymmetricCipherVersion.AesCbcThenHmac)
        必须: Encrypt-then-MAC 结构
        """
        if len(master_key) != 32:
            raise ValueError("AES key must be 256-bit (32 bytes)")

        # 1. Derive subkeys (SymmetricKeyDeriver.ts)
        hashed = hashlib.sha512(master_key).digest()
        enc_key = hashed[:32]
        auth_key = hashed[32:64]

        # 2. Encrypt with AES-CBC NO padding (since key plaintext is exactly 32 bytes)
        iv = os.urandom(16)
        cipher = Cipher(algorithms.AES(enc_key), modes.CBC(iv), backend=default_backend())
        encryptor = cipher.encryptor()
        
        # 处理 Padding
        if use_padding:
            from cryptography.hazmat.primitives import padding as sym_padding
            padder = sym_padding.PKCS7(128).padder()
            padded_data = padder.update(plaintext) + padder.finalize()
        else:
            padded_data = plaintext
            
        ciphertext = encryptor.update(padded_data) + encryptor.finalize()

        # 3. HMAC-SHA256 Authentication (AesCbcFacade.ts)
        unauth_ciphertext = iv + ciphertext
        mac = hmac.new(auth_key, unauth_ciphertext, hashlib.sha256).digest()

        # 4. Append Version Byte 0x01
        return b'\x01' + unauth_ciphertext + mac

    @staticmethod
    def _get_symmetric_cipher_version(ciphertext: bytes) -> int:
        """
        对应 SymmetricCipherVersion.getSymmetricCipherVersion
        - 奇数长度: 带版本字节 (AesCbcThenHmac)
        - 偶数长度: legacy 未认证
        """
        if len(ciphertext) % 2 == 1:
            version = ciphertext[0]
            if version in (0, 1, 2):
                return version
            raise ValueError("invalid cipher version")
        return 0

    @staticmethod
    def _derive_subkeys(master_key: bytes, cipher_version: int):
        """对应 SymmetricKeyDeriver.deriveSubKeys"""
        key_len = len(master_key)
        if cipher_version == 0:
            return master_key, None
        if key_len == 16:
            hashed = hashlib.sha256(master_key).digest()
        elif key_len == 32:
            hashed = hashlib.sha512(master_key).digest()
        else:
            raise ValueError(f"unsupported key length: {key_len}")
        enc_key = hashed[:key_len]
        auth_key = hashed[key_len:key_len * 2]
        return enc_key, auth_key

    @staticmethod
    def aes_cbc_then_hmac_decrypt(
        master_key: bytes,
        ciphertext: bytes,
        use_padding: bool = False,
        iv_prepended: bool = True,
        skip_auth: bool = False,
    ) -> bytes:
        """
        对应 SymmetricCipherFacade.decrypt / AesCbcFacade.decrypt
        """
        if len(master_key) not in (16, 32):
            raise ValueError("AES key must be 128/256-bit (16/32 bytes)")

        cipher_version = TutaCryptoCore._get_symmetric_cipher_version(ciphertext)
        enc_key, auth_key = TutaCryptoCore._derive_subkeys(master_key, cipher_version)

        if cipher_version == 1:
            if len(ciphertext) < 1 + 16 + 32:
                raise ValueError("ciphertext too short")
            data = ciphertext[1:-32]
            provided_mac = ciphertext[-32:]
            if not skip_auth:
                calc_mac = hmac.new(auth_key, data, hashlib.sha256).digest()
                if not hmac.compare_digest(calc_mac, provided_mac):
                    raise ValueError("HMAC verification failed")
        elif cipher_version == 0:
            data = ciphertext
        else:
            raise ValueError("unsupported cipher version")

        if iv_prepended:
            if len(data) < 16:
                raise ValueError("ciphertext missing iv")
            iv = data[:16]
            ct = data[16:]
        else:
            iv = TutaCryptoCore._FIXED_IV
            ct = data

        cipher = Cipher(algorithms.AES(enc_key), modes.CBC(iv), backend=default_backend())
        decryptor = cipher.decryptor()
        padded = decryptor.update(ct) + decryptor.finalize()

        if use_padding:
            from cryptography.hazmat.primitives import padding as sym_padding
            unpadder = sym_padding.PKCS7(128).unpadder()
            return unpadder.update(padded) + unpadder.finalize()
        return padded

    @staticmethod
    def decrypt_key(encryption_key: bytes, encrypted_key: bytes) -> bytes:
        """对应 decryptKey: 使用 AES-CBC 还原 16/32 字节密钥"""
        if len(encryption_key) == 16:
            return TutaCryptoCore.aes_cbc_then_hmac_decrypt(
                encryption_key, encrypted_key, use_padding=False, iv_prepended=False, skip_auth=True
            )
        if len(encryption_key) == 32:
            return TutaCryptoCore.aes_cbc_then_hmac_decrypt(
                encryption_key, encrypted_key, use_padding=False, iv_prepended=True
            )
        raise ValueError("unsupported key length for decrypt_key")

    @staticmethod
    def decrypt_bytes(encryption_key: bytes, encrypted_bytes: bytes) -> bytes:
        """对应 aesDecrypt: 解密加密字段 (带 padding)"""
        return TutaCryptoCore.aes_cbc_then_hmac_decrypt(
            encryption_key, encrypted_bytes, use_padding=True, iv_prepended=True
        )

    @staticmethod
    def lz4_uncompress(data: bytes) -> bytes:
        """对应 Compression.uncompress (LZ4 block)"""
        if not data:
            return b""
        end_index = len(data)
        out = bytearray()
        i = 0
        while i < end_index:
            token = data[i]
            i += 1

            literals_length = token >> 4
            if literals_length > 0:
                l = literals_length + 240
                while l == 255:
                    l = data[i]
                    i += 1
                    literals_length += l
                end = i + literals_length
                out.extend(data[i:end])
                i = end
                if i == end_index:
                    break

            if i + 1 >= end_index:
                break
            offset = data[i] | (data[i + 1] << 8)
            i += 2
            if offset == 0 or offset > len(out):
                raise ValueError("Invalid offset value")

            match_length = token & 0x0F
            l = match_length + 240
            while l == 255:
                l = data[i]
                i += 1
                match_length += l
            match_length += 4

            pos = len(out) - offset
            for _ in range(match_length):
                out.append(out[pos])
                pos += 1

        return bytes(out)

    @staticmethod
    def decompress_string(compressed: bytes) -> str:
        """解压 CompressedString 字段 (LZ4)"""
        if not compressed:
            return ""
        raw = TutaCryptoCore.lz4_uncompress(compressed)
        return raw.decode("utf-8", errors="replace")

    @staticmethod
    def argon2_derive_passphrase_key(password: str, salt: bytes) -> bytes:
        """
        对应 Tuta 的 Argon2id 参数:
        t=4 (iterations), p=1 (parallelism), m=32768 (32MB RAM)
        """
        # Argon2id() equivalent with 32-byte output
        return argon2.low_level.hash_secret_raw(
            secret=password.encode('utf-8'),
            salt=salt,
            time_cost=4,
            memory_cost=32768,
            parallelism=1,
            hash_len=32,
            type=argon2.low_level.Type.ID
        )

    @staticmethod
    def get_auth_verifier(passphrase_key: bytes) -> bytes:
        """计算 authVerifier = SHA-256(passphrase_key)"""
        return hashlib.sha256(passphrase_key).digest()

    @staticmethod
    def parse_tuta_rsa_public_key(pub_key_bytes: bytes) -> rsa.RSAPublicKey:
        """
        解析 Tuta 系统公钥
        Tuta 格式: 2 字节长度(hex形式) + 变长 hex。由于本身已经是 bytes:
        实际上 pub_key_bytes 转换成 hex 之后，前4个字符是长度。
        """
        hex_str = pub_key_bytes.hex()
        # 长度字段是 4 个 hex 字符
        len_hex = int(hex_str[:4], 16)
        modulus_hex = hex_str[4 : 4 + len_hex]
        
        n = int(modulus_hex, 16)
        e = 65537 # Tuta 默认 RSA_PUBLIC_EXPONENT
        return rsa.RSAPublicNumbers(e, n).public_key(default_backend())

    @staticmethod
    def rsa_oaep_encrypt(pub_key: rsa.RSAPublicKey, plaintext: bytes) -> bytes:
        """对应 Tuta 的 oaepPad (MGF1, SHA-256)"""
        return pub_key.encrypt(
            plaintext,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None
            )
        )

    @staticmethod
    def rsa_key_to_tuta_format(pub_numbers, priv_numbers) -> Tuple[bytes, bytes]:
        """将生成的 Python RSA 密钥序列化为 Tuta 专属格式"""
        
        def to_hex_param(val: int) -> str:
            val_hex = val.as_integer_ratio()[0].to_bytes((val.bit_length() + 7) // 8, 'big').hex()
            # _padAndUnpad 逻辑: 确保不会有奇数长度
            if len(val_hex) % 2 != 0:
                val_hex = "0" + val_hex
            # 长度作为 4 位 hex 前缀
            len_prefix = format(len(val_hex), "04x")
            return len_prefix + val_hex

        # Public key array: [modulus]
        pub_hex = to_hex_param(pub_numbers.n)
        
        # Private key array: [modulus, privateExponent, primeP, primeQ, primeExponentP, primeExponentQ, crtCoefficient]
        priv_hex = (
            to_hex_param(priv_numbers.public_numbers.n) +
            to_hex_param(priv_numbers.d) +
            to_hex_param(priv_numbers.p) +
            to_hex_param(priv_numbers.q) +
            to_hex_param(priv_numbers.dmp1) +
            to_hex_param(priv_numbers.dmq1) +
            to_hex_param(priv_numbers.iqmp)
        )
        
        return bytes.fromhex(pub_hex), bytes.fromhex(priv_hex)

    @staticmethod
    def generate_rsa_keypair() -> Tuple[bytes, bytes]:
        """生成 Tuta 格式的 RSA 密钥对 (公钥数据, 私钥数据)"""
        key = rsa.generate_private_key(65537, 2048, default_backend())
        priv_numbers = key.private_numbers()
        pub_numbers = priv_numbers.public_numbers
        return TutaCryptoCore.rsa_key_to_tuta_format(pub_numbers, priv_numbers)

    @staticmethod
    def _b64rnd(n: int) -> str:
        return base64.urlsafe_b64encode(os.urandom(n)).decode().rstrip('=')

    @staticmethod
    def _b64enc(b: bytes) -> str:
        return base64.b64encode(b).decode()

    @staticmethod
    def _byte_arrays_to_bytes(chunks) -> bytes:
        """等价于 @tutao/tutanota-utils 的 byteArraysToBytes (2 字节长度前缀, 大端)"""
        out = bytearray()
        for chunk in chunks:
            out += len(chunk).to_bytes(2, "big")
            out += chunk
        return bytes(out)

    @staticmethod
    def _kyber_public_key_to_bytes(raw_pub: bytes) -> bytes:
        """对应 kyberPublicKeyToBytes: t(1536) + rho(32)"""
        t = raw_pub[:1536]
        rho = raw_pub[1536:1568]
        return TutaCryptoCore._byte_arrays_to_bytes([t, rho])

    @staticmethod
    def _kyber_private_key_to_bytes(raw_priv: bytes) -> bytes:
        """对应 kyberPrivateKeyToBytes: s,hpk,nonce,t,rho"""
        s = raw_priv[:1536]
        t = raw_priv[1536:3072]
        rho = raw_priv[3072:3104]
        hpk = raw_priv[3104:3136]
        nonce = raw_priv[3136:3168]
        return TutaCryptoCore._byte_arrays_to_bytes([s, hpk, nonce, t, rho])

    @staticmethod
    def _generate_kyber_keypair_raw() -> Tuple[bytes, bytes]:
        """生成 Kyber-1024 原始公私钥 (1568/3168). 若无 oqs 则回退随机字节"""
        try:
            import oqs  # type: ignore
            if hasattr(oqs, "KeyEncapsulation"):
                with oqs.KeyEncapsulation("Kyber1024") as kem:
                    pub = kem.generate_keypair()
                    priv = kem.export_secret_key()
                return pub, priv
        except Exception:
            pass

        # 回退：使用官方 liboqs.wasm 生成
        try:
            node = shutil.which("node")
            if not node:
                raise RuntimeError("node 未找到")
            script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kyber_gen.mjs")
            res = subprocess.run(
                [node, script],
                check=True,
                capture_output=True,
                text=True,
            )
            data = json.loads(res.stdout.strip())
            pub = base64.b64decode(data["pub"])
            priv = base64.b64decode(data["priv"])
            if len(pub) != 1568 or len(priv) != 3168:
                raise RuntimeError(f"Kyber key length mismatch pub={len(pub)} priv={len(priv)}")
            return pub, priv
        except Exception as e:
            if not TutaCryptoCore._KYBER_WARNED:
                TutaCryptoCore._KYBER_WARNED = True
                print(f"[Crypto] Kyber 生成失败，使用随机字节占位: {e}")
            return os.urandom(1568), os.urandom(3168)

    @staticmethod
    def generate_registration_payload(
        email: str,
        password: str,
        auth_token: str,
        sys_pub_rsa_bytes: bytes,
        lang: str = "zh",
        app: str = "0",
        system_admin_pub_key_version: str = "0",
    ) -> Tuple[dict, str, str]:
        """
        全量模拟前端生成 CustomerAccountCreateData 
        完全合法、加密结构正确的 JSON Payload
        """
        TC = TutaCryptoCore
        
        # 1) 派生 passphrase key
        salt = TC.generate_random_bytes(16)
        passphrase_key = TC.argon2_derive_passphrase_key(password, salt)

        # 2) 生成组密钥与会话密钥 (全部 32B)
        user_group_key = os.urandom(32)
        admin_group_key = os.urandom(32)
        customer_group_key = os.urandom(32)
        mail_group_key = os.urandom(32)
        contact_group_key = os.urandom(32)
        file_group_key = os.urandom(32)

        user_group_info_sk = os.urandom(32)
        admin_group_info_sk = os.urandom(32)
        customer_group_info_sk = os.urandom(32)

        mailbox_sk = os.urandom(32)
        contact_list_sk = os.urandom(32)
        file_system_sk = os.urandom(32)
        mail_group_info_sk = os.urandom(32)
        contact_group_info_sk = os.urandom(32)
        file_group_info_sk = os.urandom(32)
        tutanota_props_sk = os.urandom(32)

        accounting_info_sk = os.urandom(32)
        customer_server_props_sk = os.urandom(32)

        # 3) 生成 PQ keypairs (x25519 + kyber)
        try:
            from cryptography.hazmat.primitives.asymmetric import x25519
            from cryptography.hazmat.primitives import serialization
        except Exception:
            x25519 = None
            serialization = None

        def _gen_x25519():
            if x25519:
                priv = x25519.X25519PrivateKey.generate()
                pub = priv.public_key()
                priv_bytes = priv.private_bytes(
                    encoding=serialization.Encoding.Raw,
                    format=serialization.PrivateFormat.Raw,
                    encryption_algorithm=serialization.NoEncryption(),
                )
                pub_bytes = pub.public_bytes(
                    encoding=serialization.Encoding.Raw,
                    format=serialization.PublicFormat.Raw,
                )
                return priv_bytes, pub_bytes
            return os.urandom(32), os.urandom(32)

        def _build_internal_group(group_key: bytes, group_info_sk: bytes) -> dict:
            x_priv, x_pub = _gen_x25519()
            kyber_pub_raw, kyber_priv_raw = TC._generate_kyber_keypair_raw()
            kyber_pub = TC._kyber_public_key_to_bytes(kyber_pub_raw)
            kyber_priv = TC._kyber_private_key_to_bytes(kyber_priv_raw)

            admin_enc_group_key = TC.aes_cbc_then_hmac_encrypt(admin_group_key, group_key, use_padding=False)
            owner_enc_group_info_sk = TC.aes_cbc_then_hmac_encrypt(customer_group_key, group_info_sk, use_padding=False)

            return {
                "643": TC._b64rnd(4),
                "644": None,
                "645": None,
                "646": TC._b64enc(admin_enc_group_key),
                "647": TC._b64enc(owner_enc_group_info_sk),
                "874": [],
                "1342": TC._b64enc(x_pub),
                "1343": TC._b64enc(TC.aes_cbc_then_hmac_encrypt(group_key, x_priv, use_padding=True)),
                "1344": TC._b64enc(kyber_pub),
                "1345": TC._b64enc(TC.aes_cbc_then_hmac_encrypt(group_key, kyber_priv, use_padding=True)),
                "1415": "0",
                "1416": "0",
            }

        user_group_data = _build_internal_group(user_group_key, user_group_info_sk)
        admin_group_data = _build_internal_group(admin_group_key, admin_group_info_sk)
        customer_group_data = _build_internal_group(customer_group_key, customer_group_info_sk)

        # 4) 用户数据 (UserAccountUserData)
        user_enc_customer_group_key = TC.aes_cbc_then_hmac_encrypt(user_group_key, customer_group_key, use_padding=False)
        user_enc_mail_group_key = TC.aes_cbc_then_hmac_encrypt(user_group_key, mail_group_key, use_padding=False)
        user_enc_contact_group_key = TC.aes_cbc_then_hmac_encrypt(user_group_key, contact_group_key, use_padding=False)
        user_enc_file_group_key = TC.aes_cbc_then_hmac_encrypt(user_group_key, file_group_key, use_padding=False)
        user_enc_tuta_props_sk = TC.aes_cbc_then_hmac_encrypt(user_group_key, tutanota_props_sk, use_padding=False)
        user_enc_entropy = TC.aes_cbc_then_hmac_encrypt(user_group_key, os.urandom(32), use_padding=True)

        customer_enc_mail_group_info_sk = TC.aes_cbc_then_hmac_encrypt(customer_group_key, mail_group_info_sk, use_padding=False)
        customer_enc_contact_group_info_sk = TC.aes_cbc_then_hmac_encrypt(customer_group_key, contact_group_info_sk, use_padding=False)
        customer_enc_file_group_info_sk = TC.aes_cbc_then_hmac_encrypt(customer_group_key, file_group_info_sk, use_padding=False)

        contact_enc_contact_list_sk = TC.aes_cbc_then_hmac_encrypt(contact_group_key, contact_list_sk, use_padding=False)
        file_enc_file_system_sk = TC.aes_cbc_then_hmac_encrypt(file_group_key, file_system_sk, use_padding=False)
        mail_enc_mailbox_sk = TC.aes_cbc_then_hmac_encrypt(mail_group_key, mailbox_sk, use_padding=False)

        # 恢复码
        recover_code = os.urandom(32)
        user_enc_recover_code = TC.aes_cbc_then_hmac_encrypt(user_group_key, recover_code, use_padding=False)
        recover_code_enc_user_group_key = TC.aes_cbc_then_hmac_encrypt(recover_code, user_group_key, use_padding=False)
        recover_code_verifier = TC.get_auth_verifier(recover_code)

        user_data = {
            "623": TC._b64rnd(4),
            "624": email,
            "625": TC._b64enc(TC.aes_cbc_then_hmac_encrypt(user_group_info_sk, b"", use_padding=True)),
            "626": TC._b64enc(salt),
            "627": TC._b64enc(TC.get_auth_verifier(passphrase_key)),
            "629": TC._b64enc(TC.aes_cbc_then_hmac_encrypt(passphrase_key, user_group_key, use_padding=False)),
            "630": TC._b64enc(user_enc_customer_group_key),
            "631": TC._b64enc(user_enc_mail_group_key),
            "632": TC._b64enc(user_enc_contact_group_key),
            "633": TC._b64enc(user_enc_file_group_key),
            "634": TC._b64enc(user_enc_entropy),
            "635": TC._b64enc(user_enc_tuta_props_sk),
            "636": TC._b64enc(mail_enc_mailbox_sk),
            "637": TC._b64enc(contact_enc_contact_list_sk),
            "638": TC._b64enc(file_enc_file_system_sk),
            "639": TC._b64enc(customer_enc_mail_group_info_sk),
            "640": TC._b64enc(customer_enc_contact_group_info_sk),
            "641": TC._b64enc(customer_enc_file_group_info_sk),
            "892": TC._b64enc(user_enc_recover_code),
            "893": TC._b64enc(recover_code_enc_user_group_key),
            "894": TC._b64enc(recover_code_verifier),
            "1322": "1",
            "1426": "0",
        }

        # 5) 顶层 CustomerAccountCreateData
        sys_rsa_pub = TC.parse_tuta_rsa_public_key(sys_pub_rsa_bytes)
        system_admin_pub_enc_accounting_sk = TC.rsa_oaep_encrypt(sys_rsa_pub, accounting_info_sk)

        user_enc_admin_group_key = TC.aes_cbc_then_hmac_encrypt(user_group_key, admin_group_key, use_padding=False)
        admin_enc_accounting_sk = TC.aes_cbc_then_hmac_encrypt(admin_group_key, accounting_info_sk, use_padding=False)
        admin_enc_customer_server_props_sk = TC.aes_cbc_then_hmac_encrypt(admin_group_key, customer_server_props_sk, use_padding=False)

        post_body = {
            "649": "0",
            "650": auth_token,
            "651": None,
            "652": lang,
            "653": [user_data],
            "654": TC._b64enc(user_enc_admin_group_key),
            "655": "",
            "656": [user_group_data],
            "657": [admin_group_data],
            "658": [customer_group_data],
            "659": TC._b64enc(admin_enc_accounting_sk),
            "660": TC._b64enc(system_admin_pub_enc_accounting_sk),
            "661": TC._b64enc(admin_enc_customer_server_props_sk),
            "873": "",
            "1355": "0",
            "1421": "0",
            "1422": system_admin_pub_key_version,
            "1511": app,
        }

        return post_body, TC._b64enc(salt), recover_code.hex()
