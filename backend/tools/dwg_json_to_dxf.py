"""DWG에서 뽑은 JSON을 DXF로 되쓴다 — 실제 사무소 도면을 파이프라인에 넣기 위해.

**왜 JSON을 거치나.** DWG는 비공개 형식이라 파이썬에서 직접 못 읽는다. 읽을 수
있는 건 LibreDWG인데 GPL이라 제품에 넣으면 우리 코드까지 공개 의무가 붙는다
(`docs/ARCHITECTURE_RULES.md`의 조사 절). 그래서 **배포하지 않고 개발 기계에서만**
WASM 빌드를 돌려 기하를 JSON으로 뽑고, 그걸 여기서 DXF로 되쓴다. 제품은 지금처럼
DXF만 받는다 — 이 도구는 시험용 도면을 만들기 위한 것이다.

JSON 모양은 `scratchpad/dwgconv/dump.mjs`가 내는 것과 같다:
    {"items": [{"t":"L","layer":"WA1","a":[x,y],"b":[x,y]}, ...]}
    t: L=선 A=호 C=원 E=타원 P=폴리라인(bulge 포함) T=글자 I=블록참조

Run:
  cd backend && .venv/Scripts/python -m tools.dwg_json_to_dxf plan1.json out.dxf --units m
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import ezdxf

# $INSUNITS 코드. 건축 도면은 mm로 그리는 일이 가장 흔하다.
UNIT_CODES = {"mm": 4, "cm": 5, "m": 6, "inch": 1, "ft": 2}


def _add_layer(doc, name: str) -> None:
    if name and name not in doc.layers:
        try:
            doc.layers.add(name)
        except Exception:
            # 레이어 이름에 DXF가 금지하는 글자가 있으면 건너뛴다 — 엔티티는
            # 그대로 그 이름을 쓰고, 뷰어가 암묵적으로 만들어 준다.
            pass


def build(items: list[dict], units: str) -> "ezdxf.document.Drawing":
    doc = ezdxf.new("R2013")
    doc.header["$INSUNITS"] = UNIT_CODES.get(units, 4)
    msp = doc.modelspace()

    for item in items:
        layer = str(item.get("layer") or "0")
        _add_layer(doc, layer)
        attribs = {"layer": layer}
        kind = item.get("t")
        try:
            if kind == "L":
                msp.add_line(item["a"], item["b"], dxfattribs=attribs)
            elif kind == "A":
                msp.add_arc(
                    center=item["c"], radius=item["r"],
                    start_angle=item["s"], end_angle=item["e"], dxfattribs=attribs,
                )
            elif kind == "C":
                msp.add_circle(center=item["c"], radius=item["r"], dxfattribs=attribs)
            elif kind == "E":
                major = item.get("major") or [1.0, 0.0]
                msp.add_ellipse(
                    center=item["c"], major_axis=major,
                    ratio=item.get("ratio") or 1.0,
                    start_param=item.get("s") or 0.0,
                    end_param=item.get("e") if item.get("e") is not None else math.tau,
                    dxfattribs=attribs,
                )
            elif kind == "P":
                points = [(v[0], v[1], 0.0, 0.0, v[2] if len(v) > 2 else 0.0) for v in item["v"]]
                msp.add_lwpolyline(
                    points, format="xyseb",
                    close=bool(item.get("closed")), dxfattribs=attribs,
                )
            elif kind == "T":
                text = str(item.get("text") or "").strip()
                if text and item.get("p"):
                    msp.add_text(text, dxfattribs={**attribs, "height": 2.5}).set_placement(item["p"])
            elif kind == "I":
                # 블록 본체는 JSON에 없다. 자리만 점으로 남겨 「심볼이 여기 있다」를
                # 보존한다 — 계단·엘리베이터 위치가 그대로 쓸모 있다.
                if item.get("p"):
                    msp.add_point(item["p"], dxfattribs={**attribs, "layer": layer})
        except Exception:
            # 한 엔티티가 이상해도 도면 전체를 버리지 않는다.
            continue
    return doc


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("json_path", type=Path)
    parser.add_argument("dxf_path", type=Path)
    parser.add_argument("--units", default="mm", choices=sorted(UNIT_CODES))
    args = parser.parse_args()

    data = json.loads(args.json_path.read_text(encoding="utf-8"))
    items = data.get("items", [])
    doc = build(items, args.units)
    doc.saveas(args.dxf_path)

    counts: dict[str, int] = {}
    for item in items:
        counts[item.get("t", "?")] = counts.get(item.get("t", "?"), 0) + 1
    names = {"L": "선", "A": "호", "C": "원", "E": "타원", "P": "폴리라인", "T": "글자", "I": "블록"}
    summary = " · ".join(f"{names.get(k, k)} {v:,}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))
    print(f"wrote {args.dxf_path}")
    print(f"  {summary}")


if __name__ == "__main__":
    main()
