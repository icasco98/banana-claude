#!/usr/bin/env python3
"""Nano Banana MCP -- remote Streamable HTTP MCP server.

Wraps the Google Gemini image generation/editing API as MCP tools and serves
them over Streamable HTTP, so the server can be added as a custom connector
in Claude.ai chat (or any MCP client that speaks Streamable HTTP) via a
plain URL, instead of running locally as a stdio process.

Configuration (environment variables):
    GOOGLE_AI_API_KEY  Required. Key from https://aistudio.google.com/apikey
    MCP_ACCESS_TOKEN   Optional. If set, the MCP endpoint is served at
                        /<token>/mcp instead of /mcp, so the connection URL
                        itself acts as a shared secret.
    PORT               Optional. Defaults to 8000 (Render sets this itself).
"""

import base64
import json
import os
import time
import urllib.error
import urllib.request

from mcp.server.fastmcp import FastMCP, Image

API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

DEFAULT_MODEL = "gemini-3.1-flash-image-preview"
ALLOWED_MODELS = {
    "gemini-3.1-flash-image-preview",  # Nano Banana 2 (active default)
    "gemini-2.5-flash-image",  # Nano Banana original (budget/free tier)
}
DEAD_MODELS = {"gemini-3-pro-image-preview"}  # shut down by Google 2026-03-09

VALID_RATIOS = {
    "1:1", "16:9", "9:16", "4:3", "3:4", "2:3", "3:2",
    "4:5", "5:4", "1:4", "4:1", "1:8", "8:1", "21:9",
}
VALID_SIZES = {"512", "1K", "2K", "4K"}
VALID_MIME_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}

GOOGLE_AI_API_KEY = os.environ.get("GOOGLE_AI_API_KEY", "").strip()
if not GOOGLE_AI_API_KEY:
    raise RuntimeError(
        "GOOGLE_AI_API_KEY is not set. Get a free key at "
        "https://aistudio.google.com/apikey and set it as an environment "
        "variable before starting this server."
    )

ACCESS_TOKEN = os.environ.get("MCP_ACCESS_TOKEN", "").strip()
STREAMABLE_HTTP_PATH = f"/{ACCESS_TOKEN}/mcp" if ACCESS_TOKEN else "/mcp"
PORT = int(os.environ.get("PORT", "8000"))

mcp = FastMCP(
    "nanobanana",
    instructions=(
        "Generate and edit images with Google Gemini's Nano Banana image "
        "models. Call generate_image to create a new image from a text "
        "prompt, and edit_image to modify an image the user has shared in "
        "the conversation."
    ),
    host="0.0.0.0",
    port=PORT,
    stateless_http=True,
    streamable_http_path=STREAMABLE_HTTP_PATH,
)


@mcp.custom_route("/", methods=["GET"])
async def index(request):
    from starlette.responses import PlainTextResponse

    return PlainTextResponse("nanobanana-mcp is running.")


@mcp.custom_route("/health", methods=["GET"])
async def health(request):
    from starlette.responses import JSONResponse

    return JSONResponse({"status": "ok"})


def _validate_model(model: str) -> str:
    if model in DEAD_MODELS:
        raise ValueError(
            f"Model '{model}' was shut down by Google and no longer works. "
            f"Use one of: {sorted(ALLOWED_MODELS)}"
        )
    if model not in ALLOWED_MODELS:
        raise ValueError(f"Unknown model '{model}'. Use one of: {sorted(ALLOWED_MODELS)}")
    return model


def _call_gemini(model: str, body: dict) -> dict:
    url = f"{API_BASE}/{model}:generateContent?key={GOOGLE_AI_API_KEY}"
    data = json.dumps(body).encode("utf-8")

    max_retries = 3
    for attempt in range(max_retries):
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8") if e.fp else ""
            if e.code == 429 and attempt < max_retries - 1:
                time.sleep(2 ** (attempt + 1))
                continue
            if e.code == 400 and "FAILED_PRECONDITION" in error_body:
                raise RuntimeError(
                    "Billing not enabled for this Google AI API key. Enable "
                    "billing at https://aistudio.google.com/apikey"
                ) from e
            raise RuntimeError(f"Gemini API error {e.code}: {error_body}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"Could not reach Gemini API: {e.reason}") from e

    raise RuntimeError("Gemini API rate-limited; max retries exceeded.")


def _extract_image(result: dict) -> tuple[bytes, str]:
    candidates = result.get("candidates", [])
    if not candidates:
        reason = result.get("promptFeedback", {}).get("blockReason", "UNKNOWN")
        raise RuntimeError(f"No candidates returned. Reason: {reason}")

    parts = candidates[0].get("content", {}).get("parts", [])
    image_b64 = None
    text = ""
    for part in parts:
        if "inlineData" in part:
            image_b64 = part["inlineData"]["data"]
        elif "text" in part:
            text = part["text"]

    if not image_b64:
        reason = candidates[0].get("finishReason", "UNKNOWN")
        raise RuntimeError(f"No image in response. finishReason: {reason}")

    return base64.b64decode(image_b64), text


@mcp.tool(structured_output=False)
def generate_image(
    prompt: str,
    aspect_ratio: str = "1:1",
    image_size: str = "1K",
    model: str = DEFAULT_MODEL,
):
    """Generate a new image from a text prompt using Google Gemini (Nano Banana).

    aspect_ratio: one of 1:1, 16:9, 9:16, 4:3, 3:4, 2:3, 3:2, 4:5, 5:4, 1:4,
        4:1, 1:8, 8:1, 21:9.
    image_size: 512, 1K, 2K, or 4K (must be uppercase).
    """
    model = _validate_model(model)
    if aspect_ratio not in VALID_RATIOS:
        raise ValueError(f"Invalid aspect_ratio '{aspect_ratio}'. Valid: {sorted(VALID_RATIOS)}")
    if image_size not in VALID_SIZES:
        raise ValueError(f"Invalid image_size '{image_size}'. Valid: {sorted(VALID_SIZES)}")

    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseModalities": ["TEXT", "IMAGE"],
            "imageConfig": {"aspectRatio": aspect_ratio, "imageSize": image_size},
        },
    }
    result = _call_gemini(model, body)
    image_bytes, text = _extract_image(result)

    content = [Image(data=image_bytes, format="png")]
    if text:
        content.insert(0, text)
    return content


@mcp.tool(structured_output=False)
def edit_image(
    image_base64: str,
    prompt: str,
    mime_type: str = "image/png",
    model: str = DEFAULT_MODEL,
):
    """Edit an existing image using a text instruction.

    image_base64 must be the raw base64-encoded bytes of the source image
    (no "data:image/...;base64," prefix).
    """
    model = _validate_model(model)
    if mime_type not in VALID_MIME_TYPES:
        raise ValueError(f"Unsupported mime_type '{mime_type}'. Use one of: {sorted(VALID_MIME_TYPES)}")

    body = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {"inlineData": {"mimeType": mime_type, "data": image_base64}},
                ]
            }
        ],
        "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
    }
    result = _call_gemini(model, body)
    image_bytes, text = _extract_image(result)

    content = [Image(data=image_bytes, format="png")]
    if text:
        content.insert(0, text)
    return content


@mcp.tool()
def list_models() -> str:
    """List the Gemini image models currently supported by this server."""
    return json.dumps({"default": DEFAULT_MODEL, "available": sorted(ALLOWED_MODELS)}, indent=2)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
