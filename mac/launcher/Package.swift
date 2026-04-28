// swift-tools-version: 5.10
import PackageDescription

// Sparkle 2.6.0 is wired in by the build pipeline (mac/build/build.sh) when
// the build host has network access to GitHub releases. SparkleBridge.swift
// gates its real code path behind `#if canImport(Sparkle)`, so the package
// builds and the launcher functions identically without it.
//
// To enable Sparkle for a local development build, append:
//
//   .binaryTarget(
//       name: "Sparkle",
//       url: "https://github.com/sparkle-project/Sparkle/releases/download/2.6.0/Sparkle-for-Swift-Package-Manager.zip",
//       checksum: "a5088d48a37ba415081335502e009dece75acae9d130705fee6c6988b90d0877"
//   ),
//
// to the targets list and add "Sparkle" to the executableTarget dependencies.

let package = Package(
    name: "taOSLauncher",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(
            name: "taOSLauncher",
            dependencies: [],
            path: "Sources/taOSLauncher",
            resources: [.process("Resources")]
        ),
        .testTarget(
            name: "taOSLauncherTests",
            dependencies: ["taOSLauncher"],
            path: "Tests/taOSLauncherTests"
        ),
    ]
)
