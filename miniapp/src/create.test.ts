import { afterEach, describe, expect, it, vi } from 'vitest';
import type { AssistResult, MarketCard, MarketTemplate } from './api';
import {
  acceptSuggestion,
  applyAssist,
  applyTemplate,
  assistCard,
  assistLeftLabel,
  balanceAfterCreation,
  canProceed,
  changedFields,
  clearDraft,
  createPayload,
  dateErrors,
  dateInputValue,
  dayMonthOf,
  defaultDraft,
  dismissSuggestion,
  editDraft,
  errorField,
  fillTemplate,
  loadDraft,
  localIso,
  localParts,
  previewCard,
  readDraft,
  resolutionRule,
  revertSuggestion,
  saveDraft,
  shiftDate,
  similarLine,
  stepOfField,
  templateCategories,
  templateFilled,
  templateMinDate,
  undoAssist,
  type CreateDraft,
} from './create';

const NOW = new Date(2026, 9, 25, 12, 0); // 25 Oct 2026, 12:00 local

function draft(patch: Partial<CreateDraft> = {}): CreateDraft {
  return {
    ...defaultDraft(NOW),
    title: 'Будет ли снег в Москве до 1 декабря?',
    criteria: 'ДА, если Гидрометцентр сообщит о снеге.',
    category: 'fun',
    ...patch,
  };
}

describe('localIso', () => {
  it('keeps the local wall time and appends the UTC offset', () => {
    const iso = localIso('2026-11-15', '23:59');
    expect(iso).toMatch(/^2026-11-15T23:59:00[+-]\d{2}:\d{2}$/);
    const back = new Date(Date.parse(iso!));
    expect([back.getFullYear(), back.getMonth(), back.getDate(), back.getHours(), back.getMinutes()]).toEqual([
      2026, 10, 15, 23, 59,
    ]);
  });

  it('rejects malformed and impossible dates', () => {
    expect(localIso('', '12:00')).toBeNull();
    expect(localIso('2026-11-15', '')).toBeNull();
    expect(localIso('2026-02-30', '12:00')).toBeNull();
    expect(localIso('2026-11-15', '25:00')).toBeNull();
  });
});

describe('defaultDraft', () => {
  it('closes in a week at 23:59 and resolves the next day', () => {
    const d = defaultDraft(NOW);
    expect(d.step).toBe(1);
    expect(d.closeDate).toBe('2026-11-01');
    expect(d.closeTime).toBe('23:59');
    expect(d.resultDate).toBe('2026-11-02');
    expect(Object.keys(dateErrors(d, NOW))).toHaveLength(0);
  });

  it('formats dates for date inputs', () => {
    expect(dateInputValue(new Date(2026, 0, 5))).toBe('2026-01-05');
  });
});

describe('dateErrors', () => {
  it('uses selected result time and rejects a result before trading closes', () => {
    const valid = draft({ closeDate: '2026-11-01', closeTime: '14:00', resultDate: '2026-11-01', resultTime: '15:30' });
    expect(createPayload(valid)!.resolves_at).toMatch(/^2026-11-01T15:30:00/);
    expect(dateErrors(valid, NOW).resolves).toBeUndefined();
    expect(dateErrors({ ...valid, resultTime: '13:00' }, NOW).resolves).toMatch(/раньше/);
    expect(dateErrors({ ...valid, resultTime: '' }, NOW).resolves).toBe('Укажите дату и время итога.');
  });
  it('needs at least an hour of trading', () => {
    expect(dateErrors(draft({ closeDate: '2026-10-25', closeTime: '12:30' }), NOW).closes).toBe(
      'Приём прогнозов — хотя бы час.',
    );
    expect(dateErrors(draft({ closeDate: '2026-10-25', closeTime: '13:30' }), NOW).closes).toBeUndefined();
  });

  it('allows trading for at most a year', () => {
    expect(dateErrors(draft({ closeDate: '2027-11-01', resultDate: '2027-11-02' }), NOW).closes).toBe(
      'Приём прогнозов — не дольше года.',
    );
  });

  it('does not allow the result before the end of trading', () => {
    expect(dateErrors(draft({ closeDate: '2026-11-10', resultDate: '2026-11-09' }), NOW).resolves).toBe(
      'Итог не может стать известен раньше конца приёма прогнозов.',
    );
    // Same day is fine: the result is due by the end of that day.
    expect(dateErrors(draft({ closeDate: '2026-11-10', resultDate: '2026-11-10' }), NOW).resolves).toBeUndefined();
  });

  it('limits the gap between the end of trading and the result to a year', () => {
    expect(dateErrors(draft({ resultDate: '2027-12-01' }), NOW).resolves).toMatch(/не позже чем через год/);
  });

  it('asks for missing dates', () => {
    const errors = dateErrors(draft({ closeDate: '', resultDate: '' }), NOW);
    expect(errors.closes).toBe('Укажите дату и время.');
    expect(errors.resolves).toBe('Укажите дату и время итога.');
  });
});

