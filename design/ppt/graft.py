# -*- coding: utf-8 -*-
"""gen_slides.js가 만든 새 슬라이드를 원본 덱에 이식하고 페이지 번호를 재정렬한다.

원본 덱(13장)은 건드리지 않고, 새 11장을 정해진 자리에 끼운 24장을 만든다.
새 슬라이드는 pptxgenjs가 원본과 같은 레이아웃(단일 blank slideLayout,
13.333"x7.5")으로 만들므로 파일을 그대로 복사해 등록만 하면 된다.

    build/  ├ unpacked/  원본 덱을 푼 것
            └ newu/      gen_slides.js 출력(new-slides.pptx)을 푼 것

사용법은 README.md 참고. 원본 덱 경로는 --base로 넘긴다.
"""
import argparse
import re
import shutil
import zipfile
from pathlib import Path

_ap = argparse.ArgumentParser(description=__doc__)
_ap.add_argument("--base", required=True, help="원본 덱 .pptx 경로 (13장)")
_ap.add_argument("--new", default="new-slides.pptx", help="gen_slides.js 출력")
_ap.add_argument("--out", default="build/out.pptx", help="완성 덱 저장 경로")
_ap.add_argument("--work", default="build", help="작업 폴더")
_args = _ap.parse_args()

WORK = Path(_args.work)
BASE = WORK / "unpacked"
NEW = WORK / "newu"

# 매번 깨끗하게 다시 푼다 — 이전 실행의 잔재가 섞이면 페이지 번호가 어긋난다.
for d in (BASE, NEW):
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
zipfile.ZipFile(_args.base).extractall(BASE)
zipfile.ZipFile(_args.new).extractall(NEW)

# newu의 slide1~11 → base의 slide14~24
MAP = {n: 13 + n for n in range(1, 12)}

# ── 1. 슬라이드 + 노트 파일 복사, rels 생성 ──
for src, dst in MAP.items():
    shutil.copy(NEW / f"ppt/slides/slide{src}.xml", BASE / f"ppt/slides/slide{dst}.xml")
    shutil.copy(
        NEW / f"ppt/notesSlides/notesSlide{src}.xml",
        BASE / f"ppt/notesSlides/notesSlide{dst}.xml",
    )
    (BASE / f"ppt/slides/_rels/slide{dst}.xml.rels").write_text(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
        f'<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        f'relationships/notesSlide" Target="../notesSlides/notesSlide{dst}.xml"/>'
        "</Relationships>",
        encoding="utf-8",
    )
    (BASE / f"ppt/notesSlides/_rels/notesSlide{dst}.xml.rels").write_text(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/notesMaster" Target="../notesMasters/notesMaster1.xml"/>'
        f'<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        f'relationships/slide" Target="../slides/slide{dst}.xml"/>'
        "</Relationships>",
        encoding="utf-8",
    )
print("1. 슬라이드/노트 복사 + rels 생성 완료")

# ── 2. [Content_Types].xml 등록 ──
ct_path = BASE / "[Content_Types].xml"
ct = ct_path.read_text(encoding="utf-8")
adds = []
for dst in MAP.values():
    for part, ctype in (
        (f"/ppt/slides/slide{dst}.xml",
         "application/vnd.openxmlformats-officedocument.presentationml.slide+xml"),
        (f"/ppt/notesSlides/notesSlide{dst}.xml",
         "application/vnd.openxmlformats-officedocument.presentationml.notesSlide+xml"),
    ):
        if f'PartName="{part}"' not in ct:
            adds.append(f'<Override PartName="{part}" ContentType="{ctype}"/>')
ct = ct.replace("</Types>", "".join(adds) + "</Types>")
ct_path.write_text(ct, encoding="utf-8")
print(f"2. [Content_Types].xml — Override {len(adds)}개 추가")

# ── 3. presentation.xml.rels 등록 ──
pr_path = BASE / "ppt/_rels/presentation.xml.rels"
pr = pr_path.read_text(encoding="utf-8")
used = {int(m) for m in re.findall(r'Id="rId(\d+)"', pr)}
next_id = max(used) + 1
rid_for = {}
new_rels = []
for dst in MAP.values():
    rid_for[dst] = f"rId{next_id}"
    new_rels.append(
        f'<Relationship Id="rId{next_id}" Type="http://schemas.openxmlformats.org/'
        f'officeDocument/2006/relationships/slide" Target="slides/slide{dst}.xml"/>'
    )
    next_id += 1
