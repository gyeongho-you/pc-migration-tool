"""20a-2/3: 내보내기 임시(stage) 폴더 위치 옵션(--stage-dir), .gitconfig 복원 경고. 모든 시험은 tmp 가짜 환경에서만 한다."""
from conftest import patch_pkg, set_pkg
import json
import os
import time
import zipfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from pcmigrate import api, cli, fs, package
from pcmigrate.detect import Program
from pcmigrate.package import PackageError, apply_import, export_package, plan_import
from pcmigrate.rules import parse_rule

NOW = lambda: datetime(2030, 1, 1)  # noqa: E731


def make_rule(**over):
    data = {"id": "demo", "name": "Demo", "version": "7", "detect": {"display_name_patterns": ["Demo"]},
            "paths": [{"path": "%ROAM%\\Vendor\\Demo", "dest": "demo/cfg", "kind": "dir"}],
            "manual_warnings": ["재로그인 필요"]}
    data.update(over)
    return parse_rule(data)


@pytest.fixture
def env(tmp_path):
    old = tmp_path / "old"
    demo = old / "Vendor" / "Demo"
    demo.mkdir(parents=True)
    (demo / "a.cfg").write_bytes(b"a-config")
    (demo / "big.dat").write_bytes(b"B" * 200)
    return SimpleNamespace(tmp=tmp_path, old=old, demo=demo, out=tmp_path / "out" / "pkg.zip",
                           expand=lambda s: s.replace("%ROAM%", str(old)))


def export(env, **kw):
    return export_package([make_rule()], env.out, [], None, expand=env.expand, now=NOW, **kw)


def stage_dirs(folder):
    return [p for p in Path(folder).glob(f"{package.STAGE_PREFIX}*")] if Path(folder).exists() else []


@pytest.fixture
def spy_stage(monkeypatch):
    """_write_entry 가 받은 stage 폴더와, 그때 그 폴더가 실제로 있는지를 기록한다."""
    seen = []
    real = package._write_entry

    def spy(zf, arc, src, stage=None):
        seen.append((stage, os.path.isdir(stage) if stage else None))
        return real(zf, arc, src, stage)
    patch_pkg(monkeypatch, "_write_entry", spy)
    return seen


# =============================================================================== 20a-2 --stage-dir

def test_default_stage_stays_next_to_output_and_is_removed(env, spy_stage):
    export(env)
    assert spy_stage and Path(spy_stage[0][0]).parent == env.out.parent and spy_stage[0][1] is True
    assert stage_dirs(env.out.parent) == []


def test_stage_dir_moves_temp_folder_away_from_output(env, spy_stage):
    local = env.tmp / "local-temp"
    m = export(env, stage_dir=local)
    assert m["failed"] == [] and env.out.exists()
    assert spy_stage and all(Path(s).parent == local and exists for s, exists in spy_stage)
    assert stage_dirs(local) == [] and stage_dirs(env.out.parent) == []  # 사용 후 정리
    assert local.is_dir()  # 지정한 폴더 자신은 지우지 않는다
    assert package.verify_package(env.out)[1] == []


def test_stage_dir_created_if_missing_and_user_files_untouched(env):
    local = env.tmp / "new" / "deeper"
    export(env, stage_dir=local)
    assert local.is_dir() and list(local.iterdir()) == []
    keep = local / "user-notes.txt"
    keep.write_text("mine")
    export(env, stage_dir=local)
    assert keep.read_text() == "mine" and [p.name for p in local.iterdir()] == ["user-notes.txt"]


@pytest.mark.parametrize("where", ["inside", "same", "deep"])
def test_stage_dir_inside_source_is_refused_and_source_untouched(env, where):
    stage = {"inside": env.demo / ".tmp", "same": env.demo, "deep": env.demo / "a" / "b"}[where]
    before = sorted(str(p.relative_to(env.old)) for p in env.old.rglob("*"))
    with pytest.raises(PackageError, match="--stage-dir"):
        export(env, stage_dir=stage)
    assert sorted(str(p.relative_to(env.old)) for p in env.old.rglob("*")) == before  # 원본 안에는 폴더 하나도 만들지 않았다
    assert (env.demo / "a.cfg").read_bytes() == b"a-config" and not env.out.exists()


def test_stage_dir_that_is_a_file_or_uncreatable_is_a_clear_error(env):
    f = env.tmp / "afile"
    f.write_text("x")
    with pytest.raises(PackageError, match="--stage-dir"):
        export(env, stage_dir=f)
    with pytest.raises(PackageError, match="--stage-dir"):
        export(env, stage_dir=f / "sub")  # 파일 아래에는 폴더를 만들 수 없다