describe('canProceed', () => {
  it('step 1 needs a question of 10..120 characters', () => {
    expect(canProceed(draft({ title: 'Коротко?' }), 1, NOW)).toBe(false);
    expect(canProceed(draft({ title: '   Будет ли дождь?   ' }), 1, NOW)).toBe(true);
    expect(canProceed(draft({ title: 'x'.repeat(121) }), 1, NOW)).toBe(false);
  });

  it('step 2 needs criteria, a category and valid dates', () => {
    expect(canProceed(draft(), 2, NOW)).toBe(true);
    expect(canProceed(draft({ criteria: 'ДА, если да' }), 2, NOW)).toBe(false);
    expect(canProceed(draft({ category: '' }), 2, NOW)).toBe(false);
    expect(canProceed(draft({ resultDate: '2026-10-30' }), 2, NOW)).toBe(false);
  });

  it('step 3 re-checks everything', () => {
    expect(canProceed(draft(), 3, NOW)).toBe(true);
    expect(canProceed(draft({ title: '' }), 3, NOW)).toBe(false);
  });
});

describe('createPayload', () => {
  it('trims texts and sends ISO dates with the offset', () => {
    const payload = createPayload(draft({ title: '  Вопрос про снег?  ', criteria: ' ДА, если снег. ' }))!;
    expect(payload.title).toBe('Вопрос про снег?');
    expect(payload.criteria).toBe('ДА, если снег.');
    expect(payload.category).toBe('fun');
    expect(payload.closes_at).toMatch(/^2026-11-01T23:59:00[+-]\d{2}:\d{2}$/);
    expect(payload.resolves_at).toMatch(/^2026-11-02T23:59:00[+-]\d{2}:\d{2}$/);
  });

  it('is null without dates', () => {
    expect(createPayload(draft({ closeDate: '' }))).toBeNull();
  });
});

describe('errorField', () => {
  it('maps server validation errors to fields', () => {
    expect(errorField('Напишите вопрос.')).toBe('title');
    expect(errorField('Вопрос — не длиннее 120 символов.')).toBe('title');
    expect(errorField('Опишите, как определяем результат.')).toBe('criteria');
    expect(errorField('Выберите категорию.')).toBe('category');
    expect(errorField('Приём прогнозов — хотя бы час.')).toBe('closes');
    expect(errorField('Укажите, до какого момента принимаем прогнозы и когда станет известен итог.')).toBe('closes');
    expect(errorField('Итог не может стать известен раньше конца приёма прогнозов.')).toBe('resolves');
  });

  it('leaves limits and balance to a toast', () => {
    expect(errorField('Не больше 3 новых рынков в день.')).toBeNull();
    expect(errorField('Создание рынка стоит 100 монет — на балансе не хватает.')).toBeNull();
  });

  it('knows the step of each field', () => {
    expect(stepOfField('title')).toBe(1);
    expect(stepOfField('category')).toBe(2);
    expect(stepOfField('resolves')).toBe(2);
  });
});

