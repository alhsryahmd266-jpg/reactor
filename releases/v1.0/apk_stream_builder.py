#!/usr/bin/env python3
"""
ARM APK Stream Builder - Maximum Power Edition
محرك بناء مستقل ومحصن - Disk Streaming الكامل
"""
import os, sys, struct, hashlib, zlib, subprocess, shutil
import zipfile, base64, datetime, time, json, sqlite3, tempfile
from pathlib import Path

# ══════════════════════════════════════════════════════
# CONSTANTS
# ══════════════════════════════════════════════════════
NDK       = "/opt/arm/ndk/android-ndk-r25c"
TOOLCHAIN = f"{NDK}/toolchains/llvm/prebuilt/linux-x86_64/bin"
ANDROID_JAR = "/usr/lib/android-sdk/platforms/android-23/android.jar"
DX          = "/usr/lib/android-sdk/build-tools/debian/dx"
AAPT        = "/usr/bin/aapt"
ECJ         = "/usr/bin/ecj"
APKSIGNER   = "/usr/bin/apksigner"
ARM_KS      = "/opt/arm/keystore/arm.jks"
KS_PASS     = "ARMpass2025"
KS_ALIAS    = "arm"
BUILDS      = "/opt/arm/builds"
CHUNK       = 4 * 1024 * 1024
os.makedirs(BUILDS, exist_ok=True)

# ══════════════════════════════════════════════════════
# 1. STREAMING ZIP - صفر RAM
# ══════════════════════════════════════════════════════
class StreamZip:
    def __init__(self, path):
        self.path = str(path)
        self.entries = []
        self.pos = 0
        self.fd = open(self.path, 'wb')

    def _w(self, b): self.fd.write(b); self.pos += len(b)

    def add(self, arcname, src, store=False):
        src = str(src)
        fsize = os.path.getsize(src)
        nb = arcname.encode('utf-8')
        method = 0 if store else 8
        hdr = self.pos
        # Local header
        self._w(b'PK\x03\x04')
        self._w(struct.pack('<H', 20))       # version
        self._w(struct.pack('<H', 0x0008))   # flags: data descriptor
        self._w(struct.pack('<H', method))
        self._w(struct.pack('<H', 0))        # mod time
        self._w(struct.pack('<H', 0))        # mod date
        self._w(struct.pack('<I', 0))        # crc placeholder
        self._w(struct.pack('<I', 0))        # csz placeholder
        self._w(struct.pack('<I', fsize & 0xFFFFFFFF))
        self._w(struct.pack('<H', len(nb)))
        self._w(struct.pack('<H', 0))
        self._w(nb)
        # Stream data
        crc = 0; csz = 0
        comp = None if store else zlib.compressobj(6, zlib.DEFLATED, -15)
        with open(src, 'rb') as f:
            while True:
                chunk = f.read(CHUNK)
                if not chunk: break
                crc = zlib.crc32(chunk, crc) & 0xFFFFFFFF
                out = chunk if store else comp.compress(chunk)
                if out: self._w(out); csz += len(out)
        if not store:
            tail = comp.flush()
            if tail: self._w(tail); csz += len(tail)
        # Data descriptor
        self._w(b'PK\x07\x08')
        self._w(struct.pack('<I', crc))
        self._w(struct.pack('<I', csz))
        self._w(struct.pack('<I', fsize & 0xFFFFFFFF))
        # Patch local header
        save = self.pos
        self.fd.seek(hdr + 14)
        self.fd.write(struct.pack('<III', crc, csz, fsize & 0xFFFFFFFF))
        self.fd.seek(save); self.pos = save
        self.entries.append(dict(nb=nb, off=hdr, crc=crc,
                                  sz=fsize, csz=csz, m=method))
        mb = fsize / 1024 / 1024
        print(f"    + {arcname}: {fsize:,}B" + (f" [{mb:.0f}MB]" if mb >= 1 else ""))

    def add_bytes(self, arcname, data):
        t = tempfile.mktemp(); open(t,'wb').write(data)
        self.add(arcname, t); os.unlink(t)

    def close(self):
        cd = self.pos
        for e in self.entries:
            nb = e['nb']
            self._w(b'PK\x01\x02')
            self._w(struct.pack('<H', 20))
            self._w(struct.pack('<H', 20))
            self._w(struct.pack('<H', 0x0008))
            self._w(struct.pack('<H', e['m']))
            self._w(struct.pack('<H', 0))
            self._w(struct.pack('<H', 0))
            self._w(struct.pack('<I', e['crc']))
            self._w(struct.pack('<I', e['csz']))
            self._w(struct.pack('<I', e['sz'] & 0xFFFFFFFF))
            self._w(struct.pack('<H', len(nb)))
            self._w(struct.pack('<H', 0))
            self._w(struct.pack('<H', 0))
            self._w(struct.pack('<H', 0))
            self._w(struct.pack('<H', 0))
            self._w(struct.pack('<I', 0))
            self._w(struct.pack('<I', e['off']))
            self._w(nb)
        cdsz = self.pos - cd
        self._w(b'PK\x05\x06')
        self._w(struct.pack('<H', 0))
        self._w(struct.pack('<H', 0))
        self._w(struct.pack('<H', len(self.entries)))
        self._w(struct.pack('<H', len(self.entries)))
        self._w(struct.pack('<I', cdsz))
        self._w(struct.pack('<I', cd))
        self._w(struct.pack('<H', 0))
        self.fd.close()
        sz = os.path.getsize(self.path)
        print(f"  [ZIP] {sz:,}B ({sz/1024/1024:.2f}MB)")
        return self.path

