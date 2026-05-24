"""日记文本转换脚本
支持两种旧日记格式 → daily dairy template 独立日记文件。

用法:
    python scripts/convert_diary.py 始于2021-9-2.textbundle/text.md
"""
import re
import os
import sys
import shutil

# ===== 路径配置 =====
VAULT_ROOT = r"d:\Users\Administrator\Documents\GitHub\Obsidian_my_verse"
TARGET_DIR = os.path.join(VAULT_ROOT, "nodes", "dairy")
ATTACH_DIR = os.path.join(VAULT_ROOT, "Attachments")
TITLE_MAX_LEN = 15

TEMPLATE = """---
date: {date}
main: {title}
tags:
  - daily
weather: {weather}
exercise？: false
---
# 1 Daily Logs
{content}

# 2 Reading Notes



# 3 Tiny Moments
{tiny}
"""


def detect_format(text: str) -> str:
    """检测日记格式：A=## YYYY-M 式，B=## M月 式"""
    if re.search(r"^##\s+\d{4}-\d{1,2}", text, re.M):
        return "A"
    if re.search(r"^##\s*\d{1,2}月", text, re.M):
        return "B"
    return "A"


def parse_format_a(text: str) -> list:
    """Format A: ## YYYY-M 月份标题 → ### M-D [后缀] 日期标题
    
    后缀可能是天气（晴/雨/雪等）或备注（水仙-day-5），或两者都有。
    通过 finditer 定位所有 ### M-D 标题的位置，
    再向前查找最近的 ## YYYY-M 获取年份。
    """
    # 常见天气词（用于识别后缀中的天气）
    WEATHER_SET = {'晴', '雨', '雪', '阴', '雾', '云', '风', '多云',
                   '雷', '雹', '霾', '沙尘', '小雨', '中雨', '大雨', '阵雨'}

    entries = []

    # 收集所有 ## YYYY-M 位置
    month_markers = [(m.start(), int(m.group(1)), int(m.group(2)))
                     for m in re.finditer(r"^##\s+(\d{4})-(\d{1,2})", text, re.M)]

    # 收集所有 ### M-D [可选后缀] 位置
    day_matches = list(re.finditer(r"^###\s+(\d{1,2})-(\d{1,2})(?:\s+(.+))?\s*$", text, re.M))

    for idx, dm in enumerate(day_matches):
        pos = dm.start()
        month_num = int(dm.group(1))
        day_num = int(dm.group(2))
        suffix = (dm.group(3) or "").strip()

        # ---- 解析后缀：天气 + 备注 ----
        weather = ""
        extra_note = ""
        if suffix:
            parts = suffix.split(None, 1)  # 按空格分最多两段
            first = parts[0]
            # 判断首段是否为天气（在集合中，或为单个汉字）
            if first in WEATHER_SET or (len(first) == 1 and '\u4e00' <= first <= '\u9fff'):
                weather = first
                extra_note = parts[1] if len(parts) > 1 else ""
            else:
                extra_note = suffix

        # 找到最近的月份标记（在当前位置之前）
        year = None
        for mp, y, m in reversed(month_markers):
            if mp < pos:
                year = y
                break
        if year is None:
            continue

        # 提取内容：从该标题后到下一个 ### 标题前（或文件末尾）
        content_start = dm.end()
        content_end = day_matches[idx + 1].start() if idx + 1 < len(day_matches) else len(text)
        raw = text[content_start:content_end].strip()

        # 把后缀中的备注部分前置到正文
        if extra_note:
            raw = extra_note + "\n" + raw if raw else extra_note

        entries.append({
            "year": year,
            "month": month_num,
            "day": day_num,
            "weather": weather,
            "raw": raw,
        })
    return entries


def parse_format_b(text: str) -> list:
    """Format B: # YYYY年 → ## M月 → ### M-D 天气
    
    通过 finditer 定位所有 ### M-D 标题的位置，
    向前查找月份标记和年份标记。
    """
    entries = []

    # 提取年份
    ym = re.search(r"#\s*(\d{4})年", text)
    year = int(ym.group(1)) if ym else None

    # 收集所有 ### M-D [weather] 位置
    day_matches = list(re.finditer(r"^###\s+(\d{1,2})-(\d{1,2})\s*(\S*)\s*$", text, re.M))

    for idx, dm in enumerate(day_matches):
        month_num = int(dm.group(1))
        day_num = int(dm.group(2))
        weather = dm.group(3).strip()

        # 提取内容：从该标题后到下一个 ### 标题前（或文件末尾）
        content_start = dm.end()
        content_end = day_matches[idx + 1].start() if idx + 1 < len(day_matches) else len(text)
        raw = text[content_start:content_end].strip()

        entries.append({
            "year": year,
            "month": month_num,
            "day": day_num,
            "weather": weather,
            "raw": raw,
        })
    return entries


