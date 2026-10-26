import AppKit
import WebKit
import Darwin

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate,
    WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate {
    private var window: NSWindow!
    private var webView: WKWebView!
    private var loading: NSView!
    private var status: NSTextField!
    private var spinner: NSProgressIndicator!
    private var retryButton: NSButton!
    private var chooseButton: NSButton!
    private var process: Process?
    private var processLog: FileHandle?
    private var root: URL!
    private var baseURL: URL?
    private var quitting = false
    private var starting = false
    private var startupTask: Task<Void, Never>?
    private let bundle = Bundle.main
    private var identity: String { bundle.object(forInfoDictionaryKey: "ShixuWorkspaceID") as? String ?? "" }
    private var version: String { bundle.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "" }

    func applicationDidFinishLaunching(_ notification: Notification) {
        if let existing = NSRunningApplication.runningApplications(withBundleIdentifier: bundle.bundleIdentifier ?? "")
            .first(where: { $0.processIdentifier != ProcessInfo.processInfo.processIdentifier }) {
            existing.activate(options: [.activateAllWindows])
            NSApp.terminate(nil)
            return
        }
        root = DesktopPolicy.canonical(URL(fileURLWithPath:
            UserDefaults.standard.string(forKey: "WorkspacePath")
                ?? bundle.object(forInfoDictionaryKey: "ShixuWorkspacePath") as? String ?? ""))
        NSApp.appearance = NSAppearance(named: .aqua)
        makeMenu()
        makeWindow()
        startupTask = Task { @MainActor in await startBackend() }
    }

    private func makeWindow() {
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1320, height: 860),
            styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "FundDesk · 基金工作台"
        window.minSize = NSSize(width: 1120, height: 760)
        window.isReleasedWhenClosed = false
        window.delegate = self
        window.center()
        window.setFrameAutosaveName("ShixuMainWindow")
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .default()
        config.preferences.javaScriptCanOpenWindowsAutomatically = false
        webView = WKWebView(frame: window.contentView!.bounds, configuration: config)
        webView.autoresizingMask = [.width, .height]
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.allowsBackForwardNavigationGestures = false
        window.contentView!.addSubview(webView)
        loading = NSView(frame: window.contentView!.bounds)
        loading.wantsLayer = true
        loading.layer?.backgroundColor = NSColor.white.cgColor
        loading.autoresizingMask = [.width, .height]
        status = NSTextField(wrappingLabelWithString: "正在打开本机记录…")
        status.font = .systemFont(ofSize: 16)
        status.alignment = .center
        spinner = NSProgressIndicator()
        spinner.style = .spinning
        spinner.startAnimation(nil)
        retryButton = NSButton(title: "重新连接", target: self, action: #selector(reconnect))
        chooseButton = NSButton(title: "选择项目文件夹…", target: self, action: #selector(chooseWorkspace))
        retryButton.isHidden = true
        chooseButton.isHidden = true
        let stack = NSStackView(views: [spinner, status, retryButton, chooseButton])
        stack.orientation = .vertical
        stack.spacing = 18
        stack.translatesAutoresizingMaskIntoConstraints = false
        loading.addSubview(stack)
        NSLayoutConstraint.activate([stack.centerXAnchor.constraint(equalTo: loading.centerXAnchor),
            stack.centerYAnchor.constraint(equalTo: loading.centerYAnchor),
            status.widthAnchor.constraint(equalToConstant: 580)])
        window.contentView!.addSubview(loading)
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    private func menuItem(_ title: String, _ action: Selector, _ key: String = "", modifiers: NSEvent.ModifierFlags = .command) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: key)
        item.target = self
        item.keyEquivalentModifierMask = modifiers
        return item
    }

    private func makeMenu() {
        let bar = NSMenu()
        func add(_ title: String, _ items: [NSMenuItem]) {
            let parent = NSMenuItem(title: title, action: nil, keyEquivalent: "")
            let menu = NSMenu(title: title)
            items.forEach { menu.addItem($0) }
            parent.submenu = menu
            bar.addItem(parent)
        }
        add("FundDesk", [menuItem("关于FundDesk", #selector(about)), .separator(),
            menuItem("隐藏FundDesk", #selector(hideApp), "h"), .separator(),
            menuItem("退出FundDesk", #selector(quit), "q")])
        add("文件", [menuItem("打开输入文件夹", #selector(openInput), "i", modifiers: [.command, .shift]),
            menuItem("打开产出文件夹", #selector(openOutput), "o", modifiers: [.command, .shift]),
            menuItem("打开备份文件夹", #selector(openBackups)),
            menuItem("打开数据文件夹", #selector(openData)), .separator(),
            menuItem("关闭窗口", #selector(closeWindow), "w")])
        let edit = NSMenu(title: "编辑")
        for (name, selector, key) in [("撤销", "undo:", "z"), ("剪切", "cut:", "x"),
            ("复制", "copy:", "c"), ("粘贴", "paste:", "v"), ("全选", "selectAll:", "a")] {
            edit.addItem(NSMenuItem(title: name, action: Selector(selector), keyEquivalent: key))
        }
        let editParent = NSMenuItem(title: "编辑", action: nil, keyEquivalent: "")
        editParent.submenu = edit; bar.addItem(editParent)
        add("显示", [menuItem("回到工作台", #selector(home), "1"),
            menuItem("刷新页面", #selector(reload), "r"), menuItem("重新连接数据服务", #selector(reconnect)),
            .separator(), menuItem("在浏览器中打开", #selector(openBrowser))])
        add("帮助", [menuItem("日常使用说明", #selector(help)),
            menuItem("显示运行日志", #selector(showLog))])
        NSApp.mainMenu = bar
    }

    @MainActor private func health(_ state: BackendState) async -> Bool {
        guard state.matches(root: root, identity: identity) else { return false }
        do {
            var request = URLRequest(url: state.url.appendingPathComponent("api/v1/health"))
            request.timeoutInterval = 1
            request.cachePolicy = .reloadIgnoringLocalCacheData
            let (data, response) = try await URLSession.shared.data(for: request)
            guard (response as? HTTPURLResponse)?.statusCode == 200,
                  let json = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { return false }
            return json["app"] as? String == "funding-workbench"
                && json["workspace_id"] as? String == identity && json["instance_id"] as? String == state.instance_id
        } catch { return false }
    }

    @MainActor private func startBackend() async {
        guard !starting, !quitting else { return }
        starting = true
        defer { starting = false }
        loading.isHidden = false
        retryButton.isHidden = true
        chooseButton.isHidden = true
        status.stringValue = "正在打开本机记录…"
        spinner.startAnimation(nil)
        let selectedRoot = root!
        let expectedIdentity = identity
        let workspaceOK = await Task.detached(priority: .userInitiated) {
            DesktopPolicy.workspaceMatches(selectedRoot, identity: expectedIdentity)
        }.value
        guard workspaceOK else {
            fail("没有找到当前项目的数据位置。请选回原来的 Funding 项目文件夹，程序不会创建空账本替代它。")
            return
        }
        if (try? root.resourceValues(forKeys: [.isUbiquitousItemKey]).isUbiquitousItem) == true {
            fail("当前项目位于 iCloud 同步目录。请先将完整项目放到本机目录，再选择该文件夹。")
            return
        }
        if let existing = BackendState.read(root: root), await health(existing) {
            guard existing.version == version else {
                fail("已有其他版本的数据服务正在运行。请先退出旧版FundDesk，或用项目停止入口关闭旧开发服务，再重新连接。")
                return
            }
            showWorkbench(existing.url)
            return
        }
        guard let resources = bundle.resourceURL else { fail("应用资源缺失，请重新安装完整的FundDesk.app。"); return }
        let executable = resources.appendingPathComponent("backend/FundingBackend")
        let runtime = root.appendingPathComponent("data/runtime")
        do {
            try FileManager.default.createDirectory(at: runtime, withIntermediateDirectories: true)
            let logURL = runtime.appendingPathComponent("desktop.log")
            if !FileManager.default.fileExists(atPath: logURL.path) {
                FileManager.default.createFile(atPath: logURL.path, contents: nil, attributes: [.posixPermissions: 0o600])
            }
            processLog = try FileHandle(forWritingTo: logURL)
            try processLog?.seekToEnd()
            let instance = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
            let child = Process()
            child.executableURL = executable
            child.arguments = ["--root", root.path, "--resources", resources.appendingPathComponent("payload").path,
                "--instance", instance, "--parent-pid", String(ProcessInfo.processInfo.processIdentifier)]
            child.currentDirectoryURL = root
            var environment = ProcessInfo.processInfo.environment
            environment.removeValue(forKey: "PYTHONHOME")
            environment.removeValue(forKey: "PYTHONPATH")
            environment["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
            child.environment = environment
            child.standardInput = FileHandle.nullDevice
            child.standardOutput = processLog
            child.standardError = processLog
            child.terminationHandler = { [weak self] _ in
                DispatchQueue.main.async {
                    guard let self = self, !self.quitting, !self.starting, self.baseURL != nil else { return }
                    self.baseURL = nil
                    self.fail("本机数据服务已停止，已保存记录仍保留。请重新连接；若仍失败，可在帮助菜单显示日志。")
                }
            }
            process = child
            try child.run()
            for _ in 0..<150 {
                if quitting || Task.isCancelled { return }
                if let state = BackendState.read(root: root), state.instance_id == instance, await health(state) {
                    showWorkbench(state.url)
                    return
                }
                if !child.isRunning { break }
                try await Task.sleep(nanoseconds: 100_000_000)
            }
            stopOwnedBackend()
            fail("数据服务未能启动。请检查项目是否可写，并在帮助菜单显示运行日志。")
        } catch {
            stopOwnedBackend()
            fail("应用启动失败：\(error.localizedDescription)。请保留原项目数据，查看日志后重试。")
        }
    }

    private func showWorkbench(_ url: URL) {
        baseURL = url
        spinner.stopAnimation(nil)
        loading.isHidden = true
        var components = URLComponents(url: url, resolvingAgainstBaseURL: false)!
        components.queryItems = [URLQueryItem(name: "mode", value: "real")]
        webView.load(URLRequest(url: components.url!))
    }

    private func fail(_ message: String) {
        loading.isHidden = false
        spinner.stopAnimation(nil)
        status.stringValue = message
        retryButton.isHidden = false
        chooseButton.isHidden = false
    }

    private func stopOwnedBackend() {
        guard let child = process else { return }
        if child.isRunning {
            child.terminate()
            let until = Date().addingTimeInterval(5)
            while child.isRunning && Date() < until { Thread.sleep(forTimeInterval: 0.05) }
            if child.isRunning { Darwin.kill(child.processIdentifier, SIGKILL); child.waitUntilExit() }
        }
        process = nil
        try? processLog?.close()
        processLog = nil
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationWillTerminate(_ notification: Notification) {
        quitting = true
        startupTask?.cancel()
        stopOwnedBackend()
    }

    @objc private func reconnect() {
        guard !starting else { return }
        baseURL = nil
        stopOwnedBackend()
        startupTask = Task { @MainActor in await startBackend() }
    }
    @objc private func chooseWorkspace() {
        let picker = NSOpenPanel()
        picker.title = "选择原来的 Funding 项目文件夹"
        picker.canChooseDirectories = true; picker.canChooseFiles = false; picker.allowsMultipleSelection = false
        guard picker.runModal() == .OK, let selected = picker.url else { return }
        guard DesktopPolicy.workspaceMatches(selected, identity: identity) else {
            fail("该文件夹不是当前项目。请选择包含原有 data、input、output 和 config 的完整 Funding 文件夹。")
            return
        }
        root = DesktopPolicy.canonical(selected)
        UserDefaults.standard.set(root.path, forKey: "WorkspacePath")
        reconnect()
    }
    @objc private func about() {
        let alert = NSAlert()
        alert.messageText = "FundDesk \(version)"
        alert.informativeText = "本地基金工作台\nApple Silicon · 本机运行\n\n数据位置：\(root.path)\n应用更新与账本资料分开保存。"
        alert.runModal()
    }
    @objc private func help() {
        let alert = NSAlert()
        alert.messageText = "日常使用FundDesk"
        alert.informativeText = "双击FundDesk.app 即可打开，无需终端或 Codex。\n\n文件菜单可打开 input、output、备份和数据文件夹。\n在设置页创建备份；交易、确认和到账按实际资料登记。\n关闭窗口或按 ⌘Q 退出本应用自建的数据服务。\n\n请保留完整 Funding 项目，尤其是 data、input 和 output。更换应用程序不会覆盖这些资料。"
        alert.runModal()
    }
    @objc private func hideApp() { NSApp.hide(nil) }
    @objc private func quit() { NSApp.terminate(nil) }
    @objc private func closeWindow() { window.performClose(nil) }
    @objc private func home() { if let baseURL = baseURL { showWorkbench(baseURL) } }
    @objc private func reload() { webView.reload() }
    @objc private func openBrowser() { if let url = webView.url, let base = baseURL, DesktopPolicy.sameOrigin(url, base) { NSWorkspace.shared.open(url) } }
    @objc private func openInput() { openFolder("input/inbox") }
    @objc private func openOutput() { openFolder("output") }
    @objc private func openBackups() { openFolder("data/backups/real") }
    @objc private func openData() { openFolder("data") }
    private func openFolder(_ path: String) { NSWorkspace.shared.open(root.appendingPathComponent(path)) }
    @objc private func showLog() {
        NSWorkspace.shared.activateFileViewerSelecting([root.appendingPathComponent("data/runtime/desktop.log")])
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url, let base = baseURL else { decisionHandler(.cancel); return }
        if DesktopPolicy.sameOrigin(url, base) {
            decisionHandler(action.shouldPerformDownload ? .download : .allow)
        } else {
            if DesktopPolicy.externalLink(url, userActivated: action.navigationType == .linkActivated) {
                NSWorkspace.shared.open(url)
            }
            decisionHandler(.cancel)
        }
    }
    func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        guard let url = response.response.url, let base = baseURL, DesktopPolicy.sameOrigin(url, base) else {
            decisionHandler(.cancel); return
        }
        let attachment = (response.response as? HTTPURLResponse)?.value(forHTTPHeaderField: "Content-Disposition")?.lowercased().contains("attachment") ?? false
        decisionHandler(attachment || !response.canShowMIMEType ? .download : .allow)
    }
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = action.request.url, let base = baseURL, DesktopPolicy.sameOrigin(url, base) { webView.load(action.request) }
        return nil
    }
    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) { download.delegate = self }
    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) { download.delegate = self }
    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse,
                  suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
        let save = NSSavePanel()
        save.nameFieldStringValue = (suggestedFilename as NSString).lastPathComponent
        save.directoryURL = root.appendingPathComponent("output/exports")
        save.canCreateDirectories = true
        save.beginSheetModal(for: window) { result in completionHandler(result == .OK ? save.url : nil) }
    }
    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
        if (error as NSError).code == NSURLErrorCancelled { return }
        let alert = NSAlert(); alert.messageText = "导出未完成"; alert.informativeText = error.localizedDescription
        alert.beginSheetModal(for: window)
    }
}

@main struct ShixuApplication {
    static func main() {
        let app = NSApplication.shared
        let delegate = AppDelegate()
        app.setActivationPolicy(.regular)
        app.delegate = delegate
        withExtendedLifetime(delegate) { app.run() }
    }
}
