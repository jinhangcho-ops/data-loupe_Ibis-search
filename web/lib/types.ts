import type { AutoRule, AutoTable, AutoView, AxisScale, Order, Scale, SemanticWords } from "@/lib/autoviz";
import type { GroupView } from "@/lib/groups";
import type { CtxFilter } from "@/lib/links";
import type { FrameKind, FrameSettings } from "@/components/frame/registry";

// Flask API가 주고받는 JSON 모양(core/dash_store.py의 check_* 와 dashboard/dash_app.py가 돌려주는 것 그대로). 이름은 서버 키를 바꾸지 않는다.

/** 12칸 격자의 자리(dash_store.check_layout): x 0~11, y 0~999, w·h 2~12, x + w ≤ 12 */
export type Layout = { x: number; y: number; w: number; h: number };

/** 카드 종류(dash_store.KINDS) */
export type CardKind =
  | "chart" | "analysis" | "metric" | "kpi" | "tile" | "donut_kpi" | "gauge" | "summary" | "dual_trend" | "stripes" | "blocks"
  | "target_trend" | "map" | "alerts" | "deviation" | "funnel" | "table" | "list" | "status" | "sentence" | "geomap"
  | "histogram" | "boxplot" | "heatmap" | "calheat" | "treemap" | "sunburst" | "bullet" | "bump" | "sankey" | "cohort" | "candle" | "wordcloud" | FrameKind;

/** 차트 종류(dash_store.CHART_TYPES). ""이면 계열마다 정한 대로 */
export type ChartType = "" | "bar" | "hbar" | "stacked" | "line" | "polyline" | "area" | "doughnut" | "pie" | "radar" | "scatter" | "bubble"
  | "group" | "stack100" | "hstack" | "hstack100" | "sarea" | "sarea100" | "step" | "combo" | "waterfall" | "pareto" | "radial" | "polar" | "range" | "slope" | "trendline" | "butterfly" | "lollipop" | "bar3d" | "surface3d" | "scatter3d";
export type Unit = "day" | "week" | "month" | "quarter" | "year";
export type ValueFormat = "number" | "percent" | "currency";
/** (B6) 값 모양 칸과 짝인 소수 자릿수(0~6, 없으면 지금 규칙)·끝자리 0 채우기(참일 때만) */
export type Places = { digits?: number; zeros?: boolean };

/** 지정 색(check_colors): 이름별 HEX와 라벨이 처음 나온 순서 */
export type Colors = { series: Record<string, string>; parts: Record<string, string>; order: string[]; parts_order: string[] };

/** 조건 색 규칙(lib/dash_rules.js) */
export type CondRule = { column: string; match: string; color: string; arrow: "none" | "up" | "down" | "auto"; scope: "cell" | "row" };
/** 상태 규칙(parseStateLine) */
export type StateRule = { column: string; op: string; value: number | string; level: "warn" | "bad" };
export type Target = { value: number } | { name?: string; period?: string; scope?: string };
export type Slot = { label: string; format: ValueFormat; unit_text: string; color: string; y?: string; agg?: string };
/** (B3) 목록 줄의 부분 하나(dash_store.check_list): 모양, 읽을 컬럼(swap이면 값 바꾸기 단추가 고른 값), 값 모양, 색 규칙(맞으면 그 색), 막대 기준, 줄 안 자리 */
export type ListPart = { kind: "text" | "chip" | "tag" | "number" | "bar"; column: string; swap: boolean; format: ValueFormat; digits?: number; zeros?: boolean; rules: { match: string; color: string }[]; basis: "max" | "sum"; side: "start" | "end" };
/** (B3) 목록 카드(kind list)의 모양 설정 view.list: 줄 부분들(차례대로), 줄 고르기, 거를 컬럼(묶음 컬럼), 값 바꾸기 단추들, 그림·보고서 모양 */
export type ListView = { parts: ListPart[]; pick: "none" | "one" | "many"; key: string; swaps: { label: string; value: string }[]; report: "screen" | "table" };
/** (B9) 목록 줄에 붙이는 표시 칸 하나(dash_store.check_row_marks): id(표시 파일 컬럼), 이름(사용자가 적음), 종류, 고를 값(고르기 칸만, 그 밖은 빈 목록) */
export type MarkField = { id: string; name: string; kind: "choice" | "text" | "date" | "number"; choices: string[] };
/** (B9) 목록 카드의 줄 표시 설정 view.row_marks: 열쇠 컬럼들(묶음 컬럼 가운데 하나 이상, 차례대로 열쇠 글자로 잇는다), 표시 칸들, 바꾸는 곳(줄 끝 칸·누르면 뜨는 칸), 가져올 때 같은 줄 규칙 */
export type MarkView = { keys: string[]; fields: MarkField[]; edit: "cell" | "pop"; clash: "keep" | "replace" | "fill" };
/** (B9) 표시 값 하나(날짜는 YYYY-MM-DD 글자, 빈 값 null) */
export type MarkValue = string | number | null;

