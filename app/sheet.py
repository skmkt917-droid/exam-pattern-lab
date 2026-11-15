# -*- coding: utf-8 -*-
"""색인 마크다운을 주석 PDF 바이트로 만든다."""

import base64
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from catalog import fill_types
from errors import AppError

import fitz

ANSWER_LINE = re.compile(r"^\d+\)")
PATTERNS = (
    ("doubled", re.compile(r"^(\d+)\.\1\)"), 4),
    ("dotted", re.compile(r"^(\d+)\.(?!\d)"), 3),
    ("paren", re.compile(r"^(\d+)\)"), 2),
    ("bracket", re.compile(r"^\[(\d+)\]"), 2),
    ("bare", re.compile(r"^(\d{1,2})(?:\s+|(?=[\uac00-\ud7a3]))"), 1),
)
ITEM_SPLIT = re.compile(r"(?m)^###\s+\[([^\]]+)\]\s*$")
FIELD_LINE = re.compile(r"^-\s*([^:]+):\s*(.*)$")

def parse_items(text):
    parts = ITEM_SPLIT.split(text)
    preamble = parts[0]
    items = []
    for index in range(1, len(parts), 2):
        heading = parts[index].strip()
        body = parts[index + 1]
        body = re.split(r"(?m)^##\s+", body)[0]
        fields = {}
        current = None
        for line in body.splitlines():
            match = FIELD_LINE.match(line.strip())
            if match:
                current = match.group(1).strip()
                fields[current] = match.group(2).strip()
                continue
            extra = line.strip()
            if current and extra and not extra.startswith("#"):
                fields[current] = (fields[current] + "\n" + extra).strip()
        number = fields.get("문항 번호") or heading
        page_text = fields.get("PDF 페이지", "")
        page_match = re.search(r"\d+", page_text)
        items.append({
            "heading": heading,
            "number": number,
            "page": int(page_match.group(0)) if page_match else None,
            "printed": fields.get("인쇄 쪽수", ""),
            "parent": fields.get("상위 문항", ""),
            "type_id": fields.get("유형", ""),
            "structure": fields.get("대표 구조", ""),
            "support": fields.get("보조 구조", ""),
            "detail": fields.get("세부 코드", ""),
            "relation": fields.get("의존 관계", ""),
            "reason": fields.get("조건 부합 이유", ""),
            "answer": fields.get("정답", ""),
            "snippet": fields.get("식별 문구", ""),
            "body": fields.get("문항 본문", ""),
        })
    return preamble, items

def split_codes(raw):
    if not raw or raw.strip() in {"없음", "-", "없음."}:
        return []
    codes = []
    seen = set()
    for part in re.split(r"[,，/]+", raw):
        piece = part.strip()
        if not piece or piece in {"없음", "-"}:
            continue
        match = re.search(r"(PS\d+|R\d+|[A-F]\d+)", piece)
        code = match.group(1) if match else piece
        if code not in seen:
            seen.add(code)
            codes.append(code)
    return codes

def page_label(item):
    printed = re.search(r"\d+", item.get("printed") or "")
    printed_no = printed.group(0) if printed else ""
    pdf_no = str(item["page"]) if item.get("page") else ""
    if printed_no and pdf_no and printed_no != pdf_no:
        return f"p{pdf_no} · 인쇄 p{printed_no}"
    if pdf_no or printed_no:
        return f"p{pdf_no or printed_no}"
    return "p?"

def sort_key(item):
    match = re.search(r"\d+", item["number"])
    return (int(match.group(0)) if match else 10**6, item["number"])

def line_text(line):
    text = "".join(span["text"] for span in line["spans"]).strip()
    return text.translate(str.maketrans("０１２３４５６７８９", "0123456789"))

def iter_body_lines(page):
    height = page.rect.height
    top = height * 0.075
    bottom = height * 0.935
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            text = line_text(line)
            box = fitz.Rect(line["bbox"])
            if not text or box.y0 < top or box.y0 > bottom:
                continue
            yield text, box

