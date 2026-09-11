"""deploy/backup.py 的行为测试（PRD v1.2 FR-13 / AC-8 / GAP-07）。

备份脚本是这期唯一能兜住"机器坏了作文没了"的东西，所以它必须被当代码测，而不是
"跑一次没报错就算好"。重点测四件事：归档真的可用（往返）、篡改能被抓出来、
权限收到 0600（归档里有 API Key 和口令哈希）、清理永远不越界进数据目录。
"""

from __future__ import annotations

import sqlite3
import sys
import tarfile
from datetime import datetime
from pathlib import Path

import pytest

# deploy/ 不是 Python 包，按脚本目录加载。
_DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
if str(_DEPLOY) not in sys.path:
    sys.path.insert(0, str(_DEPLOY))

import backup as bk  # noqa: E402  (上面做了 sys.path 引导，导入必须跟在后面)

ROWS = 12


@pytest.fixture
def data_root(tmp_path: Path) -> Path:
    """造一个形态真实的数据目录：WAL 库 + config/ + photos/。"""
    root = tmp_path / "data"
    (root / "config").mkdir(parents=True)
    (root / "photos" / "1" / "1").mkdir(parents=True)

    conn = sqlite3.connect(root / bk.DB_NAME)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE essays (id INTEGER PRIMARY KEY, title TEXT)")
        conn.executemany("INSERT INTO essays (title) VALUES (?)", [(f"第{i}篇",) for i in range(ROWS)])
        conn.commit()
    finally:
        conn.close()

    (root / "config" / "app.yaml").write_text("auth: {}\n", encoding="utf-8")
    (root / "config" / "engines.yaml").write_text("primary: {}\n", encoding="utf-8")
    for seq in range(1, 4):
        (root / "photos" / "1" / "1" / f"{seq}.jpg").write_bytes(b"jpeg-bytes-" + str(seq).encode())
    return root


@pytest.fixture
def out_dir(tmp_path: Path) -> Path:
    """数据目录之外的归档目录。"""
    out = tmp_path / "backups"
    out.mkdir()
    return out


# ---------------------------------------------------------------------------
# 往返：备份 -> 校验 -> 还原可用
# ---------------------------------------------------------------------------
def test_backup_then_verify_passes(data_root: Path, out_dir: Path) -> None:
    archive = bk.create_backup(data_root, out_dir, now=datetime(2026, 9, 12, 3, 10, 0))

    assert archive.is_file()
    assert archive.name.startswith(bk.ARCHIVE_PREFIX)
    assert bk.ARCHIVE_RE.match(archive.name), archive.name
    assert bk.verify_backup(archive) == []


def test_restored_database_is_readable_and_complete(data_root: Path, out_dir: Path) -> None:
    """归档不是摆设：解出来的库要能打开，并且行数和照片数都对得上。"""
    archive = bk.create_backup(data_root, out_dir)
    workspace = out_dir / "restore"
    bk._extract_bundle(archive, workspace)

    conn = sqlite3.connect(workspace / bk.DB_NAME)
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT count(*) FROM essays").fetchone()[0] == ROWS
    finally:
        conn.close()

    assert (workspace / "config" / "engines.yaml").is_file()
    assert len(list((workspace / "photos").rglob("*.jpg"))) == 3


def test_snapshot_captures_uncheckpointed_wal_changes(tmp_path: Path) -> None:
    """WAL 边车里未 checkpoint 的改动也必须在归档里——这正是不能直接 cp 的原因。"""
    root = tmp_path / "data"
    root.mkdir()
    conn = sqlite3.connect(root / bk.DB_NAME)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE t (v TEXT)")
        conn.execute("INSERT INTO t VALUES ('只在工作树里')")
        conn.commit()
        # 不 checkpoint、不关连接前先复制：-wal 里还压着未落主文件的改动
        assert (root / (bk.DB_NAME + "-wal")).exists()
        target = tmp_path / "snap.db"
        pages = bk.snapshot_database(root / bk.DB_NAME, target)
    finally:
        conn.close()

    assert pages > 0
    probe = sqlite3.connect(target)
    try:
        assert probe.execute("SELECT v FROM t").fetchone()[0] == "只在工作树里"
    finally:
        probe.close()


