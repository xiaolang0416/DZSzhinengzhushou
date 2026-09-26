import sys
import io
import struct
import time
import os

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

try:
    import frida
except ImportError:
    print("pip install frida")
    sys.exit(1)

JS = r"""
'use strict';

// Scan all readable memory for executable code regions that aren't part of known modules
function scanAllMemory() {
    var results = [];
    var ranges = Process.enumerateRanges('r--');

    for (var i = 0; i < ranges.length; i++) {
        var range = ranges[i];
        var addr = range.base;
        var size = range.size;

        // Skip very small ranges and known system modules
        if (size < 0x1000) continue;

        // Check if this range belongs to a known module
        var isModule = false;
        var modules = Process.enumerateModules();
        for (var m = 0; m < modules.length; m++) {
            var mod = modules[m];
            if (addr.compare(mod.base) >= 0 &&
                addr.compare(mod.base.add(mod.size)) < 0) {
                isModule = true;
                break;
            }
        }

        if (isModule) continue;

        // Check for executable permission
        var execRanges = Process.enumerateRanges('r-x');
        var isExec = false;
        for (var e = 0; e < execRanges.length; e++) {
            var er = execRanges[e];
            if (addr.compare(er.base) >= 0 &&
                addr.compare(er.base.add(er.size)) < 0) {
                isExec = true;
                break;
            }
        }

        // Read first bytes to check for code patterns
        try {
            var firstBytes = addr.readByteArray(64);
            var view = new Uint8Array(firstBytes);
            var hasCodePattern = false;

            // Check for common x86 code patterns (push ebp, mov ebp esp, etc.)
            for (var j = 0; j < 60; j++) {
                // push ebp; mov ebp,esp = 55 8B EC
                if (view[j] === 0x55 && view[j+1] === 0x8B && view[j+2] === 0xEC) {
                    hasCodePattern = true;
                    break;
                }
                // push ebx; ... = 53 xx
                if (view[j] === 0x53 && view[j+1] >= 0x40 && view[j+1] <= 0x5F) {
                    hasCodePattern = true;
                    break;
                }
            }

            if (size >= 0x10000 && (hasCodePattern || isExec)) {
                results.push({
                    addr: addr.toString(),
                    size: size,
                    exec: isExec,
                    codePattern: hasCodePattern,
                    firstBytes: Array.from(view.slice(0, 32)).map(function(b) {
                        return ('0' + b.toString(16)).slice(-2);
                    }).join('')
                });
            }
        } catch(e) {}
    }

    return results;
}

// Check if the main program has been unpacked by looking at section data
function checkUnpackStatus() {
    var modules = Process.enumerateModules();
    var mainMod = null;

    for (var i = 0; i < modules.length; i++) {
        var m = modules[i];
        if (m.name.indexOf('.exe') !== -1 && m.size > 0x100000) {
            mainMod = m;
            break;
        }
    }

    if (!mainMod) return { error: 'main module not found' };

    var base = mainMod.base;
    var result = {
        module: mainMod.name,
        base: base.toString(),
        size: mainMod.size,
        sections: []
    };

    // Read PE header
    try {
        var peOff = base.add(0x3C).readU32();
        var numSec = base.add(peOff + 6).readU16();
        var optSize = base.add(peOff + 20).readU16();
        var secStart = peOff + 24 + optSize;

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
            var rawsize = base.add(off + 16).readU32();

            // Check if section data is zeroed
            var zeroCount = 0;
            var checkSize = Math.min(vsize, 0x100);
            if (rva > 0 && vsize > 0) {
                try {
                    var secData = base.add(rva).readByteArray(checkSize);
                    var secArr = new Uint8Array(secData);
                    for (var k = 0; k < secArr.length; k++) {
                        if (secArr[k] === 0) zeroCount++;
                    }
                } catch(e) {}
            }

            result.sections.push({
                name: name,
                rva: '0x' + rva.toString(16),
                vsize: '0x' + vsize.toString(16),
                zeroPercent: Math.round(zeroCount / Math.max(checkSize, 1) * 100)
            });
        }

        // Read entry point bytes
        var epRva = base.add(peOff + 0x28).readU32();
        var epAddr = base.add(epRva);
        try {
            var epBytes = epAddr.readByteArray(32);
            result.entryPoint = '0x' + epRva.toString(16);
            result.epBytes = Array.from(new Uint8Array(epBytes)).map(function(b) {
                return ('0' + b.toString(16)).slice(-2);
            }).join('');
        } catch(e) {
            result.entryPoint = '0x' + epRva.toString(16) + ' (unreadable)';
        }
    } catch(e) {
        result.error = e.toString();
    }

    return result;
}

// Find large executable memory regions not backed by files
function findUnpackedCode() {
    var execRanges = Process.enumerateRanges('r-x');
    var results = [];

    for (var i = 0; i < execRanges.length; i++) {
        var r = execRanges[i];
        if (r.size < 0x10000) continue; // Skip small regions

        // Check if it belongs to a known module
        var isModule = false;
        var modules = Process.enumerateModules();
        for (var m = 0; m < modules.length; m++) {
            var mod = modules[m];
            if (r.base.compare(mod.base) >= 0 &&
                r.base.compare(mod.base.add(mod.size)) < 0) {
                isModule = true;
                break;
            }
        }

        if (!isModule) {
            try {
                var bytes = r.base.readByteArray(64);
                results.push({
                    addr: r.base.toString(),
                    size: r.size,
                    firstBytes: Array.from(new Uint8Array(bytes)).map(function(b) {
                        return ('0' + b.toString(16)).slice(-2);
                    }).join('')
                });
            } catch(e) {}
        }
    }

    return results;
}

rpc.exports = {
    scan: function() {
        return {
            nonModuleCode: scanAllMemory(),
            unpackStatus: checkUnpackStatus(),
            execRegions: findUnpackedCode()
        };
    }
};
"""