/** 보기 설정(check_view + check_extra + check_more). 3단계 카드 설정은 종류마다 쓰는 것만 있다 */
export type CardView = {
  type: ChartType;
  unit: Unit;
  compare: { mode: "span" | "point" };
  better: "up" | "down";
  styles?: { type: "bar" | "line"; dash: "" | "dashed"; axis: "left" | "right" }[];
  ranges?: { y: { min?: number; max?: number }; y1: { min?: number; max?: number } };
  ratio?: number;
  analysis_view?: string;
  points?: { size: string };
  trend?: boolean;
  show_r?: boolean;
  connect?: boolean;
  format?: ValueFormat;
  digits?: number;
  zeros?: boolean;
  unit_text?: string;
  ring?: "stack" | "double" | "half" | "ticks";
  slots?: Slot[];
  target?: Target;
  full?: number;
  cells?: number;
  size?: number;
  rows?: number;
  group?: string;
  window?: { from: string; to: string };
  cond_rules?: CondRule[];
  rules?: StateRule[];
  bg?: "rack" | "network" | "image";
  image_path?: string;
  positions?: Record<string, [number, number]>;
  links?: [string, string][];
  template?: string;
  blanks?: Record<string, unknown>;
  bucket?: "hour" | "day" | "week" | "month";
  baseline?: number;
  threshold?: number;
  geo?: Record<string, unknown>;
  rings?: boolean;                    // 겹친 도넛: 도넛·파이·방사형·장미에서 계열마다 링
  bins?: number;                      // 히스토그램 구간 수(1~100)
  bands?: number[];                   // 불릿 구간 경계
  funnel_shape?: "flow" | "ladder";   // 퍼널 그림: 흐름(기본) | 사다리꼴
  ladder_scale?: boolean;             // 사다리꼴 폭을 인원에 비례
  gap_stats?: ("mean" | "median" | "trimmed")[];  // 걸린 시간 표에 보일 값
  flow_unlimited?: boolean;           // 생키 자동 흐름: 칸·띠 상한 없음
  flow_dropout?: boolean;             // 생키 자동 흐름: 이탈 칸(없으면 끝 값을 골랐는지로)
  auto?: AutoView;                    // 자동 규칙(Phase 9): 키 없음 = 기본 켬, false = 끔, 객체 = 수치
  scale?: AxisScale;                  // 값 축 눈금: log | symlog(없으면 보통)
  all_data?: boolean;                 // 모든 데이터: 자동 규칙이 덜어 낸 것을 풀어 그림
  data_table?: boolean;               // 차트 아래 표로 보기
  cross?: boolean;                    // 누르면 거르기: 보는 곳에서 막대·조각을 누르면 가로축 값으로 링크한 카드를 거른다
  link_to?: string[];  // (B4) 이 카드·요소가 보내는 것을 받는 카드 id(빈 목록도 저장)
  link_map?: Record<string, Record<string, string>>;  // (B4) 상대 id → {보내는 쪽 컬럼: 상대 컬럼}
  note?: string;  // (B2) 숫자 타일 설명 글
  press?: "cond" | "period";  // (B2) 타일 누르기: 조건만 / 기간 줄도(없으면 꺼짐)
  list?: ListView;  // (B3) 목록 카드의 줄 부분·고르기·값 바꾸기·그림 모양
  list_sort?: { column: string; desc: boolean };  // (B3) 목록 정렬(바꾸면 다시 구함)
  list_rows?: number;  // (B3) 목록 줄 수
  row_marks?: MarkView;  // (B9) 목록 카드의 줄 표시 칸·열쇠 컬럼·바꾸는 곳·가져올 때 규칙
  page?: string;                      // 놓인 쪽(탭) 이름. 대시보드의 쪽 목록에 없으면 첫 쪽에 보인다
  groups?: GroupView;              // 표시할 그룹 고르기(Phase 4, lib/groups.applyGroups)
  cloud?: { mode: "values" | "labels" | "words"; tags?: string[]; rows?: "all" | "tagged"; match?: "any" | "all"; text?: string; pos?: ("noun" | "verb" | "adj" | "foreign")[]; stop?: string[]; min_len?: number };  // 워드클라우드 방식(Phase 4)
  frame?: FrameSettings;              // 틀 요소(B10)의 설정: 요소 카드에만 쓴다
  [more: string]: unknown;  // stale·entity·time_col·kind_col·link_col·group_cols·formats·total 등(check_more)
};

