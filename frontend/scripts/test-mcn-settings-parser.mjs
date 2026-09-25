import assert from 'node:assert/strict';
import { parseMcnSettingsText } from '../app/phone-app/mcnSettingsParser.mjs';

const cases = [
  {
    name: 'ru labels',
    text: 'Сервер SIP: sip.mcn.ru\nSIP login: 74951234567\nSIP password: AbC!23/x\nНомер MCN: +7 (495) 123-45-67',
    want: {registrar:'sip.mcn.ru', username:'74951234567', password:'AbC!23/x', number:'+74951234567'}
  },
  {
    name: 'english labels and 8-number',
    text: 'Registrar = sip.mcn.ru:5060\nUsername = user100\nPassword = p@ss:word\nDID = 8 495 111-22-33',
    want: {registrar:'sip.mcn.ru:5060', username:'user100', password:'p@ss:word', number:'+74951112233'}
  },
  {
    name: 'fallback registrar and phone',
    text: 'Подключайтесь к sip.mcn.ru\nЛогин: alpha\nПароль: zZ-99_//\nВаш городской: +7 812 555 10 20',
    want: {registrar:'sip.mcn.ru', username:'alpha', password:'zZ-99_//', number:'+78125551020'}
  },
  {
    name: 'source ips',
    text: 'SIP server: sip.mcn.ru\nIP-адреса MCN: 85.94.32.92/32, 85.94.32.93',
    want: {registrar:'sip.mcn.ru', source_ips:['85.94.32.92/32','85.94.32.93']}
  },
  {
    name: 'separate outbound proxy',
    text: 'Registrar: sip.mcn.ru\nOutbound proxy: edge.mcn.ru:5080\nSIP login: user42\nSIP password: secret42\nНомер MCN: +7 495 222-33-44',
    want: {registrar:'sip.mcn.ru', outbound_proxy:'edge.mcn.ru:5080', username:'user42', password:'secret42', number:'+74952223344'}
  },
  {
    name: 'numeric sip login is not did',
    text: 'Сервер SIP: sip.mcn.ru\nSIP login: 74951234567\nSIP password: secret42',
    want: {registrar:'sip.mcn.ru', username:'74951234567', password:'secret42', number:''}
  },
  {
    name: 'phone hint fallback does not use login',
    text: 'Сервер SIP: sip.mcn.ru\nSIP login: 74951234567\nSIP password: secret42\nВаш городской телефон +7 (812) 555-10-20',
    want: {registrar:'sip.mcn.ru', username:'74951234567', password:'secret42', number:'+78125551020'}
  },
  {
    name: 'official mcn equipment labels',
    text: 'Number: 74951234567\nUser ID / Account: 74951234567\nPassword: official-secret\nProxy Server IP / Domain: sip.mcn.ru\nDID: +7 495 444-55-66',
    want: {registrar:'sip.mcn.ru', username:'74951234567', password:'official-secret', number:'+74954445566'}
  },
  {
    name: 'official Number field is sip login not did',
    text: 'Number: 74951234567\nUser ID / Account: 74951234567\nPassword: official-secret\nProxy Server IP / Domain: sip.mcn.ru',
    want: {registrar:'sip.mcn.ru', username:'74951234567', password:'official-secret', number:''}
  },
  {
    name: 'ambiguous generic russian number fails closed',
    text: 'Сервер SIP: sip.mcn.ru\nSIP login: 74951234567\nSIP password: secret42\nНомер: +7 495 000-11-22',
    want: {registrar:'sip.mcn.ru', username:'74951234567', password:'secret42', number:''}
  },
  {
    name: 'password trailing punctuation is preserved exactly',
    text: 'Сервер SIP: sip.mcn.ru\nSIP login: 74951234567\nSIP password: qa#=,;\nНомер MCN: +7 495 000-11-22',
    want: {registrar:'sip.mcn.ru', username:'74951234567', password:'qa#=,;', number:'+74950001122'}
  }
];

for (const c of cases) {
  const got = parseMcnSettingsText(c.text);
  for (const [k,v] of Object.entries(c.want)) {
    assert.deepEqual(got[k], v, c.name + ': ' + k);
  }
}
console.log('MCN_SETTINGS_PARSER_OK', {cases: cases.length});
