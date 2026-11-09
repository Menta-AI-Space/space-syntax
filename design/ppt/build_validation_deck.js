/**
 * 검증 결과 발표 덱 (12장) — docs/VALIDATION_REPORT_2026-09.md 를 슬라이드로 옮긴 것.
 *
 * 교수님께 «보내는» 자료라 발표자 없이 읽혀도 이해되게 썼다. 그래서 슬라이드에
 * 결론 문장을 넣고, 말로 할 부분은 addNotes 로 12장 모두에 붙였다.
 *
 * ⚠️ 수치를 여기서 고치지 마라. 정본은 docs/DECISIONS_LAND_MODEL.md §12~§24 이고
 * VALIDATION_REPORT 가 그 파생본이다. 셋이 어긋나면 DECISIONS 를 따른다.
 *
 * ⚠️ 이 환경의 LibreOffice 는 pptx 를 못 읽는다(최소 파일도 실패) — 이미지 QA 가
 * 안 되므로 기하 검사(박스 경계·겹침·한글 전각 기준 넘침 추정)로 대신했다.
 * 한글 폰트는 Malgun Gothic 을 쓰되, 렌더러가 다르면 폭이 달라지므로 여백을 넉넉히 뒀다.
 *
 * Run:
 *   npm install pptxgenjs && node design/ppt/build_validation_deck.js
 *   → 검증결과_발표.pptx (빌드 산출물이라 저장소에 넣지 않는다)
 */
const pptxgen = require("pptxgenjs");
const p = new pptxgen();
p.layout = "LAYOUT_WIDE";            // 13.3 x 7.5
p.author = "space-syntax";
p.title = "길 구조는 상권의 무엇을 설명하는가 — 검증 결과";

const NAVY="1E2761", INK="141B34", ICE="CADCFC", LIGHT="F2F5FA", WHITE="FFFFFF";
const TEXT="1A1A2E", MUTED="6B7280", PASS="0F766E", FAIL="B85042", WARN="9A6B1E";
const HF="Malgun Gothic", BF="Malgun Gothic";
const W=13.3, H=7.5, M=0.7;

// ---------- helpers ----------
function darkSlide(){ const s=p.addSlide(); s.background={color:NAVY}; return s; }
function lightSlide(title, kicker){
  const s=p.addSlide(); s.background={color:WHITE};
  if(kicker) s.addText(kicker,{x:M,y:0.42,w:W-2*M,h:0.3,fontSize:12,bold:true,color:PASS,
    fontFace:BF,charSpacing:1.5,isTextBox:true,margin:0});
  s.addText(title,{x:M,y:0.72,w:W-2*M,h:0.72,fontSize:32,bold:true,color:NAVY,
    fontFace:HF,isTextBox:true,margin:0,valign:"top"});
  return s;
}
function card(s,x,y,w,h,fill){ s.addShape(p.ShapeType.roundRect,{x,y,w,h,fill:{color:fill||LIGHT},
  rectRadius:0.08,line:{color:fill||LIGHT,width:0}}); }
function numDot(s,x,y,n,col){
  s.addShape(p.ShapeType.ellipse,{x,y,w:0.42,h:0.42,fill:{color:col||NAVY},line:{color:col||NAVY,width:0}});
  s.addText(String(n),{x,y,w:0.42,h:0.42,fontSize:15,bold:true,color:WHITE,fontFace:HF,
    align:"center",valign:"middle",isTextBox:true,margin:0});
}
function foot(s,t){ s.addText(t,{x:M,y:H-0.62,w:W-2*M,h:0.3,fontSize:10,color:MUTED,
  fontFace:BF,isTextBox:true,margin:0}); }

const chartBase = () => ({
  showLegend:false, showTitle:false, showValue:true, dataLabelPosition:"outEnd",
  dataLabelFontSize:12, dataLabelFontBold:true, dataLabelColor:TEXT, dataLabelFontFace:BF,
  catAxisLabelColor:TEXT, catAxisLabelFontSize:12, catAxisLabelFontFace:BF,
  valAxisLabelColor:MUTED, valAxisLabelFontSize:10, valAxisLabelFontFace:BF,
  valGridLine:{color:"E3E7EF", size:1}, catGridLine:{style:"none"},
  barGapWidthPct:55, valAxisHidden:false,
});

/* ===================== 1. 표지 ===================== */
{
  const s=darkSlide();
  s.addShape(p.ShapeType.ellipse,{x:9.7,y:-1.5,w:6.2,h:6.2,fill:{color:"27337A"},line:{width:0}});
  s.addShape(p.ShapeType.ellipse,{x:11.4,y:4.3,w:3.4,h:3.4,fill:{color:"1A2560"},line:{width:0}});
  s.addText("공간구문론 기반 상권 분석 · 검증 보고",{x:M,y:1.5,w:8.4,h:0.34,fontSize:13,bold:true,
    color:ICE,fontFace:BF,charSpacing:2,isTextBox:true,margin:0});
  s.addText("길 구조는 상권의\n무엇을 설명하는가",{x:M,y:2.0,w:8.6,h:1.9,fontSize:44,bold:true,
    color:WHITE,fontFace:HF,lineSpacing:52,isTextBox:true,margin:0});
  s.addText("「지금」은 설명한다. 「미래」는 설명하지 못한다.\n그리고 단위는 「상권」이지 「필지」가 아니다.",
    {x:M,y:4.15,w:8.6,h:0.95,fontSize:17,color:ICE,fontFace:BF,lineSpacing:28,isTextBox:true,margin:0});
  s.addShape(p.ShapeType.roundRect,{x:M,y:5.45,w:8.6,h:0.82,fill:{color:"27337A"},rectRadius:0.08,line:{width:0}});
  s.addText("검증 실험 9건 · 상권 1,648개 · 필지 898,268개 · 정답지 4종",
    {x:M+0.28,y:5.45,w:8.1,h:0.82,fontSize:14,bold:true,color:WHITE,fontFace:BF,
     valign:"middle",isTextBox:true,margin:0});
  s.addText("2026-09-10 기준",{x:M,y:6.6,w:6,h:0.3,fontSize:12,color:"8C9BC4",fontFace:BF,isTextBox:true,margin:0});
  s.addNotes("이 발표는 지난 8월 23일 발표 이후 3주간 진행한 검증의 결과입니다. 결론부터 말씀드리면, 저희가 그때 주장했던 것 중 상당수를 저희 손으로 기각했습니다. 오늘 보여드릴 것은 '무엇이 됐다'보다 '무엇이 안 됐는지, 그리고 그걸 어떻게 확인했는지'입니다.");
}

