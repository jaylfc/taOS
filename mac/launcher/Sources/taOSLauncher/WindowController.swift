import AppKit
import WebKit

public enum WindowMode: String {
    case fullscreen
    case phone
}

public final class WindowController {
    public private(set) var mode: WindowMode
    private let serverPort: Int
    private let defaults: UserDefaults
    private var window: NSWindow?
    private var webView: WKWebView?

    public init(serverPort: Int, defaults: UserDefaults = .standard) {
        self.serverPort = serverPort
        self.defaults = defaults
        let raw = defaults.string(forKey: "lastWindowMode") ?? "phone"
        self.mode = WindowMode(rawValue: raw) ?? .phone
    }

    public static func route(for mode: WindowMode, port: Int) -> URL {
        let path = (mode == .fullscreen) ? "/" : "/mobile"
        return URL(string: "http://127.0.0.1:\(port)\(path)")!
    }

    public func toggleMode() {
        mode = (mode == .phone) ? .fullscreen : .phone
        defaults.set(mode.rawValue, forKey: "lastWindowMode")
        applyMode()
    }

    public func showWindow() {
        if window == nil { buildWindow() }
        applyMode()
        window?.makeKeyAndOrderFront(nil)
    }

    public func hideWindow() {
        window?.orderOut(nil)
    }

    private func buildWindow() {
        let frame = NSRect(x: 100, y: 100, width: 393, height: 852)
        let style: NSWindow.StyleMask = [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView]
        let w = NSWindow(contentRect: frame, styleMask: style, backing: .buffered, defer: false)
        w.title = "taOS"
        w.titlebarAppearsTransparent = true
        w.minSize = NSSize(width: 320, height: 694)
        w.maxSize = NSSize(width: 480, height: 1040)

        let webView = WKWebView(frame: w.contentView!.bounds)
        webView.autoresizingMask = [.width, .height]
        webView.layer?.cornerRadius = 47
        webView.layer?.masksToBounds = true
        w.contentView?.addSubview(webView)

        self.window = w
        self.webView = webView
    }

    private func applyMode() {
        guard let w = window, let webView = webView else { return }
        let url = Self.route(for: mode, port: serverPort)
        webView.load(URLRequest(url: url))

        switch mode {
        case .fullscreen:
            if !(w.styleMask.contains(.fullScreen)) {
                w.toggleFullScreen(nil)
            }
            w.collectionBehavior = [.fullScreenPrimary]
        case .phone:
            if w.styleMask.contains(.fullScreen) {
                w.toggleFullScreen(nil)
            }
            let target = NSRect(x: w.frame.origin.x, y: w.frame.origin.y,
                                width: 393, height: 852)
            w.setFrame(target, display: true, animate: true)
        }
    }
}
