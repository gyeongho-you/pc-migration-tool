"""28c: 내 파일 자동 찾기(find_data), 다중 원본 전송(multi_transfer/data_api), 정크 제외(junk) 시험.

가짜 환경만 쓴다: 임시 폴더 트리 + 주입 가능한 루트/사용자 폴더/규칙 경로/속성/시계. 실제 드라이브 전체는 훑지 않는다.
"""
import fnmatch
import functools
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from pcmigrate import cli, find_data as fd, junk, multi_transfer as mt, transfer as tr
from pcmigrate.api import ApiError, Deps, run_transfer
from pcmigrate.cancel import Cancelled
from pcmigrate.data_api import preview_plan, run_find_data, run_transfer_selection
from pcmigrate.fs import DestInfo
from pcmigrate.manifest import ManifestReader, verify_against_manifest
from pcmigrate.transfer import TransferError

BIG = DestInfo("x", "fixed", "NTFS", 10 ** 12, 1, "C:")


def put(path: Path, data: bytes = b"x" * 10):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def snapshot(root: Path):
    return {str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}


def make_pc(tmp_path: Path) -> Path:
    """가짜 드라이브: 프로젝트, 사진, 시스템 흉내, 휴지통, node_modules, 사용자 프로필(AppData)."""
    d = tmp_path / "fakeD"
    put(d / "Work" / "A" / "plan.dwg", b"d" * 100)
    put(d / "Work" / "A" / "model.rvt", b"r" * 300)
    put(d / "Work" / "A" / "render.jpg", b"j" * 50)
    put(d / "Work" / "A" / "sub" / "spec.pdf", b"p" * 20)
    put(d / "Work" / "A" / "note.log", b"l" * 5)
    put(d / "Work" / "B" / "site1.jpg", b"1" * 40)
    put(d / "Work" / "B" / "site2.png", b"2" * 40)
    put(d / "Photos" / "2024" / "x.cr2", b"c" * 70)
    put(d / "Videos" / "clip.mp4", b"v" * 90)
    put(d / "Windows" / "System32" / "sys.dwg", b"s" * 999)
    put(d / "Program Files" / "App" / "tool.dwg", b"s" * 999)
    put(d / "$RECYCLE.BIN" / "S-1" / "old.dwg", b"s" * 999)
    put(d / "System Volume Information" / "z.dwg", b"s" * 999)
    put(d / "Work" / "A" / "node_modules" / "pkg" / "i.png", b"n" * 999)
    put(d / "Users" / "me" / "AppData" / "Local" / "cache.dwg", b"a" * 999)
    put(d / "Users" / "Default" / "ntuser.jpg", b"a" * 999)
    put(d / "tiny" / "a.txt", b"t")  # 문서 1개뿐 -> 후보 아님(직접 3개 미만) ... 아래 깊이 규칙은 별도 시험
    return d


def find(d, **kw):
    kw.setdefault("user_folders", {})
    kw.setdefault("exclude_paths", [])
    return fd.find_data([d], **kw)


# --------------------------------------------------------------------- 분류/후보 묶기

def test_classify_table_samples():
    assert fd.classify_name("a.DWG") == ("design", "A")
    assert fd.classify_name("a.rvt")[0] == "design" and fd.classify_name("m.3dm")[0] == "design"
    assert fd.classify_name("p.CR2")[0] == "photo" and fd.classify_name("v.mkv")[0] == "video"
    assert fd.classify_name("a.hwpx")[0] == "doc" and fd.classify_name("a.7z")[0] == "archive"
    assert fd.classify_name("a.bin", 10) is None
    assert fd.classify_name("a.bin", fd.LARGE_OTHER_BYTES) == ("other", "C")
    assert fd.classify_name("setup.exe", fd.LARGE_OTHER_BYTES) is None  # 실행 파일은 큰 기타에서 제외
    for cat, tbl in (("design", fd.DESIGN_EXT), ("photo", fd.PHOTO_EXT), ("video", fd.VIDEO_EXT), ("doc", fd.DOC_EXT)):
        assert all(g in "ABC" for g in tbl.values()) and all(e.startswith(".") and e == e.lower() for e in tbl)
    assert not (set(fd.DESIGN_EXT) & set(fd.PHOTO_EXT))  # 한 확장자는 한 분류


def test_classify_stage45_new_extensions():
    for n in ("a.STEP", "a.stp", "a.iges", "a.igs", "a.sat", "s.las", "s.LAZ", "s.e57", "m.ply", "t.osgb", "g.kml", "g.kmz", "g.shp"):
        assert fd.classify_name(n)[0] == "design", n
    for n in ("a.svg", "a.eps"):
        assert fd.classify_name(n)[0] == "photo", n
    assert ".geotiff" not in fd.PHOTO_EXT  # 실제 확장자는 .tif/.tiff 라 효과가 없는 항목은 뺐다
    assert fd.classify_name("s.las") == ("design", "A") and fd.classify_name("a.svg") == ("photo", "C")
    # 보류한 확장자는 그대로 미분류(작으면 None), .obj 는 큰 기타 제외 목록 유지
    for n in ("a.xyz", "a.pts", "a.tfw", "a.dbf"):
        assert fd.classify_name(n, 10) is None, n
    assert ".obj" in fd.OTHER_EXCLUDED_EXT and fd.classify_name("a.obj", fd.LARGE_OTHER_BYTES) is None
    assert fd.classify_name("setup.exe", fd.LARGE_OTHER_BYTES) is None and fd.classify_name("a.tmp", fd.LARGE_OTHER_BYTES) is None


