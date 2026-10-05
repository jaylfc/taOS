"""Ordering guarantees of the shared archive guards in tinyagentos.safe_archive.

The caps themselves are exercised end-to-end by the theme and restore route
tests. What this module pins down is *when* they fire: a tar header has to be
judged before the parser is allowed to walk past its payload, because for an
``r:gz`` upload walking past a payload means decompressing it. A guard that
first enumerates the whole archive has already spent the CPU it was meant to
deny.
"""

import io
import tarfile

import pytest

from tinyagentos import safe_archive
from tinyagentos.safe_archive import ArchiveError, check_tar_limits, extract_tar_safely, open_tar_gz

_MIB = 1024 * 1024


class _Zeros(io.RawIOBase):
    """A readable stream of `size` zero bytes, without allocating them."""

    def __init__(self, size: int) -> None:
        self.remaining = size

    def readable(self) -> bool:
        return True

    def readinto(self, buf) -> int:
        count = min(len(buf), self.remaining)
        self.remaining -= count
        buf[:count] = bytes(count)
        return count


def _tar_gz(members: list[tuple[str, int]]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=1) as tar:
        for name, size in members:
            info = tarfile.TarInfo(name)
            info.size = size
            tar.addfile(info, io.BufferedReader(_Zeros(size)))
    return buf.getvalue()


def test_oversized_member_is_rejected_from_its_own_header(tmp_path):
    """The first header must be judged before anything after it is read.

    The archive is truncated a kilobyte in, so every byte past the first
    member's header is simply absent. A guard that enumerates the archive
    before applying the caps blows up on the missing data; a guard that judges
    each header as it arrives never reaches for it and raises ArchiveError.
    """
    full = _tar_gz([("big.bin", safe_archive.MAX_MEMBER_BYTES + _MIB), ("later.txt", 1)])
    truncated = full[:1024]

    with tarfile.open(fileobj=io.BytesIO(truncated), mode="r:gz") as tar:
        with pytest.raises(ArchiveError, match="member too large"):
            check_tar_limits(tar, kind="backup")


def test_cumulative_cap_is_rejected_from_the_header_that_crosses_it():
    """Same for the running total: stop at the header that crosses the cap."""
    member = 52 * _MIB
    # Five 52 MiB members: each under the per-member cap, the fifth carries the
    # running total past MAX_UNCOMPRESSED_BYTES.
    full = _tar_gz([(f"pad{i}.bin", member) for i in range(5)])
    with tarfile.open(fileobj=io.BytesIO(full), mode="r:gz") as tar:
        with pytest.raises(ArchiveError, match="uncompressed size too large"):
            check_tar_limits(tar, kind="backup")


def test_a_within_limits_tarball_still_passes():
    """The guard must not turn a legitimate archive away."""
    full = _tar_gz([("a.txt", 3), ("b.txt", 4)])
    with tarfile.open(fileobj=io.BytesIO(full), mode="r:gz") as tar:
        check_tar_limits(tar, kind="backup")
        assert [m.name for m in tar.getmembers()] == ["a.txt", "b.txt"]


class _FakeTar:
    """The one method check_tar_limits uses, fed from a canned member list."""

    def __init__(self, members: list[tarfile.TarInfo]) -> None:
        self._members = iter(members)

    def next(self) -> tarfile.TarInfo | None:
        return next(self._members, None)


def _member(name: str, size: int, type_: bytes = tarfile.REGTYPE) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = type_
    info.size = size
    return info


def test_a_negative_member_size_is_rejected():
    """``size > cap`` is False for every negative number, so without an explicit
    sign check a ``-1`` member sails past the per-member cap and pulls the
    running total *down*, disarming the cumulative cap for everything after it.
    """
    with pytest.raises(ArchiveError, match="member size invalid"):
        check_tar_limits(_FakeTar([_member("neg.bin", -1)]), kind="backup")


def test_a_negative_size_cannot_buy_headroom_under_the_cumulative_cap():
    """A negative member must not offset later members against the total."""
    tar = _FakeTar([_member("neg.bin", -1000), _member("a", 600), _member("b", 600)])
    with pytest.raises(ArchiveError, match="member size invalid"):
        check_tar_limits(tar, kind="backup", max_uncompressed_bytes=1000)


