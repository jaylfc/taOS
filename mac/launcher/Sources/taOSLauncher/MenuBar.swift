import AppKit

/// Builds and owns the NSStatusItem menu.
public enum MenuBar {
    public struct Actions {
        public var openDesktop: () -> Void
        public var openMobile: () -> Void
        public var togglePause: () -> Void
        public var openPreferences: () -> Void
        public var checkForUpdates: () -> Void
        public var quit: () -> Void

        public init(openDesktop: @escaping () -> Void,
                    openMobile: @escaping () -> Void,
                    togglePause: @escaping () -> Void,
                    openPreferences: @escaping () -> Void,
                    checkForUpdates: @escaping () -> Void,
                    quit: @escaping () -> Void) {
            self.openDesktop = openDesktop
            self.openMobile = openMobile
            self.togglePause = togglePause
            self.openPreferences = openPreferences
            self.checkForUpdates = checkForUpdates
            self.quit = quit
        }
    }

    public static func buildMenu(actions: Actions, isPaused: Bool) -> NSMenu {
        let menu = NSMenu()
        menu.addItem(makeItem(title: "Open taOS", action: actions.openDesktop))
        menu.addItem(makeItem(title: "Open Mobile View", action: actions.openMobile))
        menu.addItem(makeItem(title: isPaused ? "Resume Agents" : "Pause Agents",
                              action: actions.togglePause))
        menu.addItem(NSMenuItem.separator())
        menu.addItem(makeItem(title: "Preferences…", action: actions.openPreferences))
        menu.addItem(makeItem(title: "Check for Updates…", action: actions.checkForUpdates))
        menu.addItem(NSMenuItem.separator())
        menu.addItem(makeItem(title: "Quit taOS", action: actions.quit, keyEquivalent: "q"))
        return menu
    }

    private static func makeItem(title: String,
                                 action: @escaping () -> Void,
                                 keyEquivalent: String = "") -> NSMenuItem {
        let item = NSMenuItem(title: title, action: nil, keyEquivalent: keyEquivalent)
        let target = ActionWrapper(action: action)
        item.target = target
        item.action = #selector(ActionWrapper.invoke)
        item.representedObject = target
        return item
    }

    private final class ActionWrapper: NSObject {
        let action: () -> Void
        init(action: @escaping () -> Void) { self.action = action }
        @objc func invoke() { action() }
    }
}
