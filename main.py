
import os
import uvicorn

if __name__ == "__main__":
    is_render = "PORT" in os.environ

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8001)),
        reload=not is_render,
    )
