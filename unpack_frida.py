"""
MEW Packer Dynamic Unpacker using Frida (v2 - corrected API)
"""
import frida
import sys
import os
import struct
import time

TARGET = r"D:\下载\飞飞智能助手4.8991\飞飞智能助手4.8991\飞飞智能助手4.8991.exe"
OUTPUT_DIR = r"C:\Users\Administrator\Documents\Qoder\2026-09-26\fded2f16\dump"

FRIDA_SCRIPT = r"""
'use strict';

var allocations = [];
var allAllocations = [];

function safeHook(funcName, callbacks) {
    try {
        var addr = Module.getGlobalExportByName(funcName);
        if (addr) {
            Interceptor.attach(addr, callbacks);
            send({type: 'log', msg: 'Hooked: ' + funcName});
        }
    } catch(e) {
        send({type: 'log', msg: 'Hook error ' + funcName + ': ' + e});
    }
}

safeHook('VirtualAlloc', {
    onEnter: function(args) {
        this.size = args[1].toInt32();
        this.allocType = args[2].toInt32();
        this.protect = args[3].toInt32();
    },
    onLeave: function(retval) {
        if (!retval.isNull() && this.size > 0) {
            var entry = { address: retval, size: this.size, type: this.allocType, protect: this.protect };
            allAllocations.push(entry);
            if (this.size > 0x10000) {
                allocations.push(entry);
                send({type: 'alloc', address: retval.toString(), size: this.size});
            }
        }
    }
});

safeHook('VirtualAllocEx', {
    onEnter: function(args) {
        this.size = args[2].toInt32();
    },
    onLeave: function(retval) {
        if (!retval.isNull() && this.size > 0x10000) {
            allocations.push({ address: retval, size: this.size });
            send({type: 'alloc', address: retval.toString(), size: this.size, note: 'VirtualAllocEx'});
        }
    }
});

safeHook('LoadLibraryA', {
    onEnter: function(args) {
        try { send({type: 'loadlib', name: args[0].readUtf8String()}); } catch(e) {}
    }
});

safeHook('LoadLibraryW', {
    onEnter: function(args) {
        try { send({type: 'loadlibw', name: args[0].readUtf16String()}); } catch(e) {}
    }
});

safeHook('CreateWindowExA', {
    onEnter: function(args) {
        try {
            var wn = args[2].readUtf8String();
            if (wn) send({type: 'window', title: wn});
        } catch(e) {}
    }
});

safeHook('CreateWindowExW', {
    onEnter: function(args) {
        try {
            var wn = args[2].readUtf16String();
            if (wn) send({type: 'windoww', title: wn});
        } catch(e) {}
    }
});

function checkPE(addr, label) {
    try {
        var hdr = new Uint8Array(addr.readByteArray(64));
        if (hdr[0] !== 0x4d || hdr[1] !== 0x5a) return null;
        var peOff = addr.add(0x3c).readU32();
        if (peOff > 0x800) return null;
        var pe = new Uint8Array(addr.add(peOff).readByteArray(4));
        if (pe[0] !== 0x50 || pe[1] !== 0x45 || pe[2] !== 0 || pe[3] !== 0) return null;
        var soImage = addr.add(peOff + 0x50).readU32();
        var nSec = addr.add(peOff + 6).readU16();
        var ep = addr.add(peOff + 0x28).readU32();
        var ib = addr.add(peOff + 0x34).readU32();
        return { address: addr.toString(), peOffset: peOff, sizeOfImage: soImage, numSections: nSec, entryPoint: ep, imageBase: ib, label: label };
    } catch(e) {
        return null;
    }
}

function scanAndDump() {
    send({type: 'log', msg: '=== Starting final scan ==='});
    send({type: 'log', msg: 'Total allocations tracked: ' + allAllocations.length});
    send({type: 'log', msg: 'Large allocations (>64KB): ' + allocations.length});

    var found = 0;

    // Scan all allocations
    for (var i = 0; i < allAllocations.length; i++) {
        var a = allAllocations[i];
        var info = checkPE(a.address, 'alloc[' + i + ']');
        if (info) {
            found++;
            send({type: 'pe', info: JSON.stringify(info)});
            var dumpSize = Math.min(Math.max(info.sizeOfImage, a.size), 0x4000000);
            try {
                var raw = a.address.readByteArray(dumpSize);
                send({type: 'dump', idx: found, info: JSON.stringify(info)}, raw);
            } catch(e) {
                send({type: 'log', msg: 'Dump error: ' + e});
            }
        }
    }

    // Scan main image base
    var imgInfo = checkPE(ptr(0x400000), 'image_base');
    if (imgInfo) {
        found++;
        send({type: 'pe', info: JSON.stringify(imgInfo)});
        try {
            var raw = ptr(0x400000).readByteArray(Math.min(imgInfo.sizeOfImage, 0x4000000));
            send({type: 'dump', idx: found, info: JSON.stringify(imgInfo)}, raw);
        } catch(e) {
            send({type: 'log', msg: 'Dump main error: ' + e});
        }
    }

    // Scan other common image bases
    var otherBases = [0x10000000, 0x300000, 0x500000, 0x600000, 0x700000, 0x800000, 0x900000, 0xA00000, 0xB00000, 0xC00000, 0xD00000, 0xE00000, 0xF00000, 0x1000000, 0x2000000, 0x3000000, 0x4000000, 0x5000000, 0x6000000, 0x7000000];
    for (var j = 0; j < otherBases.length; j++) {
        try {
            var bInfo = checkPE(ptr(otherBases[j]), 'base_' + otherBases[j].toString(16));
            if (bInfo && bInfo.sizeOfImage > 0x10000) {
                found++;
                send({type: 'pe', info: JSON.stringify(bInfo)});
                var raw = ptr(otherBases[j]).readByteArray(Math.min(bInfo.sizeOfImage, 0x4000000));
                send({type: 'dump', idx: found, info: JSON.stringify(bInfo)}, raw);
            }
        } catch(e) {}
    }

    send({type: 'log', msg: 'Scan complete. Found ' + found + ' PE images.'});
    send({type: 'done', count: found});
}

var tick = 0;
var iv = setInterval(function() {
    tick++;
    if (tick >= 10) {
        clearInterval(iv);
        scanAndDump();
    }
}, 2000);

send({type: 'log', msg: 'Monitor ready. Waiting for unpacking...'});
"""

