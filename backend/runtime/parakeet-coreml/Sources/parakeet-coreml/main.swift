// parakeet-coreml: transcribe one audio file with Parakeet TDT 0.6B v3 on Core ML.
//
// Command line and output are compatible with the backend's Core ML runner:
//   parakeet-coreml --model <dir> --input <audio> --output-dir <dir>
//                   --output-format json [--output-filename <name>]
// stdout carries one JSON event per line ({"status":"progress","progress":N},
// then {"status":"success","result":"<path>"}); the result file is FluidAudio's
// ASRResult (text, duration, tokenTimings[].token/startTime/endTime).
//
// The model directory is never handed to FluidAudio directly: on a failed load
// FluidAudio deletes its model folder and re-downloads it. We give it a private
// folder of symlinks and an unreachable registry, so a bad load can only remove
// those links and can never touch the user's files or reach the network.

import FluidAudio
import Foundation

let toolVersion = "1.0.0 (FluidAudio 0.9.1)"
let modelFolderName = "parakeet-tdt-0.6b-v3-coreml"
let modelEntries = ["Preprocessor.mlmodelc", "Encoder.mlmodelc", "Decoder.mlmodelc", "JointDecision.mlmodelc"]
let vocabularyNames = ["parakeet_vocab.json", "parakeet_v3_vocab.json"]

let usage = """
    OVERVIEW: Parakeet TDT 0.6B v3 Core ML transcription (FluidAudio)

    USAGE: parakeet-coreml --model <model> --input <input> --output-dir <output-dir> --output-format <output-format> [--output-filename <output-filename>]

    OPTIONS:
      -m, --model <model>     Path to the parakeet-tdt-0.6b-v3-coreml directory
      -i, --input <input>     Path to the input audio file
      --output-dir <output-dir>
                              Output directory for transcription results
      --output-format <output-format>
                              json (txt is also accepted, comma separated)
      --output-filename <output-filename>
                              Output filename without extension (default: input name)
      --version               Show the version.
      -h, --help              Show help information.
    """

struct Failure: Error { let message: String }

func emit(_ event: [String: Any]) {
    guard let data = try? JSONSerialization.data(withJSONObject: event, options: [.sortedKeys]),
        let line = String(data: data, encoding: .utf8)
    else { return }
    print(line)
    fflush(stdout)
}

func parseArguments(_ arguments: [String]) throws -> [String: String] {
    var values: [String: String] = [:]
    var index = 0
    let aliases = ["-m": "--model", "-i": "--input"]
    while index < arguments.count {
        let key = aliases[arguments[index]] ?? arguments[index]
        guard key.hasPrefix("--"), index + 1 < arguments.count else {
            throw Failure(message: "Unexpected argument: \(arguments[index])")
        }
        values[key] = arguments[index + 1]
        index += 2
    }
    return values
}

/// Builds `<temp>/parakeet-tdt-0.6b-v3-coreml/` containing symlinks to the real model entries.
func stageModel(from source: URL) throws -> (root: URL, repo: URL) {
    let files = FileManager.default
    var isDirectory: ObjCBool = false
    for entry in modelEntries {
        guard files.fileExists(atPath: source.appendingPathComponent(entry).path, isDirectory: &isDirectory),
            isDirectory.boolValue
        else { throw Failure(message: "Model entry missing: \(entry)") }
    }
    guard let vocabulary = vocabularyNames.map({ source.appendingPathComponent($0) })
        .first(where: { files.fileExists(atPath: $0.path) })
    else { throw Failure(message: "Model vocabulary missing: parakeet_vocab.json") }

    let root = files.temporaryDirectory.appendingPathComponent("parakeet-coreml-\(UUID().uuidString)")
    let repo = root.appendingPathComponent(modelFolderName)
    try files.createDirectory(at: repo, withIntermediateDirectories: true)
    for entry in modelEntries {
        try files.createSymbolicLink(
            at: repo.appendingPathComponent(entry), withDestinationURL: source.appendingPathComponent(entry))
    }
    for name in vocabularyNames {
        try files.createSymbolicLink(at: repo.appendingPathComponent(name), withDestinationURL: vocabulary)
    }
    return (root, repo)
}

func run() async throws {
    let arguments = Array(CommandLine.arguments.dropFirst())
    if arguments.contains("-h") || arguments.contains("--help") { print(usage); return }
    if arguments.contains("--version") { print(toolVersion); return }

    let options = try parseArguments(arguments)
    guard let modelPath = options["--model"], let inputPath = options["--input"],
        let outputPath = options["--output-dir"]
    else { throw Failure(message: "--model, --input and --output-dir are required\n\n\(usage)") }
    let formats = Set((options["--output-format"] ?? "json").split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) })
    guard formats.isSubset(of: ["json", "txt"]), !formats.isEmpty else {
        throw Failure(message: "Unsupported --output-format: \(options["--output-format"] ?? "")")
    }

    let input = URL(fileURLWithPath: inputPath).standardizedFileURL
    guard FileManager.default.isReadableFile(atPath: input.path) else {
        throw Failure(message: "Input audio not readable")
    }
    let outputDirectory = URL(fileURLWithPath: outputPath).standardizedFileURL
    try FileManager.default.createDirectory(at: outputDirectory, withIntermediateDirectories: true)
    let baseName = options["--output-filename"] ?? input.deletingPathExtension().lastPathComponent

    // Never let FluidAudio's self-healing download reach the network.
    ModelRegistry.baseURL = "http://127.0.0.1:9"
    let staged = try stageModel(from: URL(fileURLWithPath: modelPath).resolvingSymlinksInPath())
    defer { try? FileManager.default.removeItem(at: staged.root) }

    emit(["status": "progress", "progress": 0, "message": "loading model"])
    let models = try await AsrModels.load(from: staged.repo, version: .v3)
    let manager = AsrManager(config: .default)
    try await manager.initialize(models: models)

    let progress = await manager.transcriptionProgressStream
    let progressTask = Task {
        var last = -1
        do {
            for try await value in progress {
                let percent = max(0, min(99, Int(value * 100)))
                if percent != last { last = percent; emit(["status": "progress", "progress": percent]) }
            }
        } catch {}
    }
    let result = try await manager.transcribe(input, source: .system)
    progressTask.cancel()

    var resultPath = outputDirectory.appendingPathComponent("\(baseName).json")
    if formats.contains("json") {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        try encoder.encode(result).write(to: resultPath, options: .atomic)
    }
    if formats.contains("txt") {
        let textPath = outputDirectory.appendingPathComponent("\(baseName).txt")
        try result.text.write(to: textPath, atomically: true, encoding: .utf8)
        if !formats.contains("json") { resultPath = textPath }
    }
    emit(["status": "progress", "progress": 100])
    emit(["status": "success", "result": resultPath.path])
}

do {
    try await run()
} catch let failure as Failure {
    FileHandle.standardError.write(Data("error: \(failure.message)\n".utf8))
    emit(["status": "error", "message": failure.message])
    exit(1)
} catch {
    FileHandle.standardError.write(Data("error: \(error.localizedDescription)\n".utf8))
    emit(["status": "error", "message": error.localizedDescription])
    exit(1)
}
