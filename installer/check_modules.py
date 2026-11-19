"""만든 exe 안에 기능 폴더의 모든 .py 모듈이 들어 있는지 본다(하나라도 없으면 실패).
기능 모듈은 lazy.Later가 이름으로 처음 쓸 때 불러와서, 빠져도 빌드는 성공하고 설치본에서 그 화면을 열 때에야 오류가 난다.
build-exe.yml의 "기능 모듈 확인" 단계가 프로젝트 폴더에서 check(exe 경로, 기능 폴더들)를 부른다."""
from pathlib import Path


def module_names(root: Path, packages: list[str]) -> set[str]:
    """root 아래 기능 폴더(packages)들의 패키지·모듈 이름 전부(하위 패키지 포함). 폴더 이름 자체도 넣어, 없는 폴더를 적으면 빠진 것으로 잡힌다."""
    files = [path.relative_to(root).with_suffix("") for package in packages for path in (root / package).rglob("*.py")]
    return {*packages, *(".".join(path.parts).removesuffix(".__init__") for path in files)}


def missing(root: Path, packages: list[str], bundled: set[str]) -> list[str]:
    """기능 폴더의 모듈 가운데 묶인 모듈 목록(bundled)에 없는 것."""
    return sorted(module_names(root, packages) - bundled)


def check(exe: str, packages: list[str]) -> None:
    """지금 폴더의 기능 폴더 모듈이 exe에 모두 묶였는지 본다. 빠진 것이 있으면 이름을 적고 실패(SystemExit)."""
    from PyInstaller.archive.readers import pkg_archive_contents  # exe에 붙은 묶음(PKG)과 그 안의 PYZ(순수 파이썬 모듈) 이름 목록
    lost = missing(Path.cwd(), packages, set(pkg_archive_contents(exe)))
    if lost:
        raise SystemExit("exe에 빠진 기능 모듈:\n" + "\n".join(lost))
    print(f"기능 모듈이 모두 들어 있어요: {' '.join(packages)}")
