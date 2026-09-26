// Bookbind's window.
//
// The server underneath is the same Python as always. This process exists so the
// app is a Mac app: its own window and Dock icon, and real Finder panels instead
// of a browser tab and a path typed out by hand. It starts the server, points a
// web view at it, and answers three requests from the page -- pick a folder, pick
// files, show something in the Finder.
//
// Build/make_window.sh compiles this into the universal binary at
// Bookbind.app/Contents/MacOS/Bookbind.

import AppKit
import UniformTypeIdentifiers
import WebKit

let fm = FileManager.default

enum Where {
    static let resources = Bundle.main.resourcePath ?? ""
    static let macOS = (Bundle.main.executablePath as NSString? ?? "").deletingLastPathComponent

    /// A frozen server, when this copy carries one (the bundled builds).
    static var frozenServer: String? {
        let p = resources + "/bin/BookbindServer"
        return fm.isExecutableFile(atPath: p) ? p : nil
    }

    /// Where app.py is: beside the server in a release, at the checkout root otherwise.
    static var sourceRoot: String? {
        if fm.fileExists(atPath: resources + "/app.py") { return resources }
        // In a checkout the bundle sits inside the repo, three levels down from the
        // executable: MacOS -> Contents -> Bookbind.app -> the repo root.
        let root = URL(fileURLWithPath: macOS)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .path
        return fm.fileExists(atPath: root + "/app.py") ? root : nil
    }

    static var logFile: String {
        let dir = (NSHomeDirectory() as NSString).appendingPathComponent("Library/Logs/Bookbind")
        try? fm.createDirectory(atPath: dir, withIntermediateDirectories: true)
        return dir + "/Bookbind.log"
    }
}

struct Trouble: Error {
    let text: String
    init(_ t: String) { text = t }
}

// ---------------------------------------------------------------- small tools

func httpGet(_ url: String, timeout: TimeInterval = 1.5) -> String? {
    guard let u = URL(string: url) else { return nil }
    var req = URLRequest(url: u)
    req.timeoutInterval = timeout
    req.cachePolicy = .reloadIgnoringLocalCacheData
    var body: String?
    let done = DispatchSemaphore(value: 0)
    URLSession.shared.dataTask(with: req) { data, _, _ in
        if let data { body = String(data: data, encoding: .utf8) }
        done.signal()
    }.resume()
    _ = done.wait(timeout: .now() + timeout + 1)
    return body
}

func answered(_ port: Int) -> Bool {
    httpGet("http://127.0.0.1:\(port)/api/home", timeout: 0.6)?.contains("\"home\"") == true
}

