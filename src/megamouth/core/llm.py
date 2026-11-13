"""Model gateway: the one choke point for every model call (ARCHITECTURE §9).

- Structured output only: every call names a Pydantic model and the result is
  validated before anyone sees it. Invalid output is retried, never written.
- Policy: a call that carries content from a source not cleared for cloud use is
  refused before anything leaves the machine.
- Manual mode: with no credentials (or `mode = "manual"`), the prompt is written
  to `manual/<hash>.json` so a person can run it elsewhere and paste the answer
  back to `manual/<hash>.answer.json`.
- Every call is logged (model, tokens, prompt hash) to `manual/llm-log.jsonl`
  and stamped into the evidence it produces.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence, TypeVar

from pydantic import BaseModel, ValidationError

from megamouth.core.evidence import SourceRef

T = TypeVar("T", bound=BaseModel)


class CloudRefused(RuntimeError):
    pass


class ManualPending(RuntimeError):
    def __init__(self, path: Path):
        super().__init__(f"model call written to {path}; put the JSON answer in {path.with_suffix('.answer.json')}")
        self.path = path


@dataclass
class Gateway:
    workspace: Path
    model: str = "claude-opus-5-5"
    effort: str = "medium"
    mode: str = "auto"            # auto | cloud | manual
    retries: int = 2

    @property
    def _dir(self) -> Path:
        d = self.workspace / "manual"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _cloud_available(self) -> bool:
        if self.mode == "manual":
            return False
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return self.mode == "cloud" and _raise("install the llm extra: uv sync --extra llm")
        # The SDK also resolves `ant auth login` profiles; an unset key alone is not decisive.
        return self.mode == "cloud" or bool(
            os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
            or os.environ.get("ANTHROPIC_PROFILE")
        )

    def structured(
        self,
        task: str,
        prompt: str,
        output: type[T],
        sources: Sequence[SourceRef],
        images: Sequence[Path] = (),
    ) -> T:
        blocked = [s.id for s in sources if not s.cloud]
        key = hashlib.sha256(
            json.dumps([task, prompt, output.__name__, [s.sha256 for s in sources]]).encode()
        ).hexdigest()[:16]
        answer = self._dir / f"{task}-{key}.answer.json"
        if answer.exists():  # a pasted-back manual answer, or a cached cloud answer
            return output.model_validate_json(answer.read_text(encoding="utf-8"))

        if blocked or not self._cloud_available():
            req = self._dir / f"{task}-{key}.json"
            req.write_text(json.dumps({
                "task": task, "prompt": prompt, "images": [str(i) for i in images],
                "output_schema": output.model_json_schema(),
                "why_manual": f"sources not cleared for cloud: {blocked}" if blocked else "no cloud credentials / manual mode",
            }, indent=2, ensure_ascii=False), encoding="utf-8")
            if blocked and self.mode == "cloud":
                raise CloudRefused(f"policy forbids sending {blocked} to a cloud model")
            raise ManualPending(req)

        result = self._call(prompt, output, images)
        answer.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        return result

    def _call(self, prompt: str, output: type[T], images: Sequence[Path]) -> T:
        import anthropic

        client = anthropic.Anthropic()
        content: list[dict] = []
        for img in images:
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": "image/png",
                "data": base64.b64encode(img.read_bytes()).decode()}})
        content.append({"type": "text", "text": prompt})

        last: Exception | None = None
        for _ in range(self.retries + 1):
            # Server-side refusal fallback: if a safety classifier declines, the API re-runs
            # the same request on a fallback model inside this call.
            resp = client.beta.messages.parse(
                model=self.model,
                max_tokens=16000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                output_config={"effort": self.effort},
                messages=[{"role": "user", "content": content}],
                output_format=output,
            )
            self._log(resp)
            if resp.stop_reason == "refusal":
                last = RuntimeError(f"model declined: {getattr(resp, 'stop_details', None)}")
                continue
            try:
                if resp.parsed_output is None:
                    raise ValueError(f"no parsed output (stop_reason={resp.stop_reason})")
                return resp.parsed_output
            except (ValidationError, ValueError) as e:
                last = e
        raise RuntimeError(f"model output never validated: {last}")

    def _log(self, resp) -> None:
        u = getattr(resp, "usage", None)
        with (self._dir / "llm-log.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "model": getattr(resp, "model", self.model),
                "stop_reason": getattr(resp, "stop_reason", None),
                "input_tokens": getattr(u, "input_tokens", None),
                "output_tokens": getattr(u, "output_tokens", None),
            }) + "\n")


def _raise(msg: str):
    raise RuntimeError(msg)