def is_answer_key_page(page):
    short = 0
    long_stem = 0
    for text, _box in iter_body_lines(page):
        if not re.match(r"^\d{1,2}(?:[.)\]]|\s)", text):
            continue
        if len(text) <= 10:
            short += 1
        elif len(text) >= 18:
            long_stem += 1
    return long_stem == 0 and short >= 5

def dedupe_anchors(found):
    by_number = {}
    for anchor in found:
        current = by_number.get(anchor["no"])
        if current is None or anchor["y"] < current["y"]:
            by_number[anchor["no"]] = anchor
    return list(by_number.values())

def score_anchors(found):
    if not found:
        return -1
    numbers = sorted({anchor["no"] for anchor in found})
    if len(numbers) == 1:
        return 1.0
    span = numbers[-1] - numbers[0] + 1
    quality = len(numbers) / span
    if quality < 0.5:
        return -1
    return len(numbers) * quality

def find_anchors(page):
    if is_answer_key_page(page):
        return []
    grouped = {name: [] for name, _pattern, _priority in PATTERNS}
    for text, box in iter_body_lines(page):
        if box.x0 > page.rect.width * 0.86:
            continue
        for name, pattern, _priority in PATTERNS:
            match = pattern.match(text)
            if not match:
                continue
            number = int(match.group(1))
            if number < 1 or number > 60:
                continue
            grouped[name].append({
                "no": number,
                "x": box.x0,
                "y": box.y0,
                "rect": box,
            })
            break
    best = []
    best_score = -1
    best_priority = -1
    for name, _pattern, priority in PATTERNS:
        found = dedupe_anchors(grouped[name])
        score = score_anchors(found)
        if score > best_score or (score == best_score and score >= 0 and priority > best_priority):
            best = found
            best_score = score
            best_priority = priority
    return best if best_score >= 0 else []

def find_problem(document, number, preferred_page):
    order = []
    if preferred_page and 1 <= preferred_page <= document.page_count:
        order.append(preferred_page - 1)
    order.extend(index for index in range(document.page_count) if index not in order)
    for index in order:
        page = document[index]
        if is_answer_key_page(page):
            continue
        anchors = find_anchors(page)
        wanted = next((anchor for anchor in anchors if anchor["no"] == number), None)
        if wanted is not None:
            return page, wanted, anchors, index + 1
    return None, None, [], None

def clip_is_answer_key(page, rect):
    text = page.get_text("text", clip=rect) or ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 3:
        return False
    answers = sum(1 for line in lines if ANSWER_LINE.match(line) and len(line) <= 8)
    return answers >= 3 and answers >= len(lines) * 0.6

def image_folder_name(stem):
    cleaned = re.sub(r"[^0-9A-Za-z가-힣]+", "-", stem).strip("-")
    return cleaned or "exam"

def column_bands(anchors, page_width):
    if not anchors:
        return [(18, page_width - 14)]
    xs = sorted({round(anchor["x"], 1) for anchor in anchors})
    gap_limit = max(48, page_width * 0.08)
    clusters = [[xs[0]]]
    for x in xs[1:]:
        if x - clusters[-1][-1] > gap_limit:
            clusters.append([x])
        else:
            clusters[-1].append(x)
    if len(clusters) == 1:
        return [(18, page_width - 14)]
    bands = []
    for index, cluster in enumerate(clusters):
        left = 18 if index == 0 else cluster[0] - 12
        if index + 1 < len(clusters):
            right = clusters[index + 1][0] - 12
        else:
            right = page_width - 14
        if right <= left + 36:
            right = left + 36
        bands.append((left, right))
    return bands

def band_for(anchor_x, bands):
    for x0, x1 in bands:
        if x0 - 8 <= anchor_x <= x1 + 8:
            return (x0, x1)
    return min(bands, key=lambda band: abs(((band[0] + band[1]) / 2) - anchor_x))

