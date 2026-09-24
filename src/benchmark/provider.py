import os
import json
import shutil
from pathlib import Path
import subprocess
import tempfile
import time
from dataclasses import dataclass
from dotenv import load_dotenv

import requests

load_dotenv()

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


@dataclass
class GenerationResult:
    output: str
    model: str

    input_tokens: int | None
    output_tokens: int | None
    cost: float | None

    latency: float
    transcript: list | None = None


class OpenRouterProvider:
    def __init__(self, api_key: str | None = None, max_retries=5, backoff_seconds=1, timeout=300, max_retry_wait_seconds=60):
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self.timeout = timeout
        self.max_retry_wait_seconds = max_retry_wait_seconds
        if backoff_seconds < 0 or max_retry_wait_seconds <= 0:
            raise ValueError("Retry delays must be non-negative and the retry wait limit positive")
        if type(max_retries) is not int or max_retries < 1:
            raise ValueError("max_retries must be positive")
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY")

        if not self.api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not set")

    def _wait_before_retry(self, attempt, response=None):
        from email.utils import parsedate_to_datetime
        delay = min(self.backoff_seconds * 2 ** attempt, self.max_retry_wait_seconds)
        if response is not None:
            value = response.headers.get("Retry-After")
            if isinstance(value, str):
                try:
                    retry_after = float(value)
                except ValueError:
                    try:
                        retry_after = parsedate_to_datetime(value).timestamp() - time.time()
                    except (TypeError, ValueError, OverflowError):
                        retry_after = 0
                if retry_after > self.max_retry_wait_seconds:
                    raise RuntimeError(f"OpenRouter Retry-After is {retry_after:g}s, above max_retry_wait_seconds; retry later")
                delay = max(delay, retry_after)
        print(f"Temporary OpenRouter failure; retry {attempt + 2}/{self.max_retries} in {delay:g}s", flush=True)
        time.sleep(delay)

    def generate(
        self,
        model: str,
        system: str,
        prompt: str,
        temperature: float,
        max_tokens: int,
        workspace: str | None = None,
    ) -> GenerationResult:

        started = time.perf_counter()

        max_retries = self.max_retries

        for attempt in range(max_retries):

            try:
                response = requests.post(
                    OPENROUTER_URL,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": [
                            {
                                "role": "system",
                                "content": system,
                            },
                            {
                                "role": "user",
                                "content": prompt,
                            },
                        ],
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                        "usage": {
                            "include": True
                        },
                    },
                    timeout=self.timeout,
                )
            except (requests.Timeout, requests.ConnectionError) as exc:
                if attempt == max_retries - 1:
                    raise RuntimeError(f"OpenRouter transport failure: {exc}") from exc
                self._wait_before_retry(attempt)
                continue

            if response.status_code in (429, 502, 503, 504) and attempt < max_retries - 1:
                self._wait_before_retry(attempt, response)
                continue
            try:
                data = response.json()
            except ValueError as exc:
                raise RuntimeError(f"OpenRouter HTTP {response.status_code}: {response.text[:500]}") from exc
            if not isinstance(data, dict):
                raise RuntimeError("OpenRouter returned a non-object response")
            if response.status_code >= 400 and "error" not in data:
                raise RuntimeError(f"OpenRouter HTTP {response.status_code}: {data}")

            # OpenRouter или upstream provider вернул ошибку
            if "error" in data:

                error = data["error"]
                code = str(error.get("code")) if isinstance(error, dict) else ""

                # Временные ошибки — пробуем ещё раз
                if (
                    code in ("429", "502", "503", "504")
                    and attempt < max_retries - 1
                ):
                    self._wait_before_retry(attempt, response)
                    continue

                # Ошибка не временная
                # или закончились попытки
                raise RuntimeError(
                    f"OpenRouter error: {error}"
                )

            # Ответ формально пришёл,
            # но модель ничего не сгенерировала
            if not data.get("choices"):
                raise RuntimeError(
                    f"OpenRouter returned no choices:\n{data}"
                )

            # Всё успешно — выходим из retry-цикла
            break

        latency = time.perf_counter() - started

        usage = data.get("usage") or {}
        if not isinstance(data["choices"][0].get("message", {}).get("content"), str):
            raise RuntimeError("OpenRouter returned no text content")

        return GenerationResult(
            output=data["choices"][0]["message"]["content"],
            model=data.get("model", model),
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            cost=usage.get("cost"),
            latency=latency,
        )


