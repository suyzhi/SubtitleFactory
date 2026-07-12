"""Frozen desktop backend entry point."""

import multiprocessing

import uvicorn


if __name__ == "__main__":
    multiprocessing.freeze_support()
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, log_level="info")
