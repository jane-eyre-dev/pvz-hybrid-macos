#!/usr/bin/env python3
"""Convert an IL-only PE32+ (x64-targeted, Machine=AMD64) .NET assembly into a PE32 AnyCPU one.

CoreCLR only treats an assembly as processor-agnostic (MSIL) when it is PE32 + Machine=I386 + ILONLY
(see AssemblyBinderCommon::TranslatePEToArchitectureType). An x64-targeted IL-only assembly is rejected
on arm64. Only headers change; section data (IL, metadata, resources) is untouched.

Usage: pe_anycpu.py <in.dll> <out.dll>
"""
import struct
import sys

IMAGE_FILE_MACHINE_I386 = 0x14C
IMAGE_FILE_MACHINE_AMD64 = 0x8664
COMIMAGE_FLAGS_ILONLY = 0x1
COMIMAGE_FLAGS_32BITREQUIRED = 0x2
COMIMAGE_FLAGS_32BITPREFERRED = 0x20000
DIR_IMPORT, DIR_BASERELOC, DIR_COMHEADER = 1, 5, 14


def main(src, dst):
    b = bytearray(open(src, "rb").read())
    pe = struct.unpack_from("<I", b, 0x3C)[0]
    assert b[pe:pe + 4] == b"PE\0\0", "not a PE file"
    machine, nsec = struct.unpack_from("<HH", b, pe + 4)
    optsz = struct.unpack_from("<H", b, pe + 20)[0]
    opt = pe + 24
    magic = struct.unpack_from("<H", b, opt)[0]
    if magic == 0x10B and machine == IMAGE_FILE_MACHINE_I386:
        open(dst, "wb").write(b)
        print(f"{src}: already PE32/I386 (AnyCPU), copied unchanged")
        return
    assert magic == 0x20B and optsz == 240, f"expected PE32+ (magic={magic:#x}, optsz={optsz})"
    assert machine == IMAGE_FILE_MACHINE_AMD64, f"unexpected machine {machine:#x}"

    entry = struct.unpack_from("<I", b, opt + 16)[0]
    nrva = struct.unpack_from("<I", b, opt + 108)[0]
    dirs = [struct.unpack_from("<II", b, opt + 112 + 8 * i) for i in range(nrva)]
    # Must be a "clean" IL-only image: no native entry stub, imports or relocations to fix up.
    assert entry == 0, "has native entry point"
    assert dirs[DIR_IMPORT] == (0, 0) and dirs[DIR_BASERELOC] == (0, 0), "has imports/relocations"

    # Locate CLR header and check flags.
    sec_off = opt + optsz
    secs = [struct.unpack_from("<8sIIII", b, sec_off + 40 * i) for i in range(nsec)]

    def rva2off(rva):
        for _, vs, va, rs, ro in secs:
            if va <= rva < va + max(vs, rs):
                return rva - va + ro
        raise ValueError(hex(rva))

    cor = rva2off(dirs[DIR_COMHEADER][0])
    flags = struct.unpack_from("<I", b, cor + 16)[0]
    assert flags & COMIMAGE_FLAGS_ILONLY, "not IL-only"
    assert not flags & (COMIMAGE_FLAGS_32BITREQUIRED | COMIMAGE_FLAGS_32BITPREFERRED)

    # Build PE32 optional header (224 bytes) from the PE32+ one (240 bytes).
    o = bytes(b[opt:opt + 240])
    n = bytearray(224)
    n[0:2] = struct.pack("<H", 0x10B)
    n[2:24] = o[2:24]                                  # linker ver, sizes, entry, BaseOfCode
    n[24:28] = struct.pack("<I", 0)                    # BaseOfData (unused by CLR)
    n[28:32] = struct.pack("<I", 0x10000000)           # ImageBase (Roslyn AnyCPU dll default)
    n[32:72] = o[32:72]                                # alignments .. DllCharacteristics
    for i, src_off in enumerate((72, 80, 88, 96)):     # stack/heap reserve/commit: 64 -> 32 bit
        v = struct.unpack_from("<Q", o, src_off)[0]
        n[72 + 4 * i:76 + 4 * i] = struct.pack("<I", min(v, 0xFFFFFFFF))
    n[88:96] = o[104:112]                              # LoaderFlags, NumberOfRvaAndSizes
    n[96:224] = o[112:240]                             # data directories

    sec_table = bytes(b[sec_off:sec_off + 40 * nsec])
    first_raw = min(s[4] for s in secs if s[3])
    assert opt + 224 + len(sec_table) <= first_raw

    struct.pack_into("<H", b, pe + 4, IMAGE_FILE_MACHINE_I386)
    struct.pack_into("<H", b, pe + 20, 224)
    b[opt:opt + 224] = n
    b[opt + 224:opt + 224 + len(sec_table)] = sec_table
    b[opt + 224 + len(sec_table):sec_off + len(sec_table)] = bytes(16)  # clear the 16 freed bytes
    struct.pack_into("<I", b, opt + 64, 0)             # CheckSum (not validated for DLLs)
    open(dst, "wb").write(b)
    print(f"{src} -> {dst}: PE32+/AMD64 -> PE32/I386 (AnyCPU), {len(b)} bytes")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
