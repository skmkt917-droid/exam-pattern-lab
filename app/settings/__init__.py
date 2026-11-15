# -*- coding: utf-8 -*-
"""키, 모델, 지시문, 분류 지침을 읽는다."""

import json
import re
from dataclasses import dataclass
from pathlib import Path

from catalog import load_catalog
from errors import AppError


APP_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = APP_DIR.parent
SETTINGS_DIR = Path(__file__).resolve().parent
KEYS_FILE = SETTINGS_DIR / ".env"
MODEL_FILE = SETTINGS_DIR / "model.env"
PROMPT_FILE = SETTINGS_DIR / "prompt.md"
RULES_DIR = SETTINGS_DIR / "rules"
CACHE_DIR = APP_DIR / "cache"
MODEL_CACHE = CACHE_DIR / "model.json"

ALLOWED_KEYS = ("GEMINI", "CLAUDE", "GPT", "GROK")
KEY_CHOICES = ", ".join(ALLOWED_KEYS)
RULE_SUFFIXES = {".md", ".txt"}
ENV_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")
CODE_LINE = re.compile(
    r"^(?:\|\s*)?(PS\d+|R\d+|[A-F]\d+)(?:\t|\s*\|\s*)([^|\t]+)"
)


@dataclass
class ApiKey:
    provider: str
    value: str


@dataclass
class ModelSpec:
    name: str
    temperature: float
    max_output_tokens: int
    timeout_seconds: int
    vision_timeout_seconds: int


@dataclass
class Settings:
    key: ApiKey
    model: ModelSpec
    prompt: str
    rules_text: str
    rule_names: tuple
    code_names: dict
    catalog: object


def display_path(path):
    path = Path(path)
    try:
        return path.resolve().relative_to(ROOT_DIR).as_posix()
    except ValueError:
        return path.as_posix()


def read_env(path):
    path = Path(path)
    if not path.is_file():
        raise AppError(f"파일이 없습니다: {display_path(path)}")
    env = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        trimmed = line.strip()
        if not trimmed or trimmed.startswith("#"):
            continue
        match = ENV_LINE.match(trimmed)
        if not match:
            continue
        env[match.group(1)] = match.group(2).strip().strip('"').strip("'")
    return env


def list_rule_files():
    if not RULES_DIR.is_dir():
        return []
    files = [
        path for path in RULES_DIR.rglob("*")
        if path.is_file() and path.suffix.lower() in RULE_SUFFIXES
    ]
    return sorted(files, key=lambda path: path.relative_to(RULES_DIR).as_posix().lower())


def _required(env, name, path):
    value = str(env.get(name, "")).strip()
    if not value:
        raise AppError(f"{display_path(path)}에 {name} 값이 없습니다.")
    return value


def _positive_int(raw, name, path):
    try:
        value = int(raw)
    except ValueError:
        raise AppError(f"{display_path(path)}의 {name}는 정수여야 합니다.") from None
    if value < 1:
        raise AppError(f"{display_path(path)}의 {name}는 1 이상이어야 합니다.")
    return value


def load_key():
    env = read_env(KEYS_FILE)
    current = str(env.get("CURRENT_KEY", "")).strip().upper()
    if not current:
        raise AppError(f"CURRENT_KEY가 비어 있습니다. {KEY_CHOICES} 중 하나를 지정하세요.")
    if current not in ALLOWED_KEYS:
        raise AppError(f"CURRENT_KEY={current} 는 사용할 수 없습니다. {KEY_CHOICES} 중 하나를 지정하세요.")
    name = f"{current}_API_KEY"
    value = str(env.get(name, "")).strip()
    if not value:
        raise AppError(f"{name} 값이 비어 있습니다. {display_path(KEYS_FILE)}에 키를 넣은 뒤 다시 실행하세요.")
    return ApiKey(current, value)