# ══════════════════════════════════════════════════════
# 2. RESOURCES.ARSC BUILDER - ديناميكي كامل
# ══════════════════════════════════════════════════════
class ARSCBuilder:
    """يبني resources.arsc من الصفر بدون aapt"""

    def __init__(self, package_name, app_name):
        self.pkg = package_name
        self.app_name = app_name

    def _u16(self, v): return struct.pack('<H', v)
    def _u32(self, v): return struct.pack('<I', v)

    def _encode_str16(self, s):
        """UTF-16LE string مع length prefix"""
        enc = s.encode('utf-16-le')
        return struct.pack('<H', len(s)) + enc + b'\x00\x00'

    def _string_pool(self, strings):
        """يبني String Pool chunk"""
        # حساب offsets
        offsets = []
        data = b''
        for s in strings:
            offsets.append(len(data))
            data += self._encode_str16(s)
        # Pad to 4-byte alignment
        while len(data) % 4 != 0:
            data += b'\x00'
        # Header
        strings_start = 7 * 4 + len(offsets) * 4  # relative to chunk start
        chunk_size = 8 + 5 * 4 + len(offsets) * 4 + len(data)
        header = b''
        header += self._u16(0x0001)              # type: STRING_POOL
        header += self._u16(28)                  # header size
        header += self._u32(chunk_size)          # chunk size
        header += self._u32(len(strings))        # string count
        header += self._u32(0)                   # style count
        header += self._u32(0)                   # flags (UTF-16)
        header += self._u32(28 + len(offsets)*4) # strings start
        header += self._u32(0)                   # styles start
        for off in offsets:
            header += self._u32(off)
        return header + data

    def build(self, output_path):
        """يبني resources.arsc كامل"""
        pkg_id = 0x7F

        # الـ strings نحتاجها
        type_strings = ["string"]
        key_strings  = ["app_name"]
        values       = [self.app_name]
        pkg_name_str = self.pkg

        # String pool للـ types
        type_pool_data  = self._string_pool(type_strings)
        key_pool_data   = self._string_pool(key_strings)
        global_pool_data = self._string_pool([])

        # TYPE_SPEC chunk
        type_spec = b''
        type_spec += self._u16(0x0202)  # TYPE_SPEC
        type_spec += self._u16(8)       # header size
        type_spec += self._u32(8 + 4)   # chunk size (1 entry)
        type_spec += bytes([1])         # id (string=1)
        type_spec += bytes([0])         # res0
        type_spec += self._u16(0)       # res1
        type_spec += self._u32(1)       # entry count
        type_spec += self._u32(0)       # flags

        # TYPE chunk (1 string entry)
        entry_data = b''
        entry_data += self._u16(8)      # size
        entry_data += self._u16(0)      # flags
        entry_data += self._u32(0)      # key index (app_name=0)
        # Value
        entry_data += self._u16(8)      # value size
        entry_data += bytes([0])        # res0
        entry_data += bytes([0x03])     # dataType STRING
        entry_data += self._u32(0)      # data (string index 0)

        val_strings = [self.app_name]
        val_pool = self._string_pool(val_strings)

        entries_offset = 52 + len(val_pool)
        type_chunk = b''
        type_chunk += self._u16(0x0201)  # TYPE
        type_chunk += self._u16(52)      # header size
        type_chunk += self._u32(52 + len(val_pool) + len(entry_data))
        type_chunk += bytes([1])         # id
        type_chunk += bytes([0])
        type_chunk += self._u16(0)
        type_chunk += self._u32(1)       # entryCount
        type_chunk += self._u32(52 + len(val_pool))  # entriesStart
        # Config (32 bytes - default)
        type_chunk += self._u32(32)      # config size
        type_chunk += b'\x00' * 28      # all defaults
        type_chunk += val_pool
        # Offsets table (1 entry, offset=0)
        type_chunk += self._u32(0)
        type_chunk += entry_data

        # Package chunk
        pkg_data = type_pool_data + key_pool_data + type_spec + type_chunk
        pkg_chunk = b''
        pkg_chunk += self._u16(0x0200)   # PACKAGE
        pkg_chunk += self._u16(288)      # header size
        pkg_chunk += self._u32(288 + len(pkg_data))
        pkg_chunk += self._u32(pkg_id)   # id
        # package name (256 bytes = 128 UTF-16 chars)
        name_enc = self.pkg.encode('utf-16-le')
        name_enc = name_enc[:254] + b'\x00\x00'
        name_enc = name_enc.ljust(256, b'\x00')
        pkg_chunk += name_enc
        pkg_chunk += self._u32(288)      # typeStrings offset
        pkg_chunk += self._u32(len(type_strings))
        pkg_chunk += self._u32(288 + len(type_pool_data))  # keyStrings offset
        pkg_chunk += self._u32(0)
        pkg_chunk += pkg_data

        # Table header
        table_chunk = global_pool_data + pkg_chunk
        table = b''
        table += self._u16(0x0002)   # TABLE
        table += self._u16(12)       # header size
        table += self._u32(12 + len(table_chunk))
        table += self._u32(1)        # package count
        table += table_chunk

        open(str(output_path), 'wb').write(table)
        print(f"  [ARSC] {os.path.getsize(output_path):,}B")
        return output_path