# ---------------------------------------------------------------------------
# 归档权限（归档含 API Key 与口令哈希）
# ---------------------------------------------------------------------------
@pytest.mark.skipif(sys.platform == "win32", reason="Windows 权限位不按 POSIX 语义生效")
def test_archive_is_owner_only(data_root: Path, out_dir: Path) -> None:
    archive = bk.create_backup(data_root, out_dir)
    mode = archive.stat().st_mode & 0o777
    assert mode == 0o600, f"归档权限为 {oct(mode)}，共享备份目录里等于公开密钥"


def test_archive_file_mode_constant_is_owner_only() -> None:
    """权限常量本身也要钉住（Windows 上上一条会跳过，靠这条兜底）。"""
    assert bk.ARCHIVE_FILE_MODE == 0o600


def test_archive_is_created_with_owner_only_mode_calls(monkeypatch, data_root: Path, out_dir: Path) -> None:
    """跨平台钉住 0600：Windows 上权限位不按 POSIX 生效，故直接看系统调用参数。"""
    import os

    opened: list[int] = []
    chmodded: list[int] = []
    real_open = os.open
    real_chmod = os.chmod

    def spy_open(path, flags, mode=0o777, **kwargs):
        if mode is not None:
            opened.append(int(mode))
        return real_open(path, flags, mode, **kwargs)  # type: ignore[misc]

    def spy_chmod(path, mode, **kwargs):
        chmodded.append(int(mode))
        return real_chmod(path, mode, **kwargs)

    monkeypatch.setattr(os, "open", spy_open)
    monkeypatch.setattr(os, "chmod", spy_chmod)

    archive = bk.create_backup(data_root, out_dir)
    assert archive.is_file()

    # 创建时就带 0600：避免「先按 umask 建出来、再 chmod」中间那段可读窗口
    assert bk.ARCHIVE_FILE_MODE in opened, f"创建参数里没有 0600：{[oct(m) for m in opened]}"
    # 写完再 chmod 一次，覆盖同名旧归档时也能把它一起收紧
    assert bk.ARCHIVE_FILE_MODE in chmodded, f"收尾没有 chmod 兜底：{[oct(m) for m in chmodded]}"


def test_repeated_backup_tightens_an_existing_loose_archive(monkeypatch, data_root: Path, out_dir: Path) -> None:
    """同名旧归档即使曾被别的工具放宽权限，下一次备份也要把它收回来。"""
    import os

    first = bk.create_backup(data_root, out_dir, now=datetime(2026, 9, 12, 3, 10, 0))
    calls: list[int] = []
    real_chmod = os.chmod
    monkeypatch.setattr(
        os, "chmod", lambda path, mode, **kw: (calls.append(int(mode)), real_chmod(path, mode))[1]
    )
    again = bk.create_backup(data_root, out_dir, now=datetime(2026, 9, 12, 3, 10, 0))

    assert again == first
    assert calls and calls[0] == bk.ARCHIVE_FILE_MODE

# ---------------------------------------------------------------------------
# 篡改必须被 verify 抓住
# ---------------------------------------------------------------------------
def test_verify_detects_a_missing_photo(data_root: Path, out_dir: Path) -> None:
    archive = bk.create_backup(data_root, out_dir)
    tampered = _rebuild_without(archive, out_dir / "tampered.tar.gz", drop_prefix="photos/")

    problems = bk.verify_backup(tampered)
    assert problems, "少了一张照片却被判定为可用，verify 就是假的"
    assert any("photos" in text for text in problems), problems


def test_verify_detects_a_corrupt_archive(data_root: Path, out_dir: Path) -> None:
    archive = bk.create_backup(data_root, out_dir)
    broken = out_dir / "broken.tar.gz"
    broken.write_bytes(archive.read_bytes()[:64])  # 截断成半截 gzip

    assert bk.verify_backup(broken), "半截归档必须报问题"


def test_verify_reports_missing_archive(out_dir: Path) -> None:
    problems = bk.verify_backup(out_dir / "ewb-backup-20260101-000000.tar.gz")
    assert problems and "不存在" in problems[0]