def test_find_groups_projects_and_excludes_system(tmp_path):
    d = make_pc(tmp_path)
    before = snapshot(d)
    res = find(d)
    assert snapshot(d) == before  # 읽기 전용
    paths = {Path(p.path).relative_to(d).as_posix(): p for p in res.projects}
    assert set(paths) == {"Work/A", "Work/B", "Photos/2024", "Videos"}
    a = paths["Work/A"]
    assert a.kind == "project" and a.categories["design"] == {"files": 2, "bytes": 400}
    assert a.categories["photo"]["files"] == 1 and a.categories["doc"]["files"] == 1
    assert a.total_files == 5 + 0 and a.total_bytes == 100 + 300 + 50 + 20 + 5  # node_modules 제외, .log 는 분류 안 되지만 폴더 합계에는 포함
    assert a.top_extensions[0] in (".dwg", ".rvt", ".jpg", ".pdf") and a.latest_mtime and a.basis
    assert "node_modules" in " ".join(e["path"] for e in res.excluded_examples)
    # 시스템/휴지통/프로필 구간은 후보에도 합계에도 없다
    assert all("Windows" not in p.path and "Program Files" not in p.path for p in res.projects)
    drive = res.drives[0]
    assert drive["categories"]["design"]["files"] == 2  # 시스템 폴더의 dwg 는 세지 않음
    assert res.complete and res.files_seen > 0
    assert res.projects == sorted(res.projects, key=lambda p: -p.total_bytes)


def test_find_never_selects_and_selection_round_trip(tmp_path):
    d = make_pc(tmp_path)
    res = find(d)
    out = fd.write_selection(res, tmp_path / "out" / "sel.json")
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["format"] == fd.SELECTION_FORMAT and data["items"]
    assert all(i["include"] is False for i in data["items"])  # 자동 선택 금지
    items = fd.load_selection(out)
    assert [i.path for i in items] == [p.path for p in res.user_folders + res.projects]
    assert items[0].total_bytes is not None and not any(i.include for i in items)
    # 다시 쓰면 기존 파일은 .bak 으로 보존
    fd.write_selection(res, out)
    assert (tmp_path / "out" / "sel.json.bak").is_file()


@pytest.mark.parametrize("bad", [[], {"format": "x"}, {"format": fd.SELECTION_FORMAT, "version": 9, "items": []},
                                  {"format": fd.SELECTION_FORMAT, "version": 1, "items": [{"path": "C:\\a", "include": "yes"}]},
                                  {"format": fd.SELECTION_FORMAT, "version": 1, "items": [{"include": True}]},
                                  {"format": fd.SELECTION_FORMAT, "version": 1, "items": "no"}])
def test_selection_validation(bad):
    with pytest.raises(fd.SelectionFileError):
        fd.parse_selection(bad)


def test_selection_file_errors(tmp_path):
    with pytest.raises(fd.SelectionFileError):
        fd.load_selection(tmp_path / "nope.json")
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(fd.SelectionFileError):
        fd.load_selection(p)


def test_container_folder_descends_and_depth_limit(tmp_path):
    d = tmp_path / "r"
    for i in range(3):
        put(d / "container" / f"p{i}" / "a.dwg")
    put(d / "deep" / "l1" / "l2" / "l3" / "l4" / "l5" / "a.dwg")
    res = find(d, max_depth=3)
    got = {Path(p.path).relative_to(d).as_posix(): p.basis for p in res.projects}
    assert {"container/p0", "container/p1", "container/p2"} <= set(got)  # 직접 데이터가 없는 컨테이너는 내려감
    assert "deep/l1/l2" in got and "한도" in got["deep/l1/l2"]  # 깊이 한도에서 묶음


def test_categories_and_min_size_filters(tmp_path):
    d = make_pc(tmp_path)
    only_video = find(d, categories=["video"])
    assert [Path(p.path).name for p in only_video.projects] == ["Videos"]
    big = find(d, min_size=300)
    assert {Path(p.path).name for p in big.projects} == {"A"}
    with pytest.raises(ValueError):
        find(d, categories=["bogus"])
    with pytest.raises(ValueError):
        find(d, categories=[])


def test_other_large_category_opt_in(tmp_path):
    d = tmp_path / "r"
    put(d / "proj" / "blob.bin", b"b" * 50)
    assert find(d, other_large_bytes=10).projects == []  # 기본 분류에는 other 가 없다
    got = find(d, categories=["other"], other_large_bytes=10)
    assert [p.categories for p in got.projects] == [{"other": {"files": 1, "bytes": 50}}]


def test_user_folders_are_separate_items_and_not_double_counted(tmp_path):
    d = make_pc(tmp_path)
    docs = d / "Users" / "me" / "Documents"
    put(docs / "memo.docx", b"m" * 30)
    put(docs / "Temp" / "x.txt", b"t")  # 사용자 폴더 안의 Temp 는 루트 직속 규칙이 적용되지 않는다
    res = fd.find_data([d], user_folders={"documents": str(docs)}, exclude_paths=[])
    assert [(u.label, u.kind, u.total_files) for u in res.user_folders] == [("documents", "user_folder", 2)]
    assert all(not p.path.startswith(str(docs)) for p in res.projects)
    res2 = fd.find_data([d], user_folders={}, exclude_paths=[])  # 사용자 폴더를 따로 빼지 않고 루트 훑기에 맡긴 경우
    assert res.drives[0]["files"] == res2.drives[0]["files"]  # 같은 파일을 두 번 세지 않는다(합계가 같음)


