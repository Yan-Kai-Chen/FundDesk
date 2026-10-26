import Foundation

@main struct CoreTests {
    static func main() throws {
        var count = 0
        func check(_ value: Bool) { precondition(value); count += 1 }
        let base = URL(string: "http://127.0.0.1:8731")!
        check(DesktopPolicy.sameOrigin(URL(string:"http://127.0.0.1:8731/api/v1/state")!, base))
        for value in ["http://127.0.0.1:8732/", "https://127.0.0.1:8731/", "http://evil.example:8731/", "file:///etc/passwd", "http://" + "user:pass" + "@127.0.0.1:8731/"] {
            check(!DesktopPolicy.sameOrigin(URL(string:value)!,base))
        }
        check(DesktopPolicy.externalLink(URL(string:"https://www.gffunds.com.cn/")!,userActivated:true))
        check(!DesktopPolicy.externalLink(URL(string:"https://www.gffunds.com.cn/")!,userActivated:false))
        check(!DesktopPolicy.externalLink(URL(string:"file:///etc/passwd")!,userActivated:true))
        check(!DesktopPolicy.externalLink(URL(string:"javascript:alert(1)")!,userActivated:true))
        let root = URL(fileURLWithPath: CommandLine.arguments[1],isDirectory:true)
        let directory = root.appendingPathComponent("config/app")
        try FileManager.default.createDirectory(at: directory,withIntermediateDirectories:true)
        let identity = "aabbccddeeff00112233445566778899"
        try JSONSerialization.data(withJSONObject:["workspace_id":identity]).write(to:directory.appendingPathComponent("desktop.json"))
        check(DesktopPolicy.workspaceMatches(root,identity:identity))
        check(!DesktopPolicy.workspaceMatches(root,identity:"wrong"))
        let json: [String:Any] = ["pid":123,"url":base.absoluteString,"instance_id":identity,
            "root":root.path,"workspace_id":identity,"owner":"desktop","version":"0.2.0"]
        let state = try JSONDecoder().decode(BackendState.self,from:JSONSerialization.data(withJSONObject:json))
        check(state.matches(root:root,identity:identity))
        check(!state.matches(root:root.appendingPathComponent("other"),identity:identity))
        check(!state.matches(root:root,identity:"wrong"))
        let output: [String:Any] = ["checks":count,"all_passed":true]
        print(String(data:try JSONSerialization.data(withJSONObject:output,options:[.sortedKeys]),encoding:.utf8)!)
    }
}