def test_stage_dir_cleaned_after_failure_and_stale_ones_swept(env):
    local = env.tmp / "local"
    local.mkdir()
    dead = local / f"{package.STAGE_PREFIX}{2 ** 30}-dead"
    dead.mkdir()
    (dead / "junk").write_bytes(b"x" * 10)
    real = package._write_entry
    set_pkg("_write_entry", lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
    try:
        with pytest.raises(OSError):
            export(env, stage_dir=local)
    finally:
        set_pkg("_write_entry", real)
    assert stage_dirs(local) == []  # 실패해도 이번 임시 폴더와 옛(주인 없는) 임시 폴더가 모두 없다
    assert not env.out.exists()


def test_stage_space_checked_on_the_stage_drive(env, monkeypatch):
    patch_pkg(monkeypatch, "SPOOL_MAX", 50)  # big.dat(200) 은 임시 폴더를 쓰는 큰 파일
    monkeypatch.setattr(fs, "volume_root", lambda p: "S:\\" if "stagehere" in str(p) else "O:\\")
    info = lambda p: fs.DestInfo(str(p), "fixed", "NTFS", 100 if "stagehere" in str(p) else 10 ** 12, 1, "")  # noqa: E731
    with pytest.raises(PackageError, match="임시 폴더\\(--stage-dir\\)가 있는 드라이브의 여유 공간"):
        export(env, stage_dir=env.tmp / "stagehere", dest_info_fn=info)
    assert not env.out.exists() and stage_dirs(env.tmp / "stagehere") == []
    ok = lambda p: fs.DestInfo(str(p), "fixed", "NTFS", 10 ** 12, 1, "")  # noqa: E731
    export(env, stage_dir=env.tmp / "stagehere", dest_info_fn=ok)
    assert env.out.exists()


def test_stage_on_output_drive_still_counts_biggest_file_there(env, monkeypatch):
    patch_pkg(monkeypatch, "SPOOL_MAX", 50)
    monkeypatch.setattr(fs, "volume_root", lambda p: "O:\\")  # 같은 드라이브
    seen = []

    def info(p):
        seen.append(str(p))
        return fs.DestInfo(str(p), "fixed", "NTFS", 10 ** 12, 1, "")
    export(env, stage_dir=env.tmp / "elsewhere", dest_info_fn=info)
    assert len(seen) == 1  # 드라이브가 같으면 출력 쪽 점검 하나로 합쳐서 본다(전과 같은 계산)


def test_api_run_export_passes_stage_dir_only_when_given(env, monkeypatch):
    from pcmigrate import envpaths
    monkeypatch.setattr(envpaths, "expand_env", lambda s: s.replace("%ROAM%", str(env.old)))
    patch_pkg(monkeypatch, "expand_env", envpaths.expand_env)
    seen = {}

    def fake_export(matched, out, registry, winget_text, **kw):
        seen.clear()
        seen.update(kw)
        return {"rules": [], "files": [], "skipped": [], "failed": [], "manual_warnings": []}
    d = api.Deps(registry=lambda: [Program("Demo", "1", "V")], winget_export=lambda: None, winget_list=lambda: None,
                 summarize=lambda rules, programs: (0, 0), export=fake_export)
    rd = env.tmp / "rules"
    rd.mkdir()
    (rd / "demo.json").write_text(json.dumps({"id": "demo", "name": "Demo", "version": "7",
                                              "detect": {"display_name_patterns": ["Demo"]}, "paths": [],
                                              "manual_warnings": []}), encoding="utf-8")
    api.run_export(rd, env.out, deps=d)
    assert "stage_dir" not in seen
    api.run_export(rd, env.out, deps=d, stage_dir=env.tmp / "loc")
    assert seen["stage_dir"] == str(env.tmp / "loc")
    import inspect
    assert inspect.signature(api.run_export).parameters["stage_dir"].default is None


def test_cli_stage_dir_flag(monkeypatch, capsys, tmp_path):
    a = cli.build_parser().parse_args(["export", "--output", "x.zip", "--stage-dir", "D:\\tmp"])
    assert a.stage_dir == "D:\\tmp" and cli.build_parser().parse_args(["export", "--output", "x.zip"]).stage_dir is None
    seen = {}

    def fake_run_export(rules, output, **kw):
        seen.update(kw)
        manifest = {"rules": [], "files": [], "skipped": [], "failed": [], "manual_warnings": []}
        return api.ExportResult(manifest, api.ExtraPrep([], 0), [], [], api.build_checklist([], [], [], None), None)
    monkeypatch.setattr(cli, "run_export", fake_run_export)
    assert cli.main(["export", "--output", str(tmp_path / "x.zip"), "--stage-dir", str(tmp_path / "t")]) == 0
    assert seen["stage_dir"] == str(tmp_path / "t")


# =============================================================================== 20a-3 .gitconfig 경고

HOME_RULE = {"id": "gitcfg", "name": "Git", "version": "1", "detect": {"display_name_patterns": ["Git"]},
             "paths": [{"path": "%USERPROFILE%\\.gitconfig", "dest": "git/gitconfig", "kind": "file"},
                       {"path": "%USERPROFILE%\\.gitignore_global", "dest": "git/ignore", "kind": "file"}],
             "manual_warnings": []}


def home_rule():
    return parse_rule(HOME_RULE)


@pytest.fixture
def git_env(tmp_path):
    old, new = tmp_path / "oldhome", tmp_path / "newhome"
    old.mkdir()
    new.mkdir()
    (old / ".gitconfig").write_text("[user]\n\tname = X\n[alias]\n\tx = !echo hi\n")
    (old / ".gitignore_global").write_text("*.tmp\n")
    out = tmp_path / "pkg.zip"
    export_package([home_rule()], out, [], None, expand=lambda s: s.replace("%USERPROFILE%", str(old)), now=NOW)
    return SimpleNamespace(out=out, new=new, expand=lambda s: s.replace("%USERPROFILE%", str(new)), tmp=tmp_path)


def test_gitconfig_restore_allowed_with_warning_in_plan_and_report_meta(git_env):
    plan = plan_import(git_env.out, [home_rule()], git_env.expand)
    assert plan.problems == [] and any(Path(i["target"]).name == ".gitconfig" for i in plan.items)
    assert "core.sshCommand" in plan.git_config_warning and "alias" in plan.git_config_warning
    assert "복원 전에 내용을 확인하세요" in plan.git_config_warning
    report = apply_import(git_env.out, plan)
    assert (git_env.new / ".gitconfig").read_text().startswith("[user]")  # 복원은 허용 유지
    assert report.meta["git_config_warning"] == plan.git_config_warning
    assert report.meta["git_config_files"] == [str(git_env.new / ".gitconfig")]
    assert report.to_dict()["git_config_warning"] == plan.git_config_warning  # 리포트 JSON 최상위에 병합
    assert report.failures == []


def test_no_gitconfig_no_warning(env):
    export(env)
    plan = plan_import(env.out, [make_rule()], lambda s: s.replace("%ROAM%", str(env.tmp / "n")))
    assert plan.git_config_warning == ""
    rep = apply_import(env.out, plan)
    assert "git_config_warning" not in rep.meta and "git_config_warning" not in rep.to_dict()


def test_denied_or_rejected_gitconfig_does_not_warn():
    items = [{"target": "C:/u/.gitconfig", "status": "denied"}, {"target": "C:/u/.gitconfig", "status": "rejected"},
             {"target": "C:/u/.gitignore_global", "status": "would_copy"}]
    assert package.git_config_targets(items) == []
    assert package.git_config_targets(items + [{"target": "C:/u/.gitconfig-work", "status": "would_copy"}]) == ["C:/u/.gitconfig-work"]


def test_api_preview_and_cli_summary_show_the_warning(git_env, capsys, tmp_path):
    from conftest import make_rules_dir
    rd = tmp_path / "rules"
    rd.mkdir()
    (rd / "git.json").write_text(json.dumps(HOME_RULE), encoding="utf-8")
    d = api.Deps(plan=lambda p, rules: plan_import(p, rules, git_env.expand))
    r = api.run_import(git_env.out, rd, dry_run=True, deps=d)
    assert r.git_config_warning and r.preview_dict()["git_config_warning"] == r.git_config_warning
    from pcmigrate import summary
    text = "\n".join(summary.import_lines(r, str(git_env.out)))
    assert "[주의] Git 설정 파일은 명령 실행 설정" in text
    # 적용 결과 요약에도 나온다
    r2 = api.run_import(git_env.out, rd, yes=True, deps=d)
    assert r2.written and r2.report.meta["git_config_warning"]
    assert "복원 전에 내용을 확인하세요" in "\n".join(summary.import_lines(r2, str(git_env.out), r2.report))


def test_cli_import_dry_run_prints_warning_and_json_key(git_env, capsys, tmp_path, monkeypatch):
    rd = tmp_path / "rules"
    rd.mkdir()
    (rd / "git.json").write_text(json.dumps(HOME_RULE), encoding="utf-8")
    monkeypatch.setattr(cli, "plan_import", lambda p, rules, *a, **k: plan_import(p, rules, git_env.expand))
    assert cli.main(["import", str(git_env.out), "--rules", str(rd), "--dry-run"]) == 0
    assert "복원 전에 내용을 확인하세요" in capsys.readouterr().out
    assert cli.main(["import", str(git_env.out), "--rules", str(rd), "--dry-run", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["git_config_warning"]
    assert cli.main(["import", str(git_env.out), "--rules", str(rd), "--yes", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["git_config_warning"]
