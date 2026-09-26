"""
Memory dumper - dumps process memory after unpacking
"""
import frida
import struct
import time
import os

TARGET = r"D:\下载\飞飞智能助手4.8991\飞飞智能助手4.8991\飞飞智能助手4.8991.exe"
OUTPUT_DIR = r"C:\Users\Administrator\Documents\Qoder\2026-09-26\fded2f16\dump"

DUMP_SCRIPT = r"""
'use strict';

function readSafe(addr, size) {
    try { return addr.readByteArray(size); } catch(e) { return null; }
}

function checkPE(addr) {
    try {
        var hdr = new Uint8Array(addr.readByteArray(64));
        if (hdr[0] !== 0x4d || hdr[1] !== 0x5a) return null;
        var peOff = addr.add(0x3c).readU32();
        if (peOff > 0x800) return null;
        var pe = new Uint8Array(addr.add(peOff).readByteArray(4));
        if (!(pe[0]===0x50 && pe[1]===0x45 && pe[2]===0 && pe[3]===0)) return null;
        return {
            peOffset: peOff,
            numSections: addr.add(peOff+6).readU16(),
            sizeOfImage: addr.add(peOff+0x50).readU32(),
            imageBase: addr.add(peOff+0x34).readU32(),
            entryPoint: addr.add(peOff+0x28).readU32(),
        };
    } catch(e) { return null; }
}

// 1. Dump main image at 0x400000
var base = ptr(0x400000);
var peInfo = checkPE(base);
if (peInfo) {
    send({type: 'log', msg: 'Main image PE: ' + JSON.stringify(peInfo)});
    var raw = readSafe(base, Math.min(peInfo.sizeOfImage, 0x4000000));
    if (raw) send({type: 'dump', name: 'main_image', size: peInfo.sizeOfImage}, raw);
} else {
    send({type: 'log', msg: 'No PE at 0x400000, dumping first 0x100 bytes to check...'});
    var raw = readSafe(base, 0x100);
    if (raw) {
        var bytes = new Uint8Array(raw);
        var hex = '';
        for (var i = 0; i < Math.min(bytes.length, 64); i++) hex += bytes[i].toString(16).padStart(2,'0') + ' ';
        send({type: 'log', msg: '0x400000: ' + hex});
    }
    // Dump anyway
    var raw2 = readSafe(base, 0x500000);
    if (raw2) send({type: 'dump', name: 'main_image_raw', size: 0x500000}, raw2);
}

// 2. Dump the large VirtualAlloc regions
var regions = [
    {addr: ptr(0x5d70000), size: 0x30d40, name: 'alloc_5d70000'},
    {addr: ptr(0x5db0000), size: 0x2d3100, name: 'alloc_5db0000'},
];

for (var i = 0; i < regions.length; i++) {
    var r = regions[i];
    var pe = checkPE(r.addr);
    if (pe) {
        send({type: 'log', msg: r.name + ' has PE: ' + JSON.stringify(pe)});
    }
    var raw = readSafe(r.addr, r.size);
    if (raw) send({type: 'dump', name: r.name, size: r.size}, raw);
}

// 3. Enumerate all readable memory ranges and look for PE signatures
send({type: 'log', msg: 'Scanning all memory ranges for PE signatures...'});
try {
    var ranges = Process.enumerateRanges('r--');
    send({type: 'log', msg: 'Total readable ranges: ' + ranges.length});
    var peCount = 0;
    for (var i = 0; i < ranges.length; i++) {
        var r = ranges[i];
        if (r.size < 0x200) continue;
        try {
            var hdr = new Uint8Array(r.base.readByteArray(4));
            if (hdr[0] === 0x4d && hdr[1] === 0x5a) {
                var pe = checkPE(r.base);
                if (pe && pe.sizeOfImage > 0x10000) {
                    peCount++;
                    send({type: 'log', msg: 'PE at ' + r.base + ' size=' + pe.sizeOfImage.toString(16)});
                    var raw = readSafe(r.base, Math.min(pe.sizeOfImage, 0x4000000));
                    if (raw) send({type: 'dump', name: 'pe_' + peCount + '_' + r.base.toString(), size: pe.sizeOfImage}, raw);
                }
            }
        } catch(e) {}
    }
    send({type: 'log', msg: 'Found ' + peCount + ' PE images in memory'});
} catch(e) {
    send({type: 'log', msg: 'Range scan error: ' + e});
}

// 4. Also enumerate modules
try {
    var mods = Process.enumerateModules();
    send({type: 'log', msg: 'Loaded modules: ' + mods.length});
    for (var i = 0; i < mods.length; i++) {
        var m = mods[i];
        send({type: 'log', msg: '  ' + m.name + ' base=' + m.base + ' size=' + m.size.toString(16) + ' path=' + m.path});
    }
} catch(e) {
    send({type: 'log', msg: 'Module enum error: ' + e});
}

send({type: 'done'});
"""

