"""20a-1: 모드 B 옵션 4GB 분할 파일(--split-large)과 pcmigrate join. 실제 4GB 파일은 만들지 않고 기준(조각 크기)을 낮춰서 시험한다."""
import json
import os
from pathlib import Path

import pytest

from pcmigrate import api, cli, fs, manifest as mf, split, transfer as tr
from pcmigrate.copier import COPIED, MISMATCH, MISSING, SKIPPED_SAME, VERIFIED
from pcmigrate.transfer import TransferError, transfer
from test_transfer import FakeRobocopy

THRESH, PSZ = 1000, 400
BIG = bytes((i * 7 + 3) % 251 for i in range(2500))  # 조각 400 x 6 + 100 = 7개


@pytest.fixture(autouse=True)
def small_limits(monkeypatch):
    monkeypatch.setattr(split, "SPLIT_THRESHOLD", THRESH)
    monkeypatch.setattr(split, "PART_SIZE", PSZ)


def ntfs(path):
    return fs.DestInfo(str(path), "fixed", "NTFS", 10 ** 12, 1, "D:")


def fat32(path):
    return fs.DestInfo(str(path), "removable", "FAT32", 10 ** 12, 2, "E:")


def make_src(tmp_path):
    src = tmp_path / "src"
    (src / "proj" / "deep").mkdir(parents=True)
    (src / "small.txt").write_bytes(b"small" * 10)
    (src / "proj" / "model.rvt").write_bytes(BIG)
    (src / "proj" / "deep" / "c.txt").write_bytes(b"charlie")
    return src


def run(src, dst, **kw):
    kw.setdefault("runner", FakeRobocopy())
    kw.setdefault("dest_info_fn", ntfs)
    return transfer(src, dst, **kw)


def part_files(folder, name="model.rvt"):
    return sorted(p.name for p in Path(folder).glob(name + ".pcm*") if not p.name.endswith((".bak", ".pcmigrate-part")))


def rows(path):
    return {r["rel"]: r for r in mf.ManifestReader(Path(path)).rows()}


# --------------------------------------------------------------------- 분할 -> 검사 -> 합치기 왕복

def test_split_roundtrip_verify_and_join(tmp_path):
    src = make_src(tmp_path)
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in src.rglob("*") if p.is_file()}
    dst, m = tmp_path / "dst", tmp_path / "m.jsonl"
    rc = FakeRobocopy()
    rep = run(src, dst, runner=rc, split_large=True, manifest_out=m)
    assert rep.failures == []
    assert part_files(dst / "proj") == [f"model.rvt.pcm{i:03d}" for i in range(1, 8)]
    assert not (dst / "proj" / "model.rvt").exists()  # robocopy 에서 제외됨
    assert (dst / "small.txt").read_bytes() == b"small" * 10 and (dst / "proj" / "deep" / "c.txt").exists()
    sizes = [p.stat().st_size for p in sorted((dst / "proj").glob("model.rvt.pcm*"))]
    assert sizes == [400] * 6 + [100] and b"".join(p.read_bytes() for p in sorted((dst / "proj").glob("model.rvt.pcm*"))) == BIG
    assert rep.meta["split_files"] == 1 and rep.meta["split_parts"] == 7 and rep.meta["split_part_size"] == PSZ
    assert rep.meta["split_written_parts"] == 7 and rep.meta["verification_complete"] is True
    # 원본은 읽기만 했다
    assert before == {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in src.rglob("*") if p.is_file()}
    # robocopy 에는 큰 파일 이름이 제외 목록으로 넘어갔다
    assert any("model.rvt" in c[c.index("/XF") + 1:] for c in rc.calls)
    # 목록: parts 와 전체 해시
    row = rows(m)["proj/model.rvt"]
    import hashlib
    assert row["sha256"] == hashlib.sha256(BIG).hexdigest() and row["size"] == 2500
    assert [p["name"] for p in row["parts"]] == [f"model.rvt.pcm{i:03d}" for i in range(1, 8)]
    assert [p["size"] for p in row["parts"]] == [400] * 6 + [100]
    assert row["parts"][0]["sha256"] == hashlib.sha256(BIG[:400]).hexdigest()
    assert json.loads(m.read_text(encoding="utf-8").splitlines()[0])["version"] == 2
    # 새 PC 쪽 verify: 조각을 이어 읽어 전체 해시와 비교
    v = mf.verify_against_manifest(m, dst)
    assert v.failures == [] and v.meta["extra_files"] == 0
    assert {r.status for r in v.results} == {VERIFIED}
    # join
    out = tmp_path / "out"
    j = api.run_join(dst, out, dest_info_fn=ntfs)
    assert j.failures == [] and (out / "proj" / "model.rvt").read_bytes() == BIG
    assert j.meta["hash_source"] == "manifest" or j.meta["hash_source"] == "state"
    assert len(list(dst.rglob("*.pcm00*"))) == 7  # 조각은 지우지 않는다
    # 합친 폴더도 목록으로 검사된다(조각이 없으면 합쳐진 파일로 본다)
    v2 = mf.verify_against_manifest(m, out, find_extras=False)
    assert [r.status for r in v2.results if "model.rvt" in r.src] == [VERIFIED]


