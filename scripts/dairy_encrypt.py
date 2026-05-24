"""
日记文件加密/解密脚本

对 nodes/dairy/ 路径下的 .md 日记文件 + 其引用的 Attachments/ 图片进行 AES 加密。
加密后文件内容为乱码，只有输入正确密码才能解密查看明文。

加解密顺序：
    加密：提取日记中引用的图片 → 加密图片 → 加密日记
    解密：解密日记 → 提取日记中引用的图片 → 解密图片

加密原理：
    - 随机生成 16 字节 Salt，PBKDF2-HMAC-SHA256 派生密钥（10 万次迭代）
    - Fernet（AES-128-CBC + HMAC-SHA256）对称加密
    - Salt 存入密文文件头部，解密时重新派生密钥

文件格式：
    明文：正常 Markdown / 二进制图片
    密文：DIARY_ENC_V1:<salt_base64>\n<fernet_token_base64>

用法：
    python scripts/dairy_encrypt.py                      # 交互式操作
    python scripts/dairy_encrypt.py status               # 查看加密状态
    python scripts/dairy_encrypt.py encrypt 2021-01-01 2021-05-01  # 按日期范围加密
    python scripts/dairy_encrypt.py decrypt 2021-01-01 2021-05-01  # 按日期范围解密
"""

import os
import sys
import re
import base64
from getpass import getpass
from datetime import date

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

# ===== 路径配置 =====
VAULT_ROOT = r"d:\Users\Administrator\Documents\GitHub\Obsidian_my_verse"
DAIRY_DIR = os.path.join(VAULT_ROOT, "nodes", "dairy")
ATTACH_DIR = os.path.join(VAULT_ROOT, "Attachments")

# 密文文件头标记（后跟 salt:token）
MARKER = b"DIARY_ENC_V1:"
MARKER_LEN = len(MARKER)

# PBKDF2 迭代次数（越大越安全，但越慢）
PBKDF2_ITERATIONS = 100_000

# Fernet token 是标准 base64（不含换行），我们约定 salt 和 token 用换行分隔
# 文件格式: DIARY_ENC_V1:<salt_b64>\n<fernet_token_b64>

# 文件名日期正则（如 2021-09-02.md）
DATE_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")

# 图片引用正则（如 ![[hash.jpg]]）
IMAGE_REF_PATTERN = re.compile(r"!\[\[(.+?)\]\]")


def extract_date(fname: str) -> str | None:
    """从文件名提取日期字符串 YYYY-MM-DD，无法提取返回 None"""
    m = DATE_PATTERN.match(fname)
    return m.group(1) if m else None


def parse_date_arg(s: str) -> date | None:
    """将 '2021-01-01' 字符串解析为 date，失败返回 None"""
    try:
        parts = s.strip().split("-")
        if len(parts) == 3:
            return date(int(parts[0]), int(parts[1]), int(parts[2]))
    except (ValueError, IndexError):
        pass
    return None


def filter_files_by_date(files: list, date_start: date | None,
                         date_end: date | None) -> list:
    """按日期范围筛选文件列表，不连续的日期自动跳过"""
    if date_start is None and date_end is None:
        return files

    result = []
    for fname in files:
        d = extract_date(fname)
        if d is None:
            continue
        fd = parse_date_arg(d)
        if fd is None:
            continue
        if date_start and fd < date_start:
            continue
        if date_end and fd > date_end:
            continue
        result.append(fname)
    return result


def collect_image_refs(files: list) -> set:
    """从一批 dairy 文件中提取所有 ![[filename]] 图片引用，返回去重后的文件名集合。

    文件必须为明文才能正常读取；如果文件已加密则跳过。
    """
    refs = set()
    for fname in files:
        fpath = os.path.join(DAIRY_DIR, fname)
        try:
            # 跳过已加密文件（无法读取引用）
            if is_encrypted(fpath):
                continue
            with open(fpath, "r", encoding="utf-8") as f:
                content = f.read()
            for m in IMAGE_REF_PATTERN.finditer(content):
                img_name = m.group(1).strip()
                if img_name:
                    refs.add(img_name)
        except Exception:
            pass
    return refs