pr = pr.replace("</Relationships>", "".join(new_rels) + "</Relationships>")
pr_path.write_text(pr, encoding="utf-8")
print(f"3. presentation.xml.rels — {rid_for}")

# ── 4. <p:sldIdLst> 재배치 ──
p_path = BASE / "ppt/presentation.xml"
p = p_path.read_text(encoding="utf-8")
lst = re.search(r"<p:sldIdLst>(.*?)</p:sldIdLst>", p, re.S)
entries = re.findall(r"<p:sldId [^/]*?/>", lst.group(1))
assert len(entries) == 13, f"기존 슬라이드가 13장이 아니다: {len(entries)}"

# rId → 원본 슬라이드 번호 매핑
rid2slide = {
    m.group(1): int(m.group(2))
    for m in re.finditer(r'Id="(rId\d+)"[^>]*Target="slides/slide(\d+)\.xml"', pr)
}
by_slide = {}
for e in entries:
    rid = re.search(r'r:id="(rId\d+)"', e).group(1)
    by_slide[rid2slide[rid]] = e

max_sid = max(int(m) for m in re.findall(r'id="(\d+)"', lst.group(1)))
new_entry = {}
for dst in MAP.values():
    max_sid += 1
    new_entry[dst] = f'<p:sldId id="{max_sid}" r:id="{rid_for[dst]}"/>'

# 최종 순서 — 새 장 11개를 주제별 자리에 끼운다.
#   A 탐색 · B 필지 · K 한계 · C 평면도 → 원본 5번(공간구문론) 뒤
#   F 국내시장 · G 해외시장 → 원본 12번(COMPARISON) 앞
#   D vs 생성형AI · H 수익모델 · I 비용 · J 손익 · E 진행상황 → STATUS 앞
order = [1, 2, 3, 4, 5, "A", "B", "K", "C",
         6, 7, 8, 9, 10, 11, "F", "G", 12,
         "D", "H", "I", "J", "E", 13]
alias = {"A": 14, "B": 15, "C": 16, "D": 17, "E": 18,
         "F": 19, "G": 20, "H": 21, "I": 22, "J": 23, "K": 24}
seq = []
for o in order:
    seq.append(new_entry[alias[o]] if isinstance(o, str) else by_slide[o])
assert len(seq) == len(order)
p = p[: lst.start(1)] + "".join(seq) + p[lst.end(1) :]
p_path.write_text(p, encoding="utf-8")
print(f"4. <p:sldIdLst> 재배치 — {len(order)}장, 순서 {order}")

# ── 5. 기존 슬라이드 페이지 번호 재정렬 ──
PAGENO_X = "11368735"  # 페이지 번호 도형의 x 좌표 (오른쪽 하단)
renum = {6: "10", 7: "11", 8: "12", 9: "13", 10: "14", 11: "15", 12: "18", 13: "24"}
for old, new in renum.items():
    f = BASE / f"ppt/slides/slide{old}.xml"
    x = f.read_text(encoding="utf-8")
    cur = f"{old:02d}"
    # 페이지 번호 도형만 고른다 (x 좌표로 특정)
    shapes = [m for m in re.finditer(r"<p:sp>.*?</p:sp>", x, re.S)
              if PAGENO_X in m.group(0) and f"<a:t>{cur}</a:t>" in m.group(0)]
    assert len(shapes) == 1, f"slide{old}: 페이지번호 도형 {len(shapes)}개 — 확인 필요"
    sp = shapes[0]
    fixed = sp.group(0).replace(f"<a:t>{cur}</a:t>", f"<a:t>{new}</a:t>")
    x = x[: sp.start()] + fixed + x[sp.end() :]
    f.write_text(x, encoding="utf-8")
    print(f"   slide{old}: {cur} -> {new}")
print("5. 페이지 번호 재정렬 완료")