def test_option_off_behaves_as_before(tmp_path, monkeypatch):
    src = make_src(tmp_path)
    monkeypatch.setattr(split, "copy_split", lambda *a, **k: pytest.fail("분할 안 쓰는 경로에서 불리면 안 된다"))
    m = tmp_path / "m.jsonl"
    rc = FakeRobocopy()
    rep = run(src, tmp_path / "dst", runner=rc, manifest_out=m)
    assert rep.failures == []
    assert (tmp_path / "dst" / "proj" / "model.rvt").read_bytes() == BIG
    assert "split_files" not in rep.meta and "split_large" not in rep.meta
    assert not list((tmp_path / "dst").rglob("*.pcm0*"))
    assert all("parts" not in r for r in rows(m).values())
    assert rc.calls[0].count("/XF") == 1 and rc.calls[0][-1] == tr.OWN_PATTERN  # 제외 목록이 늘지 않았다(/E 한 번에 복사)
    assert "/LEV:1" not in rc.calls[0]


def test_split_large_with_no_big_file_changes_nothing(tmp_path):
    src = tmp_path / "s"
    src.mkdir()
    (src / "a.txt").write_bytes(b"x" * 100)
    rc = FakeRobocopy()
    rep = run(src, tmp_path / "d", runner=rc, split_large=True)
    assert rep.failures == [] and rep.meta["split_files"] == 0 and rc.calls[0].count("/XF") == 1
    assert (tmp_path / "d" / "a.txt").exists()


def test_threshold_is_strictly_greater(tmp_path):
    src = tmp_path / "s"
    src.mkdir()
    (src / "exact.bin").write_bytes(b"e" * THRESH)  # 한도와 같으면 나누지 않는다(FAT32 는 4GiB-1 까지 저장 가능)
    (src / "over.bin").write_bytes(b"o" * (THRESH + 1))
    rep = run(src, tmp_path / "d", split_large=True)
    assert rep.failures == []
    assert (tmp_path / "d" / "exact.bin").exists() and not (tmp_path / "d" / "over.bin").exists()
    assert part_files(tmp_path / "d", "over.bin") == ["over.bin.pcm001", "over.bin.pcm002", "over.bin.pcm003"]


def test_env_override_of_limits(tmp_path, monkeypatch):
    monkeypatch.undo()  # autouse 로 바꾼 상수를 되돌리고 환경변수만 쓴다
    monkeypatch.setenv(split.ENV_THRESHOLD, "50")
    monkeypatch.setenv(split.ENV_PART_SIZE, "30")
    assert split.split_threshold() == 50 and split.part_size() == 30
    assert split.part_sizes(100, 30) == [30, 30, 30, 10]
    monkeypatch.setenv(split.ENV_PART_SIZE, str(2 ** 40))
    assert split.part_size() == fs.FAT4G  # FAT32 한도를 넘는 조각은 만들지 않는다
    monkeypatch.setenv(split.ENV_PART_SIZE, "abc")
    assert split.part_size() == split.PART_SIZE


# --------------------------------------------------------------------- FAT32 가정 사전 검사

