"""
Static MEW unpacker for 飞飞智能助手4.8991.exe
Uses the XOR+ADD decryption found in the MEW stub:
  for each DWORD: val = (val ^ key1) + key2
Keys: key1=0x772e32ec, key2=0x7d551564
Count: 0x1000 DWORDs (4096 bytes from image base)
"""
import sys
import io
import struct
import os

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

EXE_PATH = r'D:\下载\飞飞智能助手4.8991\飞飞智能助手4.8991\飞飞智能助手4.8991.exe'
OUTPUT_DIR = 'static_unpack'
os.makedirs(OUTPUT_DIR, exist_ok=True)

with open(EXE_PATH, 'rb') as f:
    data = bytearray(f.read())

print(f"File size: {len(data)} bytes")

# Parse PE header
pe_off = struct.unpack_from('<I', data, 0x3C)[0]
num_sections = struct.unpack_from('<H', data, pe_off + 6)[0]
opt_hdr_size = struct.unpack_from('<H', data, pe_off + 20)[0]
image_base = struct.unpack_from('<I', data, pe_off + 0x34)[0]
entry_point = struct.unpack_from('<I', data, pe_off + 0x28)[0]

print(f"PE offset: 0x{pe_off:X}")
print(f"Image base: 0x{image_base:X}")
print(f"Entry point RVA: 0x{entry_point:X}")
print(f"Sections: {num_sections}")

# Parse sections
sections = []
sec_start = pe_off + 24 + opt_hdr_size
for i in range(num_sections):
    off = sec_start + i * 40
    name = data[off:off+8].rstrip(b'\x00').decode('ascii', errors='replace')
    vsize = struct.unpack_from('<I', data, off + 8)[0]
    rva = struct.unpack_from('<I', data, off + 12)[0]
    rawsize = struct.unpack_from('<I', data, off + 16)[0]
    rawptr = struct.unpack_from('<I', data, off + 20)[0]
    chars = struct.unpack_from('<I', data, off + 36)[0]
    sections.append({
        'name': name, 'rva': rva, 'vsize': vsize,
        'rawptr': rawptr, 'rawsize': rawsize, 'chars': chars
    })
    print(f"  [{i}] '{name}' RVA=0x{rva:X} VSize=0x{vsize:X} Raw=0x{rawptr:X}+0x{rawsize:X} Chars=0x{chars:X}")

# The MEW stub decrypts 0x1000 DWORDs from image base
# In the file, image base corresponds to offset 0 (the PE header starts there)
# So it decrypts file offset 0 through 0x3FFF (first 16KB)
key1 = 0x772E32EC
key2 = 0x7D551564
count = 0x1000  # 4096 DWORDs = 16384 bytes

print(f"\n=== Attempting decryption ===")
print(f"Key1: 0x{key1:08X}")
print(f"Key2: 0x{key2:08X}")
print(f"Count: {count} DWORDs ({count*4} bytes)")

# Decrypt first 0x1000 DWORDs of the file (image base)
decrypted = bytearray(data)
for i in range(count):
    off = i * 4
    val = struct.unpack_from('<I', decrypted, off)[0]
    val = (val ^ key1) & 0xFFFFFFFF
    val = (val + key2) & 0xFFFFFFFF
    struct.pack_into('<I', decrypted, off, val)

# Save decrypted file
out_path = os.path.join(OUTPUT_DIR, 'decrypted_header.exe')
with open(out_path, 'wb') as f:
    f.write(decrypted)
print(f"Saved decrypted file: {out_path} ({len(decrypted)} bytes)")

# Check if the decryption produced valid code at the entry point
ep_file_off = entry_point  # For the MEW stub section
print(f"\n=== Entry point analysis ===")
print(f"EP file offset: 0x{ep_file_off:X}")
if ep_file_off < len(decrypted):
    ep_bytes = decrypted[ep_file_off:ep_file_off+32]
    print(f"EP bytes: {ep_bytes.hex()}")

# Check PE header after decryption
print(f"\n=== PE header after decryption ===")
print(f"First 64 bytes: {decrypted[:64].hex()}")
mz = decrypted[0:2]
print(f"MZ signature: {mz}")

