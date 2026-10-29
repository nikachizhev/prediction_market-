// Market creation wizard from docs/mockups/Create.dc.html (spec E3, three steps) with templates,
// the AI rules helper and similar markets from docs/mockups/quick-create/Main.dc.html (spec E9.1–E9.3),
// «Кто увидит» and the price by place for closed chats (spec E8.3, E5).
import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  api,
  ApiError,
  isAssistWarning,
  newRequestKey,
  type Category,
  type ChatSpace,
  type MarketCard,
  type MarketTemplate,
} from '../api';
import { BottomSheet } from '../components/BottomSheet';
import { MainAction } from '../components/MainAction';
import { MarketCardView } from '../components/MarketCardView';
import { Skeleton } from '../components/Skeleton';
import { useToast } from '../components/Toast';
import {
  acceptSuggestion,
  applyAssist,
  applyTemplate,
  ASSIST_DAILY_LIMIT,
  assistCard,
  assistForChat,
  audienceOptions,
  creationTerms,
  pickedChat,
  previewMetaSuffix,
  settleChat,
  assistLeftLabel,
  balanceAfterCreation,
  canProceed,
  changedFields,
  clearDraft,
  createPayload,
  dateErrors,
  defaultDraft,
  dismissSuggestion,
  editDraft,
  errorField,
  fillTemplate,
  loadDraft,
  previewCard,
  resolutionRule,
  resolvesAtIso,
  revertSuggestion,
  saveDraft,
  similarLine,
  stepOfField,
  templateCategories,
  templateFilled,
  templateMinDate,
  TITLE_MAX,
  TITLE_MIN,
  undoAssist,
  type AssistUi,
  type CreateDraft,
  type CreateField,
  type CreateStep,
  type DraftEdit,
  type FilledGroup,
  type FillSource,
} from '../create';
import { formatDayMonth, formatMoney } from '../format';
import { useSession } from '../session';
import { isTelegram, tg } from '../telegram';
import './create.css';

const OWN_RULE = 'Ты не сможешь делать прогнозы на свой рынок';
const LATER_RULES = ['У участников будет 24 часа, чтобы оспорить итог', 'Половина комиссии со всех сделок — твоя'];
/** Template category open at first, as in the mockup. */
const DEFAULT_TEMPLATE_CATEGORY = 'study';
const SIMILAR_MAX = 3;

const noop = () => {};

// Categories and templates do not change while the app is open; avoid an empty block on return.
let cachedCategories: Category[] = [];
let cachedTemplates: MarketTemplate[] = [];
// Similar markets of the last question, so coming back from one of them shows the list at once.
let cachedSimilar: { q: string; items: MarketCard[] } | null = null;
// Chats for «Кто увидит»; refreshed on every opening, shown at once on return.
let cachedChats: ChatSpace[] | null = null;

