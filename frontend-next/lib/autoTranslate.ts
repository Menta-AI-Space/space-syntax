/**
 * 영어 모드 자동 번역층 (2026-11-09 소유자: 「영어가 기본 — 탭 안도, 이름도 한국어잖아」).
 *
 * 화면 코드·서버 문장·AI 답이 모두 한국어로 쓰여 있어서 문장마다 영어판을 손으로 붙이는 대신, 영어 모드에선
 * 화면에 뜬 한국어 글 조각(글자 노드와 placeholder·aria-label·title·alt)을 모아 서버 `/api/translate`(GPT +
 * 용어집)로 옮겨 그 자리에 바꿔 끼운다. 옮긴 문장은 이 브라우저(localStorage)와 서버에 남아 다음부터는 바로 바뀐다.
 *
 * - React가 그린 글자 노드의 값만 바꾼다(노드를 새로 만들거나 감싸지 않는다) — 감싸면 React가 지울 때 깨진다.
 * - 입력칸·코드·`translate="no"`·`.notranslate` 안은 건드리지 않는다. 캔버스에 그린 글자(도면 이름표)는 못 바꾼다.
 * - 글이 계속 바뀌는 중이면(AI 답이 흘러나오는 중) 멈춘 뒤 0.5초 지나서 묻는다 — 조각마다 묻지 않게.
 * - 한국어 모드로 돌아가면 페이지를 다시 연다(lib/i18n.tsx) — 바꿔 끼운 글을 되돌리는 것보다 확실하다.
 */

import { api } from "@/lib/api";
import { hasHangul, splitPad } from "@/lib/lang";

export { hasHangul, splitPad };

const ATTRS = ["placeholder", "aria-label", "title", "alt"] as const;
const SKIP = "script,style,textarea,input,select,code,pre,[translate='no'],.notranslate,[contenteditable='true']";
// v2 (2026-11-09): 용어집이 「목 좋은 곳」을 high-footfall location으로 바꿔 옛 번역(prime spot)을 버린다.
const STORAGE_KEY = "ui-tr-en-v2";
const MAX_STORED = 6000;
const SETTLE_MS = 500;
const BATCH = 100;

class AutoTranslator {
  private cache = new Map<string, string>();
  /** 우리가 써 넣은 글 — 그 글이 다시 바뀜 알림으로 와도 또 옮기지 않는다(번역에 한글 지명이 남을 수 있다). */
  private outputs = new Set<string>();
  private queue = new Set<string>();
  private inflight = new Set<string>();
  private settle = new Map<Node, number>();
  private observer: MutationObserver | null = null;
  private flushTimer: number | null = null;
  private saveTimer: number | null = null;
  private failures = 0;
  private running = false;
  private listeners = new Set<() => void>();

  /** 캔버스처럼 글자 노드가 없는 곳용 — 옮긴 게 있으면 주고, 없으면 원문을 주고 물어 둔다. */
  lookup(text: string): string {
    if (!hasHangul(text)) return text;
    const [lead, core, trail] = splitPad(text);
    const hit = this.cache.get(core);
    if (hit !== undefined) return lead + hit + trail;
    this.want(core);
    return text;
  }

  subscribe(cb: () => void): () => void {
    this.listeners.add(cb);
    return () => this.listeners.delete(cb);
  }

  constructor() {
    try {
      const raw = window.localStorage.getItem(STORAGE_KEY);
      if (raw) for (const [k, v] of Object.entries(JSON.parse(raw) as Record<string, string>)) this.cache.set(k, v);
    } catch {
      // 저장소를 못 읽어도 번역은 된다
    }
  }

  start() {
    if (this.running) return;
    this.running = true;
    const root = document.documentElement;
    this.scan(root);
    this.observer = new MutationObserver((records) => {
      for (const r of records) {
        if (r.type === "characterData") this.text(r.target as Text);
        else if (r.type === "attributes") this.attrs(r.target as Element);
        else r.addedNodes.forEach((n) => this.scan(n));
      }
    });
    this.observer.observe(root, {
      subtree: true,
      childList: true,
      characterData: true,
      attributes: true,
      attributeFilter: [...ATTRS],
    });
  }

  private skipped(el: Element | null): boolean {
    return !el || !!el.closest(SKIP);
  }