def test_fat32_big_file_blocked_without_option_and_hint(tmp_path):
    src = make_src(tmp_path)
    monkey_big = FakeRobocopy()
    # 실제 FAT32 한도(4GiB)를 쓰는 시험은 파일을 만들 수 없으므로 한도를 낮춘 값으로 같은 분기를 지나게 한다
    import pcmigrate.fs as fsmod
    orig = fsmod.FAT4G
    fsmod.FAT4G = THRESH
    try:
        with pytest.raises(TransferError) as ei:
            run(src, tmp_path / "d", runner=monkey_big, dest_info_fn=fat32)
        assert "--split-large" in str(ei.value) and "join" in str(ei.value)
        assert monkey_big.calls == []  # 복사 전에 막았다
        rep = run(src, tmp_path / "d2", dest_info_fn=fat32, split_large=True)  # 옵션을 켜면 막지 않는다
        assert rep.failures == [] and rep.meta["split_files"] == 1 and "fat32_blocked" not in rep.meta
        assert part_files(tmp_path / "d2" / "proj")
    finally:
        fsmod.FAT4G = orig


def test_fat32_dry_run_with_option_warns_not_blocks(tmp_path):
    src = make_src(tmp_path)
    warns = []
    rep = run(src, tmp_path / "d", dry_run=True, split_large=True, dest_info_fn=fat32, warn=warns.append)
    assert any("--split-large" in w and "조각" in w for w in warns)
    assert not (tmp_path / "d").exists() or not any((tmp_path / "d").rglob("*"))
    assert rep.meta["split_files"] == 1
    big = [r for r in rep.results if r.src.endswith("model.rvt")]
    assert big and big[0].dst.endswith("model.rvt.pcm001 외 6개")


def test_ntfs_dest_splits_when_option_on(tmp_path):
    rep = run(make_src(tmp_path), tmp_path / "d", split_large=True, dest_info_fn=ntfs)
    assert rep.meta["dest_fs"] == "NTFS" and rep.meta["split_files"] == 1
    assert part_files(tmp_path / "d" / "proj")


# --------------------------------------------------------------------- 이어받기(조각 단위)

def test_resume_skips_finished_parts_and_restarts_partial_part(tmp_path):
    src = make_src(tmp_path)
    dst = tmp_path / "dst"
    calls = {"n": 0}

    def cancel_midway():  # 4번째 조각을 쓰는 도중 중단(조각당 청크 100바이트 -> 읽기 4번)
        calls["n"] += 1
        return calls["n"] > 15

    with pytest.raises(Exception) as ei:
        run(src, dst, split_large=True, cancel=cancel_midway, chunk=100)
    assert type(ei.value).__name__ == "Cancelled"
    done = part_files(dst / "proj")
    assert 1 <= len(done) < 7
    assert not list((dst / "proj").glob("*.pcmigrate-part"))  # 정상 중단: 임시 조각을 남기지 않는다
    first_mtime = (dst / "proj" / done[0]).stat().st_ino, (dst / "proj" / done[0]).stat().st_mtime_ns
    # 강제 종료로 쓰다 만 임시 조각이 남았다고 가정(완성 이름이 아니라 임시 이름): 처음부터 다시 쓴다
    stray = dst / "proj" / f"model.rvt.pcm{len(done) + 1:03d}.pcmigrate-part"
    stray.write_bytes(b"JUNK" * 10)
    rep = run(src, dst, split_large=True, chunk=100)
    assert rep.failures == [] and part_files(dst / "proj") == [f"model.rvt.pcm{i:03d}" for i in range(1, 8)]
    assert rep.meta["split_kept_parts"] == len(done) and rep.meta["split_written_parts"] == 7 - len(done)
    assert not stray.exists()
    assert (dst / "proj" / done[0]).stat().st_ino == first_mtime[0]  # 완성 조각은 다시 쓰지 않았다
    assert b"".join((dst / "proj" / n).read_bytes() for n in part_files(dst / "proj")) == BIG
    # 세 번째 실행: 이미 검사 끝난 파일은 상태 파일 기록으로 건너뛴다
    rep3 = run(src, dst, split_large=True)
    assert rep3.failures == [] and rep3.meta["resumed_skipped"] >= 1
    st = tr.read_state_entries(dst / tr.STATE_NAME)
    assert st["proj/model.rvt"]["verified"] and st["proj/model.rvt" + split.STATE_SEP + "001"]["verified"]


