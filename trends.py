"""新しい言葉（まだ広まっていない職種・業態など）を検知する"""
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).parent
TERMS_FILE = BASE / "terms.json"
WARMUP_DAYS = 14      # 最初の2週間は「普段よく出る言葉」を覚える期間
KEEP_DAYS = 60        # 記録を残す日数
MIN_COUNT = 3         # 直近7日でこの回数以上出た言葉だけ通知
MIN_SOURCES = 2       # かつ、この数以上のサイトに出た言葉
SURGE_RATIO = 3       # 以前の週平均のこの倍以上に増えたら「急増」

KATAKANA = re.compile(r"[ァ-ヴー・]{4,}")
QUOTED = re.compile(r"「([^「」]{2,12})」")
ACRONYM = re.compile(r"(?<![A-Za-z])[A-Z][A-Z0-9]{1,5}(?![A-Za-z])")
PHRASE = re.compile(r"(?<![A-Za-z])(?:[A-Z][a-z]+(?:-[A-Z]?[a-z]+)?\s){1,3}[A-Z][a-z]+(?![A-Za-z])")
PHRASE_SKIP = {"The", "How", "Why", "What", "When", "Where", "Who", "Our", "Your", "Is",
               "Are", "This", "That", "New", "My", "We", "I", "It", "Do", "Can", "Show", "Ask"}


def load_stopwords():
    p = BASE / "stopwords.txt"
    if not p.exists():
        return set()
    return {l.strip().lower() for l in p.read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.startswith("#")}


def extract_terms(text):
    terms = set(KATAKANA.findall(text)) | set(ACRONYM.findall(text)) | set(QUOTED.findall(text))
    for ph in PHRASE.findall(text):
        words = ph.split()
        while words and words[0] in PHRASE_SKIP:
            words = words[1:]
        while words and words[-1] in PHRASE_SKIP:
            words = words[:-1]
        if len(words) >= 2:
            terms.add(" ".join(words))
    stop = load_stopwords()
    return {t.strip("・") for t in terms if t.lower() not in stop and len(t) >= 2}


def load():
    try:
        return json.loads(TERMS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"started": None, "terms": {}, "seen": []}


def save(db):
    TERMS_FILE.write_text(json.dumps(db, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def record(db, articles, today):
    """articles: [(サイト名, タイトル, 概要, URL)] を記録（同じ記事は1回だけ数える）"""
    db["started"] = db["started"] or today
    seen = set(db["seen"])
    for source, title, summary, link in articles:
        if link in seen:
            continue
        seen.add(link)
        db["seen"].append(link)
        for t in extract_terms(title + " " + summary):
            e = db["terms"].setdefault(t, {"first": today, "days": {}, "src": [], "ex": None})
            e["days"][today] = e["days"].get(today, 0) + 1
            if source not in e["src"]:
                e["src"].append(source)
            e["ex"] = [title, link]
    db["seen"] = db["seen"][-8000:]
    prune(db, today)


def prune(db, today):
    d0 = datetime.fromisoformat(today)
    old = (d0 - timedelta(days=KEEP_DAYS)).date().isoformat()
    lone = (d0 - timedelta(days=14)).date().isoformat()
    for t in list(db["terms"]):
        e = db["terms"][t]
        e["days"] = {d: c for d, c in e["days"].items() if d >= old}
        if not e["days"] or (sum(e["days"].values()) == 1 and max(e["days"]) < lone):
            del db["terms"][t]


def report(db, today):
    """週1回の通知文を作る。準備期間中や該当なしなら None"""
    d0 = datetime.fromisoformat(today)
    if not db["started"] or (d0 - datetime.fromisoformat(db["started"])).days < WARMUP_DAYS:
        return None
    wk = (d0 - timedelta(days=7)).date().isoformat()
    base_from = (d0 - timedelta(days=28)).date().isoformat()
    newborn = (datetime.fromisoformat(db["started"]) + timedelta(days=WARMUP_DAYS)).date().isoformat()

    new, surge = [], []
    for t, e in db["terms"].items():
        recent = sum(c for d, c in e["days"].items() if d > wk)
        if recent < MIN_COUNT or len(e["src"]) < MIN_SOURCES:
            continue
        before = sum(c for d, c in e["days"].items() if base_from < d <= wk) / 3
        if e["first"] >= newborn and e["first"] > (d0 - timedelta(days=30)).date().isoformat():
            new.append((recent, t, e))
        elif recent >= max(before, 1) * SURGE_RATIO:
            surge.append((recent, t, e))

    if not new and not surge:
        return None
    lines = ["🔎 今週の兆し（提案ネタ・取り入れネタの候補）"]
    for label, items in (("■ 新しく出てきた言葉", new), ("■ 急に増えた言葉", surge)):
        if items:
            lines.append(f"\n{label}")
            for recent, t, e in sorted(items, reverse=True)[:8]:
                lines.append(f"・{t}（{recent}回／{len(e['src'])}サイト）\n  例: {e['ex'][0]}\n  {e['ex'][1]}")
    return "\n".join(lines)
