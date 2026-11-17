#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
內嵌注音字體小幫手 zhuyin-font-embed

把注音字型（字旁直接帶注音）內嵌進 HTML、PDF、Word，並自動處理破音字。

指令：
  python zhuyin.py html  輸入.html [-o 輸出.html] [--font zihi|huninn|iansui] [--no-toggle]
  python zhuyin.py text  輸入.txt  [-o 輸出.html] [--title 標題]        # 純文字 → 注音網頁
  python zhuyin.py fix   已輸出.html --set 12=2 --set 30=1                # 依報告編號改讀音
  python zhuyin.py merge 已輸出.html 校對後文字.txt                       # bpmfvs / toneoz 修正後回填
  python zhuyin.py export 已輸出.html [-o 文字.txt]                       # 匯出純文字，可貼到 bpmfvs
  python zhuyin.py pdf   已輸出.html [-o 輸出.pdf]
  python zhuyin.py docx  輸入.docx [-o 輸出.docx] [--font zihi]
  python zhuyin.py readings 長行和 [--font zihi]                            # 查某字有哪些讀音

需要：pip install fonttools brotli lxml
字型第一次使用時會自動從 Google Fonts 官方 GitHub 下載並快取。
"""
import argparse
import base64
import difflib
import html as htmlmod
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import uuid
import zipfile

import logging
logging.getLogger("fontTools").setLevel(logging.ERROR)

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "data")

FONTS = {
    "zihi":   ("Bpmf Zihi Kai Std", "字嗨注音標楷", "bpmfzihikaistd/BpmfZihiKaiStd-Regular.ttf"),
    "huninn": ("Bpmf Huninn",       "粉圓注音",     "bpmfhuninn/BpmfHuninn-Regular.ttf"),
    "iansui": ("Bpmf Iansui",       "芫荽注音",     "bpmfiansui/BpmfIansui-Regular.ttf"),
}
FONT_URL = "https://raw.githubusercontent.com/google/fonts/main/ofl/"
FALLBACK = "'TW-Kai','BiauKai','標楷體','DFKai-SB','Kaiti TC',serif"
VS_BASE = 0xE01E0
VS_ALL = set(range(0xE0100, 0xE01F0)) | set(range(0xFE00, 0xFE10))
MARK_CSS = ("<!--zhuyin-font-embed:css-->", "<!--/zhuyin-font-embed:css-->")
MARK_BTN = ("<!--zhuyin-font-embed:toggle-->", "<!--/zhuyin-font-embed:toggle-->")
SKIP_TAGS = {"script", "style", "title", "textarea", "noscript", "svg", "math", "code", "pre"}
BLOCK_TAGS = {"p", "div", "li", "ul", "ol", "br", "td", "th", "tr", "table", "h1", "h2", "h3", "h4",
              "h5", "h6", "section", "article", "header", "footer", "nav", "aside", "main", "blockquote",
              "dt", "dd", "dl", "figure", "figcaption", "label", "button", "option", "select", "hr",
              "body", "html", "head", "form", "fieldset", "legend", "caption", "summary", "details"}


def out(*a):
    print(*a)
    sys.stdout.flush()


# ───────────────────────── 字型 ─────────────────────────

def font_cache_dir():
    d = os.environ.get("ZHUYIN_FONT_DIR") or os.path.join(os.path.expanduser("~"), ".cache", "zhuyin-font-embed")
    os.makedirs(d, exist_ok=True)
    return d


def font_path(key, font_file=None):
    if font_file:
        return font_file
    name, zh, rel = FONTS[key]
    p = os.path.join(font_cache_dir(), os.path.basename(rel))
    if not os.path.exists(p) or os.path.getsize(p) < 100000:
        out(f"第一次使用「{zh}」，下載字型中（約 5～17 MB）…")
        tmp = p + ".part"
        with urllib.request.urlopen(FONT_URL + rel, timeout=300) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f)
        os.replace(tmp, p)
    return p


_BPMF = {"b": "ㄅ", "p": "ㄆ", "m": "ㄇ", "f": "ㄈ", "d": "ㄉ", "t": "ㄊ", "n": "ㄋ", "l": "ㄌ", "g": "ㄍ",
         "k": "ㄎ", "h": "ㄏ", "j": "ㄐ", "q": "ㄑ", "x": "ㄒ", "zh": "ㄓ", "ch": "ㄔ", "sh": "ㄕ", "r": "ㄖ",
         "z": "ㄗ", "c": "ㄘ", "s": "ㄙ", "i": "ㄧ", "u": "ㄨ", "iu": "ㄩ", "a": "ㄚ", "o": "ㄛ", "e": "ㄜ",
         "eh": "ㄝ", "ai": "ㄞ", "ei": "ㄟ", "ao": "ㄠ", "ou": "ㄡ", "an": "ㄢ", "en": "ㄣ", "ang": "ㄤ",
         "eng": "ㄥ", "er": "ㄦ"}
_ORDER = "ㄅㄆㄇㄈㄉㄊㄋㄌㄍㄎㄏㄐㄑㄒㄓㄔㄕㄖㄗㄘㄙㄧㄨㄩㄚㄛㄜㄝㄞㄟㄠㄡㄢㄣㄤㄥㄦ"
_TONE = {"tone2": "ˊ", "tone3": "ˇ", "tone4": "ˋ"}


class ZhuyinFont:
    """讀取注音 IVS 字型：每個字有哪些讀音、讀音的注音文字。"""

    def __init__(self, path):
        from fontTools.ttLib import TTFont
        self.path = path
        self.tt = TTFont(path, lazy=True)
        self.cmap = self.tt.getBestCmap()
        t14 = [t for t in self.tt["cmap"].tables if t.format == 14]
        self.uvs = {}
        if t14:
            for sel, lst in t14[0].uvsDict.items():
                for cp, g in lst:
                    self.uvs[(cp, sel)] = g or self.cmap.get(cp)
        self.family = self.tt["name"].getDebugName(1)
        self._cache = {}

    def count(self, ch):
        cp = ord(ch)
        n = 1
        while (cp, VS_BASE + n) in self.uvs:
            n += 1
        return n

    def glyph(self, ch, j):
        cp = ord(ch)
        return self.cmap.get(cp) if j == 0 else self.uvs.get((cp, VS_BASE + j))

    def reading(self, ch, j):
        key = (ch, j)
        if key in self._cache:
            return self._cache[key]
        g = self.glyph(ch, j)
        r = ""
        if g:
            gl = self.tt["glyf"][g]
            if gl.isComposite():
                names = [c.glyphName for c in gl.components]
                sym = [_BPMF[n[2:]] for n in names if n.startswith("zy") and n[2:] in _BPMF]
                sym.sort(key=_ORDER.index)
                tone = "".join(_TONE[n] for n in names if n in _TONE)
                r = ("˙" if "tone5" in names else "") + "".join(sym) + tone
        self._cache[key] = r
        return r

    def readings(self, ch):
        return [self.reading(ch, j) for j in range(self.count(ch))]


def subset_font(path, text, flavor=None, english_name_only=False):
    from fontTools.ttLib import TTFont
    from fontTools import subset
    cps = {ord(c) for c in text if ord(c) not in VS_ALL}
    cps |= set(range(0x20, 0x7F)) | set(range(VS_BASE, VS_BASE + 16))
    cps |= {ord(c) for c in "，。、；：？！「」『』（）《》〈〉…—～．·"}
    opt = subset.Options()
    opt.layout_features = ["*"]
    opt.name_IDs = ["*"]
    opt.name_languages = ["*"]
    opt.notdef_outline = True
    opt.drop_tables = []
    if flavor:
        opt.flavor = flavor
    f = TTFont(path)
    s = subset.Subsetter(opt)
    s.populate(unicodes=cps)
    s.subset(f)
    if english_name_only:
        # Word 會用作業系統語言的字型名稱比對（中文 Windows 看到「ㄅ字嗨注音標楷」），
        # 只留英文名稱，確保 fontTable 的名稱一定對得上。
        f["name"].names = [n for n in f["name"].names
                           if not (n.platformID == 3 and n.langID != 0x409) and n.platformID != 1]
    if flavor:
        f.flavor = flavor
    b = io.BytesIO()
    f.save(b)
    return b.getvalue()


# ───────────────────────── 破音字判斷 ─────────────────────────

class Poyin:
    """bpmfvs 詞庫（Apache 2.0）＋ 特工365補充詞庫。規則：'*' 代表該字，例如「*大」=長大；
    開頭 '!' 表示優先規則。比對時取最長的規則；一樣長時補充詞庫優先，再以右側詞語優先。"""

    def __init__(self, sandhi=False):
        self.sandhi = sandhi
        with open(os.path.join(DATA, "poyin_db.json"), encoding="utf-8") as f:
            self.db = json.load(f)
        self.extra = {}
        self.amb = set()
        self.disabled = {}
        p = os.path.join(DATA, "extra_phrases.json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                ex = json.load(f)
            self.amb = set(ex.get("_ambiguous", ""))
            self.disabled = {c: set(v) for c, v in ex.get("_停用bpmfvs規則", {}).items()}
            for ch, d in ex.items():
                if not ch.startswith("_"):
                    self.extra[ch] = {int(k): v for k, v in d.items()}

    def is_poly(self, ch):
        return ch in self.db

    def need_judge(self, ch):
        if ch in "一不" and not self.sandhi:
            return False
        return ch in self.amb or self.fuzzy(ch)

    def fuzzy(self, ch):
        return bool(self.db.get(ch, {}).get("f"))

    def rules(self, ch):
        for j, pats in self.extra.get(ch, {}).items():
            for p in pats:
                yield j, p, 1
        for j, v in enumerate(self.db.get(ch, {}).get("v", [])):
            for p in v.split("/"):
                if p and p not in self.disabled.get(ch, ()):
                    yield j, p, 0

    def auto(self, units):
        """units: list of dict {c, vs}；就地設定 vs 與 src。"""
        n = len(units)
        for i, u in enumerate(units):
            c = u["c"]
            if c not in self.db or u.get("vs") is not None or u.get("lock"):
                continue
            if c in "一不" and not self.sandhi:
                u["src"] = "本調"
                continue
            best = None
            for j, p, is_extra in self.rules(c):
                strong = p.startswith("!")
                p = p.lstrip("!")
                pos = p.index("*")
                st = i - pos
                if st < 0 or st + len(p) > n:
                    continue
                want = p.replace("*", c)
                if "".join(units[k]["c"] for k in range(st, st + len(p))) != want:
                    continue
                score = (len(p) + (10 if strong else 0), is_extra, -pos)
                if best is None or score > best[0]:
                    best = (score, j, p, st, want, is_extra)
            if not best:
                continue
            _, j, p, st, want, is_extra = best
            src = "補充詞庫" if is_extra else "bpmfvs"
            for x, pc in enumerate(p):
                t = units[st + x]
                if pc == "*" and t.get("vs") is None and not t.get("lock"):
                    t["vs"] = j
                    t["src"] = f"{src}「{want}」"


# ───────────────────────── HTML 處理 ─────────────────────────

TOKEN_RE = re.compile(r"(<!--.*?-->|<![^>]*>|<\?.*?\?>|<[^>]+>)", re.S)


def split_units(s):
    """把文字切成 [(字, 原始片段)]；異體選擇子跟著前一字；實體字元整體一單位。"""
    res = []
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == "&":
            m = re.match(r"&(#\d+|#x[0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]*);", s[i:])
            if m:
                res.append([m.group(0), m.group(0), None])
                i += len(m.group(0))
                continue
        if ord(ch) in VS_ALL and res:
            res[-1][1] += ch
            if VS_BASE <= ord(ch) < VS_BASE + 16:
                res[-1][2] = (ord(ch) - VS_BASE) or -1   # -1 = 不標注音（U+E01E0）
            i += 1
            continue
        res.append([ch, ch, None])
        i += 1
    return res


def scan_html(src):
    """回傳 (tokens, units)。tokens: list of [type, text]；units 依文件順序。"""
    toks = TOKEN_RE.split(src)
    units = []
    skip = []
    script_hanzi = 0
    for ti, t in enumerate(toks):
        if ti % 2 == 1:
            m = re.match(r"<\s*(/)?\s*([a-zA-Z0-9]+)", t)
            if m:
                name = m.group(2).lower()
                closing = bool(m.group(1))
                if name in SKIP_TAGS and not t.rstrip().endswith("/>"):
                    if closing:
                        if skip and skip[-1] == name:
                            skip.pop()
                    else:
                        skip.append(name)
                if name in BLOCK_TAGS:
                    units.append({"c": "\n", "tok": None})
            continue
        if not t:
            continue
        if skip:
            if skip[-1] in ("script",):
                script_hanzi += sum(1 for c in t if "\u4e00" <= c <= "\u9fff")
            continue
        for k, (c, raw, vs) in enumerate(split_units(t)):
            units.append({"c": c, "raw": raw, "vs": vs, "tok": ti, "k": k, "pre": vs is not None})
    return toks, units, script_hanzi


def vs_char(vs):
    if vs == -1:
        return chr(VS_BASE)
    return chr(VS_BASE + vs) if vs else ""


def rebuild(toks, units):
    by_tok = {}
    for u in units:
        if u.get("tok") is not None:
            by_tok.setdefault(u["tok"], []).append(u)
    toks = list(toks)
    for ti, us in by_tok.items():
        parts = []
        for u in us:
            vs = u.get("vs")
            parts.append(u["c"] + vs_char(vs))
        toks[ti] = "".join(parts)
    return "".join(toks)


def strip_marked(src, marks):
    a, b = marks
    return re.sub(re.escape(a) + r".*?" + re.escape(b) + r"\n?", "", src, flags=re.S)


def gf_url(family):
    return "https://fonts.googleapis.com/css2?family=" + family.replace(" ", "+") + "&display=block"


def css_block(family, b64, key, lock="", only=None):
    excl = ':not(.no-bpmf):not(.no-bpmf *):not(i[class*="fa"]):not(.material-icons):not(.material-symbols-outlined):not(svg *)'
    if only:
        scope = f"html:not(.zy-off) :is({only}),html:not(.zy-off) :is({only}) *{excl}"
    else:
        scope = f"html:not(.zy-off) body,html:not(.zy-off) body *{excl}"
    if b64:
        face = f"@font-face{{font-family:'ZY {family}';src:url(data:font/woff2;base64,{b64}) format('woff2');font-display:block;}}"
        fam = f"'ZY {family}'"
        link = ""
    else:
        face = ""
        fam = f"'{family}'"
        link = f'<link rel="stylesheet" href="{gf_url(family)}">\n'
    return f"""{MARK_CSS[0]}
{link}<style>
{face}
{scope}{{font-family:{fam},{FALLBACK}!important;}}
html.zy-off body,html.zy-off body *:not(i[class*="fa"]):not(.material-icons):not(.material-symbols-outlined){{font-family:{FALLBACK}!important;}}
#zy-toggle{{position:fixed;right:12px;bottom:12px;z-index:99999;padding:8px 14px;border:2px solid #4a7;border-radius:20px;background:#fff;color:#264;font-size:16px;cursor:pointer;box-shadow:0 2px 6px rgba(0,0,0,.15);font-family:{FALLBACK}!important;}}
@media print{{#zy-toggle{{display:none!important;}}}}
</style>
<meta name="zhuyin-font" content="{key}">
<meta name="zhuyin-lock" content="{lock}">{'<meta name="zhuyin-online" content="1">' if not b64 else ''}
<meta name="zhuyin-only" content="{htmlmod.escape(only or '')}">
{MARK_CSS[1]}
"""


TOGGLE = f"""{MARK_BTN[0]}
<button id="zy-toggle" type="button" class="no-bpmf" onclick="var h=document.documentElement;h.classList.toggle('zy-off');this.textContent=h.classList.contains('zy-off')?'注音：關':'注音：開';">注音：開</button>
{MARK_BTN[1]}
"""


def inject(src, block, where):
    if where == "head":
        m = re.search(r"</head\s*>", src, re.I)
        if m:
            return src[:m.start()] + block + src[m.start():]
        m = re.search(r"<body[^>]*>", src, re.I)
        if m:
            return src[:m.start()] + block + src[m.start():]
        return block + src
    m = None
    for m in re.finditer(r"</body\s*>", src, re.I):
        pass
    if m:
        return src[:m.start()] + block + src[m.start():]
    return src + block


DI_PREV = set("然心意真力細靜聲氣快慢緊輕悄默停過奮傷怕柔情貌口深偷注耐敢樂斷忙匆興傲勉怒暗鬆楚定喜急笑叫喊乖勤懇切")


def di_adverb(units, idx):
    """「地」前面是疊字或常見副詞結尾字時，才可能唸˙ㄉㄜ。"""
    p1 = units[idx - 1]["c"] if idx >= 1 else ""
    p2 = units[idx - 2]["c"] if idx >= 2 else ""
    return (p1 and p1 == p2) or p1 in DI_PREV


def collect_items(units, zf, py):
    """列出所有破音字（字型有 2 個以上讀音者）。"""
    items = []
    for idx, u in enumerate(units):
        c = u["c"]
        if u.get("tok") is None or len(c) != 1 or not py.is_poly(c):
            continue
        n = zf.count(c)
        if n < 2:
            continue
        vs = u.get("vs") or 0
        if u.get("pre"):
            st = "已指定"
        elif u.get("src") and u.get("src") != "本調":
            st = "規則"
        elif u.get("src") == "本調":
            st = "本調"
        elif py.need_judge(c) and not (c == "地" and not di_adverb(units, idx)):
            st = "需判斷"
        else:
            st = "預設"
        items.append({"u": idx, "c": c, "vs": vs, "status": st, "src": u.get("src", "")})
    for no, it in enumerate(items, 1):
        it["id"] = no
    return items


def context(units, idx, span=12):
    a = idx
    while a > 0 and idx - a < span and units[a - 1]["c"] != "\n":
        a -= 1
    b = idx
    while b < len(units) - 1 and b - idx < span and units[b + 1]["c"] != "\n":
        b += 1
    s = "".join(units[k]["c"] if k != idx else f"【{units[k]['c']}】" for k in range(a, b + 1))
    return re.sub(r"&[a-z]+;", " ", s).strip()


def write_report(path_base, units, items, zf, script_hanzi):
    rows = []
    for it in items:
        c = it["c"]
        rs = zf.readings(c)
        rows.append({
            "id": it["id"], "字": c, "讀音": "不標注音" if it["vs"] == -1 else rs[it["vs"]], "編號": it["vs"],
            "狀態": it["status"], "依據": it["src"], "上下文": context(units, it["u"]),
            "可選": {**{str(j): r for j, r in enumerate(rs)}, "-1": "不標注音"},
        })
    with open(path_base + "_破音字報告.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    need = [r for r in rows if r["狀態"] == "需判斷"]
    lines = ["# 破音字報告", "",
             f"共 {len(rows)} 個破音字：規則判定 {sum(r['狀態']=='規則' for r in rows)}、"
             f"需判斷 {len(need)}、用預設讀音 {sum(r['狀態']=='預設' for r in rows)}、"
             f"原本已指定 {sum(r['狀態']=='已指定' for r in rows)}。", ""]
    if script_hanzi:
        lines += [f"注意：程式碼（script）裡有 {script_hanzi} 個中文字不會自動標讀音，"
                  "動態產生的文字若有破音字，請在程式字串裡加上選擇符（見 SKILL.md）。", ""]
    lines += ["## 需判斷（AI 要逐句讀上下文決定）", "",
              "同一個詞出現多次會合併成一列，「id」欄列出全部編號；讀音若相同可一起指定，例如 --set \"12,30,41=1\"。", "",
              "| id | 詞 | 上下文（第一次出現） | 目前讀音 | 可選讀音 |", "|---|---|---|---|---|"]
    groups = {}
    for r in need:
        m = re.search(r"(.)?【(.)】(.)?", r["上下文"])
        key = (m.group(1) or "") + "【" + m.group(2) + "】" if m else r["字"]
        groups.setdefault((key, r["讀音"]), []).append(r)
    for (key, _), rs in groups.items():
        r = rs[0]
        opts = "、".join(f"{j}={v}" for j, v in r["可選"].items() if j != "-1")
        ids = ",".join(str(x["id"]) for x in rs)
        lines.append(f"| {ids} | {key} | {r['上下文']} | {r['讀音']} | {opts} |")
    lines += ["", "## 其他破音字（規則判定或預設讀音，請快速掃過）", "",
              "| id | 上下文 | 讀音 | 狀態 | 依據 |", "|---|---|---|---|---|"]
    for r in rows:
        if r["狀態"] != "需判斷":
            lines.append(f"| {r['id']} | {r['上下文']} | {r['讀音']} | {r['狀態']} | {r['依據']} |")
    with open(path_base + "_破音字報告.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return need


def proof_page(path_base, final_src, family, b64, zf, py, items):
    """產生「破音字校對頁」：點字換讀音，可下載修正後網頁、複製文字。"""
    toks, units, _ = scan_html(final_src)
    # 計算每個破音字在 final_src 中的 UTF-16 位移
    offs = []
    pos = 0
    tok_start = []
    for t in toks:
        tok_start.append(pos)
        pos += len(t)
    tokunits = {}
    for u in units:
        if u.get("tok") is not None:
            tokunits.setdefault(u["tok"], []).append(u)
    cp_off = {}
    for ti, us in tokunits.items():
        p = tok_start[ti]
        for u in us:
            u["cp"] = p
            p += len(u["raw"])

    def u16(cpidx, _cache={}):
        return len(final_src[:cpidx].encode("utf-16-le")) // 2

    blocks = [[]]
    data = []
    readings = {}
    for idx, u in enumerate(units):
        if u.get("tok") is None:
            if blocks[-1]:
                blocks.append([])
            continue
        c = u["c"]
        if len(c) == 1 and py.is_poly(c) and zf.count(c) >= 2:
            if c not in readings:
                readings[c] = zf.readings(c)
            start = u["cp"] + 1
            vslen = len(u["raw"]) - 1
            blocks[-1].append([c, len(data)])
            data.append([u16(start), u16(start + vslen) - u16(start), u.get("vs") or 0])
        else:
            blocks[-1].append(htmlmod.unescape(c) if c.startswith("&") else c)
    blocks = [b for b in blocks if any((x.strip() if isinstance(x, str) else True) for x in b)]
    src_b64 = base64.b64encode(final_src.encode("utf-8")).decode()
    name = os.path.basename(path_base)
    if b64:
        face = "@font-face{font-family:'ZY';src:url(data:font/woff2;base64,%s) format('woff2');font-display:block;}" % b64
    else:
        face = "@import url('%s');" % gf_url(family)
    page = PROOF_TMPL.replace("%FACE%", face)
    if not b64:
        page = page.replace("'ZY'", "'%s'" % family).replace("%NAME%", htmlmod.escape(name))
    code = {"需判斷": "n", "規則": "r", "已指定": "r"}
    ok = [code.get(it["status"], "d") for it in items] if len(items) == len(data) else ["d"] * len(data)
    page = page.replace("%DATA%", json.dumps({"blocks": blocks, "items": data, "readings": readings, "ok": ok,
                                              "file": name + ".html"}, ensure_ascii=False))
    page = page.replace("%SRC%", src_b64)
    with open(path_base + "_破音字校對.html", "w", encoding="utf-8") as f:
        f.write(page)


PROOF_TMPL = r"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>破音字校對：%NAME%</title>
<style>
%FACE%
body{margin:0;background:#f6f4ee;color:#222;font-family:'Microsoft JhengHei','PingFang TC',sans-serif;}
header{position:sticky;top:0;background:#264;color:#fff;padding:12px 16px;display:flex;gap:10px;flex-wrap:wrap;align-items:center;z-index:5}
header h1{font-size:18px;margin:0 12px 0 0}
header button{font-size:15px;padding:7px 12px;border:0;border-radius:6px;background:#fff;color:#264;cursor:pointer}
.legend{font-size:14px;padding:10px 16px;background:#fff;border-bottom:1px solid #ddd;line-height:1.8}
.legend span{display:inline-block;padding:0 6px;border-radius:4px;margin-right:6px}
main{max-width:900px;margin:16px auto;padding:0 16px}
.blk{font-family:'ZY',serif;font-size:30px;line-height:2.1;background:#fff;padding:10px 16px;margin:0 0 10px;border-radius:8px;white-space:pre-wrap;word-break:break-all}
.p{cursor:pointer;border-radius:4px;border-bottom:2px dotted #bbb}
.p.n{background:#fff3b0;border-bottom-color:#e0a800}.p.r{background:#e3f4e7;border-bottom-color:#8c8}.p.chg{background:#cfe3ff;border-bottom-color:#48c}.p.cur{outline:3px solid #e66}
body.onlyn .blk:not(.hasn){display:none}
#pop{position:fixed;display:none;background:#fff;border:2px solid #264;border-radius:10px;padding:8px;box-shadow:0 4px 14px rgba(0,0,0,.25);z-index:9}
#pop button{font-family:'ZY',serif;font-size:34px;margin:4px;padding:2px 8px;border:1px solid #ccc;border-radius:6px;background:#fff;cursor:pointer;min-width:62px}
#pop button small{display:block;font-family:sans-serif;font-size:13px;color:#555}
#pop button.on{border-color:#e66;background:#ffecec}
#msg{margin-left:auto;font-size:14px}
</style></head><body>
<header><h1>破音字校對</h1>
<button id="dl">下載修正後網頁</button><button id="cp">複製文字（可貼到 bpmfvs）</button><button id="code">複製修正碼給 AI</button><span id="msg"></span></header>
<div class="legend">點有虛線的字就能選讀音。<span style="background:#fff3b0">黃底</span>需要確認（<b id="cnt"></b> 個）
<span style="background:#e3f4e7">綠底</span>已依詞語判斷 <span style="background:#cfe3ff">藍底</span>你改過的，沒底色的是常用預設讀音。改完按「下載修正後網頁」，用下載的檔案取代原檔即可。
<label style="margin-left:8px"><input type="checkbox" onchange="document.body.classList.toggle('onlyn',this.checked)"> 只看需要確認的段落</label></div>
<main id="m"></main><div id="pop"></div>
<script>
const D=%DATA%;const SRC="%SRC%";const VS=0xE01E0;
const cur=D.items.map(x=>x[2]);const orig=cur.slice();const ok=new Set();
const m=document.getElementById('m'),pop=document.getElementById('pop');
function ch(c,j){return c+(j===-1?String.fromCodePoint(VS):j?String.fromCodePoint(VS+j):'');}
D.blocks.forEach(b=>{const d=document.createElement('div');d.className='blk';b.forEach(x=>{if(typeof x==='string'){d.appendChild(document.createTextNode(x));}else{const s=document.createElement('span');s.className='p';s.dataset.i=x[1];s.dataset.c=x[0];s.textContent=ch(x[0],cur[x[1]]);d.appendChild(s);}});m.appendChild(d);});
function paint(s){const i=+s.dataset.i;s.classList.toggle('chg',cur[i]!==orig[i]);}
document.querySelectorAll('.p').forEach(s=>{const i=+s.dataset.i;s.classList.add(D.ok[i]);if(D.ok[i]==='n')s.closest('.blk').classList.add('hasn');paint(s);});
document.getElementById('cnt').textContent=D.ok.filter(x=>x==='n').length;
let open=null;
m.addEventListener('click',e=>{const s=e.target.closest('.p');if(!s)return;e.stopPropagation();
 document.querySelectorAll('.cur').forEach(x=>x.classList.remove('cur'));s.classList.add('cur');open=s;
 const i=+s.dataset.i,c=s.dataset.c,rs=D.readings[c]||[];pop.innerHTML='';
 rs.forEach((r,j)=>{const b=document.createElement('button');b.innerHTML=ch(c,j)+'<small>'+(r||'不標注音')+'</small>';if(j===cur[i])b.className='on';
  b.onclick=ev=>{ev.stopPropagation();cur[i]=j;s.textContent=ch(c,j);paint(s);pop.style.display='none';msg('已改：'+c+' → '+(r||'不標注音'));};pop.appendChild(b);});
 const nb=document.createElement('button');nb.innerHTML=ch(c,0)+String.fromCodePoint(VS)+'<small>不標注音</small>';
 nb.onclick=ev=>{ev.stopPropagation();cur[i]=-1;s.textContent=ch(c,-1);paint(s);pop.style.display='none';msg('已改：'+c+' → 不標注音');};if(cur[i]===-1)nb.className='on';pop.appendChild(nb);
 const rc=s.getBoundingClientRect();pop.style.display='block';
 pop.style.left=Math.max(8,Math.min(innerWidth-pop.offsetWidth-8,rc.left-20))+'px';pop.style.top=(rc.bottom+6)+'px';});
document.addEventListener('click',()=>{pop.style.display='none';});
function msg(t){document.getElementById('msg').textContent=t;}
function fixed(){const bin=atob(SRC);const u8=new Uint8Array(bin.length);for(let k=0;k<bin.length;k++)u8[k]=bin.charCodeAt(k);
 let s=new TextDecoder().decode(u8);const idx=D.items.map((x,i)=>i).sort((a,b)=>D.items[b][0]-D.items[a][0]);
 idx.forEach(i=>{const [o,l]=D.items[i];if(cur[i]===orig[i])return;s=s.slice(0,o)+ch('',cur[i])+s.slice(o+l);});return s;}
document.getElementById('dl').onclick=()=>{const b=new Blob([fixed()],{type:'text/html'});const a=document.createElement('a');a.href=URL.createObjectURL(b);a.download=D.file;a.click();msg('已下載：'+D.file);};
document.getElementById('cp').onclick=()=>{const t=[...m.querySelectorAll('.blk')].map(d=>d.textContent).join('\n');navigator.clipboard.writeText(t).then(()=>msg('已複製文字'));};
document.getElementById('code').onclick=()=>{const c=D.items.map((x,i)=>cur[i]!==orig[i]?(i+1)+'='+cur[i]:null).filter(Boolean);
 const t=c.length?'破音字修正碼：'+c.join(' '):'沒有修改';navigator.clipboard.writeText(t).then(()=>msg(t));};
</script></body></html>"""


def build_html(src, out_path, key, font_file=None, toggle=True, fixes=None, auto=True, quiet=False, sandhi=False, extra_lock=None, only=None, online=None):
    py = Poyin(sandhi)
    zf = ZhuyinFont(font_path(key, font_file))
    if online is None:
        online = '<meta name="zhuyin-online" content="1">' in src
    if only is None:
        mo = re.search(r'<meta name="zhuyin-only" content="([^"]*)">', src)
        only = htmlmod.unescape(mo.group(1)) if mo and mo.group(1) else None
    m = re.search(r'<meta name="zhuyin-lock" content="([\d,]*)">', src)
    locked = {int(x) for x in m.group(1).split(",") if x} if m else set()
    locked |= set(extra_lock or ())
    src = strip_marked(strip_marked(src, MARK_CSS), MARK_BTN)
    toks, units, script_hanzi = scan_html(src)
    text_units = [u for u in units if u.get("tok") is not None]
    for n in locked:
        if n < len(text_units):
            text_units[n]["lock"] = True
            text_units[n]["pre"] = True
    if auto:
        py.auto(units)
    items = collect_items(units, zf, py)
    if fixes:
        byid = {it["id"]: it for it in items}
        for i, j in fixes.items():
            if i not in byid:
                raise SystemExit(f"找不到 id {i}（報告共 {len(items)} 筆）")
            it = byid[i]
            if j >= zf.count(it["c"]) or j < -1:
                raise SystemExit(f"id {i}「{it['c']}」只有 0～{zf.count(it['c'])-1} 號讀音（-1 = 不標注音）")
            units[it["u"]]["vs"] = j
            units[it["u"]]["pre"] = True
            units[it["u"]]["lock"] = True
            units[it["u"]]["src"] = "手動指定"
        items = collect_items(units, zf, py)
    body = rebuild(toks, units)
    if online:
        fontbytes, b64 = b"", None
    else:
        fontbytes = subset_font(zf.path, body, "woff2")
        b64 = base64.b64encode(fontbytes).decode()
    lock = [str(n) for n, u in enumerate(text_units) if u.get("lock") and not u.get("vs")]
    final = inject(body, css_block(zf.family, b64, key, ",".join(lock), only), "head")
    if toggle:
        final = inject(final, TOGGLE, "body")
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        f.write(final)
    base = os.path.splitext(out_path)[0]
    need = write_report(base, units, items, zf, script_hanzi)
    proof_page(base, final, zf.family, b64, zf, py, items)
    if not quiet:
        out(f"完成：{out_path}（字型 {FONTS[key][1]}，" + (f"內嵌 {len(fontbytes)//1024} KB）" if b64 else "線上載入 Google Fonts，需要網路）"))
        out(f"破音字 {len(items)} 個，需 AI 判斷 {len(need)} 個 → {base}_破音字報告.md")
        out(f"校對頁：{base}_破音字校對.html")
    return items, need


def read_text(p):
    with open(p, "rb") as f:
        b = f.read()
    for enc in ("utf-8-sig", "cp950", "utf-16"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            pass
    return b.decode("utf-8", "replace")


def detect_key(src, default="zihi"):
    m = re.search(r'<meta name="zhuyin-font" content="(\w+)">', src)
    return m.group(1) if m else default


TEXT_TMPL = """<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
body{{margin:0;background:#fffdf7;color:#222}}
main{{max-width:880px;margin:24px auto;padding:0 20px 60px}}
h1{{font-size:40px;line-height:1.8;margin:0 0 10px}}
p{{font-size:32px;line-height:2.1;margin:0 0 14px}}
@media print{{main{{margin:0;max-width:none}}}}
</style></head><body><main>
{body}
</main></body></html>
"""


# ───────────────────────── Word ─────────────────────────

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_R = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
FONT_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/font"


def q(tag, ns=W):
    return "{%s}%s" % (ns, tag)


def is_cjk(s):
    return any("\u2e80" <= c <= "\u9fff" or "\u3000" <= c <= "\u303f" or "\uff00" <= c <= "\uffef" for c in s)


def docx_build(inp, outp, key, font_file=None, fixes=None, sandhi=False):
    from lxml import etree
    py = Poyin(sandhi)
    zf = ZhuyinFont(font_path(key, font_file))
    fam = zf.family
    zin = zipfile.ZipFile(inp)
    files = {n: zin.read(n) for n in zin.namelist()}
    parts = [n for n in files if re.match(r"word/(document|header\d*|footer\d*|footnotes|endnotes)\.xml$", n)]
    alltext = []
    all_units = []
    trees = {}
    for n in parts:
        root = etree.fromstring(files[n])
        trees[n] = root
        for p in root.iter(q("p")):
            units = []
            for t in p.iter(q("t")):
                for k, (c, raw, vs) in enumerate(split_units(t.text or "")):
                    units.append({"c": c, "raw": raw, "vs": vs, "tok": t, "k": k, "pre": vs is not None})
            if not units:
                continue
            py.auto(units)
            all_units.extend(units)
            all_units.append({"c": "\n", "tok": None})
    items = collect_items(all_units, zf, py)
    if fixes:
        byid = {it["id"]: it for it in items}
        for i, j in fixes.items():
            all_units[byid[i]["u"]]["vs"] = j
            all_units[byid[i]["u"]]["pre"] = True
        items = collect_items(all_units, zf, py)
    by_t = {}
    for u in all_units:
        if u.get("tok") is not None:
            by_t.setdefault(id(u["tok"]), (u["tok"], []))[1].append(u)
    for t, us in by_t.values():
        t.text = "".join(u["c"] + vs_char(u.get("vs")) for u in us)
        t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        alltext.append(t.text)
    # 字型：有中文的 run 都改成注音字型
    for n, root in trees.items():
        for r in root.iter(q("r")):
            txt = "".join(t.text or "" for t in r.iter(q("t")))
            if not is_cjk(txt):
                continue
            rpr = r.find(q("rPr"))
            if rpr is None:
                rpr = etree.Element(q("rPr"))
                r.insert(0, rpr)
            rf = rpr.find(q("rFonts"))
            if rf is None:
                rf = etree.Element(q("rFonts"))
                rpr.insert(0, rf)
            for a in ("asciiTheme", "hAnsiTheme", "eastAsiaTheme", "cstheme"):
                rf.attrib.pop(q(a), None)
            for a in ("ascii", "hAnsi", "eastAsia"):
                rf.set(q(a), fam)
            rf.set(q("hint"), "eastAsia")
        files[n] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    # 內嵌字型（混淆格式 .odttf）
    ttf = subset_font(zf.path, "".join(alltext), english_name_only=True)
    guid = str(uuid.uuid4()).upper()
    keyb = bytes.fromhex(guid.replace("-", ""))[::-1]
    data = bytearray(ttf)
    for i in range(32):
        data[i] ^= keyb[i % 16]
    fname = "word/fonts/zhuyin1.odttf"
    files[fname] = bytes(data)
    # content types
    ct = etree.fromstring(files["[Content_Types].xml"])
    if not any(e.get("Extension") == "odttf" for e in ct.findall(q("Default", CT_NS))):
        d = etree.SubElement(ct, q("Default", CT_NS))
        d.set("Extension", "odttf")
        d.set("ContentType", "application/vnd.openxmlformats-officedocument.obfuscatedFont")
    files["[Content_Types].xml"] = etree.tostring(ct, xml_declaration=True, encoding="UTF-8", standalone=True)
    # fontTable + rels
    ftn = "word/fontTable.xml"
    if ftn in files:
        ft = etree.fromstring(files[ftn])
    else:
        ft = etree.Element(q("fonts"), nsmap={"w": W, "r": R_NS})
        dr = etree.fromstring(files["word/_rels/document.xml.rels"])
        e = etree.SubElement(dr, q("Relationship", PKG_R))
        e.set("Id", "rIdZyFontTable")
        e.set("Type", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/fontTable")
        e.set("Target", "fontTable.xml")
        files["word/_rels/document.xml.rels"] = etree.tostring(dr, xml_declaration=True, encoding="UTF-8", standalone=True)
        if not any(x.get("PartName") == "/word/fontTable.xml" for x in ct.findall(q("Override", CT_NS))):
            o = etree.SubElement(ct, q("Override", CT_NS))
            o.set("PartName", "/word/fontTable.xml")
            o.set("ContentType", "application/vnd.openxmlformats-officedocument.wordprocessingml.fontTable+xml")
            files["[Content_Types].xml"] = etree.tostring(ct, xml_declaration=True, encoding="UTF-8", standalone=True)
    relsn = "word/_rels/fontTable.xml.rels"
    rels = etree.fromstring(files[relsn]) if relsn in files else etree.Element(q("Relationships", PKG_R), nsmap={None: PKG_R})
    for e in list(rels):
        if e.get("Target", "").startswith("fonts/zhuyin"):
            rels.remove(e)
    rel = etree.SubElement(rels, q("Relationship", PKG_R))
    rel.set("Id", "rIdZy1")
    rel.set("Type", FONT_REL)
    rel.set("Target", "fonts/zhuyin1.odttf")
    files[relsn] = etree.tostring(rels, xml_declaration=True, encoding="UTF-8", standalone=True)
    for f in ft.findall(q("font")):
        if f.get(q("name")) == fam:
            ft.remove(f)
    fe = etree.SubElement(ft, q("font"))
    fe.set(q("name"), fam)
    for tag, val in (("charset", "88"), ("family", "auto"), ("pitch", "variable")):
        x = etree.SubElement(fe, q(tag))
        x.set(q("val"), val)
    em = etree.SubElement(fe, q("embedRegular"))
    em.set(q("id", R_NS), "rIdZy1")
    em.set(q("fontKey"), "{" + guid + "}")
    em.set(q("subsetted"), "1")
    files[ftn] = etree.tostring(ft, xml_declaration=True, encoding="UTF-8", standalone=True)
    # settings: embedTrueTypeFonts
    sn = "word/settings.xml"
    if sn in files:
        st = etree.fromstring(files[sn])
        if st.find(q("embedTrueTypeFonts")) is None:
            before = ["writeProtection", "view", "zoom", "removePersonalInformation", "removeDateAndTime",
                      "doNotDisplayPageBoundaries", "displayBackgroundShape", "printPostScriptOverText",
                      "printFractionalCharacterWidth", "printFormsData"]
            pos = 0
            for i, ch in enumerate(st):
                if etree.QName(ch).localname in before:
                    pos = i + 1
            e = etree.Element(q("embedTrueTypeFonts"))
            st.insert(pos, e)
            if st.find(q("saveSubsetFonts")) is None:
                st.insert(pos + 1, etree.Element(q("saveSubsetFonts")))
        files[sn] = etree.tostring(st, xml_declaration=True, encoding="UTF-8", standalone=True)
    with zipfile.ZipFile(outp, "w", zipfile.ZIP_DEFLATED) as zo:
        order = ["[Content_Types].xml"] + [n for n in files if n != "[Content_Types].xml"]
        for n in order:
            zo.writestr(n, files[n])
    base = os.path.splitext(outp)[0]
    need = write_report(base, all_units, items, zf, 0)
    out(f"完成：{outp}（字型 {FONTS[key][1]}，內嵌 {len(ttf)//1024} KB）")
    out(f"破音字 {len(items)} 個，需 AI 判斷 {len(need)} 個 → {base}_破音字報告.md")
    out("提醒：對方用 Windows / Mac 版 Word 開啟即可看到注音；Google 文件、手機 App 看不到內嵌字型。")


# ───────────────────────── PDF ─────────────────────────

def find_browser():
    for p in [os.environ.get("CHROME_PATH"),
              r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
              "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
              "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
              "/opt/pw-browsers/chromium", shutil.which("google-chrome"), shutil.which("chromium"),
              shutil.which("chromium-browser"), shutil.which("msedge")]:
        if p and os.path.exists(p):
            return p
    return None


def to_pdf(inp, outp):
    tmp = tempfile.mkdtemp()
    src = read_text(inp)
    src = strip_marked(src, MARK_BTN)
    th = os.path.join(tmp, "page.html")
    with open(th, "w", encoding="utf-8") as f:
        f.write(src)
    b = find_browser()
    if not b:
        raise SystemExit("找不到 Edge 或 Chrome。可直接用瀏覽器開啟網頁 → 列印 → 另存為 PDF，字型一樣會內嵌。")
    url = "file:///" + os.path.abspath(th).replace("\\", "/").lstrip("/")
    cmd = [b, "--headless", "--disable-gpu", "--no-sandbox", "--no-pdf-header-footer",
           "--virtual-time-budget=8000", f"--print-to-pdf={os.path.abspath(outp)}", url]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180)
    out(f"完成：{outp}")


# ───────────────────────── 指令 ─────────────────────────

def parse_sets(sets):
    fx = {}
    for s in sets or []:
        s = re.sub(r"(\d)\s*[,，、]\s*(?=\d+\s*[,，、=])", r"\1;", s.strip())
        s = re.sub(r"(\d)\s*[,，、]\s*(?=\d+\s*=)", r"\1;", s)
        for part in re.split(r"[\s,，]+", s):
            if not part:
                continue
            m = re.match(r"^([\d;]+)\s*=\s*(-?\d+)$", part)
            if not m:
                raise SystemExit(f"格式錯誤：{part}（應為 id=讀音編號，例如 12=2 或 12;30;41=1）")
            for i in m.group(1).split(";"):
                if i:
                    fx[int(i)] = int(m.group(2))
    return fx


def main():
    ap = argparse.ArgumentParser(description="內嵌注音字體小幫手")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, outp=True):
        if outp:
            p.add_argument("-o", "--output")
        p.add_argument("--font", choices=FONTS.keys(), help="zihi 字嗨注音標楷（預設）、huninn 粉圓注音、iansui 芫荽注音")
        p.add_argument("--font-file", help="改用本機字型檔（離線時）")
        p.add_argument("--sandhi", action="store_true", help="「一」「不」標變調（預設標本調，同國小課本）")

    p = sub.add_parser("html"); p.add_argument("input"); common(p)
    p.add_argument("--no-toggle", action="store_true", help="不加「注音開／關」按鈕")
    p.add_argument("--no-auto", action="store_true", help="不自動判斷破音字")
    p.add_argument("--online", action="store_true", help="不內嵌，改從 Google Fonts 線上載入（檔案小，但要有網路）")
    p.add_argument("--only", help="只讓這些區塊顯示注音（CSS 選擇器，例如 \".passage, .question\"）；預設整頁")
    p = sub.add_parser("text"); p.add_argument("input"); common(p); p.add_argument("--title")
    p.add_argument("--online", action="store_true", help="不內嵌，改從 Google Fonts 線上載入")
    p = sub.add_parser("fix"); p.add_argument("input"); common(p)
    p.add_argument("--set", action="append", help="id=讀音編號，可多個，例如 --set 12=2 --set 30=1 或 --set \"12=2 30=1\"")
    p = sub.add_parser("merge"); p.add_argument("input"); p.add_argument("textfile"); common(p)
    p = sub.add_parser("export"); p.add_argument("input"); p.add_argument("-o", "--output")
    p = sub.add_parser("pdf"); p.add_argument("input"); p.add_argument("-o", "--output")
    p = sub.add_parser("docx"); p.add_argument("input"); common(p)
    p.add_argument("--set", action="append", help="依報告 id 改讀音（重新從原始 Word 產生）")
    p = sub.add_parser("readings"); p.add_argument("chars"); common(p, False)
    a = ap.parse_args()

    if a.cmd == "readings":
        zf = ZhuyinFont(font_path(a.font or "zihi", a.font_file))
        py = Poyin()
        for c in a.chars:
            rs = zf.readings(c)
            out(c, "  ".join(f"{j}={r}" for j, r in enumerate(rs)), "" if py.is_poly(c) else "（非破音字）")
        return

    if a.cmd == "export":
        src = read_text(a.input)
        toks, units, _ = scan_html(strip_marked(strip_marked(src, MARK_CSS), MARK_BTN))
        t = "".join(htmlmod.unescape(u["raw"]) if u.get("tok") is not None else "\n" for u in units)
        t = re.sub(r"[ \t]*\n[\s]*", "\n", t).strip() + "\n"
        o = a.output or os.path.splitext(a.input)[0] + "_文字.txt"
        with open(o, "w", encoding="utf-8") as f:
            f.write(t)
        out(f"完成：{o}（可貼到 https://buttaiwan.github.io/bpmfvs/ 校對，改好後用 merge 回填）")
        return

    if a.cmd == "pdf":
        to_pdf(a.input, a.output or os.path.splitext(a.input)[0] + ".pdf")
        return

    if a.cmd == "docx":
        o = a.output or os.path.splitext(a.input)[0] + "_注音.docx"
        docx_build(a.input, o, a.font or "zihi", a.font_file, parse_sets(a.set), a.sandhi)
        return

    if a.cmd == "text":
        t = read_text(a.input).strip()
        lines = [l.strip() for l in t.splitlines()]
        title = a.title or (lines[0] if lines else "注音文章")
        body = [f"<h1>{htmlmod.escape(title)}</h1>"] if not a.title else [f"<h1>{htmlmod.escape(title)}</h1>"]
        rest = lines[1:] if not a.title else lines
        body += [f"<p>{htmlmod.escape(l)}</p>" for l in rest if l]
        src = TEXT_TMPL.format(title=htmlmod.escape(title), body="\n".join(body))
        o = a.output or os.path.splitext(a.input)[0] + "_注音.html"
        build_html(src, o, a.font or "zihi", a.font_file, sandhi=a.sandhi, online=a.online or None)
        return

    src = read_text(a.input)
    if a.cmd == "html":
        o = a.output or os.path.splitext(a.input)[0] + "_注音.html"
        build_html(src, o, a.font or "zihi", a.font_file, toggle=not a.no_toggle, auto=not a.no_auto, sandhi=a.sandhi, only=a.only, online=a.online or None)
    elif a.cmd == "fix":
        key = a.font or detect_key(src)
        o = a.output or a.input
        toggle = MARK_BTN[0] in src
        build_html(src, o, key, a.font_file, toggle=toggle, fixes=parse_sets(a.set))
    elif a.cmd == "merge":
        key = a.font or detect_key(src)
        src0 = strip_marked(strip_marked(src, MARK_CSS), MARK_BTN)
        toks, units, _ = scan_html(src0)
        Poyin(a.sandhi).auto(units)
        pasted = split_units(read_text(a.textfile))
        A = [u["c"] for u in units if u.get("tok") is not None]
        Ai = [i for i, u in enumerate(units) if u.get("tok") is not None]
        B = [p[0] for p in pasted]
        sm = difflib.SequenceMatcher(None, A, B, autojunk=False)
        changed = 0
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag != "equal":
                continue
            for k in range(i2 - i1):
                u = units[Ai[i1 + k]]
                vs = pasted[j1 + k][2] or 0
                if (u.get("vs") or 0) != vs:
                    changed += 1
                u["vs"] = vs or None
                u["pre"] = True
                u["lock"] = True
        body = rebuild(toks, units)
        o = a.output or a.input
        tu = [u for u in units if u.get("tok") is not None]
        lk = {n for n, u in enumerate(tu) if u.get("lock")}
        build_html(body, o, key, a.font_file, toggle=MARK_BTN[0] in src, extra_lock=lk)
        out(f"已回填 {changed} 處讀音（對齊比例 {sm.ratio():.0%}）")


if __name__ == "__main__":
    main()
