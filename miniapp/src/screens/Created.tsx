// «Рынок опубликован» after the creation wizard (canvas «Как указать итог», Done.dc.html):
// how the bot will ask for the outcome and what counts as proof.
import { useEffect, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { api, type MarketDetail } from '../api';
import { useToast } from '../components/Toast';
import { formatDayMonth } from '../format';
import { shareMarket } from '../links';
import { botChatLink } from '../market';
import { useSession } from '../session';
import { tg } from '../telegram';
import './created.css';

interface CreatedState {
  market?: MarketDetail;
  /** From POST /api/markets: false when the bot's note did not reach the creator. */
  botNotified?: boolean | null;
}

export function CreatedScreen() {
  const { id } = useParams();
  const marketId = Number(id);
  const navigate = useNavigate();
  const toast = useToast();
  const { me } = useSession();
  const state = (useLocation().state ?? {}) as CreatedState;
  const [market, setMarket] = useState<MarketDetail | null>(state.market?.id === marketId ? state.market : null);
  const [botBlocked, setBotBlocked] = useState(state.botNotified === false);

  // Opened again later (reload, history): the wizard's copy of the market is gone.
  useEffect(() => {
    if (market || !Number.isFinite(marketId)) return;
    let cancelled = false;
    api
      .market(marketId)
      .then((m) => !cancelled && setMarket(m))
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [market, marketId]);

  const resolveDay = market ? formatDayMonth(market.resolves_at) : null;
  const title = market?.title ?? 'Твой рынок';

  const allowBot = async () => {
    if (tg.canRequestWriteAccess) {
      if (await tg.requestWriteAccess()) {
        tg.HapticFeedback.notification('success');
        setBotBlocked(false);
        toast('Готово — бот напомнит про итог');
      }
      return;
    }
    const link = botChatLink(me?.referral_link);
    if (link) tg.openTelegramLink(link);
  };

  const share = () => {
    if (market && me) void shareMarket(market, me.referral_link);
  };

  return (
    <div className="screen created">
      <div className="created-hero">
        <span className="created-check" aria-hidden="true">
          <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
            <path d="M5 12.5l4.5 4.5L19 7.5" />
          </svg>
        </span>
        <h1 className="screen-title">Рынок опубликован</h1>
        {market && (
          <p className="created-lead sub">
            «{market.title}» Прогнозы принимаем до {formatDayMonth(market.closes_at)}, итог — {resolveDay}.
          </p>
        )}
      </div>

      {botBlocked && (
        <div className="created-warning" role="status">
          <div className="created-warning-text">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M12 4l9 16H3z" />
              <path d="M12 10v4M12 17.5v.5" />
            </svg>
            <span>
              Бот пока не может тебе писать — {resolveDay ?? 'в дату итога'} он не напомнит про итог. Указать итог можно
              будет во вкладке «Мои».
            </span>
          </div>
          <button type="button" className="button-secondary" onClick={allowBot}>
            {tg.canRequestWriteAccess ? 'Разрешить боту писать' : 'Открыть бота'}
          </button>
        </div>
      )}

      <section className="card created-card" aria-label="Как указать итог">
        <h2 className="created-title">Как указать итог</h2>
        <ol className="created-steps">
          <li>
            <span className="created-num" aria-hidden="true">1</span>
            <div className="created-step">
              <span className="created-step-title">{resolveDay ?? 'В дату итога'} бот спросит, чем закончилось</span>
              <div className="mini-chat" aria-hidden="true">
                <div className="mini-bubble">
                  Приём прогнозов на твоём рынке закрыт: <b>«{title}»</b> Чем закончилось?
                </div>
                <div className="mini-keys">
                  <span className="mini-key is-on">ДА</span>
                  <span className="mini-key">НЕТ</span>
                </div>
                <span className="mini-key is-wide">Отменить рынок</span>
              </div>
            </div>
          </li>
          <li>
            <span className="created-num" aria-hidden="true">2</span>
            <div className="created-step">
              <span className="created-step-title">Нажми ДА или НЕТ — итог указан</span>
              <p className="created-step-text sub">Одно нажатие. Фото можно приложить по желанию.</p>
              <div className="mini-chat" aria-hidden="true">
                <div className="mini-bubble mini-preview">
                  <span>
                    Итог: <b>ДА</b>
                  </span>
                </div>
                <div className="mini-keys is-narrow">
                  <span className="mini-key">Приложить фото</span>
                </div>
              </div>
            </div>
          </li>
          <li>
            <span className="created-num" aria-hidden="true">3</span>
            <div className="created-step">
              <span className="created-step-title">24 часа на спор, потом выплаты придут сами</span>
              <p className="created-step-text sub">
                Участники увидят итог и смогут его оспорить. Доказательство понадобится, только если итог оспорят.
              </p>
            </div>
          </li>
        </ol>
      </section>

      <div className="created-note sub">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
          <circle cx="12" cy="12" r="9" />
          <path d="M12 11v5M12 8v.5" />
        </svg>
        <span>
          Если промолчишь сутки после {resolveDay ?? 'даты итога'}, итог смогут предложить участники. Никто не предложит
          за 72 часа — рынок отменится и всем вернутся монеты.
        </span>
      </div>

      <div className="created-actions">
        <button type="button" className="button-primary is-ink button-block" onClick={share} disabled={!market || !me}>
          Поделиться в чате
        </button>
        <button type="button" className="created-open" onClick={() => navigate(`/market/${marketId}`, { replace: true })}>
          Открыть рынок
        </button>
      </div>
    </div>
  );
}
