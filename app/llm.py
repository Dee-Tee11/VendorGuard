"""The LLM calls: closed tasks with no tools and no credentials in prompts.

ask() returns JSON that must match a Pydantic model; read_image() returns the text in a screenshot.
"""
import base64
import os

import groq
import httpx
from langsmith import traceable
from pydantic import BaseModel, ValidationError

MODEL = os.getenv("LLM_MODEL", "openai/gpt-oss-120b")
VISION_MODEL = os.getenv("VISION_MODEL", "qwen/qwen3.8-27b")  # a Groq preview model: may be withdrawn at short notice
# checked and built at import, so a missing key stops the app at boot. The SDK alone accepts ""
# (an empty GROQ_API_KEY= line in .env), and then every check would quietly run degraded.
if not os.getenv("GROQ_API_KEY", "").strip():
    raise RuntimeError("GROQ_API_KEY is not set")
# max_retries=0: the SDK would otherwise sleep up to 60 s on retry-after inside a request thread.
client = groq.Groq(timeout=httpx.Timeout(20.0, connect=5.0), max_retries=0)


@traceable(run_type="llm", name="groq")  # no-op unless LANGSMITH_TRACING=true
def ask(schema: type[BaseModel], system: str, user: str, attempts: int = 2) -> BaseModel:
    """Raises on any failure, so callers can fail closed. By default retries once on output that doesn't match the schema."""
    if attempts < 1:  # the loop would never run and None would come back as if it were an answer
        raise ValueError("attempts must be at least 1")
    for attempt in range(1, attempts + 1):
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
            if attempt == attempts:
                raise


def image_type(data: bytes) -> str | None:
    """The MIME type from the file's first bytes, never from the client. None unless PNG or JPEG."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    return None


@traceable(run_type="llm", name="groq-vision")  # with tracing on, the image goes to LangSmith too (RECHECK #18)
def read_image(prompt: str, image: bytes) -> str:
    """The screenshot's text. Raises on any failure; one call, no retry."""
    if not (mime := image_type(image)):
        raise ValueError("not a PNG or JPEG")
    r = client.chat.completions.create(
        model=VISION_MODEL,
        # the instruction sits next to the image, not in a system message: as a system message the labels were skipped
        messages=[{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(image).decode()}"}},
        ]}],
        temperature=0,
        seed=7,
        reasoning_effort="none",
        max_completion_tokens=2048,
    )
    choice = r.choices[0]
    if choice.finish_reason != "stop" or not choice.message.content:
        raise RuntimeError(f"unusable completion ({choice.finish_reason})")
    return choice.message.content
