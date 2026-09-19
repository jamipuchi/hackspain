#!/usr/bin/env python
"""
conveyor_button.py — one-page local control panel for the conveyor motor on the magnet_arm Uno.

    cd ~/robotics/magnet_sorter
    ../.venv/bin/python conveyor_button.py            # opens http://127.0.0.1:8765 in your browser

Holds the serial port open (so the Uno is not reset between clicks), sends `C 0` on start-up so
the belt is stopped, and sends `C 0` again when you press Ctrl-C. Buttons: RUN / STOP, speed slider.
"""
import argparse, glob, json, sys, threading, time, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import serial

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Conveyor</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 body{font-family:-apple-system,system-ui,sans-serif;background:#111;color:#eee;margin:0;min-height:100vh;
      display:flex;flex-direction:column;align-items:center;justify-content:center;gap:28px}
 button{font-size:2.4rem;font-weight:700;padding:34px 60px;border:0;border-radius:20px;cursor:pointer;min-width:280px}
 #run{background:#1db954;color:#032}  #stop{background:#e53935;color:#fff}
 button:active{transform:scale(.97)} input{width:280px}
 #st{font-size:1.1rem;color:#aaa;min-height:1.4em} .row{display:flex;gap:20px;flex-wrap:wrap;justify-content:center}
</style></head><body>
<div class="row"><button id="run">RUN</button><button id="stop">STOP</button></div>
<label>speed <span id="sv">60</span>% <br><input id="sp" type="range" min="10" max="100" value="60"></label>
<div id="st">connecting…</div>
<script>
const st=document.getElementById('st'),sp=document.getElementById('sp'),sv=document.getElementById('sv');
sp.oninput=()=>sv.textContent=sp.value;
async function send(speed){
  st.textContent='sending…';
  const r=await fetch('/belt?speed='+speed,{method:'POST'}); const j=await r.json();
  st.textContent=j.ok?('belt '+(speed==0?'STOPPED':'running at '+speed+'%')+'  (uno: '+j.reply.trim()+')'):('error: '+j.error);
}
document.getElementById('run').onclick=()=>send(+sp.value);
document.getElementById('stop').onclick=()=>send(0);
document.addEventListener('keydown',e=>{if(e.code==='Space'||e.key==='Escape')send(0);});
fetch('/status').then(r=>r.json()).then(j=>st.textContent=j.ok?'connected on '+j.port+' — belt stopped':'error: '+j.error);
</script></body></html>"""

class Uno:
    def __init__(self, port):
        self.lock = threading.Lock()
        self.port = port
        self.ser = serial.Serial(port, 115200, timeout=0.5)
        time.sleep(2.5)                      # opening the port resets the Uno; wait for boot
        self.ser.reset_input_buffer()
        print("uno:", self.cmd("C 0").strip() or "(no reply)")

    def cmd(self, line):
        with self.lock:
            self.ser.reset_input_buffer()
            self.ser.write((line + "\n").encode())
            return self.ser.readline().decode(errors="replace")

def find_port():
    ports = [p for p in glob.glob("/dev/cu.usbmodem*") + glob.glob("/dev/cu.usbserial*")]
    if not ports:
        sys.exit("no Arduino serial port found (looked for /dev/cu.usbmodem* and /dev/cu.usbserial*)")
    return ports[0]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", help="serial port (default: first /dev/cu.usbmodem*)")
    ap.add_argument("--http", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    uno = Uno(a.port or find_port())

    class H(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def _json(self, obj, code=200):
            b = json.dumps(obj).encode(); self.send_response(code)
            self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", len(b))
            self.end_headers(); self.wfile.write(b)
        def do_GET(self):
            if self.path == "/status":
                return self._json({"ok": True, "port": uno.port})
            b = PAGE.encode(); self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", len(b))
            self.end_headers(); self.wfile.write(b)
        def do_POST(self):
            if self.path.startswith("/belt"):
                try:
                    speed = int(self.path.split("speed=")[1]); speed = max(-100, min(100, speed))
                    reply = uno.cmd(f"C {speed}")
                    print(f"C {speed} -> {reply.strip()}")
                    return self._json({"ok": True, "reply": reply})
                except Exception as e:
                    return self._json({"ok": False, "error": str(e)}, 500)
            self._json({"ok": False, "error": "unknown"}, 404)

    srv = ThreadingHTTPServer(("127.0.0.1", a.http), H)
    url = f"http://127.0.0.1:{a.http}"
    print(f"conveyor panel: {url}   (Ctrl-C stops the belt and quits)")
    if not a.no_browser: webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        print("\nstopping belt…", uno.cmd("C 0").strip()); uno.ser.close()

if __name__ == "__main__":
    main()
