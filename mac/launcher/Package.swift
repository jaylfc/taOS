// swift-tools-version: 5.10
import PackageDescription

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