# Also try: maybe the decryption is meant to be applied to section data
# Section 0 (code) starts at file offset 0x1000, RVA 0x1000
# Let's try decrypting section 0 data
print(f"\n=== Trying section-specific decryption ===")
for sec in sections:
    rawptr = sec['rawptr']
    rawsize = sec['rawsize']
    if rawsize == 0 or rawsize > 0x100000:
        continue

    sec_data = bytearray(data[rawptr:rawptr+rawsize])
    # Decrypt with same keys
    n_dwords = len(sec_data) // 4
    for i in range(n_dwords):
        off = i * 4
        val = struct.unpack_from('<I', sec_data, off)[0]
        val = (val ^ key1) & 0xFFFFFFFF
        val = (val + key2) & 0xFFFFFFFF
        struct.pack_into('<I', sec_data, off, val)

    out_sec = os.path.join(OUTPUT_DIR, f"sec_{sec['name']}_decrypted.bin")
    with open(out_sec, 'wb') as f:
        f.write(sec_data)

    # Check for code patterns
    has_push_ebp = b'\x55\x8B\xEC' in sec_data[:256]
    has_ret = b'\xC3' in sec_data[:256]
    printable = sum(1 for b in sec_data[:256] if 0x20 <= b <= 0x7E)

    print(f"  Section '{sec['name']}' ({rawsize} bytes):")
    print(f"    First 32: {sec_data[:32].hex()}")
    print(f"    push ebp: {has_push_ebp}, ret: {has_ret}, printable: {printable}/256")

# Try the overlay data (everything after the PE)
overlay_start = 0x2D3400  # After last section raw data
overlay = data[overlay_start:]
print(f"\n=== Overlay analysis ===")
print(f"Overlay starts at: 0x{overlay_start:X}")
print(f"Overlay size: {len(overlay)} bytes ({len(overlay)/1024/1024:.1f} MB)")
print(f"First 64 bytes: {overlay[:64].hex()}")

# Check for PE signatures in overlay
pe_sigs = []
for i in range(len(overlay) - 4):
    if overlay[i:i+2] == b'MZ':
        pe_sigs.append(i + overlay_start)
    if len(pe_sigs) > 10:
        break

if pe_sigs:
    print(f"PE signatures found in overlay at offsets: {[f'0x{x:X}' for x in pe_sigs]}")
else:
    print("No PE signatures in first part of overlay")

# Try decrypting overlay data
print(f"\n=== Trying overlay decryption ===")
ov_dec = bytearray(overlay[:min(len(overlay), 0x100000)])  # First 1MB
for i in range(len(ov_dec) // 4):
    off = i * 4
    val = struct.unpack_from('<I', ov_dec, off)[0]
    val = (val ^ key1) & 0xFFFFFFFF
    val = (val + key2) & 0xFFFFFFFF
    struct.pack_into('<I', ov_dec, off, val)

print(f"Decrypted overlay first 64: {ov_dec[:64].hex()}")
# Check for MZ
if ov_dec[:2] == b'MZ':
    print("*** OVERLAY DECRYPTS TO PE! ***")
    out_ov = os.path.join(OUTPUT_DIR, 'overlay_decrypted.exe')
    with open(out_ov, 'wb') as f:
        f.write(ov_dec)
    print(f"Saved: {out_ov}")

# Also try reverse order: ADD first, then XOR (decryption instead of encryption)
print(f"\n=== Trying reverse decryption (ADD then XOR) ===")
ov_dec2 = bytearray(overlay[:min(len(overlay), 0x100000)])
for i in range(len(ov_dec2) // 4):
    off = i * 4
    val = struct.unpack_from('<I', ov_dec2, off)[0]
    val = (val + key2) & 0xFFFFFFFF
    val = (val ^ key1) & 0xFFFFFFFF
    struct.pack_into('<I', ov_dec2, off, val)

print(f"Reverse decrypted overlay first 64: {ov_dec2[:64].hex()}")
if ov_dec2[:2] == b'MZ':
    print("*** REVERSE DECRYPTION PRODUCES PE! ***")
    out_ov2 = os.path.join(OUTPUT_DIR, 'overlay_reverse_decrypted.exe')
    with open(out_ov2, 'wb') as f:
        f.write(ov_dec2)

# Check if any decrypted data has high entropy reduction
import math
def entropy(data):
    if not data:
        return 0
    freq = [0] * 256
    for b in data:
        freq[b] += 1
    ent = 0
    for f in freq:
        if f > 0:
            p = f / len(data)
            ent -= p * math.log2(p)
    return ent

print(f"\n=== Entropy analysis ===")
print(f"Original overlay: {entropy(overlay[:0x10000]):.2f} bits/byte")
print(f"XOR+ADD decrypted: {entropy(ov_dec[:0x10000]):.2f} bits/byte")
print(f"ADD+XOR decrypted: {entropy(ov_dec2[:0x10000]):.2f} bits/byte")
print(f"Section 0 (code): {entropy(data[0x1000:0x11000]):.2f} bits/byte")
print(f"Section 4 (packed): {entropy(data[0x159A00:0x169A00]):.2f} bits/byte")

print("\nDone.")