def encrypt_file_content(filepath: str, password: str) -> bool:
    """加密单个文件（通用），返回是否成功"""
    try:
        with open(filepath, "rb") as f:
            plaintext = f.read()

        salt = os.urandom(16)
        key = derive_key(password, salt)
        fernet = Fernet(key)
        token = fernet.encrypt(plaintext)

        salt_b64 = base64.urlsafe_b64encode(salt).decode("ascii")
        token_b64 = token.decode("ascii")
        encrypted_content = f"{MARKER.decode('ascii')}{salt_b64}\n{token_b64}"

        with open(filepath, "w", encoding="ascii") as f:
            f.write(encrypted_content)
        return True
    except Exception:
        return False


def decrypt_file_content(filepath: str, password: str) -> str:
    """解密单个文件（通用），返回 "ok" / "wrong_pwd" / "error" """
    try:
        with open(filepath, "r", encoding="ascii") as f:
            content = f.read()

        if not content.startswith(MARKER.decode("ascii")):
            return "error"

        payload = content[MARKER_LEN:]
        newline_pos = payload.index("\n")
        salt_b64 = payload[:newline_pos]
        token_b64 = payload[newline_pos + 1:]

        salt = base64.urlsafe_b64decode(salt_b64)
        token = token_b64.encode("ascii")

        key = derive_key(password, salt)
        fernet = Fernet(key)

        try:
            plaintext = fernet.decrypt(token)
        except InvalidToken:
            return "wrong_pwd"

        with open(filepath, "wb") as f:
            f.write(plaintext)
        return "ok"
    except Exception:
        return "error"


def process_images_encrypt(password: str, image_names: set) -> int:
    """加密一批图片，跳过已加密的，返回加密数量"""
    if not os.path.isdir(ATTACH_DIR):
        return 0

    count = 0
    for name in sorted(image_names):
        fpath = os.path.join(ATTACH_DIR, name)
        if not os.path.exists(fpath):
            continue
        if is_encrypted(fpath):
            continue  # 已加密，跳过
        if encrypt_file_content(fpath, password):
            count += 1
    return count


def process_images_decrypt(password: str, image_names: set) -> tuple:
    """解密一批图片，返回 (解密数, 密码错误数)，跳过非加密文件"""
    if not os.path.isdir(ATTACH_DIR):
        return 0, 0

    count = 0
    wrong = 0
    for name in sorted(image_names):
        fpath = os.path.join(ATTACH_DIR, name)
        if not os.path.exists(fpath):
            continue
        if not is_encrypted(fpath):
            continue  # 已是明文，跳过
        result = decrypt_file_content(fpath, password)
        if result == "ok":
            count += 1
        elif result == "wrong_pwd":
            wrong += 1
    return count, wrong


def show_attach_status():
    """显示 Attachments 目录加密状态概览"""
    if not os.path.isdir(ATTACH_DIR):
        return
    enc = 0
    plain = 0
    total = 0
    for fname in os.listdir(ATTACH_DIR):
        fpath = os.path.join(ATTACH_DIR, fname)
        if not os.path.isfile(fpath):
            continue
        total += 1
        if is_encrypted(fpath):
            enc += 1
        else:
            plain += 1
    if total > 0:
        print(f"🖼 附件目录: {ATTACH_DIR}")
        print(f"   📊 总文件数: {total}")
        print(f"   🔒 已加密: {enc} 个")
        print(f"   📝 明文:   {plain} 个")


def derive_key(password: str, salt: bytes) -> bytes:
    """PBKDF2 派生 32 字节密钥，返回 Fernet 兼容的 urlsafe-base64 密钥"""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    return base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))


def is_encrypted(filepath: str) -> bool:
    """检测文件头部是否为加密标记"""
    try:
        with open(filepath, "rb") as f:
            head = f.read(MARKER_LEN)
        return head == MARKER
    except Exception:
        return False


def scan_dairy() -> dict:
    """扫描 dairy 目录，返回加密/明文文件列表"""
    if not os.path.isdir(DAIRY_DIR):
        return {"encrypted": [], "plaintext": [], "total": 0}

    encrypted = []
    plaintext = []
    for fname in sorted(os.listdir(DAIRY_DIR)):
        if not fname.endswith(".md"):
            continue
        fpath = os.path.join(DAIRY_DIR, fname)
        if is_encrypted(fpath):
            encrypted.append(fname)
        else:
            plaintext.append(fname)

    return {
        "encrypted": encrypted,
        "plaintext": plaintext,
        "total": len(encrypted) + len(plaintext),
    }


