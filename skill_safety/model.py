import json
import os
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse


class ModelError(RuntimeError):
    pass


def identity(config):
    if config["backend"] == "mock":
        return {"backend": "mock", "model": "deterministic-workflow-test-only"}
    base = os.environ.get(config["base_url_env"], "").rstrip("/")
    model = os.environ.get(config["model_env"], "")
    parsed = urlparse(base)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username or parsed.password or parsed.query:
        raise ValueError("MODEL_BASE_URL must be an http(s) base URL without credentials or query parameters")
    if not model:
        raise ValueError("Set MODEL_NAME to the server's served model identifier")
    return {"backend": config["backend"], "base_url": base, "model": model}


class Client:
    def __init__(self, config):
        self.config = config
        self.identity = identity(config)

    def chat(self, messages, tools=None):
        if self.config["backend"] == "mock":
            raise ModelError("Mock mode does not issue model requests")
        if len(json.dumps(messages, ensure_ascii=False)) > self.config["max_input_chars"]:
            raise ModelError("Input exceeds configured limit; refusing to truncate package evidence")
        payload = {"model": self.identity["model"], "messages": messages,
                   "temperature": self.config["temperature"], "max_tokens": self.config["max_output_tokens"]}
        extra = self.config.get("model_extra", {})
        if set(extra) & {"model", "messages", "tools", "tool_choice", "stream"}:
            raise ValueError("model_extra cannot replace model, messages or tool protocol")
        payload.update(extra)
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        headers = {"Content-Type": "application/json"}
        key = os.environ.get(self.config["api_key_env"], "")
        if key:
            headers["Authorization"] = "Bearer " + key
        started = time.monotonic()
        attempts = []
        for attempt in range(self.config["http_retries"] + 1):
            request = urllib.request.Request(self.identity["base_url"] + "/chat/completions", data=json.dumps(payload).encode(), headers=headers)
            try:
                with urllib.request.urlopen(request, timeout=self.config["request_timeout"]) as response:
                    raw = json.loads(response.read())
                message = raw["choices"][0]["message"]
                return {"message": message, "usage": raw.get("usage", {}), "reported_model": raw.get("model"),
                        "system_fingerprint": raw.get("system_fingerprint"), "finish_reason": raw["choices"][0].get("finish_reason"),
                        "latency_s": time.monotonic() - started, "http_attempts": attempt + 1, "transient_errors": attempts}
            except urllib.error.HTTPError as exc:
                retryable = exc.code in (429, 500, 502, 503, 504)
                attempts.append({"type": "http", "status": exc.code})
                if not retryable or attempt == self.config["http_retries"]:
                    raise ModelError(f"Model HTTP status {exc.code}; verify endpoint and model configuration") from None
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                attempts.append({"type": type(exc).__name__})
                if attempt == self.config["http_retries"]:
                    raise ModelError("Model transport failed after bounded retries") from None
            except (KeyError, IndexError, TypeError, json.JSONDecodeError):
                raise ModelError("Invalid chat-completions response; check the server protocol") from None
            time.sleep(min(2 ** attempt, 8))


def parse_json(text):
    if not isinstance(text, str):
        raise ValueError("Expected text JSON")
    text = text.strip()
    if text.startswith("```json") and text.endswith("```"):
        text = text[7:-3].strip()
    elif text.startswith("```") and text.endswith("```"):
        text = text[3:-3].strip()
    return json.loads(text)
