import httpx, pathlib
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse, Response
import uvicorn

app = FastAPI()
VERIFIER = "http://localhost:8001"
HERE = pathlib.Path(__file__).parent

@app.get("/", response_class=HTMLResponse)
async def index():
    return (HERE / "index.html").read_text(encoding="utf-8")

@app.get("/stream")
async def sse_proxy(request: Request):
    async def gen():
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("GET", f"{VERIFIER}/stream") as r:
                async for chunk in r.aiter_bytes():
                    if await request.is_disconnected(): break
                    yield chunk
    return StreamingResponse(gen(), media_type="text/event-stream",
        headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

@app.api_route("/api/{path:path}", methods=["GET","POST","PUT","DELETE"])
async def api_proxy(path: str, request: Request):
    url = f"{VERIFIER}/{path}"
    body = await request.body()
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.request(request.method, url, params=dict(request.query_params),
                            content=body, headers={"Content-Type":"application/json"})
    return Response(content=r.content, status_code=r.status_code,
                    media_type=r.headers.get("content-type","application/json"))

if __name__ == "__main__":
    print("\n  ZEROAUDIT Dashboard → http://localhost:3000\n")
    uvicorn.run(app, host="0.0.0.0", port=3000)