import os
import logging

import uvicorn


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # The FastAPI lifespan owns an in-process scheduler: never fork extra workers.
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=int(os.environ["PORT"]),
        workers=1,
        reload=False,
    )
