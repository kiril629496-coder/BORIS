export function parseMcnSettingsText(input) {
  const text = String(input || '').replace(/\r/g, '').trim();
  const result = {
    registrar: '',
    outbound_proxy: '',
    username: '',
    password: '',
    number: '',
    source_ips: [],
    found: [],
  };
  if (!text) return result;

  const lines = text.split('\n').map((x) => x.trim()).filter(Boolean);

  const cleanValue = (value) => String(value || '')
    .trim()
    .replace(/^["']|["']$/g, '')
    .replace(/[;,]+$/g, '')
    .trim();

  const take = (names) => {
    const re = new RegExp('^(?:' + names.join('|') + ')\\s*[:=–—-]\\s*(.+)$', 'i');
    for (const line of lines) {
      const m = line.match(re);
      if (m) return cleanValue(m[1]);
    }
    return '';
  };

  const takeExact = (names) => {
    const re = new RegExp('^(?:' + names.join('|') + ')\\s*[:=–—-]\\s*(.+)$', 'i');
    for (const line of lines) {
      const m = line.match(re);
      if (m) return String(m[1] || '');
    }
    return '';
  };

  const registrarRaw = take([
    'SIP server', 'SIP сервер', 'Сервер SIP', 'Сервер регистрации',
    'Registrar', 'Registration server', 'Proxy Server IP / Domain',
    'Proxy Server Realm', 'SIP Domain'
  ]);
  const registrarFallback = text.match(/\b(?:sips?:\/\/)?([a-z0-9.-]*mcn\.ru(?::\d{2,5})?)\b/i);
  result.registrar = cleanValue(registrarRaw || (registrarFallback ? registrarFallback[1] : ''));
  result.registrar = result.registrar.replace(/^sips?:\/\//i, '').replace(/\/$/, '');

  result.outbound_proxy = cleanValue(take([
    'Outbound proxy', 'SIP proxy', 'Proxy server', 'Прокси', 'Исходящий proxy'
  ])).replace(/^sips?:\/\//i, '').replace(/\/$/, '');

  result.username = take([
    'SIP login', 'SIP логин', 'Логин SIP', 'Login', 'Логин',
    'Username', 'User name', 'Пользователь', 'User ID / Account', 'User ID'
  ]);

  result.password = takeExact([
    'SIP password', 'SIP пароль', 'Пароль SIP', 'Password', 'Пароль', 'Pass'
  ]);

  const numberRaw = take([
    'Номер MCN', 'Телефонный номер', 'Номер телефона', 'DID', 'Caller ID'
  ]);
  const normalizePhone = (value) => {
    const v = String(value || '').trim();
    if (!v) return '';
    let digits = v.replace(/\D/g, '');
    if (digits.length === 11 && digits.startsWith('8')) digits = '7' + digits.slice(1);
    if (digits.length === 11 && digits.startsWith('7')) return '+' + digits;
    if (digits.length >= 10 && digits.length <= 15) return '+' + digits;
    return '';
  };
  result.number = normalizePhone(numberRaw);
  if (!result.number) {
    for (const line of lines) {
      if (/(?:login|логин|username|user name|password|пароль|pass)\s*[:=–—-]/i.test(line)) continue;
      if (!/(?:номер\s+mcn|телефон|phone|did|caller\s*id|городск|виртуальн)/i.test(line)) continue;
      const phones = line.match(/(?:\+7|8)[\s()\-]*\d[\d\s()\-]{8,16}/g) || [];
      for (const candidate of phones) {
        const normalized = normalizePhone(candidate);
        if (normalized) {
          result.number = normalized;
          break;
        }
      }
      if (result.number) break;
    }
  }

  const ipCandidates = [];
  for (const line of lines) {
    if (!/(?:^|\b)(?:ip|ip-адрес|ip адрес|source ip|источник)/i.test(line)) continue;
    for (const m of line.matchAll(/\b(?:\d{1,3}\.){3}\d{1,3}(?:\/\d{1,2})?\b/g)) {
      const raw = m[0];
      const parts = raw.split('/');
      const host = parts[0];
      const prefix = parts[1] || '';
      const octets = host.split('.').map(Number);
      if (octets.length === 4 && octets.every((x) => Number.isInteger(x) && x >= 0 && x <= 255)) {
        ipCandidates.push(prefix ? host + '/' + prefix : host);
      }
    }
  }
  result.source_ips = Array.from(new Set(ipCandidates));

  for (const key of ['registrar', 'outbound_proxy', 'username', 'password', 'number']) {
    if (result[key]) result.found.push(key);
  }
  if (result.source_ips.length) result.found.push('source_ips');

  return result;
}