# ══════════════════════════════════════════════════════
# 3. AXML BUILDER - AndroidManifest.xml binary
# ══════════════════════════════════════════════════════
class AXMLBuilder:
    """يبني AndroidManifest.xml binary بدون aapt"""

    def __init__(self):
        self.strings = []
        self.ns_uri = "http://schemas.android.com/apk/res/android"
        self.ns_prefix = "android"

    def _add_str(self, s):
        if s not in self.strings:
            self.strings.append(s)
        return self.strings.index(s)

    def _u16(self, v): return struct.pack('<H', v)
    def _u32(self, v): return struct.pack('<I', v)

    def _string_pool(self):
        data = b''
        offsets = []
        for s in self.strings:
            offsets.append(len(data))
            enc = s.encode('utf-16-le')
            data += struct.pack('<H', len(s)) + enc + b'\x00\x00'
        while len(data) % 4 != 0:
            data += b'\x00'
        pool_start = 28 + len(offsets) * 4
        chunk_size = 28 + len(offsets) * 4 + len(data)
        header = b''
        header += self._u16(0x0001)           # STRING_POOL
        header += self._u16(28)               # header size
        header += self._u32(chunk_size)
        header += self._u32(len(self.strings))
        header += self._u32(0)               # style count
        header += self._u32(0)               # flags
        header += self._u32(pool_start)      # strings start
        header += self._u32(0)               # styles start
        for off in offsets:
            header += self._u32(off)
        return header + data

    def _res_map(self, attrs):
        data = self._u32(len(attrs) * 4 + 8)
        chunk = self._u16(0x0180) + self._u16(8) + data
        for attr_id in attrs:
            chunk += self._u32(attr_id)
        return chunk

    def _start_ns(self):
        chunk  = self._u16(0x0100) + self._u16(16) + self._u32(24)
        chunk += self._u32(0) + self._u32(0)  # line, comment
        chunk += self._u32(self._add_str(self.ns_prefix))
        chunk += self._u32(self._add_str(self.ns_uri))
        return chunk

    def _end_ns(self):
        chunk  = self._u16(0x0101) + self._u16(16) + self._u32(24)
        chunk += self._u32(0) + self._u32(0)
        chunk += self._u32(self._add_str(self.ns_prefix))
        chunk += self._u32(self._add_str(self.ns_uri))
        return chunk

    def _start_elem(self, tag, attrs):
        ns_idx = self._add_str(self.ns_uri)
        tag_idx = self._add_str(tag)
        attr_data = b''
        for ns, name, val_str, val_type, val_data in attrs:
            ns_i  = self._add_str(ns) if ns else 0xFFFFFFFF
            nm_i  = self._add_str(name)
            raw_i = self._add_str(val_str) if val_str else 0xFFFFFFFF
            attr_data += self._u32(ns_i)
            attr_data += self._u32(nm_i)
            attr_data += self._u32(raw_i)
            attr_data += self._u16(8)       # value size
            attr_data += bytes([0])         # res0
            attr_data += bytes([val_type])  # data type
            attr_data += self._u32(val_data)
        attr_count = len(attrs)
        size = 16 + 20 + attr_count * 20
        chunk  = self._u16(0x0102) + self._u16(16) + self._u32(size)
        chunk += self._u32(0) + self._u32(0)  # line, comment
        chunk += self._u32(0xFFFFFFFF)         # ns
        chunk += self._u32(tag_idx)
        chunk += self._u16(0x0014)  # attr start
        chunk += self._u16(0x0014)  # attr size
        chunk += self._u16(attr_count)
        chunk += self._u16(0) + self._u16(0)  # id/class/style
        chunk += attr_data
        return chunk

    def _end_elem(self, tag):
        tag_idx = self._add_str(tag)
        chunk  = self._u16(0x0103) + self._u16(16) + self._u32(24)
        chunk += self._u32(0) + self._u32(0)
        chunk += self._u32(0xFFFFFFFF)
        chunk += self._u32(tag_idx)
        return chunk

    def build(self, pkg, activity, app_name,
              min_sdk=21, target_sdk=33, output_path=None):
        A = self.ns_uri

        def attr(name, val_str=None, val_type=0x03, val_data=0):
            return (A, name, val_str, val_type, val_data)

        # version_code=1 (0x10), version_name="1.0" (string)
        body = b''
        body += self._start_ns()
        # <manifest>
        body += self._start_elem("manifest", [
            (None,  "package",            pkg,   0x03, self._add_str(pkg)),
            (A,     "versionCode",        "1",   0x10, 1),
            (A,     "versionName",        "1.0", 0x03, self._add_str("1.0")),
        ])
        # <uses-sdk>
        body += self._start_elem("uses-sdk", [
            (A, "minSdkVersion",    str(min_sdk),    0x10, min_sdk),
            (A, "targetSdkVersion", str(target_sdk), 0x10, target_sdk),
        ])
        body += self._end_elem("uses-sdk")
        # <application>
        body += self._start_elem("application", [
            (A, "label",        app_name, 0x03, self._add_str(app_name)),
            (A, "allowBackup",  "true",   0x12, 0xFFFFFFFF),
        ])
        # <activity>
        body += self._start_elem("activity", [
            (A, "name",     f".{activity}", 0x03, self._add_str(f".{activity}")),
            (A, "exported", "true",         0x12, 0xFFFFFFFF),
        ])
        # <intent-filter>
        body += self._start_elem("intent-filter", [])
        # <action>
        body += self._start_elem("action", [
            (A, "name", "android.intent.action.MAIN", 0x03,
             self._add_str("android.intent.action.MAIN")),
        ])
        body += self._end_elem("action")
        # <category>
        body += self._start_elem("category", [
            (A, "name", "android.intent.category.LAUNCHER", 0x03,
             self._add_str("android.intent.category.LAUNCHER")),
        ])
        body += self._end_elem("category")
        body += self._end_elem("intent-filter")
        body += self._end_elem("activity")
        body += self._end_elem("application")
        body += self._end_elem("manifest")
        body += self._end_ns()

        pool = self._string_pool()
        total = 8 + len(pool) + len(body)
        header = self._u16(0x0003) + self._u16(8) + self._u32(total)
        result = header + pool + body

        if output_path:
            open(str(output_path), 'wb').write(result)
            print(f"  [AXML] {len(result):,}B")
        return result