/* ===================== 2. 무엇을 물었나 ===================== */
{
  const s=lightSlide("질문은 둘이다 — 섞으면 안 된다","01  연구 질문");
  const boxes=[
    {x:M, t:"A. 지금 설명하는가", q:"길 구조가 좋은 곳은\n지금 장사가 잘 되나?",
     m:"상관분석 · 5분위", c:NAVY},
    {x:7.05, t:"B. 미래를 맞히는가", q:"길 구조로 앞으로 자랄 곳을\n미리 찍을 수 있나?",
     m:"백테스트 · Precision@K", c:NAVY},
  ];
  boxes.forEach(b=>{
    card(s,b.x,1.85,5.55,3.0,LIGHT);
    s.addText(b.t,{x:b.x+0.4,y:2.15,w:4.8,h:0.42,fontSize:20,bold:true,color:b.c,fontFace:HF,isTextBox:true,margin:0});
    s.addText(b.q,{x:b.x+0.4,y:2.72,w:4.8,h:1.0,fontSize:16,color:TEXT,fontFace:BF,lineSpacing:26,isTextBox:true,margin:0});
    s.addText("측정 방법",{x:b.x+0.4,y:3.85,w:4.8,h:0.26,fontSize:10,bold:true,color:MUTED,fontFace:BF,charSpacing:1,isTextBox:true,margin:0});
    s.addText(b.m,{x:b.x+0.4,y:4.13,w:4.8,h:0.42,fontSize:15,bold:true,color:PASS,fontFace:BF,isTextBox:true,margin:0});
  });
  card(s,M,5.2,W-2*M,1.15,"FBF3E7");
  s.addText([
    {text:"A가 된다고 B가 되는 것이 아니다. ",options:{bold:true,color:INK}},
    {text:"이 프로젝트의 가장 큰 발견이 정확히 그것이다 — 같은 지표가 「지금」은 잘 설명하면서 「미래」에는 무작위보다 못하다.",options:{color:TEXT}},
  ],{x:M+0.4,y:5.2,w:W-2*M-0.8,h:1.15,fontSize:14,fontFace:BF,valign:"middle",lineSpacing:23,isTextBox:true,margin:0});
  s.addNotes("검증을 시작하면서 가장 먼저 한 일이 질문을 둘로 나눈 것입니다. '지금 설명하는가'와 '미래를 맞히는가'는 전혀 다른 질문인데, 저희는 처음에 이걸 하나로 봤습니다. 결과적으로 이 구분이 이번 검증의 핵심이 됐습니다.");
}

/* ===================== 3. 속지 않기 위한 장치 ===================== */
{
  const s=lightSlide("속지 않기 위해 건 장치 여섯 개","02  검증 설계");
  const items=[
    ["사전 등록","창·정답 비율·K·대조군을 돌리기 전에 문서에 박았다. 잘 나올 때까지 바꿔 돌리는 것을 금지 규칙으로 뒀다."],
    ["공간 홀드아웃","가까운 땅끼리 값이 비슷하므로 법정동 블록으로 훈련·시험을 갈랐다. 「옆집 답 보기」를 막는다."],
    ["무작위 기준선","「17개 맞혔다」는 그 자체로 의미가 없다. 실제로 300번 셔플해 기준선을 구했다."],
    ["성분별 대조군","지표 X가 성능을 내면 X의 각 성분만으로도 같은 성능이 나오는지 반드시 확인한다."],
    ["공간 분산 진단","상위 K가 한 동네에 몰리면 그 동네 운에 점수가 좌우된다. 걸친 동 수를 함께 본다."],
    ["자기 추론 반증","「Integration이 더 나을 것」이라 예측을 먼저 적고 시험했다. 반대로 나왔다."],
  ];
  const cw=3.85, ch=1.72;
  items.forEach((it,i)=>{
    const col=i%3, row=Math.floor(i/3);
    const x=M+col*(cw+0.33), y=1.8+row*(ch+0.36);
    card(s,x,y,cw,ch,LIGHT);
    numDot(s,x+0.3,y+0.28,i+1,NAVY);
    s.addText(it[0],{x:x+0.85,y:y+0.3,w:cw-1.1,h:0.38,fontSize:15,bold:true,color:NAVY,fontFace:HF,isTextBox:true,margin:0});
    s.addText(it[1],{x:x+0.3,y:y+0.8,w:cw-0.6,h:0.82,fontSize:11,color:TEXT,fontFace:BF,lineSpacing:17,isTextBox:true,margin:0});
  });
  foot(s,"④와 ⑤는 실제로 저희가 보고했던 성과를 뒤집었습니다 — 슬라이드 10 참조");
  s.addNotes("검증에서 중요한 건 '무엇을 쟀나'보다 '어떻게 속지 않았나'입니다. 여섯 가지 장치를 걸었는데, 이 중 네 번째와 다섯 번째는 저희가 이미 성과로 보고했던 결과를 스스로 뒤집는 데 쓰였습니다. 뒤에서 보여드리겠습니다.");
}