def collect_elements(page):
    elements = []
    for block in page.get_text("dict")["blocks"]:
        rect = fitz.Rect(block["bbox"])
        if rect.width > 1 and rect.height > 1:
            elements.append(rect)
    for drawing in page.get_drawings():
        rect = drawing.get("rect")
        if not rect:
            continue
        rect = fitz.Rect(rect)
        if rect.width < 20 or rect.height < 8:
            continue
        if rect.width > page.rect.width * 0.85 or rect.height > 360:
            continue
        elements.append(rect)
    return elements

def span_rect(page, anchor, anchors):
    height = page.rect.height
    bands = column_bands(anchors, page.rect.width)
    x0, x1 = band_for(anchor["x"], bands)
    same_column = [other for other in anchors if band_for(other["x"], bands) == (x0, x1)]
    next_y = height * 0.935
    for other in same_column:
        if other["y"] > anchor["y"] + 8:
            next_y = min(next_y, other["y"] - 4)
    y0 = max(height * 0.08, anchor["y"] - 10)
    bottom = anchor["rect"].y1
    right_edge = x1
    left_edge = min(x0, anchor["x"] - 8)
    for rect in collect_elements(page):
        center = (rect.x0 + rect.x1) / 2
        if center < x0 - 6 or center > x1 + 6:
            continue
        if rect.y0 < y0 - 2 or rect.y0 >= next_y - 1:
            continue
        bottom = max(bottom, rect.y1)
        left_edge = min(left_edge, rect.x0 - 4)
        right_edge = max(right_edge, rect.x1 + 6)
    left_edge = max(x0, min(left_edge, anchor["x"] - 8))
    right_edge = min(x1, max(right_edge, anchor["rect"].x1 + 6))
    y1 = min(bottom + 12, next_y - 2)
    if y1 < y0 + 28:
        y1 = min(y0 + 28, next_y - 2)
    rect = fitz.Rect(left_edge, y0, right_edge, y1) & page.rect
    if rect.width < 8 or rect.height < 8:
        return None
    return rect

def clip_rect(page, anchor, anchors):
    rect = span_rect(page, anchor, anchors)
    if rect is None or rect.width < 40 or rect.height < 24:
        return None
    return rect

def typed_problem(page, rect):
    if rect is None:
        return ""
    text = page.get_text("text", clip=rect) or ""
    lines = []
    for line in text.splitlines():
        cleaned = re.sub(r"[ \t]+", " ", line).strip()
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines).strip()

def remember_text(item, page, rect):
    if rect is None or clip_is_answer_key(page, rect):
        return
    text = typed_problem(page, rect)
    if len(text) >= 8:
        item["typed"] = text

def image_name(number, index):
    match = re.fullmatch(r"\d+", number.strip())
    if match:
        return f"{int(number):02d}.png"
    safe = re.sub(r"[^\w\-]+", "-", number.strip()).strip("-") or str(index)
    return f"{index:02d}-{safe}.png"

def page_number(value, page_count):
    match = re.search(r"\d+", str(value or ""))
    if not match:
        return None
    number = int(match.group(0))
    if 1 <= number <= page_count:
        return number
    return None

def save_crop(item, index, page, rect, image_dir, page_no, source):
    filename = image_name(str(item["number"]), index)
    pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=rect, alpha=False)
    pixmap.save(image_dir / filename)
    item["image"] = filename
    item["crop_note"] = ""
    print(f"image {item['number']} page {page_no} {source} {rect.y0:.0f}-{rect.y1:.0f}")

def rect_from_ai_box(page, box):
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    ymin, xmin, ymax, xmax = [float(value) for value in box]
    if xmax <= xmin or ymax <= ymin:
        return None
    width = page.rect.width
    height = page.rect.height
    rect = fitz.Rect(
        xmin / 1000 * width,
        ymin / 1000 * height,
        xmax / 1000 * width,
        ymax / 1000 * height,
    )
    rect = fitz.Rect(
        rect.x0 - width * 0.012,
        rect.y0 - height * 0.01,
        rect.x1 + width * 0.012,
        rect.y1 + height * 0.01,
    ) & page.rect
    if rect.width < 40 or rect.height < 24:
        return None
    if rect.width > width * 0.96 and rect.height > height * 0.92:
        return None
    return rect

