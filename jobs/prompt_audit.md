# 최종 판정 전수 검수 지시문

분류 지시문(`prompt_classify.md`)과 재판정 규칙(`prompt_adjudicate.md`)을 기준으로, 각 공고의 최종 판정(`label`)이 원문(`input`)과 맞는지 확인한다. 검수자는 판정을 새로 만드는 사람이 아니라 틀린 것을 찾는 사람이다.

- 원문을 끝까지 읽고 `car_level`, `car_purpose`, `transit_barrier`, `shuttle`, `night_or_shift`를 하나씩 확인한다.
- 정의상 분명히 틀린 경우만 고친다. 두 값 모두 정의에 맞게 읽힐 수 있는 애매한 경우는 고치지 않고 `ambiguous`로 표시한다.
- `evidence`가 판정을 뒷받침하지 않으면 원문에서 그대로 옮긴 근거로 바꾼다.

출력: 틀렸거나 애매한 공고만 담은 JSON 배열. 항목은
`{"id": ..., "status": "wrong" 또는 "ambiguous", "fields": {고칠 항목: 맞는 값}, "evidence": "원문 그대로", "reason": "한 줄 이유"}`.
모두 맞으면 빈 배열 `[]`.