export function CreateScreen() {
  const navigate = useNavigate();
  const toast = useToast();
  const { me, balance, setBalance, patchMe } = useSession();
  const [params, setParams] = useSearchParams();
  // «Создать рынок для чата» opens the wizard as /create?chat=<code> with the chat picked.
  const [draft, setDraftState] = useState<CreateDraft>(() => {
    const loaded = loadDraft();
    const chat = params.get('chat');
    if (!chat || chat === loaded.chat) return loaded;
    const picked = { ...loaded, chat };
    saveDraft(picked);
    return picked;
  });
  const draftRef = useRef(draft);
  const [categories, setCategories] = useState<Category[]>(cachedCategories);
  const [templates, setTemplates] = useState<MarketTemplate[]>(cachedTemplates);
  const [chats, setChats] = useState<ChatSpace[] | null>(cachedChats);
  const [chatsAttempt, setChatsAttempt] = useState(0);
  const [chatsFailed, setChatsFailed] = useState(false);
  // The cached list may predate joining a chat by its link: settle the pick only on a fresh one.
  const [chatsFresh, setChatsFresh] = useState(false);
  const [tplCategory, setTplCategory] = useState('');
  const [tplOpen, setTplOpen] = useState<string | null>(null);
  const [tplValues, setTplValues] = useState<Record<string, string>>({});
  const [assistUi, setAssistUi] = useState<AssistUi>('idle');
  const [warning, setWarning] = useState('');
  // Bumped to drop the answer of a request that no longer fits (new question, closed wizard).
  const assistRequest = useRef(0);
  const [similar, setSimilar] = useState<MarketCard[]>(() =>
    cachedSimilar && cachedSimilar.q === draft.title.trim() ? cachedSimilar.items : [],
  );
  const [serverErrors, setServerErrors] = useState<Partial<Record<CreateField, string>>>({});
  const [busy, setBusy] = useState(false);
  // «Разреши боту писать тебе» before publishing, when the bot cannot reach the creator yet.
  const [askWrite, setAskWrite] = useState(false);
  const submitting = useRef(false);
  // One key per submission: a retry of the same payload after a lost response reuses it,
  // so the server returns the market already created instead of charging again.
  const attempt = useRef<{ key: string; payload: string } | null>(null);
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
    // Without templates the block is not shown; the wizard works as before.
    api
      .templates()
      .then((list) => {
        if (!Array.isArray(list)) return;
        cachedTemplates = list;
        setTemplates(list);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (params.has('chat')) setParams({}, { replace: true });
  }, [params, setParams]);

  useEffect(() => {
    let cancelled = false;
    setChatsFailed(false);
    api
      .chats()
      .then((res) => {
        if (cancelled) return;
        const list = Array.isArray(res?.items) ? res.items : [];
        cachedChats = list;
        setChats(list);
        setChatsFresh(true);
      })
      .catch(() => {
        if (!cancelled) setChatsFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [chatsAttempt]);

  useEffect(
    () => () => {
      assistRequest.current += 1;
    },
    [],
  );

  // Date checks depend on the current time ("at least an hour from now").
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 30_000);
    return () => window.clearInterval(id);
  }, []);

  /** Every change of the draft goes through here: saved, stale server errors cleared. */
  const commit = useCallback((next: CreateDraft) => {
    const prev = draftRef.current;
    if (next === prev) return;
    draftRef.current = next;
    saveDraft(next);
    setDraftState(next);
    const stale = changedFields(prev, next);
    if (stale.length > 0) {
      setServerErrors((errors) => {
        const kept = { ...errors };
        for (const field of stale) delete kept[field];
        return kept;
      });
    }
    if (prev.title !== next.title) {
      // The helper's answer or warning was about the old question.
      assistRequest.current += 1;
      setAssistUi((ui) => (ui === 'loading' || ui === 'warn' || ui === 'off' ? 'idle' : ui));
    }
  }, []);

  // A chat that is no longer available (left, frozen) falls back to everyone.
  useEffect(() => {
    if (chats && chatsFresh) commit(settleChat(draftRef.current, chats));
  }, [chats, chatsFresh, commit]);

  /** A change made by the creator: removes the «ИИ» / «Шаблон» mark of the field. */
  const update = useCallback((patch: DraftEdit) => commit(editDraft(draftRef.current, patch)), [commit]);

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

  // Similar markets on the review step (spec E9.3); an error or nothing similar — no block.
  const similarQuery = step === 3 ? draft.title.trim() : '';
  useEffect(() => {
    if (similarQuery.length < TITLE_MIN) {
      setSimilar([]);
      return;
    }
    if (cachedSimilar?.q === similarQuery) {
      setSimilar(cachedSimilar.items);
      return;
    }
    let cancelled = false;
    setSimilar([]);
    api
      .similarMarkets(similarQuery)
      .then((res) => {
        const items = Array.isArray(res?.items) ? res.items.slice(0, SIMILAR_MAX) : [];
        cachedSimilar = { q: similarQuery, items };
        if (!cancelled) setSimilar(items);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [similarQuery]);

  const listed = !!draft.chat && !!chats?.some((c) => c.code === draft.chat);
  const chat = pickedChat(draft, chatsFresh || listed ? chats : null);
  const audience = audienceOptions(chats);
  const assist = assistForChat(me?.assist, chat);
  const card = assistCard(draft, assistUi, assist);
  const loading = step === 2 && card === 'loading';
  // Price and depth by place (spec E5); unknown until the chat list arrives.
  const terms = me ? creationTerms(me.limits, chat) : null;
  const cost = terms ? terms.cost : 0;
  const rest = balanceAfterCreation(balance, cost);
  const ready = canProceed(draft, step, now) && !loading;
  const dates = dateErrors(draft, now);
  const fieldError = (field: CreateField) =>
    serverErrors[field] ?? (field === 'closes' || field === 'resolves' ? dates[field] : undefined);

  const setLeft = (left: unknown) => {
    if (typeof left !== 'number' || !Number.isFinite(left)) return;
    patchMe((prev) => (prev.assist ? { ...prev, assist: { ...prev.assist, left: Math.max(0, left) } } : prev));
  };

  const ask = async () => {
    if (!assist?.enabled) return;
    if (assist.left <= 0) {
      setAssistUi('limit');
      return;
    }
    tg.HapticFeedback.impact('light');
    const request = ++assistRequest.current;
    setAssistUi('loading');
    const current = draftRef.current;
    try {
      const res = await api.assist(current.title.trim(), current.category || undefined, current.chat ?? undefined);
      setLeft(res.left);
      if (request !== assistRequest.current) return;
      if (isAssistWarning(res)) {
        setWarning(res.text);
        setAssistUi('warn');
        tg.HapticFeedback.notification('warning');
        return;
      }
      commit(applyAssist(draftRef.current, res));
      setAssistUi('idle');
      tg.HapticFeedback.notification('success');
    } catch (err) {
      if (request !== assistRequest.current) return;
      if (err instanceof ApiError && err.status === 429) {
        setLeft(0);
        setAssistUi('limit');
      } else {
        // 503 (off, no answer in 10 s), no connection or anything else: the wizard works without it.
        setAssistUi('off');
      }
    }
  };

  const submit = async () => {
    const payload = createPayload(draft);
    if (!payload || submitting.current) return;
    submitting.current = true;
    setBusy(true);
    const signature = JSON.stringify(payload);
    if (attempt.current?.payload !== signature) attempt.current = { key: newRequestKey(), payload: signature };
    try {
      const res = await api.createMarket(payload, attempt.current.key);
      attempt.current = null;
      setBalance(res.balance);
      tg.HapticFeedback.notification('success');
      if (typeof res.bot_notified === 'boolean') tg.setBotCanWrite(res.bot_notified);
      clearDraft();
      // Replace the wizard: Back from «Рынок опубликован» must not reopen it.
      navigate(`/market/${res.market.id}/created`, {
        replace: true,
        state: { market: res.market, botNotified: res.bot_notified ?? null },
      });
    } catch (err) {
      tg.HapticFeedback.notification('error');
      // A 4xx refusal created nothing; after a lost connection or a 5xx the retry keeps the key.
      if (err instanceof ApiError && err.status >= 400 && err.status < 500) attempt.current = null;
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
    else if (tg.canRequestWriteAccess && !tg.botCanWrite) setAskWrite(true);
    else void submit();
  };

  const allowAndPublish = async () => {
    await tg.requestWriteAccess();
    setAskWrite(false);
    void submit();
  };

  const publishQuietly = () => {
    setAskWrite(false);
    void submit();
  };

  const close = () => {
    clearDraft();
    const fresh = defaultDraft();
    draftRef.current = fresh;
    setDraftState(fresh);
    setServerErrors({});
    assistRequest.current += 1;
    setAssistUi('idle');
    setTplOpen(null);
    setTplValues({});
    navigate('/');
  };

  const labels = new Map(categories.map((c) => [c.slug, c.label]));
  const notEnough = step === 3 && !!terms && rest === null;
  const nextLabel = step === 3 ? (terms ? `Опубликовать за ${formatMoney(cost)}` : 'Опубликовать') : 'Далее';
  const resolves = resolvesAtIso(draft);
  const resolutionDay = resolves ? `${formatDayMonth(resolves, now)} в ${draft.resultTime}` : 'В дату итога';

  // Templates on step 1.
  const tplCategories = templateCategories(templates);
  const activeTplCategory = tplCategories.includes(tplCategory)
    ? tplCategory
    : tplCategories.includes(DEFAULT_TEMPLATE_CATEGORY)
      ? DEFAULT_TEMPLATE_CATEGORY
      : (tplCategories[0] ?? '');
  const openTemplate = templates.find((t) => t.id === tplOpen) ?? null;
  const tplReady = openTemplate ? templateFilled(openTemplate, tplValues) : false;

  const pickTemplate = (id: string | null) => {
    setTplOpen(id);
    setTplValues({});
  };

  const putTemplate = () => {
    if (!openTemplate || !tplReady) return;
    tg.HapticFeedback.impact('light');
    commit(applyTemplate(draftRef.current, openTemplate, tplValues, now));
    pickTemplate(null);
  };

  const mark = (group: FilledGroup) =>
    draft.filledBy && draft.marks.includes(group) ? <FillMark by={draft.filledBy} /> : null;

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
                {draft.templateId
                  ? 'Сроки и правила подставятся из шаблона — проверишь их на следующем шаге.'
                  : 'Вопрос должен иметь однозначный ответ ДА или НЕТ, дату и понятный способ проверить итог.'}
                {' '}От {TITLE_MIN} до {TITLE_MAX} символов без пробелов по краям.
              </span>
              {fieldError('title') && <span className="field-error">{fieldError('title')}</span>}
            </div>

            {templates.length > 0 && (
              <section aria-label="Шаблоны" className="create-templates">
                <div className="create-section-head">
                  <span className="field-label">Шаблоны</span>
                  <span className="sub field-hint">Правила и сроки уже готовы — останется вписать детали</span>
                </div>
                <div role="group" aria-label="Категория шаблонов" className="create-tpl-categories">
                  {tplCategories.map((slug) => (
                    <button
                      key={slug}
                      type="button"
                      className={`chip create-tpl-chip ${slug === activeTplCategory ? 'is-active' : ''}`}
                      aria-pressed={slug === activeTplCategory}
                      onClick={() => {
                        tg.HapticFeedback.selection();
                        setTplCategory(slug);
                        pickTemplate(null);
                      }}
                    >
                      {labels.get(slug) ?? slug}
                    </button>
                  ))}
                </div>
                {openTemplate ? (
                  <div className="card create-tpl-panel">
                    <div className="create-tpl-top">
                      <span className="sub create-tpl-category">
                        Шаблон · {labels.get(openTemplate.category) ?? openTemplate.category}
                      </span>
                      <button
                        type="button"
                        className="icon-button create-tpl-close"
                        aria-label="Закрыть шаблон"
                        onClick={() => pickTemplate(null)}
                      >
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
                          <path d="M6 6l12 12M18 6L6 18" />
                        </svg>
                      </button>
                    </div>
                    <p className="create-tpl-preview">
                      {fillTemplate(openTemplate.text, openTemplate.blanks, tplValues, now)}
                    </p>
                    {openTemplate.blanks.map((b) => (
                      <div key={b.key} className="field create-tpl-field">
                        <label htmlFor={`tpl-${b.key}`} className="field-label">
                          {b.label}
                        </label>
                        <input
                          id={`tpl-${b.key}`}
                          type={b.type}
                          className="input input-short create-tpl-input"
                          value={tplValues[b.key] ?? ''}
                          placeholder={b.placeholder}
                          min={b.type === 'date' ? templateMinDate(now) : b.type === 'number' ? 0 : undefined}
                          inputMode={b.type === 'number' ? 'numeric' : undefined}
                          onChange={(e) => {
                            const value = e.target.value;
                            setTplValues((prev) => ({ ...prev, [b.key]: value }));
                          }}
                        />
                      </div>
                    ))}
                    <button
                      type="button"
                      className="button-primary is-ink create-tpl-apply"
                      disabled={!tplReady}
                      onClick={putTemplate}
                    >
                      Подставить в вопрос
                    </button>
                  </div>
                ) : (
                  <div className="create-tpl-list">
                    {templates
                      .filter((t) => t.category === activeTplCategory)
                      .map((t) => (
                        <button key={t.id} type="button" className="create-tpl-item" onClick={() => pickTemplate(t.id)}>
                          <span>{fillTemplate(t.text, t.blanks, {}, now)}</span>
                          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                            <path d="M9 6l6 6-6 6" />
                          </svg>
                        </button>
                      ))}
                  </div>
                )}
              </section>
            )}

            {audience.length > 0 && (
              <div role="group" aria-label="Кто увидит рынок" className="create-audience">
                <span className="field-label">Кто увидит</span>
                {audience.map((option) => {
                  const on = option.chat === (chat ? chat.code : null);
                  return (
                    <button
                      key={option.chat ?? 'all'}
                      type="button"
                      className={`create-audience-option ${on ? 'is-on' : ''}`}
                      aria-pressed={on}
                      onClick={() => {
                        if (on) return;
                        tg.HapticFeedback.selection();
                        update({ chat: option.chat });
                      }}
                    >
                      <span className="create-audience-radio" aria-hidden="true">
                        <span />
                      </span>
                      <span className="create-audience-text">
                        <span className="create-audience-label">{option.label}</span>
                        <span className="sub create-audience-sub">{option.sub}</span>
                      </span>
                    </button>
                  );
                })}
              </div>
            )}
          </>
        )}

        {step === 2 && (
          <>
            <h1 className="screen-title">Сроки и правила итога</h1>

            {card === 'idle' && (
              <section aria-label="ИИ-помощник" className="create-assist is-outlined">
                <div className="create-assist-row">
                  <span className="create-assist-badge">
                    <SparkIcon size={20} />
                  </span>
                  <div className="create-assist-text">
                    <span className="create-assist-name">ИИ-помощник</span>
                    <span className="sub create-assist-note">
                      Предложит, как определить итог, сроки и категорию по твоему вопросу. Ты всё проверишь и поправишь.
                    </span>
                  </div>
                </div>
                <button type="button" className="create-assist-ask" onClick={() => void ask()}>
                  <SparkIcon size={18} single />
                  Помочь с правилами
                </button>
                <span className="sub create-assist-small">Помощнику уходит только текст вопроса — без имени и Telegram ID.</span>
              </section>
            )}

            {card === 'loading' && (
              <section role="status" className="create-assist is-outlined is-row">
                <span className="create-assist-badge">
                  <SparkIcon size={20} />
                </span>
                <div className="create-assist-text">
                  <span className="create-assist-name">Помощник пишет правила…</span>
                  <span className="sub create-assist-note">Обычно 2–3 секунды. Поля ниже заполнятся сами.</span>
                </div>
              </section>
            )}

            {card === 'done' && (
              <section role="status" className="create-assist is-done">
                <div className="create-assist-row is-tight">
                  <span className="create-assist-icon">
                    <SparkIcon size={20} />
                  </span>
                  <div className="create-assist-text">
                    <span className="create-assist-title">Помощник заполнил сроки, правила и категорию</span>
                    <span className="sub create-assist-note">Проверь каждое поле: за итог отвечаешь ты, а не ИИ.</span>
                  </div>
                </div>
                <div className="create-assist-foot">
                  <button
                    type="button"
                    className="create-link"
                    onClick={() => {
                      tg.HapticFeedback.impact('light');
                      commit(undoAssist(draftRef.current));
                    }}
                  >
                    Вернуть как было
                  </button>
                  <span className="sub create-assist-small">{assistLeftLabel(assist?.left ?? 0)}</span>
                </div>
              </section>
            )}

            {card === 'done' && draft.suggestion?.state === 'open' && (
              <section aria-label="Уточнить вопрос" className="card create-better">
                <span className="field-label">Точнее так</span>
                <p className="create-better-question">{draft.suggestion.question}</p>
                {draft.suggestion.why && <span className="sub create-assist-note">{draft.suggestion.why}</span>}
                <div className="create-pair">
                  <button type="button" className="create-button is-ink" onClick={() => commit(acceptSuggestion(draftRef.current))}>
                    Заменить
                  </button>
                  <button type="button" className="create-button is-outline" onClick={() => commit(dismissSuggestion(draftRef.current))}>
                    Оставить мой
                  </button>
                </div>
              </section>
            )}

            {card === 'done' && draft.suggestion?.state === 'accepted' && (
              <div className="create-accepted sub">
                <span>Вопрос уточнён</span>
                <button type="button" className="create-link" onClick={() => commit(revertSuggestion(draftRef.current))}>
                  Вернуть мой
                </button>
              </div>
            )}

            {card === 'template' && (
              <section role="status" className="create-assist is-row is-plain">
                <svg className="create-assist-icon sub" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" aria-hidden="true">
                  <rect x="4" y="4" width="16" height="16" rx="3" />
                  <path d="M4 10h16M10 10v10" />
                </svg>
                <div className="create-assist-text">
                  <span className="create-assist-title">Сроки и правила — из шаблона</span>
                  <span className="sub create-assist-note">Проверь их: все поля можно менять.</span>
                </div>
              </section>
            )}

            {card === 'warn' && (
              <section role="alert" className="create-assist is-warn">
                <div className="create-assist-row is-tight">
                  <svg className="create-assist-icon create-warn-icon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M12 4l9 16H3z" />
                    <path d="M12 10v4M12 17v.01" />
                  </svg>
                  <div className="create-assist-text is-loose">
                    <span className="create-assist-title">{warning}</span>
                    <span className="create-assist-note">
                      Такие рынки запрещены и скрываются по жалобам. Спроси о событии, а не о человеке, — например,
                      «Пройдёт ли экзамен по расписанию?»
                    </span>
                  </div>
                </div>
                <div className="create-pair">
                  <button
                    type="button"
                    className="create-button is-ink"
                    onClick={() => {
                      setAssistUi('idle');
                      goTo(1);
                    }}
                  >
                    Изменить вопрос
                  </button>
                  <button type="button" className="create-button is-outline-ink" onClick={() => setAssistUi('manual')}>
                    Заполню сам
                  </button>
                </div>
              </section>
            )}

            {card === 'off' && (
              <section role="status" className="create-assist">
                <div className="create-assist-text">
                  <span className="create-assist-title">Помощник сейчас не отвечает</span>
                  <span className="sub create-assist-note">Заполни правила сам — мастер работает и без ИИ.</span>
                </div>
                <button type="button" className="create-button is-outline" onClick={() => void ask()}>
                  Попробовать ещё раз
                </button>
              </section>
            )}

            {card === 'limit' && (
              <section role="status" className="create-assist is-compact">
                <span className="create-assist-title">Подсказки на сегодня закончились</span>
                <span className="sub create-assist-note">
                  Их {ASSIST_DAILY_LIMIT} в день. Шаблоны на первом шаге работают без ИИ.
                </span>
              </section>
            )}

            {loading ? (
              <FieldsSkeleton />
            ) : (
              <>
                <div className="create-two">
                  <div className="field">
                    <div className="create-label-row">
                      <label htmlFor="create-close" className="field-label">
                        Приём прогнозов до
                      </label>
                    </div>
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
                    <div className="create-label-row">
                      <label htmlFor="create-time" className="field-label">
                        Время
                      </label>
                      {mark('dates')}
                    </div>
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
                  <div className="create-label-row">
                    <label htmlFor="create-result" className="field-label">
                      Итог станет известен
                    </label>
                    {mark('dates')}
                  </div>
                  <input
                    id="create-result"
                    type="date"
                    className="input input-short"
                    value={draft.resultDate}
                    onChange={(e) => update({ resultDate: e.target.value })}
                    aria-invalid={!!fieldError('resolves') || undefined}
                  />
                  <label htmlFor="create-result-time" className="field-label">
                    Время итога
                  </label>
                  <input
                    id="create-result-time"
                    type="time"
                    className="input input-short"
                    value={draft.resultTime}
                    onChange={(e) => update({ resultTime: e.target.value })}
                    aria-invalid={!!fieldError('resolves') || undefined}
                  />
                  <span className="sub field-hint">Дата и время по часовому поясу твоего устройства.</span>
                  {fieldError('resolves') && <span className="field-error">{fieldError('resolves')}</span>}
                </div>
                <div className="field">
                  <div className="create-label-row">
                    <label htmlFor="create-criteria" className="field-label">
                      Как определяем результат
                    </label>
                    {mark('criteria')}
                  </div>
                  <textarea
                    id="create-criteria"
                    className="input"
                    rows={card === 'none' ? 5 : 7}
                    value={draft.criteria}
                    onChange={(e) => update({ criteria: e.target.value })}
                    placeholder="ДА, если … НЕТ, если … Чем подтвержу итог: скриншот, фото, ссылка."
                    aria-invalid={!!fieldError('criteria') || undefined}
                  />
                  {fieldError('criteria') && <span className="field-error">{fieldError('criteria')}</span>}
                </div>
                <div role="group" aria-label="Категория" className="field">
                  <div className="create-label-row">
                    <span className="field-label">Категория</span>
                    {mark('category')}
                  </div>
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
              </>
            )}
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
            {similar.length > 0 && (
              <section aria-label="Похожие рынки" className="create-similar">
                <div className="create-section-head">
                  <span className="create-similar-title">Похожие рынки уже есть</span>
                  <span className="sub field-hint">Можно сделать прогноз там — или всё равно создать свой.</span>
                </div>
                {similar.map((m) => (
                  <article key={m.id} className="create-similar-card">
                    <div className="create-similar-text">
                      <span className="create-similar-question">{m.title}</span>
                      <span className="sub create-similar-line">{similarLine(m)}</span>
                    </div>
                    <button type="button" className="create-button is-outline create-similar-open" onClick={() => navigate(`/market/${m.id}`)}>
                      Открыть
                    </button>
                  </article>
                ))}
              </section>
            )}
            <span className="field-label sub">Так рынок увидят в ленте</span>
            <div className="create-preview" ref={(el) => el?.setAttribute('inert', '')} aria-label="Предпросмотр карточки">
              <MarketCardView
                market={previewCard(draft)}
                categoryLabel={labels.get(draft.category) ?? ''}
                metaSuffix={previewMetaSuffix(chat)}
                onTrade={noop}
                onStar={noop}
              />
            </div>
            <section aria-label="Стоимость" className="card create-cost">
              <div className="quote-row">
                <span className="sub">Создание рынка</span>
                <span className="quote-strong">{terms ? formatMoney(cost) : '—'}</span>
              </div>
              <div className="quote-row">
                <span className="sub">Баланс после</span>
                <span className={`quote-value ${terms && rest === null ? 'quote-error' : ''}`}>
                  {!terms ? '—' : rest === null ? 'не хватает' : formatMoney(rest)}
                </span>
              </div>
              {terms && (
                <span className="sub create-cost-note">
                  Эти {formatMoney(cost)} монет покрывают работу маркет-мейкера. Что не потратится, вернётся после итога.
                  {chat && ` Ставка в ${formatMoney(cost)} монет сдвинет вероятность с 50% до 75%: чем дешевле рынок, тем легче он двигается.`}
                </span>
              )}
            </section>
            {!terms && me && (
              <div className="hint-box create-warning" role="alert">
                <InfoIcon />
                <span>
                  {chatsFailed ? 'Не удалось загрузить чат, поэтому цена создания пока неизвестна. ' : 'Загружаем цену создания для чата… '}
                  {chatsFailed && (
                    <button type="button" className="create-link" onClick={() => setChatsAttempt((n) => n + 1)}>
                      Повторить
                    </button>
                  )}
                </span>
              </div>
            )}
            {notEnough && (
              <div className="hint-box create-warning" role="alert">
                <InfoIcon />
                <span>
                  Не хватает монет: на балансе {formatMoney(balance)}, а создание рынка стоит {formatMoney(cost)}.
                </span>
              </div>
            )}
            <ul className="create-rules">
              {[OWN_RULE, resolutionRule(draft, now), ...LATER_RULES].map((r) => (
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
          {/* Hidden while the sheet is open: Telegram's MainButton would sit under it. */}
          {!askWrite && (
            <MainAction
              text={nextLabel}
              onClick={next}
              disabled={!ready || notEnough || !me || !terms}
              busy={busy}
              variant="ink"
            />
          )}
        </div>
      </div>

      <BottomSheet open={askWrite} onClose={() => setAskWrite(false)} label="Разреши боту писать тебе">
        <div className="create-ask">
          <span className="create-ask-icon" aria-hidden="true">
            <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <path d="M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15z" />
              <path d="M10 20.5a2 2 0 0 0 4 0" />
            </svg>
          </span>
          <h2 className="create-ask-title">Разреши боту писать тебе</h2>
          <p className="create-ask-text sub">
            {resolutionDay} бот спросит, чем закончился рынок. Если итог не указать за 72 часа, рынок отменится и всем
            вернутся монеты.
          </p>
          <button type="button" className="button-primary is-ink button-block" onClick={allowAndPublish} disabled={busy}>
            Разрешить и опубликовать
          </button>
          <button type="button" className="create-ask-skip" onClick={publishQuietly} disabled={busy}>
            Опубликовать без напоминаний
          </button>
        </div>
      </BottomSheet>
    </div>
  );
}

/** «ИИ» or «Шаблон» next to a field the helper or a template filled. */
function FillMark({ by }: { by: FillSource }) {
  return <span className={`create-mark ${by === 'ai' ? 'is-ai' : ''}`}>{by === 'ai' ? 'ИИ' : 'Шаблон'}</span>;
}

/** Step 2 fields while the helper writes the rules. */
function FieldsSkeleton() {
  return (
    <div className="create-skeleton" aria-hidden="true">
      <div className="create-two">
        <div className="field">
          <span className="field-label">Приём прогнозов до</span>
          <Skeleton height={48} radius={12} />
        </div>
        <div className="field">
          <span className="field-label">Время</span>
          <Skeleton height={48} radius={12} />
        </div>
      </div>
      <div className="field">
        <span className="field-label">Итог станет известен</span>
        <Skeleton height={48} radius={12} />
        <span className="field-label">Время итога</span>
        <Skeleton height={48} radius={12} />
      </div>
      <div className="field">
        <span className="field-label">Как определяем результат</span>
        <div className="create-skeleton-box">
          <Skeleton width="92%" height={12} radius={6} />
          <Skeleton width="78%" height={12} radius={6} />
          <Skeleton width="86%" height={12} radius={6} />
          <Skeleton width="54%" height={12} radius={6} />
        </div>
      </div>
      <div className="field">
        <span className="field-label">Категория</span>
        <div className="create-chips">
          <Skeleton width={72} height={40} radius={999} />
          <Skeleton width={84} height={40} radius={999} />
          <Skeleton width={96} height={40} radius={999} />
        </div>
      </div>
    </div>
  );
}

function SparkIcon({ size, single = false }: { size: number; single?: boolean }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={single ? 2 : 1.8} strokeLinejoin="round" aria-hidden="true">
      <path d="M11 3.5l1.9 5.1 5.1 1.9-5.1 1.9L11 17.5l-1.9-5.1L4 10.5l5.1-1.9z" />
      {!single && <path d="M18.5 15l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8z" />}
    </svg>
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
