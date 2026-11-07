// «Колесо дня» (spec E12.2, docs/mockups/oracle/ArtWheel.dc.html): the card in the feed and the wheel itself.
import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { ApiError, api, type WheelState } from '../api';
import { AnimatedNumber } from '../components/AnimatedNumber';
import { CoinIcon } from '../components/CoinIcon';
import { useToast } from '../components/Toast';
import { formatMoney } from '../format';
import { mountScene, type SceneHandle } from '../scene/scene';
import { useSession } from '../session';
import { tg } from '../telegram';
import { cardView, celebrationLevel, chanceLabel, pickDemoPrize, slicesOf, stopAngle, winPhrase } from '../wheel';
import './wheel.css';

const WIDTH = 195;
const HEIGHT = 250;

function prefersStill(): boolean {
  return typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

/** In the feed: locked until a prediction, then an invitation, then what fell out today. */
export function WheelCard() {
  const { me } = useSession();
  const [state, setState] = useState<WheelState | null>(null);

  // A purchase changes the balance: that is when the wheel may have opened.
  useEffect(() => {
    let live = true;
    api
      .wheel()
      .then((res) => live && setState(res))
      .catch(() => live && setState(null));
    return () => {
      live = false;
    };
  }, [me?.balance]);

  if (!state) return null;
  const view = cardView(state);
  if (view === 'spun') {
    return <p className="card wheel-card is-spun">Колесо дня: сегодня выпало +{formatMoney(state.spun ?? 0)}. Новое вращение завтра.</p>;
  }
  if (view === 'locked') {
    return (
      <section className="card wheel-card">
        <span className="wheel-card-title">Колесо дня</span>
        <span className="sub small">Сделай прогноз от 10 монет, и колесо откроется</span>
      </section>
    );
  }
  return (
    <Link to="/wheel" className="card wheel-card is-open">
      <span className="wheel-card-text">
        <span className="wheel-card-title">Колесо дня открыто</span>
        <span className="sub small">одно вращение, до полуночи</span>
      </span>
      <span className="wheel-card-go">крутить</span>
    </Link>
  );
}

type Phase = 'loading' | 'locked' | 'ready' | 'spinning' | 'done';

export function WheelScreen() {
  const navigate = useNavigate();
  const toast = useToast();
  const { setBalance } = useSession();
  const [params] = useSearchParams();
  /** `?demo=1` (startapp=wheel_demo): spins as often as you like, nothing reaches the server, no coins. */
  const demo = params.get('demo') === '1';
  const demoPrize = params.get('prize');
  const canvas = useRef<HTMLCanvasElement>(null);
  const scene = useRef<SceneHandle | null>(null);
  const [state, setState] = useState<WheelState | null>(null);
  const [phase, setPhase] = useState<Phase>('loading');
  const [prize, setPrize] = useState<string | null>(null);
  /** The number counts up from zero right after the wheel stops; a prize seen again later just shows. */
  const [shown, setShown] = useState(0);
  const [fresh, setFresh] = useState(false);
  const lastTick = useRef(0);

  useEffect(() => {
    api
      .wheel()
      .then((res) => {
        setState(res);
        if (demo) {
          setPhase('ready');
          return;
        }
        setPrize(res.spun);
        setShown(Number(res.spun ?? 0));
        setPhase(res.spun !== null ? 'done' : res.unlocked ? 'ready' : 'locked');
      })
      .catch(() => navigate('/', { replace: true }));
  }, [navigate, demo]);

  useEffect(() => {
    if (!state || !canvas.current || scene.current) return;
    const angle = !demo && state.spun !== null ? (stopAngle(state.prizes, state.spun, 0) ?? 0) : 0;
    scene.current = mountScene(canvas.current, {
      width: WIDTH,
      height: HEIGHT,
      transparent: true,
      still: prefersStill(),
      props: [{ type: 'wheel', x: WIDTH / 2, y: 104, r: 78, angle, slices: slicesOf(state.prizes) }],
      onTick: () => {
        // A light tick for each slice edge, but not a buzz when the wheel is at full speed.
        const now = performance.now();
        if (now - lastTick.current < 70) return;
        lastTick.current = now;
        tg.HapticFeedback.tick();
      },
    });
    return () => {
      scene.current?.stop();
      scene.current = null;
    };
  }, [state, demo]);

  /** Spin to `won` and celebrate; `after` runs once the number has started to count up. */
  const play = (won: string, after?: () => void) => {
    if (!state) return;
    setPhase('spinning');
    setFresh(false);
    tg.HapticFeedback.impact('heavy');
    const angle = stopAngle(state.prizes, won, 5, Math.random() - 0.5) ?? 0;
    const finish = () => {
      setPrize(won);
      setFresh(true);
      setShown(0);
      setPhase('done');
      scene.current?.celebrate(celebrationLevel(state.prizes, won));
      tg.HapticFeedback.notification('success');
      // Let the coins fly before the number runs up.
      window.setTimeout(() => {
        setShown(Number(won));
        after?.();
      }, 250);
    };
    if (!scene.current) {
      finish();
      return;
    }
    scene.current.spinWheel(angle, 3400, finish);
  };

  const spin = async () => {
    if (!state || phase !== 'ready') return;
    if (demo) {
      play(pickDemoPrize(state.prizes, demoPrize));
      return;
    }
    setPhase('spinning');
    try {
      // The server picks the prize; the wheel only shows its answer.
      const res = await api.spinWheel();
      play(res.prize, () => setBalance(res.balance));
    } catch (err) {
      setPhase(err instanceof ApiError && err.status === 400 ? 'locked' : 'ready');
      toast(err instanceof Error ? err.message : 'Не удалось. Попробуй ещё раз.');
      tg.HapticFeedback.notification('error');
    }
  };

  /** Today's spin once more: the same prize, nothing asked of the server. */
  const replay = () => {
    if (prize !== null && phase === 'done') play(prize);
  };

  return (
    <div className="screen wheel">
      <h1 className="screen-title">Колесо дня</h1>
      {demo && <p className="wheel-demo">Демо: монеты не начисляются, крутить можно сколько угодно</p>}
      <canvas ref={canvas} className="wheel-hero" aria-hidden="true" />

      <div className="wheel-action" aria-live="polite">
        {phase === 'done' && prize !== null && (
          <div className={`wheel-result ${fresh ? 'is-fresh' : ''}`}>
            {fresh && <span className="wheel-phrase">{winPhrase(state ? celebrationLevel(state.prizes, prize) : 0)}</span>}
            <span className="wheel-prize">
              <CoinIcon size={40} strokeWidth={2.5} />
              <AnimatedNumber value={shown} duration={1100} format={(n) => '+' + formatMoney(Math.round(n), 0)} />
            </span>
            <span className="sub">{demo ? 'это демо, монеты не начислены' : 'монеты уже на балансе · новое вращение завтра'}</span>
            {demo ? (
              <button type="button" className="button-primary" onClick={() => play(pickDemoPrize(state!.prizes, demoPrize))}>
                крутить ещё
              </button>
            ) : (
              <button type="button" className="wheel-replay" onClick={replay}>
                посмотреть ещё раз
              </button>
            )}
          </div>
        )}
        {phase === 'ready' && (
          <button type="button" className="button-primary button-block" onClick={spin}>
            крутить
          </button>
        )}
        {phase === 'spinning' && <span className="wheel-wait">колесо решает…</span>}
        {phase === 'locked' && (
          <div className="wheel-result">
            <span className="sub">Колесо откроется после прогноза от 10 монет.</span>
            <Link to="/" className="button-secondary">
              К рынкам
            </Link>
          </div>
        )}
      </div>

      {state && (
        <section className="card wheel-odds" aria-label="Шансы">
          <span className="wheel-odds-title">Шансы</span>
          <div className="wheel-odds-row">
            {state.prizes.map((p) => (
              <span key={p.prize} className="wheel-odd">
                <span className="wheel-odd-prize">{formatMoney(p.prize)}</span>
                <span className="sub small">{chanceLabel(p.chance)}</span>
              </span>
            ))}
          </div>
          <span className="sub small">
            Чем шире доля, тем чаще она выпадает — колесо честное. Крутить можно раз в день и бесплатно. Монеты игровые:
            их нельзя купить или вывести, а в рейтинг идёт только прибыль от прогнозов.
          </span>
        </section>
      )}
    </div>
  );
}
