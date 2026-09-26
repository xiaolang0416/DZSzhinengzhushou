"""
Quick memory scan of running process - attach and dump.
"""
import sys
import io
import struct
import os
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import frida

OUTPUT_DIR = 'quick_dump'
os.makedirs(OUTPUT_DIR, exist_ok=True)

JS = r"""
'use strict';

function scan() {
    var results = [];
    var mod = null;
    var modules = Process.enumerateModules();

    for (var i = 0; i < modules.length; i++) {
        var m = modules[i];
        if (m.size > 0x100000) {
            mod = m;
            break;
        }
    }

    if (!mod) return { error: 'no main module' };

    var base = mod.base;
    var size = mod.size;

    // Parse PE sections
    var peOff = base.add(0x3C).readU32();
    var numSec = base.add(peOff + 6).readU16();
    var optSize = base.add(peOff + 20).readU16();
    var secStart = peOff + 24 + optSize;
    var epRva = base.add(peOff + 0x28).readU32();

    var sections = [];
    for (var s = 0; s < numSec; s++) {
        var off = secStart + s * 40;
        var nameBytes = base.add(off).readByteArray(8);
        var nameArr = new Uint8Array(nameBytes);
        var name = '';
        for (var n = 0; n < 8 && nameArr[n] !== 0; n++) {
            name += String.fromCharCode(nameArr[n]);
        }

        var rva = base.add(off + 12).readU32();
        var vsize = base.add(off + 8).readU32();

        // Check section content
        var nonZero = 0;
        var codePatterns = 0;
        var checkSize = Math.min(vsize, 0x2000);

        if (rva > 0 && vsize > 0) {
            try {
                var secData = base.add(rva).readByteArray(checkSize);
                var arr = new Uint8Array(secData);
                for (var k = 0; k < arr.length - 2; k++) {
                    if (arr[k] !== 0) nonZero++;
                    if (arr[k] === 0x55 && arr[k+1] === 0x8B && arr[k+2] === 0xEC) {
                        codePatterns++;
                    }
                }
            } catch(e) {}
        }

        sections.push({
            name: name,
            rva: rva,
            vsize: vsize,
            nonZeroPct: Math.round(nonZero / Math.max(checkSize, 1) * 100),
            codePatterns: codePatterns
        });
    }

    // Find non-module executable regions
    var execRanges = Process.enumerateRanges('r-x');
    var codeRegions = [];

    for (var e = 0; e < execRanges.length; e++) {
        var r = execRanges[e];
        if (r.size < 0x10000) continue;

        var isMod = false;
        for (var m = 0; m < modules.length; m++) {
            if (r.base.compare(modules[m].base) >= 0 &&
                r.base.compare(modules[m].base.add(modules[m].size)) < 0) {
                isMod = true;
                break;
            }
        }

        if (!isMod) {
            try {
                var bytes = r.base.readByteArray(Math.min(r.size, 0x1000));
                var arr = new Uint8Array(bytes);
                var nz = 0;
                var cp = 0;
                for (var j = 0; j < arr.length - 2; j++) {
                    if (arr[j] !== 0) nz++;
                    if (arr[j] === 0x55 && arr[j+1] === 0x8B && arr[j+2] === 0xEC) cp++;
                }
                if (nz > arr.length * 0.2) {
                    codeRegions.push({
                        addr: r.base.toString(),
                        size: r.size,
                        nonZeroPct: Math.round(nz / arr.length * 100),
                        codePatterns: cp
                    });
                }
            } catch(e) {}
        }
    }

    return {
        module: mod.name,
        base: base.toString(),
        size: size,
        epRva: epRva,
        sections: sections,
        codeRegions: codeRegions,
        moduleCount: modules.length
    };
}

rpc.exports = { scan: scan };
"""

def on_message(message, data):
    if message['type'] == 'send':
        payload = message['payload']
        if payload.get('type') == 'dump':
            fn = payload['filename']
            with open(os.path.join(OUTPUT_DIR, fn), 'wb') as f:
                f.write(data)
            print(f"  Saved {fn} ({len(data)} bytes)")
    elif message['type'] == 'error':
        print(f"  [ERR] {message.get('description', message)}")

