# -*- coding: utf-8 -*-
"""공통수학2 유형 목록을 읽고, 색인 문항에 유형과 세부유형을 붙인다."""

import json
import re
from dataclasses import dataclass
from pathlib import Path

from errors import AppError


TYPES_FILE = Path(__file__).resolve().parent / "data" / "types.json"
ROOT_DIR = TYPES_FILE.parent.parent.parent
TYPE_ID = re.compile(r"(?<![A-Za-z])T(\d+)", re.I)
LITTLE_ID = re.compile(r"(?<![A-Za-z])L(\d+)", re.I)
SOURCE_KEY = re.compile(r"SUB_TOPIC__\d+")
CHUNK_LIMIT = 48000
BODY_LIMIT = 1500
TYPE_FIELDS = (
    "semester",
    "big_chapter",
    "middle_chapter",
    "little_chapter",
    "topic",
    "subtopic",
    "source_key",
    "type_code",
)


@dataclass
class TypeEntry:
    source_key: str
    semester: str
    big_chapter: str
    middle_chapter: str
    little_chapter: str
    topic: str
    subtopic: str


@dataclass
class TopicGroup:
    id: str
    semester: str
    big_chapter: str
    middle_chapter: str
    little_chapter: str
    topic: str
    entries: tuple


@dataclass
class LittleGroup:
    id: str
    semester: str
    big_chapter: str
    middle_chapter: str
    little_chapter: str
    topics: tuple


@dataclass
class Catalog:
    topics: tuple
    littles: tuple
    by_topic_id: dict
    by_little_id: dict
    by_source_key: dict
    by_topic_name: dict
    prompt_text: str


def display_path(path):
    path = Path(path)
    try:
        return path.resolve().relative_to(ROOT_DIR).as_posix()
    except ValueError:
        return path.as_posix()