# ══════════════════════════════════════════════════════
# 4. SELF-DEBUGGING JAVA COMPILER
# ══════════════════════════════════════════════════════
class JavaBuilder:
    def __init__(self, root):
        self.root = Path(root)
        for d in ['src','classes','dex','res/values','out','libs/arm64-v8a']:
            (self.root/d).mkdir(parents=True, exist_ok=True)

    def write_java(self, pkg, cls, code):
        d = self.root/"src"/Path(*pkg.split('.'))
        d.mkdir(parents=True, exist_ok=True)
        f = d/f"{cls}.java"
        f.write_text(code, encoding='utf-8')

    def compile(self):
        files = list((self.root/"src").rglob("*.java"))
        if not files: raise RuntimeError("No Java files")
        r = subprocess.run(
            [ECJ, "-cp", ANDROID_JAR,
             "-d", str(self.root/"classes"), "-nowarn"]
            + [str(f) for f in files],
            capture_output=True, text=True)
        cls = list((self.root/"classes").rglob("*.class"))
        if not cls:
            raise RuntimeError(f"ECJ failed:\n{r.stdout}\n{r.stderr}")
        print(f"  [ECJ] {len(cls)} classes")

    def to_dex(self):
        out = self.root/"dex"/"classes.dex"
        r = subprocess.run(
            [DX, "--dex", f"--output={out}", str(self.root/"classes")],
            capture_output=True, text=True)
        if not out.exists():
            raise RuntimeError(f"DX failed:\n{r.stderr}")
        print(f"  [DX] DEX: {out.stat().st_size:,}B")
        return out

    def build_res_with_aapt(self, pkg, app_name):
        """يحاول AAPT أولاً، لو فشل يستخدم ARSCBuilder"""
        manifest = self.root/"AndroidManifest.xml"
        res_apk  = self.root/"out"/"res.ap_"
        (self.root/"res/values/strings.xml").write_text(
            f'<?xml version="1.0" encoding="utf-8"?>'
            f'<resources><string name="app_name">{app_name}</string></resources>')

        r = subprocess.run([AAPT, "package", "-f",
            "-M", str(manifest), "-S", str(self.root/"res"),
            "-I", ANDROID_JAR, "-F", str(res_apk),
            "--auto-add-overlay"], capture_output=True, text=True)

        if res_apk.exists():
            print(f"  [AAPT] {res_apk.stat().st_size:,}B")
            return res_apk, None

        # Fallback: ARSCBuilder + AXMLBuilder
        print(f"  [AAPT] fallback to native builders")
        arsc_path = self.root/"out"/"resources.arsc"
        ARSCBuilder(pkg, app_name).build(str(arsc_path))
        return None, arsc_path

