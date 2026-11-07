// 1-on-1 challenge (spec E11): creating, the link, accepting, «Чем закончилось?», a dispute, the end.
// Texts — docs/mockups/challenge. Money and rules are on the server: the screens only show its answer.
import { useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import {
  ApiError,
  api,
  isAssistWarning,
  type Challenge,
  type ChallengeAnswer,
  type ChallengeLimits,
  type ChallengeReply,
} from '../api';
import {
  CRITERIA_MAX,
  TITLE_MAX,
  answerLabel,
  challengePhase,
  challengeShareUrl,
  closedText,
  draftProblem,
  formParams,
  hoursLeft,
  hoursText,
  otherSide,
  prizeOf,
  rowState,
  rowSubtitle,
  shareWays,
  stakeOptions,
  type ChallengeDraft,
} from '../challenge';
import { CardSkeleton } from '../components/Skeleton';
import { useToast } from '../components/Toast';
import { dateInputValue, localIso, localParts } from '../create';
import { formatDayMonth, formatMoney } from '../format';
import { botChatLink } from '../market';
import { useSession } from '../session';
import { tg } from '../telegram';
import './challenge.css';

const message = (err: unknown) => (err instanceof Error ? err.message : 'Не удалось. Попробуй ещё раз.');

function SideValue({ side }: { side: 'yes' | 'no' }) {
  return <span className={side === 'yes' ? 'ch-yes' : 'ch-no'}>{side === 'yes' ? 'ДА' : 'НЕТ'}</span>;
}

function Rules({ text }: { text: string }) {
  return (
    <section className="card ch-card">
      <span className="ch-label">Как определяем результат</span>
      <p className="ch-text">{text}</p>
    </section>
  );
}

// ---- «Мои → Вызовы» ----

export function ChallengesTab() {
  const navigate = useNavigate();
  const [items, setItems] = useState<Challenge[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let live = true;
    setError(null);
    api
      .challenges()
      .then((res) => live && setItems(res.challenges))
      .catch((err) => live && setError(message(err)));
    return () => {
      live = false;
    };
  }, [attempt]);

  return (
    <div className="ch-list">
      {error && (
        <div className="feed-message" role="alert">
          <p>Не удалось загрузить. {error}</p>
          <button type="button" className="button-secondary" onClick={() => setAttempt((n) => n + 1)}>
            Повторить
          </button>
        </div>
      )}
      {!error && !items && [0, 1].map((i) => <CardSkeleton key={i} />)}
      {items?.length === 0 && <p className="sub">Поспорь с другом на монеты: он примет вызов одним нажатием.</p>}
      {items?.map((c) => {
        const state = rowState(c);
        return (
          <button key={c.code} type="button" className="card ch-item" onClick={() => navigate(`/challenge/${c.code}`)}>
            <span className="ch-item-title">{c.title}</span>
            <span className="ch-item-row">
              <span className="sub">{rowSubtitle(c)}</span>
              <span className={`ch-item-state ${state.tone === 'alert' ? 'is-alert' : state.tone === 'good' ? 'is-good' : 'sub'}`}>
                {state.text}
              </span>
            </span>
          </button>
        );
      })}
      <button type="button" className="button-primary" onClick={() => navigate('/challenge/new')}>
        Вызвать друга
      </button>
    </div>
  );
}

// ---- Creating ----

function defaultDraft(now: Date = new Date()): ChallengeDraft {
  const week = new Date(now.getTime() + 7 * 86_400_000);
  return { title: '', criteria: '', side: 'yes', stake: 0, date: dateInputValue(week), time: '18:00' };
}