def show_status() -> dict:
    """打印加密状态并返回统计"""
    s = scan_dairy()
    print(f"\n📂 日记目录: {DAIRY_DIR}")
    print(f"📊 总文件数: {s['total']}")
    print(f"   🔒 已加密: {len(s['encrypted'])} 个")
    print(f"   📝 明文:   {len(s['plaintext'])} 个")

    if s["encrypted"]:
        print(f"\n🔒 已加密 ({len(s['encrypted'])} 个):")
        for f in s["encrypted"][:8]:
            print(f"     {f}")
        if len(s["encrypted"]) > 8:
            print(f"     ... 还有 {len(s['encrypted']) - 8} 个")

    if s["plaintext"]:
        print(f"\n📝 明文 ({len(s['plaintext'])} 个):")
        for f in s["plaintext"][:8]:
            print(f"     {f}")
        if len(s["plaintext"]) > 8:
            print(f"     ... 还有 {len(s['plaintext']) - 8} 个")

    print()
    show_attach_status()

    return s


def encrypt_files(password: str, files: list) -> int:
    """加密一批文件

    每个文件使用独立随机 Salt，Salt 存入文件头部。
    这样即使密码相同，每个文件的密文也不一样。
    """
    count = 0
    total = len(files)

    for i, fname in enumerate(files, 1):
        fpath = os.path.join(DAIRY_DIR, fname)
        try:
            # 读取明文
            with open(fpath, "rb") as f:
                plaintext = f.read()

            # 随机 salt → 派生密钥
            salt = os.urandom(16)
            key = derive_key(password, salt)
            fernet = Fernet(key)

            # 加密
            token = fernet.encrypt(plaintext)  # bytes, 标准 base64

            # 写入格式: MARKER + salt_b64 + \n + token_b64
            salt_b64 = base64.urlsafe_b64encode(salt).decode("ascii")
            token_b64 = token.decode("ascii")
            encrypted_content = f"{MARKER.decode('ascii')}{salt_b64}\n{token_b64}"

            with open(fpath, "w", encoding="ascii") as f:
                f.write(encrypted_content)

            count += 1
            print(f"   [{i}/{total}] 🔒 {fname}")
        except Exception as e:
            print(f"   [{i}/{total}] ❌ {fname}: {e}")

    return count


def decrypt_files(password: str, files: list) -> tuple:
    """解密一批文件，返回 (成功数, 密码错误数)"""
    count = 0
    wrong_pwd = 0
    total = len(files)

    for i, fname in enumerate(files, 1):
        fpath = os.path.join(DAIRY_DIR, fname)
        try:
            with open(fpath, "r", encoding="ascii") as f:
                content = f.read()

            # 解析格式: MARKER<salt_b64>\n<token_b64>
            if not content.startswith(MARKER.decode("ascii")):
                print(f"   [{i}/{total}] ⚠ {fname}: 格式异常，跳过")
                continue

            payload = content[MARKER_LEN:]  # salt_b64\ntoken_b64
            newline_pos = payload.index("\n")
            salt_b64 = payload[:newline_pos]
            token_b64 = payload[newline_pos + 1:]

            salt = base64.urlsafe_b64decode(salt_b64)
            token = token_b64.encode("ascii")

            # 派生密钥 → 解密
            key = derive_key(password, salt)
            fernet = Fernet(key)

            try:
                plaintext = fernet.decrypt(token)
            except InvalidToken:
                # 密码错误，Fernet HMAC 校验失败
                print(f"   [{i}/{total}] 🔑 {fname}: 密码错误")
                wrong_pwd += 1
                continue

            # 写回明文
            with open(fpath, "wb") as f:
                f.write(plaintext)

            count += 1
            print(f"   [{i}/{total}] 🔓 {fname}")

        except InvalidToken:
            print(f"   [{i}/{total}] 🔑 {fname}: 密码错误")
            wrong_pwd += 1
        except Exception as e:
            print(f"   [{i}/{total}] ❌ {fname}: {e}")

    return count, wrong_pwd


def confirm_action(action: str, count: int) -> bool:
    """批量操作前确认"""
    print(f"\n⚠ 即将 {action} {count} 个文件")
    ans = input("   确认操作？输入 yes 继续: ").strip().lower()
    return ans == "yes"


