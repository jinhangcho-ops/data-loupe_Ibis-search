"""exe 빌드(build-exe.yml)가 기능 폴더를 빠짐없이 넣고, 빠지면 빌드가 멈추는지. v2.3.8 설치본에서 dashboard·collect·integrations 모듈이 빠졌다."""
import re
from pathlib import Path

from installer import check_modules

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = (ROOT / ".github/workflows/build-exe.yml").read_text(encoding="utf-8")
FEATURES = ["core", "query", "prep", "analysis", "metrics", "dashboard", "collect", "integrations", "geo"]
NOT_SOURCE = {"tests_pytest", "build", "dist", "web", "testdata"}  # 앱 코드가 아닌 폴더(이름이 .으로 시작하는 폴더도 뺀다)


def listed() -> list[str]:
    """워크플로에 적힌 기능 폴더 목록(한 곳: FEATURE_PACKAGES)."""
    return re.search(r"^ +FEATURE_PACKAGES: (.+)$", WORKFLOW, re.M)[1].split()


def step(name: str) -> str:
    """워크플로에서 이름이 name으로 시작하는 단계의 글."""
    return re.search(rf"^      - name: {name}.*?(?=^      - )", WORKFLOW, re.M | re.S)[0]


def test_nine_feature_folders_are_listed_once() -> None:
    assert listed() == FEATURES
    assert WORKFLOW.count("FEATURE_PACKAGES:") == 1
    assert not re.search(r"--collect-submodules (?:" + "|".join(FEATURES) + r")\b", WORKFLOW)  # 목록을 명령줄에 따로 또 적지 않는다


def test_build_collects_every_listed_folder_with_the_project_on_the_path() -> None:
    build = step("exe 만들기")
    assert "$features = -split $env:FEATURE_PACKAGES | ForEach-Object { '--collect-submodules', $_ }" in build
    assert re.search(r"pyinstaller desktop\.py[^\n]*(?:`\n[^\n]*)*@features", build)
    assert "PYTHONPATH: ${{ github.workspace }}" in build  # 없으면 collect_submodules가 기능 폴더를 못 찾아 빈 목록이 된다


def test_check_step_runs_right_after_the_build() -> None:
    steps = re.findall(r"^      - (?:name|uses|run): ([^\n(]+)", WORKFLOW, re.M)
    at = steps.index("exe 만들기")
    assert steps[at + 1].startswith("기능 모듈 확인")
    assert "check_modules.check(sys.argv[1], sys.argv[2:])\" dist/DataLoupe/DataLoupe.exe $FEATURE_PACKAGES" in step("기능 모듈 확인")


def test_every_package_named_by_later_is_listed() -> None:
    sources = [path for path in ROOT.rglob("*.py")
               if not any(part in NOT_SOURCE or part.startswith(".") for part in path.relative_to(ROOT).parts[:-1])]
    named = {name for path in sources for name in re.findall(r"\bLater\(\s*[\"']([\w.]+)[\"']", path.read_text(encoding="utf-8"))}
    packages = {name.split(".")[0] for name in named}
    assert len(named) > len(FEATURES) and packages <= set(listed()), packages - set(listed())
    assert all((ROOT / package / "__init__.py").is_file() for package in listed())  # 폴더가 패키지여야 collect_submodules가 모은다


def test_check_finds_modules_left_out(tmp_path: Path) -> None:
    for name in ("dashboard/__init__.py", "dashboard/dash_calc.py", "integrations/__init__.py", "integrations/adapters/__init__.py", "integrations/adapters/ga4.py"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("", encoding="utf-8")
    packages = ["dashboard", "integrations"]
    every = {"dashboard", "dashboard.dash_calc", "integrations", "integrations.adapters", "integrations.adapters.ga4"}
    assert check_modules.module_names(tmp_path, packages) == every
    assert check_modules.missing(tmp_path, packages, every | {"os", "flask"}) == []
    assert check_modules.missing(tmp_path, packages, {"dashboard", "integrations", "integrations.adapters"}) == ["dashboard.dash_calc", "integrations.adapters.ga4"]
    assert check_modules.missing(tmp_path, ["geo"], every) == ["geo"]  # 없는 폴더를 적어도 조용히 넘어가지 않는다
