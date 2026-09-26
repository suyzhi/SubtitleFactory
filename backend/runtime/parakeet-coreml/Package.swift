// swift-tools-version: 6.0
// Open-source Core ML runner for Parakeet TDT 0.6B v3, bundled as bin/parakeet-coreml.
// Uses FluidAudio (Apache-2.0) and the FluidInference/parakeet-tdt-0.6b-v3-coreml
// conversion of nvidia/parakeet-tdt-0.6b-v3 (CC-BY-4.0).
import PackageDescription

let package = Package(
    name: "ParakeetCoreML",
    platforms: [.macOS(.v14)],
    dependencies: [
        .package(url: "https://github.com/FluidInference/FluidAudio.git", exact: "0.9.1"),
    ],
    targets: [
        .executableTarget(
            name: "parakeet-coreml",
            dependencies: [.product(name: "FluidAudio", package: "FluidAudio")],
            path: "Sources/parakeet-coreml"
        ),
    ]
)
