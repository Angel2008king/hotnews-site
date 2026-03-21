#!/usr/bin/env python3 in range(RETRY + 1):
        try:
            r = _session.get(url, timeout=TIMEOUT, allow_redirects=True)
            s = r.status_code
            if 200 <= s < 300:
                r.encoding = r.apparent_encoding or r.encoding or "utf-8"
                return r.text
            elif s in (401, 403):
                _rotate_ua(); last = Exception(f"Forbidden {s}")
            elif s in (404,):
                raise RuntimeError(f"404 Not Found: {url}")
            elif s in (429,):
                last = Exception("Too Many Requests")
            elif 500 <= s < 600:
                last = Exception(f"Server error {s}")
            else:
                last = Exception(f"HTTP {s}")
        except Exception as e:
            last = e
        time.sleep(delay)
        delay = min(delay * 1.8, 6.0)
    raise RuntimeError(f"请求失败：{url} 错误：{last}")

# ===================== RSS 抓取 =====================
def fetch_rss(feed_url: str, source_name: str) -> list[dict[str, Any]]:
    if not feedparser:
        return []
    d = feedparser.parse(feed_url)
    items: list[dict[str, Any]] = []
    for e in d.entries[:MAX_ITEMS_PER_SOURCE]:
        title = normalize_space(getattr(e, "title", ""))
        link = safe_url(getattr(e, "link", "") or getattr(e, "id", ""))
        if not title or not link:
            continue
        summary = strip_html(getattr(e, "summary", ""))
        published = normalize_space(getattr(e, "published", "")) or normalize_space(getattr(e, "updated", ""))
        pub_parsed = getattr(e, "published_parsed", None) or getattr(e, "updated_parsed", None)
        items.append({
            "title": title, "url": link, "summary": summary,
            "published": published, "published_parsed": pub_parsed,
            "source": source_name, "via": "rss"
        })
    return items

# ===================== 通用链接抓取 =====================
def _parse_generic_links(html: str, domain_allow: tuple[str, ...]) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    out: list[dict[str, Any]] = []
    for a in soup.select("a"):
        title = normalize_space(a.get_text())
        href = safe_url(a.get("href") or "")
        if not title or not href or not href.startswith("http"):
            continue
        if domain_allow and not any(d in href for d in domain_allow):
            continue
        if "javascript:" in href or "#" in href:
            continue
        if len(title) < 6:
            continue
        out.append({"title": title, "url": href})
        if len(out) >= MAX_ITEMS_PER_SOURCE:
            break
    return out