# ══════════════════════════════════════════════════════
# 5. NDK NATIVE COMPILER
# ══════════════════════════════════════════════════════
class NativeBuilder:
    CLANG = f"{TOOLCHAIN}/aarch64-linux-android21-clang"
    GPP   = f"{TOOLCHAIN}/aarch64-linux-android21-clang++"
    STRIP = f"{TOOLCHAIN}/llvm-strip"

    @classmethod
    def available(cls):
        return os.path.exists(cls.CLANG)

    @classmethod
    def build_so(cls, srcs, name, out_dir, lang='c',
                 includes=None, libs=None, cflags=None):
        out = Path(out_dir)/f"lib{name}.so"
        cc  = cls.GPP if lang in ('cpp','c++') else cls.CLANG
        cmd = [cc, "-O2", "-shared", "-fPIC"]
        if lang in ('cpp','c++'): cmd += ["-std=c++17"]
        if includes:
            for i in includes: cmd += ["-I", str(i)]
        if cflags: cmd += cflags
        cmd += [str(s) for s in srcs]
        cmd += ["-o", str(out), "-llog", "-landroid"]
        if libs:
            for l in libs: cmd += [f"-l{l}"]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"NDK failed:\n{r.stderr}")
        subprocess.run([cls.STRIP, str(out)], capture_output=True)
        print(f"  [NDK] lib{name}.so: {out.stat().st_size:,}B")
        return out

