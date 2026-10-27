# Hunt Briefing Nexus

공개 Hunt Briefing 자료를 출처·대상·주장·관계·적용 기간으로 정리하는 독립 지식 저장소입니다. 앱의 브리핑 라이브러리가 검증된 발행본을 읽습니다.

기존 브리핑 발행·트렌드 집계 저장소에는 쓰지 않습니다. 고정 키워드만 검색하는 방식 대신 공개 본문 전체를 입력으로 사용하며, AI 추출 → 별도 의미 검토 → 인용·해시·동일성·시점 검사 → 발행 순서로 처리합니다.

- 앱 엔드포인트: `data/manifest.json`
- 발행본: `data/releases/<sha256>.json`
- 불변 원본·지식 세대: `data/states/<sha256>.json`
- 작업·검토 큐: `data/work/queue.json`
- 명칭 사전: `data/work/catalog.json`
- 운영 규칙: [OPERATIONS.md](OPERATIONS.md)

관계망에는 대표 주제와 근거 있는 의미 관계를 표시합니다. 언급 문서와 개별 주장은 상세 목록으로 연결합니다. AI 검토 통과는 독립적인 사실 검증이나 게임 전체의 진실을 보증하는 표시가 아닙니다.

Python 3.12+, 외부 패키지 없이 실행합니다.

```sh
python -m unittest discover -s tests -v
python -m nexus.pipeline collect
```

GitHub Actions가 6시간마다 읽기·검증·발행을 담당하고, 별도 예약 AI가 inbox에 추출·검토 결과를 작성합니다. 스키마 오류나 네트워크 실패 시 마지막 정상 지식은 보존됩니다. 초기 이관은 완료된 구간부터 공개하고 남은 작업 수를 명시합니다.

개인 아카이브·비공개 대화·인증키·토큰은 저장하지 않습니다. source/state/work 데이터는 공개 브리핑과 그 인용 원문으로 제한합니다.
