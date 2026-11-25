"""Tiny DeepSeek client (OpenAI-compatible chat API, JSON mode)."""
import json, time
import requests

URL = "https://api.deepseek.com/chat/completions"


def chat_json(cfg, system, user, temperature=0.3, retries=3):
    key = cfg.get("deepseek_key")
    if not key: raise SystemExit("deepseek_key is empty in config.yaml")
    body = dict(model=cfg.get("deepseek_model", "deepseek-chat"), temperature=temperature,
                response_format={"type": "json_object"},
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
    last = None
    for i in range(retries):
        try:
            r = requests.post(URL, json=body, timeout=180, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            if r.status_code == 401: raise SystemExit("DeepSeek: API key is wrong (check deepseek_key in config.yaml)")
            if r.status_code == 402: raise SystemExit("DeepSeek: account balance is empty")
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"]
            return json.loads(text[text.find("{"): text.rfind("}") + 1])
        except SystemExit: raise
        except Exception as e:
            last = e; time.sleep(2 * (i + 1))
    raise SystemExit(f"DeepSeek request failed: {last}")