dumps = []

def on_message(message, data):
    if message.get('type') == 'send':
        p = message.get('payload', {})
        t = p.get('type', '')
        if t == 'log':
            print(f"  {p.get('msg','')}")
        elif t == 'dump':
            name = p.get('name', 'unknown')
            size = p.get('size', 0)
            dsize = len(data) if data else 0
            print(f"  [DUMP] {name}: expected={size:#x} got={dsize:#x}")
            if data:
                dumps.append({'name': name, 'data': bytes(data), 'expected_size': size})
        elif t == 'done':
            print("  [DONE]")
    elif message.get('type') == 'error':
        print(f"  [ERROR] {message.get('description','')}")

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print(f"Target: {TARGET}\n")

    print("[*] Spawning...")
    pid = frida.spawn([TARGET])
    session = frida.attach(pid)
    script = session.create_script(DUMP_SCRIPT)
    script.on('message', on_message)
    script.load()

    print("[*] Resuming and waiting 15 seconds for full unpack...\n")
    frida.resume(pid)
    time.sleep(15)

    # Now run the dump script (it runs immediately on load, but we need the process to be unpacked first)
    # The script already ran on load, so we just collect results

    print(f"\n[*] Collected {len(dumps)} dumps, saving...\n")

    for d in dumps:
        fname = os.path.join(OUTPUT_DIR, f"{d['name']}.bin")
        with open(fname, 'wb') as f:
            f.write(d['data'])
        print(f"  Saved: {fname} ({len(d['data'])} bytes)")

        data = d['data']
        if data[:2] == b'MZ':
            try:
                pe_off = struct.unpack_from('<I', data, 0x3C)[0]
                if pe_off < 0x800 and data[pe_off:pe_off+4] == b'PE\x00\x00':
                    exe_fname = os.path.join(OUTPUT_DIR, f"{d['name']}.exe")
                    with open(exe_fname, 'wb') as f:
                        f.write(data)
                    n_sec = struct.unpack_from('<H', data, pe_off + 6)[0]
                    soi = struct.unpack_from('<I', data, pe_off + 0x50)[0]
                    ep = struct.unpack_from('<I', data, pe_off + 0x28)[0]
                    ib = struct.unpack_from('<I', data, pe_off + 0x34)[0]
                    print(f"    -> Valid PE! IB={ib:#x} SOI={soi:#x} EP={ep:#x} Sec={n_sec}")

                    # Parse sections
                    sec_off = pe_off + 24 + struct.unpack_from('<H', data, pe_off + 20)[0]
                    for s in range(n_sec):
                        soff = sec_off + s * 40
                        sname = data[soff:soff+8].rstrip(b'\x00').decode('ascii', errors='replace')
                        svsize = struct.unpack_from('<I', data, soff + 8)[0]
                        srva = struct.unpack_from('<I', data, soff + 12)[0]
                        srawsize = struct.unpack_from('<I', data, soff + 16)[0]
                        srawptr = struct.unpack_from('<I', data, soff + 20)[0]
                        print(f"    Sec {s}: {sname!r} RVA={srva:#x} VSize={svsize:#x} Raw={srawptr:#x}:{srawsize:#x}")
            except Exception as e:
                print(f"    PE parse error: {e}")

    try:
        frida.kill(pid)
    except:
        pass

    print("\n[*] Done!")

if __name__ == '__main__':
    main()