# ══════════════════════════════════════════════════════
# 6. SIGNER - apksigner رسمي v1+v2+v3
# ══════════════════════════════════════════════════════
class Signer:
    @staticmethod
    def ensure():
        if Path(ARM_KS).exists(): return
        # استرجاع من DB
        conn = sqlite3.connect('/opt/arm/db/arm.db')
        try:
            row = conn.execute(
                "SELECT value FROM config WHERE key='ks_b64'").fetchone()
            if row:
                Path(ARM_KS).parent.mkdir(parents=True, exist_ok=True)
                Path(ARM_KS).write_bytes(base64.b64decode(row[0]))
                return
        except: pass
        # توليد جديد
        subprocess.run(["keytool","-genkeypair",
            "-keystore", ARM_KS, "-alias", KS_ALIAS,
            "-keyalg","RSA","-keysize","2048","-validity","10000",
            "-dname","CN=ARM,O=ARM,C=EG",
            "-storepass", KS_PASS, "-keypass", KS_PASS,
            "-storetype","PKCS12"],
            capture_output=True)
        # حفظ في DB
        ks_b64 = base64.b64encode(Path(ARM_KS).read_bytes()).decode()
        conn.execute("CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT OR REPLACE INTO config VALUES ('ks_b64',?)", (ks_b64,))
        conn.commit()

    @staticmethod
    def sign(unsigned, signed):
        Signer.ensure()
        r = subprocess.run([APKSIGNER,"sign",
            "--ks", ARM_KS,
            "--ks-pass", f"pass:{KS_PASS}",
            "--key-pass", f"pass:{KS_PASS}",
            "--ks-key-alias", KS_ALIAS,
            "--out", str(signed),
            str(unsigned)], capture_output=True, text=True)
        if not Path(signed).exists():
            raise RuntimeError(f"Sign failed:\n{r.stderr}")
        v = subprocess.run([APKSIGNER,"verify","--verbose",str(signed)],
            capture_output=True, text=True)
        for ln in v.stdout.splitlines():
            if "v2" in ln or "v3" in ln:
                print(f"  [SIGN] {ln.strip()}")