/** 다시 구할 기록(check_source). body는 쿼리 화면의 조회 body 그대로 */
export type CardSource = {
  key?: string; label?: string; schema?: string; table?: string; metric_id?: string; flow_id?: string; date?: string;
  body?: Record<string, unknown> & { periods?: string[]; series?: { y?: string; agg?: string }[]; order?: Order };
  recipe?: Record<string, unknown>; analysis?: Record<string, unknown>; full?: unknown; sample?: unknown;
};

/** 일 값 계열(dash_calc.day_section): 다시 묶을 수 있으면 sum·count·min·max, 아니면 values(merge: false) */
export type DaySeries = { name: string; agg: string; sum?: (number | null)[]; count?: (number | null)[]; min?: (number | null)[]; max?: (number | null)[]; values?: (number | null)[]; merge?: boolean };
export type UnitSection = { labels: string[]; series: { name: string; values: (number | string | null)[] }[] };

/** 구한 값(check_data). 종류마다 쓰는 키가 다르다 */
export type CardData = {
  computed_at?: string; def_hash?: string; note?: string; x?: string; columns?: string[];
  by_unit?: { day?: { labels: string[]; series: DaySeries[] } } & Partial<Record<Unit, UnitSection>>;
  values?: Record<string, unknown> & { kind?: string; labels?: string[]; series?: { name: string; values: (number | string | null)[] }[]; merged?: boolean };
  points?: ChartPoints; x_kind?: string; num_cols?: string[]; date_cols?: string[]; text?: string; sentences?: unknown[];
  target?: unknown; pair?: unknown; since?: string; window?: { start?: string; end?: string };
  bins?: { edges: number[]; counts: number[]; under?: number; over?: number; clip?: [number, number] | null };  // histogram(P14)
  x_scale?: Scale; x_levels?: string[];  // 순서형 정보(Phase 5가 채움)
};
/** 새 카드(P14, 서버 dash_calc)의 data.values 모양: 종류마다 쓰는 칸만 있다(histogram은 data.bins) */
export type MoreValues = {
  groups?: { name: string; n?: number; min: number; q1: number; med: number; q3: number; max: number; outliers?: number[]; outlier_count?: number; p01?: number | null; p99?: number | null; below?: number; above?: number }[];  // boxplot(min·max는 1.5 IQR 수염 끝)
  rows?: string[]; cols?: string[]; cells?: (number | null)[][];                         // heatmap
  days?: { d: string; v: number | null }[];                                             // calheat
  items?: TreeItem[];                                                                   // treemap·sunburst
  nodes?: { name: string; users?: number }[]; links?: { s: number; t: number; v: number }[];  // sankey("이탈" 노드는 있을 때만 마지막)
  periods?: (number | string)[]; cohorts?: { start: string; size: number; retained: (number | null)[] }[]; partial?: boolean;  // cohort(비율 0~1, partial: 마지막 기간이 진행 중)
  labels?: string[]; candles?: { open: number | null; high: number | null; low: number | null; close: number | null }[];  // candle(labels와 같은 순서)
  series?: { name: string; values: (number | string | null)[]; ranks?: (number | null)[] }[];  // bump(ranks: 1 = 1등)
  words?: { w: string; n: number }[];                                                   // wordcloud
};
export type TreeItem = { name: string; value: number; children?: TreeItem[] };

