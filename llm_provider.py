"""LLM provider abstraction for GIT4SW commit message generation.

Supports in-house OpenAI-compatible servers (e.g. Strata / vLLM / Ollama) as well
as external cloud APIs (OpenAI, Gemini, Anthropic) using Python stdlib urllib only,
matching git_provider.py's zero-SDK-dependency design.
"""

import json
import os
import re
import ssl
import time
import urllib.error
import urllib.request


DEFAULT_LLM_CONFIG = {
    "llm_provider": "strata",
    "llm_api_style": "openai",
    "llm_base_url": "http://10.124.1.64:6006//v1",
    "llm_model": "qwen3.8-flash-next-iq3_s",
    "llm_api_key": "",
    "llm_timeout_sec": 600,
    "llm_verify_ssl": True,
    "llm_params": {
        "temperature": 0.2,
        "max_tokens": 16384,
        "top_p": 0.9,
        "chat_template_kwargs": {
            "enable_thinking": False,
        },
    },
    "llm_prompt_file": "template/llm_commit_prompt.md",
    "llm_custom_instructions": "",
    "auto_bg_diff": True,
}

DEFAULT_PROMPT_TEMPLATE = """[SYSTEM]
당신은 기계·기구 설계 및 SolidWorks CAD 버전관리 전문가입니다.
제공되는 SolidWorks 파일(.sldprt, .sldasm, .slddrw)의 구조적 변경 내역(JSON)만을 근거로 정확하고 간결한 Git 커밋 메시지를 작성하십시오.

[작성 규칙]
1. 출력 형식은 반드시 아래 구조를 따르십시오 (코드 블록, 부가 설명, 사고 과정 출력 금지):
   - 첫 번째 줄: <설계단계 접두사><핵심 변경 내용 1행 요약 (60자 내외)>
   - 두 번째 줄: 반드시 빈 줄
   - 세 번째 줄 이하: 변경된 파일별 상세 변경 내역 불릿 (`- 파일명: 구체적 변경 내용`)
2. 사용자가 선택한 접두사({selected_prefix})가 있으면 반드시 그 접두사로 시작하고, 없으면 프리셋 목록({preset_list}) 중 가장 알맞은 접두사를 선택하십시오.
3. JSON에 명시된 사실과 수치만 인용하고 추측하지 마십시오.
4. 사용자가 입력한 메모({user_hint})가 있으면 1행 요약에 반영하십시오.
{custom_instructions}

[USER]
선택된 접두사: {selected_prefix}
사용자 메모/힌트: {user_hint}

변경사항 분석 결과(JSON):
{changes_json}
"""


def get_app_dir():
    return os.path.dirname(os.path.abspath(__file__))


def load_dotenv(search_dirs=None):
    """Parse key=value pairs from .env files without requiring python-dotenv."""
    if search_dirs is None:
        search_dirs = [get_app_dir(), os.getcwd()]

    loaded = {}
    seen = set()
    for d in search_dirs:
        if not d:
            continue
        env_path = os.path.normpath(os.path.join(d, ".env"))
        norm_key = env_path.lower()
        if norm_key in seen or not os.path.isfile(env_path):
            continue
        seen.add(norm_key)
        try:
            with open(env_path, "r", encoding="utf-8-sig") as f:
                for raw_line in f:
                    line = raw_line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if line.startswith("export "):
                        line = line[7:].strip()
                    if "=" not in line:
                        continue
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip()
                    if not key:
                        continue
                    # Strip matching surrounding quotes
                    if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                        val = val[1:-1]
                    elif " #" in val:
                        val = val.split(" #", 1)[0].strip()
                    if key not in loaded and val != "":
                        loaded[key] = val
        except Exception:
            pass
    return loaded