/* ===================== 4. 순환성 ===================== */
{
  const s=lightSlide("정답지를 넷으로 둔 이유 — 순환성","03  가장 중요한 개념");
  card(s,M,1.8,6.1,3.15,LIGHT);
  s.addText("개별공시지가는 이렇게 매겨진다",{x:M+0.35,y:2.05,w:5.4,h:0.36,fontSize:15,bold:true,color:NAVY,fontFace:HF,isTextBox:true,margin:0});
  s.addText("공시지가 = 비교표준지 × 토지가격비준표",{x:M+0.35,y:2.5,w:5.4,h:0.34,fontSize:14,bold:true,color:TEXT,fontFace:BF,isTextBox:true,margin:0});
  card(s,M+0.35,2.95,5.4,1.05,"E8EDF7");
  s.addText("비준표 항목:  · 도로에 접했나\n                    · 도로에서 얼마나 머나",
    {x:M+0.6,y:2.95,w:5.0,h:1.05,fontSize:13,bold:true,color:NAVY,fontFace:BF,valign:"middle",lineSpacing:21,isTextBox:true,margin:0});
  s.addText("즉 공시지가는 「도로를 보고 매긴 값」이다.\n그런데 우리 목 점수도 도로에서 나온다.",
    {x:M+0.35,y:4.12,w:5.4,h:0.66,fontSize:13,color:TEXT,fontFace:BF,lineSpacing:21,isTextBox:true,margin:0});

  card(s,7.25,1.8,5.35,3.15,"FBF3E7");
  s.addText("둘이 닮은 것은 발견이 아니라 산수다",{x:7.6,y:2.05,w:4.7,h:0.36,fontSize:15,bold:true,color:WARN,fontFace:HF,isTextBox:true,margin:0});
  s.addText("같은 재료로 만들었기 때문이다.",{x:7.6,y:2.46,w:4.7,h:0.3,fontSize:13,color:TEXT,fontFace:BF,isTextBox:true,margin:0});
  s.addText([
    {text:"비유  ",options:{bold:true,color:WARN}},
    {text:"선생님이 출석번호를 이름 가나다순으로 매겨 놓고 「출석번호와 이름 순서가 상관있다」고 발표한다면? 당연하다 — 그렇게 만들었으니까.",options:{color:TEXT}},
  ],{x:7.6,y:2.9,w:4.7,h:1.05,fontSize:12.5,fontFace:BF,lineSpacing:20,isTextBox:true,margin:0});
  s.addText("→ 공시지가로 나온 결과는 우리 기여로 셀 수 없다.",
    {x:7.6,y:4.1,w:4.7,h:0.66,fontSize:13,bold:true,color:WARN,fontFace:BF,lineSpacing:21,isTextBox:true,margin:0});

  const rows=[
    [{text:"정답지",options:{bold:true}},{text:"누가 매기나",options:{bold:true}},{text:"순환성",options:{bold:true}}],
    ["개별공시지가","정부가 도로를 보고","있다 — 결과를 쓸 수 없다"],
    ["상권 매출","손님이 지갑을 열어서","없다"],
    ["시장 임대료","시장이","없다"],
  ];
  s.addTable(rows,{x:M,y:5.25,w:W-2*M,colW:[3.2,4.3,4.4],rowH:0.36,fontSize:13,fontFace:BF,
    color:TEXT,border:{type:"solid",color:"E3E7EF",pt:1},fill:{color:WHITE},valign:"middle"});
  foot(s,"살아남은 결과가 전부 순환성 없는 축에 있습니다 — 그것이 이 결론의 무게입니다");
  s.addNotes("이 슬라이드가 방법론의 핵심입니다. 개별공시지가는 정부가 도로 조건을 보고 매기는 값입니다. 그런데 저희 지표도 도로에서 나옵니다. 그러니 둘이 상관되는 건 발견이 아니라 당연한 산수입니다. 그래서 정답지를 넷으로 늘렸고, 결과적으로 이 결정이 값을 했습니다 — 공시지가로는 보이던 차이가 시장 임대료로 재니 사라졌습니다.");
}