/** 카드(B5부터 "조각"). section이 있으면 layout은 그 섹션 눈금의 자리(dash_store.check_spot), 없으면 옛 카드라 layout은 쪽 격자 자리(섹션 하나를 꽉 채운 조각으로 읽는다: grid/sections.sectionsOf) */
export type Card = { id: string; kind: CardKind; title: string; layout: Layout; colors: Colors; view: CardView; source: CardSource; data: CardData; section?: string };

/** (B5) 섹션(dash_store.check_section): 쪽 12칸 격자의 자리, 이름(비면 이름 줄 없음), 크기를 바꾸면 조각도 같이 바뀌는지, 놓인 쪽(view.page) */
export type Section = { id: string; title: string; layout: Layout; follow: boolean; view: { page?: string } };

export type NorthStar = { card: string; counters: string[]; limits?: Record<string, { op: ">=" | "<="; value: number }> };

/** 대시보드 하나(check_dashboard) */
export type Dashboard = {
  id: string; name: string; template: string; palette: string[]; period: string; as_of: string; compare_mode?: CompareMode;
  cards: Card[]; created: string; updated: string; north_star: NorthStar;
  filters?: DashFilter[];  // 필터 칸 목록(옛 저장본에는 없다)
  pages?: string[];        // 쪽(탭) 이름 목록. 비었으면 쪽을 나누지 않는다(옛 저장본에는 없다)
  pull?: Pull;             // (B11) 받기 설정(옛 저장본에는 없다: consts.dash.pull.default)
  sections?: Section[];    // (B5) 섹션 목록(옛 저장본에는 없다: 카드마다 섹션 하나로 읽는다)
  press_chips?: boolean;  // (B4) 누른 값을 도구 줄 필터 줄에 칩으로(옛 저장본에는 없다: 켜짐)
};
/** (B11) 갱신 주기 단위(dash_store.PULL_UNITS의 키) */
export type PullUnit = "s" | "m" | "h";
/** (B11) 새 줄을 알아볼 컬럼 하나(테이블마다) */
export type PullMark = { schema: string; table: string; column: string };
/** (B11) 받은 Parquet 암호화(dash_store.PULL_CRYPTS): 없음·AES-128·AES-256·사용자가 적는 암호문 */
export type PullCrypt = "none" | "aes128" | "aes256" | "pass";
/** (B11) 대시보드 받기 설정(dash_store.check_pull): 계산 방식, 갱신 주기(null = 단추를 누를 때만), 받는 범위, 새 줄 컬럼, 암호화 */
export type Pull = { calc: "pc" | "db"; every: { n: number; unit: PullUnit } | null; range: "all" | "new"; marks: PullMark[]; crypt: PullCrypt };
/** (B11) 아무 대시보드도 쓰지 않는 칸(GET /api/dash/pull/orphans)·이 대시보드에서 더는 안 쓰는 칸(POST /api/dash/pull/unused, shared = 다른 대시보드도 씀) */
export type PullOrphan = { slot: string; schema: string; table: string; bytes: number };
export type PullUnused = PullOrphan & { shared: boolean };
/** (B11) GET /api/dash/pull/state: 이 대시보드가 쓰는 칸들. lost = 마지막 줄을 못 찾았을 때 다시 받을 기간 컬럼("" 없음, "-" 기간 컬럼이 하나가 아님) */
export type PullSlot = PullUnused & { pulled_at: string; lost: string | null };
export type PullState = { bytes: number; text: string; slots: PullSlot[] };
/** (B11) POST /api/dash/pull/tables의 테이블 하나: 새 줄 컬럼 후보(정수·날짜·시각)와 조인으로만 읽는지 */
export type PullTable = { schema: string; table: string; columns: string[]; join: boolean };
/** POST /api/dash/settings가 바꾸는 항목만 */
export type DashSettings = Partial<Pick<Dashboard, "as_of" | "period" | "compare_mode" | "pull">>;
/** 비교 기준(dashboard/period.MODES): 직전 기간·같은 요일 기간·작년 같은 기간 */
export type CompareMode = "prev" | "same_weekday" | "yoy";

