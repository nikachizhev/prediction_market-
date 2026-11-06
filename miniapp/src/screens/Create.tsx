// «Создать»: «О чём спорим?» and the ready market from the helper (spec E9.5, phase 3d — no mockup),
// a chat draft opened by startapp=draft_<code> (spec E9.4), and the market creation wizard from
// docs/mockups/Create.dc.html (spec E3, three steps) with templates, the AI rules helper and similar
// markets from docs/mockups/quick-create/Main.dc.html (spec E9.1–E9.3), «Кто увидит», the price by
// place and «Объявить в чате» for closed chats (spec E8.3, E8.4, E5).
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
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
import {
  chatDraftTarget,
  draftCreateFailure,
  lineAnswer,
  lineFailure,
  lineFromDraft,
  lineReady,
  lineUsesAssist,
  quickProblems,
  wizardEvent,
  wizardFromLine,
  type LineOutcome,
  type QuickField,
  type WizardReason,
} from '../oneline';
import { QuickFields } from './QuickFields';
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
  withChat,
  creationTerms,
  expectedCost,
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
  OPTION_MAX,
  OPTIONS_MAX,
  OPTIONS_MIN,
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

const EXAMPLES_MULTIPLE: { title: string; options: string[] }[] = [
  {
    title: 'Какая будет завтра погода?',
    options: ['солнечная', 'дождь', 'ветер'],
  },
  {
    title: 'Сколько процентов хорошо сдаст сессию?',
    options: ['меньше 50%', '50–70%', 'больше 70%'],
  },
];
const OWN_RULE = 'Ты не сможешь делать прогнозы на свой рынок';
const DRAFT_MISSING = 'Черновик не найден: он живёт 24 часа и открывается только у того, кто вызвал /market. Можно создать рынок здесь.';
const DRAFT_GONE = 'Черновик не найден: он живёт 24 часа. Рынок можно создать и без него — нажми ещё раз.';
const DRAFT_NOT_PUBLISHED = 'Рынок по этому черновику ещё не опубликован. Статус — в разделе «Мои».';
const CHAT_FROZEN = 'Бот больше не администратор в этом чате, поэтому новые рынки создать нельзя.';
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
  // «Создать рынок для чата» opens «Создать» as /create?chat=<code> with the chat picked.
  const [draft, setDraftState] = useState<CreateDraft>(() => {
    const loaded = loadDraft();
    const chat = params.get('chat');
    if (!chat || chat === loaded.chat) return loaded;
    // The helper's ready market was made for another place: start over from its line.
    const picked: CreateDraft =
      loaded.flow === 'quick' ? { ...defaultDraft(), line: loaded.line, chat } : { ...loaded, chat, draftCode: null };
    saveDraft(picked);
    return picked;
  });
  // startapp=draft_<code> opens /create?draft=<code>: the chat draft is loaded first.
  const [draftLoad, setDraftLoad] = useState<string | null>(() => params.get('draft'));
  // The chat of the opened draft, known even if GET /api/chats answered before the membership appeared.
  const [draftChat, setDraftChat] = useState<ChatSpace | null>(null);
  const [lineBusy, setLineBusy] = useState(false);
  const [editing, setEditing] = useState<QuickField | null>(null);
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
    if (params.has('chat') || params.has('draft')) setParams({}, { replace: true });
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

  const chatList = useMemo(() => withChat(chats, draftChat), [chats, draftChat]);

  // A chat that is no longer available (left, frozen) falls back to everyone.
  useEffect(() => {
    if (chatList && chatsFresh) commit(settleChat(draftRef.current, chatList));
  }, [chatList, chatsFresh, commit]);

  /** A change made by the creator: removes the «ИИ» / «Шаблон» mark of the field. */
  const update = useCallback((patch: DraftEdit) => commit(editDraft(draftRef.current, patch)), [commit]);

  const goTo = useCallback(
    (step: CreateStep) => {
      update({ step });
      mainRef.current?.scrollIntoView({ block: 'start' });
    },
    [update],
  );

  /** A whole new draft (a helper's answer, a chat draft, starting over): nothing of the old one is kept. */
  const replaceDraft = useCallback(
    (next: CreateDraft) => {
      commit(next);
      setServerErrors({});
      setEditing(null);
      mainRef.current?.scrollIntoView({ block: 'start' });
    },
    [commit],
  );

  const step = draft.step;
  const flow = draft.flow;
  const wizard = flow === 'wizard';
  const quick = flow === 'quick';

  /** Back to «О чём спорим?» with its text. */
  const backToLine = useCallback(() => {
    assistRequest.current += 1;
    setAssistUi('idle');
    replaceDraft(lineFromDraft(draftRef.current));
  }, [replaceDraft]);

  // Telegram's back button: steps 2 and 3 return to the previous step, step 1 and the ready market to «О чём спорим?».
  useEffect(() => {
    if (flow === 'line') return;
    tg.BackButton.show();
    const off = tg.BackButton.onClick(() => (wizard && step > 1 ? goTo((step - 1) as CreateStep) : backToLine()));
    return () => {
      off();
      tg.BackButton.hide();
    };
  }, [flow, wizard, step, goTo, backToLine]);

  // The chat draft of startapp=draft_<code>: its market if it is made already, otherwise its preview.
  useEffect(() => {
    if (!draftLoad) return;
    let cancelled = false;
    const open = async () => {
      const view = await api.chatDraft(draftLoad);
      if (cancelled) return;
      const target = chatDraftTarget(view);
      if ('market' in target) {
        if (draftRef.current.draftCode === draftLoad) clearDraft();
        // Until premoderation approves it the market itself answers 404; its status is in «Мои».
        const unpublished = await api
          .publicationDrafts()
          .then((res) => res.items.some((item) => item.id === target.market))
          .catch(() => false);
        if (cancelled) return;
        if (unpublished) toast(DRAFT_NOT_PUBLISHED);
        navigate(unpublished ? '/mine' : `/market/${target.market}`, { replace: true });
        return;
      }
      // The price and the helper depend on the chat. GET /api/drafts has just checked the membership.
      const space = await api.chat(view.chat.code);
      if (cancelled) return;
      if (space.frozen) {
        toast(CHAT_FROZEN, 'error');
        replaceDraft(defaultDraft());
        setDraftLoad(null);
        return;
      }
      assistRequest.current += 1;
      setAssistUi('idle');
      setDraftChat(space);
      replaceDraft(target.draft);
      setDraftLoad(null);
    };
    open().catch((err: unknown) => {
      if (cancelled) return;
      if (err instanceof ApiError && err.status === 404) {
        toast(DRAFT_MISSING, 'error');
        replaceDraft(defaultDraft());
      } else {
        toast(err instanceof ApiError ? err.message : 'Что-то пошло не так. Попробуйте ещё раз.', 'error');
      }
      setDraftLoad(null);
    });
    return () => {
      cancelled = true;
    };
  }, [draftLoad, navigate, replaceDraft, toast]);

  // Similar markets on the review step and the ready market (spec E9.3); an error or nothing similar — no block.
  const reviewing = (wizard && step === 3) || quick;
  const similarQuery = reviewing ? draft.title.trim() : '';
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

  const listed = !!draft.chat && !!chatList?.some((c) => c.code === draft.chat);
  const chat = pickedChat(draft, chatsFresh || listed ? chatList : null);
  const audience = audienceOptions(chatList);
  const assist = assistForChat(me?.assist, chat);
  const card = assistCard(draft, assistUi, assist);
  const loading = wizard && step === 2 && card === 'loading';
  // Price and depth by place (spec E5); unknown until the chat list arrives.
  const terms = me ? creationTerms(me.limits, chat) : null;
  const cost = terms ? terms.cost : 0;
  const rest = balanceAfterCreation(balance, cost);
  const ready = canProceed(draft, quick ? 3 : step, now) && !loading;
  const dates = dateErrors(draft, now);
  const fieldError = (field: CreateField) =>
    serverErrors[field] ?? (field === 'closes' || field === 'resolves' ? dates[field] : undefined);
  const problems = quick ? quickProblems(draft, now) : {};
  const quickProblem = (field: QuickField) => serverErrors[field] ?? problems[field];

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

  const toWizard = (next: CreateDraft, reason: WizardReason) => {
    replaceDraft(next);
    const event = wizardEvent(reason);
    api.event(event.name, event.props).catch(() => {});
  };

  /** «Заполнить самому»: the wizard with templates, the line in the question field. */
  const fillMyself = () => {
    tg.HapticFeedback.impact('light');
    assistRequest.current += 1;
    setLineBusy(false);
    setAssistUi('idle');
    toWizard(wizardFromLine(draftRef.current), 'manual');
  };

  const applyLine = (outcome: LineOutcome) => {
    if (outcome.kind === 'quick') {
      replaceDraft(outcome.draft);
      tg.HapticFeedback.notification('success');
      return;
    }
    toWizard(outcome.draft, 'unavailable');
    // After replaceDraft: a new title resets the helper card, this sets what happened.
    setAssistUi(outcome.ui);
    if (outcome.ui === 'warn') {
      setWarning(outcome.warning ?? '');
      tg.HapticFeedback.notification('warning');
    } else if (outcome.ui === 'limit') {
      toast('Подсказки на сегодня закончились — заполни рынок сам.');
    } else if (outcome.ui === 'off') {
      toast('Помощник сейчас не отвечает — заполни рынок сам.');
    }
  };

  /** «Сделать рынок»: the helper makes the whole market; off, no answer or no suggestions left — the wizard. */
  const makeMarket = async () => {
    const current = draftRef.current;
    if (!lineReady(current.line) || lineBusy) return;
    tg.HapticFeedback.impact('light');
    if (!lineUsesAssist(assist)) {
      applyLine({ kind: 'wizard', draft: wizardFromLine(current), ui: assist?.enabled ? 'limit' : 'idle', warning: null });
      return;
    }
    const request = ++assistRequest.current;
    setLineBusy(true);
    let outcome: LineOutcome;
    try {
      const res = await api.assist(current.line.trim(), undefined, current.chat ?? undefined, 'oneline');
      setLeft(res.left);
      if (request !== assistRequest.current) return;
      outcome = lineAnswer(draftRef.current, res);
    } catch (err) {
      if (err instanceof ApiError && err.status === 429) setLeft(0);
      if (request !== assistRequest.current) return;
      outcome = lineFailure(draftRef.current, err);
    }
    setLineBusy(false);
    applyLine(outcome);
  };

  const submit = async () => {
    const payload = createPayload(draft);
    if (!payload || submitting.current) return;
    submitting.current = true;
    setBusy(true);
    const signature = JSON.stringify(payload);
    if (attempt.current?.payload !== signature) attempt.current = { key: newRequestKey(), payload: signature };
    try {
      const res = await api.createMarket({ ...payload, ...expectedCost(chat, terms) }, attempt.current.key);
      attempt.current = null;
      setBalance(res.balance);
      tg.HapticFeedback.notification('success');
      if (typeof res.bot_notified === 'boolean') tg.setBotCanWrite(res.bot_notified);
      clearDraft();
      if (!res.market) {
        toast('Вопрос отправлен на проверку. Монеты зарезервированы; при отказе вернутся. Статус — в разделе «Мои».');
        navigate('/mine', { replace: true });
        return;
      }
      // Replace the wizard: Back from «Рынок опубликован» must not reopen it.
      navigate(`/market/${res.market.id}/created`, {
        replace: true,
        state: { market: res.market, botNotified: res.bot_notified ?? null },
      });
    } catch (err) {
      tg.HapticFeedback.notification('error');
      // A 4xx refusal created nothing; after a lost connection or a 5xx the retry keeps the key.
      if (err instanceof ApiError && err.status >= 400 && err.status < 500) attempt.current = null;
      if (err instanceof ApiError && err.status === 409) {
        // The chat's price moved: show the new one on the button, the next press confirms it.
        setDraftChat(null);
        setChatsAttempt((n) => n + 1);
        toast(err.message, 'error');
        return;
      }
      const refused = draftCreateFailure(draftRef.current, err);
      if (refused === 'made') {
        // The button in the chat was faster: the draft leads to its market.
        setDraftLoad(draftRef.current.draftCode);
        return;
      }
      if (refused === 'gone') {
        commit({ ...draftRef.current, draftCode: null });
        toast(DRAFT_GONE, 'error');
        return;
      }
      const message = err instanceof ApiError ? err.message : 'Что-то пошло не так. Попробуйте ещё раз.';
      const field = err instanceof ApiError && err.status === 400 ? errorField(message) : null;
      if (field) {
        setServerErrors({ [field]: message });
        if (draftRef.current.flow === 'wizard') goTo(stepOfField(field));
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
    if (wizard && step < 3) goTo((step + 1) as CreateStep);
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
    setLineBusy(false);
    setEditing(null);
    setTplOpen(null);
    setTplValues({});
    navigate('/');
  };

  const labels = new Map(categories.map((c) => [c.slug, c.label]));
  const notEnough = reviewing && !!terms && rest === null;
  const nextLabel = quick
    ? terms
      ? `Создать за ${formatMoney(cost)}`
      : 'Создать'
    : step === 3
      ? terms
        ? `На проверку · резерв ${formatMoney(cost)}`
        : 'Отправить на проверку'
      : 'Далее';
  const lineAssist = lineUsesAssist(assist);
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

  const audienceBlock =
    audience.length > 0 ? (
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
    ) : null;

  const similarBlock =
    similar.length > 0 ? (
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
    ) : null;

  const costBlock = (
    <>
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
    </>
  );

  // «Объявить в чате» (spec E8.4): a market made from a chat message is announced by the server anyway.
  const announceBlock = !draft.chat ? null : draft.draftCode ? (
    <p className="sub small create-announce-note">Бот объявит рынок в чате ответом на исходное сообщение.</p>
  ) : (
    <label className="create-check">
      <input
        type="checkbox"
        checked={draft.announce}
        onChange={(e) => {
          tg.HapticFeedback.selection();
          update({ announce: e.target.checked });
        }}
      />
      <span className="create-check-box" aria-hidden="true">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
          <path d="M5 12.5l4.5 4.5L19 7.5" />
        </svg>
      </span>
      <span className="create-check-text">
        <span className="create-check-label">Объявить в чате</span>
        {/* Hint text is ours (no mockup for phase 3d), based on spec E8.4. */}
        <span className="sub create-check-hint">
          Бот напишет о рынке в чат. Без галочки он объявит рынок, когда в нём поставят 3 участника чата.
        </span>
      </span>
    </label>
  );

  const rulesBlock = (
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
  );

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
          <span className="create-step sub">{wizard ? `${step} / 3` : ''}</span>
        </div>
        {wizard && (
          <div className="create-progress" aria-hidden="true">
            {[1, 2, 3].map((n) => (
              <span key={n} className={step >= n ? 'is-done' : ''} />
            ))}
          </div>
        )}
      </header>

      <div className="create-body">
        {draftLoad && (
          <div className="create-draft-loading" role="status" aria-label="Загружаем черновик">
            <Skeleton height={28} width="70%" radius={8} />
            <Skeleton height={220} radius={16} />
            <Skeleton height={96} radius={16} />
          </div>
        )}

        {!draftLoad && flow === 'line' && (
          <>
            <h1 className="screen-title">О чём спорим?</h1>
            <div className="field">
              <div className="field-head">
                <label htmlFor="create-line" className="visually-hidden">
                  О чём спорим?
                </label>
                <span className="sub field-counter create-line-counter">
                  {draft.line.length} / {TITLE_MAX}
                </span>
              </div>
              <textarea
                id="create-line"
                className="input create-line-input"
                rows={3}
                maxLength={TITLE_MAX}
                value={draft.line}
                disabled={lineBusy}
                onChange={(e) => commit({ ...draftRef.current, line: e.target.value.slice(0, TITLE_MAX) })}
                placeholder="Например: Сдам ли я сессию без троек?"
              />
            </div>
            {audienceBlock}
            <button
              type="button"
              className="button-primary is-ink button-block"
              onClick={() => void makeMarket()}
              disabled={!lineReady(draft.line) || lineBusy}
              aria-busy={lineBusy || undefined}
            >
              {lineBusy ? 'Помощник собирает рынок…' : 'Сделать рынок'}
            </button>
            {lineBusy ? (
              <span role="status" className="sub small create-line-note">
                Обычно 2–3 секунды.
              </span>
            ) : (
              <span className="sub small create-line-note">
                {lineAssist
                  ? 'Текст уйдёт ИИ-помощнику — без имени и Telegram ID. Он соберёт рынок, а ты проверишь и поправишь.'
                  : assist?.enabled
                    ? 'Подсказки на сегодня закончились — откроется обычный мастер.'
                    : 'ИИ-помощник сейчас выключен — откроется обычный мастер.'}
                {lineAssist && assist ? ` ${assistLeftLabel(assist.left)}.` : ''}
              </span>
            )}
            <button type="button" className="create-link create-line-manual" onClick={fillMyself}>
              Заполнить самому
            </button>
          </>
        )}

        {!draftLoad && quick && (
          <>
            <h1 className="screen-title">Проверь и создай</h1>
            <p className="sub small">
              {draft.assist ? 'Проверь каждое поле: за итог отвечаешь ты, а не ИИ. ' : ''}Нажми на поле, чтобы поправить.
            </p>
            <QuickFields
              draft={draft}
              update={update}
              categories={categories}
              problem={quickProblem}
              editing={editing}
              onEdit={setEditing}
              now={now}
            />
            {chat && <p className="sub small create-quick-place">Только чат «{chat.title}»: увидят и смогут ставить только участники чата.</p>}
            {similarBlock}
            {costBlock}
            {announceBlock}
            <p className="sub small">Перед публикацией вопрос, правила и варианты ответа проверяет внешняя ИИ-модель. Спорные вопросы рассматривает модератор. До одобрения вопрос не виден участникам; монеты создания зарезервированы и вернутся при отказе.</p>
            {rulesBlock}
          </>
        )}

        {!draftLoad && wizard && <p className="sub small">Перед публикацией вопрос, правила и варианты ответа проверяет внешняя ИИ-модель. Спорные вопросы рассматривает модератор. До одобрения вопрос не виден участникам; монеты создания зарезервированы и вернутся при отказе.</p>}
        {!draftLoad && wizard && step === 1 && (
          <>
            <h1 className="screen-title">О чём спрашиваем?</h1>
            {assistUi === 'warn' && (
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
              </section>
            )}
            <div className="field">
              <span className="field-label">Формат рынка</span>
              <div className="create-mode-tabs" role="tablist" aria-label="Формат рынка">
                <button
                  type="button"
                  role="tab"
                  aria-selected={(draft.mode ?? 'binary') === 'binary'}
                  className={`create-mode-tab ${(draft.mode ?? 'binary') === 'binary' ? 'is-active' : ''}`}
                  onClick={() => update({ mode: 'binary' })}
                >
                  ДА / НЕТ
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={draft.mode === 'multiple'}
                  className={`create-mode-tab ${draft.mode === 'multiple' ? 'is-active' : ''}`}
                  onClick={() =>
                    update({
                      mode: 'multiple',
                      options: draft.options && draft.options.length >= OPTIONS_MIN ? draft.options : ['', ''],
                    })
                  }
                >
                  Несколько вариантов
                </button>
              </div>
            </div>
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
                placeholder={
                  draft.mode === 'multiple'
                    ? 'Например: Какая будет завтра погода?'
                    : 'Например: Сдам ли я сессию без троек?'
                }
                aria-invalid={!!fieldError('title') || undefined}
                aria-describedby="create-q-hint"
              />
              <span id="create-q-hint" className="sub field-hint">
                {draft.mode === 'multiple'
                  ? 'По каждому варианту участники смогут отдельно ставить ДА или НЕТ.'
                  : draft.templateId
                    ? 'Сроки и правила подставятся из шаблона — проверишь их на следующем шаге.'
                    : 'Вопрос должен иметь однозначный ответ ДА или НЕТ, дату и понятный способ проверить итог.'}
                {' '}От {TITLE_MIN} до {TITLE_MAX} символов без пробелов по краям.
              </span>
              {fieldError('title') && <span className="field-error">{fieldError('title')}</span>}
            </div>
            {draft.mode === 'multiple' && (
              <div className="field">
                <div className="field-head">
                  <span className="field-label">Варианты ответа</span>
                  <span className="sub field-counter">
                    {(draft.options ?? ['', '']).length} / {OPTIONS_MAX}
                  </span>
                </div>
                <div className="create-options-list">
                  {(draft.options ?? ['', '']).map((opt, idx) => {
                    const opts = draft.options ?? ['', ''];
                    return (
                      <div key={idx} className="create-option-row">
                        <input
                          type="text"
                          className="input input-short"
                          maxLength={OPTION_MAX}
                          value={opt}
                          placeholder={`Вариант ${idx + 1} (например: ${
                            idx === 0 ? 'до конца октября' : idx === 1 ? 'до конца ноября' : 'до конца года'
                          })`}
                          onChange={(e) => {
                            const nextOpts = [...opts];
                            nextOpts[idx] = e.target.value.slice(0, OPTION_MAX);
                            update({ options: nextOpts });
                          }}
                        />
                        {opts.length > OPTIONS_MIN && (
                          <button
                            type="button"
                            className="create-option-remove"
                            aria-label={`Удалить вариант ${idx + 1}`}
                            onClick={() => update({ options: opts.filter((_, i) => i !== idx) })}
                          >
                            ×
                          </button>
                        )}
                      </div>
                    );
                  })}
                </div>
                {(draft.options ?? ['', '']).length < OPTIONS_MAX && (
                  <button
                    type="button"
                    className="create-option-add"
                    onClick={() => update({ options: [...(draft.options ?? ['', '']), ''] })}
                  >
                    + Добавить вариант
                  </button>
                )}
                {fieldError('options') && <span className="field-error">{fieldError('options')}</span>}
              </div>
            )}
            {draft.mode === 'multiple' ? (
              <div className="field">
                <span className="field-label">Примеры</span>
                <div className="create-examples">
                  {EXAMPLES_MULTIPLE.map((ex) => (
                    <button
                      key={ex.title}
                      type="button"
                      className="example-button example-button-multi"
                      onClick={() => update({ title: ex.title, options: [...ex.options] })}
                    >
                      <span className="example-title">{ex.title}</span>
                      <span className="example-sub sub">{ex.options.join(' · ')}</span>
                    </button>
                  ))}
                </div>
              </div>
            ) : templates.length > 0 ? (
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
            ) : null}

            {!draft.draftCode && audienceBlock}
          </>
        )}

        {!draftLoad && wizard && step === 2 && (
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
              <span>Если промолчишь сутки после этой даты, итог смогут предложить участники. Никто не предложит за 72 часа — рынок отменится и всем вернутся монеты.</span>
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

        {!draftLoad && wizard && step === 3 && (
          <>
            <h1 className="screen-title">Проверь и публикуй</h1>
            {similarBlock}
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
            {costBlock}
            {announceBlock}
            {rulesBlock}
          </>
        )}
      </div>

      {!draftLoad && flow !== 'line' && (
        <div className={`create-bar ${isTelegram() ? 'is-native' : ''}`}>
          {!isTelegram() && (
            <button
              type="button"
              className="create-back"
              onClick={() => (wizard && step > 1 ? goTo((step - 1) as CreateStep) : backToLine())}
            >
              Назад
            </button>
          )}
          <div className="create-next">
            {/* Hidden while a sheet is open: Telegram's MainButton would sit under it. */}
            {!askWrite && editing === null && (
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
      )}

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
            {resolutionDay} бот спросит, чем закончился рынок. Если промолчишь сутки, итог смогут предложить участники.
            Никто не предложит за 72 часа — рынок отменится и всем вернутся монеты.
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