def main():
    device = frida.get_local_device()
    procs = device.enumerate_processes()

    target_pid = None
    target_name = None
    for p in procs:
        if '飞飞' in p.name or '8991' in p.name or '助手' in p.name:
            target_pid = p.pid
            target_name = p.name
            break

    if target_pid is None:
        print("Target process not found!")
        return

    print(f"Found: {target_name} (PID {target_pid})")

    session = frida.attach(target_pid)
    script = session.create_script(JS)
    script.on('message', on_message)
    script.load()

    print("Scanning memory...\n")
    result = script.exports_sync.scan()

    if 'error' in result:
        print(f"Error: {result['error']}")
        session.detach()
        return

    print(f"Module: {result['module']}")
    print(f"Base: {result['base']}")
    print(f"Size: 0x{result['size']:X}")
    print(f"EP RVA: 0x{result['epRva']:X}")
    print(f"Modules loaded: {result['moduleCount']}")

    print(f"\n=== Sections ===")
    for sec in result['sections']:
        bar = '#' * (sec['nonZeroPct'] // 5) + '.' * (20 - sec['nonZeroPct'] // 5)
        print(f"  {sec['name']:12s} RVA=0x{sec['rva']:06X} VSize=0x{sec['vsize']:06X} "
              f"NonZero={sec['nonZeroPct']:3d}% [{bar}] Code={sec['codePatterns']}")

    print(f"\n=== Non-module code regions ({len(result['codeRegions'])}) ===")
    for r in result['codeRegions']:
        print(f"  {r['addr']}  size=0x{r['size']:X}  nonZero={r['nonZeroPct']}%  code={r['codePatterns']}")

    # Dump the main module
    print(f"\n=== Dumping main module ===")
    mod_base = int(result['base'], 16)
    mod_size = result['size']

    dump_js = f"""
    var base = ptr('{result['base']}');
    var size = {mod_size};
    var chunkSize = 0x100000;
    var offset = 0;
    while (offset < size) {{
        var thisChunk = Math.min(chunkSize, size - offset);
        try {{
            var data = base.add(offset).readByteArray(thisChunk);
            send({{ type: 'dump', filename: 'full_dump.bin', offset: offset }}, data);
        }} catch(e) {{
            send({{ type: 'log', msg: 'chunk error at 0x' + offset.toString(16) }});
        }}
        offset += thisChunk;
    }}
    send({{ type: 'done' }});
    """

    # Remove old dump
    dump_path = os.path.join(OUTPUT_DIR, 'full_dump.bin')
    if os.path.exists(dump_path):
        os.remove(dump_path)

    dump_script = session.create_script(dump_js)
    dump_script.on('message', on_message)
    dump_script.load()

    # Wait for dump to complete
    time.sleep(3)

    session.detach()

    # Check dump
    if os.path.exists(dump_path):
        dump_size = os.path.getsize(dump_path)
        print(f"Full dump: {dump_size} bytes ({dump_size/1024/1024:.1f} MB)")

        # Analyze the dump
        with open(dump_path, 'rb') as f:
            dump_data = f.read()

        # Parse PE sections in dump
        pe_off = struct.unpack_from('<I', dump_data, 0x3C)[0]
        num_sec = struct.unpack_from('<H', dump_data, pe_off + 6)[0]
        opt_size = struct.unpack_from('<H', dump_data, pe_off + 20)[0]
        sec_start = pe_off + 24 + opt_size
        ep = struct.unpack_from('<I', dump_data, pe_off + 0x28)[0]

        print(f"\nDump PE: EP=0x{ep:X}, Sections={num_sec}")
        for s in range(num_sec):
            off = sec_start + s * 40
            name = dump_data[off:off+8].rstrip(b'\x00').decode('ascii', errors='replace')
            rva = struct.unpack_from('<I', dump_data, off + 12)[0]
            vsize = struct.unpack_from('<I', dump_data, off + 8)[0]

            # Check content at this RVA in the dump
            if rva + min(vsize, 256) <= len(dump_data):
                sec_bytes = dump_data[rva:rva+min(vsize, 256)]
                non_zero = sum(1 for b in sec_bytes if b != 0)
                pct = non_zero * 100 // len(sec_bytes)
                print(f"  {name:12s} RVA=0x{rva:06X} VSize=0x{vsize:06X} NonZero={pct}%")
            else:
                print(f"  {name:12s} RVA=0x{rva:06X} VSize=0x{vsize:06X} (beyond dump)")
    else:
        print("No dump file created!")

    print("\nDone.")

if __name__ == '__main__':
    main()