export function NewChallengeScreen() {
  const navigate = useNavigate();
  const toast = useToast();
  const { me, setBalance, patchMe } = useSession();
  const [limits, setLimits] = useState<ChallengeLimits | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const opened = formParams(useLocation().search);
  const [draft, setDraft] = useState<ChallengeDraft>(() => ({ ...defaultDraft(), title: opened.title }));
  const [helping, setHelping] = useState(false);
  const [filledByHelper, setFilledByHelper] = useState(false);
  const [sending, setSending] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const requestKey = useRef(crypto.randomUUID());

  useEffect(() => {
    api
      .challenges()
      .then((res) => {
        setLimits(res.limits);
        const options = stakeOptions(res.limits);
        const usual = options.includes(50) ? 50 : (options[0] ?? 0);
        setDraft((d) => ({ ...d, stake: usual }));
      })
      .catch((err) => setLoadError(message(err)));
  }, []);

  const update = (patch: Partial<ChallengeDraft>) => {
    setDraft((d) => ({ ...d, ...patch }));
    setProblem(null);
    if ('criteria' in patch) setFilledByHelper(false);
  };

  const assistOn = !!me?.assist?.enabled && (me?.assist?.left ?? 0) > 0;

  const help = async () => {
    if (draft.title.trim().length < 10) {
      setProblem('Сначала напиши вопрос: от 10 символов.');
      return;
    }
    setHelping(true);
    try {
      const res = await api.assist(draft.title.trim());
      patchMe((prev) => (prev.assist ? { ...prev, assist: { ...prev.assist, left: res.left } } : prev));
      if (isAssistWarning(res)) {
        toast(res.text);
        return;
      }
      const resolves = localParts(res.resolves_at);
      setDraft((d) => ({
        ...d,
        criteria: res.criteria,
        ...(res.question ? { title: res.question } : {}),
        ...(resolves ? { date: resolves.date, time: resolves.time } : {}),
      }));
      setFilledByHelper(true);
    } catch {
      toast('Помощник сейчас не отвечает — заполни правила сам');
    } finally {
      setHelping(false);
    }
  };

  const send = async () => {
    if (!limits || sending) return;
    const issue = draftProblem(draft, limits);
    if (issue) {
      setProblem(issue);
      return;
    }
    setSending(true);
    try {
      if (tg.canRequestWriteAccess && !tg.botCanWrite) await tg.requestWriteAccess();
      const res = await api.createChallenge(
        {
          title: draft.title.trim(),
          criteria: draft.criteria.trim(),
          side: draft.side,
          stake: draft.stake,
          resolves_at: localIso(draft.date, draft.time) ?? '',
        },
        requestKey.current,
      );
      setBalance(res.balance);
      tg.HapticFeedback.notification('success');
      navigate(`/challenge/${res.challenge.code}${opened.inline ? '?inline=1' : ''}`, { replace: true });
    } catch (err) {
      setProblem(message(err));
      tg.HapticFeedback.notification('error');
    } finally {
      setSending(false);
    }
  };

  if (loadError) {
    return (
      <div className="screen ch" role="alert">
        <p>Не удалось загрузить. {loadError}</p>
      </div>
    );
  }
  if (!limits) {
    return (
      <div className="screen ch">
        <CardSkeleton />
      </div>
    );
  }
  const options = stakeOptions(limits);

  return (
    <div className="screen ch">
      <h1 className="ch-title">Вызов другу</h1>
      <div className="ch-field">
        <label className="ch-label" htmlFor="ch-q">
          О чём спорим?
        </label>
        <textarea
          id="ch-q"
          className="ch-input"
          rows={2}
          maxLength={TITLE_MAX}
          value={draft.title}
          onChange={(e) => update({ title: e.target.value })}
        />
      </div>

      <div className="ch-field">
        <label className="ch-label" htmlFor="ch-rules">
          Как определяем результат
        </label>
        <textarea
          id="ch-rules"
          className="ch-input"
          rows={3}
          maxLength={CRITERIA_MAX}
          value={draft.criteria}
          placeholder="ДА — если…&#10;НЕТ — если…"
          onChange={(e) => update({ criteria: e.target.value })}
        />
        {filledByHelper && <span className="sub small">Заполнил ИИ-помощник. Нажми, чтобы исправить.</span>}
        {assistOn && !filledByHelper && (
          <button type="button" className="button-secondary" disabled={helping} onClick={help}>
            {helping ? 'Помощник думает…' : 'Заполнить с помощником'}
          </button>
        )}
      </div>

      <div className="ch-field">
        <span className="ch-label">Я за</span>
        <div className="ch-answers">
          <button type="button" className="ch-pick is-yes" aria-pressed={draft.side === 'yes'} onClick={() => update({ side: 'yes' })}>
            ДА
          </button>
          <button type="button" className="ch-pick is-no" aria-pressed={draft.side === 'no'} onClick={() => update({ side: 'no' })}>
            НЕТ
          </button>
        </div>
        <span className="sub small">Другу достанется {otherSide(draft.side) === 'yes' ? 'ДА' : 'НЕТ'}.</span>
      </div>

      <div className="ch-field">
        <span className="ch-label">Ставка каждого</span>
        {options.length > 0 ? (
          <div className="ch-stakes">
            {options.map((value) => (
              <button
                key={value}
                type="button"
                className="ch-stake"
                aria-pressed={draft.stake === value}
                onClick={() => update({ stake: value })}
              >
                {formatMoney(value)}
              </button>
            ))}
          </div>
        ) : (
          <span className="sub small">На балансе мало монет для вызова.</span>
        )}
        <span className="sub small">
          От {formatMoney(limits.min_stake)} до {formatMoney(limits.max_stake)} — твой лимит сделки сейчас.
        </span>
      </div>

      <div className="ch-field">
        <label className="ch-label" htmlFor="ch-date">
          Когда станет известен итог
        </label>
        <div className="ch-dates">
          <input id="ch-date" className="ch-input" type="date" value={draft.date} onChange={(e) => update({ date: e.target.value })} />
          <input
            aria-label="Время итога"
            className="ch-input"
            type="time"
            value={draft.time}
            onChange={(e) => update({ time: e.target.value })}
          />
        </div>
      </div>

      {problem && (
        <p className="ch-text ch-no" role="alert">
          {problem}
        </p>
      )}
      <p className="sub small ch-text">
        Победитель получит {formatMoney(prizeOf(draft.stake, limits.fee))} — банк минус {Number(limits.fee) * 100}%. Если друг не
        примет за {limits.accept_hours} часа, ставка вернётся.
      </p>
      <button type="button" className="button-primary" disabled={sending || options.length === 0} onClick={send}>
        Вызвать за {formatMoney(draft.stake)}
      </button>
    </div>
  );
}

