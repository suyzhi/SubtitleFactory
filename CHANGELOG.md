# Changelog

## 0.3.0 — 2026-07-12

- Dual-concurrency, retryable AI clean and translation batches with persisted partial results.
- Player-left/subtitles-right workspace, subtitle focus mode and inline target-language control.
- Eight independently configured AI provider cards with separate clean/translation assignments.
- In-place local model discovery for CTranslate2, MLX, Parakeet ONNX and Memo Core ML.
- MLX Whisper and selectable CPU/Core ML Parakeet runtimes on Apple Silicon.

## 0.2.0 — 2026-07-12

### Added

- Apple Silicon FFmpeg/FFprobe release runtime with architecture and dependency gates.
- App-owned Whisper and Parakeet model storage, model validation, repair and safe fallback.
- Project trash, restore, permanent deletion and empty-trash APIs.
- Persistent App settings, local path validation and expanded health diagnostics.
- Searchable, extensible source and target language selection.
- Apple-style workspace, compact workflow bar, contextual inspector and full settings center.
- Native macOS theme synchronization, motion tokens and reduced-motion behavior.

### Changed

- Automatic transcription now defaults to Whisper Small instead of depending on Memo.
- Memo Core ML is an optional detected external accelerator.
- YouTube URLs are canonicalized and yt-dlp always receives the resolved FFmpeg location.
- Failed downloads stay attached to the original project and can be retried.
- Runtime files, logs and models live under the App data directory in release builds.

### Fixed

- Highest-quality YouTube video/audio streams now merge in the packaged App.
- Light mode now updates the native macOS title bar and system controls.
- Invalid custom model and CLI paths fall back safely with a user-visible reason.
