"""RSSから記事を集め、「①伸びている業界」「②AI活用事例」に分けてLINEに送る"""
import html
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
import requests

import trends

BASE = Path(__file__).parent
SENT_FILE = BASE / "sent.json"
MAX_PER_FEED = 5        # 1回に1サイト・1区分から送る最大件数
MAX_SENT_KEEP = 3000
JST = timezone(timedelta(hours=9))
UA = "Mozilla/5.0 (compatible; ai-news-line/2.0)"

SECTIONS = {"industry": "① 伸びている業界", "ai": "② AI活用事例・動向"}


def load_lines(name):
    text = (BASE / name).read_text(encoding="utf-8")
    return [l.strip() for l in text.splitlines() if l.strip() and not l.strip().startswith("#")]


def load_feeds():
    """サイト名, URL, 送り方(filter/all/trend), 既定の区分(industry/ai)"""
    feeds = []
    for line in load_lines("feeds.txt"):
        p = [x.strip() for x in line.split(",")]
        if len(p) < 2:
            continue
        mode = p[2].lower() if len(p) > 2 and p[2] else "filter"
        default = p[3].lower() if len(p) > 3 and p[3] else "industry"
        feeds.append((p[0], p[1], mode, default))
    return feeds


def build_matcher(keywords):
    rules = []
    for kw in keywords:
        pat = re.escape(kw)
        if re.fullmatch(r"[A-Za-z0-9 .+\-]+", kw):
            pat = rf"(?<![A-Za-z]){pat}(?![A-Za-z])"
        rules.append((kw, re.compile(pat, re.IGNORECASE)))

    def match(text):
        for kw, rx in rules:
            if rx.search(text):
                return kw
        return None
    return match


def clean(text):
    return html.unescape(re.sub(r"<[^>]+>", "", text or "")).strip()


def fetch(url):
    r = requests.get(url, headers={"User-Agent": UA}, timeout=20)
    r.raise_for_status()
    return feedparser.parse(r.content).entries


def load_sent():
    try:
        return json.loads(SENT_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def classify(text, mode, default, match_ai, match_ind):
    """AIの言葉があれば②、なければ業界の言葉で①。all のサイトは既定の区分へ"""
    kw = match_ai(text)
    if kw:
        return "ai", kw
    kw = match_ind(text)
    if kw:
        return "industry", kw
    if mode == "all":
        return default, None
    return None, None


def build_message(picked, now):
    """picked: {区分: {サイト名: [記事]}}"""
    label = "朝" if now.hour < 11 else "昼" if now.hour < 16 else "夜"
    blocks = [f"📰 {label}のニュース {now:%m/%d %H:%M}"]
    for key, title in SECTIONS.items():
        sites = picked.get(key)
        if not sites:
            continue
        lines = [f"\n━━ {title} ━━"]
        for site, items in sites.items():
            lines.append(f"■ {site}")
            for it in items:
                tag = f"【{it['kw']}】" if it["kw"] else ""
                lines.append(f"・{tag}{it['title']}\n{it['link']}")
        blocks.append("\n".join(lines))

    chunks, cur = [], ""
    for b in blocks:
        if cur and len(cur) + len(b) > 4500:
            chunks.append(cur)
            cur = ""
        cur += b + "\n"
    chunks.append(cur)
    return [c.strip() for c in chunks[:4]]


def send_line(texts):
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
    user_id = os.environ.get("LINE_USER_ID")
    if not token or not user_id:
        print("【テスト表示：LINEの設定がないので送信しません】")
        print("\n---\n".join(texts))
        return False
    r = requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={"Authorization": f"Bearer {token}"},
        json={"to": user_id, "messages": [{"type": "text", "text": t} for t in texts]},
        timeout=20,
    )
    if r.status_code != 200:
        raise SystemExit(f"LINE送信エラー {r.status_code}: {r.text}")
    return True


def main():
    match_ai = build_matcher(load_lines("keywords_ai.txt"))
    match_ind = build_matcher(load_lines("keywords_industry.txt"))
    sent = load_sent()
    sent_set = set(sent)
    picked, new_links, all_articles = {}, [], []

    for name, url, mode, default in load_feeds():
        try:
            entries = fetch(url)
        except Exception as e:
            print(f"取得失敗: {name} ({e})")
            continue
        count = {}
        for e in entries:
            link = e.get("link", "")
            title = clean(e.get("title", ""))
            summary = clean(e.get("summary", ""))[:500]
            all_articles.append((name, title, summary, link))
            if mode == "trend" or not link or link in sent_set:
                continue
            key, kw = classify(title + " " + summary, mode, default, match_ai, match_ind)
            if not key or count.get(key, 0) >= MAX_PER_FEED:
                continue
            count[key] = count.get(key, 0) + 1
            picked.setdefault(key, {}).setdefault(name, []).append({"title": title, "link": link, "kw": kw})
            new_links.append(link)
            sent_set.add(link)
        print(f"{name}: {len(entries)}件中 {sum(count.values())}件")

    now = datetime.now(JST)
    today = now.date().isoformat()
    db = trends.load()
    trends.record(db, all_articles, today)
    trend_text = trends.report(db, today) if (now.weekday() == 0 and now.hour < 11) else None
    trends.save(db)

    if not picked and not trend_text:
        print("新着なし。送信しません。")
        return

    texts = build_message(picked, now) if picked else []
    if trend_text:
        texts.append(trend_text[:4800])
    if send_line(texts):
        SENT_FILE.write_text(
            json.dumps((sent + new_links)[-MAX_SENT_KEEP:], ensure_ascii=False, indent=0),
            encoding="utf-8",
        )
        print("送信しました。")


if __name__ == "__main__":
    main()
