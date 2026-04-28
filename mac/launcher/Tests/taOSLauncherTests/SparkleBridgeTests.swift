import XCTest
@testable import taOSLauncher

final class SparkleBridgeTests: XCTestCase {
    func testFeedURLReadFromBundle() {
        let bridge = SparkleBridge(infoDict: [
            "SUFeedURL": "https://taos.app/appcast.xml",
            "SUPublicEDKey": "fakekey=="
        ])
        XCTAssertEqual(bridge.feedURL, URL(string: "https://taos.app/appcast.xml"))
        XCTAssertEqual(bridge.publicKey, "fakekey==")
    }

    func testMissingFeedURLDisablesUpdates() {
        let bridge = SparkleBridge(infoDict: [:])
        XCTAssertNil(bridge.feedURL)
        XCTAssertFalse(bridge.canCheckForUpdates)
    }
}
