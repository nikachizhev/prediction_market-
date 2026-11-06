import { describe, expect, it } from 'vitest';
import { ApiError, type AssistResult, type MarketDraftView } from './api';
import { createPayload, defaultDraft, editDraft, localIso, readDraft, type CreateDraft } from './create';
import {
  chatDraftTarget,
  cleanAssistOptions,
  draftFromChatDraft,
  lineAnswer,
  lineFailure,
  lineReady,
  lineUsesAssist,
  onelineDraft,
  quickProblems,
  wizardEvent,
  wizardFromLine,
} from './oneline';

const NOW = new Date(2026, 10, 4, 12, 0); // 4 Nov 2026, 12:00 local

function line(text: string, patch: Partial<CreateDraft> = {}): CreateDraft {
  return { ...defaultDraft(NOW), line: text, ...patch };
}

function result(patch: Partial<AssistResult> = {}): AssistResult {
  return {
    question: 'Сдаст ли Петя матан до 20 декабря?',
    why: 'Добавили срок.',
    criteria: 'ДА — если в ведомости оценка 3 и выше. НЕТ — если нет. Чем подтвердить: скрин ведомости.',
    category: 'study',
    closes_at: localIso('2026-12-19', '23:59')!,
    resolves_at: localIso('2026-12-21', '23:59')!,
    left: 18,
    options: null,
    ...patch,
  };
}

const LINE = 'спорим петя не сдаст матан';

describe('«Сделать рынок»', () => {
  it('needs 10 to 120 characters, like the helper', () => {
    expect(lineReady('коротко')).toBe(false);
    expect(lineReady('   сдаст ли   ')).toBe(false);
    expect(lineReady(LINE)).toBe(true);
    expect(lineReady('x'.repeat(121))).toBe(false);
  });

  it('asks the helper only when it is on and has suggestions left', () => {
    expect(lineUsesAssist({ enabled: true, left: 3 })).toBe(true);
    expect(lineUsesAssist({ enabled: true, left: 0 })).toBe(false);
    expect(lineUsesAssist({ enabled: false, left: 3 })).toBe(false);
    expect(lineUsesAssist(undefined)).toBe(false);
  });
});

describe('the helper answered', () => {
  it('opens the ready market with every field filled', () => {
    const outcome = lineAnswer(line(LINE, { chat: 'k7Q2', announce: true }), result(), NOW);
    expect(outcome.kind).toBe('quick');
    expect(outcome.left).toBe(18);
    const d = outcome.draft;
    expect(d.flow).toBe('quick');
    expect(d.title).toBe('Сдаст ли Петя матан до 20 декабря?');
    expect(d.line).toBe(LINE);
    expect(d.category).toBe('study');
    expect(d.criteria).toMatch(/^ДА — если/);
    expect([d.closeDate, d.closeTime, d.resultDate, d.resultTime]).toEqual(['2026-12-19', '23:59', '2026-12-21', '23:59']);
    expect(d.mode).toBeUndefined();
    expect(d.chat).toBe('k7Q2');
    expect(d.announce).toBe(true);
    expect(d.assist).toBe('oneline');
  });

  it('keeps the line as the question when there is no clearer one', () => {
    expect(onelineDraft(line('Пойдёт ли снег в субботу?'), result({ question: null }), NOW).title).toBe('Пойдёт ли снег в субботу?');
    expect(onelineDraft(line('Пойдёт ли снег в субботу?'), result({ question: 'Кратко' }), NOW).title).toBe('Пойдёт ли снег в субботу?');
  });

  it('makes a multiple-choice market for «кто из…»', () => {
    const d = onelineDraft(line('кто из нас первым сдаст курсач'), result({ options: ['Аня', ' Петя ', 'аня', ''] }), NOW);
    expect(d.mode).toBe('multiple');
    expect(d.options).toEqual(['Аня', 'Петя']);
  });

  it('ignores options that do not make a market', () => {
    expect(cleanAssistOptions(null)).toBeNull();
    expect(cleanAssistOptions(['Один'])).toBeNull();
    expect(cleanAssistOptions(Array.from({ length: 12 }, (_, i) => `Вариант ${i + 1}`))).toHaveLength(10);
    expect(cleanAssistOptions(['x'.repeat(100), 'y'])![0]).toHaveLength(80);
  });

  it('keeps the default dates when the helper dates do not parse', () => {
    const d = onelineDraft(line(LINE), result({ closes_at: 'nope' }), NOW);
    const def = defaultDraft(NOW);
    expect([d.closeDate, d.resultDate]).toEqual([def.closeDate, def.resultDate]);
  });

  it('goes to the wizard with the warning on a forbidden topic', () => {
    const outcome = lineAnswer(line(LINE), { warning: 'forbidden', text: 'Про здоровье людей нельзя.', left: 17 }, NOW);
    expect(outcome.kind).toBe('wizard');
    if (outcome.kind !== 'wizard') return;
    expect(outcome.ui).toBe('warn');
    expect(outcome.warning).toBe('Про здоровье людей нельзя.');
    expect(outcome.left).toBe(17);
    expect(outcome.draft.flow).toBe('wizard');
    expect(outcome.draft.step).toBe(1);
    expect(outcome.draft.title).toBe(LINE);
    expect(outcome.draft.criteria).toBe('');
  });
});