def _read_model_cache():
    if not MODEL_CACHE.is_file():
        return {}
    try:
        data = json.loads(MODEL_CACHE.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        raise AppError("모델 캐시를 읽지 못했습니다: " + str(error)) from None
    if not isinstance(data, dict):
        raise AppError("모델 캐시를 읽지 못했습니다.")
    cached = {}
    for provider, model in data.items():
        name = str(model or "").strip()
        if name:
            cached[str(provider)] = name
    return cached


def read_cached_model(provider):
    return _read_model_cache().get(provider, "")


def write_cached_model(provider, model):
    model = str(model or "").strip()
    if not model:
        raise AppError("저장할 모델이 없습니다.")
    cached = {}
    if MODEL_CACHE.is_file():
        try:
            cached = _read_model_cache()
        except AppError:
            cached = {}
    cached[provider] = model
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_CACHE.write_text(json.dumps(cached, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return model


def load_model():
    env = read_env(MODEL_FILE)
    raw_temp = _required(env, "TEMPERATURE", MODEL_FILE)
    try:
        temperature = float(raw_temp)
    except ValueError:
        raise AppError(f"{display_path(MODEL_FILE)}의 TEMPERATURE는 숫자여야 합니다.") from None
    if temperature < 0:
        raise AppError(f"{display_path(MODEL_FILE)}의 TEMPERATURE는 0 이상이어야 합니다.")
    key = load_key()
    name = read_cached_model(key.provider)
    if not name:
        raise AppError("선택한 모델이 없습니다. AI 모델 선택을 눌러 모델을 고르세요.")
    return ModelSpec(
        name=name,
        temperature=temperature,
        max_output_tokens=_positive_int(
            _required(env, "MAX_OUTPUT_TOKENS", MODEL_FILE),
            "MAX_OUTPUT_TOKENS",
            MODEL_FILE,
        ),
        timeout_seconds=_positive_int(
            _required(env, "TIMEOUT_SECONDS", MODEL_FILE),
            "TIMEOUT_SECONDS",
            MODEL_FILE,
        ),
        vision_timeout_seconds=_positive_int(
            _required(env, "VISION_TIMEOUT_SECONDS", MODEL_FILE),
            "VISION_TIMEOUT_SECONDS",
            MODEL_FILE,
        ),
    )


def load_prompt():
    if not PROMPT_FILE.is_file():
        raise AppError(f"지시문이 없습니다: {display_path(PROMPT_FILE)}")
    prompt = PROMPT_FILE.read_text(encoding="utf-8-sig").strip()
    if not prompt:
        raise AppError(f"지시문이 비어 있습니다: {display_path(PROMPT_FILE)}")
    if "{pdf_name}" not in prompt or "{rules}" not in prompt:
        raise AppError(f"{display_path(PROMPT_FILE)}에 {{pdf_name}}과 {{rules}}가 있어야 합니다.")
    return prompt


def load_rules():
    if not RULES_DIR.is_dir():
        raise AppError(f"분류 지침 폴더가 없습니다: {display_path(RULES_DIR)}")
    files = list_rule_files()
    if not files:
        raise AppError(f"분류 지침 파일이 없습니다: {display_path(RULES_DIR)}")
    parts = []
    names = []
    code_names = {}
    for path in files:
        text = path.read_text(encoding="utf-8-sig").strip()
        if not text:
            continue
        relative = path.relative_to(RULES_DIR).as_posix()
        names.append(relative)
        parts.append(f"<!-- {display_path(path)} -->\n{text}")
        for line in text.splitlines():
            match = CODE_LINE.match(line.strip())
            if match:
                code_names[match.group(1)] = match.group(2).strip()
    if not parts:
        raise AppError(f"분류 지침이 비어 있습니다: {display_path(RULES_DIR)}")
    return "\n\n".join(parts), tuple(names), code_names


def load():
    rules_text, rule_names, code_names = load_rules()
    return Settings(
        key=load_key(),
        model=load_model(),
        prompt=load_prompt(),
        rules_text=rules_text,
        rule_names=rule_names,
        code_names=code_names,
        catalog=load_catalog(),
    )


def fill_prompt(config, pdf_name):
    return (
        config.prompt
        .replace("{pdf_name}", str(pdf_name))
        .replace("{rules}", config.rules_text)
    )
