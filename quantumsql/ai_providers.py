"""AI providers for the SQL assistant: Anthropic (official SDK), Google Gemini and any OpenAI-compatible API
(OpenAI, OpenRouter, Groq, DeepSeek, Ollama, LM Studio …). Every function here runs in a worker thread."""
import json
import os
import urllib.error
import urllib.request

from .config import protect_secret, settings, unprotect_secret

# kind: "anthropic" (SDK), "gemini" (REST) or "openai" (OpenAI-compatible chat/completions REST)
PROVIDERS = {
    "anthropic": {"label": "Anthropic — Claude", "kind": "anthropic", "base": "",
                  "models": ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1"],
                  "key_url": "https://console.anthropic.com/settings/keys", "env": "ANTHROPIC_API_KEY"},
    "openai": {"label": "OpenAI — ChatGPT", "kind": "openai", "base": "https://api.openai.com/v1",
               "models": ["gpt-5", "gpt-5-mini", "gpt-4.1", "gpt-4o"],
               "key_url": "https://platform.openai.com/api-keys", "env": "OPENAI_API_KEY"},
    "gemini": {"label": "Google — Gemini", "kind": "gemini",
               "base": "https://generativelanguage.googleapis.com/v1beta",
               "models": ["gemini-2.5-pro", "gemini-2.5-flash"],
               "key_url": "https://aistudio.google.com/apikey", "env": "GEMINI_API_KEY"},
    "openrouter": {"label": "OpenRouter (many models, one key)", "kind": "openai",
                   "base": "https://openrouter.ai/api/v1", "models": [],
                   "key_url": "https://openrouter.ai/keys", "env": "OPENROUTER_API_KEY"},
    "groq": {"label": "Groq", "kind": "openai", "base": "https://api.groq.com/openai/v1",
             "models": ["llama-3.3-70b-versatile"], "key_url": "https://console.groq.com/keys",
             "env": "GROQ_API_KEY"},
    "deepseek": {"label": "DeepSeek", "kind": "openai", "base": "https://api.deepseek.com/v1",
                 "models": ["deepseek-chat", "deepseek-reasoner"], "key_url": "https://platform.deepseek.com/api_keys",
                 "env": "DEEPSEEK_API_KEY"},
    "ollama": {"label": "Ollama — local, offline (no key)", "kind": "openai", "base": "http://localhost:11434/v1",
               "models": ["qwen2.5-coder", "llama3.1"], "key_url": "https://ollama.com/download", "env": "",
               "no_key": True},
    "custom": {"label": "Custom OpenAI-compatible server", "kind": "openai", "base": "http://localhost:1234/v1",
               "models": [], "key_url": "", "env": "", "no_key": True},
}

# Claude models that accept output_config.effort and the server-side refusal fallback.
_EFFORT_MODELS = ("claude-opus-5", "claude-sonnet-5", "claude-haiku-5", "claude-fable-5", "claude-mythos-5",
                  "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-4-6")
_FALLBACK_MODELS = ("claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-1")


class AiError(Exception):
    pass


# ── configuration ────────────────────────────────────────────────────────────
def _ai():
    cfg = settings.get("ai") or {}
    if not isinstance(cfg, dict):
        cfg = {}
    keys = settings.get("ai_keys") or {}
    # Early versions kept keys in plain text (ai_api_key, then copied as-is into ai_keys): encrypt them once.
    legacy = settings.data.pop("ai_api_key", None)
    if legacy and "anthropic" not in keys:
        keys["anthropic"] = legacy
    plain = {pid: v for pid, v in keys.items() if v and not v.startswith(("dpapi:", "fernet:", "b64:"))}
    if legacy or plain:
        keys.update({pid: protect_secret(v) for pid, v in plain.items()})
        settings.set("ai_keys", keys)
    return cfg, keys


def current():
    """Active configuration: dict(provider, label, kind, base, model, key)."""
    cfg, keys = _ai()
    pid = cfg.get("provider") if cfg.get("provider") in PROVIDERS else "anthropic"
    return config_for(pid)