def test_state_part_records_survive_and_pending_parts_reverified(tmp_path):
    src = make_src(tmp_path)
    dst, m = tmp_path / "dst", tmp_path / "m.jsonl"
    rep = run(src, dst, split_large=True, manifest_out=m, verify_dest=False)  # 대상 검사 미룸(랜선/네트워크 흐름과 같은 동작)
    assert rep.meta["dest_verification"] == "deferred" and rep.failures == []
    assert {r.status for r in rep.results if r.src.endswith("model.rvt")} == {"pending_verify"}
    assert rows(m)["proj/model.rvt"]["parts"]
    assert mf.verify_against_manifest(m, dst).failures == []


# --------------------------------------------------------------------- 변조/누락 검출

def test_tampered_part_detected_by_verify_verify_only_and_join(tmp_path):
    src = make_src(tmp_path)
    dst, m = tmp_path / "dst", tmp_path / "m.jsonl"
    run(src, dst, split_large=True, manifest_out=m)
    victim = dst / "proj" / "model.rvt.pcm003"
    data = bytearray(victim.read_bytes())
    data[5] ^= 0xFF  # 크기는 그대로, 내용만 바꾼다
    victim.write_bytes(bytes(data))
    v = mf.verify_against_manifest(m, dst)
    bad = [r for r in v.results if r.status == MISMATCH]
    assert len(bad) == 1 and "003" in bad[0].error and "pcm003" in bad[0].error
    vo = run(src, dst, split_large=True, verify_only=True)
    bad2 = [r for r in vo.results if r.status == MISMATCH]
    assert len(bad2) == 1 and "003" in bad2[0].error
    out = tmp_path / "out"
    j = api.run_join(dst, out, manifest=m, dest_info_fn=ntfs)
    assert len(j.failures) == 1 and j.failures[0].status == MISMATCH and "003" in j.failures[0].error
    assert not (out / "proj" / "model.rvt").exists() and not list(out.rglob("*.pcmigrate-part"))  # 틀린 파일을 만들지 않는다


def test_strict_repairs_only_the_bad_part_and_keeps_bak(tmp_path):
    src = make_src(tmp_path)
    dst = tmp_path / "dst"
    run(src, dst, split_large=True)
    victim = dst / "proj" / "model.rvt.pcm002"
    data = bytearray(victim.read_bytes())
    data[0] ^= 0x55
    victim.write_bytes(bytes(data))
    other = dst / "proj" / "model.rvt.pcm004"
    ino = other.stat().st_ino
    rep = run(src, dst, split_large=True, strict=True)
    assert rep.failures == []
    assert b"".join((dst / "proj" / n).read_bytes() for n in part_files(dst / "proj")) == BIG
    assert (dst / "proj" / "model.rvt.pcm002.bak").read_bytes() == bytes(data)  # 어긋난 조각은 .bak 으로 보존
    assert other.stat().st_ino == ino  # 멀쩡한 조각은 다시 쓰지 않았다


def test_missing_part_reported_and_retry_failed_recopies(tmp_path):
    src = make_src(tmp_path)
    dst, m = tmp_path / "dst", tmp_path / "m.jsonl"
    run(src, dst, split_large=True, manifest_out=m)
    (dst / "proj" / "model.rvt.pcm005").unlink()
    v = mf.verify_against_manifest(m, dst)
    assert [r.status for r in v.failures] == [MISSING] and "005" in v.failures[0].error
    vo = run(src, dst, split_large=True, verify_only=True)
    assert [r.status for r in vo.failures] == [MISSING]
    rr = run(src, dst, split_large=True, retry_failed=True)
    assert rr.failures == [] and (dst / "proj" / "model.rvt.pcm005").exists()
    assert mf.verify_against_manifest(m, dst).failures == []


def test_existing_other_part_gets_bak_and_stale_parts_retired(tmp_path):
    src = make_src(tmp_path)
    dst = tmp_path / "dst"
    (dst / "proj").mkdir(parents=True)
    (dst / "proj" / "model.rvt.pcm002").write_bytes(b"OLD-CONTENT")  # 이름이 같은 다른 조각
    (dst / "proj" / "model.rvt.pcm009").write_bytes(b"STALE")  # 이번 조각 수(7)보다 번호가 큰 옛 조각
    rep = run(src, dst, split_large=True)
    assert rep.failures == []
    assert (dst / "proj" / "model.rvt.pcm002.bak").read_bytes() == b"OLD-CONTENT"
    assert (dst / "proj" / "model.rvt.pcm009.bak").read_bytes() == b"STALE" and not (dst / "proj" / "model.rvt.pcm009").exists()
    assert part_files(dst / "proj") == [f"model.rvt.pcm{i:03d}" for i in range(1, 8)]
    assert rep.meta["preserved_existing_count"] == 2
    assert {Path(x["backup"]).name for x in rep.meta["preserved_existing"]} == {"model.rvt.pcm002.bak", "model.rvt.pcm009.bak"}


