import pathlib, base64, zlib

BASE = pathlib.Path(r'C:\zeroaudit\dashboard_static')
BASE.mkdir(exist_ok=True)

serve = '''import httpx, pathlib
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
        r = await c.request(request.method, url,
            params=dict(request.query_params),
            content=body,
            headers={"Content-Type":"application/json"})
    return Response(content=r.content, status_code=r.status_code,
        media_type=r.headers.get("content-type","application/json"))

if __name__ == "__main__":
    print("ZEROAUDIT Dashboard -> http://localhost:3000")
    uvicorn.run(app, host="0.0.0.0", port=3000)
'''

(BASE / 'serve.py').write_text(serve, encoding='utf-8')
print('serve.py done')

import base64
b64_1 = "CjxzdHlsZT4KICBAaW1wb3J0IHVybCgnaHR0cHM6Ly9mb250cy5nb29nbGVhcGlzLmNvbS9jc3MyP2ZhbWlseT1KZXRCcmFpbnMrTW9ubzp3Z2h0QDQwMDs1MDA7NzAwJmRpc3BsYXk9c3dhcCcpOwogICp7Ym94LXNpemluZzpib3JkZXItYm94O21hcmdpbjowO3BhZGRpbmc6MH0KICA6cm9vdHsKICAgIC0tb2JzaWRpYW46IzBhMGMwZjsKICAgIC0tc2xhdGU6IzBlMTExNzsKICAgIC0tcGFuZWw6IzExMTUyMDsKICAgIC0tcGFuZWwyOiMxNjFjMmE7CiAgICAtLWJvcmRlcjojMWUyYTNhOwogICAgLS1ib3JkZXIyOiMyNDMwNDA7CiAgICAtLWN5YW46IzAwZDRmZjsKICAgIC0tY3lhbjI6IzAwOTliYjsKICAgIC0tY3lhbi1kaW06IzAwZDRmZjIyOwogICAgLS1hbWJlcjojZjU5ZTBiOwogICAgLS1hbWJlci1kaW06I2Y1OWUwYjIyOwogICAgLS1yZWQ6I2VmNDQ0NDsKICAgIC0tcmVkLWRpbTojZWY0NDQ0MjI7CiAgICAtLWdyZWVuOiMyMmM1NWU7CiAgICAtLWdyZWVuLWRpbTojMjJjNTVlMjI7CiAgICAtLXRleHQ6I2UyZThmMDsKICAgIC0tdGV4dDI6Izk0YTNiODsKICAgIC0tdGV4dDM6IzQ3NTU2OTsKICAgIC0tbW9ubzonSmV0QnJhaW5zIE1vbm8nLG1vbm9zcGFjZTsKICAgIC0tc2FuczonQW50aHJvcGljIFNhbnMnLHNhbnMtc2VyaWY7CiAgfQo="

import base64
html_b64 = "CjxzdHlsZT4KICBAaW1wb3J0IHVybCgnaHR0cHM6Ly9mb250cy5nb29nbGVhcGlzLmNvbS9jc3MyP2ZhbWlseT1KZXRCcmFpbnMrTW9ubzp3Z2h0QDQwMDs1MDA7NzAwJmRpc3BsYXk9c3dhcCcpOwogICp7Ym94LXNpemluZzpib3JkZXItYm94O21hcmdpbjowO3BhZGRpbmc6MH0KICA6cm9vdHsKICAgIC0tb2JzaWRpYW46IzBhMGMwZjsKICAgIC0tc2xhdGU6IzBlMTExNzsKICAgIC0tcGFuZWw6IzExMTUyMDsKICAgIC0tcGFuZWwyOiMxNjFjMmE7CiAgICAtLWJvcmRlcjojMWUyYTNhOwogICAgLS1ib3JkZXIyOiMyNDMwNDA7CiAgICAtLWN5YW46IzAwZDRmZjsKICAgIC0tY3lhbjI6IzAwOTliYjsKICAgIC0tY3lhbi1kaW06IzAwZDRmZjIyOwogICAgLS1hbWJlcjojZjU5ZTBiOwogICAgLS1hbWJlci1kaW06I2Y1OWUwYjIyOwogICAgLS1yZWQ6I2VmNDQ0NDsKICAgIC0tcmVkLWRpbTojZWY0NDQ0MjI7CiAgICAtLWdyZWVuOiMyMmM1NWU7CiAgICAtLWdyZWVuLWRpbTojMjJjNTVlMjI7CiAgICAtLXRleHQ6I2UyZThmMDsKICAgIC0tdGV4dDI6Izk0YTNiODsKICAgIC0tdGV4dDM6IzQ3NTU2OTsKICAgIC0tbW9ubzonSmV0QnJhaW5zIE1vbm8nLG1vbm9zcGFjZTsKICAgIC0tc2FuczonQW50aHJvcGljIFNhbnMnLHNhbnMtc2VyaWY7CiAgfQogIGJvZHl7YmFja2dyb3VuZDp2YXIoLS1vYnNpZGlhbik7Y29sb3I6dmFyKC0tdGV4dCk7Zm9udC1mYW1pbHk6dmFyKC0tc2Fucyk7Zm9udC1zaXplOjEzcHg7bWluLWhlaWdodDoxMDB2aH0="
html = base64.b64decode(html_b64).decode('utf-8')
(BASE / 'index.html').write_text(html, encoding='utf-8')
print('index.html done')