def main():
    status = scan_dairy()

    # 解析日期范围参数（encrypt/decrypt 后可跟 1~2 个日期）
    # 格式: encrypt [YYYY-MM-DD] [YYYY-MM-DD]
    date_start: date | None = None
    date_end: date | None = None

    if len(sys.argv) > 1:
        cmd = sys.argv[1].lower()
        # 尝试从剩余参数解析日期
        for i in range(2, len(sys.argv)):
            d = parse_date_arg(sys.argv[i])
            if d:
                if date_start is None:
                    date_start = d
                else:
                    date_end = d
            else:
                print(f"⚠ 忽略无效日期: {sys.argv[i]}")

        # 如果只给了一个日期，作为起始日期
        if date_start and date_end is None:
            # 查找目录中最大日期作为结束
            all_files = status["encrypted"] + status["plaintext"]
            max_date = date_start
            for fname in all_files:
                d = extract_date(fname)
                if d:
                    fd = parse_date_arg(d)
                    if fd and fd > max_date:
                        max_date = fd
            if max_date > date_start:
                date_end = max_date
            else:
                date_end = date_start

        if cmd == "status":
            show_status()
            return
        elif cmd in ("encrypt", "enc"):
            files = status["plaintext"]
            files = filter_files_by_date(files, date_start, date_end)
            if not files:
                if date_start:
                    print(f"\n✅ {date_start} ~ {date_end} 范围内无明文文件。")
                else:
                    print("\n✅ 所有文件已加密，无需操作。")
                return
            show_status()
            print(f"\n📅 日期范围: {date_start} ~ {date_end}" if date_start else "")

            # ---- 收集图片引用（必须在加密日记前） ----
            img_refs = collect_image_refs(files)
            total_count = len(files) + len(img_refs)
            if img_refs:
                print(f"🖼 引用图片: {len(img_refs)} 个")

            if not confirm_action("加密", total_count):
                print("已取消。")
                return
            password = getpass("🔑 请设置加密密码: ")
            if not password:
                print("❌ 密码不能为空！")
                return
            password2 = getpass("🔑 请再次输入密码确认: ")
            if password != password2:
                print("❌ 两次密码不一致！")
                return

            # 步骤 1：加密图片
            if img_refs:
                print(f"\n🖼 正在加密 {len(img_refs)} 个图片...")
                img_ok = process_images_encrypt(password, img_refs)
                print(f"   ✅ 图片加密: {img_ok}/{len(img_refs)}")
            else:
                img_ok = 0

            # 步骤 2：加密日记
            print(f"\n🔒 开始加密 {len(files)} 个日记文件...")
            ok = encrypt_files(password, files)
            print(f"\n✅ 加密完成: 日记 {ok}/{len(files)}，图片 {img_ok}/{len(img_refs)}")
            return
        elif cmd in ("decrypt", "dec"):
            files = status["encrypted"]
            files = filter_files_by_date(files, date_start, date_end)
            if not files:
                if date_start:
                    print(f"\n✅ {date_start} ~ {date_end} 范围内无密文文件。")
                else:
                    print("\n✅ 所有文件已是明文，无需解密。")
                return
            show_status()
            print(f"\n📅 日期范围: {date_start} ~ {date_end}" if date_start else "")
            if not confirm_action("解密", len(files)):
                print("已取消。")
                return
            password = getpass("🔑 请输入解密密码: ")
            if not password:
                print("❌ 密码不能为空！")
                return

            # 步骤 1：解密日记
            print(f"\n🔓 开始解密 {len(files)} 个日记文件...")
            ok, wrong = decrypt_files(password, files)
            if wrong:
                print(f"⚠ 密码错误: {wrong} 个文件未能解密")
                return

            # 步骤 2：从已解密的日记中提取图片引用，再解密图片
            img_refs = collect_image_refs(files)
            if img_refs:
                print(f"\n🖼 正在解密 {len(img_refs)} 个图片...")
                img_ok, img_wrong = process_images_decrypt(password, img_refs)
                print(f"   ✅ 图片解密: {img_ok}/{len(img_refs)}")
                if img_wrong:
                    print(f"   ⚠ 密码错误: {img_wrong} 个图片")
            else:
                img_ok = 0

            print(f"\n✅ 解密完成: 日记 {ok}/{len(files)}，图片 {img_ok}/{len(img_refs)}")
            return
        else:
            print(f"未知命令: {cmd}")
            print("可用: status | encrypt [start] [end] | decrypt [start] [end]")
            return

    # 无参数：交互模式
    show_status()

    if status["encrypted"] and status["plaintext"]:
        print("\n⚠ 目录中存在混合状态（部分加密、部分明文）。")
        print("  请选择操作：")
        print("    1 - 加密全部明文文件")
        print("    2 - 解密全部密文文件")
        choice = input("  输入 1 或 2: ").strip()
        if choice == "1":
            files = status["plaintext"]
            action = "加密"
        elif choice == "2":
            files = status["encrypted"]
            action = "解密"
        else:
            print("无效选择，退出。")
            return
    elif status["encrypted"]:
        print("\n📌 当前所有日记文件均已加密。")
        print("   输入密码以解密查看 →")
        files = status["encrypted"]
        action = "解密"
    elif status["plaintext"]:
        print("\n📌 当前所有日记文件均为明文。")
        print("   设置密码以加密保护 →")
        files = status["plaintext"]
        action = "加密"
    else:
        print("\n❌ 目录中没有 .md 文件。")
        return

    # 交互式日期范围输入
    date_start = None
    date_end = None
    date_input = input(
        "\n📅 输入日期范围（如 2021-01-01 2021-05-01，直接回车处理全部）: "
    ).strip()
    if date_input:
        parts = date_input.split()
        for p in parts:
            d = parse_date_arg(p)
            if d:
                if date_start is None:
                    date_start = d
                else:
                    date_end = d
        if date_start and date_end is None:
            date_end = date_start

    files = filter_files_by_date(files, date_start, date_end)
    if not files:
        print(f"\n❌ {date_start or '全部'} 范围内无符合条件的文件。")
        return
    if date_start:
        print(f"   目标范围: {date_start} ~ {date_end}，匹配 {len(files)} 个日记")

    # 收集图片引用并确认
    if action == "加密":
        img_refs = collect_image_refs(files)
        total_count = len(files) + len(img_refs)
        if img_refs:
            print(f"🖼 引用图片: {len(img_refs)} 个")
        if not confirm_action("加密", total_count):
            print("已取消。")
            return
        password = getpass("🔑 请设置加密密码: ")
        if not password:
            print("❌ 密码不能为空！")
            return
        password2 = getpass("🔑 请再次输入密码确认: ")
        if password != password2:
            print("❌ 两次密码不一致！")
            return

        # 步骤 1：加密图片
        if img_refs:
            print(f"\n🖼 正在加密 {len(img_refs)} 个图片...")
            img_ok = process_images_encrypt(password, img_refs)
            print(f"   ✅ 图片加密: {img_ok}/{len(img_refs)}")
        else:
            img_ok = 0

        # 步骤 2：加密日记
        print(f"\n🔒 开始加密 {len(files)} 个日记...")
        ok = encrypt_files(password, files)
        print(f"\n✅ 加密完成: 日记 {ok}/{len(files)}，图片 {img_ok}/{len(img_refs)}")
    else:
        if not confirm_action("解密", len(files)):
            print("已取消。")
            return
        password = getpass("🔑 请输入解密密码: ")
        if not password:
            print("❌ 密码不能为空！")
            return

        # 步骤 1：解密日记
        print(f"\n🔓 开始解密 {len(files)} 个日记...")
        ok, wrong = decrypt_files(password, files)
        if wrong:
            print(f"⚠ 密码错误: {wrong} 个文件未能解密")

        # 步骤 2：从已解密日记中提取图片引用
        img_refs = collect_image_refs(files)
        if img_refs:
            print(f"\n🖼 正在解密 {len(img_refs)} 个图片...")
            img_ok, img_wrong = process_images_decrypt(password, img_refs)
            print(f"   ✅ 图片解密: {img_ok}/{len(img_refs)}")
            if img_wrong:
                print(f"   ⚠ 密码错误: {img_wrong} 个图片")
        else:
            img_ok = 0

        print(f"\n✅ 解密完成: 日记 {ok}/{len(files)}，图片 {img_ok}/{len(img_refs)}")


if __name__ == "__main__":
    main()
