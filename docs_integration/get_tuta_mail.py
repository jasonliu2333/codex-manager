import getpass
import json
import time
import re
import html
from pathlib import Path
from tuta_register import TutaRegister


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


def main():
    email = input("邮箱: ").strip()
    password = getpass.getpass("密码: ").strip()
    auth_token = input("二次认证码(如无直接回车): ").strip() or None

    # 如需代理：proxy="socks5://ip:port" 或 "http://ip:port"
    reg = TutaRegister(proxy=None, tag="login")

    # 1) 获取 salt
    status, salt_data = reg.get_salt(email)
    if status != 200:
        raise RuntimeError(f"get_salt failed: {status} {salt_data}")
    salt_b64 = salt_data.get("422")

    # 2) 登录拿 session
    status, session_data = reg.create_session(email, password, salt_b64, auth_token=auth_token)
    if status not in (200, 201):
        raise RuntimeError(f"create_session failed: {status} {session_data}")

    print("client_id:", reg.client_id)
    print("access_token:", reg.access_token)
    print("user_id:", reg.user_id)

    # 3) 获取用户数据（带重试），然后拉取并解密邮件
    user = None
    for attempt in range(1, 4):
        status, user = reg.get_user()
        if status == 200:
            break
        if status in (401, 429):
            time.sleep(2 * attempt)
            continue
        raise RuntimeError(f"get_user failed: {status}")

    if not user:
        raise RuntimeError("获取用户失败，无法继续拉取邮件")

    reg.download_mail_details(output_dir="mail_details", max_mails=5, decrypt=True, password=password, user_data=user)

    # 仅输出解密后的文本
    readable_path = Path("mail_details") / "mail_readable.json"
    if readable_path.exists():
        data = json.loads(readable_path.read_text(encoding="utf-8"))
        plain_path = Path("mail_details") / "mail_plain.txt"
        with plain_path.open("w", encoding="utf-8") as f:
            for item in data:
                body = item.get("body") or ""
                body = _html_to_text(body)
                if body:
                    f.write(body + "\n" + ("=" * 60) + "\n\n")
        print("已输出解密文本:", plain_path)
    else:
        print("未生成 mail_readable.json，无法输出纯文本")


if __name__ == "__main__":
    main()