describe('preview and cost', () => {
  it('previews a new market at 50% with no history', () => {
    const card = previewCard(draft());
    expect(card.prob).toBe(0.5);
    expect(card.change_24h).toBe(0);
    expect(card.participants).toBe(0);
    expect(card.category).toBe('fun');
    expect(previewCard(draft({ title: '  ' })).title).toBe('Твой вопрос появится здесь');
  });

  it('says in advance when the balance is not enough', () => {
    expect(balanceAfterCreation('1240.00', '100.00')).toBe(1140);
    expect(balanceAfterCreation('100.00', '100.00')).toBe(0);
    expect(balanceAfterCreation('99.99', '100.00')).toBeNull();
  });
});

describe('draft storage', () => {
  it('keeps the draft in memory between screen visits', () => {
    const d = draft({ step: 2 });
    saveDraft(d);
    expect(loadDraft(NOW)).toEqual(d);
    clearDraft();
    expect(loadDraft(NOW)).toEqual(defaultDraft(NOW));
  });
});

describe('resolutionRule', () => {
  it('names the result date and what the bot will ask for', () => {
    expect(resolutionRule(draft({ resultDate: '2026-11-16' }), NOW)).toBe(
      '16 ноября в 23:59 бот спросит, чем закончилось: нажмёшь ДА или НЕТ и пришлёшь фото или скриншот',
    );
  });

  it('falls back to words without a date', () => {
    expect(resolutionRule(draft({ resultDate: '' }), NOW)).toMatch(/^В дату итога бот спросит/);
  });
});

describe('multiple choice mode', () => {
  it('validates 2..10 unique non-empty options in multiple mode', () => {
    expect(canProceed(draft({ mode: 'multiple', options: ['до конца октября', ''] }), 1, NOW)).toBe(false);
    expect(canProceed(draft({ mode: 'multiple', options: ['до конца октября', 'до конца октября'] }), 1, NOW)).toBe(false);
    expect(
      canProceed(
        draft({ mode: 'multiple', options: ['до конца октября', 'до конца ноября', 'в целом'] }),
        1,
        NOW,
      ),
    ).toBe(true);
  });

  it('includes options in createPayload and submarkets in previewCard', () => {
    const d = draft({ mode: 'multiple', options: [' до конца октября ', 'до конца ноября', 'в целом'] });
    const payload = createPayload(d)!;
    expect(payload).toHaveProperty('options', ['до конца октября', 'до конца ноября', 'в целом']);
    const card = previewCard(d);
    expect(card.submarkets).toHaveLength(3);
    expect(card.submarkets?.[0].label).toBe('до конца октября');
  });
});

// ---- Quick creation: templates, the AI helper, similar markets (spec E9) ----

const EXAM: MarketTemplate = {
  id: 'exam',
  category: 'study',
  text: 'Сдам ли {subject} на 4 или 5 до {date}?',
  criteria: 'ДА — если до {date} в зачётке по предмету «{subject}» стоит 4 или 5.',
  blanks: [
    { key: 'subject', label: 'Предмет', type: 'text', placeholder: 'матанализ', hint: '[предмет]' },
    { key: 'date', label: 'Когда', type: 'date', placeholder: '', hint: '[дата]' },
  ],
};

const VIEWS: MarketTemplate = {
  id: 'views',
  category: 'fun',
  text: 'Наберёт ли {video} {num} просмотров до {date}?',
  criteria: 'ДА — если до {date} у видео «{video}» будет {num} просмотров или больше.',
  blanks: [
    { key: 'video', label: 'Видео', type: 'text', placeholder: 'наш клип', hint: '[видео]' },
    { key: 'num', label: 'Сколько просмотров', type: 'number', placeholder: '10000', hint: '[N]' },
    { key: 'date', label: 'Когда', type: 'date', placeholder: '', hint: '[дата]' },
  ],
};

/** Local wall time as an ISO moment with the offset, as the server would send it. */
const at = (date: string, time: string) => localIso(date, time)!;