def _rebuild_without(archive: Path, target: Path, *, drop_prefix: str) -> Path:
    """解包后丢掉某目录的成员再打回去，模拟归档被删过/被截过。"""
    staging = target.parent / (target.stem + "-src")
    bk._extract_bundle(archive, staging)
    target.unlink(missing_ok=True)
    with tarfile.open(target, "w:gz") as bundle:
        for path in sorted(staging.rglob("*")):
            if not path.is_file():
                continue
            name = path.relative_to(staging).as_posix()
            if name.startswith(drop_prefix):
                continue
            bundle.add(path, arcname=name)
    return target


# ---------------------------------------------------------------------------
# 保留策略与清理边界
# ---------------------------------------------------------------------------
def _fake_archive(name: str, where: Path) -> Path:
    path = where / name
    path.write_bytes(b"x")
    return path


def test_plan_retention_keeps_daily_weekly_monthly_slots(tmp_path: Path) -> None:
    """连续 20 天各一份：日档留 7，周档/月档在更远处补位。"""
    archives = [
        _fake_archive(f"ewb-backup-2026{month:02d}{day:02d}-031000.tar.gz", tmp_path)
        for month, day in [(8, day) for day in range(12, 32)]
    ]
    doomed = bk.plan_retention(archives, keep_daily=7, keep_weekly=4, keep_monthly=6)

    kept = len(archives) - len(doomed)
    assert kept >= 7, f"日档 7 份都没留住，剩 {kept}"
    assert kept < len(archives), "一份都没删，保留策略没生效"
    # 被删的只能是较老的归档，最近一份永远保留
    assert archives[-1].name not in doomed


def test_plan_retention_can_disable_a_slot(tmp_path: Path) -> None:
    archives = [_fake_archive(f"ewb-backup-202609{day:02d}-031000.tar.gz", tmp_path) for day in range(1, 11)]
    doomed = bk.plan_retention(archives, keep_daily=0, keep_weekly=0, keep_monthly=1)
    assert len(archives) - len(doomed) == 1


def test_prune_refuses_to_delete_inside_the_data_dir(data_root: Path, tmp_path: Path) -> None:
    """--out 误指到数据目录内时，清理必须拒绝，而不是把老师的照片当旧归档删掉。"""
    inside = data_root / "backups"
    inside.mkdir()
    victim = _fake_archive("ewb-backup-20260101-031000.tar.gz", inside)

    removed = bk.prune_out_dir(inside, data_root, keep_daily=0, keep_weekly=0, keep_monthly=0)

    assert removed == []
    assert victim.is_file(), "数据目录内的文件被删了，这是最严重的越界"


def test_prune_deletes_only_archives_it_produced(out_dir: Path) -> None:
    doomed = _fake_archive("ewb-backup-20260101-031000.tar.gz", out_dir)
    keeper = out_dir / "notes.txt"
    keeper.write_text("别删我", encoding="utf-8")
    other = _fake_archive("random-file.tar.gz", out_dir)

    removed = bk.prune_out_dir(
        out_dir, out_dir.parent / "data", keep_daily=0, keep_weekly=0, keep_monthly=0
    )

    assert removed == [doomed.name]
    assert keeper.is_file() and other.is_file()


# ---------------------------------------------------------------------------
# CLI 参数（运维只会敲命令，不会敲函数）
# ---------------------------------------------------------------------------
def test_cli_verify_flag_reports_problems_via_exit_code(data_root: Path, out_dir: Path, capsys) -> None:
    archive = bk.create_backup(data_root, out_dir)
    assert bk.main(["--data-dir", str(data_root), "--verify", str(archive)]) == 0

    broken = out_dir / "ewb-backup-20200101-000000.tar.gz"
    broken.write_bytes(b"not-a-tar")
    assert bk.main(["--data-dir", str(data_root), "--verify", str(broken)]) != 0
    capsys.readouterr()


def test_cli_requires_a_data_dir() -> None:
    """既没给 --data-dir 也没有 EWB_DATA_DIR 时非零退出，绝不瞎猜路径。"""
    import os

    os.environ.pop("EWB_DATA_DIR", None)
    assert bk.main(["--out", "somewhere"]) != 0
