"""S2-T05 数据备份与校验（GAP-07 / PRD v1.2 FR-13）。

职责：给"每台机器跑一份、数据只落本机 SQLite + 照片目录"的形态补上运维底线——
一条命令产出一份**一致性**归档，一条命令校验归档是否真的可用，外加按日/周/月
保留裁剪。

为什么不能直接拷 ``essay.db``：生产库跑在 WAL 模式下，主文件之外还有 ``-wal`` 边车
（未 checkpoint 的改动都在里面），cp 走的可能是半截状态。这里用标准库
``sqlite3.Connection.backup()``（SQLite Online Backup API）在事务内把库复制到临时
文件，再连同 ``config/``（口令哈希、engines.yaml，**属敏感件**）、``photos/``（原片）
一起打成 ``ewb-backup-YYYYMMDD-HHMMSS.tar.gz``。

安全边界：
* 对数据目录**只读**；本脚本在数据目录内不创建、不修改、更不删除任何文件。
  唯一例外是 WAL 库在只读打开失败时退化为普通打开（可能由 SQLite 自行补写检查点）。
* 清理只作用于 ``--out`` 目录里由本脚本产出的 ``ewb-backup-*.tar.gz``；
  ``--out`` 落在数据目录之内时直接拒绝清理。
* 归档以 0600（owner-only）落盘：包里有 ``config/engines.yaml``（模型 API Key）和
  ``config/app.yaml``（口令哈希），共享备份目录下 world-readable 的归档等于把这两样
  东西交给了机器上所有账号。异地存放时请继续用加密通道（rsync over ssh 等）。
* ``--verify`` 解到系统临时目录后跑 ``PRAGMA integrity_check`` 并比对照片/配置文件数，
  校验完立即删除临时目录。

用法::

    python deploy/backup.py --data-dir /data/essay-workbench --out /var/backups/ewb
    python deploy/backup.py --verify /var/backups/ewb/ewb-backup-20260912-031000.tar.gz
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path, PurePosixPath

#: 归档命名：``ewb-backup-YYYYMMDD-HHMMSS.tar.gz``（时间戳即保留策略的归桶依据）。
ARCHIVE_PREFIX = "ewb-backup-"
NAME_FORMAT = "%Y%m%d-%H%M%S"
ARCHIVE_RE = re.compile(r"^ewb-backup-(\d{8})-(\d{6})\.tar\.gz$")

DB_NAME = "essay.db"
CONFIG_DIR_NAME = "config"
PHOTOS_DIR_NAME = "photos"
MANIFEST_NAME = "manifest.json"

#: 随库一起打包的相对目录（缺哪个就跳过哪个）。
EXTRA_DIRECTORIES: tuple[str, ...] = (CONFIG_DIR_NAME, PHOTOS_DIR_NAME)

#: ``--out`` 未指定时，落在数据目录的**同级**目录，绝不落在数据目录里面。
DEFAULT_OUT_NAME = "essay-backups"

#: 归档文件权限：内含 API Key 与口令哈希，必须 owner-only 可读（见 ``_write_archive``）。
ARCHIVE_FILE_MODE = 0o600

DEFAULT_KEEP_DAILY = 7
DEFAULT_KEEP_WEEKLY = 4
DEFAULT_KEEP_MONTHLY = 6


class BackupError(Exception):
    """可预期的失败（参数错、目录不存在、归档损坏）：CLI 只打印文案，不抛栈。"""


# ---------------------------------------------------------------------------
# 路径解析
# ---------------------------------------------------------------------------
def resolve_data_dir(value: str | None) -> Path:
    """确定数据目录：``--data-dir`` > 环境变量 ``EWB_DATA_DIR`` > 报错。

    环境变量与后端 ``app.config`` 用同一个键，避免备份脚本和被备份的进程各读一套配置。

    Raises:
        BackupError: 未指定，或目录下没有 ``essay.db``。
    """
    raw = value or os.environ.get("EWB_DATA_DIR") or ""
    if not raw.strip():
        raise BackupError("未指定数据目录：给 --data-dir 或设置 EWB_DATA_DIR")
    data_dir = Path(raw).expanduser().resolve()
    if not (data_dir / DB_NAME).is_file():
        raise BackupError(f"数据目录里没有 {DB_NAME}：{data_dir}")
    return data_dir


def _timestamp_of(name: str) -> datetime | None:
    """从归档文件名解析时间戳；命名不合规返回 ``None``（不合规的永不被选中删除）。"""
    match = ARCHIVE_RE.match(name)
    if not match:
        return None
    try:
        # ARCHIVE_RE 的两组分别是 "YYYYMMDD" 与 "HHMMSS"，拼接后按无分隔格式解析。
        return datetime.strptime("".join(match.groups()), "%Y%m%d%H%M%S")
    except ValueError:  # 形似但非法（例如月份 13）
        return None


# ---------------------------------------------------------------------------
# 一致性快照
# ---------------------------------------------------------------------------
def _connect_readonly(db_file: Path) -> sqlite3.Connection:
    """以只读方式打开源库；失败时退化为普通打开。

    退化的原因：WAL 库在只读模式下需要能读写 ``-shm`` / ``-wal`` 边车文件，若数据目录
    权限受限（例如备份账号只读挂载）就会打不开。退化为普通打开只可能由 SQLite 补写一次
    检查点，不会改动业务数据；比"备不出来"更安全。
    """
    uri = f"{db_file.resolve().as_uri()}?mode=ro"
    try:
        return sqlite3.connect(uri, uri=True)
    except sqlite3.Error:
        return sqlite3.connect(db_file)


def snapshot_database(db_file: Path, target: Path) -> int:
    """用 SQLite Online Backup API 把 ``db_file`` 快照到 ``target``，返回页数。

    Args:
        db_file: 源库路径（必须存在）。
        target: 快照输出路径（父目录会被创建）。

    Returns:
        快照库的页数（``page_count``），写进清单便于事后粗对。

    Raises:
        BackupError: 源库不存在，或备份过程报错。
    """
    if not db_file.exists():
        raise BackupError(f"数据库不存在：{db_file}")
    target.parent.mkdir(parents=True, exist_ok=True)
    source = _connect_readonly(db_file)
    try:
        destination = sqlite3.connect(target)
        try:
            with destination:
                source.backup(destination)
            pages = int(destination.execute("PRAGMA page_count").fetchone()[0])
        finally:
            destination.close()
    except sqlite3.Error as exc:
        raise BackupError(f"SQLite 快照失败：{exc}") from exc
    finally:
        source.close()
    return pages


def _file_count(root: Path) -> int:
    """目录下普通文件的数量（目录不存在返回 0）。"""
    if not root.is_dir():
        return 0
    return sum(1 for path in root.rglob("*") if path.is_file())


def _packed_directories(data_dir: Path) -> list[str]:
    """列出实际存在、需要随库一起打包的目录名。"""
    return [name for name in EXTRA_DIRECTORIES if (data_dir / name).is_dir()]


def _write_archive(
    archive_path: Path, data_dir: Path, snapshot_file: Path, manifest_path: Path
) -> None:
    """把快照库 + 清单 + config/ + photos/ 打成 tar.gz（成员名一律为相对路径）。

    归档权限收死到 ``ARCHIVE_FILE_MODE``（0600）：包里有 ``config/engines.yaml``（模型
    API Key）与 ``config/app.yaml``（口令哈希），若跟随 umask，放在共享备份目录里就等于
    对所有本机用户可读——备份本身会变成泄密面。用 ``os.open`` 以 0600 **创建**（避免
    "先按 umask 建出来、再 chmod"之间的可读窗口），写完再 ``chmod`` 兜底，覆盖同名旧归档
    时也能把它一起收紧。
    """
    descriptor = os.open(archive_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, ARCHIVE_FILE_MODE)
    with os.fdopen(descriptor, "wb") as raw, tarfile.open(fileobj=raw, mode="w:gz") as bundle:
        bundle.add(snapshot_file, arcname=DB_NAME, recursive=False)
        bundle.add(manifest_path, arcname=MANIFEST_NAME, recursive=False)
        for name in _packed_directories(data_dir):
            bundle.add(data_dir / name, arcname=name, recursive=True)
    os.chmod(archive_path, ARCHIVE_FILE_MODE)


def create_backup(data_dir: Path, out_dir: Path, *, now: datetime | None = None) -> Path:
    """产出一次备份归档，返回归档路径。

    Args:
        data_dir: 运行中的数据目录（只读访问）。
        out_dir: 归档输出目录（不存在会创建）。
        now: 归档时间戳（测试可注入）。

    Raises:
        BackupError: 数据目录不存在，或快照/打包失败。
    """
    if not data_dir.is_dir():
        raise BackupError(f"数据目录不存在：{data_dir}")
    stamp = (now or datetime.now()).strftime(NAME_FORMAT)
    out_dir.mkdir(parents=True, exist_ok=True)
    archive_path = out_dir / f"{ARCHIVE_PREFIX}{stamp}.tar.gz"

    # 暂存区开在 out 目录下（而不是数据目录里），确保源目录零写入。
    staging = Path(tempfile.mkdtemp(prefix="ewb-snapshot-", dir=out_dir))
    try:
        snapshot_file = staging / DB_NAME
        pages = snapshot_database(data_dir / DB_NAME, snapshot_file)
        manifest = {
            "created_at": stamp,
            "database": DB_NAME,
            "page_count": pages,
            "photo_files": _file_count(data_dir / PHOTOS_DIR_NAME),
            "config_files": _file_count(data_dir / CONFIG_DIR_NAME),
            "directories": [DB_NAME, *_packed_directories(data_dir)],
        }
        manifest_path = staging / MANIFEST_NAME
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _write_archive(archive_path, data_dir, snapshot_file, manifest_path)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return archive_path


# ---------------------------------------------------------------------------
# 校验（--verify）
# ---------------------------------------------------------------------------
def _safe_members(bundle: tarfile.TarFile) -> list[tarfile.TarInfo]:
    """挑出可安全落盘的成员；绝对路径、``..`` 逃逸、特殊文件一律拒绝（防 tar-slip）。"""
    members: list[tarfile.TarInfo] = []
    for member in bundle.getmembers():
        parts = PurePosixPath(member.name).parts
        if member.name.startswith(("/", "\\")) or ".." in parts:
            raise BackupError(f"归档含非法成员路径：{member.name}")
        if not (member.isfile() or member.isdir()):
            raise BackupError(f"归档含非普通文件成员：{member.name}")
        members.append(member)
    return members


def _extract_bundle(archive: Path, destination: Path) -> None:
    """把归档解到 ``destination``（已做过路径校验）。"""
    with tarfile.open(archive, "r:gz") as bundle:
        members = _safe_members(bundle)
        try:
            bundle.extractall(destination, members=members, filter="data")
        except TypeError:  # pragma: no cover - Python 3.11.0~3.11.3 不认 filter 参数
            bundle.extractall(destination, members=members)


def _integrity_ok(db_file: Path) -> tuple[bool, str]:
    """对解包出来的库跑 ``PRAGMA integrity_check``。"""
    if not db_file.is_file():
        return False, f"归档内缺少 {db_file.name}"
    try:
        connection = sqlite3.connect(db_file)
        try:
            rows = connection.execute("PRAGMA integrity_check").fetchall()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        return False, f"打开快照库失败：{exc}"
    verdict = str(rows[0][0]) if rows else "无输出"
    return verdict == "ok", verdict


def _member_names(archive: Path, directory: str) -> int:
    """统计归档里 ``directory/`` 下的文件成员数。"""
    with tarfile.open(archive, "r:gz") as bundle:
        prefix = f"{directory}/"
        return sum(
            1
            for member in bundle.getmembers()
            if member.isfile() and member.name.startswith(prefix)
        )


def verify_backup(archive: Path) -> list[str]:
    """校验一个归档是否可用，返回问题列表（空列表即通过）。

    查三件事：库能打开且 ``integrity_check`` 为 ok；清单记录的照片/配置数量与归档成员
    一致；解包落地数量与归档成员一致。"能解开但少照片"和"库打不开"是同一种事故。
    """
    if not archive.is_file():
        return [f"归档不存在：{archive}"]

    problems: list[str] = []
    workspace = Path(tempfile.mkdtemp(prefix="ewb-verify-"))
    try:
        _extract_bundle(archive, workspace)
        ok, detail = _integrity_ok(workspace / DB_NAME)
        if not ok:
            problems.append(detail)

        manifest_path = workspace / MANIFEST_NAME
        if not manifest_path.is_file():
            problems.append(f"归档内缺少 {MANIFEST_NAME}，无法核对文件数量")
            return problems
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            problems.append(f"清单不是合法 JSON：{exc}")
            return problems

        for directory, key in ((PHOTOS_DIR_NAME, "photo_files"), (CONFIG_DIR_NAME, "config_files")):
            expected = int(manifest.get(key, 0))
            listed = _member_names(archive, directory)
            extracted = _file_count(workspace / directory)
            if listed != expected:
                problems.append(f"{directory} 数量不符：清单 {expected} / 归档 {listed}")
            if extracted != listed:
                problems.append(f"{directory} 解包不完整：归档 {listed} / 落地 {extracted}")
        if int(manifest.get("page_count", 0)) <= 0:
            problems.append("清单里的数据库页数为 0，快照疑似空库")
    except (
        BackupError,
        OSError,
        tarfile.TarError,
        EOFError,  # 截断的 .tar.gz：gzip 读到一半缺结束标记就抛，且它不是 OSError 子类
        sqlite3.Error,
        ValueError,  # 清单 JSON 非法等
    ) as exc:
        # 校验器的职责是「把坏归档说清楚」，不是「在坏归档上崩溃」：
        # 解析期异常一律归约成一条 problem，由 main() 决定退出码。
        problems.append(str(exc) or exc.__class__.__name__)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
    return problems


# ---------------------------------------------------------------------------
# 保留策略
# ---------------------------------------------------------------------------
def plan_retention(
    archives: list[Path],
    *,
    keep_daily: int = DEFAULT_KEEP_DAILY,
    keep_weekly: int = DEFAULT_KEEP_WEEKLY,
    keep_monthly: int = DEFAULT_KEEP_MONTHLY,
) -> list[Path]:
    """按"日 N / 周 M / 月 K"槽位算出应当删除的归档（GFS 简化版）。

    从最新往旧走：每个归档尝试占用它**所在那一天/那一周/那一个月**的槽位，三类槽位都满
    了才删。于是保留结果同时满足"最近 7 天每天一份、最近 4 周每周一份、最近 6 个月每月
    一份"，而不是简单只留最新 N 份。

    Args:
        archives: 待判定的归档路径；命名不合 ``ewb-backup-*.tar.gz`` 的会被忽略（不删）。
        keep_daily: 日槽位数（<=0 关闭该档）。
        keep_weekly: 周槽位数。
        keep_monthly: 月槽位数。

    Returns:
        应当删除的归档路径列表。
    """
    dated: list[tuple[datetime, Path]] = []
    for path in archives:
        moment = _timestamp_of(path.name)
        if moment is not None:
            dated.append((moment, path))
    dated.sort(key=lambda item: (item[0], item[1].name), reverse=True)

    days: set[str] = set()
    weeks: set[str] = set()
    months: set[str] = set()
    to_delete: list[Path] = []
    for moment, path in dated:
        iso = moment.isocalendar()
        day_key = moment.date().isoformat()
        week_key = f"{iso[0]}-W{iso[1]:02d}"
        month_key = moment.strftime("%Y-%m")
        if keep_daily > 0 and day_key not in days and len(days) < keep_daily:
            days.add(day_key)
        elif keep_weekly > 0 and week_key not in weeks and len(weeks) < keep_weekly:
            weeks.add(week_key)
        elif keep_monthly > 0 and month_key not in months and len(months) < keep_monthly:
            months.add(month_key)
        else:
            to_delete.append(path)
    return to_delete


def prune_out_dir(
    out_dir: Path,
    data_dir: Path,
    *,
    keep_daily: int,
    keep_weekly: int,
    keep_monthly: int,
) -> list[str]:
    """在 ``out_dir`` 内执行保留策略，返回被删文件名。

    **数据目录内的文件永不删除**：``--out`` 落在数据目录内部（或就是数据目录）时直接
    跳过清理并给出提示——备份脚本可以拒绝干活，但不能成为删老师数据的入口。
    """
    resolved_out = out_dir.expanduser().resolve()
    resolved_data = data_dir.expanduser().resolve()
    if resolved_out == resolved_data or resolved_data in resolved_out.parents:
        print("   输出目录位于数据目录内，跳过清理（请把归档放到数据目录之外）")
        return []
    archives = sorted(
        (path for path in resolved_out.glob(ARCHIVE_PREFIX + "*.tar.gz") if path.is_file()),
        key=lambda path: path.name,
    )
    doomed = plan_retention(
        archives, keep_daily=keep_daily, keep_weekly=keep_weekly, keep_monthly=keep_monthly
    )
    removed: list[str] = []
    for path in doomed:
        path.unlink(missing_ok=True)
        removed.append(path.name)
    return removed


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    """构造命令行参数。"""
    parser = argparse.ArgumentParser(
        prog="backup.py",
        description="班级作文工作台数据备份：SQLite 在线快照 + config/ + photos/ 打包",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--data-dir", help="数据目录（缺省读环境变量 EWB_DATA_DIR；两者都没有则报错）"
    )
    parser.add_argument(
        "--out",
        help=f"归档输出目录（缺省为数据目录同级的 {DEFAULT_OUT_NAME}/，不会落在数据目录内）",
    )
    parser.add_argument("--keep-daily", type=int, default=DEFAULT_KEEP_DAILY, help="保留最近 N 天，每天一份")
    parser.add_argument("--keep-weekly", type=int, default=DEFAULT_KEEP_WEEKLY, help="N 天之外保留 M 周，每周一份")
    parser.add_argument("--keep-monthly", type=int, default=DEFAULT_KEEP_MONTHLY, help="M 周之外保留 K 个月，每月一份")
    parser.add_argument("--verify", metavar="ARCHIVE", help="只校验指定归档是否完整可用")
    return parser


def main(argv: list[str] | None = None) -> int:
    """入口。返回进程退出码（0 成功）。"""
    args = build_parser().parse_args(argv)

    if args.verify:
        archive = Path(args.verify).expanduser()
        problems = verify_backup(archive)
        if problems:
            print(f"FAIL {archive}")
            for problem in problems:
                print("   " + problem)
            return 1
        print(f"OK   {archive}")
        return 0

    try:
        data_dir = resolve_data_dir(args.data_dir)
        out_dir = Path(args.out).expanduser() if args.out else data_dir.parent / DEFAULT_OUT_NAME
        archive = create_backup(data_dir, out_dir)
    except BackupError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 1

    print(f"OK   {archive}")
    problems = verify_backup(archive)
    for problem in problems:
        print("   WARN " + problem)
    removed = prune_out_dir(
        out_dir,
        data_dir,
        keep_daily=args.keep_daily,
        keep_weekly=args.keep_weekly,
        keep_monthly=args.keep_monthly,
    )
    for name in removed:
        print(f"DEL  {name}")
    return 0 if not problems else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