dumps = []

def on_message(message, data):
    if message.get('type') == 'send':
        payload = message.get('payload', {})
        t = payload.get('type', '')
        if t == 'log':
            print(f"  {payload.get('msg', '')}")
        elif t == 'alloc':
            print(f"  [ALLOC] {payload['address']} size={payload['size']:#x}" + (f" ({payload.get('note','')})" if payload.get('note') else ""))
        elif t == 'loadlib':
            print(f"  [DLL] {payload['name']}")
        elif t == 'loadlibw':
            print(f"  [DLL] {payload['name']}")
        elif t in ('window', 'windoww'):
            print(f"  [WINDOW] {payload['title']}")
        elif t == 'pe':
            info = payload.get('info', '{}')
            print(f"  [PE FOUND] {info}")
        elif t == 'dump':
            idx = payload.get('idx', 0)
            info = payload.get('info', '{}')
            print(f"  [DUMP #{idx}] {info}, data={len(data) if data else 0} bytes")
            if data:
                dumps.append({'idx': idx, 'info': info, 'data': bytes(data)})
        elif t == 'done':
            print(f"  [DONE] Found {payload.get('count', 0)} PE images")
    elif message.get('type') == 'error':
        print(f"  [ERROR] {message.get('description', '')}")

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print(f"Target: {TARGET}")
    print(f"Output: {OUTPUT_DIR}\n")

    print("[*] Spawning target...")
    pid = frida.spawn([TARGET])
    print(f"[*] PID: {pid}")

    session = frida.attach(pid)
    script = session.create_script(FRIDA_SCRIPT)
    script.on('message', on_message)
    script.load()

    print("[*] Resuming process...\n")
    frida.resume(pid)

    print("[*] Waiting 20 seconds for unpacking + card key dialog...\n")
    for i in range(20):
        time.sleep(1)
        print(f"  [{i+1}/20]", end='\r')
    print()

    print(f"\n[*] Collected {len(dumps)} dumps")

    for d in dumps:
        fname = os.path.join(OUTPUT_DIR, f"dump_{d['idx']}.bin")
        with open(fname, 'wb') as f:
            f.write(d['data'])
        print(f"[*] Saved: {fname} ({len(d['data'])} bytes)")

        # Check if valid PE and save as .exe
        data = d['data']
        if data[:2] == b'MZ':
            try:
                pe_off = struct.unpack_from('<I', data, 0x3C)[0]
                if pe_off < 0x800 and data[pe_off:pe_off+4] == b'PE\x00\x00':
                    exe_fname = os.path.join(OUTPUT_DIR, f"unpacked_{d['idx']}.exe")
                    with open(exe_fname, 'wb') as f:
                        f.write(data)
                    n_sec = struct.unpack_from('<H', data, pe_off + 6)[0]
                    soi = struct.unpack_from('<I', data, pe_off + 0x50)[0]
                    ep = struct.unpack_from('<I', data, pe_off + 0x28)[0]
                    ib = struct.unpack_from('<I', data, pe_off + 0x34)[0]
                    print(f"    -> Valid PE: {exe_fname}")
                    print(f"       ImageBase={ib:#x} SizeOfImage={soi:#x} EP={ep:#x} Sections={n_sec}")
            except:
                pass

    try:
        frida.kill(pid)
    except:
        pass

    print("\n[*] Done!")

if __name__ == '__main__':
    main()