def _poison_size(raw: bytearray, header_offset: int, size: int) -> bytearray:
    """Rewrite one header's 12-byte size field (offset 124) and fix its checksum.

    ``itn`` emits the GNU base-256 encoding for a negative value: a leading
    ``0xFF`` byte, which ``nti`` decodes back to a negative int.
    """
    raw[header_offset + 124 : header_offset + 136] = tarfile.itn(
        size, 12, tarfile.GNU_FORMAT
    )
    chksum = tarfile.calc_chksums(bytes(raw[header_offset : header_offset + 512]))[0]
    raw[header_offset + 148 : header_offset + 156] = f"{chksum:06o}\0 ".encode()
    return raw


def test_a_real_tar_with_a_negative_directory_size_is_rejected():
    """CPython only guards a negative size on members whose payload it has to
    skip (``TarInfo._block`` in ``_proc_builtin``). A directory or symlink
    header has no payload, so its size field is never looked at: a base-256
    ``-1000`` there reaches ``check_tar_limits`` intact, and the two 600-byte
    files after it would net out at 200 bytes against a 1000-byte cap.
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        tar.addfile(_member("d", 0, tarfile.DIRTYPE))
        for name in ("a.bin", "b.bin"):
            tar.addfile(_member(name, 600), io.BytesIO(b"\0" * 600))
    raw = _poison_size(bytearray(buf.getvalue()), 0, -1000)

    with (
        tarfile.open(fileobj=io.BytesIO(bytes(raw)), mode="r:") as tar,
        pytest.raises(ArchiveError, match="member size invalid"),
    ):
        check_tar_limits(tar, kind="backup", max_uncompressed_bytes=1000)


def test_gnu_long_name_first_member_refused_before_decompression(monkeypatch):
    """An 8 MiB GNU long-name helper as the first member must be refused before
    the gzip payload is fully decompressed."""
    from tinyagentos.safe_archive import open_tar_gz

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.GNU_FORMAT) as tar:
        info = tarfile.TarInfo("A" * (8 * 1024 * 1024))
        info.size = 1
        tar.addfile(info, io.BytesIO(b"x"))
    raw = buf.getvalue()

    read_bytes = []
    _original_gzip_read = safe_archive.gzip.GzipFile.read

    def counting_read(self, size=-1):
        result = _original_gzip_read(self, size)
        if result:
            read_bytes.append(len(result))
        return result

    monkeypatch.setattr(safe_archive.gzip.GzipFile, "read", counting_read)
    try:
        with pytest.raises(ArchiveError, match="tar helper record"):
            with open_tar_gz(io.BytesIO(raw)) as tar:
                check_tar_limits(tar)
        total = sum(read_bytes)
        assert total < 64 * 1024, f"decompressed {total} bytes, expected < 64 KiB"
    finally:
        monkeypatch.undo()


def test_pax_long_name_second_member_refused_before_decompression(monkeypatch):
    """A PAX long-name helper as the second member must be refused before the
    gzip payload is fully decompressed."""
    from tinyagentos.safe_archive import open_tar_gz

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.PAX_FORMAT) as tar:
        info = tarfile.TarInfo("small.txt")
        info.size = 1
        tar.addfile(info, io.BytesIO(b"s"))
        info2 = tarfile.TarInfo("A" * (8 * 1024 * 1024))
        info2.size = 1
        tar.addfile(info2, io.BytesIO(b"x"))
    raw = buf.getvalue()

    read_bytes = []
    _original_gzip_read = safe_archive.gzip.GzipFile.read

    def counting_read(self, size=-1):
        result = _original_gzip_read(self, size)
        if result:
            read_bytes.append(len(result))
        return result

    monkeypatch.setattr(safe_archive.gzip.GzipFile, "read", counting_read)
    try:
        with pytest.raises(ArchiveError, match="tar helper record"):
            with open_tar_gz(io.BytesIO(raw)) as tar:
                check_tar_limits(tar)
        total = sum(read_bytes)
        assert total < 64 * 1024, f"decompressed {total} bytes, expected < 64 KiB"
    finally:
        monkeypatch.undo()


def test_helper_refused_even_when_reads_are_chunked(monkeypatch):
    """An 8 MiB GNU long-name helper as the first member must be refused by its
    declared size even when reads are chunked to 1 MiB (simulating CPython
    3.13.16's _EXTHEADER_READ_CHUNK behavior)."""
    from tinyagentos.safe_archive import open_tar_gz, _ReadSizeGuard

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.GNU_FORMAT) as tar:
        info = tarfile.TarInfo("A" * (8 * 1024 * 1024))
        info.size = 1
        tar.addfile(info, io.BytesIO(b"x"))
    raw = buf.getvalue()

    read_bytes = []
    _original_gzip_read = safe_archive.gzip.GzipFile.read

    def counting_read(self, size=-1):
        result = _original_gzip_read(self, size)
        if result:
            read_bytes.append(len(result))
        return result

    monkeypatch.setattr(safe_archive.gzip.GzipFile, "read", counting_read)
    monkeypatch.setattr(_ReadSizeGuard, "read", lambda self, size=-1: self._wrapped.read(size))
    try:
        with pytest.raises(ArchiveError, match="tar helper record"):
            with open_tar_gz(io.BytesIO(raw)) as tar:
                check_tar_limits(tar)
        total = sum(read_bytes)
        assert total < 64 * 1024, f"decompressed {total} bytes, expected < 64 KiB"
    finally:
        monkeypatch.undo()


