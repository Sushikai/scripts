"""AI features 1000-round stress test.

Hits all AI endpoints across multiple stocks, captures failures:
- /api/stock/{code}/ai_analysis (AI 铁律)
- /api/stock/{code}/ai_crash_risk (AI 砸盘风险)
- /api/stock/{code}/deep_analysis (AI 深度判断)
- /api/news (AI news analysis)
- /api/ai_review (AI 复盘)
- /api/ai_scoring (AI 评分)
"""
import asyncio
import json
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

BASE = "http://127.0.0.1:7799"
CODES = ["605179", "000001", "000428", "002659", "300750", "600519", "688981", "830799"]

HISTORY_FILE = Path("/tmp/ai_stress_history.json")


def log_issue(category, msg):
    if not HISTORY_FILE.exists():
        HISTORY_FILE.write_text("[]")
    try:
        history = json.loads(HISTORY_FILE.read_text())
    except Exception:
        history = []
    history.append({"ts": time.time(), "category": category, "msg": msg})
    HISTORY_FILE.write_text(json.dumps(history[-2000:], indent=2, ensure_ascii=False))


def fetch(path, timeout=30):
    url = BASE + path
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, {"raw": body[:200].decode(errors="ignore")}
    except Exception as e:
        return 0, {"error": str(e)[:120]}


def check_ai_analysis(code, round_num):
    """Test /api/stock/{code}/ai_analysis (AI 铁律)."""
    issues = []
    t0 = time.time()
    status, body = fetch(f"/api/stock/{code}/ai_analysis", timeout=45)
    latency = (time.time() - t0) * 1000
    if status != 200:
        issues.append({"endpoint": "ai_analysis", "code": code, "status": status, "issue": f"http_{status}"})
        return issues
    data = body.get("data") or {}
    verdict = data.get("verdict")
    summary = data.get("summary")
    if not verdict:
        issues.append({"endpoint": "ai_analysis", "code": code, "issue": "no_verdict"})
    if not summary:
        issues.append({"endpoint": "ai_analysis", "code": code, "issue": "no_summary"})
    if latency > 30000:
        issues.append({"endpoint": "ai_analysis", "code": code, "issue": f"slow:{latency:.0f}ms"})
    return issues


def check_crash_risk(code, round_num):
    """Test /api/stock/{code}/ai_crash_risk."""
    issues = []
    t0 = time.time()
    status, body = fetch(f"/api/stock/{code}/ai_crash_risk", timeout=30)
    latency = (time.time() - t0) * 1000
    if status != 200:
        issues.append({"endpoint": "crash_risk", "code": code, "status": status, "issue": f"http_{status}"})
        return issues
    data = body.get("data") or {}
    risk = data.get("crash_risk") or data.get("risk")
    if not risk:
        issues.append({"endpoint": "crash_risk", "code": code, "issue": "no_risk"})
    if latency > 15000:
        issues.append({"endpoint": "crash_risk", "code": code, "issue": f"slow:{latency:.0f}ms"})
    return issues


def check_deep_analysis(code, round_num):
    """Test /api/stock/{code}/deep_analysis."""
    issues = []
    t0 = time.time()
    status, body = fetch(f"/api/stock/{code}/deep_analysis?background=1", timeout=15)
    if status != 200:
        issues.append({"endpoint": "deep_analysis", "code": code, "status": status, "issue": f"http_{status}"})
        return issues
    data = body.get("data") or {}
    if not data.get("queued") and not data.get("from_cache") and not data.get("reason"):
        issues.append({"endpoint": "deep_analysis", "code": code, "issue": "no_queue_or_cache"})
    return issues


def check_news(round_num):
    """Test /api/news."""
    issues = []
    t0 = time.time()
    status, body = fetch(f"/api/news", timeout=20)
    latency = (time.time() - t0) * 1000
    if status != 200:
        issues.append({"endpoint": "news", "status": status, "issue": f"http_{status}"})
        return issues
    data = body.get("data") or {}
    news = data.get("news") or []
    if not news:
        issues.append({"endpoint": "news", "issue": "empty_news"})
    if latency > 10000:
        issues.append({"endpoint": "news", "issue": f"slow:{latency:.0f}ms"})
    return issues


def check_ai_scoring(round_num):
    """Test /api/meta/recommend (proxy for AI scoring output)."""
    issues = []
    t0 = time.time()
    status, body = fetch(f"/api/meta/recommend?top_n=10", timeout=60)
    latency = (time.time() - t0) * 1000
    if status != 200:
        issues.append({"endpoint": "meta_recommend", "status": status, "issue": f"http_{status}"})
        return issues
    data = body.get("data") or {}
    picks = data.get("picks") or []
    if not picks:
        issues.append({"endpoint": "meta_recommend", "issue": "empty_picks"})
    if latency > 30000:
        issues.append({"endpoint": "meta_recommend", "issue": f"slow:{latency:.0f}ms"})
    return issues


def check_ai_review(round_num):
    """Test /api/review/trades (AI 复盘 entries)."""
    issues = []
    t0 = time.time()
    status, body = fetch(f"/api/review/trades?days=7", timeout=20)
    latency = (time.time() - t0) * 1000
    if status != 200:
        issues.append({"endpoint": "review_trades", "status": status, "issue": f"http_{status}"})
        return issues
    if latency > 15000:
        issues.append({"endpoint": "review_trades", "issue": f"slow:{latency:.0f}ms"})
    return issues


async def main():
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    delay = float(sys.argv[2]) if len(sys.argv) > 2 else 0
    clear_streak = 0

    for i in range(rounds):
        all_issues = []
        # 1. AI 铁律 (per stock)
        for code in CODES:
            all_issues.extend(check_ai_analysis(code, i + 1))
        # 2. AI 砸盘 (per stock, but fewer — uses LLM)
        for code in CODES[:4]:
            all_issues.extend(check_crash_risk(code, i + 1))
        # 3. AI 深度判断 (just queue, fast)
        for code in CODES[:3]:
            all_issues.extend(check_deep_analysis(code, i + 1))
        # 4. AI news (1 call)
        all_issues.extend(check_news(i + 1))
        # 5. AI scoring (1 call)
        all_issues.extend(check_ai_scoring(i + 1))
        # 6. AI review (1 call)
        all_issues.extend(check_ai_review(i + 1))

        # Log
        for iss in all_issues:
            log_issue(f"R{i+1}:{iss.get('endpoint','?')}", json.dumps(iss, ensure_ascii=False))

        print(f"\n=== Round {i+1} ===")
        print(f"Issues: {len(all_issues)}")
        for iss in all_issues[:10]:
            print(f"  [{iss.get('endpoint')}] {iss.get('code', '-')}: {iss.get('issue')}")
        if not all_issues:
            clear_streak += 1
            print(f"✓ ALL CLEAR (streak={clear_streak})")
        else:
            clear_streak = 0
        await asyncio.sleep(delay)


if __name__ == "__main__":
    asyncio.run(main())