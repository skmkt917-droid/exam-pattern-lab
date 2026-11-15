# -*- coding: utf-8 -*-
"""Grok 문항 색인 호출."""

import json
import re
import uuid
import urllib.request

from errors import AppError
from gemini import IndexResult, _request, prepare_pdf

FILE_LIMIT = 48 * 1024 * 1024


def generate(api_key, model_name, pdf_path, prompt, temperature, max_output_tokens, timeout):
    pdf_bytes = prepare_pdf(pdf_path)
    if len(pdf_bytes) > FILE_LIMIT:
        raise AppError(f"PDF가 48MB를 넘습니다: {pdf_path.name}")
    filename = _safe_name(pdf_path.name)
    file_id = None
    try:
        file_id = _upload(api_key, filename, pdf_bytes, timeout)
        body = _respond(
            api_key,
            model_name,
            prompt,
            temperature,
            max_output_tokens,
            timeout,
            file_id,
        )
    finally:
        if file_id:
            _delete(api_key, file_id, timeout)
    return _result(body, model_name)


def read_boxes(api_key, model, prompt, png_bytes, timeout):
    encoded = _b64(png_bytes)
    payload = {
        "model": model,
        "temperature": 0,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + encoded}},
                {"type": "text", "text": prompt},
            ],
        }],
    }
    raw, _headers = _request(_json_request("https://api.x.ai/v1/chat/completions", api_key, payload), api_key, timeout)
    parsed = json.loads(raw.decode("utf-8"))
    message = ((parsed.get("choices") or [{}])[0].get("message") or {})
    content = message.get("content") or ""
    if isinstance(content, list):
        content = "\n".join(part.get("text", "") for part in content if isinstance(part, dict))
    return str(content)


def _upload(api_key, filename, data, timeout):
    body, boundary = _multipart(filename, data)
    request = urllib.request.Request(
        "https://api.x.ai/v1/files",
        data=body,
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "multipart/form-data; boundary=" + boundary,
        },
        method="POST",
    )
    raw, _headers = _request(request, api_key, timeout)
    try:
        uploaded = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        raise AppError(f"PDF 업로드 응답을 읽지 못했습니다: {filename}") from None
    file_id = str(uploaded.get("id") or "").strip()
    if not file_id:
        raise AppError(f"PDF를 올리지 못했습니다: {filename}")
    return file_id


def _respond(api_key, model_name, prompt, temperature, max_output_tokens, timeout, file_id):
    payload = {
        "model": model_name,
        "temperature": temperature,
        "max_output_tokens": max_output_tokens,
        "input": [{
            "role": "user",
            "content": [
                {"type": "input_text", "text": prompt},
                {"type": "input_file", "file_id": file_id},
            ],
        }],
    }
    raw, _headers = _request(_json_request("https://api.x.ai/v1/responses", api_key, payload), api_key, timeout)
    return raw.decode("utf-8")


def _delete(api_key, file_id, timeout):
    try:
        _request(
            urllib.request.Request(
                "https://api.x.ai/v1/files/" + file_id,
                headers={"Authorization": "Bearer " + api_key},
                method="DELETE",
            ),
            api_key,
            min(timeout, 30),
        )
    except Exception:
        return


def _result(body, model_name):
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as error:
        raise AppError("Grok 응답을 읽지 못했습니다.") from error
    text = _output_text(parsed)
    if not text:
        raise AppError("Grok 응답에 텍스트가 없습니다.")
    usage = parsed.get("usage") or {}
    return IndexResult(
        text=text,
        stop_reason=str(parsed.get("status") or ""),
        input_tokens=str(usage.get("input_tokens") or usage.get("prompt_tokens") or ""),
        output_tokens=str(usage.get("output_tokens") or usage.get("completion_tokens") or ""),
        model=model_name,
    )


def _output_text(parsed):
    if parsed.get("output_text"):
        return str(parsed["output_text"]).strip()
    chunks = []
    for item in parsed.get("output") or []:
        content = item.get("content")
        if isinstance(content, str):
            chunks.append(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("text"):
                    chunks.append(part["text"])
    return "\n".join(chunks).strip()


def _json_request(url, api_key, payload):
    return urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
        },
        method="POST",
    )


def _multipart(filename, data):
    boundary = "epl" + uuid.uuid4().hex
    head = (
        f"--{boundary}\r\n"
        "Content-Disposition: form-data; name=\"purpose\"\r\n\r\n"
        "assistants\r\n"
        f"--{boundary}\r\n"
        f"Content-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
        "Content-Type: application/pdf\r\n\r\n"
    ).encode("utf-8")
    tail = f"\r\n--{boundary}--\r\n".encode("utf-8")
    return head + data + tail, boundary


def _safe_name(name):
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-") or "exam.pdf"
    if not cleaned.lower().endswith(".pdf"):
        cleaned += ".pdf"
    return cleaned


def _b64(data):
    import base64
    return base64.b64encode(data).decode("ascii")
