# -*- coding: utf-8 -*-
"""Gemini 문항 색인 호출."""

import base64
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from errors import AppError


@dataclass
class IndexResult:
    text: str
    stop_reason: str
    input_tokens: str
    output_tokens: str
    model: str


def redact(text, secret=""):
    if text is None:
        return ""
    text = str(text)
    if secret:
        text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"AIza[0-9A-Za-z_\-]+", "[REDACTED]", text)
    text = re.sub(r"AQ\.[0-9A-Za-z_\-]+", "[REDACTED]", text)
    text = re.sub(r"sk-ant-[A-Za-z0-9_\-]+", "[REDACTED]", text)
    text = re.sub(r"sk-[A-Za-z0-9_\-]+", "[REDACTED]", text)
    return text


def generate(api_key, model_name, pdf_path, prompt, temperature, max_output_tokens, timeout):
    pdf_path = Path(pdf_path)
    encoded = base64.b64encode(pdf_path.read_bytes()).decode("ascii")
    payload = {
        "contents": [{
            "parts": [
                {"inline_data": {"mime_type": "application/pdf", "data": encoded}},
                {"text": prompt},
            ]
        }],
        "generationConfig": {
            "maxOutputTokens": max_output_tokens,
            "temperature": temperature,
        },
    }
    request = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent",
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
        detail = redact(error.read().decode("utf-8", "replace"), api_key)[:1200]
        if error.code in (401, 403) or re.search(
            r"API_KEY_INVALID|API key not valid|unauthenticated|PERMISSION_DENIED|invalid[_ ]key",
            detail,
            re.I,
        ):
            raise AppError(f"키를 사용할 수 없습니다. app/settings/.env의 키를 확인하세요. (HTTP {error.code})") from None
        if re.search(r"quota|RESOURCE_EXHAUSTED|billing|exceeded", detail, re.I):
            raise AppError(f"Gemini API 사용 한도가 부족합니다. 한도를 확인한 뒤 다시 실행하세요. (HTTP {error.code})") from None
        raise AppError(f"Gemini API 요청 실패 (HTTP {error.code}): {detail}") from None
    except urllib.error.URLError as error:
        raise AppError("Gemini API 요청 실패: " + redact(error.reason, api_key)) from None

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as error:
        raise AppError("Gemini 응답을 읽지 못했습니다: " + redact(error, api_key)) from None

    candidates = parsed.get("candidates") or []
    if not candidates:
        raise AppError("Gemini 응답에 텍스트가 없습니다.")
    parts = candidates[0].get("content", {}).get("parts", [])
    text = "\n".join(part.get("text", "") for part in parts if part.get("text")).strip()
    if not text:
        raise AppError("Gemini 응답에 텍스트가 없습니다.")
    usage = parsed.get("usageMetadata") or {}
    return IndexResult(
        text=text,
        stop_reason=str(candidates[0].get("finishReason") or ""),
        input_tokens=str(usage.get("promptTokenCount") or ""),
        output_tokens=str(usage.get("candidatesTokenCount") or ""),
        model=model_name,
    )
