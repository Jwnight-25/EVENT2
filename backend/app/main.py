from fastapi import FastAPI

app = FastAPI(title="EVENT2 · 股票研究", version="0.1.0")


@app.get("/api/v1/health")
def health():
    return {"status": "ok", "version": app.version}
