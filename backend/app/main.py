from fastapi import FastAPI

app = FastAPI(title="Manuscript Service")

@app.get("/health")
async def health():
    return {"status": "ok"}