/* ===================== 5. 검증 지도 (8칸) ===================== */
{
  const s=lightSlide("정답지 4 × 질문 2 = 여덟 칸, 통과는 한 칸","04  검증 지도");
  const rows=[
    [{text:"정답지",options:{bold:true,color:WHITE,fill:{color:NAVY}}},
     {text:"지금 설명하는가",options:{bold:true,color:WHITE,fill:{color:NAVY}}},
     {text:"미래를 맞히는가",options:{bold:true,color:WHITE,fill:{color:NAVY}}}],
    [{text:"상권 매출",options:{bold:true}},
     {text:"✅  5분위 3.3배 (16억→54억)\n     상관 +0.24 · 재측정 +0.343",options:{color:PASS,bold:true}},
     {text:"❌  Choice 17~20 (무작위 15)\n     Integration 7~8 — 역방향",options:{color:FAIL}}],
    [{text:"유동인구",options:{bold:true}},
     {text:"❌  +0.075 · 5분위가 꺾인다\n     Choice는 아예 음수 (−0.06)",options:{color:FAIL}},
     {text:"—  현재도 못 맞혀 정답지에서 제외",options:{color:MUTED}}],
    [{text:"개별공시지가",options:{bold:true}},
     {text:"△  상권 안 1.14배 — 단 순환성\n     우리 기여로 셀 수 없다",options:{color:WARN}},
     {text:"❌  목 단독 1 / 0 / 0 (무작위 10)\n     부호도 반대 (−0.044)",options:{color:FAIL}}],
    [{text:"시장 임대료",options:{bold:true}},
     {text:"❌  Choice +0.036 · Integration −0.006\n     5분위 1.01배 — 차이 없음",options:{color:FAIL}},
     {text:"—  시계열 1년뿐이라 불가",options:{color:MUTED}}],
  ];
  s.addTable(rows,{x:M,y:1.8,w:W-2*M,colW:[2.6,4.65,4.65],rowH:[0.42,0.88,0.88,0.88,0.88],
    fontSize:12,fontFace:BF,color:TEXT,border:{type:"solid",color:"E3E7EF",pt:1},
    fill:{color:WHITE},valign:"middle"});
  card(s,M,5.9,W-2*M,0.86,LIGHT);
  s.addText([
    {text:"읽는 법 — ",options:{bold:true,color:NAVY}},
    {text:"순환성 없는 정답지(매출)에서만 「지금」이 통과했다. 「미래」는 어느 정답지에서도 통과하지 못했다.",options:{color:TEXT}},
  ],{x:M+0.35,y:5.9,w:W-2*M-0.7,h:0.86,fontSize:13.5,fontFace:BF,valign:"middle",isTextBox:true,margin:0});
  s.addNotes("이 표가 검증 전체의 지도입니다. 정답지 넷에 질문 둘이니 여덟 칸인데, 통과한 건 왼쪽 위 한 칸뿐입니다. 나머지는 저희가 직접 기각했습니다. 특히 세 번째 줄 공시지가는 숫자가 나오긴 했는데, 앞 슬라이드의 순환성 때문에 저희 기여로 셀 수 없습니다.");
}

/* ===================== 6. 통과 1 — 상권 매출 ===================== */
{
  const s=lightSlide("길 구조가 좋은 상권은 지금 매출이 높다","05  ✅ 살아남은 결과 ①");
  s.addChart(p.ChartType.bar,[{name:"분기 평균 매출 중앙값(억원)",labels:["1분위\n(목 나쁨)","2분위","3분위","4분위","5분위\n(목 좋음)"],
    values:[16.2,32.4,38.3,48.0,53.6]}],
    Object.assign(chartBase(),{x:M,y:1.75,w:7.0,h:4.15,chartColors:[NAVY,NAVY,NAVY,"2A3B86",PASS],
      varyColors:true, valAxisMaxVal:62, dataLabelFormatCode:'#0.0"억"'}));
  s.addText("상권 1,648개를 목 점수로 5등분한 «분기 평균» 매출 중앙값\n(원자료 「당월_매출_금액」이 분기 합계다 — 월 환산은 ÷3)",{x:M,y:5.86,w:7.0,h:0.52,fontSize:10.5,color:MUTED,fontFace:BF,lineSpacing:15,isTextBox:true,margin:0});

  card(s,8.1,1.75,4.5,1.5,LIGHT);
  s.addText("3.3배",{x:8.1,y:1.82,w:4.5,h:1.02,fontSize:46,bold:true,color:PASS,fontFace:HF,align:"center",valign:"middle",isTextBox:true,margin:0});
  s.addText("최하위 분위 대비 최상위 분위",{x:8.1,y:2.86,w:4.5,h:0.3,fontSize:12,color:MUTED,fontFace:BF,align:"center",isTextBox:true,margin:0});

  s.addText("왜 이 결과가 무거운가",{x:8.1,y:3.45,w:4.5,h:0.32,fontSize:14,bold:true,color:NAVY,fontFace:HF,isTextBox:true,margin:0});
  s.addText([
    {text:"상권 1,648개 전수 — 표본 우연이 아니다",options:{bullet:true,breakLine:true}},
    {text:"5분위가 한 번도 안 꺾인다(단조)",options:{bullet:true,breakLine:true}},
    {text:"순환성이 없다 — 매출은 행정이 매기지 않는다",options:{bullet:true}},
  ],{x:8.1,y:3.85,w:4.5,h:1.3,fontSize:12,color:TEXT,fontFace:BF,paraSpaceAfter:7,isTextBox:true,margin:0});

  card(s,8.1,5.25,4.5,0.95,"FBF3E7");
  s.addText("정직하게: +0.24는 강한 상관이 아니다. 매출 차이의 약 6%를 설명한다.",
    {x:8.32,y:5.25,w:4.06,h:0.95,fontSize:11.5,color:INK,fontFace:BF,valign:"middle",lineSpacing:18,isTextBox:true,margin:0});
  s.addNotes("첫 번째 살아남은 결과입니다. 상권 1,648개를 전수로, 목 점수 순으로 5등분해서 매출 중앙값을 봤습니다. 16억에서 54억으로 한 계단도 꺾이지 않고 올라갑니다. 다만 정직하게 말씀드리면 상관계수 자체는 +0.24로 강하지 않습니다. 매출 차이의 6% 정도를 설명하는 수준입니다. 그럼에도 무게가 있는 건 전수조사이고, 단조이고, 무엇보다 순환성이 없는 축이기 때문입니다. 단위를 꼭 붙여 말씀하세요 — 이 금액은 «분기» 평균입니다. 원자료인 우리마을가게 당월_매출_금액이 이름과 달리 분기 합계라서, 월로 읽으면 3배 틀립니다. 다만 3.3배는 비율이라 영향받지 않습니다.");
}

