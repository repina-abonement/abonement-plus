/**
 * Gmail → Telegram + CRM «Абонемент Плюс»
 * Новая заявка с сайта (письмо от support@footbolika.ru):
 *  1) уведомление в чат «Служба заботы»
 *  2) карточка в CRM: первая в «Новый лид», красная
 * Если одно из двух не получилось, скрипт повторит именно его через минуту
 */

// ===== НАСТРОЙКИ =====
const BOT_TOKEN = 'ВСТАВЬТЕ_ТОКЕН_БОТА';      // токен от @BotFather (тот же, что был)
const CHAT_ID   = '-1002448053462';           // чат «Служба заботы»
const SENDER    = 'support@footbolika.ru';    // от кого ждём письма
const CRM_URL   = 'ВСТАВЬТЕ_АДРЕС_ИЗ_CRM';     // CRM → Настройки → Связь с клиентами → Заявки с сайта → Копировать
// =====================

const MAX_STORED_IDS = 500;

/** Главная функция: её запускает триггер раз в минуту */
function checkMail() {
  const props = PropertiesService.getScriptProperties();
  const tgDone  = new Set(JSON.parse(props.getProperty('processedIds') || '[]'));
  const crmDone = new Set(JSON.parse(props.getProperty('crmIds') || '[]'));

  const threads = GmailApp.search('from:' + SENDER + ' newer_than:2d', 0, 30);

  threads.forEach(thread => {
    thread.getMessages().forEach(msg => {
      const id = msg.getId();
      if (msg.getFrom().toLowerCase().indexOf(SENDER) === -1) return;
      const lead = parseLead(msg);

      if (!tgDone.has(id) && sendToTelegram(buildText(lead))) tgDone.add(id);
      if (!crmDone.has(id) && CRM_URL.indexOf('http') === 0 && sendToCrm(id, lead)) crmDone.add(id);
    });
  });

  props.setProperty('processedIds', JSON.stringify([...tgDone].slice(-MAX_STORED_IDS)));
  props.setProperty('crmIds', JSON.stringify([...crmDone].slice(-MAX_STORED_IDS)));
}

/** Достаёт поля заявки из письма */
function parseLead(msg) {
  const body = msg.getPlainBody() || '';
  return {
    name:    extract(body, ['Имя', 'ФИО', 'Name']),
    phone:   extract(body, ['Телефон', 'Тел', 'Phone']),
    city:    extract(body, ['Город', 'City']),
    branch:  extract(body, ['Филиал', 'Branch']),
    child:   extract(body, ['Имя ребенка', 'Имя ребёнка', 'Ребенок', 'Ребёнок']),
    age:     extract(body, ['Возраст', 'Age']),
    comment: extract(body, ['Комментарий', 'Comment'])
  };
}

/** Текст для чата «Служба заботы» */
function buildText(l) {
  return [
    '⚠️ Срочно! Упала заявка с сайта!',
    '',
    'Имя: ' + l.name,
    'Телефон: ' + l.phone,
    'Город: ' + l.city,
    'Филиал: ' + l.branch,
    '',
    'Она уже должна появиться в ЕРП в разделе Лиды, проверить. Если нет - внести. Обработать'
  ].join('\n');
}

/** Ищет в тексте строку вида «Метка: значение» */
function extract(text, labels) {
  for (const label of labels) {
    const re = new RegExp('(?:^|\\n)\\s*\\*?' + label + '\\*?\\s*[:\\-–]\\s*(.+)', 'i');
    const m = text.match(re);
    if (m && m[1].trim()) return m[1].trim();
  }
  return '—';
}

/** Отправка в Telegram */
function sendToTelegram(text) {
  const res = UrlFetchApp.fetch('https://api.telegram.org/bot' + BOT_TOKEN + '/sendMessage', {
    method: 'post',
    contentType: 'application/json',
    payload: JSON.stringify({ chat_id: CHAT_ID, text: text }),
    muteHttpExceptions: true
  });
  if (res.getResponseCode() !== 200) {
    console.error('Telegram error: ' + res.getContentText());
    return false;
  }
  return true;
}

/** Отправка в CRM. 400 = в заявке нет телефона, повторять бесполезно */
function sendToCrm(id, l) {
  const res = UrlFetchApp.fetch(CRM_URL, {
    method: 'post',
    contentType: 'application/json',
    payload: JSON.stringify(Object.assign({ id: id }, l)),
    muteHttpExceptions: true
  });
  const code = res.getResponseCode();
  if (code === 200 || code === 400) {
    if (code === 400) console.error('CRM: ' + res.getContentText());
    return true;
  }
  console.error('CRM error ' + code + ': ' + res.getContentText());
  return false;
}

/** Запустить ОДИН раз: включает проверку почты каждую минуту */
function setupTrigger() {
  ScriptApp.getProjectTriggers().forEach(t => {
    if (t.getHandlerFunction() === 'checkMail') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('checkMail').timeBased().everyMinutes(1).create();
}

/** Проверка связи с ботом: шлёт тестовое сообщение в чат */
function testTelegram() {
  sendToTelegram('✅ Тест: бот подключён к чату «Служба заботы»');
}

/** Проверка связи с CRM: создаёт тестовую заявку «Тест Заявка» */
function testCrm() {
  const ok = sendToCrm('test-' + Date.now(), { name: 'Тест Заявка', phone: '+7 900 000-00-01', city: 'Москва', branch: 'Небо' });
  console.log(ok ? 'Готово, проверьте воронку в CRM' : 'Не получилось, смотрите ошибку выше');
}