function result(patch: Partial<AssistResult> = {}): AssistResult {
  return {
    question: null,
    why: null,
    criteria: 'ДА — если по итогам зимней сессии нет троек.\nНЕТ — если есть хотя бы одна.',
    category: 'study',
    closes_at: at('2026-12-28', '23:59'),
    resolves_at: at('2027-01-31', '18:30'),
    left: 19,
    ...patch,
  };
}

describe('templates', () => {
  it('shows hints for empty blanks and fills values in, a date as «16 ноября»', () => {
    expect(fillTemplate(EXAM.text, EXAM.blanks, {}, NOW)).toBe('Сдам ли [предмет] на 4 или 5 до [дата]?');
    expect(fillTemplate(EXAM.text, EXAM.blanks, { subject: ' матанализ ' }, NOW)).toBe(
      'Сдам ли матанализ на 4 или 5 до [дата]?',
    );
    expect(fillTemplate(EXAM.text, EXAM.blanks, { subject: 'матанализ', date: '2026-11-16' }, NOW)).toBe(
      'Сдам ли матанализ на 4 или 5 до 16 ноября?',
    );
    expect(fillTemplate('До {unknown}', EXAM.blanks, {}, NOW)).toBe('До {unknown}');
    expect(dayMonthOf('2027-01-05', NOW)).toBe('5 января 2027');
  });

  it('puts question, rules, category and dates in: until 23:59 the day before, the result the day after', () => {
    const d = applyTemplate(draft({ category: '' }), EXAM, { subject: 'матанализ', date: '2026-12-20' }, NOW);
    expect(d.title).toBe('Сдам ли матанализ на 4 или 5 до 20 декабря?');
    expect(d.criteria).toBe('ДА — если до 20 декабря в зачётке по предмету «матанализ» стоит 4 или 5.');
    expect(d.category).toBe('study');
    expect([d.closeDate, d.closeTime, d.resultDate, d.resultTime]).toEqual(['2026-12-19', '23:59', '2026-12-21', '23:59']);
    expect(d.marks).toEqual(['dates', 'criteria', 'category']);
    expect(d.filledBy).toBe('template');
    expect(d.templateId).toBe('exam');
    expect(canProceed(d, 2, NOW)).toBe(true);
  });

  it('shifts dates across the end of a month and of a year', () => {
    const newYear = applyTemplate(draft(), EXAM, { subject: 'физику', date: '2027-01-01' }, NOW);
    expect([newYear.closeDate, newYear.resultDate]).toEqual(['2026-12-31', '2027-01-02']);
    const yearEnd = applyTemplate(draft(), EXAM, { subject: 'физику', date: '2026-12-31' }, NOW);
    expect([yearEnd.closeDate, yearEnd.resultDate]).toEqual(['2026-12-30', '2027-01-01']);
    const monthEnd = applyTemplate(draft(), EXAM, { subject: 'физику', date: '2026-11-30' }, NOW);
    expect([monthEnd.closeDate, monthEnd.resultDate]).toEqual(['2026-11-29', '2026-12-01']);
    expect(shiftDate('2028-03-01', -1)).toBe('2028-02-29');
  });

  it('cuts the question to 120 characters', () => {
    const d = applyTemplate(draft(), VIEWS, { video: 'клип '.repeat(40), num: '10000', date: '2026-12-01' }, NOW);
    expect(d.title).toHaveLength(120);
    expect(d.title.startsWith('Наберёт ли клип клип')).toBe(true);
  });

  it('needs every blank filled, the date a real one', () => {
    expect(templateFilled(EXAM, { subject: 'матанализ' })).toBe(false);
    expect(templateFilled(EXAM, { subject: '  ', date: '2026-12-20' })).toBe(false);
    expect(templateFilled(EXAM, { subject: 'матанализ', date: '2026-02-30' })).toBe(false);
    expect(templateFilled(EXAM, { subject: 'матанализ', date: '2026-12-20' })).toBe(true);
  });

  it('lists categories in the server order and starts dates tomorrow', () => {
    expect(templateCategories([VIEWS, EXAM, { ...EXAM, id: 'classes' }])).toEqual(['fun', 'study']);
    expect(templateMinDate(new Date(2026, 11, 31, 22, 0))).toBe('2027-01-01');
  });

  it('turns a used suggestion of the helper into «edited»', () => {
    const assisted = applyAssist(draft(), result());
    expect(applyTemplate(assisted, EXAM, { subject: 'матанализ', date: '2026-12-20' }, NOW).assist).toBe('edited');
    expect(applyTemplate(draft(), EXAM, { subject: 'матанализ', date: '2026-12-20' }, NOW).assist).toBeNull();
  });
});

