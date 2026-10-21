"""The only LLM call: a closed task whose JSON must match a Pydantic model. No tools, no credentials in prompts."""
import os

import groq
import httpx
from langsmith import traceable
from pydantic import BaseModel, ValidationError

MODEL = os.getenv("LLM_MODEL", "openai/gpt-oss-120b")
# checked and built at import, so a missing key stops the app at boot. The SDK alone accepts ""
# (an empty GROQ_API_KEY= line in .env), and then every check would quietly run degraded.
if not os.getenv("GROQ_API_KEY", "").strip():
    raise RuntimeError("GROQ_API_KEY is not set")
# max_retries=0: the SDK would otherwise sleep up to 60 s on retry-after inside a request thread.
client = groq.Groq(timeout=httpx.Timeout(20.0, connect=5.0), max_retries=0)


@traceable(run_type="llm", name="groq")  # no-op unless LANGSMITH_TRACING=true
def ask(schema: type[BaseModel], system: str, user: str) -> BaseModel:
    """Raises on any failure, so callers can fail closed. Retries once on output that doesn't match the schema."""
    for attempt in (1, 2):
        try:
            r = client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": schema.__name__, "strict": True, "schema": schema.model_json_schema()},
                },
                temperature=0,
                seed=7,
                reasoning_effort="low",
                max_completion_tokens=2048,
            )
            choice = r.choices[0]
            if choice.finish_reason != "stop" or getattr(choice.message, "refusal", None) or not choice.message.content:
                raise RuntimeError(f"unusable completion ({choice.finish_reason})")
            return schema.model_validate_json(choice.message.content)
        except (ValidationError, groq.BadRequestError):  # BadRequest covers Groq's json_validate_failed
            if attempt == 2:
                raise