/* ===================== 7. 통과 2 — 후행지표 실패 ===================== */
{
  const s=lightSlide("후행지표로 고르면 무작위보다 나쁘다","06  ✅ 살아남은 결과 ② — 가장 강한 결과");
  s.addChart(p.ChartType.bar,[
    {name:"2022→2025",labels:["지금 매출 상위\n(경쟁사 방식)","점포수 상위","유동인구 상위","무작위"],values:[10,8,6,15]},
    {name:"2021→2024",labels:["지금 매출 상위\n(경쟁사 방식)","점포수 상위","유동인구 상위","무작위"],values:[4,5,5,15]},
  ],Object.assign(chartBase(),{x:M,y:1.8,w:7.5,h:4.0,chartColors:[FAIL,"D8A296"],
    showLegend:true, legendPos:"b", legendFontSize:11, legendColor:TEXT, valAxisMaxVal:18}));
  s.addText("156개를 찍어 정답과 겹친 개수 (Precision@K)",{x:M,y:5.85,w:7.5,h:0.28,fontSize:11,color:MUTED,fontFace:BF,isTextBox:true,margin:0});

  card(s,8.5,1.8,4.1,2.0,LIGHT);
  s.addText("세 지표 전부,\n네 칸 전부에서\n무작위 아래",{x:8.75,y:1.95,w:3.6,h:1.15,fontSize:19,bold:true,color:FAIL,fontFace:HF,lineSpacing:27,isTextBox:true,margin:0});
  s.addText("두 개의 창, 두 개의 정답 정의에서\n한 번도 뒤집히지 않았다",{x:8.75,y:3.12,w:3.6,h:0.56,fontSize:11.5,color:MUTED,fontFace:BF,lineSpacing:18,isTextBox:true,margin:0});

  s.addText("기전: 평균회귀",{x:8.5,y:4.05,w:4.1,h:0.32,fontSize:14,bold:true,color:NAVY,fontFace:HF,isTextBox:true,margin:0});
  s.addText("이미 잘 되는 상권은 앞으로 덜 자란다. 지금 잘 되는 곳을 고르는 것은 눈 감고 찍느니만 못하다.",
    {x:8.5,y:4.45,w:4.1,h:0.9,fontSize:12,color:TEXT,fontFace:BF,lineSpacing:19,isTextBox:true,margin:0});
  card(s,8.5,5.45,4.1,0.78,"E6F0EE");
  s.addText("저희가 만든 것이 아니라 데이터가 그렇게 나왔습니다.",
    {x:8.72,y:5.45,w:3.66,h:0.78,fontSize:11.5,bold:true,color:PASS,fontFace:BF,valign:"middle",lineSpacing:18,isTextBox:true,margin:0});
  s.addNotes("이것이 이 프로젝트에서 가장 강한 결과입니다. 저희가 잘한다는 얘기가 아니라, 기존 방식이 작동하지 않는다는 것을 데이터가 직접 보여줍니다. 2022년에 서서 매출 상위 상권을 찍으면 156개 중 10개, 2021년 기준으로는 4개만 맞습니다. 눈 감고 찍으면 15개입니다. 점포수와 유동인구도 마찬가지입니다. 평균회귀 때문입니다.");
}

/* ===================== 8. 실패 — 미래 예측 ===================== */
{
  const s=lightSlide("그러나 길 구조로도 미래를 맞히지 못했다","07  ❌ 기각된 결과");
  s.addChart(p.ChartType.bar,[
    {name:"Choice",labels:["22→25 원","22→25 초과","21→24 원","21→24 초과"],values:[17,18,20,18]},
    {name:"Integration p90",labels:["22→25 원","22→25 초과","21→24 원","21→24 초과"],values:[7,8,8,8]},
    {name:"무작위 기준선",labels:["22→25 원","22→25 초과","21→24 원","21→24 초과"],values:[15,15,15,15]},
  ],Object.assign(chartBase(),{x:M,y:1.8,w:7.5,h:3.9,chartColors:[NAVY,FAIL,"B8BEC9"],
    showLegend:true, legendPos:"b", legendFontSize:11, legendColor:TEXT, valAxisMaxVal:24}));
  s.addText("Precision@156 · 무작위 기준선 15 · 표준편차 3.6",{x:M,y:5.78,w:7.5,h:0.28,fontSize:11,color:MUTED,fontFace:BF,isTextBox:true,margin:0});

  card(s,8.5,1.8,4.1,1.55,LIGHT);
  s.addText("전체 상관 ≈ 0",{x:8.72,y:1.95,w:3.66,h:0.46,fontSize:21,bold:true,color:FAIL,fontFace:HF,isTextBox:true,margin:0});
  s.addText("−0.007 · −0.002 · +0.026 · +0.025\n상권 1,562개 전체 기준",{x:8.72,y:2.48,w:3.66,h:0.72,fontSize:12,color:TEXT,fontFace:BF,lineSpacing:19,isTextBox:true,margin:0});

  s.addText("어떻게 판정했나",{x:8.5,y:3.6,w:4.1,h:0.32,fontSize:14,bold:true,color:NAVY,fontFace:HF,isTextBox:true,margin:0});
  s.addText([
    {text:"Choice 17~20 → 0.4~1.2 표준편차. 우연과 구분되지 않는다",options:{bullet:true,breakLine:true}},
    {text:"Integration 7~8 → −2.2 표준편차. 진짜로 무작위보다 낮다",options:{bullet:true}},
  ],{x:8.5,y:4.0,w:4.1,h:1.25,fontSize:11.5,color:TEXT,fontFace:BF,paraSpaceAfter:8,isTextBox:true,margin:0});
  card(s,8.5,5.35,4.1,0.9,"FBF3E7");
  s.addText("Integration은 이미 성숙한 중심이라 더 자랄 여지가 적다.",
    {x:8.72,y:5.35,w:3.66,h:0.9,fontSize:11.5,color:INK,fontFace:BF,valign:"middle",lineSpacing:18,isTextBox:true,margin:0});
  s.addNotes("같은 실험의 나머지 절반입니다. 길 구조로 찍어도 안 됐습니다. Choice는 17에서 20 사이인데 무작위가 15입니다. 표준편차가 3.6이니 이 차이는 우연 범위 안입니다. 결정적인 건 상권 1,562개 전체에 대한 상관이 사실상 0이라는 점입니다. 관계 자체가 없습니다. Integration은 오히려 무작위보다 낮은데, 이쪽은 마이너스 2.2 표준편차라 실제 신호입니다 — 성숙한 중심은 덜 자란다는 뜻입니다.");
}

