// Market creation wizard from docs/mockups/Create.dc.html (spec E3, three steps).
import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, ApiError, type Category } from '../api';
import { MainAction } from '../components/MainAction';
import { MarketCardView } from '../components/MarketCardView';
import { useToast } from '../components/Toast';
import {
  balanceAfterCreation,
  canProceed,
  clearDraft,
  createPayload,
  dateErrors,
  defaultDraft,
  errorField,
  loadDraft,
  previewCard,
  saveDraft,
  stepOfField,
  TITLE_MAX,
  type CreateDraft,
  type CreateField,
  type CreateStep,
} from '../create';
import { formatMoney } from '../format';
import { useSession } from '../session';
import { isTelegram, tg } from '../telegram';
import './create.css';

const EXAMPLES = ['Сдам ли я сессию без троек?', 'Будет ли дождь в Москве в субботу?', 'Отменят ли пары 31 октября?'];

const RULES = [
  'Ты не сможешь делать прогнозы на свой рынок',
  'Итог укажешь в боте и приложишь фото или скриншот',
  'У участников будет 24 часа, чтобы оспорить итог',
  'Половина комиссии со всех сделок — твоя',
];

const noop = () => {};

// Categories do not change while the app is open; avoid empty chips on return.
let cachedCategories: Category[] = [];