def normalize_base_url(url):
    """Normalize redundant path slashes (e.g. 'http://host:6006//v1/' -> 'http://host:6006/v1')."""
    if not url:
        return ""
    s = str(url).strip()
    m = re.match(r"^(https?://)(.*)$", s, re.IGNORECASE)
    if m:
        scheme, rest = m.group(1), m.group(2)
        rest = re.sub(r"/{2,}", "/", rest).rstrip("/")
        return f"{scheme}{rest}"
    return re.sub(r"/{2,}", "/", s).rstrip("/")


def resolve_api_key(config, extra_search_dirs=None):
    """Resolve API key with priority:
    1. OS env / .env: {<LLM_PROVIDER>}_API_KEY (e.g. STRATA_API_KEY)
    2. OS env / .env: GIT4SW_LLM_API_KEY
    3. OS env / .env: style-specific key (OPENAI_API_KEY / GEMINI_API_KEY / ANTHROPIC_API_KEY)
    4. config.json: llm_api_key
    Returns (api_key, source_description).
    """
    provider = str(config.get("llm_provider") or "").strip()
    style = str(config.get("llm_api_style") or "openai").strip().lower()

    candidate_env_names = []
    if provider:
        sanitized = re.sub(r"[^A-Za-z0-9]+", "_", provider).strip("_").upper()
        if sanitized:
            candidate_env_names.append(f"{sanitized}_API_KEY")
    candidate_env_names.append("GIT4SW_LLM_API_KEY")

    style_key_map = {
        "openai": "OPENAI_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
    }
    if style in style_key_map and style_key_map[style] not in candidate_env_names:
        candidate_env_names.append(style_key_map[style])

    # 1. OS environment variables first
    for env_name in candidate_env_names:
        val = os.environ.get(env_name, "").strip()
        if val:
            return val, f"env:{env_name}"

    # 2. .env files
    dirs = [get_app_dir()]
    if extra_search_dirs:
        dirs.extend(extra_search_dirs)
    dirs.append(os.getcwd())
    dotenv_vars = load_dotenv(dirs)
    for env_name in candidate_env_names:
        val = dotenv_vars.get(env_name, "").strip()
        if val:
            return val, f".env:{env_name}"

    # 3. config.json fallback
    cfg_key = str(config.get("llm_api_key") or "").strip()
    if cfg_key:
        return cfg_key, "config.json"

    return "", "none"


def resolve_prompt_file_path(config, app_dir=None):
    """Resolve the absolute path of llm_prompt_file."""
    base_dir = app_dir or get_app_dir()
    rel_or_abs = str(config.get("llm_prompt_file") or DEFAULT_LLM_CONFIG["llm_prompt_file"]).strip()
    if not rel_or_abs:
        rel_or_abs = DEFAULT_LLM_CONFIG["llm_prompt_file"]
    if os.path.isabs(rel_or_abs):
        return rel_or_abs
    return os.path.normpath(os.path.join(base_dir, rel_or_abs))


def ensure_prompt_file(config, app_dir=None):
    """Ensure the prompt template file exists on disk; create from default if missing."""
    path = resolve_prompt_file_path(config, app_dir=app_dir)
    if not os.path.exists(path):
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(DEFAULT_PROMPT_TEMPLATE)
    return path


def load_prompt_sections(config, app_dir=None):
    """Load [SYSTEM] and [USER] template strings from llm_prompt_file."""
    path = resolve_prompt_file_path(config, app_dir=app_dir)
    raw = ""
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = f.read()
        except Exception:
            raw = ""
    if not raw.strip():
        raw = DEFAULT_PROMPT_TEMPLATE

    # Strip leading HTML comments <!-- ... -->
    cleaned = re.sub(r"<!--.*?-->", "", raw, flags=re.DOTALL).strip()

    sys_match = re.search(r"\[SYSTEM\]\s*(.*?)(?=\[USER\]|\Z)", cleaned, flags=re.DOTALL | re.IGNORECASE)
    usr_match = re.search(r"\[USER\]\s*(.*)$", cleaned, flags=re.DOTALL | re.IGNORECASE)

    system_tpl = sys_match.group(1).strip() if sys_match else cleaned
    user_tpl = usr_match.group(1).strip() if usr_match else "{changes_json}"
    return system_tpl, user_tpl