def test_source_changed_during_copy_is_reported_not_silently_accepted(tmp_path, monkeypatch):
    src = make_src(tmp_path)
    real_stat = os.stat
    big = str(src / "proj" / "model.rvt")
    # 조사 때보다 파일이 커졌다고 가정: 쓰는 도중 원본에 바이트를 덧붙인다(원본을 바꾸는 쪽은 사용자 프로그램)
    orig_copy = split.copy_split

    def grow_then_copy(*a, **k):
        with open(big, "ab") as f:
            f.write(b"MORE")
        return orig_copy(*a, **k)

    monkeypatch.setattr(split, "copy_split", grow_then_copy)
    rep = run(src, tmp_path / "d", split_large=True)
    bad = [r for r in rep.failures if r.src.endswith("model.rvt")]
    assert len(bad) == 1 and "바뀐" in bad[0].error and bad[0].reason == "source_changed"
    assert real_stat(big).st_size == 2504  # 원본을 되돌리거나 지우지 않는다


# --------------------------------------------------------------------- 외장 드라이브 2단계(조각이 일반 파일로 보이는 원본)

def test_two_stage_external_drive_with_parts_and_use_manifest(tmp_path):
    src = make_src(tmp_path)
    ext, m = tmp_path / "ext", tmp_path / "m.jsonl"
    run(src, ext, split_large=True, manifest_out=m, dest_info_fn=fat32)  # 1단계: 구 PC -> FAT32 외장(분할)
    new = tmp_path / "newpc"
    rep = run(ext, new, use_manifest=m)  # 2단계: 외장 -> 새 PC(조각은 일반 파일)
    assert rep.failures == [] and not [r for r in rep.results if r.reason == "missing_in_source"]
    assert rep.meta["io"]["source_hash_reused_files"] >= 7  # 조각 해시를 목록에서 가져왔다
    out = tmp_path / "joined"
    j = api.run_join(new, out, manifest=m, dest_info_fn=ntfs)
    assert j.failures == [] and (out / "proj" / "model.rvt").read_bytes() == BIG


# --------------------------------------------------------------------- manifest 형식

def test_manifest_v1_still_readable_and_parts_validated(tmp_path):
    h = "a" * 64
    f = tmp_path / "old.jsonl"
    f.write_text(json.dumps({"format": mf.MANIFEST_FORMAT, "version": 1, "total_files": 1, "total_bytes": 3}) + "\n"
                 + json.dumps({"rel": "a/b.bin", "size": 3, "mtime_ns": 1, "sha256": h}) + "\n"
                 + json.dumps({"end": True, "files": 1, "bytes": 3, "errors": 0}) + "\n", encoding="utf-8")
    r = mf.ManifestReader(f)
    assert [x["rel"] for x in r.rows()] == ["a/b.bin"] and r.footer
    assert mf.MANIFEST_VERSION == 2 and 1 in mf.SUPPORTED_VERSIONS
    v = tmp_path / "bad.jsonl"
    hdr = json.dumps({"format": mf.MANIFEST_FORMAT, "version": 3, "total_files": 0, "total_bytes": 0})
    v.write_text(hdr + "\n", encoding="utf-8")
    with pytest.raises(mf.ManifestError):
        mf.ManifestReader(v)


