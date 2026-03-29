import os
import base64
import hashlib
import hmac
from typing import Tuple


class TutaCryptoCore:
    """Tuta 密码学核心（取件/解密所需的最小子集）"""
    _FIXED_IV = bytes.fromhex("88" * 16)

    @staticmethod
    def _get_symmetric_cipher_version(ciphertext: bytes) -> int:
        if len(ciphertext) % 2 == 1:
            version = ciphertext[0]
            if version in (0, 1, 2):
                return version
            raise ValueError("invalid cipher version")
        return 0

    @staticmethod
    def _derive_subkeys(master_key: bytes, cipher_version: int) -> Tuple[bytes, bytes]:
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
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            from cryptography.hazmat.backends import default_backend
        except Exception as e:
            raise RuntimeError(f"缺少 cryptography 依赖，无法解密: {e}") from e

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
        return TutaCryptoCore.aes_cbc_then_hmac_decrypt(
            encryption_key, encrypted_bytes, use_padding=True, iv_prepended=True
        )

    @staticmethod
    def lz4_uncompress(data: bytes) -> bytes:
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
        if not compressed:
            return ""
        raw = TutaCryptoCore.lz4_uncompress(compressed)
        return raw.decode("utf-8", errors="replace")

    @staticmethod
    def argon2_derive_passphrase_key(password: str, salt: bytes) -> bytes:
        try:
            import argon2
        except Exception as e:
            raise RuntimeError(f"缺少 argon2-cffi 依赖，无法派生密钥: {e}") from e

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
        return hashlib.sha256(passphrase_key).digest()