# ══════════════════════════════════════════════════════
# 7. MAIN ENGINE
# ══════════════════════════════════════════════════════
class APKStreamBuilder:
    """
    المحرك الرئيسي - يبني APK بأي حجم streaming كامل
    
    config = {
        'app_name'   : str,
        'package'    : str,
        'activity'   : str,      # default: MainActivity
        'java_code'  : str,
        'output'     : str,
        'native_libs': [         # optional C/C++
            {'name':'lib','src':'/path/to/lib.c','lang':'c'}
        ],
        'extra_files': {         # optional large assets
            'assets/file.mp4': '/path/to/file.mp4'
        },
        'permissions': [],       # optional Android permissions
        'min_sdk'    : 21,       # optional
        'target_sdk' : 33,       # optional
    }
    """
    def build(self, config):
        name    = config['app_name']
        pkg     = config['package']
        act     = config.get('activity', 'MainActivity')
        output  = config['output']
        extras  = config.get('extra_files', {})
        natives = config.get('native_libs', [])
        perms   = config.get('permissions', [])
        min_sdk = config.get('min_sdk', 21)
        tgt_sdk = config.get('target_sdk', 33)

        bid = f"{pkg.replace('.','_')}_{int(time.time())}"
        bd  = Path(BUILDS) / bid

        print(f"\n{'═'*58}")
        print(f"  APK STREAM BUILDER")
        print(f"  App     : {name}")
        print(f"  Package : {pkg}")
        if natives: print(f"  Native  : {len(natives)} lib(s)")
        if extras:
            tot = sum(os.path.getsize(v) for v in extras.values()
                      if os.path.exists(v))
            print(f"  Assets  : {len(extras)} file(s), {tot/1024/1024:.0f}MB")
        print(f"{'═'*58}")
        t0 = time.time()

        try:
            jb = JavaBuilder(str(bd))

            # ── Manifest (XML text → AAPT binary) ──
            manifest_xml = self._make_manifest(
                pkg, act, name, perms, min_sdk, tgt_sdk)
            manifest_path = bd/"AndroidManifest.xml"
            manifest_path.write_text(manifest_xml, encoding='utf-8')

            # ── Java source ──
            jb.write_java(pkg, act, config['java_code'])
            jb.compile()
            dex = jb.to_dex()

            # ── Resources ──
            res_apk, arsc_path = jb.build_res_with_aapt(pkg, name)

            # ── Native libs ──
            jni_dir = bd/"libs"/"arm64-v8a"
            jni_dir.mkdir(parents=True, exist_ok=True)
            if natives and NativeBuilder.available():
                for nl in natives:
                    srcs = nl.get('srcs', [nl['src']])
                    NativeBuilder.build_so(srcs, nl['name'], str(jni_dir),
                        lang=nl.get('lang','c'),
                        includes=nl.get('includes'),
                        libs=nl.get('libs'))

            # ── Package streaming ──
            unsigned = bd/"unsigned.apk"
            w = StreamZip(str(unsigned))
            w.add("classes.dex", str(dex))

            if res_apk and res_apk.exists():
                tmp = bd/"res_tmp"; tmp.mkdir(exist_ok=True)
                with zipfile.ZipFile(str(res_apk),'r') as z:
                    for n in z.namelist():
                        if n.startswith('META-INF'): continue
                        dst = tmp/n.replace('/',os.sep)
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        with z.open(n) as src, open(str(dst),'wb') as dst_f:
                            while True:
                                c = src.read(CHUNK)
                                if not c: break
                                dst_f.write(c)
                        w.add(n, str(dst))
            elif arsc_path and arsc_path.exists():
                # استخدام AXML+ARSC المبنيين يدوياً
                axml = AXMLBuilder()
                axml_data = axml.build(pkg, act, name, min_sdk, tgt_sdk)
                axml_tmp = str(bd/"AndroidManifest.bin")
                open(axml_tmp,'wb').write(axml_data)
                w.add("AndroidManifest.xml", axml_tmp)
                w.add("resources.arsc", str(arsc_path))

            # .so files
            for so in jni_dir.glob("*.so"):
                w.add(f"lib/arm64-v8a/{so.name}", str(so))

            # Extra assets - streaming مباشر
            for arcname, fpath in extras.items():
                if os.path.exists(fpath):
                    w.add(arcname, fpath)

            w.close()

            # ── Sign ──
            Signer.sign(str(unsigned), output)

            shutil.rmtree(str(bd))

            elapsed = time.time() - t0
            size = os.path.getsize(output)

            # حفظ في DB
            conn = sqlite3.connect('/opt/arm/db/arm.db')
            conn.execute("CREATE TABLE IF NOT EXISTS builds "
                "(id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "ts TEXT,name TEXT,pkg TEXT,output TEXT,"
                "size INTEGER,duration REAL,status TEXT)")
            conn.execute("INSERT INTO builds "
                "(ts,name,pkg,output,size,duration,status) VALUES "
                "(?,?,?,?,?,?,?)",
                (datetime.datetime.now().isoformat(),
                 name, pkg, output, size, elapsed, "SUCCESS"))
            conn.commit()

            print(f"\n{'═'*58}")
            print(f"  ✓ {output}")
            print(f"  ✓ {size:,}B  ({size/1024/1024:.2f}MB)")
            print(f"  ✓ {elapsed:.1f}s")
            print(f"{'═'*58}\n")
            return output

        except Exception as e:
            if bd.exists(): shutil.rmtree(str(bd))
            raise

    def _make_manifest(self, pkg, act, label, perms, min_sdk, tgt_sdk):
        perm_xml = "".join(
            f'    <uses-permission android:name="{p}"/>\n'
            for p in perms)
        return f"""<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="{pkg}" android:versionCode="1" android:versionName="1.0">
  <uses-sdk android:minSdkVersion="{min_sdk}"
            android:targetSdkVersion="{tgt_sdk}"/>
{perm_xml}  <application android:label="{label}"
               android:allowBackup="true"
               android:theme="@android:style/Theme.Material.NoActionBar">
    <activity android:name=".{act}" android:exported="true">
      <intent-filter>
        <action android:name="android.intent.action.MAIN"/>
        <category android:name="android.intent.category.LAUNCHER"/>
      </intent-filter>
    </activity>
  </application>
</manifest>"""