def parse_model_json(text):
    stripped = text.strip()
    stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
    stripped = re.sub(r"\s*```$", "", stripped)
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("좌표 JSON이 없습니다.")
    return json.loads(stripped[start:end + 1])

def request_ai_boxes(page, numbers, api_key, model, timeout, provider="GEMINI"):
    api_key = (api_key or "").strip()
    model = (model or "").strip()
    if not api_key or not model or not numbers:
        return {}
    pixmap = page.get_pixmap(matrix=fitz.Matrix(1.6, 1.6), alpha=False)
    encoded = base64.b64encode(pixmap.tobytes("png")).decode("ascii")
    number_text = ", ".join(str(number) for number in numbers)
    prompt = (
        "이 시험지 페이지에서 요청한 문항의 본문 영역만 고르세요. "
        "정답표, 쪽수, 머리말은 제외합니다. "
        "문항 번호, 문제 본문, 보기, 도형, 선택지가 모두 포함되어야 하고 다른 문항은 빠집니다. "
        "좌표는 이 이미지 기준 0부터 1000까지의 정수이고 [ymin, xmin, ymax, xmax] 순서입니다. "
        "JSON만 출력하세요. "
        '{"boxes":[{"number":1,"box":[ymin,xmin,ymax,xmax]}]} '
        f"요청 문항 번호: {number_text}"
    )
    payload = {
        "contents": [{
            "parts": [
                {"inline_data": {"mime_type": "image/png", "data": encoded}},
                {"text": prompt},
            ]
        }],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
        },
    }
    if provider == "GROK":
        from grok import read_boxes
        try:
            text = read_boxes(api_key, model, prompt, pixmap.tobytes("png"), timeout)
            data = parse_model_json(text)
        except Exception as error:
            print(f"ai boxes failed: {str(error).replace(api_key, '[REDACTED]')}", file=sys.stderr)
            return {}
        return _boxes_from(data)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "x-goog-api-key": api_key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace").replace(api_key, "[REDACTED]")
        print(f"ai boxes http {error.code}: {detail[:400]}", file=sys.stderr)
        return {}
    except Exception as error:
        print(f"ai boxes failed: {str(error).replace(api_key, '[REDACTED]')}", file=sys.stderr)
        return {}
    parsed = json.loads(body)
    parts = parsed.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    text = "\n".join(part.get("text", "") for part in parts if part.get("text"))
    try:
        data = parse_model_json(text)
    except (ValueError, json.JSONDecodeError):
        print("ai boxes json parse failed", file=sys.stderr)
        return {}
    return _boxes_from(data)

def _boxes_from(data):
    boxes = {}
    for entry in data.get("boxes", []):
        try:
            boxes[int(entry["number"])] = entry["box"]
        except (KeyError, TypeError, ValueError):
            continue
    return boxes

def crop_with_ai(document, pending, image_dir, api_key, model, timeout, provider="GEMINI"):
    groups = {}
    for index, item, target in pending:
        page_no = page_number(item.get("page"), document.page_count)
        if page_no is None:
            item["crop_note"] = "페이지 번호가 없어 잘라 내지 못했습니다."
            continue
        page = document[page_no - 1]
        if is_answer_key_page(page):
            item["crop_note"] = "정답표 쪽이라 문항으로 자르지 않았습니다."
            continue
        groups.setdefault(page_no, []).append((index, item, target))
    for page_no, group in groups.items():
        page = document[page_no - 1]
        boxes = request_ai_boxes(page, [target for _index, _item, target in group], api_key, model, timeout, provider)
        for index, item, target in group:
            rect = rect_from_ai_box(page, boxes.get(target))
            if rect is None or clip_is_answer_key(page, rect):
                item["crop_note"] = "문항 영역을 정하지 못했습니다."
                if rect is not None:
                    remember_text(item, page, rect)
                continue
            save_crop(item, index, page, rect, image_dir, page_no, "ai")

