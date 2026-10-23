# Supabase 연결

Supabase는 PostgreSQL이라 `postgres` 드라이버(psycopg)로 접속합니다. exe에는 이미 들어 있습니다. 연결은 [settings.md](settings.md)의 DB 주소 연결과 같습니다.

1. Supabase 대시보드 **Connect**에서 **Session pooler** 주소(포트 5432)를 복사합니다.
   예: `postgresql://postgres.<프로젝트ref>:[YOUR-PASSWORD]@aws-0-<리전>.pooler.supabase.com:5432/postgres`
2. 연결 카드의 [DB 주소]에 그대로 붙여넣습니다. `[YOUR-PASSWORD]` 자리는 프로그램이 빼니, **비밀번호는 비밀번호 칸**에 쓰세요(저장하지 않습니다). 주소에 실제 비밀번호를 붙여넣어도 쓸 수 있지만, 최근 연결에는 남지 않습니다.
3. `sslmode=require` 같은 주소 뒤 매개변수는 그대로 전달됩니다.

| 주소 | 설명 |
|---|---|
| Session pooler (`...pooler.supabase.com:5432`) | 권장. IPv4에서도 붙습니다 |
| Direct (`db.<ref>.supabase.co:5432`) | 무료 플랜은 IPv6 전용이라 네트워크에 따라 안 붙을 수 있습니다 |
| Transaction pooler (`...pooler.supabase.com:6543`) | 접속마다 서버가 바뀌므로 prepared statement를 끄고 접속합니다(`pgbouncer=true`가 있어도 같습니다) |

**읽기 전용 계정을 권장합니다.** `postgres` 계정은 행 보안(RLS)을 무시해 모든 행이 보입니다. SQL Editor에서 읽기 전용 역할을 만들어 그 계정의 아이디(`역할.프로젝트ref`)와 비밀번호를 쓰세요. 처리 용량은 PostgreSQL 통계로 어림하며 COUNT를 돌리지 않습니다.
