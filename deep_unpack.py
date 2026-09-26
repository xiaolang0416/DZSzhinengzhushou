"""
Deep MEW unpacker using Frida spawn mode.
Hooks the MEW stub decryption and captures the unpacked PE in memory.
Strategy:
1. Spawn the process (suspended)
2. Hook the MEW stub entry point
3. Set a breakpoint at the MEW stub's final JMP/RET (transfer to OEP)
4. When OEP is reached, dump the full unpacked PE
"""
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

EXE_PATH = r'D:\下载\飞飞智能助手4.8991\飞飞智能助手4.8991\飞飞智能助手4.8991.exe'
OUTPUT_DIR = 'deep_dump'
os.makedirs(OUTPUT_DIR, exist_ok=True)

# The MEW stub entry point is at RVA 0x786000
# After decryption, it should JMP to the original entry point
# The stub decrypts 0x1000 DWORDs from image base, then returns

JS_CODE = r"""
'use strict';

var unpackDone = false;
var imageBase = null;
var imageSize = 0;

// Find the main module
function findMainModule() {
    var modules = Process.enumerateModules();
    for (var i = 0; i < modules.length; i++) {
        var m = modules[i];
        if (m.name.indexOf('.exe') !== -1 && m.size > 0x100000) {
            return m;
        }
    }
    return null;
}

// Read PE section info
function getSectionInfo(base) {
    var peOff = base.add(0x3C).readU32();
    var numSec = base.add(peOff + 6).readU16();
    var optSize = base.add(peOff + 20).readU16();
    var secStart = peOff + 24 + optSize;
    var sections = [];

    for (var i = 0; i < numSec; i++) {
        var off = secStart + i * 40;
        var nameBytes = base.add(off).readByteArray(8);
        var nameArr = new Uint8Array(nameBytes);
        var name = '';
        for (var n = 0; n < 8 && nameArr[n] !== 0; n++) {
            name += String.fromCharCode(nameArr[n]);
        }
        sections.push({
            name: name,
            rva: base.add(off + 12).readU32(),
            vsize: base.add(off + 8).readU32(),
            rawsize: base.add(off + 16).readU32(),
            rawptr: base.add(off + 20).readU32()
        });
    }
    return { peOff: peOff, numSec: numSec, sections: sections };
}

// Check if a memory region has been unpacked (non-zero, has code patterns)
function checkRegionUnpacked(addr, size) {
    try {
        var data = addr.readByteArray(Math.min(size, 0x1000));
        var arr = new Uint8Array(data);
        var nonZero = 0;
        var codePatterns = 0;
        for (var i = 0; i < arr.length - 2; i++) {
            if (arr[i] !== 0) nonZero++;
            // push ebp; mov ebp, esp
            if (arr[i] === 0x55 && arr[i+1] === 0x8B && arr[i+2] === 0xEC) {
                codePatterns++;
            }
        }
        return {
            nonZeroPercent: Math.round(nonZero / arr.length * 100),
            codePatterns: codePatterns
        };
    } catch(e) {
        return { nonZeroPercent: 0, codePatterns: 0, error: e.toString() };
    }
}

// Dump a memory region
function dumpRegion(addr, size, filename) {
    try {
        var data = addr.readByteArray(size);
        send({ type: 'dump', filename: filename, size: size }, data);
        return true;
    } catch(e) {
        send({ type: 'error', msg: 'dump failed: ' + e.toString() });
        return false;
    }
}

// Monitor memory writes to detect unpacking
function hookMemoryWrites() {
    // Hook VirtualProtect - used when unpacker makes memory executable
    try {
        var vp = Module.getGlobalExportByName('VirtualProtect');
        if (vp) {
            Interceptor.attach(vp, {
                onEnter: function(args) {
                    this.addr = args[0];
                    this.size = args[1].toInt32();
                    this.newProt = args[2].toInt32();
                },
                onLeave: function(retval) {
                    send({
                        type: 'virtualprotect',
                        addr: this.addr.toString(),
                        size: '0x' + this.size.toString(16),
                        prot: this.newProt
                    });
                }
            });
            send({ type: 'log', msg: 'Hooked VirtualProtect' });
        }
    } catch(e) {
        send({ type: 'log', msg: 'VirtualProtect hook failed: ' + e.toString() });
    }

    // Hook VirtualAlloc - track new allocations
    try {
        var va = Module.getGlobalExportByName('VirtualAlloc');
        if (va) {
            Interceptor.attach(va, {
                onEnter: function(args) {
                    this.addr = args[0];
                    this.size = args[1].toInt32();
                    this.type = args[2].toInt32();
                    this.prot = args[3].toInt32();
                },
                onLeave: function(retval) {
                    send({
                        type: 'virtualalloc',
                        requestedAddr: this.addr.toString(),
                        allocatedAddr: retval.toString(),
                        size: '0x' + this.size.toString(16),
                        allocType: this.type,
                        prot: this.prot
                    });

                    // After allocation, check if a PE is being placed there
                    var allocAddr = retval;
                    var allocSize = this.size;
                    if (allocSize >= 0x10000) {
                        // Schedule a check after a short delay
                        setTimeout(function() {
                            var status = checkRegionUnpacked(allocAddr, allocSize);
                            if (status.nonZeroPercent > 10) {
                                send({
                                    type: 'alloc_check',
                                    addr: allocAddr.toString(),
                                    size: '0x' + allocSize.toString(16),
                                    nonZero: status.nonZeroPercent,
                                    codePatterns: status.codePatterns
                                });
                            }
                        }, 100);
                    }
                }
            });
            send({ type: 'log', msg: 'Hooked VirtualAlloc' });
        }
    } catch(e) {
        send({ type: 'log', msg: 'VirtualAlloc hook failed: ' + e.toString() });
    }

    // Hook WriteProcessMemory - used by some packers
    try {
        var wpm = Module.getGlobalExportByName('WriteProcessMemory');
        if (wpm) {
            Interceptor.attach(wpm, {
                onEnter: function(args) {
                    this.targetAddr = args[1];
                    this.bufAddr = args[2];
                    this.writeSize = args[3].toInt32();
                },
                onLeave: function(retval) {
                    send({
                        type: 'writeprocessmemory',
                        target: this.targetAddr.toString(),
                        size: '0x' + this.writeSize.toString(16)
                    });
                }
            });
            send({ type: 'log', msg: 'Hooked WriteProcessMemory' });
        }
    } catch(e) {}

    // Hook NtWriteVirtualMemory (lower level)
    try {
        var wvm = Module.getGlobalExportByName('NtWriteVirtualMemory');
        if (wvm) {
            Interceptor.attach(wvm, {
                onEnter: function(args) {
                    this.handle = args[0];
                    this.targetAddr = args[1];
                    this.bufAddr = args[2];
                    this.writeSize = args[3].toInt32();
                },
                onLeave: function(retval) {
                    send({
                        type: 'ntwritevirtualmemory',
                        target: this.targetAddr.toString(),
                        size: '0x' + this.writeSize.toString(16)
                    });
                }
            });
            send({ type: 'log', msg: 'Hooked NtWriteVirtualMemory' });
        }
    } catch(e) {}
}

// Periodically scan all executable memory for unpacked code
function startPeriodicScan() {
    var scanCount = 0;
    var interval = setInterval(function() {
        scanCount++;
        if (scanCount > 60 || unpackDone) {  // 60 scans * 500ms = 30 seconds
            clearInterval(interval);
            if (!unpackDone) {
                send({ type: 'log', msg: 'Scan timeout - doing final dump' });
                doFinalDump();
            }
            return;
        }

        // Check main module sections
        var mod = findMainModule();
        if (!mod) return;

        var info = getSectionInfo(mod.base);
        var anyUnpacked = false;

        for (var i = 0; i < info.sections.length; i++) {
            var sec = info.sections[i];
            if (sec.vsize === 0 || sec.rva === 0) continue;

            var secAddr = mod.base.add(sec.rva);
            var status = checkRegionUnpacked(secAddr, sec.vsize);

            if (status.nonZeroPercent > 20 && status.codePatterns > 0) {
                send({
                    type: 'section_unpacked',
                    section: sec.name,
                    rva: '0x' + sec.rva.toString(16),
                    nonZero: status.nonZeroPercent,
                    codePatterns: status.codePatterns,
                    scanNum: scanCount
                });
                anyUnpacked = true;
            }
        }

        // Also scan for non-module executable regions
        var execRanges = Process.enumerateRanges('r-x');
        for (var j = 0; j < execRanges.length; j++) {
            var r = execRanges[j];
            if (r.size < 0x10000) continue;

            var isMod = false;
            var modules = Process.enumerateModules();
            for (var m = 0; m < modules.length; m++) {
                if (r.base.compare(modules[m].base) >= 0 &&
                    r.base.compare(modules[m].base.add(modules[m].size)) < 0) {
                    isMod = true;
                    break;
                }
            }

            if (!isMod) {
                var st = checkRegionUnpacked(r.base, r.size);
                if (st.nonZeroPercent > 30 && st.codePatterns > 2) {
                    send({
                        type: 'new_code_region',
                        addr: r.base.toString(),
                        size: '0x' + r.size.toString(16),
                        nonZero: st.nonZeroPercent,
                        codePatterns: st.codePatterns,
                        scanNum: scanCount
                    });

                    // Dump this region
                    dumpRegion(r.base, r.size, 'code_region_' + scanCount + '_' + r.base.toString() + '.bin');
                    unpackDone = true;
                }
            }
        }

        if (anyUnpacked && scanCount > 5) {
            // Sections are being unpacked, do a full dump
            send({ type: 'log', msg: 'Sections appear unpacked at scan ' + scanCount });
            unpackDone = true;
            doFinalDump();
            clearInterval(interval);
        }

    }, 500);  // Every 500ms

    send({ type: 'log', msg: 'Periodic scan started (500ms interval, max 60 scans)' });
}

// Dump the full main module
function doFinalDump() {
    var mod = findMainModule();
    if (!mod) {
        send({ type: 'error', msg: 'main module not found for final dump' });
        return;
    }

    send({ type: 'log', msg: 'Final dump of ' + mod.name + ' at ' + mod.base.toString() + ' size=0x' + mod.size.toString(16) });

    // Dump in chunks to avoid memory issues
    var chunkSize = 0x100000;  // 1MB chunks
    var totalSize = mod.size;
    var dumped = 0;

    while (dumped < totalSize) {
        var thisChunk = Math.min(chunkSize, totalSize - dumped);
        try {
            var chunkData = mod.base.add(dumped).readByteArray(thisChunk);
            send({
                type: 'dump_chunk',
                offset: dumped,
                size: thisChunk,
                filename: 'full_dump.bin'
            }, chunkData);
        } catch(e) {
            send({ type: 'log', msg: 'Chunk at 0x' + dumped.toString(16) + ' failed: ' + e.toString() });
        }
        dumped += thisChunk;
    }

    // Also dump section-by-section for analysis
    var info = getSectionInfo(mod.base);
    for (var i = 0; i < info.sections.length; i++) {
        var sec = info.sections[i];
        if (sec.vsize === 0 || sec.rva === 0) continue;
        try {
            var secData = mod.base.add(sec.rva).readByteArray(sec.vsize);
            send({
                type: 'dump_section',
                name: sec.name,
                rva: sec.rva,
                vsize: sec.vsize,
                filename: 'sec_' + i + '_' + sec.name + '.bin'
            }, secData);
        } catch(e) {}
    }

    send({ type: 'log', msg: 'Final dump complete' });
}

// Main
function main() {
    send({ type: 'log', msg: 'Script loaded, waiting for process to initialize...' });

    var mod = findMainModule();
    if (mod) {
        imageBase = mod.base;
        imageSize = mod.size;
        send({
            type: 'log',
            msg: 'Main module: ' + mod.name + ' base=' + mod.base.toString() + ' size=0x' + mod.size.toString(16)
        });

        // Log section status
        var info = getSectionInfo(mod.base);
        for (var i = 0; i < info.sections.length; i++) {
            var sec = info.sections[i];
            var status = checkRegionUnpacked(mod.base.add(sec.rva), sec.vsize);
            send({
                type: 'log',
                msg: 'Section ' + sec.name + ' RVA=0x' + sec.rva.toString(16) +
                     ' VSize=0x' + sec.vsize.toString(16) +
                     ' nonZero=' + status.nonZeroPercent + '%' +
                     ' codePatterns=' + status.codePatterns
            });
        }
    }

    // Hook memory operations
    hookMemoryWrites();

    // Start periodic scanning
    startPeriodicScan();

    send({ type: 'log', msg: 'All hooks installed, monitoring unpacking...' });
}

main();
"""