def clean_title(text: str) -> str:
    """去除 Markdown 加粗/斜体/删除线/行内代码标记"""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)   # **加粗**
    text = re.sub(r"__(.+?)__", r"\1", text)        # __加粗__
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\1", text)  # *斜体*
    text = re.sub(r"(?<!_)_(?!_)(.+?)(?<!_)_(?!_)", r"\1", text)           # _斜体_
    text = re.sub(r"~~(.+?)~~", r"\1", text)         # ~~删除线~~
    text = re.sub(r"`(.+?)`", r"\1", text)           # `代码`
    return text.strip()


def process_entry(entry: dict, source_assets: str) -> dict:
    """处理单条日记：分离图片、列表笔记、正文，复制图片到 Attachments/"""
    raw = entry["raw"]
    lines = raw.split("\n")

    images = []       # 图片文件名列表
    notes = []        # 列表笔记列表
    body_lines = []   # 正文行

    for line in lines:
        stripped = line.strip()

        # 图片 ![](assets/xxx.jpg)
        m = re.match(r"!\[image\]\(assets/(.+?)\)", stripped)
        if m:
            images.append(m.group(1))
            continue

        # 短横线笔记
        if stripped.startswith("- "):
            notes.append(stripped[2:].strip())
            continue

        # 正文（去掉 tab 缩进）
        cleaned = line.lstrip("\t")
        if cleaned.strip():
            body_lines.append(cleaned)

    # ---- 标题提取 ----
    # 优先取第一条列表笔记作为标题，不限长度
    title = ""
    content_lines = body_lines[:]

    if notes:
        title = clean_title(notes[0])
        notes = notes[1:]   # 剩余笔记仍放入 Tiny Moments
        # 标题不过滤正文首行
    elif body_lines:
        first = body_lines[0].strip()
        if len(first) <= TITLE_MAX_LEN:
            title = clean_title(first)
            content_lines = body_lines[1:]

    content = "\n".join(content_lines).strip()

    # ---- Tiny Moments ----
    tiny_parts = []

    # 复制图片并转为 Obsidian 嵌入语法
    for img_name in images:
        src = os.path.join(source_assets, img_name)
        dst = os.path.join(ATTACH_DIR, img_name)
        if os.path.exists(src):
            os.makedirs(ATTACH_DIR, exist_ok=True)
            if not os.path.exists(dst):
                shutil.copy2(src, dst)
            tiny_parts.append(f"![[{img_name}]]")
        else:
            tiny_parts.append(f"![image](assets/{img_name})")  # fallback

    for note in notes:
        tiny_parts.append(f"- {note}")

    tiny = "\n".join(tiny_parts)

    return {
        "date": f"{entry['year']}-{entry['month']:02d}-{entry['day']:02d}",
        "title": title,
        "weather": entry.get("weather", ""),
        "content": content,
        "tiny": tiny,
    }


def main():
    if len(sys.argv) < 2:
        print("用法: python convert_diary.py <text.md相对路径>")
        print("示例: python convert_diary.py 2025年.textbundle/text.md")
        sys.exit(1)

    relative_path = sys.argv[1]
    source_md = os.path.join(VAULT_ROOT, relative_path)

    if not os.path.exists(source_md):
        print(f"❌ 文件不存在: {source_md}")
        sys.exit(1)

    source_dir = os.path.dirname(source_md)
    source_assets = os.path.join(source_dir, "assets")

    with open(source_md, encoding="utf-8") as f:
        text = f.read()

    fmt = detect_format(text)
    label = "A (## YYYY-M 旧格式)" if fmt == "A" else "B (## M月 新格式)"
    print(f"📋 检测格式: {label}")

    if fmt == "A":
        entries = parse_format_a(text)
    else:
        entries = parse_format_b(text)

    print(f"📝 共解析 {len(entries)} 条日记\n")

    os.makedirs(TARGET_DIR, exist_ok=True)
    img_count = 0

    for entry in entries:
        result = process_entry(entry, source_assets)

        # ---- 文件名（仅日期，标题保留在 YAML main 字段）----
        filename = f"{result['date']}.md"
        filepath = os.path.join(TARGET_DIR, filename)

        if os.path.exists(filepath):
            print(f"⏭ 跳过（已存在）: {filename}")
            continue

        weather_val = result["weather"] if result["weather"] else ""
        title_val = result["title"] if result["title"] else ""

        with open(filepath, "w", encoding="utf-8", newline="\n") as f:
            f.write(TEMPLATE.format(
                date=result["date"],
                title=title_val,
                weather=weather_val,
                content=result["content"],
                tiny=result["tiny"],
            ))

        # 统计图片
        imgs = result["tiny"].count("![")
        img_count += imgs

        extra = f" 📷×{imgs}" if imgs else ""
        print(f"✅ {filename}{extra}")

    print(f"\n🎉 完成！输出目录: {TARGET_DIR}")
    if img_count:
        print(f"📸 共复制 {img_count} 张图片到 {ATTACH_DIR}")


if __name__ == "__main__":
    main()