export type DashSummary = { id: string; name: string; cards: number; updated: string };
/** GET /api/dash: locked면 이 PC에서 풀 수 없는 파일, last는 첫 화면에서 마지막으로 연 id */
export type DashList = { dashboards: DashSummary[]; locked: boolean; last: string };

// ---------- 기간 ----------
export type Span = { start: string | null; end: string | null; days?: number; label: string; error: string | null; [more: string]: unknown };
export type PrevSpan = { start: string; end: string; text: string };
/** 카드를 구할 때 보내는 ctx(DashBoard.ctx, dash_refresh.context): 보는 기간·직전 기간·기준일 */
export type Ctx = { range?: { start: string; end: string }; prev?: PrevSpan | null; months?: number; as_of?: string; compare_mode?: CompareMode; filters?: CtxFilter[] };
/** 대시보드의 필터 칸 하나(만드는 곳에서 정한다): 컬럼 이름과 보일 이름(비면 컬럼 이름) */
export type DashFilter = { column: string; label: string };
/** 보는 사람이 필터 칸에서 고른 값(ctx.filters): 컬럼 값이 in 가운데 하나인 행만. 저장하지 않는다 */
export type PickedFilter = { column: string; in: string[] };
export type PeriodParse = { span: Span; previous: PrevSpan | null; context: Ctx | null };
export type DayParse = { date: string | null; label: string; error: string | null };

// ---------- 값 구하기 ----------
export type ComputeResult = { id: string; data?: CardData; error?: string };
export type ChartPoints = Record<string, unknown> & {
  groups: string[]; y: string;
  hex?: { cols: number; x: [number, number] | null; y: [number, number] | null; cells: [number, number, number][] };
};

// ---------- 연결 ----------
export type Source = { key: string; label: string } | null;
export type LiveMin = { source: Source; seconds: number };

// ---------- 그 밖 ----------
/** (B7) 템플릿 조각(dash_store.check_template): 종류·이름·섹션 id·섹션 눈금 자리·색·모양 설정(TEMPLATE_VIEW, 틀 요소는 view.frame). 못 여는 종류(unknown)는 kind·title·section·layout만 */
export type TemplatePiece = { kind: string; title: string; section: string; layout: Layout; colors?: Colors; view?: Partial<CardView> };
/** (B7) 템플릿(dash_store.check_template): 형식 번호, id(기본 템플릿은 파일 이름), 설명, 쪽, 섹션, 조각, 이 버전에서 못 여는 종류 */
export type Template = { format: number; id: string; name: string; help: string; pages: string[]; sections: Section[]; pieces: TemplatePiece[]; unknown: string[] };
/** (B7) [더 받기] 목록 한 줄(dash_templates.remote_list): 저장소 templates 폴더의 파일 이름과 크기(바이트) */
export type RemoteTemplate = { file: string; size: number };
export type Targets = { path: string; rows: Record<string, unknown>[]; errors: unknown[]; error_total: number; offset: number; limit: number; error: string };
export type CompareItem = { cur: unknown; prev: unknown; fmt?: ValueFormat; mode?: string; label?: string; better?: "up" | "down"; partial?: boolean; digits?: number; zeros?: boolean };
/** /api/columns: [컬럼, 한글 타입] */
export type Columns = { columns: [string, string][]; fields: [string, string][]; origins: Record<string, string> };