def crop_items(pdf_path, items, image_dir, api_key, model, timeout, provider="GEMINI"):
    document = fitz.open(pdf_path)
    pending = []
    try:
        image_dir.mkdir(parents=True, exist_ok=True)
        for index, item in enumerate(items, start=1):
            item["image"] = ""
            number_match = re.search(r"\d+", str(item.get("number") or ""))
            target = int(number_match.group(0)) if number_match else None
            if target is None:
                item["crop_note"] = "문항 번호가 없어 잘라 내지 못했습니다."
                continue
            preferred = page_number(item.get("page"), document.page_count)
            page, wanted, anchors, found_page = find_problem(document, target, preferred)
            rect = clip_rect(page, wanted, anchors) if wanted is not None else None
            if wanted is not None:
                remember_text(item, page, rect or span_rect(page, wanted, anchors))
            if rect is not None and not clip_is_answer_key(page, rect):
                save_crop(item, index, page, rect, image_dir, found_page, "local")
                continue
            pending.append((index, item, target))
        if pending:
            crop_with_ai(document, pending, image_dir, api_key, model, timeout, provider)
    finally:
        document.close()

def esc(value):
    return html.escape(str(value or ""), quote=True)

def tag_html(label, codes, names):
    if not codes:
        return ""
    parts = []
    for code in codes:
        name = names.get(code, "").strip()
        parts.append(f"{code} {name}".strip() if name and name != code else code)
    return (
        f"<span class=\"tag\"><span class=\"k\">{esc(label)}</span> "
        f"{esc(', '.join(parts))}</span>"
    )

def plain_answer(text):
    text = (text or "").strip()
    text = re.sub(r"^(정답|답)\s*[:：]?\s*", "", text)
    text = text.replace("$", "")
    text = re.sub(r"\\frac\s*\{([^{}]+)\}\s*\{([^{}]+)\}", r"\1/\2", text)
    text = re.sub(r"\\[a-zA-Z]+", "", text)
    text = text.replace("{", "").replace("}", "")
    return re.sub(r"\s+", " ", text).strip()

def type_html(item):
    lines = []
    for label, key in (
        ("과목", "semester"),
        ("대단원", "big_chapter"),
        ("중단원", "middle_chapter"),
        ("소단원", "little_chapter"),
        ("유형", "topic"),
        ("세부유형", "subtopic"),
    ):
        value = (item.get(key) or "").strip()
        if value:
            lines.append(f"<p class=\"type\"><span class=\"k\">{esc(label)}</span> {esc(value)}</p>")
    if not lines:
        return ""
    return "<div class=\"types\">" + "".join(lines) + "</div>"

def answer_tag(answer):
    text = plain_answer(answer)
    if not text or text in {"없음", "-", "없음."}:
        return ""
    return f"<span class=\"tag answer\"><span class=\"k\">답</span> {esc(text)}</span>"

def problem_label(item):
    number = str(item.get("number") or "").strip() or "?"
    if "번" not in number:
        number = f"{number}번"
    place = f"{item.get('page_label') or page_label(item)} {number}"
    source = (item.get("source") or "").strip()
    if source:
        return f"{source} · {place}"
    return place

def problem_text(item):
    for key in ("typed", "body", "snippet"):
        text = (item.get(key) or "").strip()
        if len(text) >= 8 and text not in {"없음", "-", "없음."}:
            return text
    return ""

def text_html(text):
    return "<br>".join(esc(line) for line in text.splitlines() if line.strip())

def group_code(item):
    codes = item.get("structure") or []
    return codes[0] if codes else ""

def group_order(code):
    match = re.search(r"\d+", code or "")
    return (code == "", int(match.group(0)) if match else 10**6, code)

