import Foundation

/// Manages the lifecycle of the embedded uvicorn FastAPI server process.
///
/// Spawns a child Process, polls /api/health to confirm readiness, and
/// terminates with SIGTERM -> SIGKILL fallback on shutdown.
final class ServerProcess {
    let executable: URL
    let arguments: [String]
    let environment: [String: String]
    let logFile: URL

    private var process: Process?
    private var logHandle: FileHandle?

    init(executable: URL, arguments: [String], environment: [String: String], logFile: URL) {
        self.executable = executable
        self.arguments = arguments
        self.environment = environment
        self.logFile = logFile
    }

    var isRunning: Bool {
        process?.isRunning ?? false
    }

    func start() throws {
        guard process == nil else { return }

        let dir = logFile.deletingLastPathComponent()
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        if !FileManager.default.fileExists(atPath: logFile.path) {
            FileManager.default.createFile(atPath: logFile.path, contents: nil)
        }
        let handle = try FileHandle(forWritingTo: logFile)
        try handle.seekToEnd()
        self.logHandle = handle

        let proc = Process()
        proc.executableURL = executable
        proc.arguments = arguments
        proc.environment = environment
        proc.standardOutput = handle
        proc.standardError = handle
        try proc.run()
        self.process = proc
    }

    /// Polls the given health URL until it returns 200 or timeout elapses.
    func waitForReady(timeoutSeconds: Double, healthURL: URL) async -> Bool {
        let deadline = Date().addingTimeInterval(timeoutSeconds)
        let session = URLSession(configuration: .ephemeral)
        var request = URLRequest(url: healthURL)
        request.timeoutInterval = 1.0

        while Date() < deadline {
            if !isRunning { return false }
            do {
                let (_, response) = try await session.data(for: request)
                if let http = response as? HTTPURLResponse, http.statusCode == 200 {
                    return true
                }
            } catch {
                // server not ready yet — keep polling
            }
            try? await Task.sleep(nanoseconds: 250_000_000)
        }
        return false
    }

    /// Sends SIGTERM, waits up to graceful timeout, then SIGKILL if still running.
    func stop(gracefulTimeoutSeconds: Double) async {
        guard let proc = process, proc.isRunning else {
            cleanup()
            return
        }

        proc.terminate()
        let deadline = Date().addingTimeInterval(gracefulTimeoutSeconds)
        while proc.isRunning && Date() < deadline {
            try? await Task.sleep(nanoseconds: 100_000_000)
        }
        if proc.isRunning {
            kill(proc.processIdentifier, SIGKILL)
            proc.waitUntilExit()
        }
        cleanup()
    }

    private func cleanup() {
        try? logHandle?.close()
        logHandle = nil
        process = nil
    }
}