describe('AI helper suggestion', () => {
  it('fills rules, category and both dates with times in local time, keeping the question', () => {
    const before = draft({ category: '', criteria: '' });
    const d = applyAssist(
      before,
      result({ question: 'Закрою ли я зимнюю сессию без троек до 31 января?', why: 'Добавили срок.' }),
    );
    expect(d.title).toBe(before.title);
    expect(d.criteria).toMatch(/^ДА — если по итогам зимней сессии/);
    expect(d.category).toBe('study');
    expect([d.closeDate, d.closeTime, d.resultDate, d.resultTime]).toEqual(['2026-12-28', '23:59', '2027-01-31', '18:30']);
    expect(d.marks).toEqual(['dates', 'criteria', 'category']);
    expect(d.filledBy).toBe('ai');
    expect(d.assist).toBe('accepted');
    expect(d.suggestion).toEqual({
      question: 'Закрою ли я зимнюю сессию без троек до 31 января?',
      why: 'Добавили срок.',
      state: 'open',
      own: before.title,
    });
  });

  it('reads the server moments in the local time zone', () => {
    const local = new Date(2026, 11, 28, 9, 5);
    expect(localParts(local.toISOString())).toEqual({ date: '2026-12-28', time: '09:05' });
    expect(localParts('not a date')).toBeNull();
  });

  it('does not offer a question that is the same or does not fit', () => {
    const d = draft();
    expect(applyAssist(d, result({ question: d.title })).suggestion).toBeNull();
    expect(applyAssist(d, result({ question: 'Коротко?' })).suggestion).toBeNull();
    expect(applyAssist(d, result({ question: 'x'.repeat(121) })).suggestion).toBeNull();
  });

  it('«Вернуть как было» restores every field, the result time and the own question', () => {
    const before = draft({ criteria: 'Мои правила итога, длинные.', category: 'fun', resultTime: '12:00' });
    const applied = acceptSuggestion(applyAssist(before, result({ question: 'Закрою ли я зимнюю сессию без троек?' })));
    expect(applied.title).toBe('Закрою ли я зимнюю сессию без троек?');
    const undone = undoAssist(applied);
    expect(undone.title).toBe(before.title);
    expect(undone.criteria).toBe(before.criteria);
    expect(undone.category).toBe('fun');
    expect([undone.closeDate, undone.closeTime, undone.resultDate, undone.resultTime]).toEqual([
      before.closeDate,
      before.closeTime,
      before.resultDate,
      '12:00',
    ]);
    expect(undone.marks).toEqual([]);
    expect(undone.filledBy).toBeNull();
    expect(undone.before).toBeNull();
    expect(undone.suggestion).toBeNull();
    expect(undone.assist).toBe('undone');
  });

  it('keeps the own question on undo when the suggestion was not taken', () => {
    const before = draft();
    const undone = undoAssist(dismissSuggestion(applyAssist(before, result({ question: 'Закрою ли я сессию без троек?' }))));
    expect(undone.title).toBe(before.title);
  });

  it('«Заменить», «Оставить мой» and «Вернуть мой» only touch the question', () => {
    const applied = applyAssist(draft(), result({ question: 'Закрою ли я зимнюю сессию без троек?' }));
    const accepted = acceptSuggestion(applied);
    expect(accepted.suggestion?.state).toBe('accepted');
    expect(accepted.before).toEqual(applied.before);
    expect(accepted.assist).toBe('accepted');
    const reverted = revertSuggestion(accepted);
    expect(reverted.title).toBe(applied.title);
    expect(reverted.suggestion?.state).toBe('dismissed');
    const kept = dismissSuggestion(applied);
    expect(kept.title).toBe(applied.title);
    expect(kept.suggestion?.state).toBe('dismissed');
    expect(acceptSuggestion(kept)).toBe(kept);
  });
});

