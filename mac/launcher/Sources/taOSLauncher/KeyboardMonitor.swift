import AppKit

/// Local NSEvent monitor that swallows kiosk-busting shortcuts when the
/// app is in fullscreen mode. Spaces gestures (Cmd+Tab, Ctrl+arrow) are
/// always allowed through.
public final class KeyboardMonitor {
    public var fullscreen: Bool = false
    private var monitor: Any?

    private let blockedCmdKeys: Set<UInt16> = [
        12,  // Q
        13,  // W
        4,   // H
        46,  // M
        7,   // X
    ]

    private let alwaysPassCmd: Set<UInt16> = [
        48,  // Tab
        50,  // `
    ]

    public init() {}

    public func shouldIntercept(keyCode: UInt16, modifiers: NSEvent.ModifierFlags) -> Bool {
        guard fullscreen else { return false }
        if modifiers.contains(.control) { return false }
        if modifiers.contains(.command), alwaysPassCmd.contains(keyCode) { return false }
        if modifiers.contains(.command), blockedCmdKeys.contains(keyCode) { return true }
        return false
    }

    public func install() {
        monitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
            guard let self else { return event }
            if self.shouldIntercept(keyCode: event.keyCode,
                                    modifiers: event.modifierFlags.intersection(.deviceIndependentFlagsMask)) {
                return nil
            }
            return event
        }
    }

    public func uninstall() {
        if let m = monitor { NSEvent.removeMonitor(m) }
        monitor = nil
    }
}
