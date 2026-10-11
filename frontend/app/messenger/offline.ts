"use client";

export type StoredBlob = {
  id: string;
  scope: string;
  blob: Blob;
  name: string;
  type: string;
  size: number;
  createdAt: number;
};

export type OutboxAttachment = {
  blobId: string;
  name: string;
  type: string;
  size: number;
};

export type MessengerOutboxItem = {
  id: string;
  scope: string;
  conversationId: number;
  kind: "text" | "voice" | "video" | "video_note";
  body: string;
  replyTo: number | null;
  clientMessageId: string;
  remoteAttachments?: Array<Record<string, unknown>>;
  localAttachments?: OutboxAttachment[];
  voiceBlobId?: string | null;
  voiceAssetId?: string | null;
  videoBlobId?: string | null;
  videoAssetId?: string | null;
  cleanupBlobIds?: string[];
  createdAt: number;
  attempts: number;
  lastError?: string | null;
};

const DB_NAME = "boris-messenger-v8";
const DB_VERSION = 1;
const DRAFTS = "drafts";
const CACHE = "messages";
const OUTBOX = "outbox";
const BLOBS = "blobs";

function ensureBrowser(): void {
  if (typeof window === "undefined" || !("indexedDB" in window)) {
    throw new Error("offline_storage_unavailable");
  }
}

function requestResult<T = unknown>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error("indexeddb_request_failed"));
  });
}

function transactionDone(transaction: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error || new Error("indexeddb_transaction_failed"));
    transaction.onabort = () => reject(transaction.error || new Error("indexeddb_transaction_aborted"));
  });
}

async function database(): Promise<IDBDatabase> {
  ensureBrowser();
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(DRAFTS)) db.createObjectStore(DRAFTS, { keyPath: "key" });
      if (!db.objectStoreNames.contains(CACHE)) db.createObjectStore(CACHE, { keyPath: "key" });
      if (!db.objectStoreNames.contains(OUTBOX)) {
        const store = db.createObjectStore(OUTBOX, { keyPath: "id" });
        store.createIndex("scope", "scope", { unique: false });
      }
      if (!db.objectStoreNames.contains(BLOBS)) {
        const store = db.createObjectStore(BLOBS, { keyPath: "id" });
        store.createIndex("scope", "scope", { unique: false });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error("indexeddb_open_failed"));
    request.onblocked = () => reject(new Error("indexeddb_upgrade_blocked"));
  });
}

function scopedKey(scope: string, conversationId: number): string {
  return `${scope}:${conversationId}`;
}

export async function saveDraft(scope: string, conversationId: number, value: string): Promise<void> {
  if (!scope || !conversationId) return;
  const db = await database();
  try {
    const transaction = db.transaction(DRAFTS, "readwrite");
    const store = transaction.objectStore(DRAFTS);
    const key = scopedKey(scope, conversationId);
    if (value) store.put({ key, scope, conversationId, value, updatedAt: Date.now() });
    else store.delete(key);
    await transactionDone(transaction);
  } finally {
    db.close();
  }
}

export async function loadDraft(scope: string, conversationId: number): Promise<string> {
  if (!scope || !conversationId) return "";
  const db = await database();
  try {
    const transaction = db.transaction(DRAFTS, "readonly");
    const value = await requestResult<any>(transaction.objectStore(DRAFTS).get(scopedKey(scope, conversationId)));
    return typeof value?.value === "string" ? value.value : "";
  } finally {
    db.close();
  }
}

export async function cacheMessages(scope: string, conversationId: number, messages: unknown[]): Promise<void> {
  if (!scope || !conversationId) return;
  const db = await database();
  try {
    const transaction = db.transaction(CACHE, "readwrite");
    transaction.objectStore(CACHE).put({
      key: scopedKey(scope, conversationId),
      scope,
      conversationId,
      messages,
      updatedAt: Date.now(),
    });
    await transactionDone(transaction);
  } finally {
    db.close();
  }
}