def _safe_format(template, values):
    """Substitute only known {placeholders} so literal JSON braces in templates never raise KeyError."""
    result = template
    for key, val in values.items():
        result = result.replace("{" + key + "}", str(val))
    return result


def build_messages(diff_payload, config, commit_presets=None, selected_prefix="", user_hint="", app_dir=None):
    """Build (system_prompt, user_prompt) strings from template + diff payload."""
    system_tpl, user_tpl = load_prompt_sections(config, app_dir=app_dir)

    presets = commit_presets or []
    preset_list_str = ", ".join(f'"{p}"' for p in presets) if presets else '"상세설계 : ", "도면작성 : "'

    custom_inst = str(config.get("llm_custom_instructions") or "").strip()
    custom_block = f"\n5. 추가 사내 지침:\n   - {custom_inst}" if custom_inst else ""

    changes_json = json.dumps(diff_payload, ensure_ascii=False, indent=2)

    values = {
        "preset_list": preset_list_str,
        "selected_prefix": selected_prefix or "(없음)",
        "user_hint": user_hint or "(없음)",
        "custom_instructions": custom_block,
        "changes_json": changes_json,
    }
    system_prompt = _safe_format(system_tpl, values).strip()
    user_prompt = _safe_format(user_tpl, values).strip()
    return system_prompt, user_prompt


def strip_thinking_and_fences(text):
    """Clean LLM output: remove <think>...</think> blocks and surrounding markdown code fences."""
    if not text:
        return ""
    s = str(text)
    # Remove closed <think>...</think> blocks (Qwen3 / DeepSeek reasoning tags)
    s = re.sub(r"<think>.*?</think>", "", s, flags=re.DOTALL | re.IGNORECASE)
    # If a truncated unclosed <think> tag remains, drop everything from <think> onward if text exists before it,
    # or drop just the tag if nothing preceded it.
    if re.search(r"<think>", s, flags=re.IGNORECASE):
        parts = re.split(r"<think>", s, maxsplit=1, flags=re.IGNORECASE)
        s = parts[0].strip() if parts[0].strip() else parts[1].strip()

    s = s.strip()
    # Unwrap single outer fenced code block if model wrapped the whole output
    fence_match = re.match(r"^```[a-zA-Z0-9_-]*\s*\n(.*?)\n```\s*$", s, flags=re.DOTALL)
    if fence_match:
        s = fence_match.group(1).strip()
    return s


def parse_commit_message(raw_text, selected_prefix=""):
    """Split raw LLM output into (summary_line, description_body).

    - summary_line: single-line title (guaranteed to start with selected_prefix if provided)
    - description_body: multi-line file-by-file bullet details
    """
    cleaned = strip_thinking_and_fences(raw_text)
    if not cleaned:
        return "", ""

    lines = [line.rstrip() for line in cleaned.splitlines()]
    # Drop leading empty lines
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        return "", ""

    summary = lines[0].strip()
    # Strip markdown heading markers or quotes if model added them
    summary = re.sub(r"^#+\s*", "", summary).strip()
    if len(summary) >= 2 and summary[0] == summary[-1] and summary[0] in ('"', "'"):
        summary = summary[1:-1].strip()

    if selected_prefix:
        prefix_clean = selected_prefix.strip()
        if prefix_clean and not summary.startswith(prefix_clean):
            # Avoid double colon if summary already starts with prefix without spaces
            summary = f"{selected_prefix}{summary}"

    body_lines = lines[1:]
    # Strip leading/trailing empty lines from body
    while body_lines and not body_lines[0].strip():
        body_lines.pop(0)
    while body_lines and not body_lines[-1].strip():
        body_lines.pop()

    # Normalize bullet prefix (* -> -)
    normalized_body = []
    for bl in body_lines:
        stripped = bl.strip()
        if stripped.startswith("* "):
            normalized_body.append("- " + stripped[2:])
        else:
            normalized_body.append(bl.rstrip())

    description = "\n".join(normalized_body).strip()
    return summary, description