def load_catalog():
    if not TYPES_FILE.is_file():
        raise AppError(f"유형 데이터가 없습니다: {display_path(TYPES_FILE)}")
    try:
        rows = json.loads(TYPES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise AppError(f"유형 데이터를 읽지 못했습니다: {display_path(TYPES_FILE)}") from error
    if not isinstance(rows, list) or not rows:
        raise AppError(f"유형 데이터가 비어 있습니다: {display_path(TYPES_FILE)}")

    entries = []
    seen_keys = set()
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise AppError(f"유형 데이터 {index}번째 항목이 올바르지 않습니다.")
        entry = _entry(row, index)
        if entry.source_key in seen_keys:
            raise AppError(f"유형 키가 겹칩니다: {entry.source_key}")
        seen_keys.add(entry.source_key)
        entries.append(entry)

    topics = []
    by_path = {}
    for entry in entries:
        path = (entry.semester, entry.big_chapter, entry.middle_chapter, entry.little_chapter, entry.topic)
        group = by_path.get(path)
        if group is None:
            group = TopicGroup(f"T{len(topics) + 1:03d}", *path, ())
            by_path[path] = group
            topics.append(group)
        group.entries = group.entries + (entry,)

    littles = []
    by_little_path = {}
    for topic in topics:
        path = (topic.semester, topic.big_chapter, topic.middle_chapter, topic.little_chapter)
        little = by_little_path.get(path)
        if little is None:
            little = LittleGroup(f"L{len(littles) + 1:02d}", *path, ())
            by_little_path[path] = little
            littles.append(little)
        little.topics = little.topics + (topic,)

    by_topic_id = {topic.id: topic for topic in topics}
    by_little_id = {little.id: little for little in littles}
    by_source_key = {entry.source_key: entry for entry in entries}
    by_topic_name = {}
    for topic in topics:
        by_topic_name.setdefault(topic.topic, []).append(topic)
    prompt_text = "\n".join(
        " | ".join((
            topic.id,
            _cell(topic.semester),
            _cell(topic.big_chapter),
            _cell(topic.middle_chapter),
            _cell(topic.little_chapter),
            _cell(topic.topic),
        ))
        for topic in topics
    )
    return Catalog(
        tuple(topics),
        tuple(littles),
        by_topic_id,
        by_little_id,
        by_source_key,
        by_topic_name,
        prompt_text,
    )


def parse_type_id(raw):
    match = TYPE_ID.search(raw or "")
    if not match:
        return ""
    return f"T{int(match.group(1)):03d}"


def assign_topic_ids(items, catalog, complete, log=None):
    if not items or complete is None:
        return
    try:
        _assign_littles(items, catalog, complete)
        _assign_topics(items, catalog, complete)
    except AppError as error:
        if log is not None:
            log("유형 일치 실패: " + str(error))


def fill_types(items, catalog, complete=None, log=None):
    pending = []
    for item in items:
        topic = _topic_for(item.get("type_id"), catalog)
        if topic is None:
            little = catalog.by_little_id.get(item.get("little_id") or "")
            if little is None:
                _clear_type(item)
            else:
                _apply_little(item, little)
            continue
        _apply_topic(item, topic)
        if len(topic.entries) == 1:
            _apply_entry(item, topic.entries[0])
        else:
            pending.append(item)
    if pending and complete is not None:
        _pick_subtopics(pending, catalog, complete, log)
    elif pending and log is not None:
        log(f"세부유형: 후보가 여러 개인 문항 {len(pending)}개는 아직 고르지 못했습니다.")
    total = len(items)
    topic_hits = sum(1 for item in items if item.get("topic"))
    sub_hits = sum(1 for item in items if item.get("source_key"))
    if log is not None:
        log(f"유형: {topic_hits}/{total}문항에 기입했습니다. 세부유형: {sub_hits}/{total}")
        missed = [str(item.get("number") or "?") for item in items if not item.get("topic")]
        if missed:
            shown = ", ".join(missed[:12])
            if len(missed) > 12:
                shown += f" 외 {len(missed) - 12}개"
            log(f"유형 없음: {shown}")
    return topic_hits, sub_hits


def _entry(row, index):
    try:
        source_key = str(row["sourceKey"]).strip()
        semester = str(row["semester"]).strip()
        big = str(row["bigChapter"]).strip()
        middle = str(row["middleChapter"]).strip()
        little = str(row["littleChapter"]).strip()
        topic = str(row["topic"]).strip()
        subtopic = str(row["subTopic"]).strip()
    except KeyError as error:
        raise AppError(f"유형 데이터 {index}번째 항목에 {error.args[0]} 값이 없습니다.") from None
    if not source_key or not topic or not subtopic:
        raise AppError(f"유형 데이터 {index}번째 항목의 유형이 비어 있습니다.")
    return TypeEntry(source_key, semester, big, middle, little, topic, subtopic)


def _cell(value):
    return " ".join(str(value or "").replace("|", "/").split())


def _topic_for(raw, catalog):
    type_id = parse_type_id(raw)
    if type_id and type_id in catalog.by_topic_id:
        return catalog.by_topic_id[type_id]
    name = " ".join(str(raw or "").split())
    found = catalog.by_topic_name.get(name) or []
    if len(found) == 1:
        return found[0]
    return None


def _clear_type(item):
    for key in TYPE_FIELDS:
        item[key] = ""


def _apply_little(item, little):
    item["type_code"] = ""
    item["semester"] = little.semester
    item["big_chapter"] = little.big_chapter
    item["middle_chapter"] = little.middle_chapter
    item["little_chapter"] = little.little_chapter
    item["topic"] = ""
    item["subtopic"] = ""
    item["source_key"] = ""


def _apply_topic(item, topic):
    item["type_code"] = topic.id
    item["semester"] = topic.semester
    item["big_chapter"] = topic.big_chapter
    item["middle_chapter"] = topic.middle_chapter
    item["little_chapter"] = topic.little_chapter
    item["topic"] = topic.topic
    item["subtopic"] = ""
    item["source_key"] = ""


def _apply_entry(item, entry):
    item["semester"] = entry.semester
    item["big_chapter"] = entry.big_chapter
    item["middle_chapter"] = entry.middle_chapter
    item["little_chapter"] = entry.little_chapter
    item["topic"] = entry.topic
    item["subtopic"] = entry.subtopic
    item["source_key"] = entry.source_key


def _assign_littles(items, catalog, complete):
    menu = "\n".join(
        " | ".join((
            little.id,
            _cell(little.semester),
            _cell(little.big_chapter),
            _cell(little.middle_chapter),
            _cell(little.little_chapter),
        ))
        for little in catalog.littles
    )
    for chunk in _chunks(items, lambda group: _little_prompt(group, menu, items)):
        text = complete(_little_prompt(chunk, menu, items))
        sections = _bind_sections(_sections(text), chunk)
        for item, section in zip(chunk, sections):
            little_id = _pick_code(
                section,
                re.compile(r"소단원\s*[:：]\s*(.+)$", re.M),
                LITTLE_ID,
                lambda number: f"L{int(number):02d}",
                catalog.by_little_id,
            )
            item["little_id"] = little_id


def _assign_topics(items, catalog, complete):
    grouped = {}
    for item in items:
        little = catalog.by_little_id.get(item.get("little_id") or "")
        if little is None:
            continue
        grouped.setdefault(little.id, []).append(item)
    for little_id, group in grouped.items():
        little = catalog.by_little_id[little_id]
        allowed = {topic.id: topic for topic in little.topics}
        for chunk in _chunks(group, lambda pending: _topic_prompt(pending, little, items)):
            text = complete(_topic_prompt(chunk, little, items))
            sections = _bind_sections(_sections(text), chunk)
            for item, section in zip(chunk, sections):
                item["type_id"] = _pick_code(
                    section,
                    re.compile(r"(?<!세부)유형\s*[:：]\s*(.+)$", re.M),
                    TYPE_ID,
                    lambda number: f"T{int(number):03d}",
                    allowed,
                )


def _little_prompt(chunk, menu, items):
    blocks = [
        "각 문항의 본문을 읽고, 아래 소단원 번호 하나만 고르세요.",
        "목록에 없는 번호를 만들지 마세요. 맞는 소단원이 없으면 없음이라고 적으세요.",
        "출력은 마크다운만 작성하세요. ### [순번] 다음 줄에 - 소단원: 과 목록의 번호를 적으세요.",
        "순번은 요청에 적힌 번호 그대로 쓰세요.",
        "",
        "소단원 목록:",
        menu,
    ]
    _append_items(blocks, chunk, items)
    return "\n".join(blocks)


def _topic_prompt(chunk, little, items):
    menu = "\n".join(f"{topic.id} | {_cell(topic.topic)}" for topic in little.topics)
    blocks = [
        "각 문항의 본문을 읽고, 아래 유형 번호 하나만 고르세요.",
        "목록에 없는 번호를 만들지 마세요. 맞는 유형이 없으면 없음이라고 적으세요.",
        "출력은 마크다운만 작성하세요. ### [순번] 다음 줄에 - 유형: 과 목록의 번호를 적으세요.",
        "순번은 요청에 적힌 번호 그대로 쓰세요.",
        "",
        "소단원: " + " > ".join((
            little.semester,
            little.big_chapter,
            little.middle_chapter,
            little.little_chapter,
        )),
        "유형 목록:",
        menu,
    ]
    _append_items(blocks, chunk, items)
    return "\n".join(blocks)


def _append_items(blocks, chunk, items):
    for index, item in enumerate(chunk, start=1):
        blocks.extend((
            "",
            f"### [{index}]",
            f"문항 번호: {item.get('number') or ''}",
            "본문:",
            _body(item, items),
        ))


def _body(item, items):
    own = " ".join((item.get("body") or "").split())
    parent = _parent_body(item, items)
    if parent and parent not in own:
        own = f"{parent} {own}".strip()
    if len(own) > BODY_LIMIT:
        return own[:BODY_LIMIT] + "…"
    return own or "(본문 없음)"


def _parent_body(item, items):
    parent = " ".join(str(item.get("parent") or "").split())
    if not parent or parent in {"없음", "-", "없음."}:
        return ""
    for other in items:
        if other is item:
            continue
        number = str(other.get("number") or "").strip()
        heading = str(other.get("heading") or "").strip()
        if parent in {number, heading, f"{number}번", f"{heading}번"}:
            return " ".join((other.get("body") or "").split())
    return ""


def _sections(text):
    sections = {}
    current = None
    buf = []

    def flush():
        if current is None:
            return
        piece = "\n".join(buf).strip()
        if not piece:
            return
        if current in sections:
            sections[current] = sections[current] + "\n" + piece
        else:
            sections[current] = piece

    for line in (text or "").splitlines():
        match = re.match(r"^###\s+\[([^\]]+)\]\s*(.*)$", line.strip())
        if match:
            flush()
            current = match.group(1).strip()
            buf = [match.group(2)] if match.group(2).strip() else []
            continue
        if current is not None:
            buf.append(line)
    flush()
    return sections


def _bind_sections(sections, chunk):
    slot_keys = [str(index) for index in range(1, len(chunk) + 1)]
    if all(key in sections for key in slot_keys):
        return [sections[key] for key in slot_keys]
    bound = []
    for index, item in enumerate(chunk, start=1):
        section = ""
        for key in _item_keys(index, item):
            if key in sections:
                section = sections[key]
                break
        bound.append(section)
    return bound


def _item_keys(index, item):
    keys = []
    number = str(item.get("number") or "").strip()
    digits = re.search(r"\d+", number)
    if digits:
        keys.append(digits.group(0))
        keys.append(digits.group(0) + "번")
    if number:
        keys.append(number)
    keys.append(str(index))
    return list(dict.fromkeys(keys))


def _pick_code(section, label_re, id_re, normalize, allowed):
    labeled = []
    for match in label_re.finditer(section or ""):
        labeled.extend(id_re.findall(match.group(1)))
    pool = labeled or id_re.findall(section or "")
    chosen = ""
    for raw in pool:
        value = normalize(raw)
        if allowed is None or value in allowed:
            chosen = value
    return chosen


def _pick_subtopics(pending, catalog, complete, log):
    try:
        for chunk in _chunks(pending, lambda group: _subtype_prompt(group, catalog)):
            _apply_picks(chunk, complete(_subtype_prompt(chunk, catalog)), catalog)
    except AppError as error:
        if log is not None:
            log("세부유형 일치 실패: " + str(error))


def _chunks(pending, prompt_for):
    chunk = []
    for item in pending:
        trial = chunk + [item]
        if chunk and len(prompt_for(trial)) > CHUNK_LIMIT:
            yield chunk
            chunk = [item]
        else:
            chunk = trial
    if chunk:
        yield chunk


def _subtype_prompt(chunk, catalog):
    blocks = [
        "각 문항의 본문을 읽고, 그 문항의 후보 중에서만 세부유형 키를 하나 고르세요.",
        "후보에 없는 키를 만들지 마세요. 어느 후보도 맞지 않으면 없음이라고 적으세요.",
        "출력은 마크다운만 작성하세요. 문항 순번은 아래 번호 그대로 쓰세요.",
        "형식: ### [순번] 다음 줄에 - 세부유형: 후보의 키",
    ]
    for index, item in enumerate(chunk, start=1):
        topic = catalog.by_topic_id.get(item.get("type_code") or "")
        entries = topic.entries if topic else ()
        chapter = " > ".join((
            topic.semester,
            topic.big_chapter,
            topic.middle_chapter,
            topic.little_chapter,
            topic.topic,
        )) if topic else ""
        body = " ".join((item.get("body") or "").split())
        if len(body) > BODY_LIMIT:
            body = body[:BODY_LIMIT] + "…"
        choices = "\n".join(
            f"- {entry.source_key}: {_cell(entry.subtopic)}" for entry in entries
        )
        blocks.extend((
            "",
            f"### [{index}]",
            f"문항 번호: {item.get('number') or ''}",
            f"유형: {chapter}",
            "본문:",
            body or "(본문 없음)",
            "후보:",
            choices,
        ))
    return "\n".join(blocks)


def _apply_picks(chunk, text, catalog):
    sections = _bind_sections(_sections(text), chunk)
    for item, section in zip(chunk, sections):
        topic = catalog.by_topic_id.get(item.get("type_code") or "")
        allowed = {entry.source_key: entry for entry in (topic.entries if topic else ())}
        key = _pick_code(
            section,
            re.compile(r"세부유형\s*[:：]\s*(.+)$", re.M),
            SOURCE_KEY,
            lambda value: value,
            allowed,
        )
        entry = allowed.get(key)
        if entry is not None:
            _apply_entry(item, entry)
