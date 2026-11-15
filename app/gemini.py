# -*- coding: utf-8 -*-
"""Gemini 문항 색인 호출."""

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import fitz

from errors import AppError

RETRY_STATUS = {429, 500, 503}
RETRY_WAITS = (2, 4, 8, 16)


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
    text = re.sub(r"xai-[A-Za-z0-9_\-]+", "[REDACTED]", text)
    return text


def prepare_pdf(pdf_path):
    pdf_path = Path(pdf_path)
    try:
        raw = pdf_path.read_bytes()
    except OSError as error:
        raise AppError(f"PDF를 읽지 못했습니다: {pdf_path.name}") from error
    if not raw.startswith(b"%PDF"):
        raise AppError(f"PDF 형식이 아닙니다: {pdf_path.name}")
    try:
        document = fitz.open(stream=raw, filetype="pdf")
    except Exception:
        raise AppError(f"PDF를 열 수 없습니다: {pdf_path.name}") from None
    try:
        if document.needs_pass and not document.authenticate(""):
            raise AppError(f"암호가 걸린 PDF는 넘길 수 없습니다: {pdf_path.name}")
        if document.page_count < 1:
            raise AppError(f"페이지가 없는 PDF입니다: {pdf_path.name}")
        if document.page_count > 1000:
            raise AppError(f"PDF가 1000쪽을 넘습니다: {pdf_path.name}")
        try:
            cleaned = document.tobytes(garbage=4, deflate=True, clean=True)
        except Exception:
            cleaned = raw
    finally:
        document.close()
    if not cleaned.startswith(b"%PDF"):
        raise AppError(f"PDF를 정리하지 못했습니다: {pdf_path.name}")
    return cleaned


def _request(request, api_key, timeout):
    waits = iter(RETRY_WAITS)
    while True:
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read(), response.headers
        except urllib.error.HTTPError as error:
            detail = redact(error.read().decode("utf-8", "replace"), api_key)[:1200]
            if error.code in RETRY_STATUS:
                delay = next(waits, None)
                if delay is not None:
                    time.sleep(delay)
                    continue
                raise AppError(
                    f"Gemini가 일시적으로 응답하지 않습니다. 잠시 뒤 다시 실행하세요. (HTTP {error.code})"
                ) from None
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


def _upload_pdf(api_key, filename, data, timeout):
    start = urllib.request.Request(
        "https://generativelanguage.googleapis.com/upload/v1beta/files",
        data=json.dumps({"file": {"displayName": filename}}).encode("utf-8"),
        headers={
            "x-goog-api-key": api_key,
            "Content-Type": "application/json",
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(len(data)),
            "X-Goog-Upload-Header-Content-Type": "application/pdf",
        },
        method="POST",
    )
    _body, headers = _request(start, api_key, timeout)
    upload_url = headers.get("X-Goog-Upload-URL")
    if not upload_url:
        raise AppError(f"PDF 업로드 주소를 받지 못했습니다: {filename}")
    upload = urllib.request.Request(
        upload_url,
        data=data,
        headers={
            "Content-Length": str(len(data)),
            "Content-Type": "application/pdf",
            "X-Goog-Upload-Offset": "0",
            "X-Goog-Upload-Command": "upload, finalize",
        },
        method="POST",
    )
    raw, _headers = _request(upload, api_key, timeout)
    try:
        uploaded = json.loads(raw.decode("utf-8")).get("file") or {}
    except json.JSONDecodeError:
        raise AppError(f"PDF 업로드 응답을 읽지 못했습니다: {filename}") from None
    name = str(uploaded.get("name") or "").strip()
    uri = str(uploaded.get("uri") or "").strip()
    if not name or not uri:
        raise AppError(f"PDF를 올리지 못했습니다: {filename}")
    state = uploaded.get("state") or "ACTIVE"
    deadline = time.time() + min(max(timeout, 1), 60)
    while state == "PROCESSING" and time.time() < deadline:
        time.sleep(1)
        info_raw, _headers = _request(
            urllib.request.Request(
                f"https://generativelanguage.googleapis.com/v1beta/{name}",
                headers={"x-goog-api-key": api_key},
                method="GET",
            ),
            api_key,
            timeout,
        )
        try:
            info = json.loads(info_raw.decode("utf-8"))
        except json.JSONDecodeError:
            info = {}
        state = info.get("state") or state
        uri = str(info.get("uri") or uri)
    if state == "FAILED":
        raise AppError(f"PDF를 읽지 못했습니다: {filename}")
    if state != "ACTIVE":
        raise AppError(f"PDF 처리가 끝나지 않았습니다: {filename}")
    return name, uri


def _delete_file(api_key, name, timeout):
    try:
        _request(
            urllib.request.Request(
                f"https://generativelanguage.googleapis.com/v1beta/{name}",
                headers={"x-goog-api-key": api_key},
                method="DELETE",
            ),
            api_key,
            min(timeout, 30),
        )
    except AppError:
        return


def _generate_content(api_key, model_name, prompt, temperature, max_output_tokens, timeout, file_uri):
    document = {"file_data": {"mime_type": "application/pdf", "file_uri": file_uri}}
    payload = {
        "contents": [{"parts": [document, {"text": prompt}]}],
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
    raw, _headers = _request(request, api_key, timeout)
    return raw.decode("utf-8")


def generate(api_key, model_name, pdf_path, prompt, temperature, max_output_tokens, timeout):
    pdf_path = Path(pdf_path)
    pdf_bytes = prepare_pdf(pdf_path)
    uploaded = None
    try:
        uploaded = _upload_pdf(api_key, pdf_path.name, pdf_bytes, timeout)
        body = _generate_content(
            api_key, model_name, prompt, temperature, max_output_tokens, timeout, uploaded[1]
        )
    finally:
        if uploaded:
            _delete_file(api_key, uploaded[0], timeout)

    return _index_result(body, model_name, api_key)


def complete(api_key, model_name, prompt, temperature, max_output_tokens, timeout):
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
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
    raw, _headers = _request(request, api_key, timeout)
    return _index_result(raw.decode("utf-8"), model_name, api_key)


def _index_result(body, model_name, api_key):
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