describe('the helper failed', () => {
  it.each([
    [new ApiError(503, 'Помощник недоступен.'), 'off', null],
    [new ApiError(0, 'Нет соединения.'), 'off', null],
    [new Error('boom'), 'off', null],
    [new ApiError(429, 'Лимит.'), 'limit', 0],
  ] as const)('%s → the wizard with the text in the question field', (err, ui, left) => {
    const outcome = lineFailure(line(`  ${LINE}  `, { chat: 'k7Q2' }), err, NOW);
    expect(outcome.kind).toBe('wizard');
    if (outcome.kind !== 'wizard') return;
    expect(outcome.ui).toBe(ui);
    expect(outcome.left).toBe(left);
    expect(outcome.draft).toMatchObject({ flow: 'wizard', step: 1, title: LINE, chat: 'k7Q2', assist: null });
  });

  it('logs the way to the wizard', () => {
    expect(wizardEvent('manual')).toEqual({ name: 'oneline_wizard', props: { reason: 'manual' } });
    expect(wizardEvent('unavailable')).toEqual({ name: 'oneline_wizard', props: { reason: 'unavailable' } });
  });

  it('«Заполнить самому» drops what the helper filled', () => {
    const ready = onelineDraft(line(LINE), result(), NOW);
    const manual = wizardFromLine(ready, NOW);
    expect(manual).toMatchObject({ flow: 'wizard', step: 1, title: LINE, criteria: '', category: '', assist: null });
  });
});

describe('editing the ready market', () => {
  const ready = () => onelineDraft(line(LINE, { chat: 'k7Q2' }), result(), NOW);

  it('is created as is with assist "oneline"', () => {
    expect(createPayload(ready())).toMatchObject({ assist: 'oneline', chat: 'k7Q2', category: 'study' });
  });

  it.each([
    [{ title: 'Сдаст ли Петя матан до Нового года?' }],
    [{ criteria: 'ДА — если сдаст. НЕТ — если нет, по ведомости.' }],
    [{ category: 'fun' }],
    [{ closeTime: '20:00' }],
    [{ resultDate: '2026-12-22' }],
    [{ mode: 'multiple' as const, options: ['Аня', 'Петя'] }],
  ])('any field change %o makes it "oneline_edited"', (patch) => {
    expect(createPayload(editDraft(ready(), patch))!.assist).toBe('oneline_edited');
  });

  it('re-entering the same value or ticking «Объявить в чате» is not an edit', () => {
    const d = ready();
    expect(editDraft(d, { category: 'study' }).assist).toBe('oneline');
    expect(editDraft(d, { announce: true }).assist).toBe('oneline');
  });

  it('names what to fix while «Создать» is disabled', () => {
    const d = ready();
    expect(quickProblems(d, NOW)).toEqual({});
    const broken = { ...d, title: 'коротко', criteria: 'мало', category: '', mode: 'multiple' as const, options: ['Аня', 'аня'] };
    expect(Object.keys(quickProblems(broken, NOW)).sort()).toEqual(['category', 'criteria', 'options', 'title']);
    expect(quickProblems({ ...d, closeDate: '2026-11-04', closeTime: '12:10' }, NOW).closes).toBe('Приём прогнозов — хотя бы час.');
  });
});

