# Nano Banana MCP server (remote / Streamable HTTP)

A small standalone MCP server that wraps the Google Gemini image
generation/editing API (`generate_image`, `edit_image`, `list_models`) and
serves it over **Streamable HTTP**, so it can be added as a **custom
connector** directly in claude.ai chat -- no local `npx`/stdio process
required.

This is a separate, independently deployable service from the
`banana-claude` Claude Code plugin in the rest of this repo. It does not
replace `skills/banana/scripts/generate.py` / `edit.py` (those stay as the
stdlib-only local fallback for the plugin); it's for using the same
Gemini image tools from a browser/chat context where there's no local
filesystem or process to run.

## 1. Get a Google AI API key

Free key: https://aistudio.google.com/apikey

## 2. Deploy to Render

1. In the Render dashboard: **New +** -> **Web Service**.
2. Connect the `banana-claude` GitHub repo.
3. Set **Root Directory** to `mcp-server`.
4. Runtime: **Python 3**.
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `python server.py`
   (Alternatively pick the **Docker** runtime -- the included `Dockerfile`
   works as-is with the same Root Directory.)
5. Under **Environment**, add:
   - `GOOGLE_AI_API_KEY` = your key from step 1
   - `MCP_ACCESS_TOKEN` = a random secret, e.g. generate one with:
     ```
     python3 -c "import secrets; print(secrets.token_urlsafe(24))"
     ```
   Render sets `PORT` itself -- don't override it.
6. Deploy. Render gives you a URL like `https://banana-claude-mcp.onrender.com`.

Render's free tier spins the service down when idle, so the first request
after inactivity can take ~30-60s to wake up (Gemini calls also add their
own latency on top of that).

## 3. Your MCP connection URL

With `MCP_ACCESS_TOKEN` set, the actual endpoint the client connects to is:

```
https://<your-render-app>.onrender.com/<MCP_ACCESS_TOKEN>/mcp
```

That full URL (token included) is the "MCP connection URL" -- treat it like
a secret; anyone with it can call the tools using your Gemini quota. If you
leave `MCP_ACCESS_TOKEN` unset, the server listens on `/mcp` with no
protection, which is only reasonable for quick local testing.

`GET /health` and `GET /` are unauthenticated on purpose (for Render's
health checks) and don't expose the token or any tool functionality.

## 4. Add it as a custom connector in claude.ai chat

1. claude.ai -> **Settings** -> **Connectors** -> **Add custom connector**.
2. Name it (e.g. "Nano Banana").
3. Paste the URL from step 3.
4. Save, then enable it in a chat and ask Claude to generate or edit an
   image -- it will call `generate_image` / `edit_image` and the image
   comes back inline in the conversation (there's no shared filesystem, so
   `edit_image` takes the source image as base64 rather than a file path).

The same URL also works as a remote server entry in a Claude Code
`.mcp.json`:

```json
{
  "mcpServers": {
    "nanobanana-remote": {
      "type": "http",
      "url": "https://<your-render-app>.onrender.com/<MCP_ACCESS_TOKEN>/mcp"
    }
  }
}
```

## Local testing

```bash
cd mcp-server
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export GOOGLE_AI_API_KEY=your-key
export MCP_ACCESS_TOKEN=devtoken
python server.py
# server listens on http://0.0.0.0:8000/devtoken/mcp
```

## Tools exposed

| Tool | Description |
|---|---|
| `generate_image` | Generate an image from a text prompt. Params: `prompt`, `aspect_ratio`, `image_size`, `model`. |
| `edit_image` | Edit an existing image. Params: `image_base64`, `prompt`, `mime_type`, `model`. |
| `list_models` | Lists the currently supported Gemini image models. |

Model roster mirrors `../skills/banana/references/gemini-models.md` --
update `ALLOWED_MODELS` / `DEAD_MODELS` in `server.py` together with that
file when Google changes model availability.