  private scan(node: Node) {
    if (node.nodeType === Node.TEXT_NODE) {
      this.text(node as Text);
      return;
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return;
    const el = node as Element;
    this.attrs(el);
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      if (n.nodeType === Node.TEXT_NODE) this.text(n as Text);
      else this.attrs(n as Element);
    }
  }

  private text(node: Text) {
    const value = node.nodeValue;
    if (!hasHangul(value) || this.outputs.has(value!) || this.skipped(node.parentElement)) return;
    const [lead, core, trail] = splitPad(value!);
    const hit = this.cache.get(core);
    if (hit !== undefined) {
      const next = lead + hit + trail;
      this.outputs.add(next);
      if (next !== value) node.nodeValue = next;
      return;
    }
    // 흘러나오는 글은 멈출 때까지 기다린다.
    const old = this.settle.get(node);
    if (old) window.clearTimeout(old);
    this.settle.set(
      node,
      window.setTimeout(() => {
        this.settle.delete(node);
        if (!node.isConnected || node.nodeValue !== value) return;
        this.want(core);
      }, SETTLE_MS),
    );
  }

  private attrs(el: Element) {
    if (this.skipped(el) && !(el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement)) return;
    for (const name of ATTRS) {
      const value = el.getAttribute(name);
      if (!hasHangul(value) || this.outputs.has(value!)) continue;
      const hit = this.cache.get(value!.trim());
      if (hit !== undefined) {
        this.outputs.add(hit);
        el.setAttribute(name, hit);
      } else {
        this.want(value!.trim());
      }
    }
  }

  private want(core: string) {
    if (!core || this.cache.has(core) || this.inflight.has(core)) return;
    this.queue.add(core);
    if (this.flushTimer == null) {
      // 실패가 이어지면 천천히 다시 묻는다 — 서버가 꺼져 있을 때 요청을 쏟아붓지 않게.
      const wait = this.failures ? Math.min(30_000, 1000 * 2 ** this.failures) : 120;
      this.flushTimer = window.setTimeout(() => void this.flush(), wait);
    }
  }

  private async flush() {
    this.flushTimer = null;
    const batch = [...this.queue].slice(0, BATCH);
    batch.forEach((t) => {
      this.queue.delete(t);
      this.inflight.add(t);
    });
    if (!batch.length) return;
    let changed = false;
    try {
      const { translations } = await api.translate(batch);
      batch.forEach((src, i) => {
        const dst = translations[i];
        if (typeof dst === "string" && dst.trim()) {
          this.cache.set(src, dst);
          changed = true;
        }
      });
      this.failures = 0;
    } catch {
      this.failures += 1;
      batch.forEach((t) => this.queue.add(t));
    } finally {
      batch.forEach((t) => this.inflight.delete(t));
    }
    if (changed) {
      this.scan(document.documentElement);
      this.saveSoon();
      this.listeners.forEach((cb) => cb());
    }
    if (this.queue.size && this.flushTimer == null) this.want([...this.queue][0]);
  }

  private saveSoon() {
    if (this.saveTimer != null) return;
    this.saveTimer = window.setTimeout(() => {
      this.saveTimer = null;
      try {
        const entries = [...this.cache.entries()].slice(-MAX_STORED);
        window.localStorage.setItem(STORAGE_KEY, JSON.stringify(Object.fromEntries(entries)));
      } catch {
        // 저장 못 해도 이번 화면은 바뀌어 있다
      }
    }, 1500);
  }
}

let instance: AutoTranslator | null = null;

let active = false;

/** 영어 모드에서 한 번 부른다. 여러 번 불러도 하나만 돈다. */
export function startAutoTranslate() {
  if (typeof window === "undefined") return;
  instance ??= new AutoTranslator();
  active = true;
  instance.start();
}

/** 캔버스에 그리는 글(도면 이름표 등) — 영어 모드면 옮긴 글, 아직 없거나 한국어 모드면 원문. */
export function translateText(text: string): string {
  return active && instance ? instance.lookup(text) : text;
}

/** 번역이 새로 도착하면 부른다 — 캔버스를 다시 그릴 때 쓴다. */
export function onTranslations(cb: () => void): () => void {
  if (typeof window === "undefined") return () => undefined;
  instance ??= new AutoTranslator();
  return instance.subscribe(cb);
}
