# -*- coding: utf-8 -*-
"""색인 실행. 설정을 읽고 문항 색인을 받은 뒤 주석 PDF 바이트를 만든다."""

from dataclasses import dataclass
from pathlib import Path

from catalog import TYPES_FILE
from catalog import display_path as catalog_path
from errors import AppError
from gemini import complete as complete_gemini
from gemini import generate as generate_gemini
from gemini import redact
from grok import complete as complete_grok
from grok import generate as generate_grok
from settings import fill_prompt, load

RUNNABLE = {"GEMINI": generate_gemini, "GROK": generate_grok}
COMPLETE = {"GEMINI": complete_gemini, "GROK": complete_grok}
from sheet import build_combined


@dataclass
class HeldPdf:
    name: str
    data: bytes


def process_pdfs(pdf_paths, log, produced, should_stop=None, progress=None):
    should_stop = should_stop or (lambda: False)

    def report(step, steps, status, index=0, total=0):
        if progress is not None:
            progress(step, steps, status, index, total)

    try:
        config = load()
    except Exception as error:
        log("실패: " + redact(error))
        report(0, 1, "중단")
        return
    if config.key.provider not in RUNNABLE:
        log(f"실패: CURRENT_KEY={config.key.provider} 는 아직 호출할 수 없습니다. 지금은 GEMINI와 GROK만 문항 색인을 실행합니다.")
        report(0, 1, "중단")
        return

    total = len(pdf_paths)
    steps = max(total, 1) * 2
    log(f"현재 키: {config.key.provider}")
    log(f"모델: {config.model.name}")
    log("지시: app/settings/prompt.md")
    log(f"유형: {catalog_path(TYPES_FILE)}")
    for name in config.rule_names:
        log(f"지침: app/settings/rules/{name}")
    log(f"PDF {total}개를 처리합니다.")
    report(0, steps, "준비", 0, total)

    indexed = []
    for index, raw_path in enumerate(pdf_paths):
        if should_stop():
            log("실행을 멈췄습니다.")
            report(index * 2, steps, "멈춤", index, total)
            break
        pdf_path = Path(raw_path)
        shown = index + 1
        mark = f"[{shown}/{total}]"
        log(f"{mark} 전송: {pdf_path.name}")
        report(index * 2, steps, f"전송  ·  {pdf_path.name}", shown, total)
        try:
            if not pdf_path.is_file():
                raise AppError(f"PDF가 없습니다: {pdf_path.name}")
            result = RUNNABLE[config.key.provider](
                config.key.value,
                config.model.name,
                pdf_path,
                fill_prompt(config, pdf_path.name),
                config.model.temperature,
                config.model.max_output_tokens,
                config.model.timeout_seconds,
            )
            indexed.append((pdf_path, result.text))
            log(f"{mark} 색인: {pdf_path.name}")
            log(f"{mark} 토큰 input={result.input_tokens} output={result.output_tokens} stop={result.stop_reason}")
            if result.stop_reason == "MAX_TOKENS":
                log(f"{mark} 응답이 출력 한도에서 끊겼습니다. 해당 PDF는 불완전할 수 있습니다.")
            report(index * 2 + 2, steps, f"색인  ·  {pdf_path.name}", shown, total)
        except Exception as error:
            log(f"{mark} 실패: " + redact(error, config.key.value))
            log("중단: PDF를 넘기지 못해 여기서 멈춥니다.")
            report(index * 2 + 2, steps, "중단", shown, total)
            return

    if not indexed:
        return
    names = [path.name for path, _text in indexed]
    joined = ", ".join(names)
    log(f"조판: {joined}")
    report(steps, steps, "조판", total, total)
    try:
        def complete(prompt):
            result = COMPLETE[config.key.provider](
                config.key.value,
                config.model.name,
                prompt,
                0,
                config.model.max_output_tokens,
                config.model.timeout_seconds,
            )
            log(f"세부유형 토큰 input={result.input_tokens} output={result.output_tokens}")
            return result.text

        data = build_combined(
            indexed,
            config.code_names,
            config.key.value,
            config.model.name,
            config.model.vision_timeout_seconds,
            config.key.provider,
            config.catalog,
            complete,
            log,
        )
    except Exception as error:
        log("실패: " + redact(error, config.key.value))
        return
    if len(indexed) == 1:
        held_name = f"{indexed[0][0].stem}_annotated.pdf"
    else:
        held_name = "annotated.pdf"
    held = HeldPdf(held_name, data)
    produced(held)
    log(f"보관: {held.name}")


def save_download(data, destination):
    destination = Path(destination)
    if not isinstance(data, (bytes, bytearray)) or len(data) < 1000:
        raise AppError("다운로드할 PDF가 없습니다.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return destination
