import asyncio
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse


REPO_ROOT = Path(__file__).resolve().parents[1]
NODE_DIR = REPO_ROOT / "services" / "hospital-node"

NODES = {
    "BCH": 9001,
    "MGH": 9002,
    "BWH": 9003,
}

processes: list[subprocess.Popen] = []


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        for node, port in NODES.items():
            env = os.environ.copy()
            env["HOSPITAL_NODE"] = node
            # Repo root exposes `shared`; node modules come from --app-dir.
            env["PYTHONPATH"] = os.pathsep.join(
                p for p in (str(REPO_ROOT), env.get("PYTHONPATH", "")) if p
            )
            processes.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "uvicorn",
                        "main:app",
                        "--app-dir",
                        str(NODE_DIR),
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(port),
                    ],
                    env=env,
                )
            )

        await asyncio.sleep(0.5)
        failed_nodes = [
            node
            for (node, _), process in zip(NODES.items(), processes)
            if process.poll() is not None
        ]
        if failed_nodes:
            raise RuntimeError(f"Failed to start nodes: {', '.join(failed_nodes)}")

        yield
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            process.wait()


app = FastAPI(title="Hospital Node Explorer", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
def explorer():
    cards = "".join(
        f"""
        <article>
          <h2>{node}</h2>
          <p>Port {port}</p>
          <nav>
            <a href="http://localhost:{port}/docs">API docs</a>
            <a href="http://localhost:{port}/health">Health</a>
            <a href="http://localhost:{port}/api/studies">Studies</a>
          </nav>
        </article>
        """
        for node, port in NODES.items()
    )
    return f"""
    <!doctype html>
    <html lang="en">
      <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Hospital Node Explorer</title>
        <style>
          body {{ font: 16px system-ui; max-width: 900px; margin: 4rem auto; padding: 0 1rem; }}
          main {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 1rem; }}
          article {{ border: 1px solid #ddd; border-radius: 12px; padding: 1.25rem; }}
          nav {{ display: grid; gap: .5rem; }}
          a {{ color: #075985; }}
        </style>
      </head>
      <body>
        <h1>Hospital Node Explorer</h1>
        <p>All three local hospital APIs are running.</p>
        <main>{cards}</main>
      </body>
    </html>
    """


@app.get("/nodes")
def list_nodes():
    return {
        node: {
            "base_url": f"http://localhost:{port}",
            "docs": f"http://localhost:{port}/docs",
        }
        for node, port in NODES.items()
    }


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=9000)