/// Nothing listening on this port? Then it is ours to take.
func free(_ port: Int) -> Bool {
    let fd = socket(AF_INET, SOCK_STREAM, 0)
    guard fd >= 0 else { return false }
    defer { close(fd) }
    var addr = sockaddr_in()
    addr.sin_family = sa_family_t(AF_INET)
    addr.sin_port = in_port_t(UInt16(port).bigEndian)
    addr.sin_addr.s_addr = inet_addr("127.0.0.1")
    let r = withUnsafePointer(to: &addr) { p in
        p.withMemoryRebound(to: sockaddr.self, capacity: 1) {
            connect(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
        }
    }
    return r != 0
}

func runs(_ exe: String, _ args: [String]) -> Bool {
    let p = Process()
    p.executableURL = URL(fileURLWithPath: exe)
    p.arguments = args
    p.standardError = FileHandle.nullDevice
    p.standardOutput = FileHandle.nullDevice
    do { try p.run() } catch { return false }
    p.waitUntilExit()
    return p.terminationStatus == 0
}

/// The first python3 that can import mutagen -- it is what reads the tags -- falling
/// back to any python3 at all, so the error can say what is missing rather than
/// pretending there is no Python here.
func findPython() -> String? {
    var candidates = ["/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"]
    for dir in (ProcessInfo.processInfo.environment["PATH"] ?? "").split(separator: ":") {
        candidates.append(String(dir) + "/python3")
    }
    var any: String?
    for c in candidates where fm.isExecutableFile(atPath: c) {
        if any == nil { any = c }
        if runs(c, ["-c", "import mutagen"]) { return c }
    }
    return any
}

// ------------------------------------------------------------------ the server

final class Server {
    private(set) var process: Process?
    private(set) var owned = false
    private(set) var port = 8765
    private(set) var url = URL(string: "http://127.0.0.1:8765/")!
    private var log: FileHandle?

    func start() throws {
        // An instance that is already running is somebody else's; just show it.
        for p in 8765...8774 where answered(p) {
            port = p
            url = URL(string: "http://127.0.0.1:\(p)/")!
            return
        }
        guard let p = (8765...8774).first(where: free) else {
            throw Trouble("Every port from 8765 to 8774 is taken.\n\nQuit whatever is using them and open Bookbind again.")
        }
        port = p
        url = URL(string: "http://127.0.0.1:\(p)/")!

        let pr = Process()
        var env = ProcessInfo.processInfo.environment
        let bin = Where.resources + "/bin"

        if let frozen = Where.frozenServer {
            env["PATH"] = bin + ":/usr/bin:/bin:/usr/sbin:/sbin"
            pr.executableURL = URL(fileURLWithPath: frozen)
        } else if let root = Where.sourceRoot {
            let py = findPython()
            guard let py else {
                throw Trouble("Bookbind needs Python 3.\n\nInstall it with:\n\n    brew install python3\n    python3 -m pip install mutagen")
            }
            guard runs(py, ["-c", "import mutagen"]) else {
                throw Trouble("Python 3 is here, but the `mutagen` library is missing — it is what reads the tags.\n\nInstall it with:\n\n    python3 -m pip install mutagen")
            }
            env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + (env["PATH"] ?? "/usr/bin:/bin")
            pr.executableURL = URL(fileURLWithPath: py)
            pr.arguments = [root + "/app.py"]
        } else {
            throw Trouble("Bookbind cannot find its own program files — the app looks incomplete.\n\nDownload it again.")
        }

        env["BOOKBIND_PORT"] = String(p)
        env["PYTHONUNBUFFERED"] = "1"
        // So the server can quit when we do, rather than outliving a force-quit and
        // being re-adopted -- stale code answering on a port the next launch trusts.
        env["BOOKBIND_PARENT"] = String(ProcessInfo.processInfo.processIdentifier)
        pr.environment = env

        fm.createFile(atPath: Where.logFile, contents: nil)
        log = try? FileHandle(forWritingTo: URL(fileURLWithPath: Where.logFile))
        log?.seekToEndOfFile()
        if let log {
            pr.standardOutput = log
            pr.standardError = log
        }

        do { try pr.run() } catch {
            throw Trouble("The server would not start: \(error.localizedDescription)")
        }
        process = pr
        owned = true

        for _ in 0..<120 {
            if answered(p) { return }
            if !pr.isRunning { break }
            usleep(250_000)
        }
        if answered(p) { return }
        throw Trouble("The server stopped on startup.\n\nWhat it said is in:\n\n\(Where.logFile)")
    }

    func stop() {
        guard owned, let pr = process, pr.isRunning else { return }
        pr.terminate()
        process = nil
    }
}

// ------------------------------------------------------------------- the window

final class MainWindow: NSObject, WKScriptMessageHandler, WKNavigationDelegate, WKUIDelegate {
    let server = Server()
    var window: NSWindow?
    var web: WKWebView!
    private var ready: (() -> Void)?

    func open() {
        let config = WKWebViewConfiguration()
        config.userContentController.add(self, name: "bookbind")
        // Tells the page it is in the app, so it can offer Finder panels instead of
        // the fallback path box and Talk to the server itself.
        config.userContentController.addUserScript(WKUserScript(
            source: "window.__bookbindNative = true;", injectionTime: .atDocumentStart,
            forMainFrameOnly: true))

        let container = NSView(frame: NSRect(x: 0, y: 0, width: 1180, height: 820))
        web = WKWebView(frame: container.bounds, configuration: config)
        web.autoresizingMask = [.width, .height]
        web.navigationDelegate = self
        web.uiDelegate = self
        container.addSubview(web)

        let w = NSWindow(contentRect: container.frame,
                         styleMask: [.titled, .closable, .miniaturizable, .resizable],
                         backing: .buffered, defer: false)
        w.title = "Bookbind"
        w.minSize = NSSize(width: 880, height: 560)
        w.contentView = container
        w.setFrameAutosaveName("BookbindWindow")
        w.center()
        w.makeKeyAndOrderFront(nil)
        window = w

        web.loadHTMLString("""
        <html><head><meta charset="utf-8"><style>
        body{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;
             font:15px -apple-system,sans-serif;color:#6b6f76;background:#f6f6f7}
        @media(prefers-color-scheme:dark){body{background:#131417;color:#9aa0aa}}
        </style></head><body>Starting Bookbind…</body></html>
        """, baseURL: nil)

        do {
            try server.start()
            web.load(URLRequest(url: server.url))
        } catch let t as Trouble {
            alert(t.text, "Bookbind cannot start")
        } catch {
            alert("\(error)", "Bookbind cannot start")
        }
    }

    func close() {
        server.stop()
    }

    func alert(_ text: String, _ title: String) {
        // The alert is modal and blocks before the run loop is up, so if nobody is
        // there to click it the process just sits there. Say it on stderr too.
        FileHandle.standardError.write(Data("\(title): \(text)\n".utf8))
        let a = NSAlert()
        a.messageText = title
        a.informativeText = text
        a.alertStyle = .warning
        a.addButton(withTitle: "OK")
        a.runModal()
        window?.close()
    }

    // MARK: what the page asks for

    func userContentController(_ c: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let body = message.body as? [String: Any],
              let cmd = body["cmd"] as? String else { return }
        let id = body["id"] as? Int ?? 0
        switch cmd {
        case "pickFolder":  pickFolder(id)
        case "pickFiles":   pickFiles(id)
        case "reveal":      reveal(id, body["path"] as? String)
        case "openURL":     openURL(id, body["url"] as? String)
        default:            reply(id, ["error": "unknown command"])
        }
    }

    private func reply(_ id: Int, _ payload: [String: Any]) {
        guard let data = try? JSONSerialization.data(withJSONObject: payload),
              let json = String(data: data, encoding: .utf8) else { return }
        // The payload is JSON, and a JSON object is a valid JavaScript object literal.
        web.evaluateJavaScript("window.__bookbindReply(\(id), \(json))", completionHandler: nil)
    }

    private func panel() -> NSOpenPanel {
        let p = NSOpenPanel()
        p.treatsFilePackagesAsDirectories = false
        p.canCreateDirectories = false
        p.resolvesAliases = true
        return p
    }

    private func pickFolder(_ id: Int) {
        let p = panel()
        p.canChooseDirectories = true
        p.canChooseFiles = false
        p.allowsMultipleSelection = false
        p.prompt = "Choose"
        p.message = "Pick the folder that holds the audio files."
        p.beginSheetModal(for: window!) { answer in
            if answer == .OK, let u = p.url { self.reply(id, ["path": u.path]) }
            else { self.reply(id, ["cancelled": true]) }
        }
    }

    private func pickFiles(_ id: Int) {
        let p = panel()
        p.canChooseFiles = true
        p.canChooseDirectories = false
        p.allowsMultipleSelection = true
        p.allowsOtherFileTypes = true          // flac and opus are not in every type list
        p.allowedContentTypes = [.audio, .audiovisualContent]
        p.prompt = "Add"
        p.message = "Pick audio files. Hold ⌘ to add more, or ⇧ for a run of them."
        p.beginSheetModal(for: window!) { answer in
            if answer == .OK {
                self.reply(id, ["paths": p.urls.map { $0.path }])
            } else {
                self.reply(id, ["cancelled": true])
            }
        }
    }

    private func reveal(_ id: Int, _ path: String?) {
        guard let path, fm.fileExists(atPath: path) else {
            reply(id, ["error": "that is not there any more"]); return
        }
        var isDir: ObjCBool = false
        fm.fileExists(atPath: path, isDirectory: &isDir)
        let u = URL(fileURLWithPath: path)
        if isDir.boolValue { NSWorkspace.shared.open(u) }
        else { NSWorkspace.shared.activateFileViewerSelecting([u]) }
        reply(id, ["ok": true])
    }

    private func openURL(_ id: Int, _ url: String?) {
        if let url, let u = URL(string: url) { NSWorkspace.shared.open(u) }
        reply(id, ["ok": true])
    }

    // MARK: keeping the window for Bookbind only

    func webView(_ w: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if let u = action.request.url, let host = u.host,
           host != "127.0.0.1" && host != "localhost" {
            NSWorkspace.shared.open(u)          // a link out goes to the browser
            decisionHandler(.cancel)
            return
        }
        decisionHandler(.allow)
    }

    func webView(_ w: WKWebView, createWebViewWith config: WKWebViewConfiguration,
                 for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let u = action.request.url { NSWorkspace.shared.open(u) }
        return nil
    }
}

// ------------------------------------------------------------------- the app

final class BookbindApp: NSObject, NSApplicationDelegate {
    let main = MainWindow()

    func applicationDidFinishLaunching(_ note: Notification) {
        buildMenu()
        main.open()
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationWillTerminate(_ note: Notification) {
        main.close()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ app: NSApplication) -> Bool { true }

    @objc func openLog() {
        NSWorkspace.shared.open(URL(fileURLWithPath: Where.logFile))
    }

    private func buildMenu() {
        let bar = NSMenu()

        let appItem = NSMenuItem()
        bar.addItem(appItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "About Bookbind",
                        action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)),
                        keyEquivalent: "")
        appMenu.addItem(.separator())
        let logItem = NSMenuItem(title: "Show the Log",
                                 action: #selector(openLog), keyEquivalent: "")
        logItem.target = self
        appMenu.addItem(logItem)
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Hide Bookbind",
                        action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quit Bookbind",
                        action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu

        // Without an Edit menu the text fields in the page have no ⌘C/⌘V at all.
        let editItem = NSMenuItem()
        bar.addItem(editItem)
        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        edit.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "Z")
        edit.addItem(.separator())
        edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)),
                     keyEquivalent: "a")
        editItem.submenu = edit

        NSApp.mainMenu = bar
    }
}

@main
struct Bookbind {
    static func main() {
        let app = NSApplication.shared
        let delegate = BookbindApp()
        app.delegate = delegate
        app.setActivationPolicy(.regular)
        app.run()
    }
}