describe('edits after a suggestion or a template', () => {
  it('removes the mark of the edited field only', () => {
    const d = applyAssist(draft(), result());
    expect(editDraft(d, { criteria: 'Свои правила итога, подлиннее.' }).marks).toEqual(['dates', 'category']);
    expect(editDraft(d, { resultTime: '20:00' }).marks).toEqual(['criteria', 'category']);
    expect(editDraft(d, { category: 'fun' }).marks).toEqual(['dates', 'criteria']);
    const t = applyTemplate(draft(), EXAM, { subject: 'матанализ', date: '2026-12-20' }, NOW);
    expect(editDraft(t, { closeDate: '2026-12-18' }).marks).toEqual(['criteria', 'category']);
  });

  it('ignores re-entering the same value', () => {
    const d = applyAssist(draft(), result());
    expect(editDraft(d, { category: 'study' })).toBe(d);
  });

  it('records the outcome: accepted as is, edited after a change, undone', () => {
    const d = applyAssist(draft(), result({ question: 'Закрою ли я зимнюю сессию без троек?' }));
    expect(d.assist).toBe('accepted');
    expect(acceptSuggestion(d).assist).toBe('accepted');
    expect(editDraft(d, { step: 3 }).assist).toBe('accepted');
    const edited = editDraft(d, { closeTime: '20:00' });
    expect(edited.assist).toBe('edited');
    expect(undoAssist(edited).assist).toBe('undone');
    expect(editDraft(draft(), { criteria: 'Без помощника, сам.' }).assist).toBeNull();
  });

  it('starts the helper over when the question changes', () => {
    const d = applyAssist(draft(), result({ question: 'Закрою ли я зимнюю сессию без троек?' }));
    const changed = editDraft(d, { title: 'Совсем другой вопрос про сессию?' });
    expect(changed.before).toBeNull();
    expect(changed.suggestion).toBeNull();
    expect(changed.marks).toEqual(d.marks);
    expect(changed.assist).toBe('accepted');
  });

  it('knows which server errors went stale', () => {
    const d = draft();
    expect(changedFields(d, { ...d, title: 'x' })).toEqual(['title']);
    expect(changedFields(d, { ...d, closeTime: '10:00' })).toEqual(['closes', 'resolves']);
    expect(changedFields(d, { ...d, resultTime: '10:00' })).toEqual(['resolves']);
    expect(changedFields(d, applyAssist(d, result())).sort()).toEqual(['category', 'closes', 'criteria', 'resolves']);
  });
});

