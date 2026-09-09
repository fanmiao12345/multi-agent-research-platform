# -*- coding: utf-8 -*-
"""
harness/ingest/html_extract.py —— 网页正文提取（S2-02 的"正文"部分）

用标准库 HTMLParser 完成，避免为 MVP 引入额外解析依赖：
- 去掉 script/style/noscript/template 内容；
- 块级元素之间按段落换行；标题 h1~h6 转成 Markdown # 前缀（段落定位仍走
  storage.sources.split_segments 的标题识别，形成统一定位口径）；
- <title> 与首个 h1 提供标题候选。

诚实边界：不做 JS 渲染、复杂表格语义、嵌套列表/代码块高保真；
复杂页面提取质量按 S6 真实样本再评估，必要时再引入专门依赖（计划 9.1）。
"""
from __future__ import annotations

import re
from html.parser import HTMLParser

_SKIP_TAGS = {"script", "style", "noscript", "template", "title", "svg", "math"}
_BLOCK_TAGS = {"p", "div", "section", "article", "aside", "main", "header", "footer",
               "nav", "blockquote", "pre", "table", "tr", "ul", "ol", "li", "dl",
               "dt", "dd", "form", "fieldset", "figure", "figcaption", "address",
               "details", "summary", "hr", "br"}
_HEADING_TAGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}


class _Extractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.titles: list[str] = []
        self.paragraphs: list[str] = []
        self._skip_depth = 0
        self._title_depth = 0
        self._in_pre = 0
        self._buffer: list[str] = []
        self._heading = 0
        self._block_open = False   # 段落间需要空行分隔
        self._last_was_heading = False

    def _flush(self):
        text = "".join(self._buffer).strip()
        self._buffer = []
        if not text:
            self._block_open = True
            return
        if self._heading:
            line = "#" * self._heading + " " + text
            self._heading = 0
        else:
            line = text
        if self.paragraphs and self._block_open and not self._last_was_heading:
            self.paragraphs.append("")
        elif self._last_was_heading and self.paragraphs and self.paragraphs[-1]:
            self.paragraphs.append("")
        self.paragraphs.append(line)
        self._block_open = False
        self._last_was_heading = line.startswith("#")

    # -- 标签处理 ----------------------------------------------------------
    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            if tag == "title":
                self._title_depth += 1
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "pre":
            self._in_pre += 1
        if tag == "br":
            self._flush()
            self._block_open = True
            return
        if tag in _HEADING_TAGS:
            self._flush()
            self._heading = _HEADING_TAGS[tag]
            return
        if tag in _BLOCK_TAGS:
            self._flush()
            self._block_open = True

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            if tag == "title" and self._title_depth:
                self._title_depth -= 1
            return
        if self._skip_depth:
            return
        if tag == "pre":
            self._in_pre = max(0, self._in_pre - 1)
        if tag in _HEADING_TAGS:
            self._flush()
            self._last_was_heading = True
            return
        if tag in _BLOCK_TAGS:
            self._flush()
            self._block_open = True

    def handle_data(self, data):
        if self._skip_depth and not self._title_depth:
            return
        if self._title_depth:
            if data.strip():
                self.titles.append(" ".join(data.split()))
            return
        if not data.strip():
            return
        if self._in_pre:
            self._buffer.append(data)
        else:
            self._buffer.append(" ".join(data.split()) + " ")

    def finish(self):
        self._flush()
        return [p for p in self.paragraphs if p != ""]


def extract_html(raw: str) -> dict:
    """HTML 字符串 → {title, text, format='md'}；text 为 Markdown 化正文。"""
    parser = _Extractor()
    try:
        parser.feed(raw)
        parser.close()
    except Exception:  # noqa: BLE001 —— 解析器对畸形 HTML 尽力而为
        pass
    paragraphs = parser.finish()
    title = " ".join(parser.titles).strip() if parser.titles else ""
    if not title:
        for line in paragraphs:
            if line.startswith("#"):
                title = line.lstrip("# ").strip()
                break
    return {"title": title[:200], "text": "\n\n".join(paragraphs), "format": "md"}


def extract_document(raw: bytes, content_type: str, charset: str = "") -> dict:
    """按内容类型提取正文。返回 {title,text,format,encoding,note}。

    支持 text/html、application/xhtml+xml、text/plain、text/markdown；
    其他类型由调用方先行拒绝。
    """
    main_type = (content_type or "").split(";", 1)[0].strip().lower()
    if not main_type:
        head = raw[:1024].lstrip()
        main_type = "text/html" if head.startswith(b"<") else "text/plain"
    declared = charset or ""
    text = _decode(raw, declared)
    note = ""
    if main_type in ("text/html", "application/xhtml+xml"):
        result = extract_html(text)
        result["encoding"] = declared or "utf-8/replace"
        return result
    body = text
    if main_type in ("text/markdown", "application/markdown"):
        fmt = "md"
    else:  # text/plain 与其他文本型
        fmt = "md" if re.search(r"^ {0,3}#{1,6}\s", body, re.MULTILINE) else "txt"
    first = next((ln for ln in body.splitlines() if ln.strip()), "")
    title = re.sub(r"^ {0,3}#{1,6}\s+", "", first)[:200] if first else ""
    return {"title": title, "text": body, "format": fmt,
            "encoding": declared or "utf-8/replace", "note": note}


def _decode(raw: bytes, charset: str) -> str:
    if charset:
        import codecs
        try:
            return raw.decode(codecs.lookup(charset).name, errors="replace")
        except LookupError:
            pass
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")