def test_pax_helper_refused_even_when_reads_are_chunked(monkeypatch):
    """A PAX long-name helper as the second member must be refused by its
    declared size even when reads are chunked to 1 MiB (simulating CPython
    3.13.16's _EXTHEADER_READ_CHUNK behavior)."""
    from tinyagentos.safe_archive import open_tar_gz, _ReadSizeGuard

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.PAX_FORMAT) as tar:
        info = tarfile.TarInfo("small.txt")
        info.size = 1
        tar.addfile(info, io.BytesIO(b"s"))
        info2 = tarfile.TarInfo("A" * (8 * 1024 * 1024))
        info2.size = 1
        tar.addfile(info2, io.BytesIO(b"x"))
    raw = buf.getvalue()

    read_bytes = []
    _original_gzip_read = safe_archive.gzip.GzipFile.read

    def counting_read(self, size=-1):
        result = _original_gzip_read(self, size)
        if result:
            read_bytes.append(len(result))
        return result

    monkeypatch.setattr(safe_archive.gzip.GzipFile, "read", counting_read)
    monkeypatch.setattr(_ReadSizeGuard, "read", lambda self, size=-1: self._wrapped.read(size))
    try:
        with pytest.raises(ArchiveError, match="tar helper record"):
            with open_tar_gz(io.BytesIO(raw)) as tar:
                check_tar_limits(tar)
        total = sum(read_bytes)
        assert total < 64 * 1024, f"decompressed {total} bytes, expected < 64 KiB"
    finally:
        monkeypatch.undo()


def test_helper_guard_hook_exists_on_this_python():
    """The CPython private hook _proc_member must still be present in TarInfo."""
    import inspect
    from tarfile import TarInfo
    
    assert "_proc_member" in vars(TarInfo)
    assert "_proc_member(" in inspect.getsource(TarInfo._fromtarfile)


@pytest.mark.guards("tinyagentos.safe_archive:open_tar_gz",
                    replace=[("tarinfo=_HelperSizeGuardTarInfo", "tarinfo=tarfile.TarInfo")])
def test_helper_guard_override_is_called(monkeypatch):
    """_HelperSizeGuardTarInfo._proc_member must be called during archive opening."""
    from tinyagentos.safe_archive import open_tar_gz
    
    call_count = 0
    
    def mock_proc_member(self, tarfile_obj):
        nonlocal call_count
        call_count += 1
        return tarfile.TarInfo._proc_member(self, tarfile_obj)
    
    monkeypatch.setattr(safe_archive._HelperSizeGuardTarInfo, "_proc_member", mock_proc_member)
    
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.GNU_FORMAT) as tar:
        info = tarfile.TarInfo("small.txt")
        info.size = 1
        tar.addfile(info, io.BytesIO(b"x"))
    raw = buf.getvalue()
    
    with open_tar_gz(io.BytesIO(raw)) as tar:
        list(tar)  # iterate through members
    
    assert call_count >= 1, "_HelperSizeGuardTarInfo._proc_member was never called"


def test_open_tar_gz_extracts_a_legit_archive(tmp_path):
    """A legitimate archive extracts correctly through open_tar_gz."""
    from tinyagentos.safe_archive import open_tar_gz

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.GNU_FORMAT) as tar:
        info = tarfile.TarInfo("big.bin")
        info.size = 20 * 1024 * 1024
        tar.addfile(info, io.BufferedReader(_Zeros(20 * 1024 * 1024)))
        info2 = tarfile.TarInfo("small.txt")
        info2.size = 5
        tar.addfile(info2, io.BytesIO(b"hello"))
    raw = buf.getvalue()

    dest = tmp_path / "out"
    dest.mkdir()
    with open_tar_gz(io.BytesIO(raw)) as tar:
        extract_tar_safely(tar, dest, kind="test")
    assert (dest / "big.bin").exists()
    assert (dest / "big.bin").stat().st_size == 20 * 1024 * 1024
    assert (dest / "small.txt").read_text() == "hello"
