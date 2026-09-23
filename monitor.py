#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
保研「九推」消息监控脚本。

功能：抓取 targets.yaml 中列出的各校研招网 / 院系官网「通知公告」列表页，
按关键词过滤出推免/九推相关链接，与 state.json 中的"已见链接"比对，
发现新增即通过「微信(PushPlus/Server酱) + 邮件(SMTP)」双通道推送。

用法：
  python monitor.py --once          跑一轮（默认，GitHub Actions 用这个）
  python monitor.py --dry-run       只打印候选/新增，不推送、不写 state
  python monitor.py --test-notify   发送一条测试通知（验证双通道是否打通）

环境变量（存 GitHub Actions Secrets）：
  PUSHPLUS_TOKEN     PushPlus 的 token（微信主通道，http://www.pushplus.plus）
  SERVERCHAN_SENDKEY Server酱 SendKey（备选微信通道，sctapi.ftqq.com）
  SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASS/MAIL_TO  邮件备份通道
"""
import argparse
import json
import os
import random
import smtplib
import sys
import time
import urllib.parse
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr

import requests
import yaml
from bs4 import BeautifulSoup

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TARGETS_FILE = os.path.join(BASE_DIR, "targets.yaml")
STATE_FILE = os.path.join(BASE_DIR, "state.json")

DEFAULT_KEYWORDS = [
    "推免", "九推", "预推免", "推荐免试", "预报名",
    "接收", "拟录取", "复试", "夏令营", "考核", "推免生",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


# ---------------------------------------------------------------- 配置 / 状态

def load_targets():
    with open(TARGETS_FILE, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    global_keywords = cfg.get("keywords") or DEFAULT_KEYWORDS
    targets = cfg.get("targets") or []
    return global_keywords, targets


def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


# ---------------------------------------------------------------- 抓取 / 解析

def fetch(url, timeout=15, retries=2):
    """抓取页面 HTML，兼容 UTF-8 与 GBK/GB2312/GB18030 编码。"""
    last_err = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=timeout)
            resp.raise_for_status()
            raw = resp.content
            # 响应头若明确声明中文常见编码则优先使用
            declared = (resp.encoding or "").lower()
            if declared in ("utf-8", "utf8", "gbk", "gb2312", "gb18030"):
                try:
                    return raw.decode(resp.encoding)
                except (UnicodeDecodeError, LookupError):
                    pass
            # 否则探测：中文站点绝大多数是 utf-8 或 gb 系列。
            # 注意不能用 ISO-8859-1（latin-1）解码，它会"成功"却产生乱码。
            for enc in ("utf-8", "gb18030"):
                try:
                    return raw.decode(enc)
                except UnicodeDecodeError:
                    continue
            return raw.decode("utf-8", errors="ignore")
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
    raise last_err


def extract_links(html, base_url):
    """从 HTML 提取所有 <a> 的 (绝对href, 文本)，去掉空链接与纯锚点。"""
    soup = BeautifulSoup(html, "lxml")
    links = []
    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()
        text = a.get_text(" ", strip=True)
        if not href or href.startswith(("javascript:", "mailto:", "#")):
            continue
        abs_href = urllib.parse.urljoin(base_url, href)
        abs_href = urllib.parse.urldefrag(abs_href)[0]  # 去掉 #fragment
        links.append((abs_href, text))
    return links


def matches(link, text, keywords):
    """链接文本或 href 命中任一关键词即视为候选。"""
    haystack = f"{text} {link}"
    return any(k in haystack for k in keywords)


# ---------------------------------------------------------------- 通知

def notify_wechat(title, content):
    """微信通道：优先 PushPlus，其次 Server酱。返回是否已发送。"""
    token = os.environ.get("PUSHPLUS_TOKEN", "").strip()
    if token:
        try:
            requests.post(
                "http://www.pushplus.plus/send",
                json={"token": token, "title": title, "content": content,
                      "template": "txt"},
                timeout=15,
            )
            return True
        except Exception as e:  # noqa: BLE001
            print(f"[notify] PushPlus 发送失败: {e}", file=sys.stderr)
    sendkey = os.environ.get("SERVERCHAN_SENDKEY", "").strip()
    if sendkey:
        try:
            requests.post(
                f"https://sctapi.ftqq.com/{sendkey}.send",
                data={"title": title, "desp": content},
                timeout=15,
            )
            return True
        except Exception as e:  # noqa: BLE001
            print(f"[notify] Server酱 发送失败: {e}", file=sys.stderr)
    return False


def notify_email(title, content):
    """邮件备份通道。返回是否已发送（未配置则返回 False，不算错误）。"""
    host = os.environ.get("SMTP_HOST", "").strip()
    user = os.environ.get("SMTP_USER", "").strip()
    pwd = os.environ.get("SMTP_PASS", "").strip()
    to_addr = os.environ.get("MAIL_TO", "").strip()
    if not (host and user and pwd and to_addr):
        return False
    port = int(os.environ.get("SMTP_PORT", "465") or 465)

    msg = MIMEText(content, "plain", "utf-8")
    msg["Subject"] = Header(title, "utf-8")
    msg["From"] = formataddr((str(Header("九推监控", "utf-8")), user))
    msg["To"] = to_addr
    try:
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=20)
        else:
            server = smtplib.SMTP(host, port, timeout=20)
            server.starttls()
        server.login(user, pwd)
        server.sendmail(user, [to_addr], msg.as_string())
        server.quit()
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[notify] 邮件发送失败: {e}", file=sys.stderr)
        return False


def send(title, content):
    """双通道推送，返回 (微信是否成功, 邮件是否成功)。"""
    wx = notify_wechat(title, content)
    mail = notify_email(title, content)
    return wx, mail


# ---------------------------------------------------------------- 主流程

def run_once(dry_run=False):
    global_keywords, targets = load_targets()
    state = load_state()

    new_items = []   # [(站点名, 标题, 链接, 页面URL)]
    errors = []      # [(站点名, 页面URL, 错误信息)]

    for t in targets:
        name = t.get("name", "未命名")
        url = t.get("url", "").strip()
        if not url:
            errors.append((name, "", "缺少 url"))
            continue
        kws = t.get("keywords") or global_keywords

        try:
            html = fetch(url)
            links = extract_links(html, url)
            candidates = [(h, txt) for h, txt in links if matches(h, txt, kws)]
            current = sorted({h for h, _ in candidates})

            seen = set(state.get(url, []))
            is_baseline = url not in state  # 首次监控该页：只建基线不告警

            fresh = [h for h in current if h not in seen] if not is_baseline else []
            for h in fresh:
                txt = next((txt for hh, txt in candidates if hh == h), "")
                new_items.append((name, txt, h, url))

            if dry_run:
                print(f"[dry-run] {name}  候选 {len(candidates)} 条, "
                      f"{'首次建基线' if is_baseline else '新增 ' + str(len(fresh)) + ' 条'}")
                for h, txt in candidates:
                    print(f"    - {txt}  ->  {h}")
            else:
                # 并集累积：已见过的链接永不遗忘，避免旧公告掉出列表又回来时重复推送
                state[url] = sorted(set(seen) | set(current))
            time.sleep(random.uniform(0.5, 1.0))  # 防反爬延迟
        except Exception as e:  # noqa: BLE001
            errors.append((name, url, str(e)))
            print(f"[error] {name} {url}: {e}", file=sys.stderr)

    if dry_run:
        print("\n[dry-run] 结束：本次不推送、不写 state.json")
        return

    save_state(state)

    # 通知
    if new_items:
        title = f"[九推监控] 发现 {len(new_items)} 条新公告"
        lines = []
        for name, txt, href, page in new_items:
            lines.append(f"【{name}】{txt or '（无标题）'}\n{page}\n{href}")
        content = "\n\n".join(lines)
        wx, mail = send(title, content)
        print(f"[notify] 新增 {len(new_items)} 条：微信={'成功' if wx else '失败'}, "
              f"邮件={'成功' if mail else ('未配置' if not os.environ.get('SMTP_HOST') else '失败')}")

    if errors:
        title = f"[九推监控] 有 {len(errors)} 个页面抓取异常"
        content = "\n".join(f"【{n}】{u}\n{e}" for n, u, e in errors)
        send(title, content)
        print(f"[notify] 已发送 {len(errors)} 条抓取异常告警")


def test_notify():
    title = "[九推监控] 测试消息"
    content = "这是一条测试通知：若你同时收到微信和邮件，说明双通道已打通。"
    wx, mail = send(title, content)
    print(f"微信: {'成功' if wx else '失败/未配置'}")
    print(f"邮件: {'成功' if mail else '失败/未配置'}")


def main():
    parser = argparse.ArgumentParser(description="保研九推消息监控")
    parser.add_argument("--once", action="store_true", help="跑一轮（默认）")
    parser.add_argument("--dry-run", action="store_true", help="只打印不推送、不写状态")
    parser.add_argument("--test-notify", action="store_true", help="发送测试通知")
    args = parser.parse_args()

    if args.test_notify:
        test_notify()
    else:
        run_once(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
