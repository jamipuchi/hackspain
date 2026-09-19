// Lists AVFoundation video devices in the order OpenCV's AVFoundation backend
// indexes them: external / Continuity cameras first, then built-in cameras.
// Build: swiftc -O demos/avf_cameras.swift -o demos/avf_cameras
import AVFoundation
let ext = AVCaptureDevice.DiscoverySession(deviceTypes: [.external, .continuityCamera],
                                           mediaType: .video, position: .unspecified).devices
let builtin = AVCaptureDevice.DiscoverySession(deviceTypes: [.builtInWideAngleCamera],
                                               mediaType: .video, position: .unspecified).devices
for (i, d) in (ext + builtin).enumerated() {
    print("\(i)\t\(d.localizedName)\t\(d.uniqueID)")
}