def test_explicit_roots_do_not_pull_in_real_user_folders(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(fd, "default_user_folders", lambda *a, **k: called.append(1) or {})
    find_roots = fd.find_data([make_pc(tmp_path)], exclude_paths=[])
    assert not called and find_roots.user_folders == []


def test_rule_covered_paths_are_skipped(tmp_path):
    d = make_pc(tmp_path)
    res = fd.find_data([d], user_folders={}, exclude_paths=[str(d / "Work" / "B")])
    assert "B" not in {Path(p.path).name for p in res.projects}
    assert res.excluded.get("설정 규칙이 담당하는 경로") == 1


def test_rule_covered_paths_from_rules(tmp_path, monkeypatch):
    from pcmigrate import envpaths
    from pcmigrate.rules import PathRule, Rule
    base = tmp_path / "cfg"
    (base / "AppX 1").mkdir(parents=True)
    monkeypatch.setenv("FAKE28C", str(base))
    rule = Rule("x", "X", paths=(PathRule("%FAKE28C%\\AppX *", "x/a"), PathRule("%FAKE28C%\\file.ini", "x/f", "file"),
                                  PathRule("%NOPE28C%\\z", "x/z")))
    assert [Path(p) for p in fd.rule_covered_paths([rule])] == [base / "AppX 1"]
    envpaths.set_overrides(None)


def test_links_not_followed(tmp_path):
    d = tmp_path / "r"
    put(d / "real" / "a.dwg")
    put(d / "outside" / "secret.dwg")
    link = d / "lnk"
    made = False
    try:
        os.symlink(d / "outside", link, target_is_directory=True)
        made = True
    except (OSError, NotImplementedError):
        if os.name == "nt":
            made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(d / "outside")], capture_output=True).returncode == 0
    if not made:
        pytest.skip("링크를 만들 수 없는 환경")
    res = find(d)
    assert res.skipped_links >= 1
    assert not any(Path(p.path).name == "lnk" for p in res.projects)
    # outside 는 실제 폴더로 따로 훑히지만 lnk 를 통해 두 번 세지 않는다
    assert sum(p.categories["design"]["files"] for p in res.projects) == 2


def test_cloud_only_counted_separately_and_not_opened(tmp_path):
    d = tmp_path / "r"
    put(d / "p" / "local.dwg", b"l" * 10)
    put(d / "p" / "cloud1.jpg", b"c" * 1000)
    put(d / "p" / "cloud2.jpg", b"c" * 1000)

    def attrs(path):
        return 0x00400000 if "cloud" in path.name else 0

    res = find(d, attr_getter=attrs)
    p = res.projects[0]
    assert p.cloud_only_files == 2 and p.cloud_only_bytes == 2000
    assert p.categories == {"design": {"files": 1, "bytes": 10}} and p.total_bytes == 10  # 클라우드 전용은 합계에 넣지 않는다
    assert res.cloud_only_files == 2 and res.drives[0]["cloud_only_files"] == 2


def test_read_error_is_recorded_and_scan_continues(tmp_path, monkeypatch):
    d = make_pc(tmp_path)
    bad = str(d / "Work" / "B")
    real = os.scandir

    def scandir(path):
        if str(path).replace("\\\\?\\", "").rstrip("\\") == bad:
            raise PermissionError(13, "denied", bad)
        return real(path)

    monkeypatch.setattr(fd.os, "scandir", scandir)
    res = find(d)
    assert res.error_count == 1 and res.errors[0]["code"] == "permission" and res.errors[0]["path"] == bad
    assert {Path(p.path).name for p in res.projects} >= {"A", "Videos"} and res.complete


def test_time_limit_returns_partial_and_cancel_raises(tmp_path):
    d = make_pc(tmp_path)
    ticks = iter(range(0, 10_000))
    res = find(d, time_limit=3, clock=lambda: next(ticks))
    assert res.complete is False and res.stop_reason == "time"
    assert any("시간 상한" in line for line in fd.summary_lines(res))
    n = [0]

    def cancel():
        n[0] += 1
        return n[0] > 3

    with pytest.raises(Cancelled):
        find(d, cancel=cancel)


def test_progress_callbacks_fire(tmp_path):
    d = make_pc(tmp_path)
    msgs, infos = [], []
    find(d, progress=msgs.append, progress_info=infos.append, report_every=0, clock=iter(range(10_000)).__next__)
    assert msgs and infos and infos[0].phase == "find" and infos[-1].done_files >= 0


def test_list_drives_injection():
    kinds = {"C:\\": "fixed", "D:\\": "removable", "E:\\": "network", "F:\\": "cdrom", "G:\\": "fixed"}
    mask = sum(1 << i for i in (2, 3, 4, 5, 6))
    assert [r for r, _ in fd.list_drives(mask_fn=lambda: mask, kind_fn=kinds.get)] == ["C:\\", "G:\\"]
    assert [r for r, _ in fd.list_drives(True, mask_fn=lambda: mask, kind_fn=kinds.get)] == ["C:\\", "D:\\", "G:\\"]
    assert fd.list_drives(mask_fn=lambda: (_ for _ in ()).throw(OSError())) == []


def test_find_default_roots_use_injected_drives(tmp_path):
    d = make_pc(tmp_path)
    res = fd.find_data(None, drives_fn=lambda: [(str(d), "fixed")], user_folders={}, exclude_paths=[])
    assert res.roots == [str(d)] and res.projects