export async function loadCachedMessages<T = unknown>(scope: string, conversationId: number): Promise<T[]> {
  if (!scope || !conversationId) return [];
  const db = await database();
  try {
    const transaction = db.transaction(CACHE, "readonly");
    const value = await requestResult<any>(transaction.objectStore(CACHE).get(scopedKey(scope, conversationId)));
    return Array.isArray(value?.messages) ? value.messages as T[] : [];
  } finally {
    db.close();
  }
}

export async function putBlob(scope: string, blob: Blob, name: string): Promise<StoredBlob> {
  const id = crypto.randomUUID();
  const record: StoredBlob = {
    id,
    scope,
    blob,
    name,
    type: blob.type || "application/octet-stream",
    size: blob.size,
    createdAt: Date.now(),
  };
  const db = await database();
  try {
    const transaction = db.transaction(BLOBS, "readwrite");
    transaction.objectStore(BLOBS).put(record);
    await transactionDone(transaction);
    return record;
  } finally {
    db.close();
  }
}

export async function getBlob(id: string): Promise<StoredBlob | null> {
  if (!id) return null;
  const db = await database();
  try {
    const transaction = db.transaction(BLOBS, "readonly");
    return (await requestResult<StoredBlob | undefined>(transaction.objectStore(BLOBS).get(id))) || null;
  } finally {
    db.close();
  }
}

export async function deleteBlob(id: string): Promise<void> {
  if (!id) return;
  const db = await database();
  try {
    const transaction = db.transaction(BLOBS, "readwrite");
    transaction.objectStore(BLOBS).delete(id);
    await transactionDone(transaction);
  } finally {
    db.close();
  }
}

export async function enqueueOutbox(item: MessengerOutboxItem): Promise<void> {
  const db = await database();
  try {
    const transaction = db.transaction(OUTBOX, "readwrite");
    transaction.objectStore(OUTBOX).put(item);
    await transactionDone(transaction);
  } finally {
    db.close();
  }
}

export async function listOutbox(scope: string): Promise<MessengerOutboxItem[]> {
  if (!scope) return [];
  const db = await database();
  try {
    const transaction = db.transaction(OUTBOX, "readonly");
    const index = transaction.objectStore(OUTBOX).index("scope");
    const records = await requestResult<MessengerOutboxItem[]>(index.getAll(IDBKeyRange.only(scope)));
    return records.sort((a, b) => a.createdAt - b.createdAt);
  } finally {
    db.close();
  }
}

export async function updateOutbox(item: MessengerOutboxItem): Promise<void> {
  const db = await database();
  try {
    const transaction = db.transaction(OUTBOX, "readwrite");
    transaction.objectStore(OUTBOX).put(item);
    await transactionDone(transaction);
  } finally {
    db.close();
  }
}

export async function removeOutbox(id: string): Promise<void> {
  const db = await database();
  try {
    const transaction = db.transaction(OUTBOX, "readwrite");
    transaction.objectStore(OUTBOX).delete(id);
    await transactionDone(transaction);
  } finally {
    db.close();
  }
}

export async function purgeScope(scope: string): Promise<void> {
  if (!scope) return;
  const db = await database();
  try {
    for (const storeName of [DRAFTS, CACHE, OUTBOX, BLOBS]) {
      const transaction = db.transaction(storeName, "readwrite");
      const store = transaction.objectStore(storeName);
      if (storeName === OUTBOX || storeName === BLOBS) {
        const index = store.index("scope");
        const keys = await requestResult<IDBValidKey[]>(index.getAllKeys(IDBKeyRange.only(scope)));
        keys.forEach(key => store.delete(key));
      } else {
        const cursor = store.openCursor();
        await new Promise<void>((resolve, reject) => {
          cursor.onsuccess = () => {
            const current = cursor.result;
            if (!current) return resolve();
            if (current.value?.scope === scope) current.delete();
            current.continue();
          };
          cursor.onerror = () => reject(cursor.error || new Error("indexeddb_cursor_failed"));
        });
      }
      await transactionDone(transaction);
    }
  } finally {
    db.close();
  }
}
