#!/usr/bin/env python
"""
conveyor_button.py — local control panel for the magnet_arm Uno (conveyor, 3 arm servos, magnet).

    cd ~/robotics/magnet_sorter
    ../.venv/bin/python conveyor_button.py            # opens http://127.0.0.1:8765

Holds the serial port open (so the Uno is not reset between clicks), sends `C 0` on start-up so the belt is
stopped, reconnects if the Uno is re-plugged, and stops the belt again on Ctrl-C.

Panel: conveyor forward / reverse / stop with speed, timed runs, angle sliders for base / shoulder / elbow
(pins 9 / 10 / 11), Home, magnet on/off, live status from `?`, and a raw command box.
Keyboard: Space or Esc = stop belt, ← → = reverse / forward at the slider speed, H = home.
"""
import argparse, glob, json, sys, threading, time, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import serial

PAGE = r"""<!doctype html><html><head><meta charset="utf-8"><title>Magnet Arm Panel</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 :root{--bg:#0f1317;--card:#171d24;--line:#273039;--ink:#e8edf2;--mut:#8b98a5;--go:#1db954;--rev:#2f7ff7;--stop:#e53935;--mag:#f2a93b;--mono:ui-monospace,Menlo,monospace}
 *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.4 -apple-system,system-ui,sans-serif;padding:20px 16px 40px}
 .wrap{max-width:820px;margin:0 auto;display:grid;gap:18px}
 h1{font-size:1.25rem;margin:0;font-weight:600;display:flex;align-items:center;gap:12px}
 .dot{width:10px;height:10px;border-radius:50%;background:var(--mut)} .dot.ok{background:var(--go)} .dot.bad{background:var(--stop)}
 .card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;display:grid;gap:14px}
 .card h2{margin:0;font-size:.8rem;letter-spacing:.08em;text-transform:uppercase;color:var(--mut);font-weight:600}
 .row{display:flex;gap:12px;flex-wrap:wrap;align-items:center}
 button{font:inherit;font-weight:700;color:#fff;border:0;border-radius:12px;padding:16px 22px;cursor:pointer;background:#2a333d;min-width:110px}
 button:active{transform:scale(.97)} button.big{font-size:1.5rem;padding:26px 28px;flex:1 1 140px}
 .fwd{background:var(--go);color:#032} .rev{background:var(--rev)} .stop{background:var(--stop)} .mag.on{background:var(--mag);color:#221}
 label{display:grid;gap:6px;font-size:.9rem;color:var(--mut)} label b{color:var(--ink);font-family:var(--mono);font-weight:500}
 input[type=range]{width:100%;accent-color:#7aa7ff} .slider{display:grid;gap:4px}
 .arm{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px}
 .arm .joint{display:grid;gap:8px;padding:12px;border:1px solid var(--line);border-radius:10px}
 .joint .name{display:flex;justify-content:space-between;font-size:.9rem} .joint .name b{font-family:var(--mono);font-weight:500}
 .joint .pin{color:var(--mut);font-size:.8rem}
 .num{width:76px;font:inherit;font-family:var(--mono);background:#0f1317;color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:8px}
 .status{font-family:var(--mono);font-size:.95rem;color:var(--ink);background:#0b0e12;border-radius:8px;padding:10px 12px;display:grid;gap:4px}
 .status .l{color:var(--mut)} .log{font-family:var(--mono);font-size:.8rem;color:var(--mut);max-height:140px;overflow:auto;white-space:pre-wrap}
 .raw{display:flex;gap:8px} .raw input{flex:1;font:inherit;font-family:var(--mono);background:#0f1317;color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:10px}
 .hint{font-size:.85rem;color:var(--mut)}
 .timed{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
</style></head><body><div class="wrap">
<h1><span id="dot" class="dot"></span>Magnet arm panel <span id="port" class="hint"></span></h1>

<div class="card"><h2>Conveyor · pin 6 (continuous servo)</h2>
 <div class="row">
  <button class="big rev" id="rev">◀ REVERSE</button>
  <button class="big stop" id="stop">■ STOP</button>
  <button class="big fwd" id="fwd">FORWARD ▶</button>
 </div>
 <label>speed <b id="spv">60 %</b><input id="sp" type="range" min="5" max="100" value="60"></label>
 <div class="timed"><span class="hint">run for</span>
  <input class="num" id="secs" type="number" min="0.1" step="0.1" value="2"><span class="hint">s</span>
  <button id="tfwd">▶ timed</button><button id="trev">◀ timed</button>
  <span class="hint">belt now: <b id="belt">stopped</b></span>
 </div>
</div>

<div class="card"><h2>Arm · angles 0–180°</h2>
 <div class="arm">
  <div class="joint"><div class="name"><span>base <span class="pin">pin 9</span></span><b id="v0">150°</b></div><input id="j0" type="range" min="0" max="180" value="150"></div>
  <div class="joint"><div class="name"><span>shoulder <span class="pin">pin 10</span></span><b id="v1">125°</b></div><input id="j1" type="range" min="0" max="180" value="125"></div>
  <div class="joint"><div class="name"><span>elbow <span class="pin">pin 11</span></span><b id="v2">75°</b></div><input id="j2" type="range" min="0" max="180" value="75"></div>
 </div>
 <div class="row">
  <button id="home">⌂ HOME (150 125 75)</button>
  <button id="mag" class="mag">MAGNET OFF</button>
  <label style="flex-direction:row;align-items:center;gap:8px"><input id="live" type="checkbox" checked> send while dragging</label>
 </div>
 <p class="hint">Sliders move the arm servos with the firmware's smooth ramp (200°/s). Nothing is plugged into pins 9–11 right now, so these do nothing until you add the arm servos. To test angle control with the one servo you have, move its orange wire to pin 9 and use the base slider.</p>
</div>

<div class="card"><h2>Status</h2>
 <div class="status"><div><span class="l">position</span> <span id="pos">—</span></div><div><span class="l">reply</span> <span id="st">connecting…</span></div></div>
 <div class="raw"><input id="rawin" placeholder="raw command, e.g.  S 90 90 90   or   C -40"><button id="rawgo">send</button></div>
 <div class="log" id="log"></div>
 <p class="hint">Keys: Space / Esc stop · ← → reverse / forward at slider speed · H home</p>
</div>
</div>
<script>
const $=id=>document.getElementById(id); const log=$('log');
let magnetOn=false, belt=0, timer=null;
function addLog(s){log.textContent=(new Date().toLocaleTimeString()+'  '+s+'\n'+log.textContent).slice(0,4000);}
async function send(line){
  try{
    const r=await fetch('/cmd?line='+encodeURIComponent(line),{method:'POST'}); const j=await r.json();
    $('st').textContent=j.ok?j.reply.trim():('error: '+j.error); $('dot').className='dot '+(j.ok?'ok':'bad');
    addLog(line+'  →  '+(j.ok?j.reply.trim():j.error)); return j;
  }catch(e){$('st').textContent='panel unreachable';$('dot').className='dot bad';}
}
function setBelt(sp){belt=sp; if(timer){clearTimeout(timer);timer=null;} send('C '+sp);
  $('belt').textContent=sp==0?'stopped':(sp>0?'forward ':'reverse ')+Math.abs(sp)+'%';}
const speed=()=>+$('sp').value;
$('sp').oninput=()=>{$('spv').textContent=speed()+' %'; if(belt!=0) setBelt(Math.sign(belt)*speed());};
$('fwd').onclick=()=>setBelt(speed()); $('rev').onclick=()=>setBelt(-speed()); $('stop').onclick=()=>setBelt(0);
function timed(dir){setBelt(dir*speed()); timer=setTimeout(()=>setBelt(0), Math.max(100,+$('secs').value*1000));}
$('tfwd').onclick=()=>timed(1); $('trev').onclick=()=>timed(-1);
const J=[0,1,2].map(i=>$('j'+i)); const sendArm=()=>send('S '+J.map(j=>j.value).join(' '));
J.forEach((j,i)=>{ j.oninput=()=>{$('v'+i).textContent=j.value+'°'; if($('live').checked) sendArm();}; j.onchange=sendArm; });
$('home').onclick=()=>{[150,125,75].forEach((v,i)=>{J[i].value=v;$('v'+i).textContent=v+'°';}); send('H');};
function paintMag(){$('mag').textContent='MAGNET '+(magnetOn?'ON':'OFF'); $('mag').classList.toggle('on',magnetOn);}
$('mag').onclick=()=>{magnetOn=!magnetOn; paintMag(); send('M '+(magnetOn?1:0));};
$('rawgo').onclick=()=>{const v=$('rawin').value.trim(); if(v){send(v); $('rawin').value='';}};
$('rawin').onkeydown=e=>{if(e.key==='Enter')$('rawgo').onclick();};
document.addEventListener('keydown',e=>{ if(e.target.tagName==='INPUT'&&e.target.type!=='range')return;
  if(e.code==='Space'||e.key==='Escape'){e.preventDefault();setBelt(0);}
  else if(e.key==='ArrowRight'){e.preventDefault();setBelt(speed());} else if(e.key==='ArrowLeft'){e.preventDefault();setBelt(-speed());}
  else if(e.key==='h'||e.key==='H'){$('home').onclick();} });
async function poll(){
  try{ const r=await fetch('/status'); const j=await r.json(); $('port').textContent=j.port||'';
    if(j.ok&&j.pos){ $('pos').textContent=`base ${j.pos[0]}°  shoulder ${j.pos[1]}°  elbow ${j.pos[2]}°  · magnet ${j.magnet?'ON':'off'} · ${j.moving?'moving':'idle'}`;
      $('dot').className='dot ok'; if(magnetOn!==!!j.magnet){magnetOn=!!j.magnet;paintMag();} }
    else if(!j.ok){$('dot').className='dot bad';$('st').textContent='error: '+j.error;}
  }catch(e){$('dot').className='dot bad';}
}
poll(); setInterval(poll,1000);
</script></body></html>"""