/* ===================== 9. 필지 수준 — 공시지가 vs 시장가 ===================== */
{
  const s=lightSlide("같은 필지, 자를 바꾸니 차이가 사라졌다","08  ❌ 필지 수준 기각");
  const panes=[
    {x:M,   t:"공시지가로 재면",   v:"1.14배", c:WARN, d:["필지 38,602개 · 상권 741개","5분위 단조 증가 (0.95 → 1.08)","방향은 맞는다"], f:"그러나 비준표에 도로접면·도로거리가 있다 → 순환성"},
    {x:6.95,t:"시장 임대료로 재면", v:"1.01배", c:FAIL, d:["오피스텔 실거래 68,448건","5분위 0.96 / 0.98 / 1.00 / 0.98 / 0.97","단조도 아니다"], f:"Choice +0.036 · Integration −0.006 — 사실상 0"},
  ];
  panes.forEach(pn=>{
    card(s,pn.x,1.8,5.65,4.0,LIGHT);
    s.addText(pn.t,{x:pn.x+0.35,y:2.02,w:4.95,h:0.36,fontSize:16,bold:true,color:NAVY,fontFace:HF,isTextBox:true,margin:0});
    s.addText(pn.v,{x:pn.x+0.35,y:2.42,w:4.95,h:0.98,fontSize:46,bold:true,color:pn.c,fontFace:HF,valign:"middle",isTextBox:true,margin:0});
    s.addText(pn.d.map((t,i)=>({text:t,options:{bullet:true,breakLine:i<pn.d.length-1}})),
      {x:pn.x+0.35,y:3.48,w:4.95,h:1.15,fontSize:12,color:TEXT,fontFace:BF,paraSpaceAfter:6,isTextBox:true,margin:0});
    card(s,pn.x+0.35,4.74,4.95,0.86,WHITE);
    s.addText(pn.f,{x:pn.x+0.55,y:4.74,w:4.55,h:0.86,fontSize:11.5,color:pn.c,bold:true,fontFace:BF,valign:"middle",lineSpacing:18,isTextBox:true,margin:0});
  });
  s.addShape(p.ShapeType.rightArrow,{x:6.42,y:3.5,w:0.45,h:0.4,fill:{color:NAVY},line:{width:0}});
  card(s,M,6.0,W-2*M,0.85,"FBF3E7");
  s.addText([
    {text:"⚠️ 최종 반증으로 삼지 않는다 — ",options:{bold:true,color:WARN}},
    {text:"오피스텔은 상가가 아니고(주거 수요 지배), 역세권에 몰려 있어 범위 제한이 있으며, 건물 스펙을 통제하지 않았다.",options:{color:INK}},
  ],{x:M+0.35,y:6.0,w:W-2*M-0.7,h:0.85,fontSize:12.5,fontFace:BF,valign:"middle",lineSpacing:19,isTextBox:true,margin:0});
  s.addNotes("이게 순환성이 실제로 문제가 된 사례입니다. 같은 필지들을 두 개의 자로 쟀습니다. 정부가 매긴 공시지가로는 1.14배 차이가 보이는데, 시장이 매긴 임대료로는 1.01배, 즉 차이가 없습니다. 1.14배는 정부 공식을 되읽은 것이었을 가능성이 높습니다. 다만 이걸 최종 결론으로 삼진 않습니다 — 오피스텔은 상가가 아니라서 한계가 큽니다.");
}

