const CONFIG_KEY = "borisCheckpointConfigV1";
const STATUS_KEY = "borisCheckpointStatusesV1";
const TAB_MAP_KEY = "borisCheckpointTabMapV1";

async function getSession(key, fallback) {
  const result = await chrome.storage.session.get(key);
  return result[key] ?? fallback;
}

async function setSession(key, value) {
  await chrome.storage.session.set({ [key]: value });
}

function entryById(config, entryId) {
  return (config?.entries || []).find((entry) => entry.id === entryId) || null;
}

function normHost(value) {
  try {
    return new URL(String(value || "")).hostname.toLowerCase().replace(/^www\./, "");
  } catch (_) {
    return "";
  }
}

function normPath(value) {
  try {
    return new URL(String(value || "")).pathname.replace(/\/+$/, "") || "/";
  } catch (_) {
    return "";
  }
}

function senderHost(sender) {
  return normHost(sender?.tab?.url || sender?.url || "");
}

function senderIsCenter(sender) {
  const host = senderHost(sender);
  const url = String(sender?.tab?.url || sender?.url || "");
  return host === "boris-ai.pro" && /\/seo-checkpoints(?:\/|$|\?)/.test(url);
}

function senderMatchesEntry(sender, entry) {
  if (!entry) return false;
  const expected = normHost(entry.action_url || entry.host || "");
  const actual = senderHost(sender);
  return Boolean(expected && actual && expected === actual);
}

async function updateStatus(entryId, patch) {
  const statuses = await getSession(STATUS_KEY, {});
  statuses[entryId] = { ...(statuses[entryId] || {}), ...patch };
  await setSession(STATUS_KEY, statuses);
  return statuses[entryId];
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.storage.session
    .setAccessLevel({ accessLevel: "TRUSTED_CONTEXTS" })
    .catch(() => {});
});

chrome.runtime.onStartup.addListener(() => {
  chrome.storage.session
    .setAccessLevel({ accessLevel: "TRUSTED_CONTEXTS" })
    .catch(() => {});
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  (async () => {
    if (!message || !message.type) {
      sendResponse({ ok: false, error: "bad_message" });
      return;
    }

    if (message.type === "PING") {
      if (!senderIsCenter(sender)) {
        sendResponse({ ok: false, error: "center_only" });
        return;
      }
      sendResponse({
        ok: true,
        version: 2,
        extensionVersion: chrome.runtime.getManifest().version,
      });
      return;
    }

    if (message.type === "SYNC_CONFIG") {
      if (!senderIsCenter(sender)) {
        sendResponse({ ok: false, error: "center_only" });
        return;
      }
      const payload = message.payload || {};
      await setSession(CONFIG_KEY, payload);
      const statuses = await getSession(STATUS_KEY, {});
      const validIds = new Set((payload.entries || []).map((entry) => entry.id));
      const cleaned = {};
      for (const [id, state] of Object.entries(statuses)) {
        if (validIds.has(id)) cleaned[id] = state;
      }
      await setSession(STATUS_KEY, cleaned);
      sendResponse({ ok: true, entries: (payload.entries || []).length });
      return;
    }

    if (message.type === "OPEN_ENTRIES") {
      if (!senderIsCenter(sender)) {
        sendResponse({ ok: false, error: "center_only" });
        return;
      }
      const config = await getSession(CONFIG_KEY, {});
      const ids = Array.isArray(message.entryIds) ? message.entryIds : [];
      const tabMap = await getSession(TAB_MAP_KEY, {});
      let opened = 0;
      for (const id of ids) {
        const entry = entryById(config, id);
        if (!entry?.action_url) continue;
        try {
          const url = new URL(entry.action_url);
          url.hash = "boris-checkpoint=" + encodeURIComponent(id);
          const tab = await chrome.tabs.create({
            url: url.toString(),
            active: opened === 0,
          });
          if (tab?.id !== undefined) {
            tabMap[String(tab.id)] = {
              entryId: id,
              actionUrl: entry.action_url,
            };
          }
          await updateStatus(id, {
            state: "opened",
            openedAt: new Date().toISOString(),
            pageUrl: url.toString(),
            actionUrl: entry.action_url,
          });
          opened += 1;
        } catch (_) {}
      }
      await setSession(TAB_MAP_KEY, tabMap);
      sendResponse({ ok: true, opened });
      return;
    }

    if (message.type === "GET_ENTRY") {
      const config = await getSession(CONFIG_KEY, {});
      const entry = entryById(config, message.entryId);
      if (!entry || !senderMatchesEntry(sender, entry)) {
        sendResponse({ ok: false, error: "entry_host_mismatch" });
        return;
      }
      sendResponse({ ok: true, entry });
      return;
    }

    if (message.type === "USER_SUBMIT_ATTEMPT") {
      const config = await getSession(CONFIG_KEY, {});
      const entry = entryById(config, message.entryId);
      if (!entry || !senderMatchesEntry(sender, entry)) {
        sendResponse({ ok: false, error: "entry_host_mismatch" });
        return;
      }
      const state = await updateStatus(message.entryId, {
        state: "attempted",
        attemptedAt: new Date().toISOString(),
        pageUrl: message.pageUrl || sender?.tab?.url || "",
        actionUrl: entry.action_url,
        trigger: message.trigger || "submit_attempt",
      });
      sendResponse({ ok: true, status: state });
      return;
    }

    if (message.type === "USER_SUBMITTED") {
      const config = await getSession(CONFIG_KEY, {});
      const entry = entryById(config, message.entryId);
      if (!entry || !senderMatchesEntry(sender, entry)) {
        sendResponse({ ok: false, error: "entry_host_mismatch" });
        return;
      }
      const state = await updateStatus(message.entryId, {
        state: "submitted",
        submittedAt: new Date().toISOString(),
        pageUrl: message.pageUrl || sender?.tab?.url || "",
        actionUrl: entry.action_url,
        trigger: message.trigger || "manual_confirm",
      });
      sendResponse({ ok: true, status: state });
      return;
    }

    if (message.type === "MARK_FILLED") {
      const config = await getSession(CONFIG_KEY, {});
      const entry = entryById(config, message.entryId);
      if (!entry || !senderMatchesEntry(sender, entry)) {
        sendResponse({ ok: false, error: "entry_host_mismatch" });
        return;
      }
      const statuses = await getSession(STATUS_KEY, {});
      const current = statuses[message.entryId] || {};
      const state = await updateStatus(message.entryId, {
        state:
          current.state === "submitted" || current.state === "attempted"
            ? current.state
            : "filled",
        filledAt: new Date().toISOString(),
        pageUrl: message.pageUrl || sender?.tab?.url || current.pageUrl || "",
      });
      sendResponse({ ok: true, status: state });
      return;
    }

    if (message.type === "GET_STATUS") {
      if (!senderIsCenter(sender)) {
        sendResponse({ ok: false, error: "center_only" });
        return;
      }
      const statuses = await getSession(STATUS_KEY, {});
      sendResponse({ ok: true, statuses });
      return;
    }

    if (message.type === "ACK_ENTRY") {
      if (!senderIsCenter(sender)) {
        sendResponse({ ok: false, error: "center_only" });
        return;
      }
      const state = await updateStatus(message.entryId, {
        ackedAt: new Date().toISOString(),
        ackedSubmittedAt: message.submittedAt || null,
      });
      sendResponse({ ok: true, status: state });
      return;
    }

    sendResponse({ ok: false, error: "unknown_message" });
  })().catch((error) => {
    sendResponse({ ok: false, error: String(error?.message || error) });
  });
  return true;
});

