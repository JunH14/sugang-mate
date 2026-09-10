// All model/data text is rendered through text nodes. No model-provided HTML runs.
if (!element.dataset.sugangReady) {
element.dataset.sugangReady = '1';
const root = element.querySelector('.sm-app');
const $ = (selector) => root.querySelector(selector);
const $$ = (selector) => [...root.querySelectorAll(selector)];
const config = props.boot;
const history = [];
let active = null;
let serial = 0;
let apiInfo = null;
const make = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = String(text);
  return node;
};
const scopes = $$('.sm-scope');
scopes.forEach(node => {node.textContent = config.scope;});
$('.sm-privacy').textContent = config.online ? '질문은 Google Gemini로 전송될 수 있습니다.' : config.sample ? '가상 과목으로 체험하는 오프라인 데모입니다.' : '외부 AI 요청 없이 수집 자료를 조회하고 있습니다.';
const textarea = $('#sm-question');
const send = $('.sm-send');
const log = $('.sm-messages');
const scroller = $('.sm-scroll');
const dialog = $('.sm-dialog');
const dialogBody = $('.sm-dialog-body');
const scrollBottom = () => {scroller.scrollTop = scroller.scrollHeight;};
const resizeInput = () => {
  textarea.style.height = 'auto';
  textarea.style.height = Math.min(textarea.scrollHeight, 126) + 'px';
  $('.sm-count').textContent = textarea.value.length > 400 ? `${textarea.value.length}/500` : '';
};
function safeURL(value) {
  if (typeof value !== 'string' || /[\u0000-\u0020\u007f]/.test(value)) return null;
  try {const url = new URL(value); return ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password ? url.href : null;} catch {return null;}
}
function inline(node, text) {
  // Small, intentionally safe Markdown subset: bold and inline code.
  const pattern = /(\*\*([^*]+)\*\*|`([^`]+)`)/g;
  let index = 0;
  for (const match of String(text).matchAll(pattern)) {
    node.append(document.createTextNode(String(text).slice(index, match.index)));
    node.append(make(match[2] ? 'strong' : 'code', '', match[2] || match[3]));
    index = match.index + match[0].length;
  }
  node.append(document.createTextNode(String(text).slice(index)));
}
function markdown(parent, text) {
  const lines = String(text).split('\n');
  let list = null;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    if (!line) {list = null; continue;}
    if (line.includes('|') && i + 1 < lines.length && /^\s*\|?\s*:?-{3}/.test(lines[i + 1])) {
      const wrap = make('div', 'sm-table-wrap'); wrap.tabIndex = 0; wrap.setAttribute('aria-label', '과목 비교 표');
      const table = make('table');
      const cells = value => value.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(cell => cell.trim());
      const header = make('tr'); cells(line).forEach(cell => {const th = make('th'); th.scope = 'col'; inline(th, cell); header.append(th);});
      const thead = make('thead'); thead.append(header); table.append(thead);
      const tbody = make('tbody'); i += 2;
      while (i < lines.length && lines[i].trim().includes('|')) {
        const tr = make('tr'); cells(lines[i]).forEach(cell => {const td = make('td'); inline(td, cell); tr.append(td);}); tbody.append(tr); i++;
      }
      i--; table.append(tbody); wrap.append(table); parent.append(wrap); list = null; continue;
    }
    const bullet = line.match(/^(?:[-*]\s+|\d+[.)]\s+)(.*)/);
    if (bullet) {if (!list) {list = make('ul'); parent.append(list);} const li = make('li'); inline(li, bullet[1]); list.append(li); continue;}
    list = null;
    const heading = line.match(/^#{1,6}\s+(.*)/);
    const quote = line.startsWith('> ');
    const node = make(heading ? 'h3' : quote ? 'blockquote' : 'p');
    inline(node, heading ? heading[1] : quote ? line.slice(2) : line);
    parent.append(node);
  }
}
function courseCard(course) {
  const card = make('div', 'sm-course-card');
  card.append(make('div', 'sm-course-name', course.course_name));
  card.append(make('div', 'sm-course-sub', [course.course_code, course.completion_type].filter(Boolean).join(' · ')));
  card.append(make('div', 'sm-course-schedule', course.schedule_summary || '수업시간 미기재 · 원문을 확인해 주세요.'));
  if (course.professor) card.append(make('div', 'sm-course-prof', `담당교수 ${course.professor}`));
  return card;
}
function renderResponse(response, result, answerHistory) {
  response.replaceChildren();
  const answer = make('div', 'sm-answer');
  markdown(answer, result.answer); response.append(answer);
  if (result.course_card) response.append(courseCard(result.course_card));
  if (result.sources?.length) {
    const evidence = make('details', 'sm-evidence');
    evidence.append(make('summary', '', `근거 ${result.sources.length}개 보기`));
    const body = make('div', 'sm-evidence-body');
    for (const source of result.sources) {
      const card = make('div', 'sm-source');
      card.append(make('strong', '', `${source.course_name} · 강의계획서`));
      card.append(make('div', 'sm-source-meta', [source.course_code, source.completion_type, source.professor].filter(Boolean).join(' · ')));
      if (source.schedule_summary) card.append(make('div', 'sm-source-meta', source.schedule_summary));
      if (source.snippet) card.append(make('blockquote', '', source.snippet));
      const href = safeURL(source.syllabus_url);
      if (href) {const link = make('a', '', '원문 열기 ↗'); link.href = href; link.target = '_blank'; link.rel = 'noopener noreferrer'; card.append(link);}
      body.append(card);
    }
    evidence.append(body); response.append(evidence);
  }
  if (result.warning) response.append(make('div', 'sm-warning', result.warning));
  const info = make('details', 'sm-response-details'); info.append(make('summary', '', '응답 상세'));
  const detail = make('div');
  detail.append(make('p', '', result.mode_label || result.mode || '근거 기반 조회'));
  if (result.timing_label) detail.append(make('p', '', result.timing_label));
  info.append(detail); response.append(info);
  if (result.sources?.length) {
    const chips = make('div', 'sm-followups');
    const course = result.sources[0];
    const queries = result.sources.length === 1 ? [
      [`평가방식도 알려줘`, `${course.course_code}의 평가방식도 알려줘`],
      ['어떤 내용을 배워?', `${course.course_code}에서는 어떤 내용을 배워?`],
    ] : [['그중 전공필수는?', '그중 전공필수 과목은 뭐야?'], ['평가방식을 비교해줘', '그 과목들의 평가방식을 비교해줘']];
    for (const [label, question] of queries) {const button = make('button', '', label); button.type = 'button'; button.addEventListener('click', () => submitQuestion(question, answerHistory)); chips.append(button);}
    response.append(chips);
  }
}
function setBusy(busy) {
  send.classList.toggle('is-busy', busy);
  send.setAttribute('aria-label', busy ? '응답 중지' : '질문 보내기');
  send.title = busy ? '응답 중지' : '질문 보내기';
  send.firstElementChild.textContent = busy ? '■' : '↑';
}
async function getAPI() {
  if (!apiInfo) apiInfo = fetch('./config', {credentials:'same-origin',signal:AbortSignal.timeout(15000)}).then(async response => {
    if (!response.ok) throw new Error('config');
    const settings = await response.json();
    const fn = settings.dependencies.find(dependency => dependency.api_name === 'respond');
    if (!fn) throw new Error('endpoint');
    return {prefix: settings.api_prefix || '/gradio_api', index: fn.id};
  }).catch(error => {apiInfo = null; throw error;});
  return apiInfo;
}
function cancelRemote(job) {
  if (job.eventId && job.api) fetch(`${job.api.prefix}/cancel`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({event_id:job.eventId,session_hash:job.eventId,fn_index:job.api.index}),keepalive:true}).catch(() => {});
}
function stopCurrent(showMessage = true) {
  const job = active;
  if (!job) return;
  job.cancelled = true;
  job.controller.abort();
  cancelRemote(job);
  if (showMessage) {job.response.replaceChildren(make('p', 'sm-pending', '응답 표시를 중지했어요. 다시 질문할 수 있어요.'));}
  active = null; serial++; setBusy(false);
}
async function completeEvent(response, job) {
  if (!response.ok || !response.body) throw new Error('response');
  const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = '';
  try {
    while (true) {
      const {value, done} = await reader.read();
      buffer += decoder.decode(value, {stream:!done}).replace(/\r/g, '');
      let end;
      while ((end = buffer.indexOf('\n\n')) !== -1) {
        const event = buffer.slice(0, end); buffer = buffer.slice(end + 2);
        const kind = event.match(/^event:\s*(\S+)/m)?.[1];
        const data = event.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trim()).join('\n');
        if (kind === 'error') throw new Error('server');
        if (kind === 'complete') {
          const output = JSON.parse(data);
          if (!Array.isArray(output) || typeof output[0]?.answer !== 'string') throw new Error('contract');
          return output[0];
        }
      }
      if (done) throw new Error('incomplete');
      if (job.cancelled) throw new DOMException('Stopped', 'AbortError');
    }
  } finally {reader.cancel().catch(() => {});}
}
async function submitQuestion(value, contextOverride = null) {
  const question = String(value || '').trim();
  if (active) {$('.sm-notice').textContent = '답변을 기다리거나 중지한 뒤 질문해 주세요.'; return;}
  if (!question) {textarea.focus(); return;}
  if (question.length > 500) {$('.sm-notice').textContent = '질문은 500자 이내로 입력해 주세요.'; return;}
  // A follow-up chip belongs to its own answer, including when clicked later.
  const context = (contextOverride || history).slice(-8).map(item => ({role:item.role,content:item.content.slice(0,1200)}));
  $('.sm-notice').textContent = '';
  $('.sm-welcome').hidden = true;
  textarea.value = ''; resizeInput();
  const turn = make('section', 'sm-turn');
  const user = make('div', 'sm-user'); user.append(make('div', 'sm-user-bubble', question)); turn.append(user);
  const head = make('div', 'sm-assistant-head'); head.append(make('span', 'sm-avatar', '수강'), make('span', '', '수강메이트')); turn.append(head);
  const response = make('div', 'sm-response');
  const pending = make('div', 'sm-pending'); pending.append(make('span', 'sm-pulse'), make('span', '', '답변을 준비하고 있어요.')); response.append(pending); turn.append(response); log.append(turn);
  const job = {token:++serial,controller:new AbortController(),response,cancelled:false,eventId:null,api:null};
  active = job; setBusy(true); scrollBottom();
  const timeout = setTimeout(() => {if (active === job) {job.timedOut = true; job.controller.abort(); cancelRemote(job);}}, 90000);
  try {
    job.api = await getAPI();
    if (job.cancelled) return;
    // Keep the short enqueue request alive on Stop so its returned ID can be cancelled.
    const queued = await fetch(`${job.api.prefix}/call/respond`, {method:'POST',signal:AbortSignal.timeout(15000),headers:{'Content-Type':'application/json'},body:JSON.stringify({data:[question,context]})});
    if (!queued.ok) throw new Error('queue');
    const ticket = await queued.json(); job.eventId = ticket.event_id;
    if (typeof job.eventId !== 'string' || !/^[a-zA-Z0-9_-]+$/.test(job.eventId)) throw new Error('ticket');
    if (job.cancelled) {cancelRemote(job); return;}
    if (job.timedOut) {cancelRemote(job); throw new Error('timeout');}
    const stream = await fetch(`${job.api.prefix}/call/respond/${job.eventId}`, {signal:job.controller.signal});
    const result = await completeEvent(stream, job);
    if (job.token !== serial || job.cancelled) return;
    const nearBottom = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 160;
    const answerHistory = [...context, {role:'user',content:question}, {role:'assistant',content:result.answer}].slice(-8);
    renderResponse(response, result, answerHistory);
    if (!result.error) history.splice(0, history.length, ...answerHistory);
    if (nearBottom) scrollBottom();
  } catch (error) {
    if (job.cancelled || job.token !== serial) return;
    response.replaceChildren(make('p', 'sm-warning', job.timedOut ? '응답 시간이 길어지고 있어요. 잠시 후 다시 시도해 주세요.' : '연결이 원활하지 않아요. 잠시 후 다시 시도해 주세요.'));
    const retry = make('button', 'sm-outline', '다시 시도'); retry.type = 'button'; retry.addEventListener('click', () => submitQuestion(question, context)); response.append(retry);
  } finally {clearTimeout(timeout); if (active === job) {active = null; setBusy(false);}}
}
function newChat() {stopCurrent(false); history.length = 0; log.replaceChildren(); $('.sm-welcome').hidden = false; $('.sm-notice').textContent = ''; textarea.value = ''; resizeInput(); scroller.scrollTop = 0; dialog.close(); textarea.focus();}
function openDialog(title) {$('#sm-dialog-title').textContent = title; dialogBody.replaceChildren(); if (!dialog.open) dialog.showModal();}
function openCatalog() {
  openDialog('과목 목록');
  dialogBody.append(make('p', '', `${config.scope} · 과목을 선택하면 바로 질문할 수 있어요.`));
  const search = make('input', 'sm-catalog-search'); search.type = 'search'; search.placeholder = '과목명, 학수번호, 교수 검색'; search.setAttribute('aria-label', '과목 검색');
  const list = make('div', 'sm-catalog-list'); dialogBody.append(search, list);
  const draw = () => {
    const term = search.value.trim().toLowerCase(); list.replaceChildren();
    for (const course of config.catalog.filter(course => [course.course_name,course.course_code,course.professor].join(' ').toLowerCase().includes(term))) {
      const button = make('button', 'sm-catalog-item'); button.type = 'button';
      button.append(make('strong', '', course.course_name), make('span', '', [course.course_code,course.completion_type,course.professor].filter(Boolean).join(' · ')), make('small', '', course.schedule_summary || '수업시간 미기재'));
      button.addEventListener('click', () => {dialog.close(); textarea.value = `${course.course_code} ${course.course_name}에 대해 알려줘`; resizeInput(); textarea.focus();}); list.append(button);
    }
    if (!list.children.length) list.append(make('p', '', '일치하는 과목이 없어요. 다른 검색어를 입력해 보세요.'));
  };
  search.addEventListener('input', draw); draw(); search.focus();
}
function openAbout() {
  openDialog('서비스 안내');
  dialogBody.append(make('h3', '', '어떤 자료로 답하나요?'), make('p', '', config.data_notice));
  dialogBody.append(make('h3', '', '답변과 근거를 함께 확인하세요'), make('p', '', '수업시간, 이수구분, 평가방식을 조회하고 과목을 비교할 수 있어요. 답변 아래의 근거 보기를 펼치면 해당 강의계획서의 발췌문과 원문 링크를 확인할 수 있습니다. 확인되지 않는 내용은 답변하지 않으며, 실제 수강신청 전에는 학교 공지를 확인해 주세요.'));
  dialogBody.append(make('h3', '', '질문과 대화는 어떻게 처리하나요?'), make('p', '', config.online ? '질문과 최근 대화 일부가 답변 생성을 위해 Google Gemini로 전송될 수 있습니다. 개인정보는 입력하지 마세요. 대화는 서버 파일에 저장하지 않으며, 새 대화나 새로고침을 하면 화면의 대화가 초기화됩니다. 공개 데모는 요청이 많으면 잠시 제한될 수 있습니다.' : '이 화면은 외부 AI 요청 없이 자료를 조회합니다. 대화는 서버 파일에 저장하지 않으며, 새 대화나 새로고침을 하면 화면의 대화가 초기화됩니다.'));
  const links = make('p'); const repo = make('a', '', 'GitHub에서 프로젝트 보기 ↗'); repo.href = 'https://github.com/JunH14/sugang-mate'; repo.target = '_blank'; repo.rel = 'noopener noreferrer'; links.append(repo); dialogBody.append(links);
}
for (const question of config.examples) {const button = make('button', 'sm-example'); button.type = 'button'; button.append(make('span', '', question), make('i', '', '↗')); button.addEventListener('click', () => submitQuestion(question)); $('.sm-examples').append(button);}
$$('.sm-new').forEach(button => button.addEventListener('click', newChat));
$$('.sm-catalog').forEach(button => button.addEventListener('click', openCatalog));
$$('.sm-about').forEach(button => button.addEventListener('click', openAbout));
$('.sm-close').addEventListener('click', () => dialog.close());
dialog.addEventListener('click', event => {if (event.target === dialog) {const rect = dialog.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();}});
$('.sm-menu').addEventListener('click', () => {openDialog('메뉴'); const menu = make('div', 'sm-menu-actions'); for (const [name,action] of [['새 대화',newChat],['과목 목록',openCatalog],['서비스 안내',openAbout]]) {const button = make('button', '', name); button.type = 'button'; button.addEventListener('click', action); menu.append(button);} dialogBody.append(menu);});
$('.sm-composer').addEventListener('submit', event => {event.preventDefault(); if (active) stopCurrent(); else submitQuestion(textarea.value);});
textarea.addEventListener('input', resizeInput);
textarea.addEventListener('keydown', event => {if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {event.preventDefault(); if (!active) submitQuestion(textarea.value);}});
const viewport = window.visualViewport;
const resizeViewport = () => {root.style.setProperty('--sm-height', `${viewport ? viewport.height : window.innerHeight}px`);};
viewport?.addEventListener('resize', resizeViewport); window.addEventListener('resize', resizeViewport); resizeViewport();
}
