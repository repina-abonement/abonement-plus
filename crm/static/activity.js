function recordCrmActivity(kind, {studentId, tab, copyType} = {}) {
  if (!S.user || S.view === 'activity') return;
  const body = {kind, section: S.view, student_id: studentId, tab, copy_type: copyType};
  fetch('/api/activity', {
    method: 'POST', credentials: 'same-origin', keepalive: true,
    headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
  }).catch(() => {});
}

function activityCopyContext(element) {
  const drawer = document.querySelector('#drawer');
  const card = history.state?.studentCard;
  const inCard = drawer && !drawer.hidden && card?.userId === S.user?.id && drawer.contains(element);
  return inCard ? {studentId: card.id, tab: card.tab} : {};
}

async function copyCrmText(text, copyType = 'text') {
  const context = activityCopyContext(document.activeElement);
  await navigator.clipboard.writeText(text);
  recordCrmActivity('copy', {...context, copyType});
}

document.addEventListener('copy', event => {
  const anchor = document.getSelection()?.anchorNode;
  recordCrmActivity('copy-selection', activityCopyContext(anchor || event.target));
});

async function renderActivityHistory(page) {
  const views = Object.fromEntries(VIEWS);
  const tabs = {feed: 'Общение', abon: 'Абонементы и оплаты', visits: 'Посещения', info: 'Данные клиента'};
  const copies = {text: 'Текст', 'payment-link': 'Ссылка на оплату', 'client-list': 'Список клиентов',
    message: 'Сообщение', login: 'Данные для входа', backup: 'Пароль копии', phone: 'Номер телефона'};
  const actions = {view: 'Открыт раздел', card: 'Открыта карточка', copy: 'Скопировано', 'copy-selection': 'Ручное копирование'};
  page.innerHTML = `<section class="panel"><h2>История действий</h2>
    <label for="activity-user">Участник команды</label>
    <select id="activity-user"><option value="">Все</option></select>
    <div id="activity-results" aria-live="polite">Загрузка…</div>
    <button class="btn" id="activity-more" hidden>Показать ещё</button></section>`;
  const select = page.querySelector('#activity-user');
  const results = page.querySelector('#activity-results');
  const more = page.querySelector('#activity-more');
  let cursor = null, generation = 0;
  async function load(append) {
    const version = ++generation;
    const user = select.value;
    more.hidden = true;
    if (!append) results.textContent = 'Загрузка…';
    try {
      const params = new URLSearchParams();
      if (user) params.set('user_id', user);
      if (append && cursor) params.set('before', cursor);
      const data = await api('GET', '/api/activity?' + params);
      if (version !== generation || !results.isConnected) return;
      select.innerHTML = '<option value="">Все</option>' + data.users.map(u =>
        `<option value="${u.id}">${esc(u.name)}</option>`).join('');
      select.value = user;
      if (!append) results.innerHTML = data.rows.length ?
        '<div class="client-history-table-wrap"><table class="client-history-table"><thead><tr><th>Дата и время</th><th>Кто</th><th>Действие</th><th>Где</th></tr></thead><tbody></tbody></table></div>' :
        '<p class="muted">Записей пока нет</p>';
      results.querySelector('tbody')?.insertAdjacentHTML('beforeend', data.rows.map(row => {
        const place = [views[row.section], tabs[row.tab], row.student_id ? `Карточка №${row.student_id}` : ''].filter(Boolean);
        return `<tr><td>${esc(dtShort(row.ts))}</td><td>${esc(row.name)}</td>
          <td>${esc([actions[row.kind], copies[row.copy_type]].filter(Boolean).join(' · '))}</td><td>${esc(place.join(' · '))}</td></tr>`;
      }).join(''));
      cursor = data.next;
    } catch (error) {
      if (version !== generation || !results.isConnected) return;
      results.textContent = error.message;
      cursor = null;
    }
    more.hidden = !cursor;
  }
  select.onchange = () => load(false);
  more.onclick = () => load(true);
  await load(false);
}
