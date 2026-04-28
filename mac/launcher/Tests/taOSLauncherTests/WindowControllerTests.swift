import XCTest
import AppKit
@testable import taOSLauncher

final class WindowControllerTests: XCTestCase {
    func testInitialModeFromDefaults() {
        let suite = "test_\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defaults.set("phone", forKey: "lastWindowMode")

        let wc = WindowController(serverPort: 6969, defaults: defaults)
        XCTAssertEqual(wc.mode, .phone)
    }

    func testFirstLaunchDefaultsToPhone() {
        let suite = "test_\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        let wc = WindowController(serverPort: 6969, defaults: defaults)
        XCTAssertEqual(wc.mode, .phone)
    }

    func testToggleSwitchesModeAndPersists() {
        let suite = "test_\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        let wc = WindowController(serverPort: 6969, defaults: defaults)
        XCTAssertEqual(wc.mode, .phone)
        wc.toggleMode()
        XCTAssertEqual(wc.mode, .fullscreen)
        XCTAssertEqual(defaults.string(forKey: "lastWindowMode"), "fullscreen")
    }

    func testRouteForMode() {
        XCTAssertEqual(WindowController.route(for: .fullscreen, port: 6969).path, "/")
        XCTAssertEqual(WindowController.route(for: .phone, port: 6969).path, "/mobile")
    }
}
