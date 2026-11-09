import type { AgentAskResponse } from "@/lib/api";

export interface ChatResultSnapshot {
  sessionId: string;
  updatedAt: number;
  result: AgentAskResponse;
}

const DB_NAME = "space-syntax-chat";
const DB_VERSION = 1;
const STORE_NAME = "analysis-snapshots";

// IndexedDB가 막힌 브라우저와 테스트 환경에서도, 현재 탭 안에서 대화를 오갈 때는
// 최신 결과가 사라지지 않게 한다. 영속 저장은 아래 IndexedDB가 담당한다.
const memorySnapshots = new Map<string, ChatResultSnapshot>();

function openDatabase(): Promise<IDBDatabase | null> {
  if (typeof indexedDB === "undefined") return Promise.resolve(null);
  return new Promise((resolve) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(STORE_NAME)) db.createObjectStore(STORE_NAME, { keyPath: "sessionId" });
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => resolve(null);
    request.onblocked = () => resolve(null);
  });
}

export async function saveChatResultSnapshot(
  sessionId: string,
  result: AgentAskResponse,
  updatedAt: number = Date.now(),
): Promise<ChatResultSnapshot> {
  const snapshot = { sessionId, result, updatedAt };
  memorySnapshots.set(sessionId, snapshot);
  const db = await openDatabase();
  if (!db) return snapshot;
  await new Promise<void>((resolve) => {
    const transaction = db.transaction(STORE_NAME, "readwrite");
    transaction.objectStore(STORE_NAME).put(snapshot);
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => resolve();
    transaction.onabort = () => resolve();
  });
  db.close();
  return snapshot;
}

export async function loadChatResultSnapshot(sessionId: string): Promise<ChatResultSnapshot | null> {
  const memory = memorySnapshots.get(sessionId);
  if (memory) return memory;
  const db = await openDatabase();
  if (!db) return null;
  const snapshot = await new Promise<ChatResultSnapshot | null>((resolve) => {
    const request = db.transaction(STORE_NAME, "readonly").objectStore(STORE_NAME).get(sessionId);
    request.onsuccess = () => resolve(isSnapshot(request.result) ? request.result : null);
    request.onerror = () => resolve(null);
  });
  db.close();
  if (snapshot) memorySnapshots.set(sessionId, snapshot);
  return snapshot;
}

export async function removeChatResultSnapshot(sessionId: string): Promise<void> {
  memorySnapshots.delete(sessionId);
  const db = await openDatabase();
  if (!db) return;
  await new Promise<void>((resolve) => {
    const transaction = db.transaction(STORE_NAME, "readwrite");
    transaction.objectStore(STORE_NAME).delete(sessionId);
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => resolve();
    transaction.onabort = () => resolve();
  });
  db.close();
}

function isSnapshot(value: unknown): value is ChatResultSnapshot {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.sessionId === "string" &&
    typeof candidate.updatedAt === "number" &&
    typeof candidate.result === "object" &&
    candidate.result !== null
  );
}
