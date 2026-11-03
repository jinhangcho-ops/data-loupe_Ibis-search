// 그림 도우미: 화면 모드의 색(themeColors)과 그림 파일 확장자. 쿼리 화면의 집계 표 그림이 쓴다(차트 그림은 차트 섬이 만든다).

// 그 모드의 색(CSS 변수). 화면 모드와 다른 모드의 색도 읽을 수 있게 잠깐 바꿨다가 되돌린다.
function themeColors(theme) {
  const root = document.documentElement, now = root.dataset.theme;
  root.dataset.theme = theme;
  const css = getComputedStyle(root), color = name => css.getPropertyValue(name).trim();
  const colors = { card: color("--card"), head: color("--bg"), ink: color("--text"), text: color("--muted"), line: color("--line"),
                   series: [1, 2, 3, 4, 5, 6, 7, 8].map(i => color("--c" + i)) };  // 계열 색. 넣은 순서대로 쓰고, 넘치면 처음부터 다시 쓴다.
  root.dataset.theme = now;
  return colors;
}

// 그림 종류(png, jpeg)에 맞는 파일 확장자
const imageExt = type => type === "jpeg" ? "jpg" : "png";
