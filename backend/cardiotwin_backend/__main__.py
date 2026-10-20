# python -m cardiotwin_backend [--host 0.0.0.0] [--port 8000]
import argparse

import uvicorn

from .app import create_app

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()
    uvicorn.run(create_app(), host=a.host, port=a.port, ws_max_size=4 * 1024 * 1024)
