import os

import uvicorn


if __name__ == "__main__":
    host = os.getenv("LAUNCHER_HOST", "127.0.0.1")
    port = int(os.getenv("LAUNCHER_PORT", "8010"))
    uvicorn.run("launcher_app:app", host=host, port=port)
