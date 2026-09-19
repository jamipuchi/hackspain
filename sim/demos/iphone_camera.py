"""Live camera feed from an iPhone connected over USB (Continuity Camera).

macOS exposes the phone as a normal AVFoundation capture device, so no extra
software is needed on the phone. Usage:

    .venv/bin/python demos/iphone_camera.py            # live window, q to quit, s to save a frame
    .venv/bin/python demos/iphone_camera.py --snapshot out.jpg
    .venv/bin/python demos/iphone_camera.py --list
"""
import argparse
import pathlib
import subprocess
import sys

import cv2


HERE = pathlib.Path(__file__).resolve().parent
HELPER_SRC = HERE / "avf_cameras.swift"
HELPER_BIN = HERE / "avf_cameras"


def list_video_devices():
    """Return [(index, name)] of cameras in the order OpenCV indexes them.

    OpenCV's AVFoundation backend lists external / Continuity cameras first and
    built-in cameras after, which is NOT the order `ffmpeg -list_devices` prints.
    A small Swift helper (demos/avf_cameras.swift) reproduces OpenCV's order; it
    is compiled on first use.
    """
    if not HELPER_BIN.exists() or HELPER_BIN.stat().st_mtime < HELPER_SRC.stat().st_mtime:
        print("compiling", HELPER_SRC.name, "...", file=sys.stderr)
        subprocess.run(["swiftc", "-O", str(HELPER_SRC), "-o", str(HELPER_BIN)], check=True)
    out = subprocess.run([str(HELPER_BIN)], capture_output=True, text=True, check=True).stdout
    devices = []
    for line in out.splitlines():
        idx, name, _uid = line.split("\t")
        devices.append((int(idx), name))
    return devices


def find_iphone(devices):
    for idx, name in devices:
        if "iphone" in name.lower() and "desk view" not in name.lower():
            return idx, name
    return None


def open_capture(index, width, height):
    cap = cv2.VideoCapture(index, cv2.CAP_AVFOUNDATION)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    if not cap.isOpened():
        sys.exit(f"could not open video device {index}")
    return cap


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--list", action="store_true", help="list video devices and exit")
    p.add_argument("--device", type=int, help="device index (default: auto-detect iPhone)")
    p.add_argument("--snapshot", metavar="FILE", help="save one frame to FILE and exit")
    p.add_argument("--probe", action="store_true",
                   help="save a thumbnail from every camera index (probe_N.jpg) and exit")
    p.add_argument("--width", type=int, default=1920)
    p.add_argument("--height", type=int, default=1080)
    args = p.parse_args()

    devices = list_video_devices()
    if args.list:
        for idx, name in devices:
            print(f"[{idx}] {name}")
        return

    if args.probe:
        for idx, name in devices:
            cap = cv2.VideoCapture(idx, cv2.CAP_AVFOUNDATION)
            ok, frame = cap.read() if cap.isOpened() else (False, None)
            cap.release()
            if ok:
                cv2.imwrite(f"probe_{idx}.jpg", cv2.resize(frame, (480, 270)))
            print(f"[{idx}] {name}: {'probe_%d.jpg' % idx if ok else 'no frame'}")
        return

    if args.device is not None:
        index, name = args.device, dict(devices).get(args.device, "?")
    else:
        found = find_iphone(devices)
        if not found:
            sys.exit("no iPhone camera found. Is it unlocked and plugged in? Devices:\n"
                     + "\n".join(f"  [{i}] {n}" for i, n in devices))
        index, name = found

    cap = open_capture(index, args.width, args.height)
    print(f"opened [{index}] {name}: "
          f"{int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))}x{int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))}")

    if args.snapshot:
        ok, frame = cap.read()
        if not ok:
            sys.exit("failed to read a frame")
        cv2.imwrite(args.snapshot, frame)
        print(f"saved {args.snapshot} {frame.shape[1]}x{frame.shape[0]}")
        cap.release()
        return

    n = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            print("frame grab failed"); break
        cv2.imshow(name, frame)
        key = cv2.waitKey(1) & 0xFF
        if key == ord("q") or key == 27:
            break
        if key == ord("s"):
            fn = f"iphone_frame_{n:03d}.jpg"; n += 1
            cv2.imwrite(fn, frame); print("saved", fn)
    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
