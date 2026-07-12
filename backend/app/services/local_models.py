"""Scan and register external model directories without copying user files."""

import hashlib, json, os, time, uuid
from pathlib import Path
from ..models.database import get_db

SKIP = {".git", "_internal", "__pycache__", "node_modules", ".cache"}


def _fingerprint(path: Path, names: list[str]) -> str:
    digest = hashlib.sha256()
    for name in sorted(names):
        item = path / name
        stat = item.stat(); digest.update(f"{name}:{stat.st_size}:{stat.st_mtime_ns}".encode())
    return digest.hexdigest()


def detect_model(path: Path, root: Path | None = None) -> dict | None:
    if not path.is_dir(): return None
    entries = {item.name for item in path.iterdir() if not item.name.startswith(".")}
    fmt = family = version = ""; runtimes: list[str] = []; required: list[str] = []
    if {"model.bin", "config.json"}.issubset(entries) and ({"tokenizer.json", "vocabulary.json"} & entries):
        tokenizer = "tokenizer.json" if "tokenizer.json" in entries else "vocabulary.json"
        fmt, family, runtimes, required = "ctranslate2", "whisper", ["cpu"], ["model.bin", "config.json", tokenizer]
    elif {"weights.npz", "config.json"}.issubset(entries):
        fmt, family, runtimes, required = "mlx", "whisper", ["mlx"], ["weights.npz", "config.json"]
    elif {"encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt", "silero_vad.onnx"}.issubset(entries):
        fmt, family, runtimes = "sherpa-onnx", "parakeet", ["cpu", "coreml"]
        required = ["encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt", "silero_vad.onnx"]
    elif all((path / item).is_dir() for item in ("Encoder.mlmodelc", "Decoder.mlmodelc", "JointDecision.mlmodelc", "Preprocessor.mlmodelc")):
        fmt, family, runtimes = "memo-coreml", "parakeet", ["external_coreml"]
        required = ["Encoder.mlmodelc", "Decoder.mlmodelc", "JointDecision.mlmodelc", "Preprocessor.mlmodelc"]
        version = "v3" if "v3" in path.name.lower() else "v2" if "v2" in path.name.lower() else ""
    elif "configuration.json" in entries or "model.pt" in entries:
        return {"path": str(path.resolve()), "display_name": path.name, "family": "other", "format": "funasr", "version": "", "supported": False, "reason": "当前运行引擎不支持"}
    else: return None
    cli = None
    if fmt == "memo-coreml" and root:
        candidate = root.parent / "plugins" / "parakeet-cli" / "parakeet"
        if candidate.is_file() and os.access(candidate, os.X_OK): cli = str(candidate.resolve())
    return {"path": str(path.resolve()), "display_name": path.name, "family": family, "format": fmt,
            "version": version, "runtimes": runtimes, "supported": True, "cli_path": cli,
            "fingerprint": _fingerprint(path, required), "reason": "" if fmt != "memo-coreml" or cli else "需要选择 Parakeet CLI"}


def scan_models(root_path: str) -> list[dict]:
    root = Path(root_path).expanduser().resolve()
    if not root.is_dir(): raise ValueError("模型根目录不存在")
    found = []; queue = [(root, 0)]
    while queue:
        path, depth = queue.pop(0)
        detected = detect_model(path, root)
        if detected: found.append(detected)
        if detected and detected.get("supported"): continue
        if depth >= 5: continue
        try:
            for child in path.iterdir():
                if child.is_dir() and not child.name.startswith(".") and child.name not in SKIP: queue.append((child, depth + 1))
            for child in path.iterdir():
                if child.is_file() and child.name.startswith("ggml-") and child.suffix == ".bin":
                    found.append({"path": str(child.resolve()), "display_name": child.name, "family": "whisper", "format": "ggml", "version": "", "supported": False, "reason": "需要 whisper.cpp 运行时"})
        except OSError: continue
    return found


def register_model(path: str, cli_path: str | None = None, display_name: str | None = None) -> dict:
    resolved = Path(path).expanduser().resolve(); detected = detect_model(resolved, resolved.parent)
    if not detected or not detected.get("supported"): raise ValueError("未识别或不支持的模型目录")
    if detected["format"] == "memo-coreml":
        cli = Path(cli_path or detected.get("cli_path") or "").expanduser()
        if not cli.is_file() or not os.access(cli, os.X_OK): raise ValueError("Memo Core ML 模型需要可执行的 Parakeet CLI")
        detected["cli_path"] = str(cli.resolve())
    model_id = f"local:{uuid.uuid4()}"; now = time.strftime("%Y-%m-%d %H:%M:%S")
    db = get_db(); db.execute(
        """INSERT INTO imported_models (id,display_name,family,version,format,path,cli_path,runtimes_json,fingerprint,status,created_at,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,'ready',?,?)""",
        (model_id, display_name or detected["display_name"], detected["family"], detected["version"], detected["format"],
         detected["path"], detected.get("cli_path"), json.dumps(detected["runtimes"]), detected["fingerprint"], now, now),
    ); db.commit(); db.close(); return get_imported(model_id)


def get_imported(model_id: str | None = None) -> dict | list[dict]:
    db = get_db()
    rows = db.execute("SELECT * FROM imported_models" + (" WHERE id=?" if model_id else " ORDER BY created_at"), ((model_id,) if model_id else ())).fetchall(); db.close()
    items=[]
    for row in rows:
        item=dict(row); item["runtimes"]=json.loads(item.pop("runtimes_json")); item["path"] = item["path"]
        items.append(item)
    if model_id:
        if not items: raise ValueError("导入模型不存在")
        return items[0]
    return items


def validate_imported(model_id: str) -> dict:
    item = get_imported(model_id); detected = detect_model(Path(item["path"]), Path(item["path"]).parent)
    ok = bool(detected and detected.get("supported") and detected.get("fingerprint") == item["fingerprint"])
    error = "" if ok else "外部目录已移动、缺失或内容发生变化，需要重新定位"
    db=get_db(); db.execute("UPDATE imported_models SET status=?,last_error=?,updated_at=? WHERE id=?", ("ready" if ok else "needs_relink", error, time.strftime("%Y-%m-%d %H:%M:%S"), model_id)); db.commit(); db.close()
    return {**get_imported(model_id), "ready": ok}


def remove_imported(model_id: str) -> None:
    db=get_db(); db.execute("DELETE FROM imported_models WHERE id=?", (model_id,)); db.commit(); db.close()
