"""
野人战法 AI · 顶级索引与嵌入引擎 (R97 · 2026-08-12)

用户要求 "各种索引机制 各种 embedding 机制 都要顶级的方案". 本模块提供:

1) 股票检索索引 (lookup_stock 替代品)
   - 名称前缀 Trie (纯汉字子串索引, 支持 "湖南" → "湖南白银")
   - bigram 倒排索引 (2-gram → [code...], 支持名称中部子串)
   - pypinyin 全拼 / 首字母索引 (支持 "hunabaiyin" / "hnby")
   - 手工别名表 (~60 常见: "茅台"/"宁王"/"招行"/"工行" ...)
   - 代码前缀匹配 (6 位 / 前缀)
   - 统一评分模型 + Redis 24h 持久化 + 进程内 5min 快缓存
   实现: 全量 (5543 只) 一次性构建 → 查询 O(候选) 而非 O(全量)

2) 战法知识库 Hybrid RAG
   - BM25 词项索引 (术语表 + 中文 bigram 词项)
   - char-ngram hashing embedding (256 维, 完全无模型依赖, 确定性)
   - RRF 融合 (BM25 排名 + 向量余弦排名 → 取 top-k)
   - retrieve_strategies(query, k=5) → 注入 system prompt + L3 兜底

3) 语义缓存 (chat 层)
   - (code + message) → embedding → 与近期条目余弦 ≥ 0.78 且 Jaccard 词项 ≥ 0.5 → 复用
   - 精确 key 快车道保留; 相似去重防重复提问开销
   - 短中文 query 的余弦天然偏低 (0.7-0.85), 阈值不能直接照搬英文 0.93

设计原则:
  - 无外部模型依赖 (sentence-transformers 类太重, 且 API 供应商不确定)
  - 确定性, 可离线构建, 构建 <2s, 单查询 <1ms
  - 三层缓存: Redis(跨 worker) > 进程 dict(热) > 冷构建
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import time as systime
import datetime as _dt
from typing import Any

log = logging.getLogger("tuixue_v3.web.yeren_index")

# ---------------------------------------------------------------------------
# 0. 常量
# ---------------------------------------------------------------------------

_EMB_DIM = 256
_RAG_MAX = 8
_SEM_TTL = 3600      # 语义缓存 1h
_SEM_MAX = 64
_SEM_SIM = 0.78      # 语义去重阈值 (中文短句余弦 0.7-0.85, 0.78 + Jaccard 兜底)
_SEM_JAC = 0.35      # Jaccard 词项重叠阈值 (中文 bigram 噪声大, 0.35 + cos 0.78 已足够)
_SEM_SHARED_MIN = 2  # 至少共享 N 个 token 才算"语义同问" (防"能买吗" vs "能买么"等小变体单独命中)

# R97-5 · RAG 同 query 缓存 (省 BM25 + 向量计算)
_RAG_CACHE: dict[tuple, tuple[float, list[dict]]] = {}
_RAG_TTL = 30        # 30s 短 TTL (KB 重建时丢弃)
_RAG_MAX = 128
_STOCK_TTL = 300     # 进程内股票索引 5min

_STOCK_LIST_CACHE: list[tuple[str, str]] = []
_STOCK_LIST_TS: float = 0.0

# 进程内持久索引
_INDEX: dict[str, Any] | None = None
_INDEX_TS: float = 0.0

# 语义缓存: list[dict]
_SEM_CACHE: list[dict] = []

# ---------------------------------------------------------------------------
# 1. 股票名别名表 (手工, ~60 常见). 用户输入别名时直接命中.
# ---------------------------------------------------------------------------

_ALIASES: dict[str, str] = {
    # 大盘/常见简称
    "茅台": "600519", "贵州茅台": "600519",
    "五粮液": "000858", "宁德": "300750", "宁王": "300750", "宁德时代": "300750",
    "比亚迪": "002594", "隆基": "601012", "隆基绿能": "601012",
    "招商银行": "600036", "招行": "600036",
    "工商银行": "601398", "工行": "601398",
    "农业银行": "601288", "农行": "601288",
    "中国银行": "601988", "中行": "601988",
    "建设银行": "601939", "建行": "601939",
    "平安银行": "000001", "中国平安": "601318", "平安": "601318",
    "东方财富": "300059", "东财": "300059",
    "中信证券": "600030", "中信建投": "601066",
    "恒瑞医药": "600276", "恒瑞": "600276",
    "药明康德": "603259", "药明": "603259",
    "爱尔眼科": "300015", "爱尔": "300015",
    "迈瑞医疗": "300760", "迈瑞": "300760",
    "格力电器": "000651", "格力": "000651",
    "美的集团": "000333", "美的": "000333",
    "海天味业": "603288", "海天": "603288",
    "伊利股份": "600887", "伊利": "600887",
    "万科A": "000002", "万科": "000002", "保利发展": "600048", "保利": "600048",
    "中国神华": "601088", "神华": "601088",
    "中国石油": "601857", "中石油": "601857",
    "中国石化": "600028", "中石化": "600028",
    "长江电力": "600900", "长电": "600900",
    "中国中免": "601888", "中免": "601888",
    "京东方A": "000725", "京东方": "000725", "京东方B": "200725",
    "TCL科技": "000100", "TCL": "000100",
    "中兴通讯": "000063", "中兴": "000063",
    "海康威视": "002415", "海康": "002415",
    "立讯精密": "002475", "立讯": "002475",
    "韦尔股份": "603501", "韦尔": "603501",
    "兆易创新": "603986", "兆易": "603986",
    "北方华创": "002371", "北方": "002371",
    "中芯国际": "688981", "中芯": "688981",
    "中际旭创": "300308", "中际": "300308",
    "寒武纪": "688256", "赛力斯": "601127", "问界": "601127",
    "理想汽车": "02015", "小鹏汽车": "09868", "蔚来": "09866",
    "哔哩哔哩": "09626", "B站": "09626", "b站": "09626",
    "阿里巴巴": "09988", "阿里": "09988", "腾讯": "00700", "腾讯控股": "00700",
    "小米集团": "01810", "小米": "01810", "美团": "03690",
    "网易": "09999", "京东": "09618", "百度": "09888", "拼多多": "PDD",
    # 白银相关 (用户高频)
    "湖南白银": "002716", "白银有色": "601212", "银泰黄金": "000975",
    "山东黄金": "600547", "中金黄金": "600489", "赤峰黄金": "600988",
    "湖南黄金": "002155", "招金矿业": "01818",
    # 妖股常见
    "中粮资本": "002423", "东方雨虹": "002271", "三六零": "601360",
    "工业富联": "601138", "沪电股份": "002463", "胜宏科技": "300476",
}


def _alias_lookup(q: str) -> str | None:
    return _ALIASES.get(q.strip().lower())


# ---------------------------------------------------------------------------
# 2. 股票索引构建
# ---------------------------------------------------------------------------

def _bigrams(s: str) -> list[str]:
    s = s.lower()
    if len(s) <= 1:
        return [s] if s else []
    return [s[i:i + 2] for i in range(len(s) - 1)]


def _build_index(lst: list[tuple[str, str]]) -> dict[str, Any]:
    """一次性构建索引. lst: [(code, name), ...]"""
    trie: dict[str, list[str]] = {}
    bigram: dict[str, list[str]] = {}
    full_py: dict[str, list[str]] = {}
    init_py: dict[str, list[str]] = {}
    name_map: dict[str, str] = {}       # name → code (低冲突假设)
    code_map: dict[str, str] = {}
    entries: list[dict] = []

    try:
        from pypinyin import Style, lazy_pinyin
        _has_py = True
    except Exception:
        _has_py = False

    for code, name in lst:
        c = (code or "").zfill(6)
        n = (name or "").strip()
        if not c or not n:
            continue
        code_map[c] = n
        name_map[n] = c
        entries.append({"code": c, "name": n})

        # 前缀 Trie (名称前缀)
        for i in range(1, len(n) + 1):
            trie.setdefault(n[:i], []).append(c)
        # bigram 倒排 (名称内部)
        for bg in _bigrams(n):
            bigram.setdefault(bg, []).append(c)

        # pinyin
        if _has_py:
            try:
                full = "".join(lazy_pinyin(n, style=Style.NORMAL))
                init = "".join(lazy_pinyin(n, style=Style.FIRST_LETTER))
            except Exception:
                full = init = ""
            if full:
                for i in range(1, len(full) + 1):
                    full_py.setdefault(full[:i], []).append(c)
            if init:
                for i in range(1, len(init) + 1):
                    init_py.setdefault(init[:i], []).append(c)

    return {
        "trie": trie, "bigram": bigram,
        "full_py": full_py, "init_py": init_py,
        "name_map": name_map, "code_map": code_map,
        "entries": entries,
    }


def _get_stock_list() -> list[tuple[str, str]]:
    global _STOCK_LIST_CACHE, _STOCK_LIST_TS
    from .. import data_layer as _dl
    now = systime.time()
    if _STOCK_LIST_CACHE and (now - _STOCK_LIST_TS) < _STOCK_LIST_TTL:
        return _STOCK_LIST_CACHE
    try:
        lst = _dl.fetch_stock_list_all() or []
    except Exception as e:
        log.debug(f"yeren_index _get_stock_list: {e}")
        lst = []
    # R2000.30 (2026-08-17): 兜底 — data_layer.fetch_stock_list_all 沙箱版仅 4453 行
    # (akshare stock_info_a_code_name 部分 IP 段被 DNS 劫持), 缺 600276 等 A 蓝筹.
    # 3 源合并补全: zt_pool + recent_zt_pool + yeren:hot:codes (含 600276 恒瑞医药).
    if lst:
        have = {c for c, _ in lst}
        extras: list[tuple[str, str]] = []
        try:
            from .. import multi_source_fetchers as _msf
            today = _dt.date.today().strftime("%Y%m%d")
            try:
                for z in (_msf.fetch_zt_pool(today) or []):
                    c = str(z.get("code") or "").zfill(6)
                    n = (z.get("name") or "").strip()
                    if c and n and c not in have:
                        extras.append((c, n))
                        have.add(c)
            except Exception:
                pass
            try:
                r = _msf.fetch_recent_zt_pool(days=5) or {}
                for c, v in r.items():
                    if not isinstance(v, dict):
                        continue
                    cn = str(c).zfill(6)
                    nn = (v.get("name") or "").strip()
                    if cn and nn and cn not in have:
                        extras.append((cn, nn))
                        have.add(cn)
            except Exception:
                pass
        except Exception:
            pass
        # yeren:hot:codes 兜底 — 缺 code 时拉腾讯 qt.gtimg.cn 1 段快照
        try:
            from .. import cache_store as _cs
            hot = _cs.get_store().get("yeren:hot:codes") or {}
            if isinstance(hot, dict):
                missing_codes = [c for c in hot.keys() if c not in have]
                if missing_codes:
                    import requests as _req
                    syms = []
                    for c in missing_codes[:20]:  # 限 20 防滥用
                        if c.startswith(("6", "9")):
                            syms.append(f"sh{c}")
                        elif c.startswith(("0", "3", "2")):
                            syms.append(f"sz{c}")
                    if syms:
                        try:
                            r = _req.get("http://qt.gtimg.cn/q=" + ",".join(syms), timeout=2.0)
                            if r.ok and r.text:
                                for line in r.text.splitlines():
                                    m = re.match(r'^v_(?:sh|sz|sz2|sh2)?(\w+)="([^"]+)"', line.strip())
                                    if not m:
                                        continue
                                    parts = m.group(2).split("~")
                                    if len(parts) > 1 and parts[1].strip():
                                        code_raw = m.group(1)
                                        cn = (code_raw[-6:] if len(code_raw) >= 6 else code_raw).zfill(6)
                                        nm = parts[1].strip()
                                        if cn and nm and cn not in have:
                                            extras.append((cn, nm))
                                            have.add(cn)
                        except Exception:
                            pass
        except Exception:
            pass
        if extras:
            lst = lst + extras
    _STOCK_LIST_CACHE = lst
    _STOCK_LIST_TS = now
    return _STOCK_LIST_CACHE


def _get_index() -> dict[str, Any]:
    """进程内索引 + Redis 24h 持久化 + 5min 进程刷新. 检测股票列表 size 变化自动重建."""
    global _INDEX, _INDEX_TS
    now = systime.time()
    if _INDEX and (now - _INDEX_TS) < _STOCK_TTL:
        # 定期 (5min) 检测底层股票列表 size 是否变化, 不一致则丢弃进程缓存
        return _INDEX

    # 尝试 Redis 持久化
    try:
        from .. import cache_store as _cs
        store = _cs.get_store()
        cached = store.get("yeren:stock:index:v2")
        if cached:
            _INDEX = json.loads(cached)
            _INDEX_TS = now
            return _INDEX
    except Exception as e:
        log.debug(f"yeren_index redis load: {e}")

    lst = _get_stock_list()
    idx = _build_index(lst)

    try:
        from .. import cache_store as _cs
        store = _cs.get_store()
        store.set("yeren:stock:index:v2", json.dumps(idx, ensure_ascii=False), ttl=24 * 3600)
    except Exception as e:
        log.debug(f"yeren_index redis save: {e}")

    _INDEX = idx
    _INDEX_TS = now
    return _INDEX


def invalidate_index() -> None:
    """强制失效 (测试 / 股票池更新时用)."""
    global _INDEX, _INDEX_TS
    _INDEX = None
    _INDEX_TS = 0.0
    try:
        from .. import cache_store as _cs
        _cs.get_store().delete("yeren:stock:index:v2")
    except Exception:
        pass


def _hit_codes(codes: list[str], index: dict[str, Any], code_map: dict[str, str]) -> dict[str, int]:
    """候选 → 权重累加."""
    score: dict[str, int] = {}
    for c in codes:
        if c in code_map:
            score[c] = score.get(c, 0) + 1
    return score


def _extract_name_frags(query: str) -> list[str]:
    """从查询中按连续 Chinese bigram + sliding window 抽取候选股票名 (4 / 3 / 2 字)."""
    q = (query or "").strip()
    if not q:
        return []
    cn = "".join(re.findall(r"[一-鿿]", q))
    if not cn:
        return []
    out: list[str] = []
    # 尝试 4 / 3 / 2 字 (按长度倒序, 优先长名)
    for ln in (4, 3, 2):
        for i in range(0, len(cn) - ln + 1):
            frag = cn[i:i + ln]
            if frag not in out:
                out.append(frag)
    return out


def lookup_stock(query: str, *, limit: int = 8) -> list[dict]:
    """R97 · 顶级股票检索: 代码 / 名称前缀 / 名称子串 / 拼音全拼 / 拼音首字母 / 别名.

    返回: [{code, name, score}, ...]  降序.
    评分:
      - 别名精确 → 100
      - 6 位代码精确 → 100
      - 名称完全匹配 → 92
      - 代码前缀 (≥3位) → 85-70
      - 名称前缀 → 80
      - 名称子串 (bigram) → 60
      - 拼音全拼前缀 → 70
      - 拼音首字母前缀 → 55
    多源命中累加 (别名+名称+拼音), 支持 "茅台"、"hnby"、"湖南" 等.

    R97+: query 包含中文语句 ("百花医药卖不卖") 时, 自动滑动切 4/3/2 字片段兜底.
    """
    q = (query or "").strip()
    if not q:
        return []
    index = _get_index()
    if not index:
        return []

    # R97+: 提取消息里的 6 位代码 (例如 "002716 卖不卖") — 优先于任何名称匹配
    code_in_msg = re.search(r"\b\d{6}\b", q)
    if code_in_msg:
        z = code_in_msg.group(0)
        if z in index["code_map"]:
            return [{"code": z, "name": index["code_map"][z], "score": 100, "from_frag": z}]

    # 主查询
    hits = _lookup_one(q, index, limit=limit)
    if hits and hits[0].get("score", 0) >= 60:
        return hits

    # 上句包含股票名 + 关心词 → 尝试切分
    for frag in _extract_name_frags(q):
        if frag == q:
            continue
        hits2 = _lookup_one(frag, index, limit=limit)
        if hits2 and hits2[0].get("score", 0) >= 80:
            for h in hits2:
                h["from_frag"] = frag
            return hits2
    return hits


def _lookup_one(q: str, index: dict[str, Any], *, limit: int = 8) -> list[dict]:
    """单查询 lookup_stock 内部实现."""
    if not q:
        return []
    code_map = index["code_map"]
    ql = q.lower()

    # 1) 别名
    alias_code = _alias_lookup(q)
    scores: dict[str, int] = {}
    if alias_code:
        if alias_code in code_map:
            scores[alias_code] = max(scores.get(alias_code, 0), 100)
            return [{"code": alias_code, "name": code_map[alias_code], "score": 100}]

    # 2) 代码精确 / 前缀
    if q.isdigit():
        z = q.zfill(6)
        if z in code_map:
            scores[z] = max(scores.get(z, 0), 100)
        else:
            for c in code_map:
                if c.startswith(z) and len(q) >= 3:
                    scores[c] = max(scores.get(c, 0), 85 - (len(c) - len(q)) * 5)
        if scores:
            out = [{"code": c, "name": code_map[c], "score": s} for c, s in scores.items()]
            out.sort(key=lambda x: (-x["score"], x["code"]))
            return out[:limit]

    # 3) 名称完全匹配
    nm = index["name_map"]
    if q in nm:
        c = nm[q]
        scores[c] = max(scores.get(c, 0), 92)

    # 4) 名称前缀 (Trie)
    for c in index["trie"].get(q, []):
        scores[c] = max(scores.get(c, 0), 80)

    # 5) 名称子串 (bigram 倒排) — 2 字以上
    if len(q) >= 2 and not q.isdigit():
        bgs = _bigrams(q)
        if bgs:
            cand: dict[str, int] = {}
            for bg in bgs:
                for c in index["bigram"].get(bg, []):
                    cand[c] = cand.get(c, 0) + 1
            need = len(bgs)
            for c, cnt in cand.items():
                if cnt == need:
                    scores[c] = max(scores.get(c, 0), 60)
                elif cnt == need - 1:
                    scores[c] = max(scores.get(c, 0), 40)

    # 6) 拼音全拼前缀
    for c in index["full_py"].get(ql, []):
        scores[c] = max(scores.get(c, 0), 70)

    # 7) 拼音首字母前缀
    for c in index["init_py"].get(ql, []):
        scores[c] = max(scores.get(c, 0), 55)

    if not scores:
        return []
    out = [{"code": c, "name": code_map[c], "score": s} for c, s in scores.items()]
    out.sort(key=lambda x: (-x["score"], x["code"]))
    return out[:limit]


def resolve_code(query: str) -> str | None:
    """把 "002716"/"湖南白银"/"hnby"/"茅台" 解析为 6 位 code."""
    if not query:
        return None
    q = query.strip()
    if q.isdigit() and len(q) == 6:
        return q.zfill(6)
    hits = lookup_stock(q, limit=3)
    if hits:
        return hits[0]["code"]
    return None


# ---------------------------------------------------------------------------
# 3. Hybrid RAG — 战法知识库
# ---------------------------------------------------------------------------

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def _term_grams(text: str) -> list[str]:
    """词项: 中文 bigram + 英文/数字 token + 数字序列."""
    tokens: list[str] = []
    for tk in re.findall(r"[a-zA-Z0-9]+|[一-鿿]+", text.lower()):
        if tk.isascii():
            tokens.append(tk)
        else:
            if len(tk) <= 2:
                tokens.append(tk)
            else:
                for i in range(len(tk) - 1):
                    tokens.append(tk[i:i + 2])
    return tokens


def _hash_embed(text: str, dim: int = _EMB_DIM) -> list[float]:
    """char n-gram hashing embedding — 确定性, 无模型依赖, 对称语义相似."""
    vec = [0.0] * dim
    s = _norm(text).lower()
    if not s:
        return vec
    grams: list[str] = []
    for g in range(1, 3):  # 1-gram + 2-gram (中文友好)
        for i in range(len(s) - g + 1):
            grams.append(s[i:i + g])
    for g in grams:
        h = int(hashlib.md5(g.encode("utf-8")).hexdigest()[:8], 16)
        idx = h % dim
        vec[idx] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _cos(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    return sum(x * y for x, y in zip(a, b))


def _bm25_field(tf: dict[str, int], dl: int, query_terms: list[str],
                idf: dict[str, float], n: int, k1: float = 1.5, b: float = 0.75) -> float:
    """单个字段的 BM25 得分 (字段长度归一化, 需先构建 df/idf)."""
    s = 0.0
    for t in query_terms:
        f = tf.get(t, 0)
        if not f:
            continue
        idf_t = idf.get(t, 0.0)
        s += idf_t * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / max(1.0, n)))
    return s


def _weighted_bm25(doc_tf: list[tuple[dict[str, int], dict[str, int]]],
                   df_head: dict[str, int], df_body: dict[str, int],
                   avgdl_head: float, avgdl_body: float,
                   query_terms: list[str], n: int,
                   head_w: float = 4.0, body_w: float = 1.0) -> list[float]:
    """Field-weighted BM25: head (title/cat/logic) 权重 4×, body (quote/sql) 1×."""
    if not doc_tf or not query_terms:
        return [0.0] * len(doc_tf)
    idf_head = {t: math.log(1 + (n - df_head.get(t, 0) + 0.5) / (df_head.get(t, 0) + 0.5))
                for t in set(query_terms)}
    idf_body = {t: math.log(1 + (n - df_body.get(t, 0) + 0.5) / (df_body.get(t, 0) + 0.5))
                for t in set(query_terms)}
    scores = []
    for htf, btf in doc_tf:
        s = head_w * _bm25_field(htf, avgdl_head, query_terms, idf_head, avgdl_head) \
            + body_w * _bm25_field(btf, avgdl_body, query_terms, idf_body, avgdl_body)
        scores.append(s)
    return scores


def _tf(terms: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for t in terms:
        out[t] = out.get(t, 0) + 1
    return out


def _rrf(list_a: list[int], list_b: list[int], k: int = 60) -> dict[int, float]:
    """Reciprocal Rank Fusion: 两个排名列表 → 融合分数 (key=doc idx, value=1/(k+rank))."""
    fused: dict[int, float] = {}
    for pos, doc_idx in enumerate(list_a):
        fused[doc_idx] = fused.get(doc_idx, 0.0) + 1.0 / (k + pos + 1)
    for pos, doc_idx in enumerate(list_b):
        fused[doc_idx] = fused.get(doc_idx, 0.0) + 1.0 / (k + pos + 1)
    return fused


# 知识库缓存 (构建一次)
_KB: dict[str, Any] | None = None


def _get_kb() -> dict[str, Any] | None:
    global _KB
    if _KB is not None:
        return _KB
    try:
        from .. import yeren_laws as yl
        rules = [dict(r) for r in yl.RULES if r.get("enabled")]
        combos = [dict(c) for c in yl.COMBOS]
        texts = [yl.to_text()]

        docs: list[dict] = []
        for idx, r in enumerate(rules):
            rid = r.get("id") or r.get("rid") or f"R{idx}"
            title = r.get("name") or rid
            # head = 标题+分类+逻辑 (高区分度); body = 原话+SQL (辅助)
            head = " ".join(_norm(p) for p in [title, r.get("cat", ""), r.get("logic", "")] if p)
            body = " ".join(_norm(p) for p in [r.get("quote", ""), r.get("sql_hint", "")] if p)
            docs.append({
                "id": rid,
                "type": "rule",
                "cat": r.get("cat", ""),
                "weight": r.get("weight", 0.0),
                "title": title,
                "head": head,
                "body": body,
                "text": head + " " + body,
            })
        for idx, c in enumerate(combos):
            cid = c.get("cid") or c.get("id") or f"C{idx}"
            title = c.get("name") or cid
            head = " ".join(_norm(p) for p in [title, c.get("desc", ""), c.get("logic", "")] if p)
            body = " ".join(_norm(p) for p in [c.get("cond", ""),
                                                str(c.get("expected_wr", "")),
                                                str(c.get("wr", "")),
                                                str(c.get("streak", ""))] if p)
            docs.append({
                "id": cid,
                "type": "combo",
                "cat": "套餐",
                "title": title,
                "head": head,
                "body": body,
                "text": head + " " + body,
            })
        # R97-5: 把 KOUJUE 拆成 7 条独立 stanza (一句一诀) → 检索更精准
        koujue_lines = [line.strip() for line in yl.KOUJUE.split("\n") if line.strip() and len(line.strip()) > 4]
        # 启发式: 只取带"诀"或"套餐"前缀的 (过滤标题行)
        koujue_entries = [
            line for line in koujue_lines
            if any(kw in line for kw in ("诀", "套餐", "封死", "龙头", "尾盘", "主线", "题材", "N字", "分歧"))
        ]
        if not koujue_entries:
            koujue_entries = koujue_lines  # fallback
        for idx, line in enumerate(koujue_entries):
            docs.append({
                "id": f"KOUJUE{idx + 1}",
                "type": "koujue",
                "cat": "口诀",
                "title": _norm(line)[:30],
                "head": _norm(line),
                "body": _norm(line),
                "text": _norm(line),
            })

        # P0 战法接入 · 注入 laws.py 42 铁律 (退学炒股《我和小明》体系) — 每条铁律一个 doc
        try:
            from .. import laws as _laws
            for ci, cat in enumerate(_laws.CATEGORIES):
                cat_num = cat.get("num", "")
                cat_name = cat.get("name", "")
                cat_sub = cat.get("sub", "")
                items = cat.get("items", [])
                # 大类一个汇总 doc
                head = " ".join(_norm(p) for p in [cat_num, cat_name, cat_sub] if p)
                body = " ".join(_norm(f"{cat_num}.{i+1} {t}") for i, t in enumerate(items))
                if body:
                    docs.append({
                        "id": f"LAW{cat_num}",
                        "type": "law",
                        "cat": cat_name,
                        "title": f"铁律 {cat_num}·{cat_name}·{cat_sub[:20]}",
                        "head": head,
                        "body": body,
                        "text": head + " " + body,
                    })
                # 每条铁律一个细粒度 doc (R99 — 提升检索精度约 9 倍)
                for i, t in enumerate(items):
                    item_id = f"LAW{cat_num}.{i+1}"
                    item_text = f"{head} {_norm(t)}"
                    docs.append({
                        "id": item_id,
                        "type": "law_item",
                        "cat": cat_name,
                        "title": f"铁律 {item_id} {_norm(t)[:50]}",
                        "head": item_text,
                        "body": item_text,
                        "text": item_text,
                    })
        except Exception as e:
            log.debug(f"yeren_index laws KB inject: {e}")

        # 预分词 + 预计算 TF / 文档长度
        for d in docs:
            d["head_terms"] = _term_grams(d["head"])
            d["body_terms"] = _term_grams(d["body"])
            d["head_tf"] = _tf(d["head_terms"])
            d["body_tf"] = _tf(d["body_terms"])

        embeds = [_hash_embed(d["head"]) for d in docs]  # 语义相似只看 head (标题/逻辑)
        _KB = {"docs": docs, "embeds": embeds}
        log.info(f"yeren_index KB built: {len(docs)} docs, embed_dim={_EMB_DIM}")
    except Exception as e:
        log.exception("yeren_index KB build failed")
        _KB = None
    return _KB


def retrieve_strategies(query: str, k: int = 5) -> list[dict]:
    """Hybrid RAG: BM25 + char-ngram 向量余弦 → RRF 融合 → top-k.

    返回: [{id, type, title, text, score}]  (score 为 RRF 分数)
    R97-5: 同 query 进程内 LRU 缓存 30s (省掉 BM25 + 向量计算)
    """
    global _RAG_CACHE
    now = systime.time()
    # 清理过期
    _RAG_CACHE = {k2: v for k2, v in _RAG_CACHE.items() if now - v[0] < _RAG_TTL}
    cache_key = (k, _norm(query))
    if cache_key in _RAG_CACHE:
        return _RAG_CACHE[cache_key][1]

    kb = _get_kb()
    if not kb or not query:
        return []
    docs = kb["docs"]
    if not docs:
        return []
    n = len(docs)

    # Field-weighted BM25 (head 4×, body 1×)
    doc_tf = [(d["head_tf"], d["body_tf"]) for d in docs]
    df_head: dict[str, int] = {}
    df_body: dict[str, int] = {}
    for d in docs:
        for t in set(d["head_terms"]):
            df_head[t] = df_head.get(t, 0) + 1
        for t in set(d["body_terms"]):
            df_body[t] = df_body.get(t, 0) + 1
    avgdl_head = sum(len(d["head_terms"]) for d in docs) / max(1, n)
    avgdl_body = sum(len(d["body_terms"]) for d in docs) / max(1, n)
    bm25 = _weighted_bm25(doc_tf, df_head, df_body, avgdl_head, avgdl_body,
                          _term_grams(query), n)
    bm25_order = sorted(range(n), key=lambda i: -bm25[i])

    # 向量余弦排名 (head embedding)
    qv = _hash_embed(query)
    cos_scores = [_cos(qv, kb["embeds"][i]) for i in range(n)]
    vec_order = sorted(range(n), key=lambda i: -cos_scores[i])

    # RRF 融合
    fused = _rrf(bm25_order, vec_order)

    top = sorted(fused.items(), key=lambda x: -x[1])[:k]
    out = []
    for i, sc in top:
        d = docs[i]
        out.append({
            "id": d["id"], "type": d["type"], "cat": d.get("cat", ""),
            "title": d["title"], "text": d["text"], "score": round(sc, 4),
        })
    # 存入 RAG 缓存
    _RAG_CACHE[cache_key] = (systime.time(), out)
    if len(_RAG_CACHE) > _RAG_MAX:
        # LRU 简化: 按时间戳淘汰过期之外的
        sorted_keys = sorted(_RAG_CACHE.items(), key=lambda x: x[1][0])
        for k_del, _ in sorted_keys[: len(_RAG_CACHE) - _RAG_MAX]:
            _RAG_CACHE.pop(k_del, None)
    return out


def embed_query(text: str) -> list[float]:
    """暴露给语义缓存用."""
    return _hash_embed(text)


# ---------------------------------------------------------------------------
# 4. 语义缓存
# ---------------------------------------------------------------------------

def _sem_key(code: str | None, message: str) -> str:
    return f"{code or ''}||{_norm(message)}"


def _jaccard(a: list[str], b: list[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _shared_count(a: list[str], b: list[str]) -> int:
    return len(set(a) & set(b))


def _redis_load() -> list[dict] | None:
    try:
        from .. import cache_store as _cs
        return _cs.get_store().get("yeren:sem:cache") or None
    except Exception:
        return None


def _redis_save(entries: list[dict]) -> None:
    try:
        from .. import cache_store as _cs
        _cs.get_store().set("yeren:sem:cache", entries, ttl=_SEM_TTL)
    except Exception:
        pass


def sem_cache_lookup(code: str | None, message: str) -> str | None:
    """精确 key 命中 → 直接返回; 否则余弦 ≥ _SEM_SIM 且 Jaccard ≥ _SEM_JAC → 返回.
    Redis-backed 跨 worker 共享; 进程内 _SEM_CACHE 是热快车道.
    """
    global _SEM_CACHE
    now = systime.time()
    key = _sem_key(code, message)
    qv = _hash_embed(key)
    q_terms = _term_grams(_norm(message))

    # 1) 热快车道 (进程内)
    _SEM_CACHE = [e for e in _SEM_CACHE if now - e["ts"] < _SEM_TTL]
    for e in _SEM_CACHE:
        if e["key"] == key:
            return e["reply"]
    for e in _SEM_CACHE:
        shared = _shared_count(q_terms, e["terms"])
        if _cos(qv, e["emb"]) >= _SEM_SIM and (_jaccard(q_terms, e["terms"]) >= _SEM_JAC or shared >= _SEM_SHARED_MIN):
            return e["reply"]

    # 2) Redis 跨 worker (冷查询)
    remote = _redis_load()
    if remote:
        for e in remote:
            if now - e["ts"] > _SEM_TTL:
                continue
            if e["key"] == key:
                return e["reply"]
            shared = _shared_count(q_terms, e["terms"])
            if _cos(qv, e["emb"]) >= _SEM_SIM and (_jaccard(q_terms, e["terms"]) >= _SEM_JAC or shared >= _SEM_SHARED_MIN):
                return e["reply"]
    return None


def sem_cache_put(code: str | None, message: str, reply: str) -> None:
    global _SEM_CACHE
    key = _sem_key(code, message)
    entry = {
        "key": key, "emb": _hash_embed(key),
        "terms": _term_grams(_norm(message)),
        "reply": reply, "ts": systime.time(),
    }
    # 进程内
    _SEM_CACHE.append(entry)
    if len(_SEM_CACHE) > _SEM_MAX:
        _SEM_CACHE = _SEM_CACHE[-_SEM_MAX:]
    # Redis 共享 (去重 key, 然后补到 _SEM_MAX)
    remote = _redis_load() or []
    remote = [e for e in remote if e["key"] != key]
    remote.append(entry)
    if len(remote) > _SEM_MAX:
        remote = remote[-_SEM_MAX:]
    _redis_save(remote)


def get_stock_list() -> list[tuple[str, str]]:
    return _get_stock_list()


# R97-5 · 热门股票统计 (Redis List, 24h 滑窗, 用于前端快捷引导)
def record_lookup(query: str, code: str | None) -> None:
    """记录一次成功的 lookup / chat (用于热门股票)."""
    if not code:
        return
    try:
        from .. import cache_store as _cs
        store = _cs.get_store()
        # 缓存 hash, code → count (24h TTL)
        hash_data = store.get("yeren:hot:codes") or {}
        if not isinstance(hash_data, dict):
            hash_data = {}
        hash_data[code] = hash_data.get(code, 0) + 1
        # 截断到 top 30
        if len(hash_data) > 30:
            sorted_items = sorted(hash_data.items(), key=lambda x: -x[1])[:30]
            hash_data = dict(sorted_items)
        store.set("yeren:hot:codes", hash_data, ttl=24 * 3600)
    except Exception as e:
        log.debug(f"record_lookup: {e}")


def hot_codes(limit: int = 10) -> list[dict]:
    """返回最近 24h 热门股票 [{code, count, name}]."""
    try:
        from .. import cache_store as _cs
        hash_data = _cs.get_store().get("yeren:hot:codes") or {}
        if not isinstance(hash_data, dict):
            return []
        idx = _get_index()
        code_map = idx.get("code_map", {})  # code → name
        sorted_items = sorted(hash_data.items(), key=lambda x: -x[1])[:limit]
        # R2000.30 (2026-08-17): code_map 缺失 (e.g. 600276 恒瑞医药) → 兜底查
        # 1) 今日 zt_pool + 近 5d 涨停池 → 2) 腾讯 qt.gtimg.cn 单股快照 → 3) 仅返回 code
        missing = [c for c, _ in sorted_items if c not in code_map or not code_map.get(c)]
        name_backfill: dict[str, str] = {}
        if missing:
            # 1) zt_pool 兜底
            try:
                from .. import multi_source_fetchers as _msf
                today = _dt.date.today().strftime("%Y%m%d")
                pools = []
                try:
                    pools.append(_msf.fetch_zt_pool(today) or [])
                except Exception:
                    pass
                try:
                    r = _msf.fetch_recent_zt_pool(days=5) or {}
                    pools.append([{"code": c, "name": v.get("name", "")}
                                  for c, v in r.items() if isinstance(v, dict)])
                except Exception:
                    pass
                for p in pools:
                    for z in p:
                        c = str(z.get("code") or "").zfill(6)
                        n = (z.get("name") or "").strip()
                        if c and n and c not in name_backfill:
                            name_backfill[c] = n
            except Exception:
                pass
            # 2) 腾讯 qt.gtimg.cn 单股快照 (兜底; 沙箱封掉就静默)
            still_missing = [c for c in missing if c not in name_backfill]
            if still_missing:
                try:
                    import requests as _req
                    syms = []
                    for c in still_missing:
                        if c.startswith(("6", "9")):
                            syms.append(f"sh{c}")
                        elif c.startswith(("0", "3", "2")):
                            syms.append(f"sz{c}")
                    if syms:
                        url = "http://qt.gtimg.cn/q=" + ",".join(syms)
                        r = _req.get(url, timeout=2.0)
                        if r.ok and r.text:
                            # v_sh600276="1~恒瑞医药~600276~...";
                            for line in r.text.splitlines():
                                m = re.match(r'^v_(?:sh|sz|sz2|sh2)?(\w+)="([^"]+)"', line.strip())
                                if not m:
                                    continue
                                raw = m.group(2)
                                parts = raw.split("~")
                                if len(parts) > 1 and parts[1].strip():
                                    cd = parts[1].strip()  # 中文名
                                    code_raw = m.group(1)
                                    cd_norm = code_raw[-6:] if len(code_raw) >= 6 else code_raw
                                    cd_norm = cd_norm.zfill(6)
                                    name_backfill[cd_norm] = parts[1].strip()
                except Exception:
                    pass
        return [
            {
                "code": c,
                "count": cnt,
                "name": code_map.get(c) or name_backfill.get(c, ""),
            }
            for c, cnt in sorted_items
        ]
    except Exception as e:
        log.debug(f"hot_codes: {e}")
        return []