# ══════════════════════════════════════════════════════
# SELF-PROTECTION
# ══════════════════════════════════════════════════════
def self_protect():
    src = Path(__file__).resolve()
    for bak in ["/opt/arm/bin/.apk_stream_builder.bak",
                "/usr/local/lib/apk_stream_builder.py"]:
        try:
            Path(bak).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(str(src), bak)
        except: pass

self_protect()


# ══════════════════════════════════════════════════════
# TEST BUILD
# ══════════════════════════════════════════════════════
if __name__ == '__main__':
    JAVA = '''package com.arm.stream;
import android.app.*;
import android.os.*;
import android.widget.*;
import android.graphics.*;
import android.view.*;
import android.util.TypedValue;

public class MainActivity extends Activity {
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setGravity(Gravity.CENTER);
        root.setBackgroundColor(Color.parseColor("#0d1117"));
        root.setPadding(48,48,48,48);

        TextView t1 = new TextView(this);
        t1.setText("APK Stream Builder");
        t1.setTextSize(TypedValue.COMPLEX_UNIT_SP, 30);
        t1.setTextColor(Color.parseColor("#58a6ff"));
        t1.setGravity(Gravity.CENTER);

        TextView t2 = new TextView(this);
        t2.setText("v1+v2+v3 Signed\\nDisk Streaming\\nNDK r25c Ready\\n\\u062c\\u0627\\u0647\\u0632 \\u0644\\u0644\\u062c\\u064a\\u062c\\u0627\\u0628\\u0627\\u064a\\u062a");
        t2.setTextSize(TypedValue.COMPLEX_UNIT_SP, 15);
        t2.setTextColor(Color.parseColor("#8b949e"));
        t2.setGravity(Gravity.CENTER);
        t2.setPadding(0,24,0,0);

        root.addView(t1); root.addView(t2);
        setContentView(root);
    }
}'''

    APKStreamBuilder().build({
        'app_name' : 'APK Stream Builder',
        'package'  : 'com.arm.stream',
        'activity' : 'MainActivity',
        'java_code': JAVA,
        'output'   : '/opt/arm/builds/APK_Stream_Builder.apk',
        'permissions': ['android.permission.INTERNET'],
    })