def _parse_llm_params(config):
    raw = config.get("llm_params")
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            try:
                import ast
                parsed = ast.literal_eval(raw)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass
    return dict(DEFAULT_LLM_CONFIG["llm_params"])


def _build_ssl_context(config):
    verify = config.get("llm_verify_ssl", True)
    if isinstance(verify, str):
        verify = verify.strip().lower() not in ("false", "0", "no")
    if not verify:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return None


def _http_post_json(url, payload, headers, timeout_sec, ssl_ctx=None):
    data_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "GIT4SW-LLM-Client",
    }
    req_headers.update(headers or {})
    req = urllib.request.Request(url, data=data_bytes, headers=req_headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec, context=ssl_ctx) as resp:
            raw_body = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw_body)
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise RuntimeError(f"LLM API HTTP {e.code} ({url}): {err_body[:500]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"LLM API connection failed ({url}): {e.reason}")
    except TimeoutError:
        raise RuntimeError(f"LLM API request timed out after {timeout_sec}s ({url})")


def call_llm(system_prompt, user_prompt, config, extra_env_dirs=None):
    """Send chat completion request according to llm_api_style and return raw response text."""
    style = str(config.get("llm_api_style") or DEFAULT_LLM_CONFIG["llm_api_style"]).strip().lower()
    if style == "disabled":
        raise RuntimeError("LLM integration is disabled (llm_api_style='disabled').")

    base_url = normalize_base_url(config.get("llm_base_url") or DEFAULT_LLM_CONFIG["llm_base_url"])
    model = str(config.get("llm_model") or DEFAULT_LLM_CONFIG["llm_model"]).strip()
    if not model:
        raise RuntimeError("LLM model is not configured ('llm_model' in config.json).")

    try:
        timeout_sec = float(config.get("llm_timeout_sec", DEFAULT_LLM_CONFIG["llm_timeout_sec"]))
    except Exception:
        timeout_sec = 30.0

    api_key, _ = resolve_api_key(config, extra_search_dirs=extra_env_dirs)
    params = _parse_llm_params(config)
    ssl_ctx = _build_ssl_context(config)

    if style == "openai":
        if not base_url:
            base_url = "https://api.openai.com/v1"
        endpoint = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        # Merge user-configured extra params without overwriting required fields
        for k, v in params.items():
            if k not in ("model", "messages"):
                payload[k] = v

        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        resp = _http_post_json(endpoint, payload, headers, timeout_sec, ssl_ctx=ssl_ctx)
        choices = resp.get("choices") or []
        if not choices:
            raise RuntimeError(f"LLM API returned no choices: {json.dumps(resp, ensure_ascii=False)[:400]}")
        msg = choices[0].get("message") or {}
        content = msg.get("content")
        if content is None:
            content = choices[0].get("text") or ""
        return str(content)

    elif style == "gemini":
        if not base_url:
            base_url = "https://generativelanguage.googleapis.com/v1beta"
        if not api_key:
            raise RuntimeError("Gemini API key is missing. Set GEMINI_API_KEY or GIT4SW_LLM_API_KEY in .env.")
        endpoint = f"{base_url}/models/{model}:generateContent?key={api_key}"

        gen_cfg = {}
        if "temperature" in params:
            gen_cfg["temperature"] = params["temperature"]
        if "top_p" in params:
            gen_cfg["topP"] = params["top_p"]
        if "max_tokens" in params:
            gen_cfg["maxOutputTokens"] = params["max_tokens"]

        payload = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        }
        if gen_cfg:
            payload["generationConfig"] = gen_cfg

        resp = _http_post_json(endpoint, payload, {}, timeout_sec, ssl_ctx=ssl_ctx)
        candidates = resp.get("candidates") or []
        if not candidates:
            raise RuntimeError(f"Gemini API returned no candidates: {json.dumps(resp, ensure_ascii=False)[:400]}")
        parts = (candidates[0].get("content") or {}).get("parts") or []
        return "".join(str(p.get("text", "")) for p in parts)

    elif style == "anthropic":
        if not base_url:
            base_url = "https://api.anthropic.com/v1"
        if not api_key:
            raise RuntimeError("Anthropic API key is missing. Set ANTHROPIC_API_KEY or GIT4SW_LLM_API_KEY in .env.")
        endpoint = base_url if base_url.endswith("/messages") else f"{base_url}/messages"

        payload = {
            "model": model,
            "system": system_prompt,
            "max_tokens": int(params.get(
                "max_tokens", DEFAULT_LLM_CONFIG["llm_params"]["max_tokens"])),
            "messages": [{"role": "user", "content": user_prompt}],
        }
        if "temperature" in params:
            payload["temperature"] = params["temperature"]
        if "top_p" in params:
            payload["top_p"] = params["top_p"]

        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
        resp = _http_post_json(endpoint, payload, headers, timeout_sec, ssl_ctx=ssl_ctx)
        content_blocks = resp.get("content") or []
        return "".join(str(b.get("text", "")) for b in content_blocks if isinstance(b, dict))

    else:
        raise RuntimeError(f"Unsupported llm_api_style: '{style}'. Expected 'openai', 'gemini', or 'anthropic'.")