b64_2 = "CiAgYm9keXtiYWNrZ3JvdW5kOnZhcigtLW9ic2lkaWFuKTtjb2xvcjp2YXIoLS10ZXh0KTtmb250LWZhbWlseTp2YXIoLS1zYW5zKTtmb250LXNpemU6MTNweDttaW4taGVpZ2h0OjEwMHZofQogIAogIC8qIFRPUCBCQVIgKi8KICAudG9wYmFye2Rpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7anVzdGlmeS1jb250ZW50OnNwYWNlLWJldHdlZW47cGFkZGluZzoxMHB4IDIwcHg7YmFja2dyb3VuZDp2YXIoLS1zbGF0ZSk7Ym9yZGVyLWJvdHRvbToxcHggc29saWQgdmFyKC0tYm9yZGVyKTtwb3NpdGlvbjpzdGlja3k7dG9wOjA7ei1pbmRleDoxMDB9CiAgLnRvcGJhci1sZWZ0e2Rpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjE2cHh9CiAgLmxvZ297Zm9udC1mYW1pbHk6dmFyKC0tbW9ubyk7Zm9udC13ZWlnaHQ6NzAwO2ZvbnQtc2l6ZToxNnB4O2NvbG9yOnZhcigtLWN5YW4pO2xldHRlci1zcGFjaW5nOjNweH0KICAudGFnbGluZXtmb250LWZhbWlseTp2YXIoLS1tb25vKTtmb250LXNpemU6MTBweDtjb2xvcjp2YXIoLS10ZXh0Myk7bGV0dGVyLXNwYWNpbmc6MnB4fQogIC5zdGF0dXMtcm93e2Rpc3BsYXk6ZmxleDtnYXA6MTJweDthbGlnbi1pdGVtczpjZW50ZXJ9CiAgLnN0YXR1cy1kb3R7d2lkdGg6NnB4O2hlaWdodDo2cHg7Ym9yZGVyLXJhZGl1czo1MCU7YmFja2dyb3VuZDp2YXIoLS1ncmVlbik7Ym94LXNoYWRvdzowIDAgNnB4IHZhcigtLWdyZWVuKX0KICAuc3RhdHVzLWRvdC53YXJue2JhY2tncm91bmQ6dmFyKC0tYW1iZXIpO2JveC1zaGFkb3c6MCAwIDZweCB2YXIoLS1hbWJlcil9CiAgLnN0YXR1cy1kb3QuZGVhZHtiYWNrZ3JvdW5kOnZhcigtLXJlZCk7Ym94LXNoYWRvdzowIDAgNnB4IHZhcigtLXJlZCl9CiAgLnN0YXR1cy1sYWJlbHtmb250LWZhbWlseTp2YXIoLS1tb25vKTtmb250LXNpemU6MTBweDtjb2xvcjp2YXIoLS10ZXh0Mik7bGV0dGVyLXNwYWNpbmc6MXB4fQogIC50aW1lLWRpc3BsYXl7Zm9udC1mYW1pbHk6dmFyKC0tbW9ubyk7Zm9udC1zaXplOjExcHg7Y29sb3I6dmFyKC0tY3lhbik7bGV0dGVyLXNwYWNpbmc6MXB4fQo="