chrome.tabs.onUpdated.addListener(async (tabId, changeInfo, tab) => {
  if (!changeInfo.url && changeInfo.status !== "complete") return;

  const tabMap = await getSession(TAB_MAP_KEY, {});
  const mapped = tabMap[String(tabId)];
  if (!mapped) return;
  const entryId = typeof mapped === "string" ? mapped : mapped.entryId;
  const actionUrl = typeof mapped === "string" ? "" : mapped.actionUrl;

  const statuses = await getSession(STATUS_KEY, {});
  const current = statuses[entryId] || {};
  const pageUrl = String(changeInfo.url || tab?.url || current.pageUrl || "");
  if (!pageUrl) return;

  if (current.state === "attempted") {
    const pathChanged =
      normPath(pageUrl) &&
      normPath(actionUrl || current.actionUrl) &&
      normPath(pageUrl) !== normPath(actionUrl || current.actionUrl);
    const looksLikeAuthForm = /\/(register|registration|signup|sign-up|login)(?:\/|$)/i.test(
      normPath(pageUrl)
    );
    const samePlatformHost =
      normHost(pageUrl) &&
      normHost(actionUrl || current.actionUrl) &&
      normHost(pageUrl) === normHost(actionUrl || current.actionUrl);
    if (pathChanged && samePlatformHost && !looksLikeAuthForm) {
      statuses[entryId] = {
        ...current,
        state: "submitted",
        submittedAt: new Date().toISOString(),
        pageUrl,
        trigger: "successful_navigation",
      };
      await setSession(STATUS_KEY, statuses);
      return;
    }
  }

  if (current.state === "submitted") {
    statuses[entryId] = {
      ...current,
      pageUrl,
      updatedAt: new Date().toISOString(),
    };
    await setSession(STATUS_KEY, statuses);
  }
});

chrome.tabs.onRemoved.addListener(async (tabId) => {
  const tabMap = await getSession(TAB_MAP_KEY, {});
  if (!(String(tabId) in tabMap)) return;
  delete tabMap[String(tabId)];
  await setSession(TAB_MAP_KEY, tabMap);
});
