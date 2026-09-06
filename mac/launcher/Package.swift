// swift-tools-version: 6.2
import PackageDescription

let package = Package(
    name: "taOSLauncher",
    platforms: [.macOS(.v26)],
    targets: [
        .executableTarget(
            name: "taOSLauncher",
            dependencies: ["Sparkle"],
            path: "Sources/taOSLauncher",
            resources: [.process("Resources")]
        ),
        .testTarget(
            name: "taOSLauncherTests",
            dependencies: ["taOSLauncher"],
            path: "Tests/taOSLauncherTests"
        ),
        .binaryTarget(
            name: "Sparkle",
            url: "https://github.com/sparkle-project/Sparkle/releases/download/2.6.0/Sparkle-for-Swift-Package-Manager.zip",
            checksum: "a5088d48a37ba415081335502e009dece75acae9d130705fee6c6988b90d0877"
        ),
    ]
)
