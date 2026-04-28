import XCTest
import AppKit
@testable import taOSLauncher

final class KeyboardMonitorTests: XCTestCase {
    func testInterceptsCmdQInFullscreen() {
        let monitor = KeyboardMonitor()
        monitor.fullscreen = true
        XCTAssertTrue(monitor.shouldIntercept(keyCode: 12, modifiers: .command))
    }

    func testInterceptsCmdWInFullscreen() {
        let monitor = KeyboardMonitor()
        monitor.fullscreen = true
        XCTAssertTrue(monitor.shouldIntercept(keyCode: 13, modifiers: .command))
    }

    func testDoesNotInterceptInPhoneMode() {
        let monitor = KeyboardMonitor()
        monitor.fullscreen = false
        XCTAssertFalse(monitor.shouldIntercept(keyCode: 12, modifiers: .command))
    }

    func testCmdTabAlwaysPassesThrough() {
        let monitor = KeyboardMonitor()
        monitor.fullscreen = true
        XCTAssertFalse(monitor.shouldIntercept(keyCode: 48, modifiers: .command))
    }

    func testCtrlArrowAlwaysPassesThrough() {
        let monitor = KeyboardMonitor()
        monitor.fullscreen = true
        XCTAssertFalse(monitor.shouldIntercept(keyCode: 124, modifiers: .control))
    }
}