describe('helper card', () => {
  const on = { enabled: true, left: 5 };

  it('is not there when the helper is off or the server is older', () => {
    expect(assistCard(draft(), 'idle', undefined)).toBe('none');
    expect(assistCard(draft(), 'idle', { enabled: false, left: 20 })).toBe('none');
  });

  it('follows the request and the draft', () => {
    expect(assistCard(draft(), 'idle', on)).toBe('idle');
    expect(assistCard(draft(), 'loading', on)).toBe('loading');
    expect(assistCard(draft(), 'warn', on)).toBe('warn');
    expect(assistCard(draft(), 'off', on)).toBe('off');
    expect(assistCard(draft(), 'manual', on)).toBe('manual');
    const applied = applyAssist(draft(), result());
    expect(assistCard(applied, 'idle', on)).toBe('done');
    expect(assistCard(undoAssist(applied), 'idle', on)).toBe('idle');
    expect(assistCard(editDraft(applied, { title: 'Другой вопрос про сессию?' }), 'idle', on)).toBe('idle');
  });

  it('says the limit is used up when nothing is left', () => {
    expect(assistCard(draft(), 'idle', { enabled: true, left: 0 })).toBe('limit');
    expect(assistCard(draft(), 'limit', on)).toBe('limit');
  });

  it('shows the template card instead of the helper after a template, even with the helper off', () => {
    const t = applyTemplate(draft(), EXAM, { subject: 'матанализ', date: '2026-12-20' }, NOW);
    expect(assistCard(t, 'idle', on)).toBe('template');
    expect(assistCard(t, 'idle', undefined)).toBe('template');
  });

  it('counts the suggestions left', () => {
    expect(assistLeftLabel(19)).toBe('Осталось 19 подсказок на сегодня');
    expect(assistLeftLabel(1)).toBe('Осталось 1 подсказка на сегодня');
    expect(assistLeftLabel(3)).toBe('Осталось 3 подсказки на сегодня');
  });
});

describe('createPayload with the helper and a template', () => {
  it('sends neither when they were not used', () => {
    const payload = createPayload(draft())!;
    expect('assist' in payload).toBe(false);
    expect('template' in payload).toBe(false);
  });

  it('sends the helper outcome and the template id', () => {
    expect(createPayload(applyAssist(draft(), result()))!.assist).toBe('accepted');
    expect(createPayload(undoAssist(applyAssist(draft(), result())))!.assist).toBe('undone');
    const t = createPayload(applyTemplate(draft(), EXAM, { subject: 'матанализ', date: '2026-12-20' }, NOW))!;
    expect(t.template).toBe('exam');
    expect('assist' in t).toBe(false);
    expect(t.closes_at).toMatch(/^2026-12-19T23:59:00/);
    expect(t.resolves_at).toMatch(/^2026-12-21T23:59:00/);
  });
});

describe('stored drafts', () => {
  afterEach(() => {
    clearDraft();
    vi.unstubAllGlobals();
  });

  it('loads a draft saved before templates and the helper', () => {
    const old = {
      step: 2,
      title: 'Будет ли снег в Москве до 1 декабря?',
      criteria: 'ДА, если Гидрометцентр сообщит о снеге.',
      category: 'fun',
      closeDate: '2026-11-01',
      closeTime: '23:59',
      resultDate: '2026-11-02',
    };
    const store = new Map<string, string>();
    vi.stubGlobal('window', {
      sessionStorage: {
        getItem: (k: string) => store.get(k) ?? null,
        setItem: (k: string, v: string) => store.set(k, v),
        removeItem: (k: string) => store.delete(k),
      },
    });
    clearDraft();
    store.set('create-draft', JSON.stringify(old));
    expect(loadDraft(NOW)).toEqual({
      ...old,
      resultTime: '23:59',
      marks: [],
      filledBy: null,
      templateId: null,
      before: null,
      assist: null,
      suggestion: null,
    });
  });

  it('keeps the helper state through storage and drops broken parts', () => {
    const d = acceptSuggestion(applyAssist(draft(), result({ question: 'Закрою ли я зимнюю сессию без троек?' })));
    expect(readDraft(JSON.parse(JSON.stringify(d)))).toEqual(d);
    const broken = { ...JSON.parse(JSON.stringify(d)), marks: ['dates', 'nope'], before: { criteria: 1 }, assist: 'maybe' };
    const read = readDraft(broken)!;
    expect(read.marks).toEqual(['dates']);
    expect(read.before).toBeNull();
    expect(read.assist).toBeNull();
    expect(readDraft({ step: 4 })).toBeNull();
  });
});

describe('similar markets', () => {
  it('shows the price and the number of participants', () => {
    const card: MarketCard = { ...previewCard(draft()), prob: 0.41, participants: 23 };
    expect(similarLine(card)).toBe('41% ДА · 23 участника');
    expect(similarLine({ ...card, participants: 1 })).toBe('41% ДА · 1 участник');
  });
});