class CodexCLIProvider:
    """Codex in a Linux mount namespace; cwd alone cannot hide references."""
    supports_agent = True

    def __init__(self, executable="codex", timeout=600, auth_file=None):
        self.executable = executable
        self.timeout = timeout
        self.auth_file = Path(auth_file).expanduser() if auth_file else Path.home() / ".codex/auth.json"

    def generate(self, model, system, prompt, temperature, max_tokens, workspace=None):
        started = time.perf_counter()
        executable = shutil.which(self.executable)
        if not executable or not shutil.which("bwrap"):
            raise RuntimeError("Codex execution requires codex and bubblewrap (bwrap) on Linux")
        executable = str(Path(executable).resolve())
        if not executable.startswith("/usr/"):
            raise RuntimeError("Install Codex and its runtime under /usr (for example /usr/local); other host directories are not exposed")
        with tempfile.TemporaryDirectory(prefix="skill-codex-") as temporary:
            root = Path(temporary)
            work = Path(workspace).resolve() if workspace else root / "workspace"
            work.mkdir(exist_ok=True)
            home = root / "home"
            (home / ".codex").mkdir(parents=True)
            if self.auth_file.is_file():
                shutil.copy2(self.auth_file, home / ".codex/auth.json")
            command = ["bwrap", "--die-with-parent", "--unshare-pid", "--unshare-ipc", "--unshare-uts"]
            for directory in ("/usr", "/bin", "/lib", "/lib64"):
                if Path(directory).exists():
                    command += ["--ro-bind", directory, directory]
            for name in ("resolv.conf", "hosts", "nsswitch.conf", "ssl", "ca-certificates"):
                path = Path("/etc") / name
                if path.exists():
                    command += ["--ro-bind", str(path), str(path)]
            command += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
                        "--bind", str(home), "/home/agent", "--bind", str(work), "/workspace"]
            for name in ("inputs", "skill"):
                if (work / name).exists():
                    command += ["--ro-bind", str(work / name), f"/workspace/{name}"]
            command += ["--chdir", "/workspace", "--clearenv", "--setenv", "HOME", "/home/agent",
                        "--setenv", "PATH", "/usr/local/bin:/usr/bin:/bin",
                        "--setenv", "CODEX_HOME", "/home/agent/.codex"]
            for name in ("CODEX_API_KEY", "OPENAI_API_KEY"):
                if os.getenv(name):
                    command += ["--setenv", name, os.environ[name]]
            command += [executable, "exec", "--ephemeral", "--skip-git-repo-check",
                        "--sandbox", "workspace-write", "--json", "-m", model,
                        "--output-last-message", "/home/agent/response.txt", "-"]
            process = subprocess.run(command, input=f"SKILL INSTRUCTIONS:\n{system}\n\nTASK:\n{prompt}",
                                     capture_output=True, text=True, timeout=self.timeout)
            if process.returncode:
                raise RuntimeError(f"Codex failed ({process.returncode}): {process.stderr[-2000:]}")
            events = [json.loads(line) for line in process.stdout.splitlines() if line.strip()]
            usage = next((event.get("usage", {}) for event in reversed(events)
                          if event.get("type") == "turn.completed"), {})
            return GenerationResult(
                output=(home / "response.txt").read_text(encoding="utf-8"), model=model,
                input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens"),
                cost=None, latency=time.perf_counter() - started, transcript=events,
            )


class FakeProvider:
    """Scripted offline provider. It never reads references or invents judge scores."""
    supports_agent = True

    def __init__(self, responses=None, files=None):
        self.responses = list(responses or [])
        self.files = files or {}
        self.calls = []

    def generate(self, model, system, prompt, temperature, max_tokens, workspace=None):
        from .case import checked_path
        self.calls.append({"system": system, "prompt": prompt, "workspace": workspace})
        if not self.responses:
            raise RuntimeError("FakeProvider responses exhausted")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if workspace:
            for name, content in self.files.items():
                path = checked_path(Path(workspace) / "outputs", name)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
        return GenerationResult(response, model, None, None, None, 0.0)


PROVIDERS = {"openrouter": OpenRouterProvider, "codex": CodexCLIProvider, "fake": FakeProvider}


def make_provider(name, config=None):
    if name not in PROVIDERS:
        raise ValueError(f"Unknown provider: {name}")
    return PROVIDERS[name](**(config or {}))