def _extract_by_selectors(soup: BeautifulSoup, selectors: list[str], domain_allow: tuple[str, ...], limit: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for sel in selectors:
        for a in soup.select(sel):
            title = normalize_space(a.get_text())
            href = safe_url(a.get("href") or "")
            if not title or not href or not href.startswith("http"):
                continue
            if any(seg in href for seg in ("#", "javascript:")):
                continue
            if domain_allow and not any(d in href for d in domain_allow):
                continue
            key = (title, href)
            if key in seen or len(title) < 6:
                continue
            seen.add(key)
            out.append({"title": title, "url": href})
            if len(out) >= limit:
                return out
        if len(out) >= limit:
            break
    return out

# ===================== 站点解析 =====================
def parse_cctv_index(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    selectors = [".roll_yw a", ".newslist a", ".title_list a", "section a"]
    return _extract_by_selectors(soup, selectors, ("news.cctv.cn", "news.cctv.com"), MAX_ITEMS_PER_SOURCE) \
        or _parse_generic_links(html, ("news.cctv.cn", "news.cctv.com"))

def parse_cctv_china(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    selectors = ["#newslist a", ".brecommend a", ".tuwen a", "section a"]
    return _extract_by_selectors(soup, selectors, ("news.cctv.com/china", "news.cctv.com"), MAX_ITEMS_PER_SOURCE) \
        or _parse_generic_links(html, ("news.cctv.com/china", "news.cctv.com"))

def parse_xinhua(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    selectors = ["section a", ".news a", ".data a", ".headline a", ".list a"]
    return _extract_by_selectors(soup, selectors, ("www.news.cn",), MAX_ITEMS_PER_SOURCE) \
        or _parse_generic_links(html, ("www.news.cn",))

def parse_net163_domestic(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    selectors = ["#js_D_list a", ".news_default_list a", ".post_item a", "section a"]
    return _extract_by_selectors(soup, selectors, ("news.163.com", "www.163.com"), MAX_ITEMS_PER_SOURCE) \
        or _parse_generic_links(html, ("news.163.com", "www.163.com"))

def parse_nandu(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, Any]] = []
    for a in soup.select("a"):
        href = safe_url(a.get("href") or "")
        if not href or not href.startswith(("http", "/")) or "javascript:" in href:
            continue
        if not ("/node_" in href or "/content/" in href or href.endswith(".shtml")):
            continue
        container = a
        for _ in range(3):
            if container.parent:
                container = container.parent
        raw = normalize_space(container.get_text(separator=" "))
        if not raw:
            continue
        raw = raw.replace("查看详情", " ")
        raw = re.sub(r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{2}\b", " ", raw)
        candidates = re.findall(r"[一-龥A-Za-z0-9《》“”‘’·—\-：:、，。,！!？?（）()]{6,}", raw)
        title = max(candidates, key=len) if candidates else ""
        if href.startswith("/"):
            href = "https://news.southcn.com" + href
        if title and len(title) >= 8:
            item = {"title": title, "url": href}
            if item not in items:
                items.append(item)
        if len(items) >= MAX_ITEMS_PER_SOURCE:
            break
    if len(items) < MAX_ITEMS_PER_SOURCE:
        more = _parse_generic_links(
            html, ("news.southcn.com", "m.nfapp.southcn.com", "www.nandu.com", "www.nandu.com")
        )
        for m in more:
            if all(m["url"] != it["url"] for it in items) and "查看详情" not in m["title"]:
                items.append(m)
            if len(items) >= MAX_ITEMS_PER_SOURCE:
                break
    return items

# —— 国际中文站点解析 ——
def parse_bbc_chinese(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    selectors = [".bbc-15apnto a", ".bbc-uk8dsi a", "a"]
    return _extract_by_selectors(soup, selectors, ("bbc.com/zhongwen", "bbc.co.uk/zhongwen"), MAX_ITEMS_PER_SOURCE) \
        or _parse_generic_links(html, ("bbc.com/zhongwen", "bbc.co.uk/zhongwen"))

def parse_rfi_cn(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    selectors = [".o-list-ui__links a", ".m-item-list a", "a"]
    return _extract_by_selectors(soup, selectors, ("rfi.fr/cn",), MAX_ITEMS_PER_SOURCE) \
        or _parse_generic_links(html, ("rfi.fr/cn",))

def parse_dw_cn(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    selectors = [".news a", "a"]
    return _extract_by_selectors(soup, selectors, ("dw.com/zh", "dw.com/zh-hant"), MAX_ITEMS_PER_SOURCE) \
        or _parse_generic_links(html, ("dw.com/zh", "dw.com/zh-hant"))

# —— 为缺失站点补通用解析（避免 KeyError） ——
def parse_huanqiu_china(html: str) -> list[dict[str, Any]]:
    return _parse_generic_links(html, ("huanqiu.com",))

def parse_caijing(html: str) -> list[dict[str, Any]]:
    return _parse_generic_links(html, ("caijing.com.cn",))

def parse_ifeng(html: str) -> list[dict[str, Any]]:
    return _parse_generic_links(html, ("news.ifeng.com",))

def parse_sohu_news(html: str) -> list[dict[str, Any]]:
    return _parse_generic_links(html, ("news.sohu.com",))

PARSERS = {
    "parse_cctv_index": parse_cctv_index,
    "parse_cctv_china": parse_cctv_china,
    "parse_xinhua": parse_xinhua,
    "parse_net163_domestic": parse_net163_domestic,
    "parse_nandu": parse_nandu,
    "parse_bbc_chinese": parse_bbc_chinese,
    "parse_rfi_cn": parse_rfi_cn,
    "parse_dw_cn": parse_dw_cn,
    "parse_huanqiu_china": parse_huanqiu_china,
    "parse_caijing": parse_caijing,
    "parse_ifeng": parse_ifeng,
    "parse_sohu_news": parse_sohu_news,
}

# ===================== 汇聚与过滤 =====================
def fetch_from_source(name: str, conf: dict[str, str]) -> list[dict[str, Any]]:
    try:
        if "rss" in conf and feedparser:
            try:
                items = fetch_rss(conf["rss"], name)
                if items:
                    return items
            except Exception as e:
                print(f"[警告] {name} RSS 抓取失败：{e}; 尝试 HTML 解析…")
        if "html" in conf and "parser" in conf:
            html = get_html(conf["html"])
            parser = PARSERS[conf["parser"]]
            data = parser(html)
            for d in data:
                d["source"] = name
                d["via"] = "html"
            return data
        return []
    except Exception as e:
        print(f"[警告] 来源 {name} 抓取失败：{e}")
        return []

def is_excluded_title_or_summary(title: str, summary: str = "") -> bool:
    return bool(EXCLUDE_REGEX.search(f"{title} {summary}"))

def dedup_and_group(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def canon_title(t: str) -> str:
        t = re.sub(r"[（(【\[\]^)）】]{1,30}", "", t)
        t = re.sub(r"^\s*(快讯|速览|重磅|独家)\s*[｜\|]\s*", "", t)
        return normalize_space(t)

    buckets: dict[str, dict[str, Any]] = {}
    for it in items:
        key = canon_title(it["title"])
        if key not in buckets:
            buckets[key] = {
                "title": key, "url": it["url"], "summary": it.get("summary", ""),
                "published": it.get("published", ""), "published_parsed": it.get("published_parsed"),
                "sources": set([it.get("source", "")]), "via": set([it.get("via", "")]), "raw": [it]
            }
        else:
            b = buckets[key]
            b["sources"].add(it.get("source", ""))
            b["via"].add(it.get("via", ""))
            if ("news." in it["url"] or "/202" in it["url"]) and "video" not in it["url"]:
                b["url"] = it["url"]
            if it.get("published_parsed") and not b.get("published_parsed"):
                b["published_parsed"] = it["published_parsed"]
                b["published"] = it.get("published", "")
            if it.get("summary") and not b.get("summary"):
                b["summary"] = it["summary"]
            b["raw"].append(it)

    merged: list[dict[str, Any]] = []
    for _, v in buckets.items():
        merged.append({
            "title": v["title"], "url": v["url"], "summary": v.get("summary", ""),
            "published": v.get("published", ""), "published_parsed": v.get("published_parsed"),
            "sources": sorted([s for s in v["sources"] if s]),
            "via_list": sorted([s for s in v["via"] if s]), "raw": v["raw"]
        })
    return merged

# ===================== 时间解析与正文摘要 =====================
def _parse_datetime_str(s: str) -> Optional[dt.datetime]:
    """解析常见日期字符串，返回带时区的 datetime（Asia/Shanghai）。"""
    s = normalize_space(s)

    # 1) 2026-03-21 / 2026/03/21 [10:20[:30]]
    m = re.search(r"(\d{4})\d{1,2}\d{1,2}(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hh, mm, ss = int(m.group(4) or 0), int(m.group(5) or 0), int(m.group(6) or 0)
        try:
            return dt.datetime(y, mo, d, hh, mm, ss, tzinfo=CN_TZ)
        except Exception:
            return None

    # 2) 20260321 [10:20[:30]]
    m = re.search(r"(\d{4})(\d{2})(\d{2})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hh, mm, ss = int(m.group(4) or 0), int(m.group(5) or 0), int(m.group(6) or 0)
        try:
            return dt.datetime(y, mo, d, hh, mm, ss, tzinfo=CN_TZ)
        except Exception:
            return None

    # 3) 2026年3月21日 [10:20[:30]]
    m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日(?:\s*(\d{1,2}):(\d{2})(?::(\d{2}))?)?", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hh, mm, ss = int(m.group(4) or 0), int(m.group(5) or 0), int(m.group(6) or 0)
        try:
            return dt.datetime(y, mo, d, hh, mm, ss, tzinfo=CN_TZ)
        except Exception:
            return None

    return None

def _try_iso_or_rfc(s: str) -> Optional[dt.datetime]:
    s = normalize_space(s)
    try:
        d = parsedate_to_datetime(s)
        if d is not None:
            if d.tzinfo is None:
                d = d.replace(tzinfo=CN_TZ)
            else:
                d = d.astimezone(CN_TZ)
        return d
    except Exception:
        pass
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=CN_TZ)
        else:
            d = d.astimezone(CN_TZ)
        return d
    except Exception:
        return None

def fetch_page_summary_and_time(url: str) -> tuple[str, Optional[str], Optional[tuple], bool]:
    summary = ""
    pub_text = None
    pub_parsed = None
    content_hit_excluded = False
    try:
        html = get_html(url)
        full_text = strip_html(html)
        if EXCLUDE_REGEX.search(full_text):
            content_hit_excluded = True
        pub_text, pub_parsed = extract_publish_time(html, url)
        soup = BeautifulSoup(html, "lxml")
        for sel in [
            'meta[name="description"]', 'meta[name="Description"]',
            'meta[property="og:description"]', 'meta[name="og:description"]'
        ]:
            tag = soup.select_one(sel)
            if tag and tag.get("content"):
                summary = strip_html(tag["content"])[:400]
                break
        if not summary:
            for p in soup.select("article p, .content p, .article p, .main p, p"):
                txt = normalize_space(p.get_text())
                if len(txt) >= 30:
                    summary = txt[:400]
                    break
    except Exception:
        pass
    return (summary, pub_text, pub_parsed, content_hit_excluded)

def extract_publish_time(html: str, url: str) -> tuple[Optional[str], Optional[tuple]]:
    soup = BeautifulSoup(html, "lxml")
    # 常见 meta
    xw_meta = soup.find("meta", attrs={"name": "pubdate"}) or soup.find("meta", attrs={"name": "publishdate"})
    if xw_meta and xw_meta.get("content"):
        raw = normalize_space(xw_meta.get("content"))
        d = _parse_datetime_str(raw) or _try_iso_or_rfc(raw)
        if d:
            return (raw, d.timetuple())
    # 常见标签
    tag = soup.select_one("#pubtime") or soup.select_one(".header-info .time")
    if tag:
        raw = normalize_space(tag.get_text())
        d = _parse_datetime_str(raw)
        if d:
            return (raw, d.timetuple())
    # 163 特例
    if any(x in url for x in ("news.163.com", "www.163.com")):
        ptime = soup.select_one("#ptime") or soup.select_one(".post_time_source") or soup.select_one(".post_info")
        if ptime:
            raw = normalize_space(ptime.get_text())
            d = _parse_datetime_str(raw)
            if d:
                return (raw, d.timetuple())
        meta_ptime = soup.find("meta", attrs={"name": "ptime"})
        if meta_ptime and meta_ptime.get("content"):
            raw = normalize_space(meta_ptime.get("content"))
            d = _parse_datetime_str(raw) or _try_iso_or_rfc(raw)
            if d:
                return (raw, d.timetuple())
    # southcn
    if "southcn.com" in url:
        cand = soup.select_one(".pub-time, .time, .info .time")
        if cand:
            raw = normalize_space(cand.get_text())
            d = _parse_datetime_str(raw) or _try_iso_or_rfc(raw)
            if d:
                return (raw, d.timetuple())
    # 更多 meta 线索
    for tag_name, attrs, key in [
        ("meta", {"property": "article:published_time"}, "content"),
        ("meta", {"name": "publishdate"}, "content"),
        ("meta", {"name": "PubDate"}, "content"),
        ("meta", {"itemprop": "datePublished"}, "content"),
        ("meta", {"name": "date"}, "content"),
        ("meta", {"name": "DC.date.issued"}, "content"),
    ]:
        t = soup.find(tag_name, attrs=attrs)
        if t and t.get(key):
            raw = normalize_space(t.get(key))
            d = _parse_datetime_str(raw) or _try_iso_or_rfc(raw)
            if d:
                return (raw, d.timetuple())
    # ld+json
    for node in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(node.get_text(strip=True))
            nodes = data if isinstance(data, list) else [data]
            for obj in nodes:
                if isinstance(obj, dict) and "datePublished" in obj:
                    raw = normalize_space(str(obj["datePublished"]))
                    d = _parse_datetime_str(raw) or _try_iso_or_rfc(raw)
                    if d:
                        return (raw, d.timetuple())
        except Exception:
            pass
    # <time> 标签
    t = soup.find("time")
    if t:
        cand = t.get("datetime") or t.get_text(strip=True)
        if cand:
            raw = normalize_space(cand)
            d = _parse_datetime_str(raw) or _try_iso_or_rfc(raw)
            if d:
                return (raw, d.timetuple())
    # 全文兜底
    body = soup.get_text(separator=" ", strip=True)
    m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日\s+(\d{1,2}):(\d{2})(?::(\d{2}))?", body) \
        or re.search(r"(\d{4})(\d{2})(\d{2})", body)
    if m:
        raw = m.group(0)
        d = _parse_datetime_str(raw) or _try_iso_or_rfc(raw)
        if d:
            return (raw, d.timetuple())
    # URL 兜底
    m = re.search(r"(\d{4})(\d{2})(\d{2})", url) or re.search(r"(\d{4})\d{2}\d{2}", url)
    if m:
        try:
            dtt = dt.datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=CN_TZ)
            return (None, dtt.timetuple())
        except Exception:
            pass
    return (None, None)

# ===================== 排序与导出 =====================
def score_item(it: dict[str, Any]) -> float:
    score = 0.0
    title = it.get("title", "")
    txt = f"{title} {it.get('summary_final') or it.get('summary', '')}"
    src_count = len(it.get("sources", []))
    score += 6 if src_count >= 3 else 4 if src_count == 2 else 1 if src_count == 1 else 0
    score += min(sum(SOURCE_WEIGHT.get(s, 1) for s in it.get("sources", [])), 10)
    for k, w in HOT_KEYWORDS.items():
        if k in txt:
            score += w
    if re.search(r"\d", title):
        score += 1
    if any(c in title for c in CITY_KEYWORDS):
        score += 1
    if 10 <= len(title) <= 30:
        score += 1
    pp = it.get("published_parsed")
    if pp:
        try:
            pub_dt = dt.datetime(*pp[:6])
            if not pub_dt.tzinfo:
                pub_dt = pub_dt.replace(tzinfo=CN_TZ)
            else:
                pub_dt = pub_dt.astimezone(CN_TZ)
            diff_h = (dt.datetime.now(CN_TZ) - pub_dt).total_seconds() / 3600
            score += 3 if diff_h < 3 else 2 if diff_h < 8 else 1 if diff_h < 24 else 0
        except Exception:
            pass
    return score

def order_by_time_desc(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def keyfunc(it: dict[str, Any]):
        pp = it.get("published_parsed")
        if not pp:
            m = re.search(r"(\d{4})(\d{2})(\d{2})", it.get("url", "")) or re.search(r"(\d{4})\d{2}\d{2}", it.get("url", ""))
            if m:
                try:
                    return dt.datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=CN_TZ)
                except Exception:
                    return dt.datetime.min.replace(tzinfo=CN_TZ)
            return dt.datetime.min.replace(tzinfo=CN_TZ)
        try:
            d = dt.datetime(*pp[:6])
            if not d.tzinfo:
                d = d.replace(tzinfo=CN_TZ)
            else:
                d = d.astimezone(CN_TZ)
            return d
        except Exception:
            return dt.datetime.min.replace(tzinfo=CN_TZ)
    return sorted(items, key=keyfunc, reverse=True)

def rank_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=score_item, reverse=True)

def is_international(it: dict[str, Any]) -> bool:
    txt = (it.get("title", "") + " " + it.get("summary_final", "") + " " + it.get("summary", ""))
    intl_hit = any(k in txt for k in INTERNATIONAL_KEYWORDS)
    local_block = any(k in txt for k in ["中国", "全国", "广东", "广州", "深圳", "珠三角", "两会"])
    return bool(intl_hit and not local_block)

def intl_first_merge_with_topk(base_sorted: list[dict[str, Any]], top_k: int = 5) -> list[dict[str, Any]]:
    intl = [it for it in base_sorted if is_international(it)]
    other = [it for it in base_sorted if not is_international(it)]
    head = intl[:top_k]
    if len(head) < top_k:
        print(f"[提示] 国际热点仅 {len(head)} 条，未达到指定的 {top_k} 条，将尽量置顶。")
    return head + intl[top_k:] + other

def fmt_pub_time(it: dict[str, Any]) -> str:
    if it.get("published_parsed"):
        try:
            d = dt.datetime(*it["published_parsed"][:6])
            if not d.tzinfo:
                d = d.replace(tzinfo=CN_TZ)
            else:
                d = d.astimezone(CN_TZ)
            return d.strftime("%Y-%m-%d %H:%M")
        except Exception:
            return it.get("published", "")
    return it.get("published", "")

def save_to_html(items: list[dict[str, Any]], out_fullpath: str, order_label: str, max_age_days: int, intl_first: bool):
    now = dt.datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M")
    css = """
:root{--fg:#222;--muted:#666;--link:#0969da;--bg:#fff;--card:#f8f9fa}
*{box-sizing:border-box}
body{margin:0;padding:16px 16px;color:var(--fg);background:var(--bg);font:16px/1.6 system-ui,-apple-system,Segoe UI,Roboto,Arial,"Microsoft Yahei",sans-serif}
.wrap{max-width:880px;margin:0 auto}
.card{background:var(--card);border:1px solid #e5e7eb;border-radius:14px;padding:14px 16px;margin:10px 0}
.quickcard{position:relative;border:2px solid #334155;background:#f3f4f6;margin-top:-6px}
.quickcard-title{position:absolute; top:-14px; left:50%; transform:translateX(-50%); background:#fff; padding:3px 10px; border:1px solid #cbd5e1; border-radius:6px; font-size:.85rem; color:#111827; box-shadow:0 1px 1px rgba(0,0,0,.04)}
nav.quicklinks{display:flex; gap:28px; justify-content:space-around; align-items:center; flex-wrap:wrap; min-height:36px; padding:6px 4px; font-size:.95rem}
nav.quicklinks a{color:#0b66d6;text-decoration:none}
nav.quicklinks a:hover{text-decoration:underline}
header h1{margin:8px 0 6px 0; font-size:1.6rem; text-align:center}
header .ts{color:#666;font-size:.9rem;margin-bottom:10px;text-align:center}
.idx{display:inline-block;width:36px;color:#888}
.title a{color:var(--link);text-decoration:none}
.title a:hover{text-decoration:underline}
.meta{color:#666;font-size:.9rem;margin-top:4px}
.sum{margin-top:6px}
footer{color:#666;font-size:.85rem;margin-top:28px}
    """.strip()

    # === Quicklinks（标准 <a>）。如需完全隐藏，把 show_quicklinks=False 即可 ===
    show_quicklinks = True
    quicklinks = [
        ("https://wap.weather.com.cn/mweather/", "天气预报"),
        ("https://www.ip138.com/ditie/", "城市地铁"),
        ("https://m.hao268.com/", "网址之家"),
        ("https://wap.baidu.com/", "百度"),
    ]
    quicklinks_html = "".join(
        f'{htmllib.escape(h)}{htmllib.escape(t)}</a>'
        for h, t in quicklinks
    )

    head_parts = [
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"/>',
        '<meta name="viewport" content="width=device-width,initial-scale=1"/>',
        '<meta http-equiv="refresh" content="3600">',
        '<meta name="description" content="今日热点新闻（国际优先/时间排序，权威多源聚合）。" />',
        '<title>今日热点新闻</title><style>', css, '</style></head><body>',
        '<div class="wrap"><header>'
    ]
    if show_quicklinks:
        head_parts += [
            '<div class="card quickcard">',
            '<div class="quickcard-title">便捷小链接</div>',
            f'<nav class="quicklinks">{quicklinks_html}</nav></div>'
        ]
    head_parts += [
        '<h1>今日热点新闻</h1>',
        f'<div class="ts">生成时间：{now}</div>',
        '</header><section>'
    ]
    head = "".join(head_parts)  # 已移除“排序/时间范围/国际优先”行

    rows: list[str] = []
    for i, it in enumerate(items, 1):
        title = htmllib.escape(it.get("title", ""))
        url = htmllib.escape(it.get("url", ""))
        srcs = "、".join(it.get("sources", [])) or "未知"
        pub = it.get("published") or fmt_pub_time(it) or "—"
        summary = htmllib.escape(it.get("summary_final", "") or it.get("summary", "") or it.get("title", ""))

        row_html = (
            f"<article class='card'>"
            f"<div class='title'><span class='idx'>{i:02d}.</span>"
            f"<a href=\"{url}\" target=\"_blank\" rel=\"noopener\">{title}</a></div>"
            f"<div class='meta'>来源：{htmllib.escape(srcs)}；日期：{htmllib.escape(pub)}</div>"
            f"<div class='sum'>摘要：{summary}</div>"
            f"</article>"
        )
        rows.append(row_html)

    tail = (
        "</section><footer>本站每小时自动刷新一次；仅做信息聚合与索引，内容以源站为准。"
        " © 2026 Yingfeng Su. All rights reserved.</footer></div></body></html>"
    )

    with open(out_fullpath, "w", encoding="utf-8") as f:
        f.write(head + "".join(rows) + tail)

# ===================== 数据源（含国际源） =====================
SOURCES: list[tuple[str, dict[str, str]]] = [
    # 国际
    ("BBC 中文", {
        "rss": "https://www.bbc.co.uk/zhongwen/simp/index.xml",
        "html": "https://www.bbc.com/zhongwen/simp",
        "parser": "parse_bbc_chinese",
    }),
    ("路透中文", {"rss": "https://feedx.net/rss/reuters.xml"}),
    ("法广 RFI 中文", {
        "rss": "https://feedx.net/rss/rfi.xml",
        "html": "https://www.rfi.fr/cn/",
        "parser": "parse_rfi_cn",
    }),
    ("德国之声 中文", {
        "rss": "https://feedx.net/rss/dw.xml",
        "html": "https://www.dw.com/zh/",
        "parser": "parse_dw_cn",
    }),
    ("CNN World", {"rss": "http://rss.cnn.com/rss/cnn_world.rss"}),

    # 国内
    ("中新网-即时", {"rss": "https://www.chinanews.com.cn/rss/scroll-news.xml"}),
    ("中新网-要闻", {"rss": "https://www.chinanews.com.cn/rss/importnews.xml"}),
    ("中新网-国内", {"rss": "https://www.chinanews.com.cn/rss/china.xml"}),
    ("中新网-社会", {"rss": "https://www.chinanews.com.cn/rss/society.xml"}),
    ("央视网-新闻频道", {"html": "https://news.cctv.cn/", "parser": "parse_cctv_index"}),
    ("央视网-国内新闻", {"html": "https://news.cctv.com/china/", "parser": "parse_cctv_china"}),
    ("新华网-首页", {"html": "https://www.news.cn/", "parser": "parse_xinhua"}),
    ("环球网-国内", {"html": "https://www.huanqiu.com/", "parser": "parse_huanqiu_china"}),
    ("网易新闻-国内", {"html": "https://news.163.com/domestic/", "parser": "parse_net163_domestic"}),
    ("南方都市报", {"html": "https://news.southcn.com/node_17a07e5926/?cms_node_post_list_page=1", "parser": "parse_nandu"}),
    ("财经网", {"html": "https://finance.caijing.com.cn/", "parser": "parse_caijing"}),
    ("凤凰网", {"html": "https://news.ifeng.com/", "parser": "parse_ifeng"}),
    ("搜狐新闻", {"rss": "https://rss.news.sohu.com/rss/guonei.xml", "html": "https://news.sohu.com/", "parser": "parse_sohu_news"}),
]

# ===================== 主流程 =====================
def attach_summaries(items: list[dict[str, Any]], max_age_days: int = 31, drop_no_time: bool = False) -> None:
    now_year = dt.datetime.now(CN_TZ).year
    keep: list[dict[str, Any]] = []
    for it in items:
        if is_excluded_title_or_summary(it.get("title", ""), it.get("summary", "")):
            continue

        s = strip_html(it.get("summary", "")) if it.get("summary") else ""
        pub_txt = it.get("published", "")
        pub_parsed = it.get("published_parsed")

        s2, t2, p2, content_hit_excluded = fetch_page_summary_and_time(it["url"])
        if content_hit_excluded:
            continue

        if not s and s2:
            s = s2
        if p2:
            pub_txt = t2 or pub_txt
            pub_parsed = p2

        it["summary_final"] = (s or it["title"])[:160]
        if pub_parsed:
            it["published_parsed"] = pub_parsed
        if pub_parsed and not pub_txt:
            try:
                dval = dt.datetime(*pub_parsed[:6])
                if not dval.tzinfo:
                    dval = dval.replace(tzinfo=CN_TZ)
                else:
                    dval = dval.astimezone(CN_TZ)
                pub_txt = dval.strftime("%Y-%m-%d %H:%M")
            except Exception:
                pass
        if pub_txt:
            it["published"] = pub_txt

        if drop_no_time and not it.get("published_parsed"):
            continue

        # 粗筛年份与最大天数
        y = None
        if it.get("published_parsed"):
            try:
                y = dt.datetime(*it["published_parsed"][:3]).year
            except Exception:
                y = None
        if y is None:
            m = re.search(r"(\d{4})", it.get("url", ""))
            y = int(m.group(1)) if m else None
        if y and y < now_year - 2:
            continue

        if it.get("published_parsed") and max_age_days and max_age_days > 0:
            try:
                pub_dt = dt.datetime(*it["published_parsed"][:6])
                if not pub_dt.tzinfo:
                    pub_dt = pub_dt.replace(tzinfo=CN_TZ)
                else:
                    pub_dt = pub_dt.astimezone(CN_TZ)
                if (dt.datetime.now(CN_TZ) - pub_dt).days > max_age_days:
                    continue
            except Exception:
                pass

        keep.append(it)

    items.clear()
    items.extend(keep)

def main():
    parser = argparse.ArgumentParser(description="CN Hot News Ranker — fixed4 (intl + UI + compat, no typing.List)")
    # —— 兼容旧版 CI（无实际作用）——
    parser.add_argument("--no-txt", action="store_true", help="兼容旧版参数（无效果）")
    parser.add_argument("--no-docx", action="store_true", help="兼容旧版参数（无效果）")
    # —— 正常参数 ——
    parser.add_argument("--html", default=None)
    parser.add_argument("--outdir", default=OUTPUT_DIR)
    parser.add_argument("--proxy", default=None)
    parser.add_argument("--order", choices=["time", "score"], default="time")
    parser.add_argument("--max-age-days", type=int, default=31, help="仅保留最近N天内的新闻；设为0不限制")
    parser.add_argument("--drop-no-time", action="store_true", help="丢弃无法解析出发布时间的条目")
    parser.add_argument("--intl-first", action="store_true", default=True, help="国际热点置顶显示（默认开启）")
    parser.add_argument("--top-intl", type=int, default=5, help="保证置顶的国际热点条数（默认5）")
    args = parser.parse_args()

    if args.proxy:
        set_proxy(args.proxy)
    os.makedirs(args.outdir, exist_ok=True)

    print("=== 中国热点新闻（HTML版）fixed4 国际源增强 (兼容旧参数，UI 修复) 运行 ===")
    print("输出目录：", args.outdir)
    if _session.proxies:
        print("代理：", _session.proxies)

    raw: list[dict[str, Any]] = []
    failed: list[str] = []

    for name, conf in SOURCES:
        time.sleep(SLEEP_BETWEEN)
        print(f"[抓取] {name} … ", end="")
        data = fetch_from_source(name, conf)
        if not data:
            failed.append(name)
        before = len(data)
        data = [d for d in data if not is_excluded_title_or_summary(d.get("title", ""), d.get("summary", ""))]
        after = len(data)
        print(f"获取 {before}，标题/摘要过滤后 {after}")
        raw.extend(data)

    print(f"[汇总] 原始合计 {len(raw)} 条；去重合并 …")
    merged = dedup_and_group(raw)

    print("[摘要] 生成摘要 + 补齐时间 + 正文过滤 …")
    attach_summaries(merged, max_age_days=args.max_age_days, drop_no_time=args.drop_no_time)

    print(f"[排序] 模式：{args.order} …")
    base_sorted = order_by_time_desc(merged) if args.order == "time" else rank_items(merged)

    ordered = intl_first_merge_with_topk(base_sorted, top_k=args.top_intl) if args.intl_first else base_sorted
    topn = ordered[:TOP_N]

    html_path = args.html or os.path.join(args.outdir, "index.html")
    save_to_html(topn, html_path, "时间（新→旧）" if args.order == "time" else "热度评分", args.max_age_days, args.intl_first)
    print("[完成] HTML:", html_path)
    if failed:
        print("[提示] 以下来源本次未获取到数据：", "、".join(failed))

if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception as e:
        log_path = os.path.join(OUTPUT_DIR, "error.log")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{dt.datetime.now(CN_TZ)}] {e}{traceback.format_exc()}")
        print("[异常] 运行出错，已写入日志：", log_path)
# -*- coding: utf-8 -*-
"""
CN Hot News Ranker — fixed4（国际源增强 + UI 修复 + CI 兼容，去除 List/Dict 注解）

- 聚合国内 + 国际新闻源（RSS + HTML 兜底），生成 index.html
- 标题/摘要/正文命中 “习近平/总书记/中共中央 …” 一律过滤
- 保证前 N 条（默认 5 条）为国际热点
- UI：标题以可点击文字呈现（不再裸露 URL）；删除“排序/时间范围/国际优先”行；Quicklinks 为标准 <a>
- CI 兼容：--no-txt / --no-docx 两个无效果参数
"""

import os
import re
import time
import argparse
import html as htmllib
import json
import datetime as dt
from email.utils import parsedate_to_datetime
from typing import Any, Optional  # 仅保留 Any/Optional，注解全部用内建 list/dict/tuple

import requests
from bs4 import BeautifulSoup

try:
    import feedparser
except Exception:
    feedparser = None

# ===================== 常量与全局 =====================
CN_TZ = dt.timezone(dt.timedelta(hours=8), name='Asia/Shanghai')
MAX_ITEMS_PER_SOURCE = 6
TOP_N = 40
SLEEP_BETWEEN = 0.5
TIMEOUT = 10
RETRY = 3

# —— 过滤关键词（标题 + 正文均生效）——
EXCLUDE_KEYWORDS = [
    "习近平", "总书记", "国家主席", "中共中央", "中央委员会",
    "中央政府", "中央统战部", "国家领导人",
]
EXCLUDE_REGEX = re.compile("|".join(map(re.escape, EXCLUDE_KEYWORDS)), re.IGNORECASE)

HOT_KEYWORDS = {
    "突发": 3, "通报": 2, "最新": 2, "预警": 2, "发布": 2, "春运": 1,
    "消费": 2, "房产": 1, "楼市": 2, "经济": 2, "事故": 3, "暴雪": 2,
    "寒潮": 1, "高铁": 2, "医保": 2, "大模型": 2, "AI": 3, "新能源": 2,
    "锂电": 2, "芯片": 2, "文旅": 1, "免税": 1, "通行": 1, "航班": 1
}
CITY_KEYWORDS = ["广州","深圳","北京","上海","杭州","南京","天津","重庆","武汉","西安"]

# —— 国际热点关键词（用于置顶规则）——
INTERNATIONAL_KEYWORDS = [
    '美国','英国','法国','德国','意大利','西班牙','欧盟','欧洲','俄罗斯','乌克兰','波兰','白俄罗斯','立陶宛','拉脱维亚','爱沙尼亚',
    '中东','以色列','加沙','巴勒斯坦','黎巴嫩','叙利亚','伊拉克','伊朗','也门','霍尔木兹','红海','胡塞',
    '阿联酋','沙特','卡塔尔','土耳其','埃及','约旦','阿曼','巴林',
    '印度','巴基斯坦','孟加拉','斯里兰卡','尼泊尔',
    '日本','韩国','朝鲜','菲律宾','越南','老挝','柬埔寨','泰国','马来西亚','新加坡','印尼','澳大利亚','新西兰',
    '加拿大','墨西哥','巴西','阿根廷','智利','秘鲁','哥伦比亚',
]

# 来源权重（用于热度评分）
SOURCE_WEIGHT = {
    "BBC 中文": 6, "路透中文": 6, "法广 RFI 中文": 6, "德国之声 中文": 5, "CNN World": 5,
    "央视网-新闻频道": 4, "央视网-国内新闻": 2, "新华网-首页": 1,
    "中新网-即时": 4, "中新网-要闻": 1, "中新网-国内": 2, "中新网-社会": 1,
    "环球网-国内": 0, "网易新闻-国内": 1,
    "南方都市报": 2, "财经网": 3, "凤凰网": 5, "搜狐新闻": 2,
}

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:122.0) Gecko/20100101 Firefox/122.0",
]

def _default_outdir() -> str:
    env = os.environ.get("HOTNEWS_OUTDIR")
    if env:
        return env
    home = os.path.expanduser("~")
    return os.path.join(home, "Hot_Points")

OUTPUT_DIR = _default_outdir()
os.makedirs(OUTPUT_DIR, exist_ok=True)

_session = requests.Session()
_session.headers.update({"User-Agent": UA_POOL[0]})
for key in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"]:
    if key in os.environ:
        _session.proxies = {
            "http": os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy"),
            "https": os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy"),
        }
        break

# ===================== 工具函数 =====================
def set_proxy(proxy: Optional[str]):
    _session.proxies = {"http": proxy, "https": proxy} if proxy else {}

def normalize_space(s: Optional[str]) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

def safe_url(u: str) -> str:
    if not u:
        return u
    u = u.replace(" ", "")
    if u.startswith("//"):
        u = "https:" + u
    return u

def strip_html(raw: str) -> str:
    if not raw:
        return ""
    try:
        soup = BeautifulSoup(raw, "lxml")
        for tag in soup(["script", "style"]):
            tag.extract()
        return normalize_space(soup.get_text(separator=" "))
    except Exception:
        t = re.sub(r"<script.*?>.*?</script>", "", raw, flags=re.S | re.I)
        t = re.sub(r"<style.*?>.*?</style>", "", t, flags=re.S | re.I)
        t = re.sub(r"<[^>]+>", "", t)
        return normalize_space(t)

UA_INDEX = 0
def _rotate_ua():
    global UA_INDEX
    UA_INDEX = (UA_INDEX + 1) % len(UA_POOL)
    _session.headers.update({"User-Agent": UA_POOL[UA_INDEX]})

def get_html(url: str) -> str:
    last = None
    delay = 0.6
