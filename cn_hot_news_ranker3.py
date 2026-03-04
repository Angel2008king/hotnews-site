#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CN Hot News Ranker (one-file version)  —— 仅 HTML 输出

变更要点（本版相对你原始文件的修改）：
1) 仅显示源站原样时间：
   - extract_publish_time() 现在返回 (published_text, published_parsed, published_raw)
   - fetch_page_summary_and_time() 同步返回 published_raw
   - attach_summaries() 强制以正文页时间覆盖显示值，并保存 published_raw
   - save_to_html() 渲染优先显示 it['published_raw']，抓不到才回退到规范化值

2) 排序/打分仍使用规范化后的时间（struct_time），不受影响。

Copyright © 2026
"""

import os, re, time, argparse, html as htmllib, json
import datetime as dt
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import List, Dict, Tuple, Optional, Any

# 依赖
import requests
from bs4 import BeautifulSoup
try:
    import feedparser  # 可选：若未安装，脚本会降级跳过 RSS
except Exception:
    feedparser = None

# ---------- 常量 & 基础工具 ----------
CN_TZ = dt.timezone(dt.timedelta(hours=8), name='Asia/Shanghai')  # 统一北京时间
MAX_ITEMS_PER_SOURCE = 5
TOP_N = 38
SLEEP_BETWEEN = 0.5
TIMEOUT = 10
RETRY = 3

# 过滤关键词（可按需调整）
EXCLUDE_KEYWORDS = [
    "习近平","总书记","国家主席","中共中央","中央委员会",
    "中央政府","中央统战部","国家领导人"
]
EXCLUDE_REGEX = re.compile("|".join(map(re.escape, EXCLUDE_KEYWORDS)), re.IGNORECASE)

# 热点加权关键词
HOT_KEYWORDS = {
    "突发": 3, "通报": 2, "最新": 2, "预警": 2, "发布": 1, "春运": 3,
    "消费": 2, "房产": 2, "楼市": 2, "经济": 2, "事故": 3, "暴雪": 2,
    "寒潮": 2, "高铁": 2, "医保": 2, "大模型": 2, "AI": 1, "新能源": 2,
    "锂电": 2, "芯片": 2, "文旅": 2, "免税": 1, "通行": 1, "航班": 1
}
CITY_KEYWORDS = ["广州","深圳","北京","上海","杭州","南京","天津","重庆","武汉","西安"]

# 来源权重
SOURCE_WEIGHT = {
    "央视网-新闻频道": 4, "央视网-国内新闻": 4, "新华网-首页": 4,
    "中新网-即时": 4, "中新网-要闻": 4, "中新网-国内": 4, "中新网-社会": 3,
    "环球网-国内": 2, "网易新闻-国内": 2,
    "南方都市报": 2, "澎湃新闻": 3, "财经网": 3, "凤凰网": 2, "搜狐新闻": 2,
}

def _default_outdir() -> str:
    env = os.environ.get("HOTNEWS_OUTDIR")
    if env:
        return env
    home = os.path.expanduser("~")
    if os.name == "nt":
        return os.path.join(os.environ.get("USERPROFILE", home), "Documents", "Hot_Points")
    else:
        return os.path.join(home, "Hot_Points")

OUTPUT_DIR = _default_outdir()
os.makedirs(OUTPUT_DIR, exist_ok=True)

_session = requests.Session()
UA_POOL = [
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
     "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
     "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"),
    ("Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:122.0) Gecko/20100101 Firefox/122.0"),
]
_session.headers.update({"User-Agent": UA_POOL[0]})

for key in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"]:
    if key in os.environ:
        _session.proxies = {
            "http": os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy"),
            "https": os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy"),
        }
        break

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
        t = re.sub(r"<script.*?>.*?</script>|<style.*?>.*?</style>", "", raw, flags=re.S|re.I)
        t = re.sub(r"<[^>]+>", "", t)
        return normalize_space(t)

def smart_trim(s: str, max_len: int = 160) -> str:
    if not s or len(s) <= max_len:
        return s
    cut = s[: max_len + 10]
    m = re.search(r"[。！？；.!?;]\s*\S*$", cut)
    return cut[: m.start() + 1] if m else cut[: max_len].rstrip("，,;；、.。") + "…"

UA_INDEX = 0
def _rotate_ua():
    global UA_INDEX
    UA_INDEX = (UA_INDEX + 1) % len(UA_POOL)
    _session.headers.update({"User-Agent": UA_POOL[UA_INDEX]})

def get_html(url: str) -> str:
    last = None
    delay = 0.6
    for _ in range(RETRY + 1):
        try:
            resp = _session.get(url, timeout=TIMEOUT, allow_redirects=True)
            status = resp.status_code
            if 200 <= status < 300:
                resp.encoding = resp.apparent_encoding or resp.encoding or "utf-8"
                return resp.text
            elif status in (301, 302, 303, 307, 308):
                last = Exception(f"Redirect {status}")
            elif status in (401, 403):
                _rotate_ua(); last = Exception(f"Forbidden {status}")
            elif status in (404,):
                raise RuntimeError(f"404 Not Found: {url}")
            elif status in (429,):
                last = Exception("429 Too Many Requests")
            elif 500 <= status < 600:
                last = Exception(f"Server error {status}")
            else:
                last = Exception(f"HTTP {status}")
        except Exception as e:
            last = e
        time.sleep(delay)
        delay = min(delay * 1.8, 6.0)
    raise RuntimeError(f"请求失败：{url} 错误：{last}")

# ---------- RSS 与 HTML 解析 ----------
def fetch_rss(feed_url: str, source_name: str) -> List[Dict[str, Any]]:
    if not feedparser:
        return []
    d = feedparser.parse(feed_url)
    items = []
    for e in d.entries[:MAX_ITEMS_PER_SOURCE]:
        title = normalize_space(getattr(e, 'title', ''))
        link = safe_url(getattr(e, 'link', '') or getattr(e, 'id', ''))
        if not title or not link:
            continue
        summary = strip_html(getattr(e, 'summary', ''))
        # RSS 的 published_parsed 默认视为 UTC；最终渲染时统一转为北京时间
        published = normalize_space(getattr(e, 'published', '')) or normalize_space(getattr(e, 'updated', ''))
        pub_parsed = getattr(e, 'published_parsed', None) or getattr(e, 'updated_parsed', None)
        items.append({
            "title": title, "url": link, "summary": summary,
            "published": published, "published_parsed": pub_parsed,
            "source": source_name, "via": "rss"
        })
    return items

def _parse_generic_links(html: str, domain_allow: Tuple[str, ...], min_title_len: int = 6) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html, 'lxml')
    items: List[Dict[str, Any]] = []
    for a in soup.select('a'):
        title = normalize_space(a.get_text())
        href = safe_url(a.get('href') or '')
        if not title or not href or not href.startswith('http'):
            continue
        if len(title) < min_title_len:
            continue
        if any(seg in href for seg in ('#', 'javascript:')):
            continue
        if domain_allow and not any(href.startswith(d) or d in href for d in domain_allow):
            continue
        items.append({'title': title, 'url': href})
        if len(items) >= MAX_ITEMS_PER_SOURCE:
            break
    return items

def _extract_by_selectors(soup: BeautifulSoup, selectors: list, domain_allow: tuple, limit: int) -> list:
    items, seen = [], set()
    for sel in selectors:
        for a in soup.select(sel):
            title = normalize_space(a.get_text())
            href = safe_url(a.get('href') or '')
            if not title or not href or not href.startswith('http'):
                continue
            if any(seg in href for seg in ('#', 'javascript:')):
                continue
            if domain_allow and not any(href.startswith(d) or d in href for d in domain_allow):
                continue
            key = (title, href)
            if key in seen or len(title) < 6:
                continue
            seen.add(key)
            items.append({'title': title, 'url': href})
            if len(items) >= limit:
                return items
        if len(items) >= limit:
            break
    return items

# 站点解析函数（精确 + 回退）
def parse_cctv_index(html: str) -> list:
    soup = BeautifulSoup(html, 'lxml')
    selectors = ['.roll_yw a', '.newslist a', '.title_list a', 'section a']
    items = _extract_by_selectors(soup, selectors, ('https://news.cctv.cn/', 'https://news.cctv.com/'), MAX_ITEMS_PER_SOURCE)
    return items or _parse_generic_links(html, ('https://news.cctv.cn/','https://news.cctv.com/'))

def parse_cctv_china(html: str) -> list:
    soup = BeautifulSoup(html, 'lxml')
    selectors = ['#newslist a', '.brecommend a', '.tuwen a', 'section a']
    items = _extract_by_selectors(soup, selectors, ('https://news.cctv.com/china/','https://news.cctv.com/'), MAX_ITEMS_PER_SOURCE)
    return items or _parse_generic_links(html, ('https://news.cctv.com/china/','https://news.cctv.com/'))

def parse_xinhua(html: str) -> list:
    soup = BeautifulSoup(html, 'lxml')
    selectors = ['section a', '.news a', '.data a', '.headline a', '.list a']
    items = _extract_by_selectors(soup, selectors, ('https://www.news.cn/',), MAX_ITEMS_PER_SOURCE)
    return items or _parse_generic_links(html, ('https://www.news.cn/',))

# 环球网
def parse_huanqiu_china(html: str) -> list:
    soup = BeautifulSoup(html, 'lxml')
    selectors = [
        'a[href*="/article/"]',
        'section[class*="list"] a', '.list a', '.item a', '.content a'
    ]
    items = _extract_by_selectors(
        soup, selectors,
        ('https://www.huanqiu.com/', 'https://china.huanqiu.com/'),
        MAX_ITEMS_PER_SOURCE
    )
    return items or _parse_generic_links(html, ('https://www.huanqiu.com/','https://china.huanqiu.com/'))

def parse_net163_domestic(html: str) -> list:
    soup = BeautifulSoup(html, 'lxml')
    selectors = ['#js_D_list a', '.news_default_list a', '.post_item a', 'section a']
    items = _extract_by_selectors(soup, selectors, ('https://news.163.com/', 'https://www.163.com/'), MAX_ITEMS_PER_SOURCE)
    return items or _parse_generic_links(html, ('https://news.163.com/','https://www.163.com/'))

# 南方都市报 → 南方网·南都列表
def parse_nandu(html: str) -> list:
    soup = BeautifulSoup(html, 'lxml')
    selectors = [
        '.newslist a',
        'section a', '.list a', 'a[href*="/content/"]'
    ]
    items = _extract_by_selectors(
        soup, selectors,
        ('https://news.southcn.com/', 'https://www.nandu.com/', 'http://www.nandu.com/', 'https://m.nfapp.southcn.com/'),
        MAX_ITEMS_PER_SOURCE
    )
    return items or _parse_generic_links(html, ('https://news.southcn.com/','https://www.nandu.com/','http://www.nandu.com/','https://m.nfapp.southcn.com/'))

def parse_thepaper(html: str) -> list:
    soup = BeautifulSoup(html, 'lxml')
    selectors = ['.news_li a', '.news__item a', '.index__news a', 'section a']
    items = _extract_by_selectors(soup, selectors, ('https://www.thepaper.cn/', 'https://m.thepaper.cn/'), MAX_ITEMS_PER_SOURCE)
    return items or _parse_generic_links(html, ('https://www.thepaper.cn/','https://m.thepaper.cn/'))

# 财经网
def parse_caijing(html: str) -> list:
    soup = BeautifulSoup(html, 'lxml')
    selectors = [
        'a[href^="https://finance.caijing.com.cn/"]',
        '.news-list a', '.article-list a', '.list a', 'section a'
    ]
    items = _extract_by_selectors(
        soup, selectors,
        ('https://www.caijing.com.cn/', 'https://m.caijing.com.cn/', 'https://finance.caijing.com.cn/', 'https://magazine.caijing.com.cn/'),
        MAX_ITEMS_PER_SOURCE
    )
    return items or _parse_generic_links(html, ('https://finance.caijing.com.cn/','https://www.caijing.com.cn/','https://m.caijing.com.cn/','https://magazine.caijing.com.cn/'))

def parse_ifeng(html: str) -> list:
    soup = BeautifulSoup(html, 'lxml')
    selectors = ['.newsList a', '.item a', '.channel_list a', 'section a']
    items = _extract_by_selectors(soup, selectors, ('https://news.ifeng.com/', 'https://www.ifeng.com/'), MAX_ITEMS_PER_SOURCE)
    return items or _parse_generic_links(html, ('https://news.ifeng.com/','https://www.ifeng.com/'))

def parse_sohu_news(html: str) -> list:
    soup = BeautifulSoup(html, 'lxml')
    selectors = ['#newslist a', '.data-news a', '.feed-card a', 'section a']
    items = _extract_by_selectors(soup, selectors, ('https://news.sohu.com/',), MAX_ITEMS_PER_SOURCE)
    return items or _parse_generic_links(html, ('https://news.sohu.com/',))

PARSERS = {
    'parse_cctv_index': parse_cctv_index,
    'parse_cctv_china': parse_cctv_china,
    'parse_xinhua': parse_xinhua,
    'parse_huanqiu_china': parse_huanqiu_china,
    'parse_net163_domestic': parse_net163_domestic,
    'parse_nandu': parse_nandu,
    'parse_thepaper': parse_thepaper,
    'parse_caijing': parse_caijing,
    'parse_ifeng': parse_ifeng,
    'parse_sohu_news': parse_sohu_news,
}

def fetch_from_source(name: str, conf: Dict[str, str]) -> List[Dict[str, Any]]:
    try:
        if 'rss' in conf and feedparser:
            try:
                items = fetch_rss(conf['rss'], name)
                if items:
                    return items
            except Exception as e:
                print(f"[警告] {name} RSS 抓取失败：{e}; 尝试 HTML 解析…")

        if 'html' in conf and 'parser' in conf:
            html = get_html(conf['html'])
            parser = PARSERS[conf['parser']]
            data = parser(html)
            for d in data:
                d['source'] = name
                d['via'] = 'html'
            return data
        return []
    except Exception as e:
        print(f"[警告] 来源 {name} 抓取失败：{e}")
        return []

def is_excluded(title: str, summary: str = '') -> bool:
    return bool(EXCLUDE_REGEX.search(f"{title} {summary}"))

def dedup_and_group(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    def canon_title(t: str) -> str:
        t = re.sub(r"[（(【\[\][^）)】\]]{1,30}[）)】\]]", "", t)  # 去括号内副标题
        t = re.sub(r"^\s*(快讯|速览|重磅|独家)\s*[｜|]\s*", "", t)  # 去前缀标识
        return normalize_space(t)

    buckets: Dict[str, Dict[str, Any]] = {}
    for it in items:
        key = canon_title(it['title'])
        if key not in buckets:
            buckets[key] = {
                'title': key, 'url': it['url'], 'summary': it.get('summary', ''),
                'published': it.get('published', ''), 'published_parsed': it.get('published_parsed'),
                'sources': set([it.get('source', '')]), 'via': set([it.get('via', '')]), 'raw': [it]
            }
        else:
            b = buckets[key]
            b['sources'].add(it.get('source', ''))
            b['via'].add(it.get('via', ''))
            if ('news.' in it['url'] or '/202' in it['url']) and 'video' not in it['url']:
                b['url'] = it['url']  # 更像“新闻”的链接优先
            if it.get('published_parsed') and not b.get('published_parsed'):
                b['published_parsed'] = it['published_parsed']
                b['published'] = it.get('published', '')
            if it.get('summary') and not b.get('summary'):
                b['summary'] = it['summary']
            b['raw'].append(it)

    merged: List[Dict[str, Any]] = []
    for _, v in buckets.items():
        merged.append({
            'title': v['title'], 'url': v['url'], 'summary': v.get('summary', ''),
            'published': v.get('published', ''), 'published_parsed': v.get('published_parsed'),
            'sources': sorted([s for s in v['sources'] if s]),
            'via_list': sorted([s for s in v['via'] if s]), 'raw': v['raw']
        })
    return merged

# ===== 规范化时间字符串 -> datetime（无时区→北京时间；有时区→换算到北京时间） =====
def _parse_datetime_str(s: str) -> Optional[dt.datetime]:
    s = normalize_space(s)

    # 1) 2026-03-03 23:06(:ss) 或 2026/03/03 23:06(:ss)
    m = re.search(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?', s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hh = int(m.group(4) or 0); mm = int(m.group(5) or 0); ss = int(m.group(6) or 0)
        try:
            return dt.datetime(y, mo, d, hh, mm, ss, tzinfo=CN_TZ)  # 按北京时间
        except Exception:
            return None

    # 2) 2026年3月3日 23:06(:ss)
    m = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日(?:\s*(\d{1,2}):(\d{2})(?::(\d{2}))?)?', s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hh = int(m.group(4) or 0); mm = int(m.group(5) or 0); ss = int(m.group(6) or 0)
        try:
            return dt.datetime(y, mo, d, hh, mm, ss, tzinfo=CN_TZ)
        except Exception:
            return None

    return None

def _try_iso_or_rfc(s: str) -> Optional[dt.datetime]:
    """尝试解析 ISO/RFC；无时区→北京时间；有时区→换算到北京时间"""
    s = normalize_space(s)
    # RFC
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
    # ISO
    try:
        d = dt.datetime.fromisoformat(s.replace('Z', '+00:00'))
        if d.tzinfo is None:
            d = d.replace(tzinfo=CN_TZ)
        else:
            d = d.astimezone(CN_TZ)
        return d
    except Exception:
        return None

# ===== 从正文或 URL 抽取发布时间（最终返回：规范化值 + struct_time + 原样字符串） =====
def extract_publish_time(html: str, url: str) -> Tuple[Optional[str], Optional[time.struct_time], Optional[str]]:
    soup = BeautifulSoup(html, 'lxml')

    # --- 站点特定：新华网 ---
    xw_meta = soup.find('meta', attrs={'name': 'pubdate'}) or soup.find('meta', attrs={'name': 'publishdate'})
    if xw_meta and xw_meta.get('content'):
        raw = normalize_space(xw_meta['content'])
        d = _parse_datetime_str(raw) or _try_iso_or_rfc(raw)
        if d:
            return (d.strftime('%Y-%m-%d %H:%M'), d.timetuple(), raw)

    tag = soup.select_one('#pubtime') or soup.select_one('.header-info .time')
    if tag:
        raw = normalize_space(tag.get_text())
        d = _parse_datetime_str(raw)
        if d:
            return (d.strftime('%Y-%m-%d %H:%M'), d.timetuple(), raw)

    # --- 站点特定：网易 news.163.com ---
    if 'news.163.com' in url or 'www.163.com' in url:
        ptime = soup.select_one('#ptime') or soup.select_one('.post_time_source') or soup.select_one('.post_info')
        if ptime:
            raw = normalize_space(ptime.get_text())
            d = _parse_datetime_str(raw)
            if d:
                return (d.strftime('%Y-%m-%d %H:%M'), d.timetuple(), raw)
        meta_ptime = soup.find('meta', attrs={'name': 'ptime'})
        if meta_ptime and meta_ptime.get('content'):
            raw = normalize_space(meta_ptime['content'])
            d = _parse_datetime_str(raw) or _try_iso_or_rfc(raw)
            if d:
                return (d.strftime('%Y-%m-%d %H:%M'), d.timetuple(), raw)

    # --- 通用 meta 位 ---
    META_CANDIDATES = [
        ('meta', {'property': 'article:published_time'}, 'content'),
        ('meta', {'name': 'publishdate'}, 'content'),
        ('meta', {'name': 'PubDate'}, 'content'),
        ('meta', {'itemprop': 'datePublished'}, 'content'),
        ('meta', {'name': 'date'}, 'content'),
        ('meta', {'name': 'DC.date.issued'}, 'content'),
    ]
    for tag_name, attrs, attr_key in META_CANDIDATES:
        tag = soup.find(tag_name, attrs=attrs)
        if tag and tag.get(attr_key):
            raw = normalize_space(tag[attr_key])
            d = _parse_datetime_str(raw) or _try_iso_or_rfc(raw)
            if not d:
                m = re.search(r'(\d{4})(\d{2})(\d{2})', raw)
                if m:
                    try:
                        d = dt.datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=CN_TZ)
                    except Exception:
                        d = None
            if d:
                return (d.strftime('%Y-%m-%d %H:%M'), d.timetuple(), raw)

    # --- JSON-LD {datePublished} ---
    for node in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(node.get_text(strip=True))
            nodes = data if isinstance(data, list) else [data]
            for obj in nodes:
                if isinstance(obj, dict) and 'datePublished' in obj:
                    raw = normalize_space(str(obj['datePublished']))
                    d = _parse_datetime_str(raw) or _try_iso_or_rfc(raw)
                    if not d:
                        m = re.search(r'(\d{4})(\d{2})(\d{2})', raw)
                        if m:
                            try:
                                d = dt.datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=CN_TZ)
                            except Exception:
                                d = None
                    if d:
                        return (d.strftime('%Y-%m-%d %H:%M'), d.timetuple(), raw)
        except Exception:
            pass

    # --- <time> ---
    t = soup.find('time')
    if t:
        cand = t.get('datetime') or t.get_text(strip=True)
        if cand:
            raw = normalize_space(cand)
            d = _parse_datetime_str(raw) or _try_iso_or_rfc(raw)
            if not d:
                m = re.search(r'(\d{4})(\d{2})(\d{2})', raw)
                if m:
                    try:
                        d = dt.datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=CN_TZ)
                    except Exception:
                        d = None
            if d:
                return (d.strftime('%Y-%m-%d %H:%M'), d.timetuple(), raw)

    # --- 文本兜底 ---
    body_text = soup.get_text(separator=' ', strip=True)
    m = re.search(r'(发布时间|发布于|时间|日期)\s*[:：]\s*(\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?)', body_text)
    if m:
        raw = normalize_space(m.group(2))
        d = _parse_datetime_str(raw)
        if d:
            return (d.strftime('%Y-%m-%d %H:%M'), d.timetuple(), raw)

    # --- URL 推断日期（仅日期，不造时分秒；raw 为 None） ---
    m = (re.search(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', url) or re.search(r'(\d{4})(\d{2})(\d{2})', url))
    if m:
        try:
            y, mo, dday = int(m.group(1)), int(m.group(2)), int(m.group(3))
            dtt = dt.datetime(y, mo, dday, tzinfo=CN_TZ)
            return (dtt.strftime('%Y-%m-%d %H:%M'), dtt.timetuple(), None)
        except Exception:
            pass
    return (None, None, None)

def fetch_page_summary_and_time(url: str) -> Tuple[str, Optional[str], Optional[time.struct_time], Optional[str]]:
    summary = ''
    published_text, published_parsed, published_raw = (None, None, None)
    try:
        html = get_html(url)
        published_text, published_parsed, published_raw = extract_publish_time(html, url)
        soup = BeautifulSoup(html, 'lxml')
        for sel in ['meta[name="description"]','meta[name="Description"]','meta[property="og:description"]','meta[name="og:description"]']:
            tag = soup.select_one(sel)
            if tag and tag.get('content'):
                summary = strip_html(tag['content'])[:400]
                break
        if not summary:
            for p in soup.select('article p, .content p, .article p, .main p, p'):
                text = normalize_space(p.get_text())
                if len(text) >= 30:
                    summary = text[:400]
                    break
    except Exception:
        pass
    return (summary, published_text, published_parsed, published_raw)

def attach_summaries(items: List[Dict[str, Any]]) -> None:
    now_year = dt.datetime.now(CN_TZ).year
    keep_items = []
    for it in items:
        s = strip_html(it.get('summary', '')) if it.get('summary') else ''
        pub_txt = it.get('published', '')
        pub_parsed = it.get('published_parsed')
        pub_raw = it.get('published_raw')

        # —— 总是去正文页抓一次，确保显示值来自正文页
        summary2, pub_txt2, pub_parsed2, pub_raw2 = fetch_page_summary_and_time(it['url'])

        if not s and summary2:
            s = summary2

        # —— 用正文页结果覆盖（抓到就用）
        if pub_raw2:
            pub_raw = pub_raw2
        if pub_parsed2:
            pub_parsed = pub_parsed2
        if pub_txt2:
            pub_txt = pub_txt2

        it['summary_final'] = smart_trim(s or it['title'], 160)
        if pub_txt:
            it['published'] = pub_txt
        if pub_parsed:
            it['published_parsed'] = pub_parsed
        if pub_raw:
            it['published_raw'] = pub_raw  # 给渲染层使用

        # ——过滤过旧稿：两年前及更早（保留原逻辑）
        y = None
        if it.get('published_parsed'):
            try:
                y = dt.datetime(*it['published_parsed'][:3]).year
            except Exception:
                y = None
        if y is None:
            m = re.search(r'(\d{4})', it.get('url',''))
            y = int(m.group(1)) if m else None
        if y and y < now_year - 2:
            continue
        keep_items.append(it)

    items.clear()
    items.extend(keep_items)

def score_item(it: Dict[str, Any]) -> float:
    score = 0.0
    title = it.get('title', '')
    summary = it.get('summary_final') or it.get('summary', '')
    txt = f"{title} {summary}"
    src_count = len(it.get('sources', []))
    score += 6 if src_count >= 3 else 4 if src_count == 2 else 1 if src_count == 1 else 0
    srcw = [SOURCE_WEIGHT.get(s, 1) for s in it.get('sources', [])]
    if srcw:
        score += min(sum(srcw), 10)
    for k, w in HOT_KEYWORDS.items():
        if k in txt:
            score += w
    if re.search(r"\d", title): score += 1
    if any(c in title for c in CITY_KEYWORDS): score += 1
    if 10 <= len(title) <= 30: score += 1

    pp = it.get('published_parsed')
    if pp:
        try:
            pub_dt = dt.datetime(*pp[:6])
            # 统一：无 tz → 北京时间；有 tz → 转北京时间
            if not pub_dt.tzinfo:
                pub_dt = pub_dt.replace(tzinfo=CN_TZ)
            else:
                pub_dt = pub_dt.astimezone(CN_TZ)
            diff_h = (dt.datetime.now(CN_TZ) - pub_dt).total_seconds() / 3600.0
            score += 3 if diff_h < 3 else 2 if diff_h < 8 else 1 if diff_h < 24 else 0
        except Exception:
            pass
    return score

def rank_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(items, key=score_item, reverse=True)

def fmt_pub_time(it: Dict[str, Any]) -> str:
    """最终渲染为北京时间（仅作回退用；正常优先显示 published_raw）"""
    if it.get('published_parsed'):
        try:
            pub_dt = dt.datetime(*it['published_parsed'][:6])
            if not pub_dt.tzinfo:
                pub_dt = pub_dt.replace(tzinfo=CN_TZ)
            else:
                pub_dt = pub_dt.astimezone(CN_TZ)
            return pub_dt.strftime('%Y-%m-%d %H:%M')
        except Exception:
            return it.get('published', '')
    return it.get('published', '')

def save_to_html(items: List[Dict[str, Any]], out_fullpath: str):
    now = dt.datetime.now(CN_TZ).strftime('%Y-%m-%d %H:%M')
    css = """
