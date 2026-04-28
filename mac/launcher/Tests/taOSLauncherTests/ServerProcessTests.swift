import XCTest
@testable import taOSLauncher

final class ServerProcessTests: XCTestCase {
    func testSpawnAndStop() async throws {
        let tmp = FileManager.default.temporaryDirectory
            .appendingPathComponent("taos-server-test-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: tmp, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: tmp) }

        let script = tmp.appendingPathComponent("fake-server.sh")
        try """
        #!/bin/bash
        echo "Uvicorn running on http://127.0.0.1:7117"
        sleep 60
        """.write(to: script, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o755], ofItemAtPath: script.path)

        let log = tmp.appendingPathComponent("server.log")
        let server = ServerProcess(
            executable: URL(fileURLWithPath: "/bin/bash"),
            arguments: [script.path],
            environment: ProcessInfo.processInfo.environment,
            logFile: log
        )

        try server.start()
        XCTAssertTrue(server.isRunning)

        try await Task.sleep(nanoseconds: 200_000_000)
        await server.stop(gracefulTimeoutSeconds: 2.0)
        XCTAssertFalse(server.isRunning)
    }
}