@pytest.mark.parametrize("parts", [
    [{"name": "../evil.pcm001", "size": 3, "sha256": "a" * 64}],       # 경로 이탈
    [{"name": "b.bin.pcm002", "size": 3, "sha256": "a" * 64}],         # 번호가 1부터가 아님
    [{"name": "b.bin.pcm001", "size": 2, "sha256": "a" * 64}],         # 크기 합이 다름
    [{"name": "b.bin.pcm001", "size": 3, "sha256": "zz"}],             # 해시 형식
    [],
])
def test_manifest_bad_parts_rejected(tmp_path, parts):
    f = tmp_path / "m.jsonl"
    f.write_text(json.dumps({"format": mf.MANIFEST_FORMAT, "version": 2, "total_files": 1, "total_bytes": 3}) + "\n"
                 + json.dumps({"rel": "b.bin", "size": 3, "mtime_ns": 1, "sha256": "a" * 64, "parts": parts}) + "\n", encoding="utf-8")
    with pytest.raises(mf.ManifestError):
        list(mf.ManifestReader(f).rows())


# --------------------------------------------------------------------- join

def make_parts(tmp_path, with_manifest=True, with_state=True):
    src = make_src(tmp_path)
    dst, m = tmp_path / "dst", tmp_path / "m.jsonl"
    run(src, dst, split_large=True, manifest_out=m)
    if with_manifest:
        (dst / mf.DEFAULT_NAME).write_bytes(m.read_bytes())  # 폴더 안 목록은 자동으로 찾는다
    if not with_state:
        (dst / tr.STATE_NAME).unlink()
    return src, dst, m


def test_join_autodetects_manifest_in_folder_and_backs_up_existing(tmp_path):
    _src, dst, _m = make_parts(tmp_path)
    out = tmp_path / "out"
    (out / "proj").mkdir(parents=True)
    (out / "proj" / "model.rvt").write_bytes(b"older version")
    rep = api.run_join(dst, out, dest_info_fn=ntfs)
    assert rep.failures == [] and rep.meta["hash_source"] == "manifest" and rep.meta["joined_files"] == 1
    assert (out / "proj" / "model.rvt").read_bytes() == BIG
    assert (out / "proj" / "model.rvt.bak").read_bytes() == b"older version"
    assert rep.results[0].status == VERIFIED and rep.results[0].backup.endswith("model.rvt.bak")
    again = api.run_join(dst, out, dest_info_fn=ntfs)  # 같은 내용이면 건너뛰고 .bak 을 또 만들지 않는다
    assert again.results[0].status == SKIPPED_SAME and not (out / "proj" / "model.rvt.bak.1").exists()


def test_join_uses_state_file_part_hashes_without_manifest(tmp_path):
    _src, dst, _m = make_parts(tmp_path, with_manifest=False)
    rep = api.run_join(dst / "proj" / "model.rvt.pcm001", tmp_path / "out", dest_info_fn=ntfs)  # 첫 조각을 직접 지정
    assert rep.failures == [] and rep.meta["hash_source"] == "state"
    assert (tmp_path / "out" / "model.rvt").read_bytes() == BIG
    # 상태 파일의 조각 해시와 다르면 합치지 않는다
    victim = dst / "proj" / "model.rvt.pcm006"
    victim.write_bytes(b"\x00" * 400)
    bad = api.run_join(dst, tmp_path / "out2", dest_info_fn=ntfs)
    assert len(bad.failures) == 1 and "006" in bad.failures[0].error and not (tmp_path / "out2" / "proj" / "model.rvt").exists()


def test_join_without_any_hash_source_says_so(tmp_path):
    _src, dst, _m = make_parts(tmp_path, with_manifest=False, with_state=False)
    rep = api.run_join(dst, tmp_path / "out", dest_info_fn=ntfs)
    assert rep.failures == [] and rep.meta["hash_source"] == "none" and rep.meta["unverified_files"] == 1
    assert rep.results[0].status == COPIED and "해시 목록이 없어" in rep.results[0].error
    assert rep.meta["verification_complete"] is False
    assert (tmp_path / "out" / "proj" / "model.rvt").read_bytes() == BIG


def test_join_missing_part_stops_that_file_only(tmp_path):
    _src, dst, _m = make_parts(tmp_path)
    (dst / "proj" / "model.rvt.pcm004").unlink()
    rep = api.run_join(dst, tmp_path / "out", dest_info_fn=ntfs)
    assert len(rep.failures) == 1 and "빠진 조각 번호: 004" in rep.failures[0].error
    assert not (tmp_path / "out" / "proj" / "model.rvt").exists()


