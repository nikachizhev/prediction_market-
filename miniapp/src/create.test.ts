import { describe, expect, it } from 'vitest';
import {
  balanceAfterCreation,
  canProceed,
  clearDraft,
  createPayload,
  dateErrors,
  dateInputValue,
  defaultDraft,
  errorField,
  loadDraft,
  localIso,
  previewCard,
  saveDraft,
  stepOfField,
  type CreateDraft,
} from './create';

const NOW = new Date(2026, 9, 25, 12, 0); // 25 Oct 2026, 12:00 local

function draft(patch: Partial<CreateDraft> = {}): CreateDraft {
  return {
    ...defaultDraft(NOW),
    title: 'Будет ли снег в Москве до 1 декабря?',
    criteria: 'ДА, если Гидрометцентр сообщит о снеге.',
    category: 'weather',
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
    expect(errors.resolves).toBe('Укажите дату.');
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
    expect(payload.category).toBe('weather');
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
    expect(card.category).toBe('weather');
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
