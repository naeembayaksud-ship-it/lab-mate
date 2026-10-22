"""Model backends for Lab Mate. The user picks ONE and supplies their own credentials:

  anthropic : Anthropic API key           (https://console.anthropic.com/)       -> direct HTTPS call
  openai    : any OpenAI-compatible API   (OpenAI, OpenRouter, Ollama, LM Studio...) key + base URL
  hermes    : Hermes CLI                  (sign-in / OAuth is handled by Hermes itself: `hermes login`)

Settings live in app/settings.json (git-ignored, written with owner-only permissions where the OS supports it).
Environment variables override the file: LABMATE_PROVIDER, ANTHROPIC_API_KEY, OPENAI_API_KEY, OPENAI_BASE_URL, LABMATE_MODEL.
The API key is never sent back to the browser; the UI only sees a masked hint.
"""
import json
import os
import shutil
import urllib.error
import urllib.request
from pathlib import Path

SETTINGS = Path(__file__).resolve().parent / "settings.json"
PROVIDERS = ("anthropic", "openai", "hermes")
DEFAULT_MODEL = {"anthropic": "claude-sonnet-4-5", "openai": "gpt-4o", "hermes": ""}
DEFAULT_BASE = {"openai": "https://api.openai.com/v1"}


def _read():
    try:
        return json.loads(SETTINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def current():
    """Effective settings: file first, environment overrides."""
    s = _read()
    prov = os.environ.get("LABMATE_PROVIDER") or s.get("provider") or ""
    if not prov:  # first run: pick whatever is available
        if os.environ.get("ANTHROPIC_API_KEY"):
            prov = "anthropic"
        elif os.environ.get("OPENAI_API_KEY"):
            prov = "openai"
        elif shutil.which("hermes") or os.environ.get("HERMES_BIN"):
            prov = "hermes"
    keys = s.get("keys", {})
    return {
        "provider": prov,
        "model": os.environ.get("LABMATE_MODEL") or (s.get("models", {}).get(prov) or DEFAULT_MODEL.get(prov, "")),
        "anthropic_key": os.environ.get("ANTHROPIC_API_KEY") or keys.get("anthropic", ""),
        "openai_key": os.environ.get("OPENAI_API_KEY") or keys.get("openai", ""),
        "openai_base": os.environ.get("OPENAI_BASE_URL") or s.get("openai_base") or DEFAULT_BASE["openai"],
    }


def _mask(k):
    return "" if not k else (k[:4] + "…" + k[-4:] if len(k) > 12 else "set")


def public():
    """What the browser may see: no secrets."""
    c = current()
    return {
        "provider": c["provider"], "model": c["model"], "openai_base": c["openai_base"],
        "anthropic_key": _mask(c["anthropic_key"]), "openai_key": _mask(c["openai_key"]),
        "hermes_found": bool(shutil.which("hermes") or os.environ.get("HERMES_BIN")),
        "configured": is_configured(c),
        "defaults": DEFAULT_MODEL, "providers": list(PROVIDERS),
    }


def is_configured(c=None):
    c = c or current()
    p = c["provider"]
    if p == "anthropic":
        return bool(c["anthropic_key"])
    if p == "openai":
        return bool(c["openai_key"]) or "localhost" in c["openai_base"] or "127.0.0.1" in c["openai_base"]
    if p == "hermes":
        return bool(shutil.which("hermes") or os.environ.get("HERMES_BIN"))
    return False


def save(d):
    """Merge new settings. Empty key = keep the old one; key '__clear__' = delete it."""
    s = _read()
    prov = d.get("provider")
    if prov:
        if prov not in PROVIDERS:
            raise ValueError("unknown provider")
        s["provider"] = prov
    keys = s.setdefault("keys", {})
    for name in ("anthropic", "openai"):
        v = (d.get(name + "_key") or "").strip()
        if v == "__clear__":
            keys.pop(name, None)
        elif v:
            if len(v) > 300 or any(ch.isspace() for ch in v):
                raise ValueError("that does not look like an API key")
            keys[name] = v
    if d.get("openai_base"):
        b = d["openai_base"].strip().rstrip("/")
        if not b.startswith(("http://", "https://")):
            raise ValueError("base URL must start with http:// or https://")
        s["openai_base"] = b
    if d.get("model") and prov:
        s.setdefault("models", {})[prov] = d["model"].strip()[:100]
    SETTINGS.write_text(json.dumps(s, indent=1), encoding="utf-8")
    try:
        os.chmod(SETTINGS, 0o600)
    except OSError:
        pass


def _post(url, headers, payload, timeout):
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (fixed https endpoints or user-chosen base URL)
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        hint = " Check the API key in Settings." if e.code in (401, 403) else ""
        raise RuntimeError(f"The model provider answered HTTP {e.code}.{hint} {body}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"Could not reach the model provider: {e.reason}") from None


def complete(prompt, timeout=420):
    """Send one prompt, return the text. Only for the anthropic and openai providers."""
    c = current()
    p = c["provider"]
    if p == "anthropic":
        if not c["anthropic_key"]:
            raise RuntimeError("No Anthropic API key. Open Settings (top bar) and paste your key.")
        r = _post("https://api.anthropic.com/v1/messages",
                  {"x-api-key": c["anthropic_key"], "anthropic-version": "2023-06-01", "content-type": "application/json"},
                  {"model": c["model"], "max_tokens": 8000, "messages": [{"role": "user", "content": prompt}]}, timeout)
        return "".join(b.get("text", "") for b in r.get("content", []))
    if p == "openai":
        h = {"content-type": "application/json"}
        if c["openai_key"]:
            h["authorization"] = "Bearer " + c["openai_key"]
        r = _post(c["openai_base"] + "/chat/completions", h,
                  {"model": c["model"], "messages": [{"role": "user", "content": prompt}]}, timeout)
        return (r.get("choices") or [{}])[0].get("message", {}).get("content", "")
    raise RuntimeError("No model provider is set up. Open Settings (top bar) and choose one.")