def config_for(pid):
    cfg, keys = _ai()
    p = PROVIDERS[pid]
    key = unprotect_secret(keys.get(pid, "")) or (os.environ.get(p["env"], "") if p["env"] else "")
    model = (cfg.get("models") or {}).get(pid) or (p["models"][0] if p["models"] else "")
    base = (cfg.get("bases") or {}).get(pid) or p["base"]
    return {"provider": pid, "label": p["label"], "kind": p["kind"], "base": base, "model": model, "key": key,
            "no_key": p.get("no_key", False)}


def save(pid, key, model, base):
    cfg, keys = _ai()
    cfg["provider"] = pid
    cfg.setdefault("models", {})[pid] = model
    if base:
        cfg.setdefault("bases", {})[pid] = base
    keys[pid] = protect_secret(key) if key else ""
    settings.set("ai", cfg)
    settings.set("ai_keys", keys)


def ready(cfg):
    if not cfg["model"]:
        return "Choose a model in AI assistant settings."
    if not cfg["key"] and not cfg["no_key"]:
        return f"Add your {cfg['label']} API key in AI assistant settings."
    return ""


# ── HTTP helpers ─────────────────────────────────────────────────────────────
def _request(url, headers, body=None, timeout=180):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET",
                                 headers={"Content-Type": "application/json", "User-Agent": "QuantumSQL", **headers})
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        raise AiError(_http_error(exc)) from None
    except urllib.error.URLError as exc:
        raise AiError(f"Could not reach {url.split('/')[2]}: {exc.reason}") from None


def _http_error(exc):
    raw = exc.read().decode("utf-8", "replace")
    msg = raw
    try:
        obj = json.loads(raw)
        if isinstance(obj, list) and obj:
            obj = obj[0]
        err = obj.get("error", obj)
        msg = err.get("message") if isinstance(err, dict) else str(err)
    except (ValueError, AttributeError):
        pass
    if exc.code in (401, 403):
        return f"The API key was rejected (HTTP {exc.code}). Check the key in AI assistant settings. {msg}"
    if exc.code == 404:
        return f"Model or endpoint not found (HTTP 404). Pick another model or press Refresh. {msg}"
    if exc.code == 429:
        return f"Rate limit or quota exceeded (HTTP 429). Wait a moment or check your plan. {msg}"
    return f"API error (HTTP {exc.code}): {msg}"


def _sse(resp, task):
    """Yield decoded JSON objects from a server-sent-events response."""
    for raw in resp:
        if task is not None and task.cancelled:
            return
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            return
        try:
            yield json.loads(data)
        except ValueError:
            continue


# ── streaming answers ────────────────────────────────────────────────────────
def stream_answer(task, cfg, system_text, context_text, prompt):
    """Stream the answer through task.progress(0, text). Returns the full text."""
    fn = {"anthropic": _anthropic, "gemini": _gemini, "openai": _openai}[cfg["kind"]]
    return fn(task, cfg, system_text, context_text, prompt)


def _emit(task, text):
    if task is not None:
        task.progress(0, text)


def _anthropic(task, cfg, system_text, context_text, prompt):
    import anthropic
    client = anthropic.Anthropic(api_key=cfg["key"] or None, max_retries=2)
    model = cfg["model"]
    kwargs = {"model": model, "max_tokens": 16000,
              "system": [{"type": "text", "text": system_text},
                         {"type": "text", "text": context_text, "cache_control": {"type": "ephemeral"}}],
              "messages": [{"role": "user", "content": prompt}]}
    if model.startswith(_EFFORT_MODELS):
        kwargs["output_config"] = {"effort": "medium"}
    text = ""
    try:
        if model in _FALLBACK_MODELS:
            ctx = client.beta.messages.stream(betas=["server-side-fallback-2026-07-01"], fallbacks="default",
                                              **kwargs)
        else:
            ctx = client.messages.stream(**kwargs)
        with ctx as stream:
            for chunk in stream.text_stream:
                if task is not None and task.cancelled:
                    return text + "\n\n*(stopped)*"
                text += chunk
                _emit(task, text)
            final = stream.get_final_message()
    except anthropic.AuthenticationError:
        raise AiError("The API key was rejected. Check the Anthropic key in AI assistant settings.") from None
    except anthropic.NotFoundError:
        raise AiError(f"Model “{model}” was not found. Pick another model or press Refresh.") from None
    except anthropic.RateLimitError:
        raise AiError("Rate limit exceeded. Wait a moment and try again.") from None
    except anthropic.APIConnectionError:
        raise AiError("Could not reach the Anthropic API. Check the internet connection or proxy.") from None
    except anthropic.APIStatusError as exc:
        raise AiError(f"Anthropic API error (HTTP {exc.status_code}): {exc.message}") from None
    if final.stop_reason == "refusal":
        details = getattr(final, "stop_details", None)
        return text + f"\n\n*The model declined this request: {getattr(details, 'explanation', None) or 'policy'}*"
    if final.stop_reason == "max_tokens":
        text += "\n\n*(The answer was cut off. Ask a narrower question.)*"
    return text