/* ===================== 10. 스스로 뒤집은 것 ===================== */
{
  const s=lightSlide("저희가 저희 손으로 뒤집은 것 넷","09  이 발표의 중심");
  const items=[
    {t:"「따라잡기 갭」 자진 철회",
     b:"「길은 좋은데 아직 값이 안 붙은 곳」이 더 자란다고 보고했다. 목을 빼고 「값 하위만」 고른 대조군을 넣으니 그쪽이 더 높았다.",
     n:"갭 42   vs   값 하위만 52~59"},
    {t:"8/23 발표의 「백분위 97」도 같은 착시",
     b:"「갭 상위 1%의 목 백분위 평균 97(전체 58.6)」. ΔR² 0.1%p 가 작다는 지적을 막으려 넣은 방어 논리였는데, 근거가 갭이라 함께 무너졌다.",
     n:"갭 ↔ 구조 +0.673 / 가격 −0.715 — 대칭"},
    {t:"최고점 58점은 한 동네 몰빵",
     b:"대형허가 지표가 58점(무작위 9)으로 그날 최고였다. 공간 분산을 보니 상위 100필지가 전부 한 법정동이었다.",
     n:"걸친 법정동 1개 · 최대 비중 100%"},
    {t:"corr +0.389 는 표본 16개의 우연",
     b:"법정동 16개로 재서 「동 단위로는 작동한다」고 보고했다. 격자 183개로 늘리니 +0.10~0.18 로 줄고 단조도 깨졌다.",
     n:"표본 16 → 183 으로 늘리자 사라짐"},
  ];
  const CW=5.81, CH=1.92;
  items.forEach((it,i)=>{
    const x=M+(i%2)*(CW+0.28), y=1.72+Math.floor(i/2)*(CH+0.16);
    card(s,x,y,CW,CH,LIGHT);
    numDot(s,x+0.3,y+0.22,i+1,FAIL);
    s.addText(it.t,{x:x+0.86,y:y+0.24,w:CW-1.16,h:0.4,fontSize:15,bold:true,color:NAVY,
      fontFace:HF,valign:"middle",isTextBox:true,margin:0});
    s.addText(it.b,{x:x+0.3,y:y+0.72,w:CW-0.6,h:0.72,fontSize:11.5,color:TEXT,
      fontFace:BF,lineSpacing:18,isTextBox:true,margin:0});
    card(s,x+0.3,y+1.48,CW-0.6,0.34,WHITE);
    s.addText(it.n,{x:x+0.42,y:y+1.48,w:CW-0.84,h:0.34,fontSize:11,bold:true,color:FAIL,
      fontFace:BF,valign:"middle",align:"center",isTextBox:true,margin:0});
  });
  card(s,M,5.86,W-2*M,0.98,NAVY);
  s.addText([
    {text:"넷 모두 대조군 한 줄이 없거나 표본이 작아서 생긴 착시였습니다. ",options:{bold:true,color:WHITE}},
    {text:"그래서 「지표 X가 성능을 내면 X의 각 성분만으로도 같은 성능이 나오는지 반드시 확인한다」를 강제 규칙으로 뒀습니다.",options:{color:ICE}},
  ],{x:M+0.4,y:5.86,w:W-2*M-0.8,h:0.98,fontSize:13,fontFace:BF,valign:"middle",lineSpacing:21,isTextBox:true,margin:0});
  s.addNotes("이 슬라이드가 오늘 발표의 중심입니다. 넷 모두 저희가 이미 성과로 보고했던 것인데, 대조군을 추가하거나 표본을 늘리니 사라졌습니다. 두 번째가 지난 8월 발표에 들어갔던 각주입니다 — ΔR² 가 0.1%p 로 작다는 지적이 나올 것 같아 「상위 목록의 품질은 좋다」는 방어로 넣었는데, 그 근거가 갭이었습니다. 갭과의 상관이 구조 플러스 0.673, 가격 마이너스 0.715 로 대칭인 것이 오히려 증거입니다 — 가격 쪽 절반만으로 같은 성능이 났습니다. 세 번째 58점은 그날 최고 숫자라 그대로 보고할 뻔했는데, 상위 100필지가 전부 한 동네였습니다.");
}

/* ===================== 11. 지표 발견 ===================== */
{
  const s=lightSlide("반경보다 「지표 종류」가 훨씬 크게 갈랐다","10  부수적 발견");
  s.addChart(p.ChartType.line,[
    {name:"Integration (닿기 쉬운 곳)",labels:["반경 500m","1,000m","2,000m","5,000m"],values:[0.176,0.316,0.297,0.313]},
    {name:"Choice (지나다니는 길)",labels:["반경 500m","1,000m","2,000m","5,000m"],values:[0.080,0.099,0.132,0.126]},
  ],{x:M,y:1.85,w:7.3,h:3.9,chartColors:[PASS,"B8BEC9"],lineSize:3.5,lineDataSymbolSize:9,
     showLegend:true,legendPos:"b",legendFontSize:11,legendColor:TEXT,
     showValue:true,dataLabelFontSize:11,dataLabelColor:TEXT,dataLabelFontFace:BF,dataLabelPosition:"t",
     catAxisLabelColor:TEXT,catAxisLabelFontSize:12,catAxisLabelFontFace:BF,
     valAxisLabelColor:MUTED,valAxisLabelFontSize:10,valAxisLabelFontFace:BF,
     valGridLine:{color:"E3E7EF",size:1},catGridLine:{style:"none"},
     valAxisMaxVal:0.40,valAxisMinVal:0,showTitle:false});
  s.addText("상권 매출과의 편상관 (면적 통제) · 도심 5개구 상권 339개",{x:M,y:5.8,w:7.3,h:0.28,fontSize:11,color:MUTED,fontFace:BF,isTextBox:true,margin:0});

  card(s,8.35,1.85,4.25,1.62,LIGHT);
  s.addText("2배 이상",{x:8.35,y:1.98,w:4.25,h:0.62,fontSize:34,bold:true,color:PASS,fontFace:HF,align:"center",isTextBox:true,margin:0});
  s.addText("Integration 최고 +0.343\nChoice 최고 +0.169",{x:8.35,y:2.66,w:4.25,h:0.62,fontSize:12,color:TEXT,fontFace:BF,align:"center",lineSpacing:19,isTextBox:true,margin:0});

  s.addText("두 가지가 나왔다",{x:8.35,y:3.65,w:4.25,h:0.32,fontSize:14,bold:true,color:NAVY,fontFace:HF,isTextBox:true,margin:0});
  s.addText([
    {text:"반경을 5km까지 키워도 설명력이 늘지 않는다 → 「측정 탓」이라는 변명이 닫혔다",options:{bullet:true,breakLine:true}},
    {text:"Integration은 반경 1km에서 정점을 찍고 평탄해진다 → 상업 매출의 스케일은 1km 안팎의 접근성이다",options:{bullet:true}},
  ],{x:8.35,y:4.05,w:4.25,h:1.75,fontSize:11.5,color:TEXT,fontFace:BF,paraSpaceAfter:9,isTextBox:true,margin:0});
  foot(s,"⚠️ 서울 전역이 아니라 도심 5개구(종로·중구·용산·서대문·마포) 339개 상권 기준입니다");
  s.addNotes("앞의 기각 결과들이 '저희 구현이 국소적이라 그런 것 아니냐'는 반박을 받을 수 있어서, 도심 5개구를 한 그래프로 놓고 반경을 500미터부터 5킬로미터까지 늘려가며 다시 계산했습니다. 설명력이 늘지 않았습니다. 대신 예상 못 한 게 나왔는데, 반경보다 지표 종류가 훨씬 크게 갈랐습니다. Integration이 Choice의 두 배 이상이고, 1킬로미터에서 정점을 찍습니다.");
}