def generate_commit_message(
    diff_payload,
    config,
    commit_presets=None,
    selected_prefix="",
    user_hint="",
    extra_env_dirs=None,
    app_dir=None,
):
    """End-to-end helper: build prompt -> call LLM -> parse (summary, description)."""
    system_prompt, user_prompt = build_messages(
        diff_payload=diff_payload,
        config=config,
        commit_presets=commit_presets,
        selected_prefix=selected_prefix,
        user_hint=user_hint,
        app_dir=app_dir,
    )
    raw = call_llm(system_prompt, user_prompt, config, extra_env_dirs=extra_env_dirs)
    summary, description = parse_commit_message(raw, selected_prefix=selected_prefix)
    if not summary:
        raise RuntimeError("LLM returned an empty commit message.")
    return summary, description


def test_llm_connection(config, extra_env_dirs=None):
    """Send a minimal probe request to verify connectivity and return (ok, message)."""
    provider = config.get("llm_provider", "strata")
    style = config.get("llm_api_style", "openai")
    model = config.get("llm_model", "")
    url = normalize_base_url(config.get("llm_base_url", ""))
    _, key_source = resolve_api_key(config, extra_search_dirs=extra_env_dirs)

    probe_cfg = dict(config)
    probe_params = _parse_llm_params(config)
    probe_params["max_tokens"] = min(int(probe_params.get("max_tokens", 64)), 64)
    probe_cfg["llm_params"] = probe_params
    # Keep the connectivity probe snappy even though generation requests may
    # legitimately run for several minutes with large max_tokens values.
    try:
        probe_timeout = float(config.get(
            "llm_timeout_sec", DEFAULT_LLM_CONFIG["llm_timeout_sec"]))
    except Exception:
        probe_timeout = DEFAULT_LLM_CONFIG["llm_timeout_sec"]
    probe_cfg["llm_timeout_sec"] = min(probe_timeout, 30.0)

    t0 = time.monotonic()
    try:
        reply = call_llm(
            "Reply with only 'OK'.",
            "Connection test. Reply OK.",
            probe_cfg,
            extra_env_dirs=extra_env_dirs,
        )
        elapsed = time.monotonic() - t0
        cleaned = strip_thinking_and_fences(reply).splitlines()[0] if strip_thinking_and_fences(reply) else "(empty)"
        return True, (
            f"Connected to {provider} ({style}) | model={model} | url={url} | "
            f"key={key_source} | {elapsed:.2f}s | reply='{cleaned[:40]}'"
        )
    except Exception as e:
        elapsed = time.monotonic() - t0
        return False, f"Failed ({provider}/{style}, {elapsed:.2f}s, key={key_source}): {e}"