ALLOWED = set("SMCH?")   # firmware commands the raw box may send

class Uno:
    def __init__(self, port):
        self.lock = threading.Lock()
        self.port = port
        self.ser = None
        self._open()
        print("uno:", self.cmd("C 0").strip() or "(no reply)")

    def _open(self):
        if self.ser:
            try: self.ser.close()
            except Exception: pass
        self.port = self.port if glob.glob(self.port) else find_port()
        self.ser = serial.Serial(self.port, 115200, timeout=0.5)
        time.sleep(2.5)                      # opening the port resets the Uno; wait for boot
        self.ser.reset_input_buffer()
        print("connected:", self.port)

    def cmd(self, line):
        with self.lock:
            for attempt in (1, 2):
                try:
                    self.ser.reset_input_buffer()
                    self.ser.write((line + "\n").encode())
                    return self.ser.readline().decode(errors="replace")
                except (serial.SerialException, OSError) as e:
                    # "Device not configured" = the Uno was re-plugged; reopen the port and retry once
                    print("serial error:", e, "-> reconnecting")
                    if attempt == 2: raise
                    self._open()

    def status(self):
        """Parse `P b s e M m B moving` into a dict."""
        r = self.cmd("?").split()
        try:
            return {"pos": [int(r[1]), int(r[2]), int(r[3])], "magnet": r[5] == "1", "moving": r[7] == "1"}
        except (IndexError, ValueError):
            return {"pos": None, "raw": " ".join(r)}

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
            if self.path.startswith("/status"):
                try: return self._json({"ok": True, "port": uno.port, **uno.status()})
                except Exception as e: return self._json({"ok": False, "port": uno.port, "error": str(e)}, 500)
            b = PAGE.encode(); self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", len(b))
            self.end_headers(); self.wfile.write(b)
        def do_POST(self):
            u = urlparse(self.path); q = parse_qs(u.query)
            try:
                if u.path == "/belt":                       # kept for the old page / curl scripts
                    speed = max(-100, min(100, int(q["speed"][0]))); line = f"C {speed}"
                elif u.path == "/cmd":
                    line = q.get("line", [""])[0].strip()
                    if not line or line[0] not in ALLOWED or "\n" in line:
                        return self._json({"ok": False, "error": "command must start with S, M, C, H or ?"}, 400)
                else:
                    return self._json({"ok": False, "error": "unknown endpoint"}, 404)
                reply = uno.cmd(line); print(f"{line} -> {reply.strip()}")
                return self._json({"ok": True, "reply": reply})
            except Exception as e:
                return self._json({"ok": False, "error": str(e)}, 500)

    srv = ThreadingHTTPServer(("127.0.0.1", a.http), H)
    url = f"http://127.0.0.1:{a.http}"
    print(f"magnet arm panel: {url}   (Ctrl-C stops the belt and quits)")
    if not a.no_browser: webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        print("\nstopping belt…", uno.cmd("C 0").strip()); uno.ser.close()

if __name__ == "__main__":
    main()