def test_cli_find_data_json_and_selection(tmp_path, capsys):
    d = make_pc(tmp_path)
    sel = tmp_path / "keep" / "sel.json"
    assert cli.main(["find-data", "--roots", str(d), "--min-size", "1KB", "--json", "--output-selection", str(sel)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["projects"] == [] and out["min_size"] == 1024
    assert cli.main(["find-data", "--roots", str(d), "--categories", "design,photo", "--output-selection", str(sel)]) == 0
    text = capsys.readouterr().out
    assert "프로젝트/자료 폴더 후보" in text and "근거:" in text
    assert all(i["include"] is False for i in json.loads(sel.read_text(encoding="utf-8"))["items"])
    assert cli.main(["find-data", "--roots", str(d), "--categories", "zzz"]) == 1
    with pytest.raises(SystemExit):
        cli.main(["find-data", "--min-size", "abc"])


def test_find_data_refuses_selection_inside_scanned_folder(tmp_path):
    d = make_pc(tmp_path)
    with pytest.raises(ApiError):
        run_find_data([d], user_folders={}, exclude_paths=[], output_selection=d / "Work" / "sel.json")
    assert not (d / "Work" / "sel.json").exists()


# --------------------------------------------------------------------- 정크 목록

def test_junk_table_is_careful():
    pats = {r.pattern.lower() for r in junk.JUNK_RULES}
    assert "*.bak" not in pats and "*.sv$" not in pats and ".git" not in pats  # 사용자 작업일 수 있는 것은 넣지 않는다
    assert all(r.grade in ("A", "B") and r.reason for r in junk.JUNK_RULES)
    spec, full = junk.make_spec(), junk.make_spec(True)
    assert spec.file_group("Thumbs.db") == "os-meta" and spec.file_group("~$plan.docx") == "office-lock"
    assert spec.file_group("a.DWL") == "cad-temp" and spec.file_group("a.dwl2") == "cad-temp" and spec.file_group("x.ac$") == "cad-temp"
    assert spec.file_group("plan.bak") is None and spec.file_group("plan.dwg") is None and spec.file_group("a.sv$") is None
    assert spec.file_group("m.0001.rvt") is None and full.file_group("m.0001.rvt") == junk.OPTIONAL_GROUP
    assert full.file_group("m.rvt") is None and full.dir_group("Proj_backup") == junk.OPTIONAL_GROUP and spec.dir_group("Proj_backup") is None
    assert spec.classify_rel("a/$RECYCLE.BIN/x.dwg") == "os-meta" and spec.classify_rel("a/node_modules/b/c.js") == "deps"
    assert spec.classify_rel("a/b.dwg") is None


def test_base_args_default_unchanged_and_xd_before_xf():
    base = tr.base_args(Path("S"), Path("D"), None, True)
    assert "/XD" not in base and base[-2:] == ["/XF", tr.OWN_PATTERN]
    args = tr.base_args(Path("S"), Path("D"), None, False, xf_extra=("Thumbs.db",), xd=("$RECYCLE.BIN",))
    assert args.index("/XD") < args.index("/XF") and args[args.index("/XF") + 1:] == [tr.OWN_PATTERN, "Thumbs.db"]
    tr.validate_args(args)


class FakeRobo:
    """robocopy 흉내: /XF /XD 를 해석해 shutil 로 복사한다. 호출 기록을 남긴다."""
    supports_lines = False

    def __init__(self, corrupt=None):
        self.calls, self.corrupt = [], corrupt

    @staticmethod
    def _list(args, flag):
        if flag not in args:
            return []
        out = []
        for a in args[args.index(flag) + 1:]:
            if a.startswith("/"):
                break
            out.append(a.lower())
        return out

    def __call__(self, args):
        self.calls.append(list(args))
        if "/L" in args:
            return 1, ""
        src, dst = Path(args[1]), Path(args[2])
        xf, xd = self._list(args, "/XF"), self._list(args, "/XD")
        for root, dirs, files in os.walk(src):
            dirs[:] = [x for x in dirs if not any(fnmatch.fnmatchcase(x.lower(), p) for p in xd)]
            rel = Path(root).relative_to(src)
            (dst / rel).mkdir(parents=True, exist_ok=True)
            for n in files:
                if any(fnmatch.fnmatchcase(n.lower(), p) for p in xf):
                    continue
                shutil.copy2(Path(root) / n, dst / rel / n)
                if self.corrupt and n == self.corrupt:
                    with open(dst / rel / n, "ab") as f:
                        f.write(b"X")
        return 1, ""


def deps_with(robo, free=10 ** 12):
    info = DestInfo("x", "fixed", "NTFS", free, 1, "C:")
    return Deps(transfer=functools.partial(tr.transfer, runner=robo, dest_info_fn=lambda p: info, keep_awake=False))


def junk_src(tmp_path):
    s = tmp_path / "junksrc"
    put(s / "a.dwg", b"a" * 10)
    put(s / "Thumbs.db", b"t" * 7)
    put(s / "desktop.ini", b"i" * 3)
    put(s / "~$memo.docx", b"l" * 5)
    put(s / "x.tmp", b"m" * 4)
    put(s / "sub" / "b.rvt", b"b" * 10)
    put(s / "sub" / "b.0001.rvt", b"B" * 12)
    put(s / "sub" / "b.dwl", b"w" * 2)
    put(s / "sub" / "plan.bak", b"k" * 9)  # 사용자 백업은 제외하지 않는다
    put(s / "$RECYCLE.BIN" / "S-1" / "junk.dwg", b"r" * 8)
    put(s / "web" / "node_modules" / "p" / "i.js", b"n" * 6)
    return s


def test_exclude_junk_default_off_copies_everything(tmp_path):
    s = junk_src(tmp_path)
    robo = FakeRobo()
    rep = run_transfer(s, tmp_path / "dst", deps=deps_with(robo), keep_awake=False)
    assert "junk_excluded" not in rep.to_dict() and not rep.failures
    assert (tmp_path / "dst" / "Thumbs.db").exists()
    assert "/XD" not in robo.calls[0]


def test_exclude_junk_excludes_counts_and_verifies(tmp_path):
    s = junk_src(tmp_path)
    robo = FakeRobo()
    rep = run_transfer(s, tmp_path / "dst", deps=deps_with(robo), keep_awake=False, exclude_junk=True)
    d = rep.to_dict()
    assert not rep.failures and d["failed_count"] == 0  # 복사 안 한 파일을 '대상에 없다'고 실패 처리하지 않음
    got = {p.relative_to(tmp_path / "dst").as_posix() for p in (tmp_path / "dst").rglob("*") if p.is_file() and not p.name.startswith(".pcmigrate")}
    assert got == {"a.dwg", "sub/b.rvt", "sub/b.0001.rvt", "sub/plan.bak"}  # Revit 백업 사본/.bak 은 기본 포함
    j = d["junk_excluded"]
    assert j["files"] == 7 and j["bytes"] == 7 + 3 + 5 + 4 + 2 + 8 + 6 and j["backup_copies"] is False
    assert set(j["by_group"]) == {"os-meta", "office-lock", "temp", "cad-temp", "deps"}
    assert d["total_files"] == 4 and d["total_bytes"] == 10 + 10 + 12 + 9
    lines = tr_lines(rep, s, tmp_path / "dst")
    assert any("정크 제외: 7개" in x for x in lines)
    assert "/XD" in robo.calls[0] and "$RECYCLE.BIN" in robo.calls[0]


def tr_lines(rep, s, dst):
    from pcmigrate import summary
    return summary.transfer_lines(rep.to_dict(), rep, str(s), str(dst))


def test_exclude_backup_copies_option(tmp_path):
    s = junk_src(tmp_path)
    put(s / "Proj_backup" / "z.dat", b"z" * 5)
    rep = run_transfer(s, tmp_path / "dst", deps=deps_with(FakeRobo()), keep_awake=False, exclude_junk=True, junk_backup_copies=True)
    names = {p.name for p in (tmp_path / "dst").rglob("*") if p.is_file()}
    assert "b.0001.rvt" not in names and "z.dat" not in names and "b.rvt" in names and "plan.bak" in names
    assert rep.to_dict()["junk_excluded"]["backup_copies"] is True and not rep.failures
    with pytest.raises(ApiError):
        run_transfer(s, tmp_path / "d2", deps=deps_with(FakeRobo()), junk_backup_copies=True)


def test_exclude_junk_dry_run_lists_junk_count_without_writing(tmp_path):
    s = junk_src(tmp_path)
    rep = run_transfer(s, tmp_path / "dst", deps=deps_with(FakeRobo()), keep_awake=False, exclude_junk=True, dry_run=True)
    d = rep.to_dict()
    assert d["junk_excluded"]["files"] == 7 and d["summary"].get("would_copy") == 4
    assert not (tmp_path / "dst").exists()


def test_exclude_junk_with_cloud_only_uses_per_dir_runs(tmp_path):
    s = junk_src(tmp_path)
    put(s / "sub" / "cloud.jpg", b"c" * 4)
    robo = FakeRobo()

    def attr(p):
        return 0x00400000 if p.name == "cloud.jpg" else 0

    info = DestInfo("x", "fixed", "NTFS", 10 ** 12, 1, "C:")
    rep = tr.transfer(s, tmp_path / "dst", runner=robo, dest_info_fn=lambda p: info, attr_getter=attr, keep_awake=False,
                      junk_spec=junk.make_spec())
    assert len(robo.calls) > 1 and not any("RECYCLE" in " ".join(c[1:3]) for c in robo.calls)  # 정크 폴더로는 내려가지 않는다
    assert not rep.failures and rep.to_dict()["junk_excluded"]["files"] == 7


@pytest.mark.skipif(os.name != "nt" or shutil.which("robocopy") is None, reason="실제 robocopy 는 Windows 에서만")
def test_real_robocopy_exclude_junk_end_to_end(tmp_path):
    s = junk_src(tmp_path)
    before = snapshot(s)
    rep = run_transfer(s, tmp_path / "dst", keep_awake=False, exclude_junk=True)
    assert snapshot(s) == before  # 원본 불변
    d = rep.to_dict()
    assert not rep.failures and d["success"] and d["junk_excluded"]["files"] == 7
    got = {p.relative_to(tmp_path / "dst").as_posix() for p in (tmp_path / "dst").rglob("*") if p.is_file() and not p.name.startswith(".pcmigrate")}
    assert got == {"a.dwg", "sub/b.rvt", "sub/b.0001.rvt", "sub/plan.bak"}


# --------------------------------------------------------------------- 단일 호출 불변

def test_single_src_call_forwards_no_new_kwargs(tmp_path):
    seen = {}

    def spy(src, dst, **kw):
        seen.update(kw)
        from pcmigrate.copier import Report
        return Report(False)

    s = put(tmp_path / "s" / "a.dwg").parent
    run_transfer(s, tmp_path / "d", deps=Deps(transfer=spy))
    assert "junk_spec" not in seen and set(seen) == {"dry_run", "include_cloud", "log_path", "warn", "strict"}
    run_transfer(s, tmp_path / "d", deps=Deps(transfer=spy), exclude_junk=True)
    assert seen["junk_spec"].rules == junk.make_spec().rules


def test_cli_single_src_still_works_and_dst_required(tmp_path, capsys):
    s = put(tmp_path / "s" / "a.dwg", b"zz").parent
    assert cli.main(["transfer", str(s), str(tmp_path / "d"), "--dry-run"]) in (0, 1)
    capsys.readouterr()
    assert cli.main(["transfer", str(s)]) == 2


# --------------------------------------------------------------------- 다중 원본

def test_dest_rel_converts_drive_and_unc():
    assert mt.dest_rel("D:\\프로젝트\\A") == "D\\프로젝트\\A"
    assert mt.dest_rel("e:\\") == "e"
    assert mt.dest_rel("\\\\srv\\share\\x") == "UNC\\srv\\share\\x"
    assert mt.dest_rel("C:\\a") == "C\\a"


def sel_tree(tmp_path):
    a, b = tmp_path / "pc" / "Work" / "A", tmp_path / "pc" / "Photos"
    put(a / "plan.dwg", b"a" * 30)
    put(a / "sub" / "m.rvt", b"m" * 50)
    put(b / "x.jpg", b"x" * 20)
    put(b / "Thumbs.db", b"t" * 3)
    return a, b


def run_sel(items, dst, robo=None, **kw):
    kw.setdefault("keep_awake", False)
    return run_transfer_selection(items, dst, deps=deps_with(robo or FakeRobo()), **kw)


def test_multi_preserves_structure_and_aggregates(tmp_path):
    a, b = sel_tree(tmp_path)
    before = snapshot(tmp_path / "pc")
    dst = tmp_path / "out"
    seen = []
    rep = run_sel([str(a), str(b)], dst, on_item=lambda n, t, s, st, r: seen.append((n, t, st)))
    assert snapshot(tmp_path / "pc") == before  # 원본 불변
    ra, rb = dst / mt.dest_rel(str(a)), dst / mt.dest_rel(str(b))
    assert (ra / "plan.dwg").read_bytes() == b"a" * 30 and (ra / "sub" / "m.rvt").is_file() and (rb / "x.jpg").is_file()
    d = rep.to_dict()
    assert d["total_items"] == 2 and d["items_ok"] == 2 and d["total_files"] == 4 and d["failed_count"] == 0
    assert d["summary"]["verified"] == 4 and d["verification_complete"] and d["success"]
    assert seen == [(1, 2, "start"), (1, 2, "ok"), (2, 2, "start"), (2, 2, "ok")]
    assert (dst / mt.JOURNAL_NAME).is_file()
    assert (ra / tr.STATE_NAME).is_file()  # 상태 파일은 항목 폴더 안에


def test_multi_overlap_and_duplicates_are_cleaned(tmp_path):
    a, _b = sel_tree(tmp_path)
    parent = a.parent
    robo = FakeRobo()
    rep = run_sel([str(a), str(parent), str(parent).upper() if os.name == "nt" else str(parent), str(a / "sub")], tmp_path / "out", robo)
    d = rep.to_dict()
    assert d["total_items"] == 1 and len(d["dropped"]) == 3
    assert {x["covered_by"] for x in d["dropped"]} == {str(parent)}
    assert len([c for c in robo.calls if "/L" not in c]) == 1  # 부모 한 번만 복사
    assert (tmp_path / "out" / mt.dest_rel(str(parent)) / "A" / "plan.dwg").is_file()


def test_multi_only_included_items_and_empty_selection(tmp_path):
    a, b = sel_tree(tmp_path)
    items = [fd.SelectedItem(str(a), True), fd.SelectedItem(str(b), False)]
    rep = run_sel(items, tmp_path / "out")
    assert [Path(i.src) for i in rep.items] == [a]
    with pytest.raises(TransferError):
        run_sel([fd.SelectedItem(str(a), False)], tmp_path / "o2")
    assert not (tmp_path / "o2").exists()


def test_multi_rejects_dest_inside_source_and_missing_source(tmp_path):
    a, _b = sel_tree(tmp_path)
    with pytest.raises(TransferError, match="원본"):
        run_sel([str(a)], a / "backup")
    with pytest.raises(TransferError, match="원본"):
        run_sel([str(a)], a)
    with pytest.raises(TransferError, match="없습니다"):
        run_sel([str(a), str(tmp_path / "nope")], tmp_path / "out")
    assert not (a / "backup").exists() and not (tmp_path / "out").exists()


def test_multi_space_precheck(tmp_path):
    a, b = sel_tree(tmp_path)
    robo = FakeRobo()
    deps = deps_with(robo)
    small = DestInfo("x", "fixed", "NTFS", 50, 1, "C:")  # 선택 합계(100B)보다 작은 여유
    with pytest.raises(TransferError, match="여유 공간"):
        run_transfer_selection([str(a), str(b)], tmp_path / "out", deps=deps, keep_awake=False, dest_info_fn=lambda p: small)
    assert not robo.calls and not (tmp_path / "out").exists()
    warns = []
    rep = run_transfer_selection([str(a), str(b)], tmp_path / "out", deps=deps, keep_awake=False, dry_run=True,
                                 dest_info_fn=lambda p: small, warn=warns.append)
    assert rep.meta["space_blocked"] and any("여유 공간" in w for w in warns)
    cd = DestInfo("x", "cdrom", None, None, None, "E:")
    with pytest.raises(TransferError, match="CD"):
        run_transfer_selection([str(a)], tmp_path / "o3", deps=deps, keep_awake=False, dest_info_fn=lambda p: cd)


def test_multi_space_precheck_uses_estimate_from_selection_and_existing(tmp_path):
    a, b = sel_tree(tmp_path)
    items = [fd.SelectedItem(str(a), True, total_bytes=10_000)]
    info = DestInfo("x", "fixed", "NTFS", 5_000, 1, "C:")
    with pytest.raises(TransferError, match="여유 공간"):
        run_transfer_selection(items, tmp_path / "out", deps=deps_with(FakeRobo()), keep_awake=False, dest_info_fn=lambda p: info)
    # 이미 대상에 있는 양은 빼고 점검한다(이어받기)
    put(tmp_path / "out" / mt.dest_rel(str(a)) / "have.bin", b"h" * 9_900)
    plan = preview_plan(items, tmp_path / "out")
    space = mt.precheck_space(plan, tmp_path / "out", dest_info_fn=lambda p: info)
    assert space["existing_bytes"] == 9_900 and not space["space_blocked"]


def test_multi_item_failure_is_isolated_and_counted(tmp_path):
    a, b = sel_tree(tmp_path)
    robo = FakeRobo(corrupt="x.jpg")  # 두 번째 항목의 파일이 복사 중 깨짐
    dst = tmp_path / "out"
    rep = run_sel([str(a), str(b)], dst, robo)
    d = rep.to_dict()
    assert [i["status"] for i in d["items"]] == ["ok", "failed"] and d["items_ok"] == 1 and d["items_failed"] == 1
    assert d["failed_count"] == 1 and d["items"][1]["failed_count"] == 1 and d["items"][1]["failures"]
    assert not d["success"] and d["summary"].get("verified") == 3
    j = json.loads((dst / mt.JOURNAL_NAME).read_text(encoding="utf-8"))
    assert {v["status"] for v in j["items"].values()} == {"done", "failed"}
    assert any("실패" in x for x in mt.summary_lines(rep, str(dst)))


def test_multi_item_error_does_not_stop_others(tmp_path):
    a, b = sel_tree(tmp_path)
    calls = []

    def flaky(src, dst, **kw):
        calls.append(src)
        if Path(src) == a:
            raise TransferError("FAT32 라서 4GB 보다 큰 파일을 저장할 수 없습니다")
        return tr.transfer(src, dst, runner=FakeRobo(), dest_info_fn=lambda p: BIG, keep_awake=False)

    rep = mt.run_multi([str(a), str(b)], tmp_path / "out", lambda s, d, **kw: flaky(s, d, **{k: v for k, v in kw.items() if k in ("dry_run", "cancel")}),
                       dest_info_fn=lambda p: BIG)
    assert [i.status for i in rep.items] == ["error", "ok"] and "FAT32" in rep.items[0].error and rep.failed_total == 1
    assert len(calls) == 2


def test_multi_resume_after_cancel_and_skip_done(tmp_path):
    a, b = sel_tree(tmp_path)
    dst = tmp_path / "out"
    flag = {"stop": False}

    def on_item(n, t, s, st, r):
        if st == "ok":
            flag["stop"] = True  # 첫 항목이 끝나면 취소

    robo1 = FakeRobo()
    with pytest.raises(Cancelled):
        run_sel([str(a), str(b)], dst, robo1, cancel=lambda: flag["stop"], on_item=on_item)
    j = json.loads((dst / mt.JOURNAL_NAME).read_text(encoding="utf-8"))
    assert [v["status"] for v in j["items"].values()] == ["done"]  # 둘째 항목은 시작도 안 함
    assert not (dst / mt.dest_rel(str(b))).exists()
    robo2 = FakeRobo()
    rep = run_sel([str(a), str(b)], dst, robo2, skip_done=True)
    assert [i.status for i in rep.items] == ["skipped_done", "ok"]
    assert len([c for c in robo2.calls if "/L" not in c]) == 1 and (dst / mt.dest_rel(str(b)) / "x.jpg").is_file()
    # skip_done 없이 다시 실행하면 파일 단위 상태 기록으로 이어받는다(검사 기록을 믿고 다시 읽지 않음)
    rep3 = run_sel([str(a), str(b)], dst, FakeRobo())
    assert rep3.to_dict()["success"] and all(i.status == "ok" for i in rep3.items)
    assert rep3.items[0].report.to_dict().get("trusted_state_files", 0) >= 2


def test_multi_retry_failed_after_item_failure(tmp_path):
    a, b = sel_tree(tmp_path)
    dst = tmp_path / "out"
    rep = run_sel([str(a), str(b)], dst, FakeRobo(corrupt="x.jpg"))
    assert not rep.to_dict()["success"]
    rep2 = run_sel([str(a), str(b)], dst, FakeRobo(), retry_failed=True)
    assert rep2.items[1].status == "ok" and rep2.to_dict()["failed_count"] == 0


def test_multi_strict_passes_through(tmp_path):
    a, b = sel_tree(tmp_path)
    dst = tmp_path / "out"
    run_sel([str(a)], dst)
    rep = run_sel([str(a)], dst, strict=True)
    assert rep.to_dict()["summary"]["verified"] == 2 and rep.items[0].report.to_dict()["strict"] is True


def test_multi_exclude_junk_totals(tmp_path):
    a, b = sel_tree(tmp_path)
    rep = run_sel([str(a), str(b)], tmp_path / "out", exclude_junk=True)
    d = rep.to_dict()
    assert d["junk_excluded"] == {"files": 1, "bytes": 3} and not (tmp_path / "out" / mt.dest_rel(str(b)) / "Thumbs.db").exists()
    assert any("정크 제외: 1개" in x for x in mt.summary_lines(rep, str(tmp_path / "out")))


def test_multi_merged_manifest_verifies_whole_destination(tmp_path):
    a, b = sel_tree(tmp_path)
    dst = tmp_path / "out"
    man = tmp_path / "keep" / "all.jsonl"
    rep = run_sel([str(a), str(b)], dst, manifest_out=man)
    assert rep.meta["manifest_files"] == 4 and rep.meta["manifest_complete"]
    assert not list((tmp_path / "keep").glob("*.items"))  # 항목별 임시 목록은 합친 뒤 지운다
    rows = list(ManifestReader(man).rows())
    assert {r["rel"] for r in rows} == {mt.dest_rel(str(a)).replace("\\", "/") + "/plan.dwg",
                                       mt.dest_rel(str(a)).replace("\\", "/") + "/sub/m.rvt",
                                       mt.dest_rel(str(b)).replace("\\", "/") + "/x.jpg",
                                       mt.dest_rel(str(b)).replace("\\", "/") + "/Thumbs.db"}
    v = verify_against_manifest(man, dst)
    assert not v.failures and v.to_dict()["summary"].get("verified") == 4


def test_multi_manifest_inside_source_is_refused(tmp_path):
    a, _b = sel_tree(tmp_path)
    with pytest.raises(ApiError):
        run_sel([str(a)], tmp_path / "out", manifest_out=a / "m.jsonl")


def test_load_selected_from_file_and_bad_file(tmp_path):
    a, b = sel_tree(tmp_path)
    res = fd.FindResult(projects=[fd.FolderItem(str(a), "project", "C:", total_bytes=80, total_files=2)])
    sel = fd.write_selection(res, tmp_path / "sel.json")
    with pytest.raises(TransferError):  # 전부 include:false
        run_transfer_selection(sel, tmp_path / "out", deps=deps_with(FakeRobo()), keep_awake=False)
    data = json.loads(sel.read_text(encoding="utf-8"))
    data["items"][0]["include"] = True
    sel.write_text(json.dumps(data), encoding="utf-8")
    rep = run_transfer_selection(sel, tmp_path / "out", deps=deps_with(FakeRobo()), keep_awake=False)
    assert rep.to_dict()["items_ok"] == 1
    bad = tmp_path / "bad.json"
    bad.write_text("[]", encoding="utf-8")
    with pytest.raises(ApiError):
        run_transfer_selection(bad, tmp_path / "o2", deps=deps_with(FakeRobo()), keep_awake=False)


def test_cli_transfer_from_selection_end_to_end_with_real_robocopy(tmp_path, capsys):
    if os.name != "nt" or shutil.which("robocopy") is None:
        pytest.skip("실제 robocopy 는 Windows 에서만")
    a, b = sel_tree(tmp_path)
    res = fd.FindResult(projects=[fd.FolderItem(str(a), "project", "C:"), fd.FolderItem(str(b), "project", "C:")])
    sel = fd.write_selection(res, tmp_path / "sel.json")
    data = json.loads(sel.read_text(encoding="utf-8"))
    for it in data["items"]:
        it["include"] = True
    sel.write_text(json.dumps(data), encoding="utf-8")
    dst, rpt = tmp_path / "out", tmp_path / "r.json"
    assert cli.main(["transfer", "--from-selection", str(sel), str(dst), "--dry-run"]) == 0
    assert "미리보기" in capsys.readouterr().out and not dst.exists()
    code = cli.main(["transfer", "--from-selection", str(sel), str(dst), "--no-keep-awake", "--exclude-junk",
                     "--report", str(rpt), "--manifest-out", str(tmp_path / "m.jsonl")])
    out = capsys.readouterr().out
    assert code == 0 and "전송을 마쳤습니다" in out and "정크 제외: 1개" in out
    assert (dst / mt.dest_rel(str(a)) / "sub" / "m.rvt").is_file() and not (dst / mt.dest_rel(str(b)) / "Thumbs.db").exists()
    rd = json.loads(rpt.read_text(encoding="utf-8"))
    assert rd["items_ok"] == 2 and rd["success"] and rd["junk_excluded"]["files"] == 1
    assert not verify_against_manifest(tmp_path / "m.jsonl", dst).failures
    # 같은 명령을 다시: 이어받기(변경 없음)
    assert cli.main(["transfer", "--from-selection", str(sel), str(dst), "--no-keep-awake", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["items_ok"] == 2


def test_cli_from_selection_argument_errors(tmp_path, capsys):
    a, _b = sel_tree(tmp_path)
    sel = tmp_path / "s.json"
    sel.write_text(json.dumps({"format": fd.SELECTION_FORMAT, "version": 1, "items": [{"path": str(a), "include": True}]}), encoding="utf-8")
    assert cli.main(["transfer", "--from-selection", str(sel), str(a), str(tmp_path / "o")]) == 2  # 원본을 함께 적음
    assert cli.main(["transfer", "--from-selection", str(sel), str(tmp_path / "o"), "--state-file", str(tmp_path / "st")]) == 2
    assert cli.main(["transfer", "--from-selection", str(sel), str(a / "inside"), "--dry-run"]) == 1  # 대상이 원본 안
    assert cli.main(["transfer", "--from-selection", str(tmp_path / "missing.json"), str(tmp_path / "o")]) == 1
    assert cli.main(["transfer", str(a), str(tmp_path / "o"), "--exclude-backup-copies"]) == 2
    capsys.readouterr()


def test_field_guide_mentions_new_commands_and_stays_synced():
    root = Path(__file__).resolve().parents[1]
    md = (root / "docs" / "field-guide.md").read_text(encoding="utf-8")
    ht = (root / "docs" / "field-guide.html").read_text(encoding="utf-8")
    readme = (root / "README.md").read_text(encoding="utf-8")
    for needle in ("find-data", "--from-selection", "--exclude-junk"):
        assert needle in md and needle in ht and needle in readme
