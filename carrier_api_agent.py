################################################################################################
# Temporary local environment variables for running this script:
#
# export OPENAI_API_KEY="sk-..."   # without this, the script will use fallback mode
# export POSTNL_APIKEY="..."
# export POSTNL_CUSTOMER_CODE="..."
# export POSTNL_CUSTOMER_NUMBER="..."
# export POSTNL_REFERENCE="..."
# export POSTNL_BASE_URL="https://api-sandbox.postnl.nl"
#
# Note: Fallback mode is used when:
#   - No OPENAI_API_KEY is set in the environment
#   - openai API request fails
################################################################################################



from __future__ import annotations
import json, os, sys, time, tempfile, subprocess, re
from pathlib import Path
from typing import Any, Optional

import requests

DOCS_URL = "https://developer.postnl.nl/docs/#/http/api-endpoints/send-track/shippingstatus/get-status-by-reference"
ARTIFACTS = Path("artifacts")
ARTIFACTS.mkdir(exist_ok=True)

# ----- small utils -----
def save_text(p: Path, s: str) -> None:
    p.write_text(s, encoding="utf-8")

def save_json(p: Path, o: Any) -> None:
    p.write_text(json.dumps(o, ensure_ascii=False, indent=2), encoding="utf-8")

def fetch_docs(url: str) -> str:
    """Save docs HTML for audit; continue even if it fails."""
    try:
        r = requests.get(url, headers={"User-Agent": "Carrier-Agent/1.0"}, timeout=30)
        r.raise_for_status()
        html = r.text
    except Exception as e:
        html = f"/* docs fetch error: {e} */"
    save_text(ARTIFACTS / "docs.html", html)
    return html

# ----- optional LLM wrapper -----
class LLM:
    """Uses OpenAI when OPENAI_API_KEY is set; otherwise disabled."""
    def __init__(self, model: Optional[str] = None):
        self.model = model or os.getenv("LLM_MODEL", "gpt-4o-mini")
        self.client = None
        try:
            from openai import OpenAI  # lazy import
            api_key = os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_APIKEY")
            if api_key:
                self.client = OpenAI(api_key=api_key)
        except Exception:
            self.client = None

    @property
    def available(self) -> bool:
        return self.client is not None

    def respond(self, prompt: str, temperature: float = 0.2, max_tokens: int = 1400) -> str:
        """Return model text; on any error, record and return empty string."""
        if not self.available:
            return ""
        try:
            resp = self.client.responses.create(
                model=self.model,
                input=prompt,
                temperature=temperature,
                max_output_tokens=max_tokens,
            )
            return getattr(resp, "output_text", "").strip()
        except Exception as e:
            save_text(ARTIFACTS / "llm_error.txt", f"{type(e).__name__}: {e}")
            return ""

# ----- standalone client used as fallback -----
FALLBACK_SCRIPT = r'''
# Standalone client. Reads env and prints one JSON line.
import json, time, os, sys, requests

def main():
    base_url = os.getenv("POSTNL_BASE_URL", "https://api-sandbox.postnl.nl")
    apikey = os.getenv("POSTNL_APIKEY")
    customer_code = os.getenv("POSTNL_CUSTOMER_CODE")
    customer_number = os.getenv("POSTNL_CUSTOMER_NUMBER")
    reference = os.getenv("POSTNL_REFERENCE")
    if not all([apikey, customer_code, customer_number, reference]):
        print(json.dumps({"http_status": -1, "error": "missing env vars"})); sys.exit(2)

    url = f"{base_url}/shipment/v2/status/reference/{reference}"
    params = {
        "detail": "true",
        "language": "NL",
        "customerCode": customer_code,
        "customerNumber": customer_number,
        "maxDays": "14",  # sandbox often needs this
    }
    headers = {"Accept": "application/json", "apikey": apikey}

    t0 = time.time()
    try:
        r = requests.get(url, headers=headers, params=params, timeout=25)
        raw = r.text
        try:
            body = r.json()
        except Exception:
            body = None  # not JSON

        # derive status_code for success rule:
        derived_status = None
        if isinstance(body, dict):
            if isinstance(body.get("status_code"), int):
                derived_status = body.get("status_code")
            elif r.status_code == 200 and any(k in body for k in ("CompleteStatus","CurrentStatus","Warnings")):
                derived_status = 200

        out = {
            "http_status": r.status_code,
            "url": r.url,
            "elapsed_ms": int((time.time() - t0)*1000),
            "status_code": derived_status,
            "result": body if isinstance(body, dict) else None,
            "content_type": r.headers.get("Content-Type"),
        }
        if body is None:
            out["raw_preview"] = raw[:400]
        print(json.dumps(out, ensure_ascii=False))
        sys.exit(0)
    except Exception as e:
        print(json.dumps({"http_status": -1, "error": str(e)}))
        sys.exit(1)

if __name__ == "__main__":
    main()
'''.strip()