// ---- One challenge ----

export function ChallengeScreen() {
  const { code = '' } = useParams();
  const fromInline = formParams(useLocation().search).inline;
  const navigate = useNavigate();
  const toast = useToast();
  const { me, setBalance } = useSession();
  const [challenge, setChallenge] = useState<Challenge | null>(null);
  const [missing, setMissing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    api
      .challenge(code)
      .then((res) => live && setChallenge(res.challenge))
      .catch((err) => {
        if (!live) return;
        if (err instanceof ApiError && err.status === 404) setMissing(true);
        else setError(message(err));
      });
    return () => {
      live = false;
    };
  }, [code]);

  const act = async (run: () => Promise<ChallengeReply>) => {
    if (busy) return;
    setBusy(true);
    try {
      const res = await run();
      setChallenge(res.challenge);
      setBalance(res.balance);
      tg.HapticFeedback.notification('success');
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) setMissing(true);
      else toast(message(err));
      tg.HapticFeedback.notification('error');
    } finally {
      setBusy(false);
    }
  };

  if (missing) {
    return (
      <div className="screen ch">
        <h1 className="ch-title">Вызов недоступен</h1>
        <p className="sub">Его уже принял кто-то другой или он закрыт.</p>
        <button type="button" className="button-primary" onClick={() => navigate('/challenge/new', { replace: true })}>
          Вызвать друга
        </button>
      </div>
    );
  }
  if (error) {
    return (
      <div className="screen ch" role="alert">
        <p>Не удалось загрузить. {error}</p>
      </div>
    );
  }
  if (!challenge) {
    return (
      <div className="screen ch">
        <CardSkeleton />
      </div>
    );
  }

  const c = challenge;
  const phase = challengePhase(c);
  const rival = c.role === 'author' ? c.opponent_name : c.author_name;
  const rivalSide = otherSide(c.my_side);
  const answer = (value: ChallengeAnswer) => () => act(() => api.answerChallenge(c.code, value));
  const report = async () => {
    try {
      await api.reportChallenge(c.code);
      toast('Жалоба отправлена — модератор посмотрит');
    } catch (err) {
      toast(message(err));
    }
  };
  const accept = () =>
    act(async () => {
      if (tg.canRequestWriteAccess && !tg.botCanWrite) await tg.requestWriteAccess();
      return api.acceptChallenge(c.code);
    });
  const send = async () => {
    tg.HapticFeedback.impact('light');
    const ways = shareWays({ inline: tg.canSwitchInline, fromInline, prepared: tg.canShareMessage });
    for (const way of ways) {
      try {
        if (way === 'inline') tg.switchInlineQuery(`ch_${c.code}`);
        else if (way === 'prepared') await tg.shareMessage((await api.shareChallenge(c.code)).prepared_message_id);
        else tg.openTelegramLink(challengeShareUrl(c));
        return;
      } catch {
        // The next way: the plain link always works.
      }
    }
  };
  const copyLink = async () => {
    try {
      await navigator.clipboard.writeText(c.link ?? '');
      toast('Ссылка скопирована');
    } catch {
      toast(c.link ?? '');
    }
  };

  return (
    <div className="screen ch">
      {phase === 'invite' && <p className="sub">Вызывает: {c.author_name}</p>}
      {phase === 'waiting_rival' && <h2 className="ch-head">Отправь вызов другу</h2>}
      {phase !== 'invite' && phase !== 'waiting_rival' && rival && <p className="sub">Соперник: {rival}</p>}
      <h1 className="ch-title">{c.title}</h1>

      {phase === 'invite' && (
        <>
          <div className="ch-sides">
            <div className={`ch-side ${rivalSide === 'yes' ? 'is-yes' : 'is-no'}`}>
              <span className="sub small">{c.author_name} за</span>
              <span className="ch-side-value">
                <SideValue side={rivalSide} />
              </span>
            </div>
            <div className={`ch-side ${c.my_side === 'yes' ? 'is-yes' : 'is-no'}`}>
              <span className="sub small">Ты будешь за</span>
              <span className="ch-side-value">
                <SideValue side={c.my_side} />
              </span>
            </div>
          </div>
          <section className="card ch-card">
            <div className="ch-row">
              <span className="sub">Ставка каждого</span>
              <span className="ch-row-value">{formatMoney(c.stake)}</span>
            </div>
            <div className="ch-row">
              <span className="sub">Победитель получит</span>
              <span className="ch-row-value">{formatMoney(c.prize)}</span>
            </div>
            <div className="ch-row">
              <span className="sub">Итог станет известен</span>
              <span className="ch-row-value">{formatDayMonth(c.resolves_at)}</span>
            </div>
          </section>
          <Rules text={c.criteria} />
          <p className="sub small ch-text">
            В дату итога бот спросит вас обоих. Если ответите одинаково, монеты придут сразу. Если нет, решит модератор.
          </p>
          <button type="button" className="button-primary" disabled={busy} onClick={accept}>
            Принять за {formatMoney(c.stake)}
          </button>
          <button type="button" className="ch-link-quiet" onClick={report}>
            Пожаловаться
          </button>
        </>
      )}

      {phase === 'closed' && <p className="sub">Этот вызов больше нельзя принять.</p>}

      {phase === 'waiting_rival' && (
        <>
          <p className="sub ch-text">Ссылка одноразовая: вызов достанется первому, кто его примет.</p>
          <section className="card ch-card">
            <div className="ch-row">
              <span className="sub">Ты за</span>
              <SideValue side={c.my_side} />
            </div>
            <div className="ch-row">
              <span className="sub">Ставка заморожена</span>
              <span className="ch-row-value">{formatMoney(c.stake)}</span>
            </div>
            <div className="ch-row">
              <span className="sub">Принять можно</span>
              <span className="ch-row-value">
                {(() => {
                  const left = hoursLeft(c.expires_at);
                  return left ? `ещё ${hoursText(left)}` : 'время вышло';
                })()}
              </span>
            </div>
          </section>
          <button type="button" className="button-primary" onClick={send}>
            {fromInline ? 'Отправить в этот чат' : 'Отправить другу'}
          </button>
          <button type="button" className="button-secondary" onClick={copyLink}>
            Скопировать ссылку
          </button>
          <p className="sub small ch-text">Когда друг примет, бот напишет тебе.</p>
          {c.can_cancel && (
            <button type="button" className="ch-link-danger" disabled={busy} onClick={() => act(() => api.cancelChallenge(c.code))}>
              Отменить вызов и вернуть {formatMoney(c.stake)}
            </button>
          )}
        </>
      )}

      {(phase === 'running' || phase === 'answer' || phase === 'waiting_answer') && (
        <section className="card ch-card">
          <div className="ch-row">
            <span className="sub">Ты за</span>
            <SideValue side={c.my_side} />
          </div>
          <div className="ch-row">
            <span className="sub">{rival} за</span>
            <SideValue side={rivalSide} />
          </div>
          <div className="ch-row">
            <span className="sub">Победитель получит</span>
            <span className="ch-row-value">{formatMoney(c.prize)}</span>
          </div>
          {phase === 'running' && (
            <div className="ch-row">
              <span className="sub">Итог спросим</span>
              <span className="ch-row-value">{formatDayMonth(c.resolves_at)}</span>
            </div>
          )}
        </section>
      )}

      {phase === 'answer' && (
        <section className="card ch-card">
          <span className="ch-head">Чем закончилось?</span>
          <p className="sub ch-text">{c.criteria}</p>
          <div className="ch-answers">
            <button type="button" className="ch-answer is-yes" disabled={busy} onClick={answer('yes')}>
              ДА
            </button>
            <button type="button" className="ch-answer is-no" disabled={busy} onClick={answer('no')}>
              НЕТ
            </button>
          </div>
          <button type="button" className="button-secondary" disabled={busy} onClick={answer('cancel')}>
            Отменить
          </button>
          <p className="sub small ch-text">Если оба нажмёте «Отменить», ставки вернутся без комиссии. Если никто не ответит за 72 часа, тоже.</p>
        </section>
      )}

      {phase === 'waiting_answer' && c.my_answer && (
        <section className="card ch-card">
          <span className="ch-head">Твой ответ: {answerLabel(c.my_answer)}</span>
          <p className="sub ch-text">
            Ждём ответа соперника.
            {(() => {
              const left = hoursLeft(c.answer_until);
              return left ? ` Если он не ответит за ${hoursText(left)}, твой ответ встанет.` : '';
            })()}
          </p>
        </section>
      )}

      {phase === 'rival_answered' && c.rival_answer && (
        <>
          <section className="card ch-card">
            <span className="ch-head">Соперник указал: {answerLabel(c.rival_answer)}</span>
            <p className="sub ch-text">
              {(() => {
                const left = hoursLeft(c.answer_until);
                return left ? `Не ответишь за ${hoursText(left)} — его ответ встанет сам.` : 'Его ответ сейчас встанет.';
              })()}
            </p>
            <button type="button" className="button-primary" disabled={busy} onClick={answer(c.rival_answer)}>
              Согласен: {answerLabel(c.rival_answer)}
            </button>
          </section>
          <section className="card ch-card">
            <span className="ch-label">Не согласен?</span>
            <p className="sub ch-text">
              Другой ответ откроет спор за {formatMoney(c.dispute_cost)} монет. Они вернутся, если модератор признает твою правоту.
            </p>
            <div className="ch-answers">
              {(['yes', 'no', 'cancel'] as const)
                .filter((value) => value !== c.rival_answer)
                .map((value) => (
                  <button key={value} type="button" className="button-secondary" disabled={busy} onClick={answer(value)}>
                    {answerLabel(value)}, спор за {formatMoney(c.dispute_cost)}
                  </button>
                ))}
            </div>
          </section>
        </>
      )}

      {phase === 'dispute' && (
        <>
          <section className="card ch-card">
            <span className="ch-status">Спор у модератора</span>
            {c.my_answer && (
              <div className="ch-row">
                <span className="sub">Твой ответ</span>
                <span className="ch-row-value">{answerLabel(c.my_answer)}</span>
              </div>
            )}
            {c.rival_answer && (
              <div className="ch-row">
                <span className="sub">Ответ соперника</span>
                <span className="ch-row-value">{answerLabel(c.rival_answer)}</span>
              </div>
            )}
          </section>
          {(() => {
            const left = hoursLeft(c.proof_until);
            const chat = botChatLink(me?.referral_link);
            return left ? (
              <section className="card ch-card">
                <span className="ch-label">Пришли доказательство в бот</span>
                <p className="sub ch-text">
                  Фото, скриншот или ссылка. На это ещё {hoursText(left)}. Без доказательства модератор всё равно решит спор, но это
                  будет доводом против тебя.
                </p>
                {chat && (
                  <button type="button" className="button-primary" onClick={() => tg.openTelegramLink(chat)}>
                    Открыть бот
                  </button>
                )}
              </section>
            ) : null;
          })()}
          <p className="sub small ch-text">Модератор выберет победителя или отменит вызов с возвратом ставок. Его решение окончательное.</p>
        </>
      )}

      {phase === 'done' && (
        <>
          <section className="card ch-card ch-result">
            <span className="sub">{c.result === 'won' ? 'Ты выиграл' : c.result === 'lost' ? 'Ты проиграл' : 'Вызов отменён'}</span>
            <span className="ch-result-value">
              {c.result === 'won' ? `+${formatMoney(c.payout ?? 0)}` : c.result === 'lost' ? `−${formatMoney(c.stake)}` : formatMoney(c.stake)}
            </span>
            <span className="sub small">
              {c.outcome && c.outcome !== 'cancel' ? (
                <>
                  Итог: <SideValue side={c.outcome} /> — {closedText(c)}
                </>
              ) : (
                `Ставка вернулась: ${closedText(c)}`
              )}
            </span>
          </section>
          {c.result === 'won' && (
            <section className="card ch-card">
              <div className="ch-row">
                <span className="sub">Банк</span>
                <span className="ch-row-value">{formatMoney(c.pot)}</span>
              </div>
              <div className="ch-row">
                <span className="sub">Комиссия</span>
                <span className="ch-row-value">−{formatMoney(Number(c.pot) - Number(c.prize))}</span>
              </div>
            </section>
          )}
          <p className="sub small ch-text">Вызовы не входят в рейтинг и статистику прогнозов.</p>
          <button type="button" className="button-primary" onClick={() => navigate('/challenge/new')}>
            Вызвать ещё раз
          </button>
        </>
      )}

      {phase !== 'invite' && phase !== 'done' && c.role === 'opponent' && (
        <button type="button" className="ch-link-quiet" onClick={report}>
          Пожаловаться
        </button>
      )}
    </div>
  );
}
