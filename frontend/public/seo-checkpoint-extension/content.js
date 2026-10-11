(() => {
  const CENTER_SOURCE = "BORIS_CHECKPOINT_CENTER";
  const EXT_SOURCE = "BORIS_CHECKPOINT_EXTENSION";
  const CENTER_HOSTS = new Set(["boris-ai.pro", "www.boris-ai.pro"]);

  function postToPage(type, extra = {}) {
    window.postMessage({ source: EXT_SOURCE, type, ...extra }, "*");
  }

  if (
    CENTER_HOSTS.has(location.hostname) &&
    location.pathname.startsWith("/seo-checkpoints")
  ) {
    window.addEventListener("message", (event) => {
      if (event.source !== window) return;
      const msg = event.data;
      if (!msg || msg.source !== CENTER_SOURCE) return;

      if (msg.type === "PING") {
        chrome.runtime.sendMessage({ type: "PING" }, (response) => {
          if (response?.ok) {
            postToPage("READY", {
              version: response.version || 1,
              extensionVersion: response.extensionVersion || "",
            });
          }
        });
        return;
      }

      if (msg.type === "SYNC_CONFIG") {
        (async () => {
          try {
            const token = localStorage.getItem("boris_token") || "";
            const headers = {};
            if (token) headers.Authorization = "Bearer " + token;
            const response = await fetch(
              "/seo-checkpoints-extension-config",
              {
                method: "GET",
                headers,
                cache: "no-store",
                credentials: "same-origin",
              }
            );
            if (!response.ok) {
              throw new Error("extension_config_http_" + response.status);
            }
            const payload = await response.json();
            chrome.runtime.sendMessage(
              { type: "SYNC_CONFIG", payload },
              (stored) => {
                if (stored?.ok) {
                  postToPage("READY", { entries: stored.entries || 0 });
                } else {
                  postToPage("SYNC_ERROR", {
                    error: stored?.error || "extension_storage_failed",
                  });
                }
              }
            );
          } catch (error) {
            postToPage("SYNC_ERROR", {
              error: String(error?.message || error),
            });
          }
        })();
        return;
      }

      if (msg.type === "OPEN_ENTRIES") {
        chrome.runtime.sendMessage(
          { type: "OPEN_ENTRIES", entryIds: msg.entryIds || [] },
          () => {}
        );
        return;
      }

      if (msg.type === "POLL_STATUS") {
        chrome.runtime.sendMessage({ type: "GET_STATUS" }, (response) => {
          if (response?.ok) {
            postToPage("STATUS", { statuses: response.statuses || {} });
          }
        });
        return;
      }

      if (msg.type === "ACK_ENTRY") {
        chrome.runtime.sendMessage(
          {
            type: "ACK_ENTRY",
            entryId: msg.entryId,
            submittedAt: msg.submittedAt,
          },
          () => {}
        );
      }
    });

    chrome.runtime.sendMessage({ type: "PING" }, (response) => {
      if (response?.ok) {
        postToPage("READY", {
          version: response.version || 1,
          extensionVersion: response.extensionVersion || "",
        });
      }
    });
    return;
  }

  function checkpointIdFromHash() {
    const raw = String(location.hash || "");
    const match = raw.match(/(?:^#|&)boris-checkpoint=([^&]+)/);
    if (!match) return "";
    try {
      return decodeURIComponent(match[1]);
    } catch (_) {
      return match[1];
    }
  }

  const entryId = checkpointIdFromHash();
  if (!entryId) return;

  function isVisible(el) {
    if (!(el instanceof HTMLElement)) return true;
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return (
      rect.width > 0 &&
      rect.height > 0 &&
      style.display !== "none" &&
      style.visibility !== "hidden"
    );
  }

  function fire(el) {
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    el.dispatchEvent(new Event("blur", { bubbles: true }));
  }

  function setValue(el, value) {
    if (!el || value === undefined || value === null || value === "") {
      return false;
    }
    if (!isVisible(el) || el.disabled || el.readOnly) return false;
    if (String(el.value || "").trim()) return false;
    try {
      const proto =
        el instanceof HTMLTextAreaElement
          ? HTMLTextAreaElement.prototype
          : HTMLInputElement.prototype;
      const descriptor = Object.getOwnPropertyDescriptor(proto, "value");
      if (descriptor?.set) descriptor.set.call(el, String(value));
      else el.value = String(value);
      fire(el);
      return true;
    } catch (_) {
      try {
        el.value = String(value);
        fire(el);
        return true;
      } catch (_) {
        return false;
      }
    }
  }

  function first(selectors, root = document) {
    for (const selector of selectors) {
      try {
        const nodes = [...root.querySelectorAll(selector)];
        const node = nodes.find((el) => isVisible(el));
        if (node) return node;
      } catch (_) {}
    }
    return null;
  }

  function fillFirst(selectors, value, root = document) {
    return setValue(first(selectors, root), value);
  }

  function fillAllPassword(value, root = document) {
    if (!value) return 0;
    let count = 0;
    for (const el of root.querySelectorAll('input[type="password"]')) {
      if (setValue(el, value)) count += 1;
    }
    return count;
  }

  function looksLegalText(text) {
    return /(terms|agree|consent|privacy|policy|услов|соглаш|правил|политик|принимаю|согласен)/i.test(
      text || ""
    );
  }

  function highlightProtectedControls() {
    const captchaSelectors = [
      'iframe[src*="captcha" i]',
      '[class*="captcha" i]',
      '[id*="captcha" i]',
      'textarea[name="g-recaptcha-response"]',
      'input[name*="captcha" i]',
      'input[id*="captcha" i]',
    ];
    for (const selector of captchaSelectors) {
      for (const el of document.querySelectorAll(selector)) {
        if (el instanceof HTMLElement) {
          el.style.outline = "3px solid #f59e0b";
          el.style.outlineOffset = "4px";
        }
      }
    }

    for (const cb of document.querySelectorAll('input[type="checkbox"]')) {
      if (!(cb instanceof HTMLInputElement)) continue;
      let context = "";
      if (cb.id) {
        const label = document.querySelector(
          `label[for="${CSS.escape(cb.id)}"]`
        );
        if (label) context += " " + (label.textContent || "");
      }
      context += " " + (cb.parentElement?.textContent || "");
      if (looksLegalText(context)) {
        // A site may ship a legal checkbox pre-checked. BORIS must never treat
        // a platform default as the owner's consent, so force it back to
        // unchecked and leave the final decision to the human.
        if (cb.checked) {
          cb.checked = false;
          fire(cb);
        }
        const target = cb.closest("label") || cb.parentElement || cb;
        if (target instanceof HTMLElement) {
          target.style.outline = "3px solid #facc15";
          target.style.outlineOffset = "4px";
          target.style.borderRadius = "8px";
        }
      }
    }

    const ageSelectors = [
      'input[name*="dob" i]',
      'input[id*="dob" i]',
      'input[name*="birth" i]',
      'input[id*="birth" i]',
      'input[name*="age" i]',
      'input[id*="age" i]',
      'select[name*="dob" i]',
      'select[name*="birth" i]',
      'select[name*="age" i]',
      "select#regMonth",
      "select#regDay",
      "select#regYear",
    ];
    for (const selector of ageSelectors) {
      for (const el of document.querySelectorAll(selector)) {
        if (el instanceof HTMLElement) {
          el.style.outline = "3px solid #a855f7";
          el.style.outlineOffset = "4px";
        }
      }
    }
  }

  function optionSelect(select, queries) {
    if (!(select instanceof HTMLSelectElement)) return false;
    const options = [...select.options];
    for (const query of queries || []) {
      const q = String(query || "").trim().toLowerCase();
      if (!q) continue;
      const option = options.find((opt) =>
        String(opt.textContent || opt.value || "").toLowerCase().includes(q)
      );
      if (option) {
        select.value = option.value;
        fire(select);
        return true;
      }
    }
    return false;
  }

  function fillOtsiv(entry) {
    const data = entry.autofill || {};
    let filled = 0;
    if (fillFirst(["#HLBLOCK_6_UF_SITE"], data.website)) filled += 1;
    if (fillFirst(["#HLBLOCK_6_UF_NAME"], data.business_name)) filled += 1;
    if (fillFirst(["#HLBLOCK_6_UF_DESCRIPTION"], data.description)) filled += 1;
    if (fillFirst(["#HLBLOCK_6_UF_EMAIL"], data.email)) filled += 1;

    // Otsiv preselects a city on a fresh form. Location is never inferred by
    // BORIS, so clear the platform default and require the owner to choose it.
    const city = document.querySelector("#HLBLOCK_6_UF_CITY2_ID");
    if (city instanceof HTMLSelectElement) {
      const blank = [...city.options].find((opt) => String(opt.value || "") === "");
      if (blank && city.value !== "") {
        city.value = "";
        fire(city);
      }
      city.style.outline = "3px solid #a855f7";
      city.style.outlineOffset = "4px";
    }
    return filled;
  }

  function fillOtsovikRegistration(entry) {
    const data = entry.autofill || {};
    const form = document.querySelector('form[action*="register=yes"]');

    if (
      form instanceof HTMLFormElement &&
      !isVisible(form)
    ) {
      const trigger = document.querySelector(
        'a[data-target="#regisrationuser"]'
      );
      if (trigger instanceof HTMLElement && isVisible(trigger)) {
        trigger.click();
        return 0;
      }
    }

    const root = form || document;
    let filled = 0;
    if (fillFirst(['input[name="USER_LOGIN"]'], data.username, root)) {
      filled += 1;
    }
    if (fillFirst(['input[name="USER_EMAIL"]'], data.email, root)) {
      filled += 1;
    }
    filled += fillAllPassword(data.password, root);
    return filled;
  }

  function fillOrgpage(entry) {
    const data = entry.autofill || {};
    let filled = 0;
    if (fillFirst(['input[name="name"]'], data.business_name)) filled += 1;
    if (fillFirst(['textarea[name="shortDesc"]'], data.description)) filled += 1;
    if (fillFirst(['textarea[name="fullDesc"]'], data.description)) filled += 1;
    // City/address/OPF/phone remain manual because this flow explicitly
    // requires truthful company identity.
    return filled;
  }

  function safeAutofill(entry) {
    const data = entry?.autofill || {};
    let filled = 0;

    if (entry.platform === "pubdisc_otsiv_ru") {
      filled += fillOtsiv(entry);
      highlightProtectedControls();
      return filled;
    }

    if (entry.platform === "pubdisc_otsovik_com") {
      filled += fillOtsovikRegistration(entry);
      highlightProtectedControls();
      return filled;
    }

    if (entry.platform === "pubdisc_orgpage_ru") {
      filled += fillOrgpage(entry);
      highlightProtectedControls();
      return filled;
    }

    if (
      fillFirst(
        [
          'input[type="email"]',
          'input[name*="email" i]',
          'input[id*="email" i]',
        ],
        data.email
      )
    ) {
      filled += 1;
    }

    if (entry.flow_kind === "account_onboarding") {
      if (
        fillFirst(
          [
            'input[name*="username" i]',
            'input[name*="user" i]',
            'input[id*="username" i]',
            'input[autocomplete="username"]',
            'input[name="login"]',
          ],
          data.username
        )
      ) {
        filled += 1;
      }
      filled += fillAllPassword(data.password);
      if (
        fillFirst(
          [
            'input[type="tel"]',
            'input[name*="phone" i]',
            'input[name*="mobile" i]',
            'input[id*="phone" i]',
            'input[id*="mobile" i]',
          ],
          data.phone
        )
      ) {
        filled += 1;
      }
    }

    if (entry.flow_kind === "direct_listing") {
      if (
        fillFirst(
          [
            'input[name="company"]',
            'input[name*="company_name" i]',
            'input[name*="business_name" i]',
            'input[id*="company" i]',
            'input[id*="business" i]',
          ],
          data.business_name
        )
      ) {
        filled += 1;
      }

      if (
        fillFirst(
          [
            'input[name="website"]',
            'input[name="website_url"]',
            'input[name="url"]',
            'input[name*="site_url" i]',
            'input[name*="website" i]',
            'input[placeholder*="website" i]',
            'input[placeholder*="site url" i]',
            'input[placeholder*="адрес сайта" i]',
          ],
          data.website
        )
      ) {
        filled += 1;
      }

      fillFirst(
        ['input[name="display_website"]', 'input[name*="display_url" i]'],
        data.website
      );

      if (
        fillFirst(
          [
            'textarea[name="description"]',
            'textarea[name="fullDesc"]',
            'textarea[name="shortDesc"]',
            'textarea[name*="description" i]',
            'textarea[id*="description" i]',
            'textarea[placeholder*="описан" i]',
            'textarea[placeholder*="description" i]',
          ],
          data.description
        )
      ) {
        filled += 1;
      }

      for (const select of document.querySelectorAll("select")) {
        const ident = [
          select.name,
          select.id,
          select.getAttribute("aria-label"),
        ].join(" ");
        if (/categor|категор|service/i.test(ident)) {
          if (optionSelect(select, data.category_queries || [])) {
            filled += 1;
            break;
          }
        }
      }
    }

    highlightProtectedControls();
    return filled;
  }

  let currentEntry = null;
  let lastSubmitSignal = 0;

  function signalAttempt(trigger) {
    chrome.runtime.sendMessage(
      {
        type: "USER_SUBMIT_ATTEMPT",
        entryId,
        pageUrl: location.href,
        trigger,
      },
      () => {}
    );
  }

  function signalSubmitted(trigger) {
    const now = Date.now();
    if (now - lastSubmitSignal < 1200) return;
    lastSubmitSignal = now;
    chrome.runtime.sendMessage(
      {
        type: "USER_SUBMITTED",
        entryId,
        pageUrl: location.href,
        trigger,
      },
      () => {}
    );
  }

  function successVisible() {
    const text = String(document.body?.innerText || "").toLowerCase();
    const markers = [
      "thank you for your submission",
      "submitted successfully",
      "submission received",
      "pending review",
      "pending moderation",
      "registration complete",
      "account created",
      "check your email",
      "verify your email",
      "заявка отправлена",
      "успешно отправлена",
      "успешно добавлен",
      "добавлено на модерацию",
      "ожидает модерации",
      "отправлено на модерацию",
      "регистрация завершена",
      "аккаунт создан",
      "проверьте почту",
      "подтвердите почту",
    ];
    return markers.some((marker) => text.includes(marker));
  }

  function scheduleSuccessDetection() {
    [1200, 3000, 6000].forEach((delay) => {
      window.setTimeout(() => {
        if (successVisible()) signalSubmitted("success_message");
      }, delay);
    });
  }

  function appendText(parent, text, style) {
    const node = document.createElement("div");
    node.textContent = text;
    if (style) node.style.cssText = style;
    parent.appendChild(node);
    return node;
  }

  function manualAction(entry) {
    const checkpoint = String(entry?.checkpoint || "");
    if (
      checkpoint === "captcha_required" ||
      checkpoint === "captcha_age_declaration_required"
    ) {
      return {
        title: "ТВОЁ ДЕЙСТВИЕ — CAPTCHA",
        steps: "1. Пройди CAPTCHA\n2. Нажми кнопку регистрации / отправки",
      };
    }
    if (
      checkpoint === "captcha_terms_registration_required" ||
      checkpoint === "captcha_age_and_terms_required"
    ) {
      return {
        title: "ТВОЁ ДЕЙСТВИЕ — CAPTCHA + УСЛОВИЯ",
        steps: "1. Проверь условия и прими их, если согласен\n2. Пройди CAPTCHA\n3. Отправь форму",
      };
    }
    if (
      checkpoint === "terms_acceptance_required" ||
      checkpoint === "registration_agreement_required"
    ) {
      return {
        title: "ТВОЁ ДЕЙСТВИЕ — УСЛОВИЯ",
        steps: "Проверь условия площадки и прими их только если согласен, затем отправь форму.",
      };
    }
    if (checkpoint === "truthful_company_identity_required") {
      return {
        title: "ТВОЁ ДЕЙСТВИЕ — РЕАЛЬНЫЕ ДАННЫЕ",
        steps: "Проверь и дополни только реальные данные компании. BORIS не придумывает адрес, имя или реквизиты.",
      };
    }
    if (
      checkpoint === "email_verification_required" ||
      checkpoint === "business_email_required"
    ) {
      return {
        title: "ТВОЁ ДЕЙСТВИЕ — EMAIL",
        steps: "Подтверди email и заверши регистрацию.",
      };
    }
    if (checkpoint === "sms_or_verification_code_required") {
      return {
        title: "ТВОЁ ДЕЙСТВИЕ — КОД",
        steps: "Введи полученный код подтверждения и заверши шаг.",
      };
    }
    if (checkpoint === "math_antibot_required") {
      return {
        title: "ТВОЁ ДЕЙСТВИЕ — ANTI-BOT",
        steps: "Пройди математическую anti-bot проверку и отправь форму.",
      };
    }
    return {
      title: "ТВОЁ ДЕЙСТВИЕ — ПОДТВЕРЖДЕНИЕ",
      steps: "Заверши защищённый ручной шаг и отправь форму.",
    };
  }

  function injectBanner(entry) {
    if (document.getElementById("boris-checkpoint-banner")) return;

    const box = document.createElement("div");
    box.id = "boris-checkpoint-banner";
    box.style.cssText = [
      "position:fixed",
      "top:18px",
      "right:18px",
      "z-index:2147483647",
      "width:min(390px,calc(100vw - 36px))",
      "background:#0f172a",
      "color:#fff",
      "border:1px solid rgba(255,255,255,.16)",
      "border-radius:20px",
      "box-shadow:0 18px 50px rgba(0,0,0,.35)",
      "padding:16px",
      "font:14px/1.45 -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif",
    ].join(";");

    appendText(
      box,
      "BORIS · ручное подтверждение",
      "font-weight:800;font-size:15px;margin-bottom:7px"
    );
    appendText(
      box,
      String(entry.name || entry.platform || ""),
      "color:#cbd5e1;margin-bottom:5px"
    );
    if (entry.target_site) {
      appendText(
        box,
        "Сайт: " + String(entry.target_site),
        "font-size:12px;color:#93c5fd;margin-bottom:9px"
      );
    }
    const manual = manualAction(entry);
    appendText(
      box,
      "Обычные поля уже заполнены",
      "color:#a7f3d0;font-weight:800;margin-bottom:8px"
    );
    appendText(
      box,
      manual.title,
      "color:#fde68a;font-weight:900;margin-bottom:6px"
    );
    appendText(
      box,
      manual.steps,
      "white-space:pre-line;color:#e2e8f0;font-size:12px;font-weight:700;margin-bottom:8px"
    );
    appendText(
      box,
      "BORIS не решает CAPTCHA, не принимает Terms и не придумывает возраст/адрес.",
      "font-size:11px;color:#94a3b8;margin-bottom:12px"
    );

    const done = document.createElement("button");
    done.type = "button";
    done.textContent = "Готово — форма отправлена";
    done.style.cssText = [
      "width:100%",
      "border:0",
      "border-radius:999px",
      "padding:10px 14px",
      "background:#22c55e",
      "color:#052e16",
      "font-weight:800",
      "cursor:pointer",
    ].join(";");
    done.addEventListener("click", () => {
      signalSubmitted("manual_confirm");
      done.textContent = "Передано BORIS ✓";
      done.disabled = true;
      done.style.opacity = "0.75";
    });
    box.appendChild(done);

    document.documentElement.appendChild(box);
  }

  document.addEventListener(
    "submit",
    () => {
      signalAttempt("form_submit");
      scheduleSuccessDetection();
    },
    true
  );

  document.addEventListener(
    "click",
    (event) => {
      const target =
        event.target instanceof Element
          ? event.target.closest(
              'button,input[type="submit"],input[type="button"]'
            )
          : null;
      if (!target || target.id === "boris-checkpoint-banner") return;
      const text = String(
        target instanceof HTMLInputElement
          ? target.value
          : target.textContent || ""
      ).trim();
      if (
        /(register|sign up|create account|submit|publish|post|save|загрузить|отправить|опубликовать|разместить|зарегистрироваться|создать аккаунт)/i.test(
          text
        ) &&
        !/(next|continue|далее|продолжить)/i.test(text)
      ) {
        signalAttempt("submit_button");
        scheduleSuccessDetection();
      }
    },
    true
  );

  chrome.runtime.sendMessage({ type: "GET_ENTRY", entryId }, (response) => {
    const entry = response?.entry;
    if (!entry) return;
    currentEntry = entry;
    injectBanner(entry);

    const run = () => {
      const count = safeAutofill(entry);
      if (count > 0) {
        chrome.runtime.sendMessage(
          { type: "MARK_FILLED", entryId, pageUrl: location.href },
          () => {}
        );
      }
    };

    run();
    [700, 1600, 3200, 5500].forEach((delay) =>
      window.setTimeout(run, delay)
    );

    const observer = new MutationObserver(() => {
      highlightProtectedControls();
    });
    observer.observe(document.documentElement, {
      childList: true,
      subtree: true,
    });
    window.setTimeout(() => observer.disconnect(), 12000);
  });
})();
