"""Local LLM client (Ollama). The application never depends on it: every caller has a deterministic fallback.
Credentials, database connections and raw tables are never sent to the model."""
import json
import urllib.request
import urllib.error

from ..config import settings


class AIUnavailable(Exception):
    pass


class OllamaClient:
    def __init__(self, base_url=None, model=None, timeout=None):
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self.model = model if model is not None else settings.ollama_model
        self.timeout = timeout or settings.ollama_timeout
        self._resolved_model = None

    def _req(self, path, body=None, timeout=None):
        if not settings.ai_enabled or settings.embedded:
            raise AIUnavailable("AI disabled in this environment")
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base_url + path, data=data, headers={"Content-Type": "application/json"},
                                     method="POST" if data else "GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                return json.loads(r.read().decode())
        except Exception as e:  # network error, timeout, bad JSON
            raise AIUnavailable(str(e)[:200])

    def status(self) -> dict:
        try:
            tags = self._req("/api/tags", timeout=2)
            names = [m.get("name") for m in tags.get("models", [])]
            model = self.model or (names[0] if names else "")
            ok = bool(model) and (not self.model or any(n == self.model or n.startswith(self.model + ":") for n in names))
            return {"available": ok, "model": model or None, "models": names[:10], "base_url": self.base_url}
        except AIUnavailable as e:
            return {"available": False, "model": self.model or None, "error": str(e), "base_url": self.base_url}

    def model_name(self) -> str:
        if self.model:
            return self.model
        if not self._resolved_model:
            st = self.status()
            if not st["available"]:
                raise AIUnavailable("No local model available")
            self._resolved_model = st["model"]
        return self._resolved_model

    def chat_json(self, system: str, user: str) -> dict:
        r = self._req("/api/chat", {"model": self.model_name(), "stream": False, "format": "json",
                                    "options": {"temperature": 0},
                                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
        try:
            return json.loads(r["message"]["content"])
        except Exception:
            raise AIUnavailable("Model returned malformed JSON")

    def chat_text(self, system: str, user: str) -> str:
        r = self._req("/api/chat", {"model": self.model_name(), "stream": False, "options": {"temperature": 0.2},
                                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
        txt = (r.get("message") or {}).get("content", "").strip()
        if not txt:
            raise AIUnavailable("Empty response")
        return txt[:1500]


client = OllamaClient()
