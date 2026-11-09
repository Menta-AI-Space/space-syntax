#!/bin/sh
# 10개 신도시를 차례로 — cityseer 가상환경. 길 원본(osm_*.json)·아파트(apts_*.json)는 받아 둔 것을 쓰고,
# 없을 때만 Overpass에 묻는다. 실패하면 1·2·4·8분 쉬고 다시. FORCE=1이면 이미 있는 결과도 다시 만든다.
cd "$(dirname "$0")"
for t in 운정 판교 별내 미사 동탄2 광교 세종 위례 김포한강 다산; do
  if [ -z "$FORCE" ] && [ -f "data/blocks_$t.csv" ]; then echo "$t 있음"; continue; fi
  wait=60
  for try in 1 2 3 4 5; do
    PYTHONUTF8=1 PYTHONIOENCODING=utf-8 /c/dev/tools/cityseer-venv/Scripts/python.exe -W ignore cityseer_town.py "$t" data > "data/log_$t.txt" 2>&1
    if grep -q "블록 [0-9]" "data/log_$t.txt"; then grep -a "블록 [0-9]" "data/log_$t.txt"; break; fi
    echo "$t 실패 $try — ${wait}s 뒤 다시"; grep -a -E "Error|실패" "data/log_$t.txt" | tail -n 2
    sleep $wait; wait=$((wait * 2))
  done
done
echo 끝