describe('«Объявить в чате» and the chat draft in the payload', () => {
  it('sends announce only with a chat', () => {
    const d = onelineDraft(line(LINE), result(), NOW);
    expect(createPayload({ ...d, announce: true })).not.toHaveProperty('announce');
    expect(createPayload({ ...d, chat: 'k7Q2', announce: true })).toMatchObject({ chat: 'k7Q2', announce: true });
    expect(createPayload({ ...d, chat: 'k7Q2', announce: false })).not.toHaveProperty('announce');
  });

  it('keeps the new fields through storage', () => {
    const d = { ...onelineDraft(line(LINE, { chat: 'k7Q2', announce: true }), result({ options: ['Аня', 'Петя'] }), NOW), draftCode: 'd9' };
    expect(readDraft(JSON.parse(JSON.stringify(d)))).toEqual(d);
    expect(readDraft({ ...JSON.parse(JSON.stringify(d)), flow: 'nope', assist: 'nope' })).toMatchObject({ flow: 'wizard', assist: null });
  });
});

describe('startapp=draft_<code>', () => {
  function view(patch: Partial<MarketDraftView> = {}): MarketDraftView {
    return {
      code: 'Dk29x',
      chat: { code: 'k7Q2', title: 'Группа ПИ-24' },
      title: 'Спорим, Петя не сдаст матан до 20 декабря?',
      criteria: 'ДА — если в ведомости оценка 3 и выше. НЕТ — если нет.',
      category: 'study',
      closes_at: localIso('2026-12-19', '23:59'),
      resolves_at: localIso('2026-12-21', '23:59'),
      options: [],
      assisted: true,
      market_id: null,
      ...patch,
    };
  }

  it('opens the creation preview with the draft fields and its chat', () => {
    const target = chatDraftTarget(view(), NOW);
    expect('draft' in target).toBe(true);
    if (!('draft' in target)) return;
    const d = target.draft;
    expect(d).toMatchObject({ flow: 'quick', chat: 'k7Q2', draftCode: 'Dk29x', category: 'study', closeDate: '2026-12-19', resultDate: '2026-12-21' });
    expect(d.mode).toBeUndefined();
    expect(createPayload(d)).toMatchObject({ draft: 'Dk29x', chat: 'k7Q2', title: 'Спорим, Петя не сдаст матан до 20 декабря?' });
    expect(createPayload(d)).not.toHaveProperty('announce');
    expect(createPayload(d)).not.toHaveProperty('assist');
  });

  it('opens the market when it is made already', () => {
    expect(chatDraftTarget(view({ market_id: 42 }), NOW)).toEqual({ market: 42 });
  });

  it('keeps options for «кто из…»', () => {
    expect(draftFromChatDraft(view({ options: ['Аня', 'Петя', 'Вова'] }), NOW)).toMatchObject({ mode: 'multiple', options: ['Аня', 'Петя', 'Вова'] });
  });

  it('opens the wizard with the message text when the helper did not help', () => {
    const d = draftFromChatDraft(view({ assisted: false, criteria: '', category: null, closes_at: null, resolves_at: null }), NOW);
    expect(d).toMatchObject({ flow: 'wizard', step: 1, title: 'Спорим, Петя не сдаст матан до 20 декабря?', chat: 'k7Q2', draftCode: 'Dk29x', criteria: '' });
    expect(d.closeDate).toBe(defaultDraft(NOW).closeDate);
  });

  it('a draft without a chat sends no draft code', () => {
    const d = draftFromChatDraft(view(), NOW);
    expect(createPayload({ ...d, chat: null })).not.toHaveProperty('draft');
  });
});