# ----- LLM codegen / judge (both optional with safe fallbacks) -----
def llm_generate_script(docs_html: str, last_output: str = "") -> str:
    """Ask LLM for a standalone client script; fall back to template on any issue."""
    llm = LLM()
    if not llm.available:
        return FALLBACK_SCRIPT
    sys_prompt = (
        "Write a single-file Python script that calls PostNL 'shipping status by reference'. "
        "Read env vars: POSTNL_APIKEY, POSTNL_CUSTOMER_CODE, POSTNL_CUSTOMER_NUMBER, POSTNL_REFERENCE. "
        "Default POSTNL_BASE_URL='https://api-sandbox.postnl.nl'. "
        "Use GET with params: detail=true, language=NL, customerCode, customerNumber, maxDays=14. "
        "Print exactly one JSON line: {http_status, status_code, result, url, elapsed_ms}. "
        "On error print JSON with http_status=-1 and error. No extra prints. No code fences."
    )
    user = "Docs:\n" + docs_html[:3000]
    if last_output:
        user += "\n\nPrevious output to fix:\n" + last_output[:2000]
    code = llm.respond(sys_prompt + "\n\n" + user, temperature=0.2)
    code = re.sub(r"(?s)^```(?:python)?\n|\n```$", "", code).strip()
    return code or FALLBACK_SCRIPT

def llm_judge(output_text: str) -> dict:
    """Try LLM-based verdict; fall back to an empty verdict on error."""
    llm = LLM()
    if not llm.available:
        return {}
    prompt = (
        "Return JSON only with keys: "
        '{"success": true|false, "reasons": [], "patch_hint": ""}. '
        "Success criteria: http_status==200 AND status_code==200. "
        "Output:\n" + output_text[:2000]
    )
    raw = llm.respond(prompt, temperature=0.0, max_tokens=500)
    try:
        return json.loads(raw)
    except Exception:
        return {}

# ----- runner / evaluation -----
def run_script(code: str) -> tuple[int, str]:
    """Write code to a temp file and execute it with current Python."""
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(code)
        tmp = f.name
    try:
        proc = subprocess.run([sys.executable, "-u", tmp],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, timeout=120)
        return proc.returncode, proc.stdout
    finally:
        try:
            os.unlink(tmp)
        except Exception:
            pass

def parse_last_json_blob(text: str):
    """Pick the last {...} block and try to parse it."""
    matches = list(re.finditer(r"\{.*\}", text, flags=re.DOTALL))
    if not matches:
        return None
    for m in reversed(matches):
        try:
            return json.loads(m.group(0))
        except Exception:
            continue
    return None

def is_success_rule(output_text: str) -> tuple[bool, dict]:
    """Rule: HTTP==200 and status_code==200."""
    save_text(ARTIFACTS / "last_output.txt", output_text)
    data = parse_last_json_blob(output_text) or {}
    http_ok = (data.get("http_status") == 200)
    status_ok = (data.get("status_code") == 200)
    return (http_ok and status_ok), data

# ----- main -----
def mask(s: str) -> str:
    return ("*"*(len(s)-4)+s[-4:]) if s and len(s) >= 4 else (s or "")

def main() -> None:
    # env preview (masked); helps debug without leaking secrets
    preview = {
        "POSTNL_APIKEY": mask(os.getenv("POSTNL_APIKEY", "")),
        "POSTNL_CUSTOMER_CODE": os.getenv("POSTNL_CUSTOMER_CODE"),
        "POSTNL_CUSTOMER_NUMBER": os.getenv("POSTNL_CUSTOMER_NUMBER"),
        "POSTNL_REFERENCE": os.getenv("POSTNL_REFERENCE"),
        "POSTNL_BASE_URL": os.getenv("POSTNL_BASE_URL", "https://api-sandbox.postnl.nl"),
        "OPENAI_API_KEY?": "yes" if os.getenv("OPENAI_API_KEY") else "no",
    }
    save_json(ARTIFACTS / "env_preview.json", preview)

    docs = fetch_docs(DOCS_URL)

    attempts = []
    last_output = ""
    for attempt in range(1, 4):
        # generate client (LLM if possible, else fallback)
        code = llm_generate_script(docs, last_output)

        # run client
        rc, out = run_script(code)

        # evaluate (try LLM, then fallback to rule)
        verdict = llm_judge(out)
        if isinstance(verdict, dict) and "success" in verdict:
            ok = bool(verdict.get("success"))
        else:
            ok, _ = is_success_rule(out)

        attempts.append({"attempt": attempt, "rc": rc, "ok": ok, "raw": out[:2000]})
        save_json(ARTIFACTS / "attempts_log.json", attempts)
        save_text(ARTIFACTS / "last_output.txt", out)

        if ok:
            Path(ARTIFACTS / "generated_postnl_tracking.py").write_text(code, encoding="utf-8")
            print("SUCCESS")
            return

        last_output = out  # use previous output to improve next generation

    print("FAIL")

if __name__ == "__main__":
    main()