def find_process():
    try:
        devices = frida.enumerate_devices()
        for dev in devices:
            if dev.id == 'local':
                procs = dev.enumerate_processes()
                for p in procs:
                    if '飞飞' in p.name or 'feifei' in p.name.lower() or '4.8991' in p.name:
                        return p.pid, p.name
                # Also look by partial match
                for p in procs:
                    if '.exe' in p.name and p.pid > 1000:
                        pass  # too broad
        return None, None
    except Exception as e:
        print(f"Error finding process: {e}")
        return None, None

def main():
    # Try to find the process
    pid, name = find_process()

    if pid is None:
        # Try attaching by name patterns
        patterns = ['飞飞智能助手', 'feifei', '4.8991']
        device = frida.get_local_device()
        procs = device.enumerate_processes()
        print("Available processes:")
        for p in procs:
            if p.pid > 100:
                print(f"  PID {p.pid}: {p.name}")

        for pattern in patterns:
            for p in procs:
                if pattern in p.name:
                    pid = p.pid
                    name = p.name
                    break
            if pid:
                break

    if pid is None:
        print("\nCould not find the target process. Please start it first.")
        print("Then run this script again.")
        return

    print(f"Found process: {name} (PID {pid})")

    session = frida.attach(pid)
    script = session.create_script(JS)
    script.load()

    print("\n=== Scanning memory for unpacked code ===\n")

    result = script.exports_sync.scan()

    # Unpack status
    status = result.get('unpackStatus', {})
    print(f"Main module: {status.get('module', 'N/A')}")
    print(f"Base: {status.get('base', 'N/A')}")
    print(f"Size: {status.get('size', 'N/A')}")
    print(f"Entry point: {status.get('entryPoint', 'N/A')}")
    print(f"EP bytes: {status.get('epBytes', 'N/A')}")
    print()

    for sec in status.get('sections', []):
        print(f"  {sec['name']:12s} RVA={sec['rva']:>10s} VSize={sec['vsize']:>10s} Zero={sec['zeroPercent']}%")

    # Non-module executable regions
    regions = result.get('execRegions', [])
    print(f"\n=== Non-module executable regions ({len(regions)}) ===")
    for r in regions:
        print(f"  {r['addr']}  size=0x{r['size']:X}  first={r['firstBytes'][:64]}")

    # Non-module code with patterns
    code_regions = result.get('nonModuleCode', [])
    print(f"\n=== Non-module code regions ({len(code_regions)}) ===")
    for c in code_regions:
        print(f"  {c['addr']}  size=0x{c['size']:X}  exec={c['exec']}  code={c['codePattern']}  first={c['firstBytes']}")

    session.detach()
    print("\nDone.")

if __name__ == '__main__':
    main()
