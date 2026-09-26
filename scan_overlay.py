"""
Deep scan overlay for embedded PE files.
Check MZ signatures for validity by parsing PE headers.
"""
import sys
import io
import struct
import os

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

EXE_PATH = r'D:\下载\飞飞智能助手4.8991\飞飞智能助手4.8991\飞飞智能助手4.8991.exe'

with open(EXE_PATH, 'rb') as f:
    data = f.read()

overlay_start = 0x2D3400
overlay = data[overlay_start:]

print(f"File size: {len(data)}")
print(f"Overlay start: 0x{overlay_start:X}")
print(f"Overlay size: {len(overlay)} ({len(overlay)/1024/1024:.1f} MB)")

# Parse the overlay header
print(f"\n=== Overlay header ===")
print(f"First 4 bytes as uint32: 0x{struct.unpack_from('<I', overlay, 0)[0]:X}")
print(f"Bytes 0-32: {overlay[:32].hex()}")

# Find ALL MZ signatures in overlay
mz_positions = []
pos = 0
while pos < len(overlay) - 2:
    idx = overlay.find(b'MZ', pos)
    if idx == -1:
        break
    mz_positions.append(idx)
    pos = idx + 1

print(f"\n=== Found {len(mz_positions)} MZ signatures in overlay ===")

# For each MZ, try to parse as PE
valid_pes = []
for mz_off in mz_positions:
    abs_off = mz_off + overlay_start
    try:
        if mz_off + 0x40 > len(overlay):
            continue

        # Check MZ header
        e_lfanew = struct.unpack_from('<I', overlay, mz_off + 0x3C)[0]

        # Sanity check e_lfanew
        if e_lfanew > 0x1000 or e_lfanew < 0x40:
            continue

        pe_off = mz_off + e_lfanew
        if pe_off + 0x100 > len(overlay):
            continue

        # Check PE signature
        pe_sig = overlay[pe_off:pe_off+4]
        if pe_sig != b'PE\x00\x00':
            continue

        # Parse COFF header
        machine = struct.unpack_from('<H', overlay, pe_off + 4)[0]
        num_sections = struct.unpack_from('<H', overlay, pe_off + 6)[0]
        opt_hdr_size = struct.unpack_from('<H', overlay, pe_off + 20)[0]

        if machine not in (0x14C, 0x8664, 0x0):  # i386, x64, or unknown
            continue
        if num_sections > 20 or num_sections == 0:
            continue
        if opt_hdr_size > 0x1000:
            continue

        # Parse optional header
        opt_off = pe_off + 24
        magic = struct.unpack_from('<H', overlay, opt_off)[0]
        if magic not in (0x10B, 0x20B):  # PE32, PE32+
            continue

        if magic == 0x10B:  # PE32
            ep_rva = struct.unpack_from('<I', overlay, opt_off + 0x10)[0]
            img_base = struct.unpack_from('<I', overlay, opt_off + 0x1C)[0]
        else:
            ep_rva = struct.unpack_from('<I', overlay, opt_off + 0x10)[0]
            img_base = struct.unpack_from('<Q', overlay, opt_off + 0x18)[0]

        # Parse sections
        sec_start = pe_off + 24 + opt_hdr_size
        sections_ok = True
        total_raw = 0
        for s in range(num_sections):
            s_off = sec_start + s * 40
            if s_off + 40 > len(overlay):
                sections_ok = False
                break
            s_rva = struct.unpack_from('<I', overlay, s_off + 12)[0]
            s_rawsize = struct.unpack_from('<I', overlay, s_off + 16)[0]
            s_rawptr = struct.unpack_from('<I', overlay, s_off + 20)[0]
            total_raw += s_rawsize
            if s_rawptr > 0x10000000:  # unreasonable
                sections_ok = False
                break

        if not sections_ok:
            continue

        valid_pes.append({
            'overlay_off': mz_off,
            'abs_off': abs_off,
            'e_lfanew': e_lfanew,
            'machine': machine,
            'num_sections': num_sections,
            'ep_rva': ep_rva,
            'img_base': img_base,
            'total_raw': total_raw
        })

    except Exception as e:
        continue