def on_message(message, data):
    if message['type'] == 'send':
        payload = message['payload']
        msg_type = payload.get('type', 'unknown')

        if msg_type == 'log':
            print(f"  [LOG] {payload.get('msg', '')}")
        elif msg_type == 'error':
            print(f"  [ERR] {payload.get('msg', '')}")
        elif msg_type == 'virtualprotect':
            print(f"  [VP] {payload['addr']} size={payload['size']} prot={payload['prot']}")
        elif msg_type == 'virtualalloc':
            print(f"  [VA] {payload['allocatedAddr']} size={payload['size']} type={payload['allocType']}")
        elif msg_type == 'writeprocessmemory':
            print(f"  [WPM] target={payload['target']} size={payload['size']}")
        elif msg_type == 'ntwritevirtualmemory':
            print(f"  [WVM] target={payload['target']} size={payload['size']}")
        elif msg_type == 'section_unpacked':
            print(f"  [UNPACK] Section '{payload['section']}' RVA={payload['rva']} nonZero={payload['nonZero']}% codePatterns={payload['codePatterns']} scan#{payload['scanNum']}")
        elif msg_type == 'new_code_region':
            print(f"  [CODE] New code region at {payload['addr']} size={payload['size']} nonZero={payload['nonZero']}% patterns={payload['codePatterns']} scan#{payload['scanNum']}")
        elif msg_type == 'alloc_check':
            print(f"  [ALLOC] {payload['addr']} size={payload['size']} nonZero={payload['nonZero']}% patterns={payload['codePatterns']}")
        elif msg_type in ('dump', 'dump_chunk', 'dump_section'):
            filename = payload.get('filename', 'unknown.bin')
            filepath = os.path.join(OUTPUT_DIR, filename)

            if msg_type == 'dump_chunk':
                # Append to file
                mode = 'ab' if payload.get('offset', 0) > 0 else 'wb'
                with open(filepath, mode) as f:
                    f.write(data)
                print(f"  [DUMP] {filename} chunk at 0x{payload['offset']:X} ({len(data)} bytes)")
            else:
                with open(filepath, 'wb') as f:
                    f.write(data)
                print(f"  [DUMP] Saved {filename} ({len(data)} bytes)")
        else:
            print(f"  [MSG] {payload}")
    elif message['type'] == 'error':
        print(f"  [FRIDA ERR] {message.get('description', message)}")

