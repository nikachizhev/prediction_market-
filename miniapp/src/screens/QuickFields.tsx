// Fields of the ready market from «О чём спорим?» (spec E9.5): every field is a row, a tap opens a
// short sheet to edit it. No mockup for phase 3d: built from the wizard's fields and styles.
import type { Category } from '../api';
import { BottomSheet } from '../components/BottomSheet';
import {
  closesAtIso,
  resolvesAtIso,
  OPTION_MAX,
  OPTIONS_MAX,
  OPTIONS_MIN,
  TITLE_MAX,
  type CreateDraft,
  type DraftEdit,
} from '../create';
import { formatDayMonth } from '../format';
import type { QuickField } from '../oneline';
import { tg } from '../telegram';

const LABELS: Record<QuickField, string> = {
  title: 'Вопрос',
  options: 'Варианты ответа',
  criteria: 'Как определяем результат',
  closes: 'Приём прогнозов до',
  resolves: 'Итог станет известен',
  category: 'Категория',
};

interface QuickFieldsProps {
  draft: CreateDraft;
  update: (patch: DraftEdit) => void;
  categories: Category[];
  /** Server error or local problem of a field. */
  problem: (field: QuickField) => string | undefined;
  editing: QuickField | null;
  onEdit: (field: QuickField | null) => void;
  now: Date;
}