/* ===================== 12. 결론 + 남은 숙제 ===================== */
{
  const s=darkSlide();
  s.addShape(p.ShapeType.ellipse,{x:10.6,y:-2.0,w:5.6,h:5.6,fill:{color:"27337A"},line:{width:0}});
  s.addText("11  결론과 남은 숙제",{x:M,y:0.55,w:8,h:0.3,fontSize:12,bold:true,color:ICE,fontFace:BF,charSpacing:2,isTextBox:true,margin:0});
  s.addText("정직하게 말할 수 있는 단위는\n「상권」이지 「필지」가 아니다",{x:M,y:0.95,w:9.2,h:1.3,fontSize:30,bold:true,color:WHITE,fontFace:HF,lineSpacing:40,isTextBox:true,margin:0});

  const claims=[
    ["✅","길 구조가 좋은 상권은 지금 매출이 높다 — 5분위 3.3배",PASS],
    ["✅","후행지표로 고르면 무작위보다 나쁘다 — 네 칸 전부",PASS],
    ["❌","길 구조가 미래 성장을 예측한다 — 우연과 구분 안 됨",FAIL],
    ["❌","필지 수준 시장가를 가른다 — 시장 임대료로는 0",FAIL],
  ];
  claims.forEach((c,i)=>{
    const y=2.5+i*0.52;
    s.addText(c[0],{x:M,y,w:0.4,h:0.44,fontSize:15,color:c[2]===PASS?"4ADE9B":"E89B8B",fontFace:BF,valign:"middle",isTextBox:true,margin:0});
    s.addText(c[1],{x:M+0.45,y,w:7.2,h:0.44,fontSize:14,color:WHITE,fontFace:BF,valign:"middle",isTextBox:true,margin:0});
  });

  s.addShape(p.ShapeType.roundRect,{x:8.2,y:2.5,w:4.4,h:2.35,fill:{color:"27337A"},rectRadius:0.08,line:{width:0}});
  s.addText("남은 숙제",{x:8.5,y:2.7,w:3.8,h:0.34,fontSize:15,bold:true,color:ICE,fontFace:HF,isTextBox:true,margin:0});
  s.addText([
    {text:"상가 임대료 필지 단위 확보 — 유일한 정면 돌파구",options:{bullet:true,breakLine:true}},
    {text:"연도별 도로망 (시불변 지표의 시점 누수 제거)",options:{bullet:true,breakLine:true}},
    {text:"서울 전역 반경 스윕 확대",options:{bullet:true,breakLine:true}},
    {text:"상관계수 신뢰구간 · Precision@K 정식 검정",options:{bullet:true}},
  ],{x:8.5,y:3.12,w:3.8,h:1.6,fontSize:11,color:WHITE,fontFace:BF,paraSpaceAfter:7,isTextBox:true,margin:0});

  s.addShape(p.ShapeType.roundRect,{x:M,y:5.2,w:11.9,h:1.35,fill:{color:"0E1836"},rectRadius:0.08,line:{width:0}});
  s.addText([
    {text:"저희가 한 것은 가설을 지지하는 증거를 모은 것이 아니라,\n",options:{bold:true,color:WHITE,breakLine:true}},
    {text:"기각 조건을 미리 정해 놓고 실제로 기각한 것입니다.",options:{bold:true,color:"4ADE9B"}},
  ],{x:M+0.45,y:5.2,w:11.0,h:1.35,fontSize:17,fontFace:HF,valign:"middle",lineSpacing:30,isTextBox:true,margin:0});
  s.addNotes("정리하겠습니다. 살아남은 주장은 둘이고, 둘 다 상권 단위입니다. 필지 단위와 미래 예측은 저희가 기각했습니다. 남은 숙제 중 가장 중요한 건 상가 임대료를 필지 단위로 확보하는 것입니다. 상권 수준에서는 되는데 필지 수준에서는 안 되는 이 모순이, 오피스텔 데이터를 써서 그런 것인지 원래 안 되는 것인지를 그것으로만 가를 수 있습니다. 마지막으로, 저희가 한 일은 증거를 모은 게 아니라 기각 조건을 미리 정해 놓고 실제로 기각한 것입니다.");
}

p.writeFile({fileName:"검증결과_발표.pptx"}).then(f=>console.log("작성 완료:",f));
