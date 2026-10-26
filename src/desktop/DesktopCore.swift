import Foundation

enum DesktopPolicy {
    static func localOrigin(_ url: URL) -> Bool {
        guard url.scheme == "http", url.host == "127.0.0.1", let port = url.port,
              (1024...65535).contains(port), url.user == nil, url.password == nil else { return false }
        return true
    }

    static func sameOrigin(_ url: URL, _ base: URL) -> Bool {
        localOrigin(url) && localOrigin(base) && url.port == base.port
    }

    static func externalLink(_ url: URL, userActivated: Bool) -> Bool {
        userActivated && ["https", "http"].contains(url.scheme ?? "") && url.host != nil
            && url.user == nil && url.password == nil
    }

    static func canonical(_ url: URL) -> URL {
        url.standardizedFileURL.resolvingSymlinksInPath()
    }

    static func workspaceMatches(_ root: URL, identity: String) -> Bool {
        guard let data = try? Data(contentsOf: root.appendingPathComponent("config/app/desktop.json")),
              let config = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return false }
        return config["workspace_id"] as? String == identity
    }
}

struct BackendState: Decodable {
    let pid: Int32
    let url: URL
    let instance_id: String
    let root: String
    let workspace_id: String
    let owner: String
    let version: String

    func matches(root expected: URL, identity: String) -> Bool {
        pid > 0 && DesktopPolicy.localOrigin(url) && (url.path.isEmpty || url.path == "/")
            && url.query == nil && url.fragment == nil && workspace_id == identity
            && DesktopPolicy.canonical(URL(fileURLWithPath: root)) == DesktopPolicy.canonical(expected)
            && instance_id.range(of: "^[a-f0-9]{32}$", options: .regularExpression) != nil
    }

    static func read(root: URL) -> BackendState? {
        guard let data = try? Data(contentsOf: root.appendingPathComponent("data/runtime/server.json")) else { return nil }
        return try? JSONDecoder().decode(BackendState.self, from: data)
    }
}