:root{--fg:#222;--muted:#666;--link:#0969da;--bg:#fff;--card:#f8f9fa}
*{box-sizing:border-box}
body{
  margin:0;padding:16px 16px;color:var(--fg);background:var(--bg);
  font:16px/1.6 system-ui,-apple-system,Segoe UI,Roboto,Arial,"Microsoft Yahei",sans-serif
}
.wrap{max-width:880px;margin:0 auto}
/* 共用卡片外观 */
.card{background:var(--card);border:1px solid #e5e7eb;border-radius:14px;padding:14px 16px;margin:10px 0}
/* 顶部“便捷小链接”卡片（更靠上 & 小标题更小） */
.quickcard{position:relative;border:2px solid #334155;background:#f3f4f6;margin-top:-6px}
.quickcard-title{
  position:absolute; top:-14px; left:50%; transform:translateX(-50%);
  background:#fff; padding:3px 10px; border:1px solid #cbd5e1; border-radius:6px;
  font-size:.85rem; color:#111827; box-shadow:0 1px 1px rgba(0,0,0,.04)
}
nav.quicklinks{
  display:flex; gap:28px; justify-content:space-around; align-items:center; flex-wrap:wrap;
  min-height:36px; padding:6px 4px; font-size:.95rem;
}
nav.quicklinks a{color:#0b66d6;text-decoration:none}
nav.quicklinks a:hover{text-decoration:underline}
/* 页面标题 */
header h1{
  margin:8px 0 6px 0;
  font-size:1.6rem;
  text-align:center;
}
header .ts{color:var(--muted);font-size:.9rem;margin-bottom:6px}
/* 新闻卡片与内容 */
.idx{display:inline-block;width:36px;color:#888}
.title a{color:var(--link);text-decoration:none}
.title a:hover{text-decoration:underline}
.meta{color:var(--muted);font-sizesum{margin-top:6px}
footer{color:var(--muted);font-size:.85rem;margin-top:28px}
    """.strip()

    head = (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"/>'
        '<meta name="viewport" content="width=device-width,initial-scale=1"/>'
        '<meta http-equiv="refresh" content="3600">'
        '<meta name="description" content="今日热点新闻（自动评分排序，汇聚多家权威媒体来源，定时更新）。" />'
        '<title>今日热点新闻</title><style>' + css + '</style></head><body>'
        '<div class="wrap"><header>'
        # ① 顶部“便捷小链接”卡片（标准 <a>）
        '<div class="card quickcard">'
        '<div class="quickcard-title">便捷小链接</div>'
        '<nav class="quicklinks">'
        'https://wap.weather.com.cn/mweather/天气预报</a>'
        'https://www.doubao.com/豆包</a>'
        'https://www.ip138.com/ditie/城市地铁</a>'
        'https://m.hao268.com/网址之家</a>'
        'https://wap.baidu.com/百度</a>'
        '</nav>'
        '</div>'
        # ② 标题放在卡片下方
        '<h1>今日热点新闻</h1>'
        f'<div class="ts">生成时间：{now}</div></header><section>'
    )

    rows: List[str] = []
    for i, it in enumerate(items, 1):
        title = htmllib.escape(it['title'])
        url = htmllib.escape(it['url'])
        srcs = '、'.join(it.get('sources', [])) or '未知'

        # ——显示优先使用源站原样字符串；抓不到再回退到规范化（北京时间）
        pub_display = it.get('published_raw') or fmt_pub_time(it) or '—'

        summary = htmllib.escape(it.get('summary_final', '') or it.get('summary', '') or it['title'])
        rows.append(
            (
                f"<article class='card'><div class='title'><span class='idx'>{i:02d}.</span>"
                f"{url}{title}</a></div>"
                f"<div class='meta'>来源：{htmllib.escape(srcs)}；日期：{htmllib.escape(pub_display)}</div>"
                f"<div class='sum'>摘要：{summary}</div></article>"
            )
        )

    tail = (
        "</section><footer>本站每小时自动刷新一次；仅做信息聚合与索引，内容以源站为准。"
        " Copyright © 2026 Yingfeng Su. All rights reserved.</footer></div></body></html>"
    )
    with open(out_fullpath, 'w', encoding='utf-8') as f:
        f.write(head + "\n".join(rows) + tail)

# 数据源（含三家更稳入口；搜狐 RSS 修正为完整 https 链接 + 半角引号）
SOURCES: List[Tuple[str, Dict[str, str]]] = [
    ("中新网-即时", {"rss": "https://www.chinanews.com.cn/rss/scroll-news.xml"}),
    ("中新网-要闻", {"rss": "https://www.chinanews.com.cn/rss/importnews.xml"}),
    ("中新网-国内", {"rss": "https://www.chinanews.com.cn/rss/china.xml"}),
    ("中新网-社会", {"rss": "https://www.chinanews.com.cn/rss/society.xml"}),
    ("央视网-新闻频道", {"html": "https://news.cctv.cn/", "parser": "parse_cctv_index"}),
    ("央视网-国内新闻", {"html": "https://news.cctv.com/china/", "parser": "parse_cctv_china"}),
    ("新华网-首页", {"html": "https://www.news.cn/", "parser": "parse_xinhua"}),
    ("环球网-国内", {"html": "https://www.huanqiu.com/", "parser": "parse_huanqiu_china"}),
    ("网易新闻-国内", {"html": "https://news.163.com/domestic/", "parser": "parse_net163_domestic"}),
    ("澎湃新闻", {"rss": "https://feedx.net/rss/thepaper.xml", "html": "https://www.thepaper.cn/news", "parser": "parse_thepaper"}),
    ("南方都市报", {"html": "https://news.southcn.com/node_17a07e5926/?cms_node_post_list_page=1", "parser": "parse_nandu"}),
    ("财经网", {"html": "https://finance.caijing.com.cn/", "parser": "parse_caijing"}),
    ("凤凰网", {"html": "https://news.ifeng.com/", "parser": "parse_ifeng"}),
    ("搜狐新闻", {"rss": "https://rss.news.sohu.com/rss/guonei.xml", "html": "https://news.sohu.com/", "parser": "parse_sohu_news"}),
]

# ---------- CLI 主流程（含兼容参数） ----------
def main():
    parser = argparse.ArgumentParser(description='CN Hot News Ranker (HTML only) — one-file version')
    # 兼容旧参数：仅接受并忽略（方便不改工作流）
    parser.add_argument('--no-txt', action='store_true', help='(兼容参数) 不导出 TXT；已移除，忽略之')
    parser.add_argument('--no-docx', action='store_true', help='(兼容参数) 不导出 DOCX；已移除，忽略之')
    # 实际有效参数
    parser.add_argument('--html', default=None, help='输出 HTML 文件路径，如 D:\\MyCode\\Hot_Points\\index.html')
    parser.add_argument('--outdir', default=OUTPUT_DIR, help='输出目录（覆盖默认设置）')
    parser.add_argument('--proxy', default=None, help='可选：HTTP/HTTPS 代理，如 http://127.0.0.1:7890')

    args = parser.parse_args()
    outdir = args.outdir
    os.makedirs(outdir, exist_ok=True)
    if args.proxy:
        set_proxy(args.proxy)

    print('=== 中国热点新闻评分模型（HTML 版，精准选择器）启动 ===')
    print('输出目录：', outdir)
    if _session.proxies:
        print('代理已启用：', _session.proxies)

    raw: List[Dict[str, Any]] = []
    failed_sources: List[str] = []

    for name, conf in SOURCES:
        time.sleep(SLEEP_BETWEEN)
        print(f'[抓取] {name} ... ', end='')
        data = fetch_from_source(name, conf)
        if not data:
            failed_sources.append(name)
        before = len(data)
        data = [d for d in data if not is_excluded(d.get('title', ''), d.get('summary', ''))]
        after = len(data)
        print(f'获取 {before} 条，过滤后 {after} 条')
        raw.extend(data)

    print(f'[汇总] 原始合计 {len(raw)} 条；去重合并 …')
    merged = dedup_and_group(raw)

    print('[摘要] 生成摘要 + 补齐时间（正文页覆盖显示值） …')
    attach_summaries(merged)

    print(f'[评分] 排序 …')
    ranked = rank_items(merged)
    topn = ranked[:TOP_N]

    html_path = args.html or os.path.join(outdir, 'index.html')
    save_to_html(topn, html_path)
    print('[完成] HTML:', html_path)

    if failed_sources:
        print('[提示] 以下来源本次未获取到数据：', '、'.join(failed_sources))

if __name__ == '__main__':
    import traceback
    try:
        main()
    except Exception as e:
        log_path = os.path.join(OUTPUT_DIR, 'error.log')
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(f"[{dt.datetime.now(CN_TZ)}] {e}{traceback.format_exc()}\n")
        print('[异常] 运行出错，已写入日志：', log_path)
