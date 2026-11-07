// «Колесо дня» (spec E12.2, docs/mockups/oracle/ArtWheel.dc.html): the card in the feed and the wheel itself.
import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { ApiError, api, type WheelState } from '../api';
import { useToast } from '../components/Toast';
import { formatMoney } from '../format';
import { mountScene, type SceneHandle } from '../scene/scene';
import { useSession } from '../session';
import { tg } from '../telegram';
import { cardView, chanceLabel, slicesOf, stopAngle } from '../wheel';
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
  const canvas = useRef<HTMLCanvasElement>(null);
  const scene = useRef<SceneHandle | null>(null);
  const [state, setState] = useState<WheelState | null>(null);
  const [phase, setPhase] = useState<Phase>('loading');
  const [prize, setPrize] = useState<string | null>(null);

  useEffect(() => {
    api
      .wheel()
      .then((res) => {
        setState(res);
        setPrize(res.spun);
        setPhase(res.spun !== null ? 'done' : res.unlocked ? 'ready' : 'locked');
      })
      .catch(() => navigate('/', { replace: true }));
  }, [navigate]);

  useEffect(() => {
    if (!state || !canvas.current || scene.current) return;
    const angle = state.spun !== null ? (stopAngle(state.prizes, state.spun, 0) ?? 0) : 0;
    scene.current = mountScene(canvas.current, {
      width: WIDTH,
      height: HEIGHT,
      transparent: true,
      still: prefersStill(),
      props: [{ type: 'wheel', x: WIDTH / 2, y: 104, r: 78, angle, slices: slicesOf(state.prizes) }],
    });
    return () => {
      scene.current?.stop();
      scene.current = null;
    };
  }, [state]);

  const spin = async () => {
    if (!state || phase !== 'ready') return;
    setPhase('spinning');
    tg.HapticFeedback.impact('medium');
    try {
      // The server picks the prize; the wheel only shows its answer.
      const res = await api.spinWheel();
      const angle = stopAngle(state.prizes, res.prize, 5, Math.random() - 0.5) ?? 0;
      const finish = () => {
        setPrize(res.prize);
        setBalance(res.balance);
        setPhase('done');
        tg.HapticFeedback.notification('success');
      };
      if (scene.current) scene.current.spinWheel(angle, 3400, finish);
      else finish();
    } catch (err) {
      setPhase(err instanceof ApiError && err.status === 400 ? 'locked' : 'ready');
      toast(err instanceof Error ? err.message : 'Не удалось. Попробуй ещё раз.');
      tg.HapticFeedback.notification('error');
    }
  };

  return (
    <div className="screen wheel">
      <h1 className="screen-title">Колесо дня</h1>
      <canvas ref={canvas} className="wheel-hero" aria-hidden="true" />

      <div className="wheel-action" aria-live="polite">
        {phase === 'done' && prize !== null && (
          <div className="wheel-result">
            <span className="wheel-prize">+{formatMoney(prize)}</span>
            <span className="sub">новое вращение завтра</span>
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
          <div className="wheel-odds-row">
            {state.prizes.map((p) => (
              <span key={p.prize} className="wheel-odd">
                <span className="wheel-odd-prize">{formatMoney(p.prize)}</span>
                <span className="sub small">{chanceLabel(p.chance)}</span>
              </span>
            ))}
          </div>
          <span className="sub small">
            Размер долей равен шансам. Вращение бесплатное, его нельзя купить. Монеты нельзя вывести, в прибыль рейтинга
            они не входят.
          </span>
        </section>
      )}
    </div>
  );
}