def render(items, names, title):
    prepared = []
    for item in items:
        structure = split_codes(item.get("structure"))[:1]
        prepared.append({
            **item,
            "structure": structure,
            "support": [code for code in split_codes(item.get("support")) if code not in structure],
            "detail": split_codes(item.get("detail")),
            "relation": split_codes(item.get("relation"))[:1],
            "page_label": page_label(item),
        })
    grouped = {}
    for item in prepared:
        grouped.setdefault(group_code(item), []).append(item)

    cards_by_heading = []
    for code in sorted(grouped, key=group_order):
        members = sorted(grouped[code], key=lambda item: (item.get("source") or "", item.get("page") or 0, sort_key(item)))
        name = names.get(code, "").strip()
        heading = f"{code} {name}".strip() or "분류 없음"
        cards = []
        for item in members:
            number = esc(item["number"])
            where = f"<p class=\"where\">{esc(problem_label(item))}</p>"
            typed_as = type_html(item)
            if item.get("image"):
                folder = item.get("image_stem") or "exam"
                image_path = f"images/{folder}/{item['image']}".replace("\\", "/")
                figure = f"<img src=\"{esc(image_path)}\" alt=\"{number}번\">"
            else:
                typed = problem_text(item)
                if typed:
                    figure = f"<div class=\"stem\">{text_html(typed)}</div>"
                else:
                    note = item.get("crop_note") or "원본에서 이 문항을 잘라 내지 못했습니다."
                    figure = f"<p class=\"missing\">{esc(note)}</p>"
            tags = answer_tag(item.get("answer")) + "".join(
                tag_html(label, item[key], names)
                for label, key in (("대표", "structure"), ("보조", "support"), ("세부", "detail"), ("의존", "relation"))
            )
            reason = (item.get("reason") or "").strip()
            note = f"<p class=\"note\">{esc(reason)}</p>" if reason else ""
            cards.append(
                "<article>"
                f"{where}"
                f"{figure}"
                "<aside class=\"comment\">"
                f"<div class=\"tags\">{tags}</div>"
                f"{note}"
                f"{typed_as}"
                "</aside>"
                "</article>"
            )
        cards_by_heading.append((heading, cards))

    page_size = 4
    pages = []
    chunks = []
    for heading, cards in cards_by_heading:
        for start in range(0, len(cards), page_size):
            chunks.append((heading, cards[start:start + page_size]))
    chunks = chunks or [("분류 없음", [])]
    total_pages = len(chunks)
    for index, (heading, chunk) in enumerate(chunks, start=1):
        pages.append(
            "<section class=\"page\">"
            f"<h1>{esc(heading)}</h1>"
            "<div class=\"cells\">"
            f"{''.join(chunk)}"
            "</div>"
            f"<p class=\"pager\">- {index} / {total_pages} -</p>"
            "</section>"
        )

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title or "annotated")}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    background: #fff;
    color: #000;
    font-family: "Malgun Gothic", "Apple SD Gothic Neo", sans-serif;
  }}
  .sheet {{ max-width: 1080px; margin: 0 auto; padding: 40px 72px 80px; }}
  .page, .cells {{ display: contents; }}
  h1 {{
    margin: 0 0 22px;
    padding-bottom: 8px;
    border-bottom: 1px solid #000;
    font-size: 22px;
    font-weight: 700;
    text-align: center;
    letter-spacing: 0;
    grid-column: 1 / -1;
  }}
  .grid {{
    position: relative;
    display: grid;
    grid-template-columns: 1fr 1fr;
    column-gap: 72px;
    row-gap: 160px;
    align-items: start;
  }}
  .grid::before {{
    content: "";
    position: absolute;
    top: 0;
    bottom: 0;
    left: 50%;
    border-left: 1px dashed #000;
  }}
  article img {{ display: block; width: 100%; height: auto; }}
  .where {{ margin: 0 0 8px; font-size: 12px; font-weight: 700; line-height: 1.3; }}
  .types {{ margin-top: 10px; }}
  .type {{ margin: 0 0 5px; font-size: 10px; line-height: 1.45; }}
  .type .k {{ margin-right: 8px; }}
  .stem {{ margin: 0; font-size: 12px; line-height: 1.45; }}
  .missing {{ margin: 0; font-size: 12px; }}
  .comment {{
    margin-top: 16px;
    padding-top: 12px;
    border-top: 1px solid #000;
  }}
  .tags {{
    margin: 0 0 10px;
    line-height: 1.45;
  }}
  .tag {{
    display: inline-block;
    margin: 0 3px 2px 0;
    border: 1px solid #000;
    padding: 0 3px;
    font-size: 9px;
    line-height: 1.35;
  }}
  .tag.answer {{
    color: #d00000;
    border-color: #d00000;
  }}
  .k {{ font-weight: 700; }}
  .note {{
    margin: 0 0 12px;
    padding: 1px 0 1px 10px;
    border-left: 2px solid #000;
    font-size: 9px;
    line-height: 1.45;
  }}
  footer {{
    margin-top: 28px;
    padding-top: 8px;
    border-top: 1px solid #000;
    font-size: 12px;
    grid-column: 1 / -1;
  }}
  footer p {{ margin: 0 0 4px; }}
  .pager {{
    grid-column: 1 / -1;
    margin: 28px 0 40px;
    padding-top: 8px;
    border-top: 1px solid #000;
    text-align: center;
    font-size: 14px;
  }}
  @media (max-width: 760px) {{
    .sheet {{ padding: 24px 20px 48px; }}
    .grid {{ grid-template-columns: 1fr; row-gap: 120px; }}
    .grid::before {{ display: none; }}
  }}
  @page {{ size: A4; margin: 10mm 14mm; }}
  @media print {{
    * {{ print-color-adjust: exact; -webkit-print-color-adjust: exact; }}
    .sheet {{ max-width: none; padding: 0; }}
    .grid {{ display: block; }}
    .grid::before {{ display: none; }}
    .page {{
      display: flex;
      flex-direction: column;
      height: 274mm;
      break-after: page;
      page-break-after: always;
    }}
    .page:last-child {{
      break-after: auto;
      page-break-after: auto;
    }}
    h1 {{ margin: 0 0 4mm; padding-bottom: 2mm; border-bottom: 1px solid #000; font-size: 20px; }}
    .cells {{
      position: relative;
      display: grid;
      flex: 1 1 auto;
      min-height: 0;
      grid-template-columns: 1fr 1fr;
      grid-template-rows: 1fr 1fr;
      align-items: start;
      column-gap: 16mm;
      row-gap: 12mm;
    }}
    .cells::before {{
      content: "";
      position: absolute;
      top: 0;
      bottom: 0;
      left: 50%;
      border-left: 1px dashed #000;
    }}
    article {{
      display: block;
      height: auto;
      max-height: 100%;
      overflow: hidden;
    }}
    article img {{
      display: block;
      width: 100%;
      height: auto;
      max-height: 62mm;
      object-fit: contain;
      object-position: top center;
    }}
    .where {{ margin: 0 0 2mm; font-size: 10px; }}
    .types {{ margin-top: 2.5mm; }}
    .type {{ margin: 0 0 1.4mm; font-size: 8px; line-height: 1.4; }}
    .type .k {{ margin-right: 2mm; }}
    .stem {{ font-size: 10px; line-height: 1.35; max-height: 70mm; overflow: hidden; }}
    .comment {{ margin-top: 4mm; padding-top: 3mm; }}
    .tags {{ margin-bottom: 2.5mm; }}
    .tag {{ margin: 0 2px 1.5px 0; padding: 0 2px; font-size: 8px; line-height: 1.35; }}
    .note {{ margin: 0 0 2.5mm; padding: 0.4mm 0 0.4mm 2.5mm; font-size: 8px; line-height: 1.4; }}
    .pager {{ margin: 3mm 0 0; padding-top: 2mm; border-top: 1px solid #000; font-size: 12px; }}
  }}
</style>
</head>
<body>
<main class="sheet">
<div class="grid">
{''.join(pages)}
</div>
</main>
</body>
</html>
"""

def find_browsers():
    names = (
        ("Microsoft", "Edge", "Application", "msedge.exe"),
        ("Google", "Chrome", "Application", "chrome.exe"),
    )
    roots = [
        os.environ.get("PROGRAMFILES(X86)", ""),
        os.environ.get("PROGRAMFILES", ""),
        os.environ.get("LOCALAPPDATA", ""),
    ]
    found = []
    for root in roots:
        if not root:
            continue
        for parts in names:
            path = Path(root, *parts)
            if path.is_file() and path not in found:
                found.append(path)
    for command in ("msedge", "chrome", "google-chrome"):
        located = shutil.which(command)
        if located:
            path = Path(located)
            if path not in found:
                found.append(path)
    return found

def html_to_pdf(html_path):
    html_path = Path(html_path).resolve()
    pdf_path = html_path.with_suffix(".pdf")
    browsers = find_browsers()
    if not browsers:
        raise RuntimeError("HTML을 PDF로 바꿀 브라우저가 없습니다. Edge 또는 Chrome이 필요합니다.")
    errors = []
    for browser in browsers:
        tmp_dir = Path(tempfile.mkdtemp(prefix="epl-pdf-"))
        tmp_pdf = tmp_dir / "sheet.pdf"
        cmd = [
            str(browser),
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--no-pdf-header-footer",
            f"--user-data-dir={tmp_dir / 'profile'}",
            "--window-size=1280,1800",
            f"--print-to-pdf={tmp_pdf}",
            html_path.as_uri(),
        ]
        try:
            completed = subprocess.run(cmd, capture_output=True, timeout=90, check=False)
            if tmp_pdf.is_file() and tmp_pdf.stat().st_size >= 1000:
                if pdf_path.exists():
                    pdf_path.unlink()
                shutil.move(str(tmp_pdf), pdf_path)
                return pdf_path
            detail = (completed.stderr or completed.stdout or b"").decode("utf-8", "replace")
            detail = " ".join(detail.split())[-240:]
            errors.append(f"{browser.name}: {detail or '파일이 생성되지 않았습니다.'}")
        except subprocess.TimeoutExpired:
            errors.append(f"{browser.name}: 시간 초과")
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
    raise RuntimeError("PDF를 만들지 못했습니다. " + " | ".join(errors))

def build_combined(entries, code_names, api_key, vision_model, vision_timeout, provider="GEMINI", catalog=None, complete=None, log=None):
    collected = []
    with tempfile.TemporaryDirectory(prefix="epl-sheet-") as temp_name:
        root = Path(temp_name)
        for index, (pdf_path, index_text) in enumerate(entries, start=1):
            pdf_path = Path(pdf_path)
            if not pdf_path.is_file():
                raise AppError(f"PDF가 없습니다: {pdf_path.name}")
            _preamble, items = parse_items(index_text)
            if not items:
                continue
            if catalog is not None:
                fill_types(items, catalog, complete, log)
            folder = f"{index:02d}-{image_folder_name(pdf_path.stem)}"
            image_dir = root / "images" / folder
            crop_items(pdf_path, items, image_dir, api_key, vision_model, vision_timeout, provider)
            for item in items:
                item["source"] = pdf_path.name
                item["image_stem"] = folder
                collected.append(item)
        if not collected:
            raise AppError("색인에서 문항을 찾지 못했습니다.")
        sources = []
        for pdf_path, _index_text in entries:
            name = Path(pdf_path).name
            if name not in sources:
                sources.append(name)
        title = sources[0] if len(sources) == 1 else "annotated"
        html_path = root / "annotated.html"
        html_path.write_text(render(collected, code_names, title), encoding="utf-8-sig")
        try:
            sheet_pdf = html_to_pdf(html_path)
        except RuntimeError as exc:
            raise AppError(str(exc)) from None
        data = sheet_pdf.read_bytes()
    if len(data) < 1000:
        raise AppError("PDF를 만들지 못했습니다.")
    return data

def build_sheet(pdf_path, index_text, code_names, api_key, vision_model, vision_timeout):
    return build_combined(
        [(pdf_path, index_text)],
        code_names,
        api_key,
        vision_model,
        vision_timeout,
    )