def _openai(task, cfg, system_text, context_text, prompt):
    headers = {"Accept": "text/event-stream"}
    if cfg["key"]:
        headers["Authorization"] = f"Bearer {cfg['key']}"
    if cfg["provider"] == "openrouter":
        headers["X-Title"] = "Quantum SQL"
    body = {"model": cfg["model"], "stream": True,
            "messages": [{"role": "system", "content": f"{system_text}\n\n{context_text}"},
                         {"role": "user", "content": prompt}]}
    resp = _request(cfg["base"].rstrip("/") + "/chat/completions", headers, body)
    text, finish = "", None
    with resp:
        for obj in _sse(resp, task):
            if obj.get("error"):
                err = obj["error"]
                raise AiError(f"API error: {err.get('message') if isinstance(err, dict) else err}")
            for choice in obj.get("choices") or []:
                piece = (choice.get("delta") or {}).get("content")
                if piece:
                    text += piece
                    _emit(task, text)
                finish = choice.get("finish_reason") or finish
    if task is not None and task.cancelled:
        return text + "\n\n*(stopped)*"
    if finish == "length":
        text += "\n\n*(The answer was cut off. Ask a narrower question.)*"
    elif finish == "content_filter":
        text += "\n\n*The provider's content filter stopped this answer.*"
    return text


def _gemini(task, cfg, system_text, context_text, prompt):
    url = f"{cfg['base'].rstrip('/')}/models/{cfg['model']}:streamGenerateContent?alt=sse"
    body = {"systemInstruction": {"parts": [{"text": f"{system_text}\n\n{context_text}"}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}]}
    resp = _request(url, {"x-goog-api-key": cfg["key"]}, body)
    text, finish = "", None
    with resp:
        for obj in _sse(resp, task):
            block = (obj.get("promptFeedback") or {}).get("blockReason")
            if block:
                raise AiError(f"Gemini blocked the request ({block}).")
            for cand in obj.get("candidates") or []:
                for part in (cand.get("content") or {}).get("parts") or []:
                    if part.get("text") and not part.get("thought"):
                        text += part["text"]
                        _emit(task, text)
                finish = cand.get("finishReason") or finish
    if task is not None and task.cancelled:
        return text + "\n\n*(stopped)*"
    if finish == "MAX_TOKENS":
        text += "\n\n*(The answer was cut off. Ask a narrower question.)*"
    elif finish in ("SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT"):
        text += f"\n\n*Gemini stopped this answer ({finish}).*"
    return text


# ── model lists ──────────────────────────────────────────────────────────────
def list_models(cfg):
    """Models the key can use, newest-looking first where the API does not sort."""
    if cfg["kind"] == "anthropic":
        import anthropic
        client = anthropic.Anthropic(api_key=cfg["key"] or None, max_retries=1)
        try:
            return [m.id for m in client.models.list(limit=100)]
        except anthropic.AuthenticationError:
            raise AiError("The API key was rejected. Check the Anthropic key.") from None
        except anthropic.APIConnectionError:
            raise AiError("Could not reach the Anthropic API.") from None
    if cfg["kind"] == "gemini":
        with _request(f"{cfg['base'].rstrip('/')}/models?pageSize=200", {"x-goog-api-key": cfg["key"]},
                      timeout=30) as resp:
            data = json.load(resp)
        return [m["name"].split("/", 1)[-1] for m in data.get("models", [])
                if "generateContent" in (m.get("supportedGenerationMethods") or [])]
    headers = {"Authorization": f"Bearer {cfg['key']}"} if cfg["key"] else {}
    with _request(cfg["base"].rstrip("/") + "/models", headers, timeout=30) as resp:
        data = json.load(resp)
    return sorted((m["id"] for m in data.get("data", [])), key=str.lower)