def main():
    print(f"Target: {EXE_PATH}")
    print(f"Output: {OUTPUT_DIR}/")
    print()

    device = frida.get_local_device()
    print(f"Device: {device.name}")

    # Spawn the process
    print("Spawning process...")
    pid = frida.spawn([EXE_PATH])
    print(f"Spawned PID: {pid}")

    # Attach
    session = frida.attach(pid)
    print("Attached")

    # Create and load script
    script = session.create_script(JS_CODE)
    script.on('message', on_message)
    script.load()
    print("Script loaded")

    # Resume the process
    print("Resuming process...")
    frida.resume(pid)
    print("Process running, monitoring unpacking...")
    print()

    # Wait for unpacking to complete or timeout
    try:
        for i in range(60):  # 30 seconds max
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nInterrupted by user")

    print("\n=== Cleaning up ===")
    try:
        session.detach()
    except:
        pass

    # List dumped files
    print(f"\n=== Files in {OUTPUT_DIR}/ ===")
    if os.path.exists(OUTPUT_DIR):
        for f in sorted(os.listdir(OUTPUT_DIR)):
            fp = os.path.join(OUTPUT_DIR, f)
            size = os.path.getsize(fp)
            print(f"  {f}: {size} bytes ({size/1024:.1f} KB)")

    print("\nDone.")

if __name__ == '__main__':
    main()