def test_join_precheck_fat32_and_space_before_writing(tmp_path):
    _src, dst, _m = make_parts(tmp_path)
    out = tmp_path / "out"
    import pcmigrate.fs as fsmod
    orig = fsmod.FAT4G
    fsmod.FAT4G = THRESH
    try:
        with pytest.raises(api.ApiError, match="FAT32"):
            api.run_join(dst, out, dest_info_fn=fat32)
    finally:
        fsmod.FAT4G = orig
    tiny = lambda p: fs.DestInfo(str(p), "fixed", "NTFS", 1000, 1, "D:")  # noqa: E731
    with pytest.raises(api.ApiError, match="여유 공간이 부족"):
        api.run_join(dst, out, dest_info_fn=tiny)
    assert not out.exists() or not any(out.rglob("*"))  # 사전 점검에서 막았으므로 아무것도 쓰지 않았다


def test_join_dry_run_and_errors(tmp_path):
    _src, dst, _m = make_parts(tmp_path)
    rep = api.run_join(dst, tmp_path / "out", dry_run=True, dest_info_fn=ntfs)
    assert rep.dry_run and rep.results[0].status == "would_copy" and not (tmp_path / "out").exists()
    with pytest.raises(api.ApiError):
        api.run_join(dst / "small.txt", tmp_path / "o")  # 조각이 아닌 파일
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(api.ApiError, match="찾지 못"):
        api.run_join(empty, tmp_path / "o")


def test_join_hash_hits_whole_even_when_part_hashes_missing(tmp_path):
    """목록에는 전체 해시만 있고(조각 해시 없음) 전체가 다르면 합치지 않는다."""
    _src, dst, m = make_parts(tmp_path)
    (dst / "proj" / "model.rvt.pcm007").write_bytes(b"Z" * 100)  # 크기는 같고 내용이 다른 마지막 조각
    rep = api.run_join(dst, tmp_path / "out", manifest=m, dest_info_fn=ntfs)
    assert len(rep.failures) == 1 and rep.failures[0].status == MISMATCH


# --------------------------------------------------------------------- CLI / API

def test_cli_join_and_transfer_option(tmp_path, capsys, monkeypatch):
    src = make_src(tmp_path)
    dst, m = tmp_path / "dst", tmp_path / "m.jsonl"
    monkeypatch.setattr(cli, "transfer", lambda s, d, **kw: transfer(s, d, runner=FakeRobocopy(), dest_info_fn=ntfs, **kw))
    code = cli.main(["transfer", str(src), str(dst), "--split-large", "--manifest-out", str(m)])
    cap = capsys.readouterr()
    assert code == 0 and "조각 7개" in cap.out and "pcmigrate join" in cap.out
    assert part_files(dst / "proj")
    out = tmp_path / "out"
    monkeypatch.setattr(split.fs, "describe_destination", ntfs)
    code = cli.main(["join", str(dst), "--output", str(out), "--manifest", str(m), "--report", str(tmp_path / "r.json")])
    cap = capsys.readouterr()
    assert code == 0 and "합치기를 마쳤습니다" in cap.out and "해시 목록" in cap.out
    assert (out / "proj" / "model.rvt").read_bytes() == BIG
    data = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert data["joined_files"] == 1 and data["success"] is True
    # 조각이 없는 폴더는 오류 코드와 쉬운 문구
    empty = tmp_path / "e"
    empty.mkdir()
    assert cli.main(["join", str(empty), "--output", str(tmp_path / "o2")]) == 1
    assert "찾지 못" in capsys.readouterr().err


def test_api_run_transfer_passes_split_large_only_when_on(tmp_path):
    seen = {}

    class D(api.Deps):
        pass

    d = api.Deps(transfer=lambda s, t, **kw: seen.update(kw) or "ok")
    api.run_transfer(tmp_path, tmp_path / "x", deps=d)
    assert "split_large" not in seen
    api.run_transfer(tmp_path, tmp_path / "x", deps=d, split_large=True)
    assert seen["split_large"] is True
    import inspect
    assert inspect.signature(api.run_transfer).parameters["split_large"].default is False


def test_part_names_widen_after_999():
    assert split.part_suffix(1) == ".pcm001" and split.part_suffix(1000) == ".pcm1000"
    assert split.PART_RE.match("a.rvt.pcm1000") and not split.PART_RE.match("a.rvt.pcm01")