export function CreateScreen() {
  const navigate = useNavigate();
  const toast = useToast();
  const { me, balance, setBalance } = useSession();
  const [draft, setDraftState] = useState<CreateDraft>(() => loadDraft());
  const [categories, setCategories] = useState<Category[]>(cachedCategories);
  const [serverErrors, setServerErrors] = useState<Partial<Record<CreateField, string>>>({});
  const [busy, setBusy] = useState(false);
  const submitting = useRef(false);
  const mainRef = useRef<HTMLDivElement>(null);
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    api
      .categories()
      .then((list) => {
        cachedCategories = list;
        setCategories(list);
      })
      .catch(() => {});
  }, []);

  // Date checks depend on the current time ("at least an hour from now").
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 30_000);
    return () => window.clearInterval(id);
  }, []);

  const update = useCallback((patch: Partial<CreateDraft>) => {
    setDraftState((prev) => {
      const next = { ...prev, ...patch };
      saveDraft(next);
      return next;
    });
    setServerErrors((prev) => {
      const keys = Object.keys(patch);
      const cleared = { ...prev };
      if (keys.includes('title')) delete cleared.title;
      if (keys.includes('criteria')) delete cleared.criteria;
      if (keys.includes('category')) delete cleared.category;
      if (keys.some((k) => k === 'closeDate' || k === 'closeTime')) delete cleared.closes;
      if (keys.some((k) => k === 'resultDate' || k === 'closeDate' || k === 'closeTime')) delete cleared.resolves;
      return cleared;
    });
  }, []);

  const goTo = useCallback(
    (step: CreateStep) => {
      update({ step });
      mainRef.current?.scrollIntoView({ block: 'start' });
    },
    [update],
  );

  const step = draft.step;

  // On steps 2 and 3 Telegram's back button returns to the previous step.
  useEffect(() => {
    if (step === 1) return;
    tg.BackButton.show();
    const off = tg.BackButton.onClick(() => goTo((step - 1) as CreateStep));
    return () => {
      off();
      tg.BackButton.hide();
    };
  }, [step, goTo]);

  const cost = me ? Number(me.limits.creation_cost) : 0;
  const rest = balanceAfterCreation(balance, cost);
  const ready = canProceed(draft, step, now);
  const dates = dateErrors(draft, now);
  const fieldError = (field: CreateField) =>
    serverErrors[field] ?? (field === 'closes' || field === 'resolves' ? dates[field] : undefined);

  const submit = async () => {
    const payload = createPayload(draft);
    if (!payload || submitting.current) return;
    submitting.current = true;
    setBusy(true);
    try {
      const res = await api.createMarket(payload);
      setBalance(res.balance);
      tg.HapticFeedback.notification('success');
      clearDraft();
      toast('Рынок опубликован');
      navigate(`/market/${res.market.id}`);
    } catch (err) {
      tg.HapticFeedback.notification('error');
      const message = err instanceof ApiError ? err.message : 'Что-то пошло не так. Попробуйте ещё раз.';
      const field = err instanceof ApiError && err.status === 400 ? errorField(message) : null;
      if (field) {
        setServerErrors({ [field]: message });
        goTo(stepOfField(field));
      } else {
        toast(message, 'error');
      }
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  };

  const next = () => {
    if (!ready) return;
    tg.HapticFeedback.impact('light');
    if (step < 3) goTo((step + 1) as CreateStep);
    else void submit();
  };

  const close = () => {
    clearDraft();
    setDraftState(defaultDraft());
    setServerErrors({});
    navigate('/');
  };

  const labels = new Map(categories.map((c) => [c.slug, c.label]));
  const notEnough = step === 3 && !!me && rest === null;
  const nextLabel = step === 3 ? `Опубликовать за ${formatMoney(cost)}` : 'Далее';

  return (
    <div className="screen create" ref={mainRef}>
      <header className="create-header">
        <div className="create-topline">
          <button type="button" className="icon-button" aria-label="Закрыть" onClick={close}>
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
          <span className="create-heading">Новый рынок</span>
          <span className="create-step sub">{step} / 3</span>
        </div>
        <div className="create-progress" aria-hidden="true">
          {[1, 2, 3].map((n) => (
            <span key={n} className={step >= n ? 'is-done' : ''} />
          ))}
        </div>
      </header>

      <div className="create-body">
        {step === 1 && (
          <>
            <h1 className="screen-title">О чём спрашиваем?</h1>
            <div className="field">
              <div className="field-head">
                <label htmlFor="create-q" className="field-label">
                  Вопрос
                </label>
                <span className="sub field-counter">
                  {draft.title.length} / {TITLE_MAX}
                </span>
              </div>
              <textarea
                id="create-q"
                className="input"
                rows={3}
                maxLength={TITLE_MAX}
                value={draft.title}
                onChange={(e) => update({ title: e.target.value.slice(0, TITLE_MAX) })}
                placeholder="Например: Сдам ли я сессию без троек?"
                aria-invalid={!!fieldError('title') || undefined}
                aria-describedby="create-q-hint"
              />
              <span id="create-q-hint" className="sub field-hint">
                Вопрос должен иметь однозначный ответ ДА или НЕТ, дату и понятный способ проверить итог.
              </span>
              {fieldError('title') && <span className="field-error">{fieldError('title')}</span>}
            </div>
            <div className="field">
              <span className="field-label">Примеры</span>
              <div className="create-examples">
                {EXAMPLES.map((ex) => (
                  <button key={ex} type="button" className="example-button" onClick={() => update({ title: ex })}>
                    {ex}
                  </button>
                ))}
              </div>
            </div>
          </>
        )}

        {step === 2 && (
          <>
            <h1 className="screen-title">Сроки и правила итога</h1>
            <div className="create-two">
              <div className="field">
                <label htmlFor="create-close" className="field-label">
                  Приём прогнозов до
                </label>
                <input
                  id="create-close"
                  type="date"
                  className="input input-short"
                  value={draft.closeDate}
                  onChange={(e) => update({ closeDate: e.target.value })}
                  aria-invalid={!!fieldError('closes') || undefined}
                />
              </div>
              <div className="field">
                <label htmlFor="create-time" className="field-label">
                  Время
                </label>
                <input
                  id="create-time"
                  type="time"
                  className="input input-short"
                  value={draft.closeTime}
                  onChange={(e) => update({ closeTime: e.target.value })}
                  aria-invalid={!!fieldError('closes') || undefined}
                />
              </div>
            </div>
            {fieldError('closes') && <span className="field-error create-tight">{fieldError('closes')}</span>}
            <div className="field">
              <label htmlFor="create-result" className="field-label">
                Итог станет известен
              </label>
              <input
                id="create-result"
                type="date"
                className="input input-short"
                value={draft.resultDate}
                onChange={(e) => update({ resultDate: e.target.value })}
                aria-invalid={!!fieldError('resolves') || undefined}
              />
              {fieldError('resolves') && <span className="field-error">{fieldError('resolves')}</span>}
            </div>
            <div className="field">
              <label htmlFor="create-criteria" className="field-label">
                Как определяем результат
              </label>
              <textarea
                id="create-criteria"
                className="input"
                rows={5}
                value={draft.criteria}
                onChange={(e) => update({ criteria: e.target.value })}
                placeholder="ДА, если … НЕТ, если … Чем подтвержу итог: скриншот, фото, ссылка."
                aria-invalid={!!fieldError('criteria') || undefined}
              />
              {fieldError('criteria') && <span className="field-error">{fieldError('criteria')}</span>}
            </div>
            <div role="group" aria-label="Категория" className="field">
              <span className="field-label">Категория</span>
              <div className="create-chips">
                {categories.map((c) => (
                  <button
                    key={c.slug}
                    type="button"
                    className={`chip ${c.slug === draft.category ? 'is-active' : ''}`}
                    aria-pressed={c.slug === draft.category}
                    onClick={() => {
                      tg.HapticFeedback.selection();
                      update({ category: c.slug });
                    }}
                  >
                    {c.label}
                  </button>
                ))}
              </div>
              {fieldError('category') && <span className="field-error">{fieldError('category')}</span>}
            </div>
            <div className="hint-box create-note sub">
              <InfoIcon />
              <span>Если не укажешь итог в течение 72 часов после этой даты, рынок отменится и всем вернутся монеты.</span>
            </div>
            <div className="hint-box create-note sub">
              <InfoIcon />
              <span>
                Нельзя создавать рынки о здоровье и смерти конкретных людей, о насилии, о политике и о личной жизни других
                пользователей. Такие рынки скрываем по жалобам.
              </span>
            </div>
          </>
        )}

        {step === 3 && (
          <>
            <h1 className="screen-title">Проверь и публикуй</h1>
            <span className="field-label sub">Так рынок увидят в ленте</span>
            <div className="create-preview" ref={(el) => el?.setAttribute('inert', '')} aria-label="Предпросмотр карточки">
              <MarketCardView
                market={previewCard(draft)}
                categoryLabel={labels.get(draft.category) ?? ''}
                onTrade={noop}
                onStar={noop}
              />
            </div>
            <section aria-label="Стоимость" className="card create-cost">
              <div className="quote-row">
                <span className="sub">Создание рынка</span>
                <span className="quote-strong">{formatMoney(cost)}</span>
              </div>
              <div className="quote-row">
                <span className="sub">Баланс после</span>
                <span className={`quote-value ${rest === null ? 'quote-error' : ''}`}>
                  {rest === null ? 'не хватает' : formatMoney(rest)}
                </span>
              </div>
              <span className="sub create-cost-note">
                Эти {formatMoney(cost)} монет покрывают работу маркет-мейкера. Что не потратится, вернётся после итога.
              </span>
            </section>
            {notEnough && (
              <div className="hint-box create-warning" role="alert">
                <InfoIcon />
                <span>
                  Не хватает монет: на балансе {formatMoney(balance)}, а создание рынка стоит {formatMoney(cost)}.
                </span>
              </div>
            )}
            <ul className="create-rules">
              {RULES.map((r) => (
                <li key={r}>
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="var(--yes)" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M5 12.5l4.5 4.5L19 7.5" />
                  </svg>
                  <span>{r}</span>
                </li>
              ))}
            </ul>
          </>
        )}
      </div>

      <div className={`create-bar ${isTelegram() ? 'is-native' : ''}`}>
        {step > 1 && !isTelegram() && (
          <button type="button" className="create-back" onClick={() => goTo((step - 1) as CreateStep)}>
            Назад
          </button>
        )}
        <div className="create-next">
          <MainAction
            text={nextLabel}
            onClick={next}
            disabled={!ready || notEnough || !me}
            busy={busy}
            variant="ink"
          />
        </div>
      </div>
    </div>
  );
}

function InfoIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 8v5M12 16.5v.01" />
    </svg>
  );
}