export function QuickFields({ draft, update, categories, problem, editing, onEdit, now }: QuickFieldsProps) {
  const label = new Map(categories.map((c) => [c.slug, c.label]));
  const closes = closesAtIso(draft);
  const resolves = resolvesAtIso(draft);
  const multiple = draft.mode === 'multiple';
  const options = draft.options ?? ['', ''];

  const fields: { field: QuickField; value: string }[] = [
    { field: 'title', value: draft.title.trim() },
    ...(multiple ? [{ field: 'options' as const, value: options.map((o) => o.trim()).filter(Boolean).join(' · ') }] : []),
    { field: 'criteria', value: draft.criteria.trim() },
    { field: 'closes', value: closes ? `${formatDayMonth(closes, now)} в ${draft.closeTime}` : '' },
    { field: 'resolves', value: resolves ? `${formatDayMonth(resolves, now)} в ${draft.resultTime}` : '' },
    { field: 'category', value: draft.category ? (label.get(draft.category) ?? draft.category) : '' },
  ];

  const open = (field: QuickField) => {
    tg.HapticFeedback.selection();
    onEdit(field);
  };
  const done = () => onEdit(null);

  return (
    <>
      <section aria-label="Готовый рынок" className="card create-quick">
        {fields.map(({ field, value }) => {
          const error = problem(field);
          return (
            <div key={field} className="create-qrow-wrap">
              <button
                type="button"
                className={`create-qrow ${error ? 'is-invalid' : ''}`}
                onClick={() => open(field)}
                aria-label={`${LABELS[field]}: изменить`}
              >
                <span className="create-qrow-text">
                  <span className="field-label">{LABELS[field]}</span>
                  <span className={`create-qrow-value ${field === 'title' ? 'is-title' : ''} ${value ? '' : 'sub'}`}>
                    {value || 'Нажми, чтобы заполнить'}
                  </span>
                </span>
                <PencilIcon />
              </button>
              {error && <span className="field-error create-qrow-error">{error}</span>}
            </div>
          );
        })}
      </section>

      <BottomSheet open={editing !== null} onClose={done} label={editing ? LABELS[editing] : 'Поле рынка'}>
        {editing && (
          <div className="create-qsheet">
            <h2 className="create-qsheet-title">{LABELS[editing]}</h2>

            {editing === 'title' && (
              <div className="field">
                <textarea
                  className="input"
                  rows={3}
                  maxLength={TITLE_MAX}
                  value={draft.title}
                  aria-label={LABELS.title}
                  onChange={(e) => update({ title: e.target.value.slice(0, TITLE_MAX) })}
                />
                <span className="sub field-counter">
                  {draft.title.length} / {TITLE_MAX}
                </span>
              </div>
            )}

            {editing === 'options' && (
              <div className="field">
                <div className="create-options-list">
                  {options.map((opt, idx) => (
                    <div key={idx} className="create-option-row">
                      <input
                        type="text"
                        className="input input-short"
                        maxLength={OPTION_MAX}
                        value={opt}
                        aria-label={`Вариант ${idx + 1}`}
                        onChange={(e) => {
                          const next = [...options];
                          next[idx] = e.target.value.slice(0, OPTION_MAX);
                          update({ options: next });
                        }}
                      />
                      {options.length > OPTIONS_MIN && (
                        <button
                          type="button"
                          className="create-option-remove"
                          aria-label={`Удалить вариант ${idx + 1}`}
                          onClick={() => update({ options: options.filter((_, i) => i !== idx) })}
                        >
                          ×
                        </button>
                      )}
                    </div>
                  ))}
                </div>
                {options.length < OPTIONS_MAX && (
                  <button type="button" className="create-option-add" onClick={() => update({ options: [...options, ''] })}>
                    + Добавить вариант
                  </button>
                )}
                <button
                  type="button"
                  className="create-link"
                  onClick={() => {
                    update({ mode: 'binary' });
                    done();
                  }}
                >
                  Убрать варианты — рынок ДА / НЕТ
                </button>
              </div>
            )}

            {editing === 'criteria' && (
              <div className="field">
                <textarea
                  className="input"
                  rows={7}
                  value={draft.criteria}
                  aria-label={LABELS.criteria}
                  placeholder="ДА, если … НЕТ, если … Чем подтвержу итог: скриншот, фото, ссылка."
                  onChange={(e) => update({ criteria: e.target.value })}
                />
              </div>
            )}

            {editing === 'closes' && (
              <div className="create-two">
                <input
                  type="date"
                  className="input input-short"
                  aria-label="Дата конца приёма прогнозов"
                  value={draft.closeDate}
                  onChange={(e) => update({ closeDate: e.target.value })}
                />
                <input
                  type="time"
                  className="input input-short"
                  aria-label="Время конца приёма прогнозов"
                  value={draft.closeTime}
                  onChange={(e) => update({ closeTime: e.target.value })}
                />
              </div>
            )}

            {editing === 'resolves' && (
              <div className="create-two">
                <input
                  type="date"
                  className="input input-short"
                  aria-label="Дата итога"
                  value={draft.resultDate}
                  onChange={(e) => update({ resultDate: e.target.value })}
                />
                <input
                  type="time"
                  className="input input-short"
                  aria-label="Время итога"
                  value={draft.resultTime}
                  onChange={(e) => update({ resultTime: e.target.value })}
                />
              </div>
            )}

            {editing === 'category' && (
              <div role="group" aria-label={LABELS.category} className="create-chips">
                {categories.map((c) => (
                  <button
                    key={c.slug}
                    type="button"
                    className={`chip ${c.slug === draft.category ? 'is-active' : ''}`}
                    aria-pressed={c.slug === draft.category}
                    onClick={() => {
                      tg.HapticFeedback.selection();
                      update({ category: c.slug });
                      done();
                    }}
                  >
                    {c.label}
                  </button>
                ))}
              </div>
            )}

            {(editing === 'closes' || editing === 'resolves') && (
              <span className="sub field-hint">Дата и время по часовому поясу твоего устройства.</span>
            )}
            {problem(editing) && <span className="field-error">{problem(editing)}</span>}

            <button type="button" className="button-primary is-ink button-block" onClick={done}>
              Готово
            </button>
          </div>
        )}
      </BottomSheet>
    </>
  );
}

function PencilIcon() {
  return (
    <svg className="create-qrow-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M4 20h4L19 9l-4-4L4 16z" />
      <path d="M13.5 6.5l4 4" />
    </svg>
  );
}
