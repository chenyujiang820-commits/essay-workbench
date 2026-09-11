# -*- coding: utf-8 -*-
"""Full-state TCP scan (IPv4+IPv6) for target ports with owning PID.
Scans ALL states (LISTEN, ESTABLISHED, TIME_WAIT, BOUND...) not just LISTEN,
because some hidden listeners (e.g. WSL mirror / hyper-v) only show in full table.
"""
import ctypes
import ctypes.wintypes as wt
import socket
import struct

TARGET_PORTS = {5173, 5174, 8000}

iphlpapi = ctypes.windll.iphlpapi
TCP_TABLE_OWNER_MODULE_LISTENER = 3
TCP_TABLE_OWNER_MODULE_ALL = 4

STATE_NAMES = {
    1: "CLOSED", 2: "LISTEN", 3: "SYN_SENT", 4: "SYN_RCVD", 5: "ESTABLISHED",
    6: "FIN_WAIT1", 7: "FIN_WAIT2", 8: "CLOSE_WAIT", 9: "CLOSING",
    10: "LAST_ACK", 11: "TIME_WAIT", 12: "DELETE_TCB",
}


def get_table(af, mode):
    """Returns list of (addr_str, port, pid, state)."""
    row_size = 160 if af == socket.AF_INET else 192
    size = wt.DWORD(0)
    iphlpapi.GetExtendedTcpTable(None, ctypes.byref(size), False, af, mode, 0)
    need = int(size.value) + 8192
    buf = None
    for _ in range(6):
        buf = ctypes.create_string_buffer(need)
        ret = iphlpapi.GetExtendedTcpTable(buf, ctypes.byref(ctypes.c_ulong(need)), False, af, mode, 0)
        if ret == 0:
            n = struct.unpack_from("<I", buf, 0)[0]
            if 4 + n * row_size <= len(buf):
                break
        elif ret != 122:
            raise OSError(f"GetExtendedTcpTable rc={ret}")
        need *= 2
    else:
        raise OSError("buffer race")
    n = struct.unpack_from("<I", buf, 0)[0]
    rows = []
    base = 4
    for i in range(n):
        off = base + i * row_size
        if af == socket.AF_INET:
            state, raw_addr, raw_port, _ra, _rp, pid = struct.unpack_from("<6I", buf, off)
            addr = socket.inet_ntoa(struct.pack("<I", raw_addr))
        else:
            raw_addr = buf[off:off + 16]
            (_ls, raw_port, _ra, _rs, _rp, state, pid) = struct.unpack_from("<7I", buf, off + 16)
            addr = socket.inet_ntop(socket.AF_INET6, raw_addr)
        port = (raw_port >> 8) | ((raw_port & 0xFF) << 8)
        rows.append((addr, port, pid, state))
    return rows


def pid_name(pid):
    if pid == 0:
        return "System"
    buf = ctypes.create_unicode_buffer(512)
    h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
    if h:
        sz = wt.DWORD(512)
        ok = ctypes.windll.psapi.GetModuleFileNameExW(h, None, buf, ctypes.byref(sz))
        ctypes.windll.kernel32.CloseHandle(h)
        if ok:
            return buf.value.replace("/", "\\").split("\\")[-1]
    return f"<pid {pid}>"


def main():
    for af, label in ((socket.AF_INET, "IPv4"), (socket.AF_INET6, "IPv6")):
        for mode, mlabel in ((TCP_TABLE_OWNER_MODULE_LISTENER, "LISTEN-only"),
                             (TCP_TABLE_OWNER_MODULE_ALL, "ALL-states")):
            print(f"--- {label} {mlabel} ---")
            try:
                rows = get_table(af, mode)
            except OSError as e:
                print("   error:", e)
                continue
            hits = [r for r in rows if r[1] in TARGET_PORTS]
            if not hits:
                print("   (no rows for target ports)")
            for addr, port, pid, state in hits:
                sname = STATE_NAMES.get(state, str(state))
                print(f"   {addr}:{port} state={sname} PID={pid} {pid_name(pid)}")
    print("=== scan complete ===")


if __name__ == "__main__":
    main()