print(f"Valid PE headers found: {len(valid_pes)}")
for p in valid_pes:
    print(f"\n  Overlay offset: 0x{p['overlay_off']:X} (absolute: 0x{p['abs_off']:X})")
    print(f"  e_lfanew: 0x{p['e_lfanew']:X}")
    print(f"  Machine: 0x{p['machine']:X}")
    print(f"  Sections: {p['num_sections']}")
    print(f"  EP RVA: 0x{p['ep_rva']:X}")
    print(f"  Image base: 0x{p['img_base']:X}")
    print(f"  Total raw size: 0x{p['total_raw']:X} ({p['total_raw']/1024:.0f} KB)")

# Also scan the ENTIRE file for MZ+PE signatures
print(f"\n\n=== Full file PE scan ===")
all_pes = []
pos = 0
while pos < len(data) - 4:
    idx = data.find(b'MZ', pos)
    if idx == -1:
        break
    pos = idx + 1

    try:
        if idx + 0x40 > len(data):
            continue
        e_lfanew = struct.unpack_from('<I', data, idx + 0x3C)[0]
        if e_lfanew > 0x1000 or e_lfanew < 0x40:
            continue
        pe_off = idx + e_lfanew
        if pe_off + 4 > len(data):
            continue
        if data[pe_off:pe_off+4] != b'PE\x00\x00':
            continue
        machine = struct.unpack_from('<H', data, pe_off + 4)[0]
        num_sec = struct.unpack_from('<H', data, pe_off + 6)[0]
        if machine != 0x14C or num_sec == 0 or num_sec > 20:
            continue
        all_pes.append((idx, e_lfanew, machine, num_sec))
    except:
        continue

print(f"Total valid PE32 headers in file: {len(all_pes)}")
for off, elf, mach, nsec in all_pes:
    print(f"  Offset 0x{off:X} (e_lfanew=0x{elf:X}, sections={nsec})")

# Extract the overlay structure
# First 4 bytes might be a size field
ov_size_field = struct.unpack_from('<I', overlay, 0)[0]
print(f"\n=== Overlay structure ===")
print(f"First uint32: 0x{ov_size_field:X} ({ov_size_field})")

# Check if it's a size of the authenticode signature
# The ASN.1 structure starts at offset 4
if overlay[4:5] == b'\x30':  # SEQUENCE
    # Parse ASN.1 length
    asn1_len_byte = overlay[5]
    if asn1_len_byte & 0x80:
        num_len_bytes = asn1_len_byte & 0x7F
        asn1_content_len = int.from_bytes(overlay[6:6+num_len_bytes], 'big')
        total_asn1 = 6 + num_len_bytes + asn1_content_len
        print(f"ASN.1 SEQUENCE: content length = {asn1_content_len}")
        print(f"Total ASN.1 structure: {total_asn1} bytes ({total_asn1/1024:.0f} KB)")
        print(f"Data after ASN.1 at offset: 0x{4 + total_asn1:X}")

        # Check what's after the ASN.1 structure
        after_asn1 = 4 + total_asn1
        if after_asn1 < len(overlay):
            remaining = overlay[after_asn1:]
            print(f"Remaining overlay after ASN.1: {len(remaining)} bytes ({len(remaining)/1024/1024:.1f} MB)")
            print(f"First 64 bytes after ASN.1: {remaining[:64].hex()}")

            # Check for MZ in remaining data
            mz_after = remaining.find(b'MZ')
            if mz_after >= 0:
                print(f"MZ found at offset {mz_after} within remaining data")
            else:
                print("No MZ in remaining data")

            # Check entropy of remaining data
            import math
            sample = remaining[:0x10000]
            freq = [0] * 256
            for b in sample:
                freq[b] += 1
            ent = 0
            for f in freq:
                if f > 0:
                    p = f / len(sample)
                    ent -= p * math.log2(p)
            print(f"Entropy of remaining data: {ent:.2f} bits/byte")

print("\nDone.")