/** (B11) 받기 설정 상수(서버 dash_store.PULL_*와 같은 값) */
export type PullConsts = { calcs: Pull["calc"][]; ranges: Pull["range"][]; crypts: PullCrypt[]; units: Record<PullUnit, number>; unit_names: Record<PullUnit, string>; min_every: number; max_every: number; default: Pull; gap_note: string };

/** (B12) POST /api/dash/shortcut/connect의 답: 연결했나, 더 물어야 할 것(""·비밀번호만 "password"·주소·아이디·비밀번호 "address"), 연결 이름(비밀번호 없음), 아이디 */
export type ShortcutConn = { connected: boolean; needs: "" | "password" | "address"; label: string; user: string; remembered: boolean };  // remembered: 넣은 주소의 연결을 바로가기 파일이 기억했나

/** GET /api/dash/constants(server.constants("dashboard")) */
export type Constants = {
  sizes: Record<string, [number, number]>;
  floats: Record<string, unknown>;
  time: { bands: unknown; max: number };
  file_kinds: unknown;
  funcs_ver: string;
  agg_names: Record<string, string>;
  merge: string[];
  week_start?: "mon" | "sun";   // 설정: 주 묶음 시작 요일(없으면 mon)
  fiscal_start_month?: number;   // 설정: 회계연도 첫 달 1~12(없으면 1)
  currency_symbol: string;       // 설정: 값 모양 currency의 기호(고른 적이 없으면 서버 기본값)
  currency_max: number;          // 통화 기호의 글자 수 상한(settings.CURRENCY_MAX)
  defaults: Record<string, unknown>;  // 설정 기본값(settings.DEFAULTS)
  shortcut?: string;  // (B12) 바로가기 창이면 그 대시보드 id, 아니면 ""(server.only_dash, dash_app.dash_constants)
  features: { on: string[]; version: string | null; all: { id: string; name: string; path: string }[] };
  dash: {
    max_cards: number; max_filters: number; max_pages: number;max_dashboards: number; max_palette: number; types: Partial<Record<Exclude<ChartType, "">, string>>; time_types: ChartType[]; max_bins?: number; max_bands?: number; max_cells: number; merge_note: string;
    units: Record<Unit, string>; locked: string; max_points: number; aggs: string[]; merge: string[]; agg_names: Record<string, string>;
    auto: AutoTable; auto_names: Record<AutoRule, string>; viz: { words: SemanticWords; query_auto: AutoView }; orders: Order[]; order_names: Record<Order, string>;
    pull: PullConsts;  // (B11) dash_store.PULL_* (dash_app.dash_constants)
    list: { max_parts: number; max_swaps: number; max_rows: number; part_kinds: ListPart["kind"][]; picks: ListView["pick"][]; bases: ListPart["basis"][]; sides: ListPart["side"][]; reports: ListView["report"][]; default: { list: ListView; list_sort: { column: string; desc: boolean }; list_rows: number } };  // (B3) 목록 카드 상수
    row_marks: { kinds: MarkField["kind"][]; edits: MarkView["edit"][]; clashes: MarkView["clash"][]; looks: ("number" | "bar")[]; max_fields: number; max_choices: number; max_name: number; max_text: number; max_rows: number; max_scan: number; default: MarkView };  // (B9) dash_store.ROW_MARK_* (dash_app.dash_constants)
    digits: { min: number; max: number };  // (B6) 소수 자릿수 칸의 범위(compare.MIN_DIGITS·MAX_DIGITS, dash_app.dash_constants)
    gone_kinds: string[]; gone_text: string;  // (B11) 앱에서 없앤 카드 종류(dash_store.GONE_KINDS)와 그 안내 글(dash_store.GONE_CARD)
  };
  [more: string]: unknown;
};
