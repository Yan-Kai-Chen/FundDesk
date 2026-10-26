import AppKit

let directory = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
let art = NSImage(size: NSSize(width: 1024, height: 1024))
art.lockFocus()
let tile = NSBezierPath(roundedRect: NSRect(x: 70, y: 70, width: 884, height: 884), xRadius: 192, yRadius: 192)
NSColor(calibratedWhite: 0.97, alpha: 1).setFill(); tile.fill()
NSColor(calibratedWhite: 0.86, alpha: 1).setStroke(); tile.lineWidth = 4; tile.stroke()
let letter = "F" as NSString
letter.draw(at: NSPoint(x: 225, y: 185), withAttributes: [.font: NSFont.systemFont(ofSize: 600, weight: .medium), .foregroundColor: NSColor.black])
let chart = NSBezierPath()
chart.move(to: NSPoint(x: 500, y: 295)); chart.line(to: NSPoint(x: 606, y: 435))
chart.line(to: NSPoint(x: 700, y: 395)); chart.line(to: NSPoint(x: 805, y: 590))
chart.lineWidth = 24; chart.lineCapStyle = .round; chart.lineJoinStyle = .round
NSColor(calibratedRed: 0.48, green: 0.65, blue: 0.87, alpha: 1).setStroke(); chart.stroke()
NSColor(calibratedRed: 0.77, green: 0.71, blue: 0.53, alpha: 1).setFill()
NSBezierPath(ovalIn: NSRect(x: 775, y: 560, width: 60, height: 60)).fill()
art.unlockFocus()
for (size, scale) in [(16,1),(16,2),(32,1),(32,2),(128,1),(128,2),(256,1),(256,2),(512,1),(512,2)] {
    let pixels = size * scale
    let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: pixels, pixelsHigh: pixels,
        bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
    art.draw(in: NSRect(x: 0, y: 0, width: pixels, height: pixels))
    NSGraphicsContext.restoreGraphicsState()
    let suffix = scale == 2 ? "@2x" : ""
    try bitmap.representation(using: .png, properties: [:])!.write(to: directory.appendingPathComponent("icon_\(size)x\(size)\(suffix).png"))
}
