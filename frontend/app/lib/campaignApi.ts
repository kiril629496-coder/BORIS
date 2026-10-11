import { apiPost as post, apiUpload as upload } from "./api";

/** Campaign actions must never treat an HTTP/API failure as a saved draft.
 * Keep the shared API's existing return-value contract for other modules.
 * Throw before callers clear edit buffers or rotate paid idempotency keys.
 */
export function requireCampaignResult<T extends Record<string, any>>(result: T): T {
  const status = String(result?.status || "");
  if (!result || status === "error" || status === "skip" ||
      Number(result?.httpStatus || 0) >= 400 ||
      status.endsWith("_blocked") || status.endsWith("_required")) {
    const error = new Error(result?.message || "Не удалось выполнить действие. Ваши данные сохранены в форме. Повторите попытку.");
    Object.assign(error, {code: result?.code, httpStatus: result?.httpStatus, raw: result?.raw});
    throw error;
  }
  return result;
}

export async function apiPost(url: string, body: any) {
  return requireCampaignResult(await post(url, body));
}

export async function apiUpload(url: string, body: FormData) {
  return requireCampaignResult(await upload(url, body));
}