# ── 5b. 제품명 치환 (소유자 결정 2026-08-23) ──
# 제품명은 "목이 좋은 곳"이다. 방법론 이름(공간구문론 / Space Syntax)은 신뢰
# 근거라 남긴다 — 제품을 가리키던 자리만 바꾼다. 원본 덱은 건드리지 않고
# 매번 푼 사본에서 치환하므로, 원본을 새로 받아도 같은 결과가 나온다.
RENAME = {
    "Space Syntax 상권분석 — 공간구문론 기반 입지 엔진 + 대화형 AI 에이전트":
        "목이 좋은 곳 — 공간구문론(Space Syntax) 기반 입지 엔진 + 대화형 AI 에이전트",
    "Space Syntax 상권분석": "목이 좋은 곳",
    "Space Syntax 엔진": "공간구문론 엔진",
    "도면 업로드 → Space Syntax 자동 산출 (도시계획·공공기관용)":
        "도면 업로드 → 공간 구조 자동 산출 (도시계획·공공기관용)",
    # 표지 날짜 — 덱이 8/23 작업까지 담고 있으므로 맞춘다.
    "2026-08-10": "2026-08-23",
}

# ── 원본 5번 문구 교정 (소유자 결정 2026-08-23) ──
# 소유자가 PowerPoint에서 직접 고친 것을 여기로 옮겼다. 완성본을 손으로 고치면
# 다음 graft 실행이 원본을 새로 풀면서 지워 버리므로, 교정은 반드시 이 표에 둔다.
#
# 1) "실거래가 비교로" → "개별 공시지가 비교로". 저평가 판정의 종속변수는
#    개별공시지가다. 실거래는 지번이 마스킹돼 PNU를 만들 수 없어 필지에 붙지
#    않는다 (CLAUDE.md "분석 대상은 토지 가격이다").
# 2) "구조 높음/낮음" → "길 구조 좋음/나쁨". 높낮이는 무엇이 좋은 쪽인지 안
#    알려 준다. 앱에서 "목"·"길 구조"로 통일한 것과 같은 이유다.
WORDING = {
    "실거래가 비교로 ": "개별 공시지가 비교로 ",
    "구조 높음 + 지금 매출 낮음  ": "길 구조 좋음 + 지금 매출 낮음  ",
    "→ 아직 안 터진 자리": "→ 아직 안 터진 자리, 저평가된 지역",
    "구조 낮음 + 지금 매출 높음  ": "길 구조 나쁨 + 지금 매출 높음  ",
    # 오른쪽 2×2도 같은 말로 — 한 장 안에서 용어가 갈리면 그게 더 안 읽힌다.
    "구조 높음 · 매출 낮음": "길 구조 좋음 · 매출 낮음",
    "구조 높음 · 매출 높음": "길 구조 좋음 · 매출 높음",
    "구조 낮음 · 매출 낮음": "길 구조 나쁨 · 매출 낮음",
    "구조 낮음 · 매출 높음": "길 구조 나쁨 · 매출 높음",
}

renamed = 0
for f in sorted((BASE / "ppt/slides").glob("slide*.xml")):
    x = f.read_text(encoding="utf-8")
    orig = x
    for old, new in {**RENAME, **WORDING}.items():
        if old in x:
            renamed += x.count(old)
            x = x.replace(old, new)
    if x != orig:
        f.write_text(x, encoding="utf-8")
# 남아 있으면 알린다 — 런이 나뉘어 한 <a:t>에 안 담긴 경우다.
leftover = [
    f.name for f in sorted((BASE / "ppt/slides").glob("slide*.xml"))
    if "Space Syntax 상권분석" in f.read_text(encoding="utf-8")
]
print(f"5b. 제품명 치환 {renamed}건" + (f" — 남음: {leftover}" if leftover else ""))
assert not leftover, "제품명이 남았다 — 런이 나뉘어 있는지 확인할 것"

# ── 6. 다시 압축 ──
out = Path(_args.out)
out.parent.mkdir(parents=True, exist_ok=True)
if out.exists():
    out.unlink()
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for path in sorted(BASE.rglob("*")):
        if path.is_file():
            z.write(path, path.relative_to(BASE).as_posix())
print(f"6. 완성 — {out} ({out.stat().st_size:,} bytes)")
