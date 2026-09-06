import Foundation
#if canImport(Sparkle)
import Sparkle
#endif

/// Thin wrapper around Sparkle. Test-friendly because the controller is
/// owned only after the real bundle keys are present.
public final class SparkleBridge {
    public let feedURL: URL?
    public let publicKey: String?
    #if canImport(Sparkle)
    private var controller: SPUStandardUpdaterController?
    #endif

    public init(infoDict: [String: Any]) {
        if let s = infoDict["SUFeedURL"] as? String {
            self.feedURL = URL(string: s)
        } else {
            self.feedURL = nil
        }
        self.publicKey = infoDict["SUPublicEDKey"] as? String
    }

    public convenience init() {
        self.init(infoDict: Bundle.main.infoDictionary ?? [:])
    }

    public var canCheckForUpdates: Bool {
        guard feedURL != nil else { return false }
        let key = publicKey?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return !key.isEmpty
    }

    public func startAutomaticUpdates() {
        #if canImport(Sparkle)
        guard canCheckForUpdates else { return }
        controller = SPUStandardUpdaterController(
            startingUpdater: true, updaterDelegate: nil, userDriverDelegate: nil
        )
        #endif
    }

    public func checkForUpdates() {
        #if canImport(Sparkle)
        controller?.checkForUpdates(nil)
        #endif
    }
}
