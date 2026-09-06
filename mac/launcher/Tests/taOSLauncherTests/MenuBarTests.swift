import XCTest
import AppKit
@testable import taOSLauncher

final class MenuBarTests: XCTestCase {
    func testMenuItemsInOrder() {
        let actions = MenuBar.Actions(
            openDesktop: {},
            openMobile: {},
            togglePause: {},
            openPreferences: {},
            checkForUpdates: {},
            quit: {}
        )
        let menu = MenuBar.buildMenu(actions: actions, isPaused: false)
        let titles = menu.items.map { $0.title }
        XCTAssertEqual(titles, [
            "Open taOS",
            "Open Mobile View",
            "Pause Agents",
            "",
            "Preferences…",
            "Check for Updates…",
            "",
            "Quit taOS",
        ])
    }

    func testPauseTogglesTitle() {
        let actions = MenuBar.Actions(
            openDesktop: {}, openMobile: {}, togglePause: {},
            openPreferences: {}, checkForUpdates: {}, quit: {}
        )
        let paused = MenuBar.buildMenu(actions: actions, isPaused: true)
        XCTAssertEqual(paused.items[2].title, "Resume Agents")
    }

    func testQuitHasCommandQ() {
        let actions = MenuBar.Actions(
            openDesktop: {}, openMobile: {}, togglePause: {},
            openPreferences: {}, checkForUpdates: {}, quit: {}
        )
        let menu = MenuBar.buildMenu(actions: actions, isPaused: false)
        XCTAssertEqual(menu.items.last?.keyEquivalent, "q")
    }
}
