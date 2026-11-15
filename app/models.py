# -*- coding: utf-8 -*-
"""현재 키로 호출할 수 있는 모델 목록."""

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from errors import AppError
from gemini import redact


LIST_TIMEOUT = 30
SKIP_OPENAI = (
    "embedding",
    "whisper",
    "tts",
    "dall-e",
    "davinci",
    "babbage",
    "moderation",
    "transcribe",
    "realtime",
)


@dataclass
class ListedModel:
    id: str
    label: str


def list_available(provider, api_key, timeout=LIST_TIMEOUT):
    if provider == "GEMINI":
        models = list_gemini(api_key, timeout)
    elif provider == "CLAUDE":
        models = list_claude(api_key, timeout)
    elif provider == "GPT":
        models = list_openai(api_key, timeout)
    elif provider == "GROK":
        models = list_grok(api_key, timeout)
    else:
        raise AppError(f"{provider} 모델 목록은 조회할 수 없습니다.")
    if not models:
        raise AppError("이 키로 호출할 수 있는 모델이 없습니다.")
    return models


def list_gemini(api_key, timeout):
    found = {}
    token = ""
    for _page in range(10):
        url = "https://generativelanguage.googleapis.com/v1beta/models?pageSize=100"
        if token:
            url += "&pageToken=" + urllib.parse.quote(token)
        payload = _read_json(url, {"x-goog-api-key": api_key}, timeout, api_key)
        for entry in payload.get("models") or []:
            methods = entry.get("supportedGenerationMethods") or []
            if "generateContent" not in methods:
                continue
            name = str(entry.get("name") or "")
            if not name.startswith("models/") or name.count("/") != 1:
                continue
            model_id = name.split("/", 1)[1].strip()
            if not model_id:
                continue
            found[model_id] = _label(model_id, entry.get("displayName"))
        token = str(payload.get("nextPageToken") or "").strip()
        if not token:
            break
    return _sorted(found)


def list_claude(api_key, timeout):
    found = {}
    after = ""
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }
    for _page in range(10):
        url = "https://api.anthropic.com/v1/models?limit=100"
        if after:
            url += "&after_id=" + urllib.parse.quote(after)
        payload = _read_json(url, headers, timeout, api_key)
        rows = payload.get("data") or []
        for entry in rows:
            model_id = str(entry.get("id") or "").strip()
            if model_id:
                found[model_id] = _label(model_id, entry.get("display_name"))
        if not payload.get("has_more") or not rows:
            break
        after = str(payload.get("last_id") or rows[-1].get("id") or "").strip()
        if not after:
            break
    return _sorted(found)


def list_openai(api_key, timeout):
    payload = _read_json(
        "https://api.openai.com/v1/models",
        {"Authorization": "Bearer " + api_key},
        timeout,
        api_key,
    )
    found = {}
    for entry in payload.get("data") or []:
        model_id = str(entry.get("id") or "").strip()
        lowered = model_id.lower()
        if not model_id or any(token in lowered for token in SKIP_OPENAI):
            continue
        found[model_id] = model_id
    return _sorted(found)


def list_grok(api_key, timeout):
    payload = _read_json(
        "https://api.x.ai/v1/models",
        {"Authorization": "Bearer " + api_key},
        timeout,
        api_key,
    )
    found = {}
    for entry in payload.get("data") or []:
        model_id = str(entry.get("id") or "").strip()
        if not model_id or not _grok_chat(model_id):
            continue
        found[model_id] = model_id
    return _sorted(found)


def _grok_chat(model_id):
    lowered = model_id.lower()
    if any(token in lowered for token in ("embed", "tts", "whisper", "video", "imagine", "moderation")):
        return False
    if "image" in lowered and "vision" not in lowered:
        return False
    return True


def _label(model_id, display):
    display = str(display or "").strip()
    if display and display != model_id:
        return f"{model_id}  {display}"
    return model_id


def _sorted(found):
    return [ListedModel(model_id, found[model_id]) for model_id in sorted(found, key=str.lower)]


def _read_json(url, headers, timeout, api_key):
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        detail = redact(error.read().decode("utf-8", "replace"), api_key)[:800]
        if error.code in (401, 403) or re.search(
            r"API_KEY_INVALID|API key not valid|unauthenticated|PERMISSION_DENIED|invalid[_ ]key",
            detail,
            re.I,
        ):
            raise AppError(f"키를 사용할 수 없습니다. app/settings/.env의 키를 확인하세요. (HTTP {error.code})") from None
        if re.search(r"quota|RESOURCE_EXHAUSTED|billing|exceeded|insufficient", detail, re.I):
            raise AppError(f"API 사용 한도가 부족합니다. (HTTP {error.code})") from None
        raise AppError(f"모델 목록 요청 실패 (HTTP {error.code}): {detail}") from None
    except urllib.error.URLError as error:
        raise AppError("모델 목록 요청 실패: " + redact(error.reason, api_key)) from None
    try:
        return json.loads(body)
    except json.JSONDecodeError as error:
        raise AppError("모델 목록을 읽지 못했습니다: " + redact(error, api_key)) from None
